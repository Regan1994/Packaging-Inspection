import sqlite3
from pathlib import Path

from app.db import get_db
from app.services import (
    backup_database,
    create_inspection_worker,
    create_model,
    create_order,
    ensure_order_barcodes,
    get_order,
    get_inspection_worker,
    make_order_payload,
    query_report,
    scan,
    set_inspection_worker_active,
    summarize_report_by_axis,
    summarize_report_by_worker,
    update_model,
    verify_sqlite_backup,
)


def setup_data(app):
    with app.app_context():
        model_id = create_model("HR-255", "HR255")
        worker_a = create_inspection_worker("E001", "王小明")
        worker_b = create_inspection_worker("E002", "李小華")
        order_a = create_order("A001", model_id, "E27-01")
        order_b = create_order("A002", model_id, "E27-01")
        return model_id, worker_a, worker_b, order_a, order_b


def payload_for(order_id, kind):
    row = get_db().execute("SELECT payload FROM inspection_barcode_record WHERE order_id=? AND barcode_type=?", (order_id, kind)).fetchone()
    return row["payload"]


def worker_barcode(worker_id):
    return get_db().execute("SELECT barcode FROM inspection_worker WHERE id=?", (worker_id,)).fetchone()["barcode"]


def test_orders_allow_duplicate_machine_serial_but_not_order_number(app):
    model_id, *_ = setup_data(app)
    with app.app_context():
        assert get_order(1)["machine_serial"] == "E27-01"
        assert len(payload_for(1, "START")) == 12
        try:
            create_order("A001", model_id, "E27-02")
        except ValueError as exc:
            assert "訂單號碼已存在" in str(exc)
        else:
            raise AssertionError("duplicate order number was accepted")


def test_order_form_accepts_typed_model_and_records_model_master(client, app):
    response = client.post("/orders/new", data={"order_no": "FORM-001", "model_name": "現場輸入機型", "machine_serial": "M-001"})
    assert response.status_code == 302
    with app.app_context():
        order = get_db().execute("SELECT * FROM inspection_order WHERE order_no='FORM-001'").fetchone()
        model = get_db().execute("SELECT * FROM inspection_model_master WHERE id=?", (order["model_id"],)).fetchone()
        assert model["model_name"] == "現場輸入機型"
        assert order["model_name_snapshot"] == "現場輸入機型"


def test_order_form_can_reuse_existing_model(client, app):
    with app.app_context():
        create_model("HR-255", "HR255")
    response = client.post("/orders/new", data={"order_no": "EXISTING-MODEL-001", "model_name": "HR-255", "machine_serial": "E27-01"})
    assert response.status_code == 302
    with app.app_context():
        order = get_db().execute("SELECT * FROM inspection_order WHERE order_no=?", ("EXISTING-MODEL-001",)).fetchone()
        assert order["model_id"] == get_db().execute("SELECT id FROM inspection_model_master WHERE model_name=?", ("HR-255",)).fetchone()["id"]


def test_report_drops_date_filters_and_can_delete_pending_order(client, app):
    _, _, _, order_a, _ = setup_data(app)
    report = client.get("/reports")
    page = report.get_data(as_text=True)
    assert "日期起" not in page
    assert "日期迄" not in page
    assert "訂單明細" in page
    with client.session_transaction() as session:
        token = session["order_delete_token"]

    response = client.post(f"/orders/{order_a}/delete", data={
        "csrf_token": token,
        "expected_order_no": "A001",
        "confirmation": "刪除",
        "next": "/reports",
    })
    assert response.status_code == 302
    assert response.location.endswith("/reports")
    with app.app_context():
        assert get_order(order_a) is None
        assert get_db().execute("SELECT COUNT(*) FROM inspection_barcode_record WHERE order_id=?", (order_a,)).fetchone()[0] == 0
        assert get_db().execute("SELECT COUNT(*) FROM inspection_audit_log WHERE action='delete' AND entity_id=?", (str(order_a),)).fetchone()[0] == 1
    assert list(Path(app.config["BACKUP_DIRECTORY"]).glob("inspection_backup_*.db"))


