from datetime import datetime
from pathlib import Path

from app.db import get_db
from app.services import TAIPEI_TZ, create_inspection_worker, create_model, create_order, create_packing_worker, format_datetime, get_order, packing_scan, scan


def _barcode(table, worker_id):
    return get_db().execute(f"SELECT barcode FROM {table} WHERE id=?", (worker_id,)).fetchone()["barcode"]


def _order_barcode(order_id, kind):
    return get_db().execute(
        "SELECT payload FROM inspection_barcode_record WHERE order_id=? AND barcode_type=?",
        (order_id, kind),
    ).fetchone()["payload"]


def _setup(app):
    with app.app_context():
        model_id = create_model("HR-255", "HR255")
        inspection_worker_id = create_inspection_worker("I001", "校機員")
        packing_worker_a = create_packing_worker("P001", "包裝甲")
        packing_worker_b = create_packing_worker("P002", "包裝乙")
        order_id = create_order("PACK-001", model_id, "E27-01")
        return inspection_worker_id, packing_worker_a, packing_worker_b, order_id


def _complete_inspection(worker_id, order_id):
    assert scan(_barcode("inspection_worker", worker_id))["result"] == "success"
    assert scan(_order_barcode(order_id, "START"), worker_id)["result"] == "success"
    assert scan(_barcode("inspection_worker", worker_id))["result"] == "success"
    assert scan(_order_barcode(order_id, "FINISH"), worker_id)["result"] == "success"


def test_packing_auto_completes_pending_inspection_and_uses_same_worker(app):
    inspection_worker_id, packing_worker_a, packing_worker_b, order_id = _setup(app)
    with app.app_context():
        start = _order_barcode(order_id, "START")
        finish = _order_barcode(order_id, "FINISH")
        assert packing_scan(_barcode("packing_worker", packing_worker_a))["result"] == "success"
        assert packing_scan(start, packing_worker_a)["result"] == "success"
        auto_completed = get_order(order_id)
        expected = datetime.now(TAIPEI_TZ).date().isoformat() + " 00:00:00"
        assert auto_completed["status"] == "completed"
        assert format_datetime(auto_completed["started_at"]) == expected
        assert format_datetime(auto_completed["finished_at"]) == expected
        audit = get_db().execute(
            "SELECT action FROM inspection_audit_log WHERE action='auto_complete_inspection_for_packing' AND entity_id=?",
            (str(order_id),),
        ).fetchone()
        assert audit["action"] == "auto_complete_inspection_for_packing"
        assert packing_scan(_barcode("packing_worker", packing_worker_b))["result"] == "success"
        wrong_finish = packing_scan(finish, packing_worker_b)
        assert wrong_finish["result"] == "invalid"
        assert "原人員" in wrong_finish["message"]
        assert packing_scan(_barcode("packing_worker", packing_worker_a))["result"] == "success"
        assert packing_scan(finish, packing_worker_a)["result"] == "success"
        assert packing_scan(finish, packing_worker_a)["result"] == "duplicate"

        order = get_db().execute("SELECT * FROM inspection_order WHERE id=?", (order_id,)).fetchone()
        assert order["packing_status"] == "completed"
        assert order["packing_worker_id"] == packing_worker_a
        assert order["packing_worker_name_snapshot"] == "包裝甲"
        assert order["packing_started_at"]
        assert order["packing_finished_at"]
        assert get_db().execute("SELECT COUNT(*) FROM inspection_scan_event WHERE process_stage='packing'").fetchone()[0] >= 6


def test_packing_routes_keep_the_packing_worker_session(client, app):
    inspection_worker_id, packing_worker_a, _, order_id = _setup(app)
    with app.app_context():
        _complete_inspection(inspection_worker_id, order_id)
        worker_barcode = _barcode("packing_worker", packing_worker_a)
        start = _order_barcode(order_id, "START")
    page = client.get("/packing")
    assert page.status_code == 200
    assert "包裝掃描" in page.get_data(as_text=True)
    employee = client.post("/api/packing/scan", json={"payload": worker_barcode})
    assert employee.status_code == 200
    started = client.post("/api/packing/scan", json={"payload": start})
    assert started.status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT packing_status FROM inspection_order WHERE id=?", (order_id,)).fetchone()["packing_status"] == "started"


def test_completed_order_can_be_manually_marked_packed(client, app):
    inspection_worker_id, _, _, order_id = _setup(app)
    with app.app_context():
        _complete_inspection(inspection_worker_id, order_id)
        assert get_order(order_id)["packing_status"] == "pending"

    page = client.get(f"/orders/{order_id}/edit").get_data(as_text=True)
    assert "完成包裝" in page
    response = client.post(f"/orders/{order_id}/packing-complete", data={"next": f"/orders/{order_id}/edit"})
    assert response.status_code == 302
    assert response.location.endswith(f"/orders/{order_id}/edit")

    expected = datetime.now(TAIPEI_TZ).date().isoformat() + " 00:00:00"
    with app.app_context():
        order = get_order(order_id)
        assert order["packing_status"] == "completed"
        assert format_datetime(order["packing_started_at"]) == expected
        assert format_datetime(order["packing_finished_at"]) == expected
        audit = get_db().execute(
            "SELECT action FROM inspection_audit_log WHERE action='manual_packing_complete' AND entity_id=?",
            (str(order_id),),
        ).fetchone()
        assert audit["action"] == "manual_packing_complete"
        assert list(Path(app.config["BACKUP_DIRECTORY"]).glob("inspection_backup_*.db"))


def test_system_portal_separates_inspection_and_packing(client):
    portal = client.get("/")
    page = portal.get_data(as_text=True)
    assert portal.status_code == 200
    assert 'href="/inspection"' in page
    assert 'href="/packing"' in page

    packing_page = client.get("/packing").get_data(as_text=True)
    assert "包裝掃描" in packing_page
    assert "人員設定" in packing_page
    assert "管理設定" in packing_page
    assert "page-utility-link" in packing_page
    assert "系統首頁" in packing_page
    assert "建立訂單" not in packing_page
    assert 'class="brand" href="/packing"' in packing_page

    workers_page = client.get("/packing-workers").get_data(as_text=True)
    assert "新增包裝人員" in workers_page
    assert "刪除" in workers_page

    inspection_page = client.get("/inspection").get_data(as_text=True)
    assert 'class="brand" href="/inspection"' in inspection_page
