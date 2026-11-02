import json
import sqlite3
from pathlib import Path

from app.db import get_db
from app.services import create_inspection_worker, create_model, create_order, get_order


def setup_order(app):
    with app.app_context():
        model_id = create_model("HR-255", "HR255")
        worker_a = create_inspection_worker("E001", "王小明")
        worker_b = create_inspection_worker("E002", "李小華")
        order_id = create_order("FIX-001", model_id, "E27-01")
        return worker_a, worker_b, order_id


def correction_data(worker_id, **overrides):
    data = {
        "worker_id": str(worker_id),
        "started_at": "2026-10-29T08:00",
        "finished_at": "",
        "reason": "修正現場漏掃資料",
        "operator_name": "主管甲",
        "password": "TJR",
        "next": "/reports",
    }
    data.update(overrides)
    return data


def pause_data(**overrides):
    data = {
        "pause_type": "material",
        "paused_at": "2026-10-29T08:30",
        "resumed_at": "2026-10-29T09:00",
        "reason": "補登缺料紀錄",
        "operator_name": "主管甲",
        "password": "TJR",
        "next": "/reports",
    }
    data.update(overrides)
    return data


def test_admin_order_correction_requires_password_backs_up_and_audits(client, app):
    _, worker_b, order_id = setup_order(app)
    page = client.get(f"/orders/{order_id}/edit").get_data(as_text=True)
    assert "管理員錯誤修正" in page
    assert 'name="password"' in page

    rejected = client.post(
        f"/orders/{order_id}/admin-correct",
        data=correction_data(worker_b, password="wrong"),
        follow_redirects=True,
    )
    assert "管理密碼錯誤" in rejected.get_data(as_text=True)
    assert not list(Path(app.config["BACKUP_DIRECTORY"]).glob("inspection_backup_*.db"))
    with app.app_context():
        assert get_order(order_id)["status"] == "pending"

    response = client.post(
        f"/orders/{order_id}/admin-correct",
        data=correction_data(worker_b, finished_at="2026-10-29T10:00"),
    )
    assert response.status_code == 302

    backups = list(Path(app.config["BACKUP_DIRECTORY"]).glob("inspection_backup_*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT status FROM inspection_order WHERE id=?", (order_id,)).fetchone()[0] == "pending"

    with app.app_context():
        order = get_order(order_id)
        assert order["status"] == "completed"
        assert order["worker_id"] == worker_b
        assert order["started_at"] == "2026-10-29T00:00:00+00:00"
        assert order["finished_at"] == "2026-10-29T02:00:00+00:00"
        audit = get_db().execute(
            "SELECT * FROM inspection_audit_log WHERE action='admin_correct_order' AND entity_id=?",
            (str(order_id),),
        ).fetchone()
        details = json.loads(audit["details"])
        assert audit["operator_name"] == "主管甲"
        assert details["reason"] == "修正現場漏掃資料"
        assert details["old"]["status"] == "pending"
        assert details["new"]["status"] == "completed"
        assert details["backup"].endswith(backups[0].name)


def test_admin_can_add_and_correct_pause_without_allowing_overlap(client, app):
    worker_a, _, order_id = setup_order(app)
    client.post(f"/orders/{order_id}/admin-correct", data=correction_data(worker_a))

    added = client.post(f"/orders/{order_id}/pauses/add", data=pause_data())
    assert added.status_code == 302
    with app.app_context():
        pause = get_db().execute("SELECT * FROM inspection_material_pause WHERE order_id=?", (order_id,)).fetchone()
        pause_id = pause["id"]
        assert pause["paused_at"] == "2026-10-29T00:30:00+00:00"
        assert pause["resumed_at"] == "2026-10-29T01:00:00+00:00"

    corrected = client.post(
        f"/orders/{order_id}/pauses/material/{pause_id}/correct",
        data=pause_data(
            paused_at="2026-10-29T08:40",
            resumed_at="2026-10-29T09:10",
            reason="修正缺料與補料時間",
        ),
    )
    assert corrected.status_code == 302
    backup_count = len(list(Path(app.config["BACKUP_DIRECTORY"]).glob("inspection_backup_*.db")))
    assert backup_count == 3

    overlap = client.post(
        f"/orders/{order_id}/pauses/add",
        data=pause_data(
            pause_type="manual",
            paused_at="2026-10-29T08:50",
            resumed_at="2026-10-29T09:05",
            reason="錯誤的重疊測試",
        ),
        follow_redirects=True,
    )
    assert "暫停區間不可與既有缺料或人工暫停重疊" in overlap.get_data(as_text=True)
    assert len(list(Path(app.config["BACKUP_DIRECTORY"]).glob("inspection_backup_*.db"))) == backup_count

    with app.app_context():
        pause = get_db().execute("SELECT * FROM inspection_material_pause WHERE id=?", (pause_id,)).fetchone()
        assert pause["paused_at"] == "2026-10-29T00:40:00+00:00"
        assert pause["resumed_at"] == "2026-10-29T01:10:00+00:00"
        assert get_db().execute("SELECT COUNT(*) FROM inspection_manual_pause WHERE order_id=?", (order_id,)).fetchone()[0] == 0
        actions = [
            row["action"]
            for row in get_db().execute(
                "SELECT action FROM inspection_audit_log WHERE entity_id=? AND action LIKE 'admin_%' ORDER BY created_at, rowid",
                (str(order_id),),
            ).fetchall()
        ]
        assert actions == ["admin_correct_order", "admin_add_pause", "admin_correct_pause"]

    page = client.get(f"/orders/{order_id}/edit").get_data(as_text=True)
    assert "管理員修正紀錄" in page
    assert "修正缺料與補料時間" in page


def test_admin_cannot_finish_order_with_open_pause(client, app):
    worker_a, _, order_id = setup_order(app)
    client.post(f"/orders/{order_id}/admin-correct", data=correction_data(worker_a))
    client.post(
        f"/orders/{order_id}/pauses/add",
        data=pause_data(pause_type="manual", paused_at="2026-10-29T10:00", resumed_at="", reason="補登人工暫停"),
    )
    backup_count = len(list(Path(app.config["BACKUP_DIRECTORY"]).glob("inspection_backup_*.db")))

    response = client.post(
        f"/orders/{order_id}/admin-correct",
        data=correction_data(worker_a, finished_at="2026-10-29T11:00", reason="誤填完成時間"),
        follow_redirects=True,
    )

    assert "FINISH 前必須完成所有暫停" in response.get_data(as_text=True)
    assert len(list(Path(app.config["BACKUP_DIRECTORY"]).glob("inspection_backup_*.db"))) == backup_count
    with app.app_context():
        assert get_order(order_id)["status"] == "started"