def test_worker_can_be_edited_and_deleted(client, app):
    rejected = client.post("/inspection-workers", data={"employee_no": "E0001", "name": "過長編號"}, follow_redirects=True)
    assert "員工編號最多四碼" in rejected.get_data(as_text=True)
    with app.app_context():
        worker_id = create_inspection_worker("E009", "待修正")

    response = client.post(f"/inspection-workers/{worker_id}/update", data={"employee_no": "E099", "name": "已修正"})
    assert response.status_code == 302
    with app.app_context():
        worker = get_db().execute("SELECT * FROM inspection_worker WHERE id=?", (worker_id,)).fetchone()
        assert worker["employee_no"] == "E099"
        assert worker["name"] == "已修正"

    response = client.post(f"/inspection-workers/{worker_id}/delete", data={"expected_employee_no": "E099", "confirmation": "刪除"})
    assert response.status_code == 302
    with app.app_context():
        assert get_inspection_worker(worker_id) is None


def test_report_detail_opens_order_actions_and_cancel_is_available(client, app):
    _, _, _, order_a, _ = setup_data(app)
    page = client.get("/reports").get_data(as_text=True)
    assert f"/orders/{order_a}/edit" in page

    edit = client.get(f"/orders/{order_a}/edit").get_data(as_text=True)
    assert "列印 Code" in edit
    assert "取消訂單" in edit
    assert "刪除訂單" in edit

    response = client.post(f"/orders/{order_a}/cancel", data={"next": f"/orders/{order_a}/edit"})
    assert response.status_code == 302
    assert response.location.endswith(f"/orders/{order_a}/edit")
    with app.app_context():
        assert get_order(order_a)["status"] == "cancelled"

    recent = client.get("/orders/recent")
    assert recent.status_code == 302
    assert recent.location.endswith("/reports")


def test_axis_type_is_saved_and_reported(client, app):
    with app.app_context():
        four_axis = create_model("HR-255", axis_type="4")
        five_axis = create_model("FHR-255", axis_type="5")
        create_order("AXIS-4", four_axis, "4-01")
        create_order("AXIS-5", five_axis, "5-01")
        create_order("AXIS-SERIAL", four_axis, "S-01")
        rows = query_report({})
        summary = summarize_report_by_axis(rows)
        assert [(item["axis_label"], item["total"]) for item in summary] == [("四軸", 1), ("五軸", 2)]

    page = client.get("/reports?axis_type=5").get_data(as_text=True)
    assert "AXIS-5" in page
    assert "AXIS-SERIAL" in page
    assert "AXIS-4" not in page
    axis_section = page.split("<h2>軸別統計</h2>", 1)[1].split("<h2>訂單明細</h2>", 1)[0]
    assert "<td>四軸</td><td>1</td>" in axis_section
    assert "<td>五軸</td><td>2</td>" in axis_section
    with app.app_context():
        update_model(four_axis, "MHR-255")
        assert get_order(get_db().execute("SELECT id FROM inspection_order WHERE order_no='AXIS-4'").fetchone()["id"])["axis_type_snapshot"] == "5"
    dashboard = client.get("/").get_data(as_text=True)
    assert "四軸" in dashboard and "五軸" in dashboard


def test_model_code_is_generated_and_not_shown_in_model_page(client, app):
    response = client.post("/models", data={"model_name": "旋轉機型", "description": "現場用"}, follow_redirects=True)
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "機型代碼" not in page
    assert "<th>代碼</th>" not in page
    with app.app_context():
        model = get_db().execute("SELECT model_code FROM inspection_model_master WHERE model_name=?", ("旋轉機型",)).fetchone()
        assert model["model_code"].startswith("MODEL-")


def test_employee_start_finish_is_bound_and_consumed(client, app):
    _, worker_a, _, order_a, _ = setup_data(app)
    with app.app_context():
        employee = client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        assert employee.json["scan_kind"] == "employee"
        start = client.post("/api/scan", json={"payload": payload_for(order_a, "START")})
        assert start.json["result"] == "success"
        finish_without_employee = client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")})
        assert finish_without_employee.status_code == 400
        assert "先掃描" in finish_without_employee.json["message"]
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        finish = client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")})
        assert finish.json["result"] == "success"
        assert get_order(order_a)["status"] == "completed"
        assert get_order(order_a)["worker_no_snapshot"] == "E001"


def test_finish_requires_original_worker_and_duplicate_is_idempotent(client, app):
    _, worker_a, worker_b, order_a, _ = setup_data(app)
    with app.app_context():
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        assert client.post("/api/scan", json={"payload": payload_for(order_a, "START")}).json["result"] == "success"
        client.post("/api/scan", json={"payload": worker_barcode(worker_b)})
        response = client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")})
        assert response.status_code == 400
        assert "原認領" in response.json["message"]
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        assert client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")}).json["result"] == "success"
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        duplicate = client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")})
        assert duplicate.json["result"] == "duplicate"


def test_inactive_worker_is_rejected(app, client):
    _, worker_a, _, order_a, _ = setup_data(app)
    with app.app_context():
        set_inspection_worker_active(worker_a, False)
        response = client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        assert response.status_code == 400
        assert "停用" in response.json["message"]
        assert client.post("/api/scan", json={"payload": payload_for(order_a, "START")}).status_code == 400


def test_well_formed_unknown_order_barcode_is_recorded_without_foreign_key_error(client, app):
    _, worker_a, _, _, _ = setup_data(app)
    with app.app_context():
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        response = client.post("/api/scan", json={"payload": make_order_payload("START", 999999)})
        assert response.status_code == 400
        assert response.json["result"] == "invalid"
        event = get_db().execute("SELECT order_id,result FROM inspection_scan_event ORDER BY scanned_at DESC LIMIT 1").fetchone()
        assert event["order_id"] is None
        assert event["result"] == "invalid"


def test_finish_rejects_timestamp_before_start(client, app):
    _, worker_a, _, order_a, _ = setup_data(app)
    with app.app_context():
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        assert client.post("/api/scan", json={"payload": payload_for(order_a, "START")}).json["result"] == "success"
        get_db().execute("UPDATE inspection_order SET started_at='2999-01-01T00:00:00+00:00' WHERE id=?", (order_a,))
        get_db().commit()
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        response = client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")})
        assert response.status_code == 400
        assert "早於 START" in response.json["message"]
        assert get_order(order_a)["status"] == "started"


def test_same_machine_history_report_and_excel(client, app, tmp_path):
    _, worker_a, worker_b, order_a, order_b = setup_data(app)
    with app.app_context():
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        client.post("/api/scan", json={"payload": payload_for(order_a, "START")})
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")})
        client.post("/api/scan", json={"payload": worker_barcode(worker_b)})
        client.post("/api/scan", json={"payload": payload_for(order_b, "START")})
        rows = query_report({"machine_serial": "E27-01"})
        assert len(rows) == 2
        assert {row["order_no"] for row in rows} == {"A001", "A002"}
        assert summarize_report_by_worker(rows)[0]["total"] == 1
        output = tmp_path / "inspection.xlsx"
        from app.services import export_xlsx
        export_xlsx(rows, output)
        assert output.is_file()
    response = client.post("/reports/export", data={"machine_serial": "E27-01"})
    assert response.status_code == 200
    assert response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_excel_export_treats_user_text_as_text(app, tmp_path):
    with app.app_context():
        model_id = create_model("HR-255", "HR255")
        create_order("SAFE-001", model_id, "E27-09", customer_name="=1+1")
        output = tmp_path / "formula-safe.xlsx"
        from app.services import export_xlsx
        export_xlsx(query_report({}), output)

    from openpyxl import load_workbook
    workbook = load_workbook(output, data_only=False)
    assert workbook["訂單明細"]["E2"].value == "'=1+1"


def test_backup_is_verified(app, tmp_path):
    setup_data(app)
    with app.app_context():
        backup = backup_database(tmp_path / "backups")
        assert verify_sqlite_backup(backup) == backup
        with sqlite3.connect(backup) as db:
            assert db.execute("SELECT COUNT(*) FROM inspection_order").fetchone()[0] == 2
