import sqlite3
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from app.db import get_db
from app.services import (
    backup_database,
    create_inspection_worker,
    create_model,
    create_order,
    ensure_order_barcodes,
    format_duration,
    format_work_duration,
    get_order,
    get_inspection_worker,
    make_material_action_payload,
    make_order_payload,
    make_pause_action_payload,
    query_report,
    scan,
    set_inspection_worker_active,
    summarize_report_by_axis,
    summarize_report_by_worker,
    TAIPEI_TZ,
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


def material_barcode(action):
    return make_material_action_payload(action)


def pause_barcode():
    return make_pause_action_payload()


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
    order_form = client.get("/orders/new").get_data(as_text=True)
    assert 'id="inspection-order-form"' in order_form
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


def test_manual_start_backdates_order_and_allows_finish(client, app):
    _, worker_a, _, order_a, _ = setup_data(app)

    page = client.get(f"/orders/{order_a}/edit").get_data(as_text=True)
    assert "補登開始時間" in page

    response = client.post(
        f"/orders/{order_a}/manual-start",
        data={
            "worker_id": str(worker_a),
            "started_at": "2026-10-29T08:00",
            "reason": "機台先到現場",
            "operator_name": "admin",
            "password": "TJR",
            "next": f"/orders/{order_a}/edit",
        },
    )
    assert response.status_code == 302
    assert response.location.endswith(f"/orders/{order_a}/edit")
    with app.app_context():
        order = get_order(order_a)
        assert order["status"] == "started"
        assert order["worker_id"] == worker_a
        assert order["started_at"] == "2026-10-29T00:00:00+00:00"
        audit = get_db().execute("SELECT action,details FROM inspection_audit_log WHERE entity_id=? AND action='manual_start' LIMIT 1", (str(order_a),)).fetchone()
        assert audit["action"] == "manual_start"
        assert "機台先到現場" in audit["details"]
        assert list(Path(app.config["BACKUP_DIRECTORY"]).glob("inspection_backup_*.db"))

        employee_payload = worker_barcode(worker_a)
        finish_payload = payload_for(order_a, "FINISH")
    client.post("/api/scan", json={"payload": employee_payload})
    finish = client.post("/api/scan", json={"payload": finish_payload})
    assert finish.json["result"] == "success"


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


def test_report_uses_full_width_layout_and_separate_date_time_columns(client, app):
    page = client.get("/reports").get_data(as_text=True)

    assert '<main class="container report-container">' in page
    assert 'class="report-detail-table"' in page
    assert '<select name="axis_type">' in page
    detail_section = page.split("<h2>訂單明細</h2>", 1)[1]
    assert "<th>校機建立日期</th>" in detail_section
    assert "<th>軸別</th>" not in detail_section
    for heading in ("開始日期", "開始時間", "結束日期", "結束時間"):
        assert f"<th>{heading}</th>" in page
    assert "<th>開始</th>" not in page
    assert "<th>完成</th>" not in page
    assert "完成率" not in page


def test_manager_report_uses_requested_columns_and_creation_date_only(client, app):
    _, _, _, order_a, _ = setup_data(app)
    with app.app_context():
        get_db().execute(
            "UPDATE inspection_order SET created_at=? WHERE id=?",
            ("2026-10-28T00:00:00+00:00", order_a),
        )
        get_db().commit()

    page = client.get("/manager?order_no=A001").get_data(as_text=True)

    assert "<h2>訂單查詢結果</h2>" in page
    assert "<th>校機建立日期</th>" in page
    for heading in ("預計出貨日", "機型", "機台製號", "客戶", "校機人員", "目前狀態", "開始日期", "開始時間", "結束日期", "結束時間", "實際工時", "明細"):
        assert f"<th>{heading}</th>" in page
    assert "<td>2026-10-28</td>" in page
    assert "2026-10-28 00:00:00" not in page
    assert "<th>總經過</th>" not in page
    assert "<th>缺料時間</th>" not in page
    assert "<th>人工暫停時間</th>" not in page
    assert "每頁 25 台" in page
    assert "下載 Excel" in page
    assert 'class="manager-filter-actions"' in page
    assert page.index('class="manager-filter-enter-submit"') < page.index('manager-filter-download')
    assert '>清除</a>' in page
    assert 'href="/manager"' in page

    detail_page = client.get(f"/manager?detail_id={order_a}&order_no=A001").get_data(as_text=True)
    assert "<h2>訂單明細：A001</h2>" in detail_page
    assert "基本資料" in detail_page
    assert "時間與工時" in detail_page
    assert "缺料／人工暫停紀錄" in detail_page
    assert "<strong>校機建立日期：</strong>" not in detail_page


def test_manager_report_is_paginated_to_25_rows_and_keeps_filters(client, app):
    with app.app_context():
        model_id = create_model("閱讀器分頁測試")
        for number in range(26):
            create_order(f"MANAGER-{number:02d}", model_id, f"M-{number:02d}")

    first_page = client.get("/manager?model_name=閱讀器分頁測試").get_data(as_text=True)
    assert "共 26 筆訂單；每頁 25 台，目前第 1／2 頁" in first_page
    assert first_page.count("<tr>") == 26
    assert "MANAGER-00" not in first_page
    assert "page=2" in first_page

    second_page = client.get("/manager?model_name=閱讀器分頁測試&page=2").get_data(as_text=True)
    assert "共 26 筆訂單；每頁 25 台，目前第 2／2 頁" in second_page
    assert second_page.count("<tr>") == 2
    assert "MANAGER-00" in second_page


def test_manager_report_sorts_rows_and_marks_open_due_dates(client, app):
    with app.app_context():
        model_id = create_model("閱讀器排序測試")
        today = datetime.now(TAIPEI_TZ).date()
        create_order("A-OVERDUE", model_id, "SORT-01", expected_ship_date=(today - timedelta(days=1)).isoformat())
        create_order("B-DUE-TODAY", model_id, "SORT-02", expected_ship_date=today.isoformat())
        create_order("Z-FUTURE", model_id, "SORT-03", expected_ship_date=(today + timedelta(days=1)).isoformat())
        completed_id = create_order("Y-COMPLETED", model_id, "SORT-04", expected_ship_date=(today - timedelta(days=2)).isoformat())
        get_db().execute("UPDATE inspection_order SET status='completed' WHERE id=?", (completed_id,))
        get_db().commit()

    order_number_page = client.get("/manager?model_name=閱讀器排序測試&sort_by=order_no_desc").get_data(as_text=True)
    assert order_number_page.index("Z-FUTURE") < order_number_page.index("Y-COMPLETED") < order_number_page.index("B-DUE-TODAY") < order_number_page.index("A-OVERDUE")
    assert 'value="order_no_desc" selected' in order_number_page

    due_date_page = client.get("/manager?model_name=閱讀器排序測試&sort_by=expected_ship_date_asc").get_data(as_text=True)
    assert due_date_page.index("Y-COMPLETED") < due_date_page.index("A-OVERDUE") < due_date_page.index("B-DUE-TODAY") < due_date_page.index("Z-FUTURE")
    overdue_start = due_date_page.index("<td>A-OVERDUE</td>")
    completed_start = due_date_page.index("<td>Y-COMPLETED</td>")
    assert 'class="manager-row-overdue"' in due_date_page[overdue_start - 80:overdue_start]
    assert "已逾期" in due_date_page[overdue_start:overdue_start + 500]
    assert 'class="manager-row-due-today"' in due_date_page
    assert "今日到期" in due_date_page
    assert 'class="manager-row-overdue"' not in due_date_page[completed_start - 80:completed_start]

    response = client.get("/manager/export?model_name=閱讀器排序測試&sort_by=order_no_desc")
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(response.data), data_only=True)
    detail = workbook["訂單明細"]
    assert [detail.cell(row=index, column=1).value for index in range(2, 6)] == ["Z-FUTURE", "Y-COMPLETED", "B-DUE-TODAY", "A-OVERDUE"]


def test_manager_export_reuses_the_full_main_system_excel(client, app):
    _, _, _, order_a, order_b = setup_data(app)
    response = client.get("/manager/export?order_no=A00")

    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(response.data), data_only=True)
    assert workbook.sheetnames == ["未認領", "訂單明細", "軸別統計"]
    summary = workbook["未認領"]
    assert [cell.value for cell in summary[1]] == ["校機人員", "未認領", None, None]
    assert [cell.value for cell in summary[2]] == ["四軸總台數", 2, "五軸總台數", 0]
    assert [cell.value for cell in summary[4]] == ["軸別", "機型", "台數", None]
    assert [cell.value for cell in summary[5]] == ["四軸", "HR-255", 2, None]
    assert [cell.value for cell in summary[6]] == ["四軸小計", None, 2, None]
    sheet = workbook["訂單明細"]
    assert [cell.value for cell in sheet[1]] == [
        "訂單號碼", "機型", "軸別", "機台製號", "客戶", "預計出貨日", "校機人員", "建立時間", "狀態",
        "開始日期", "開始時間", "結束日期", "結束時間", "總經過時間", "缺料時間", "人工暫停時間", "實際校機工時", "重新檢驗原因",
    ]
    assert [sheet.cell(row=index, column=1).value for index in range(2, 4)] == ["A002", "A001"]
    assert sheet.max_row == 3
    assert order_a and order_b


def test_excel_export_creates_a_person_sheet_per_worker_and_excludes_cancelled_rows(app, tmp_path):
    with app.app_context():
        four_axis_model = create_model("HR-255", "HR255")
        five_axis_model = create_model("FHR-255CL", "FHR255")
        four_axis_order = create_order("PERSON-4", four_axis_model, "E27-40")
        five_axis_order = create_order("PERSON-5", five_axis_model, "F27-50")
        cancelled_order = create_order("PERSON-CANCELLED", five_axis_model, "F27-51")
        get_db().execute(
            "UPDATE inspection_order SET worker_name_snapshot='黃信瑜' WHERE id IN (?,?,?)",
            (four_axis_order, five_axis_order, cancelled_order),
        )
        get_db().execute("UPDATE inspection_order SET status='cancelled' WHERE id=?", (cancelled_order,))
        get_db().commit()

        output = tmp_path / "person-sheets.xlsx"
        from app.services import export_xlsx

        export_xlsx(query_report({}), output)

    from openpyxl import load_workbook

    workbook = load_workbook(output, data_only=True)
    assert workbook.sheetnames == ["黃信瑜", "訂單明細", "軸別統計"]
    sheet = workbook["黃信瑜"]
    assert [cell.value for cell in sheet[1]] == ["校機人員", "黃信瑜", None, None]
    assert [cell.value for cell in sheet[2]] == ["四軸總台數", 1, "五軸總台數", 1]
    assert [cell.value for cell in sheet[5]] == ["四軸", "HR-255", 1, None]
    assert [cell.value for cell in sheet[7]] == ["五軸", "FHR-255CL", 1, None]
    assert sheet["C8"].value == 1


def test_manager_report_is_read_only(client):
    assert client.post("/manager").status_code == 405


def test_report_detail_is_paginated_to_25_rows(client, app):
    with app.app_context():
        model_id = create_model("分頁測試機型")
        for number in range(26):
            create_order(f"PAGE-{number:02d}", model_id, f"P-{number:02d}")

    first_page = client.get("/reports").get_data(as_text=True)
    first_detail = first_page.split("<h2>訂單明細</h2>", 1)[1]
    assert "共 26 筆訂單；目前第 1／2 頁" in first_detail
    assert first_detail.count("<tr>") == 26
    assert "page=2" in first_detail

    second_page = client.get("/reports?page=2").get_data(as_text=True)
    second_detail = second_page.split("<h2>訂單明細</h2>", 1)[1]
    assert "共 26 筆訂單；目前第 2／2 頁" in second_detail
    assert second_detail.count("<tr>") == 2


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


def test_scanned_worker_can_be_replaced_or_cleared(client, app):
    _, worker_a, worker_b, _, _ = setup_data(app)
    with app.app_context():
        assert client.post("/api/scan", json={"payload": worker_barcode(worker_a)}).json["worker_id"] == worker_a
        replacement = client.post("/api/scan", json={"payload": worker_barcode(worker_b)})
        assert replacement.json["worker_id"] == worker_b
        with client.session_transaction() as session:
            assert session["scan_worker_id"] == worker_b

        response = client.post("/scan/reset")
        assert response.status_code == 302
        assert response.location.endswith("/scan")
        with client.session_transaction() as session:
            assert "scan_worker_id" not in session
            assert "scan_material_action" not in session

    page = client.get("/scan").get_data(as_text=True)
    assert "請先掃描員工條碼" in page
    assert "V406.10.29" not in page
    assert "掃描操作說明" in page
    assert "列印缺料/補料條碼" in page
    assert "固定缺料條碼 → 該訂單 START" in page
    assert "固定補料條碼 → 該訂單 START" in page
    assert "固定暫停條碼 → 該訂單 START" in page
    assert '<div class="home-version" aria-label="系統版本">V701.11.03</div>' in client.get("/").get_data(as_text=True)


def test_scan_accepts_scanner_prefix_and_existing_legacy_barcodes(client, app):
    _, worker_a, _, order_a, _ = setup_data(app)
    with app.app_context():
        employee_payload = worker_barcode(worker_a)
        assert client.post("/api/scan", json={"payload": f"]C1\x02{employee_payload}\r\n"}).json["worker_id"] == worker_a

        get_db().execute(
            "UPDATE inspection_barcode_record SET payload='LEGACY-START-A001' WHERE order_id=? AND barcode_type='START'",
            (order_a,),
        )
        get_db().commit()
        response = client.post("/api/scan", json={"payload": "]C1LEGACY-START-A001\r"})
        assert response.status_code == 200
        assert response.json["result"] == "success"
        assert get_order(order_a)["status"] == "started"


def test_scan_accepts_lowercase_compact_barcodes(client, app):
    _, worker_a, _, order_a, _ = setup_data(app)
    with app.app_context():
        assert client.post("/api/scan", json={"payload": worker_barcode(worker_a).lower()}).json["worker_id"] == worker_a
        started = client.post("/api/scan", json={"payload": payload_for(order_a, "START").lower()})
        assert started.status_code == 200
        assert started.json["result"] == "success"

        assert client.post("/api/scan", json={"payload": worker_barcode(worker_a).lower()}).status_code == 200
        shortage = client.post("/api/scan", json={"payload": material_barcode("SHORTAGE").lower()})
        assert shortage.status_code == 200
        assert shortage.json["material_action"] == "SHORTAGE"


def test_blank_database_scan_explains_instance_folder(client):
    response = client.post("/api/scan", json={"payload": "UNKNOWN"})
    assert response.status_code == 400
    assert "空白資料庫" in response.json["message"]
    assert "instance" in response.json["message"]


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


def test_material_shortage_pause_resume_and_net_work_time(client, app):
    _, worker_a, _, order_a, _ = setup_data(app)
    with app.app_context():
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        assert client.post("/api/scan", json={"payload": payload_for(order_a, "START")}).json["result"] == "success"

        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        action = client.post("/api/scan", json={"payload": material_barcode("SHORTAGE")})
        assert action.json["result"] == "success"
        assert "訂單 START" in action.json["message"]
        with client.session_transaction() as session:
            assert session["scan_material_action"] == "SHORTAGE"
            assert session["scan_worker_id"] == worker_a
        paused = client.post("/api/scan", json={"payload": payload_for(order_a, "START")})
        assert paused.json["result"] == "success"
        assert "停止計算工時" in paused.json["message"]
        assert get_order(order_a)["material_paused"] == 1
        assert len(query_report({"status_group": "material_paused"})) == 1

        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        client.post("/api/scan", json={"payload": material_barcode("SHORTAGE")})
        duplicate = client.post("/api/scan", json={"payload": payload_for(order_a, "START")})
        assert duplicate.json["result"] == "duplicate"
        assert get_db().execute("SELECT COUNT(*) FROM inspection_material_pause WHERE order_id=?", (order_a,)).fetchone()[0] == 1

        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        blocked_finish = client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")})
        assert blocked_finish.status_code == 400
        assert "先完成補料" in blocked_finish.json["message"]

        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        client.post("/api/scan", json={"payload": material_barcode("REPLENISH")})
        resumed = client.post("/api/scan", json={"payload": payload_for(order_a, "START")})
        assert resumed.json["result"] == "success"
        assert "繼續計算工時" in resumed.json["message"]
        assert get_order(order_a)["material_paused"] == 0

        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        assert client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")}).json["result"] == "success"
        db = get_db()
        db.execute("UPDATE inspection_order SET started_at='2026-10-28T00:00:00+00:00',finished_at='2026-10-28T02:00:00+00:00' WHERE id=?", (order_a,))
        db.execute("UPDATE inspection_material_pause SET paused_at='2026-10-28T00:30:00+00:00',resumed_at='2026-10-28T01:00:00+00:00' WHERE order_id=?", (order_a,))
        db.commit()
        report_row = query_report({"order_no": "A001"})[0]
        assert report_row["material_pause_seconds"] == 1800
        assert format_work_duration(report_row["started_at"], report_row["finished_at"], report_row["pause_intervals"]) == "01:30:00"


def test_manual_pause_resume_excludes_time_from_actual_work_and_blocks_finish(client, app):
    _, worker_a, _, order_a, _ = setup_data(app)
    with app.app_context():
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        assert client.post("/api/scan", json={"payload": payload_for(order_a, "START")}).json["result"] == "success"

        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        pause_selected = client.post("/api/scan", json={"payload": pause_barcode()})
        assert pause_selected.json["result"] == "success"
        assert pause_selected.json["pause_action"] == "PAUSE"
        paused = client.post("/api/scan", json={"payload": payload_for(order_a, "START")})
        assert paused.json["result"] == "success"
        assert "暫停成功" in paused.json["message"]
        assert get_order(order_a)["manual_paused"] == 1

        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        blocked_finish = client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")})
        assert blocked_finish.status_code == 400
        assert "人工暫停" in blocked_finish.json["message"]

        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        resumed = client.post("/api/scan", json={"payload": payload_for(order_a, "START")})
        assert resumed.json["result"] == "success"
        assert "恢復成功" in resumed.json["message"]
        assert get_order(order_a)["manual_paused"] == 0

        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        assert client.post("/api/scan", json={"payload": payload_for(order_a, "FINISH")}).json["result"] == "success"

        db = get_db()
        db.execute("UPDATE inspection_order SET started_at='2026-10-28T00:00:00+00:00',finished_at='2026-10-28T03:00:00+00:00' WHERE id=?", (order_a,))
        db.execute("UPDATE inspection_manual_pause SET paused_at='2026-10-28T01:00:00+00:00',resumed_at='2026-10-28T02:00:00+00:00' WHERE order_id=?", (order_a,))
        db.commit()
        report_row = query_report({"order_no": "A001"})[0]
        assert report_row["manual_pause_seconds"] == 3600
        assert format_work_duration(report_row["started_at"], report_row["finished_at"], report_row["pause_intervals"]) == "02:00:00"


def test_work_duration_uses_weekday_overtime_and_weekend_scan_rules():
    assert format_work_duration("2026-10-29T00:00:00+00:00", "2026-10-30T02:00:00+00:00") == "10:00:00"
    assert format_work_duration("2026-10-29T00:00:00+00:00", "2026-10-29T12:00:00+00:00") == "10:30:00"
    assert format_work_duration("2026-10-30T00:00:00+00:00", "2026-11-02T02:00:00+00:00") == "10:00:00"
    assert format_work_duration("2026-10-29T08:00:00+00:00", "2026-10-29T12:00:00+00:00") == "03:30:00"
    assert format_work_duration("2026-10-31T01:00:00+00:00", "2026-10-31T12:00:00+00:00") == "09:30:00"
    assert format_work_duration("2026-11-01T01:00:00+00:00", "2026-11-01T07:00:00+00:00") == "05:00:00"


def test_work_duration_deducts_only_pause_time_inside_work_windows():
    pause_intervals = [{
        "paused_at": "2026-11-02T03:38:40+00:00",
        "resumed_at": "2026-11-03T02:21:44+00:00",
    }]
    assert format_work_duration(
        "2026-10-30T08:30:41+00:00",
        "2026-11-03T02:21:44+00:00",
        pause_intervals,
    ) == "04:07:59"


def test_material_barcodes_print_and_replenish_requires_open_pause(client, app):
    _, worker_a, _, order_a, _ = setup_data(app)
    page = client.get("/material-barcodes/print")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "缺料固定條碼" in html
    assert "補料固定條碼" in html
    assert material_barcode("SHORTAGE") in html
    assert material_barcode("REPLENISH") in html

    with app.app_context():
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        client.post("/api/scan", json={"payload": payload_for(order_a, "START")})
        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        client.post("/api/scan", json={"payload": material_barcode("REPLENISH")})
        response = client.post("/api/scan", json={"payload": payload_for(order_a, "START")})
        assert response.status_code == 400
        assert "沒有缺料暫停" in response.json["message"]

        client.post("/api/scan", json={"payload": worker_barcode(worker_a)})
        client.post("/api/scan", json={"payload": material_barcode("SHORTAGE")})
        assert client.post("/api/scan", json={"payload": payload_for(order_a, "START")}).json["result"] == "success"
        assert client.post(f"/orders/{order_a}/cancel", data={"next": "/reports"}).status_code == 302
        pause = get_db().execute("SELECT * FROM inspection_material_pause WHERE order_id=?", (order_a,)).fetchone()
        assert pause["resumed_at"] is not None
        assert get_order(order_a)["material_paused"] == 0


def test_manual_pause_barcode_print_page(client):
    page = client.get("/pause-barcodes/print")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "暫停固定條碼" in html
    assert pause_barcode() in html


def test_existing_inspection_database_adds_material_pause_table_without_losing_orders(app):
    setup_data(app)
    database = app.config["DATABASE"]
    backup_directory = app.config["BACKUP_DIRECTORY"]
    with app.app_context():
        db = get_db()
        db.execute("DROP TABLE inspection_material_pause")
        db.execute("DELETE FROM inspection_schema_migrations WHERE version=3")
        db.commit()

    from app import create_app
    upgraded = create_app({"TESTING": True, "DATABASE": database, "BACKUP_DIRECTORY": backup_directory, "SECRET_KEY": "upgrade-test"})
    with upgraded.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM inspection_order").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM inspection_material_pause").fetchone()[0] == 0
        assert db.execute("SELECT 1 FROM inspection_schema_migrations WHERE version=3").fetchone() is not None


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
        get_db().execute("UPDATE inspection_order SET started_at='2026-10-28T01:02:03+00:00',finished_at='2026-10-28T04:05:06+00:00' WHERE id=?", (order_a,))
        get_db().commit()
        rows = query_report({"machine_serial": "E27-01"})
        assert len(rows) == 2
        assert {row["order_no"] for row in rows} == {"A001", "A002"}
        assert summarize_report_by_worker(rows)[0]["total"] == 1
        output = tmp_path / "inspection.xlsx"
        from app.services import export_xlsx
        export_xlsx(rows, output)
        assert output.is_file()
        from openpyxl import load_workbook
        workbook = load_workbook(output, read_only=True)
        detail = workbook["訂單明細"]
        assert [cell.value for cell in detail[1]] == ["訂單號碼", "機型", "軸別", "機台製號", "客戶", "預計出貨日", "校機人員", "建立時間", "狀態", "開始日期", "開始時間", "結束日期", "結束時間", "總經過時間", "缺料時間", "人工暫停時間", "實際校機工時", "重新檢驗原因"]
        assert "員工編號" not in [cell.value for cell in detail[1]]
        exported_rows = {values[0]: values for values in detail.iter_rows(min_row=2, values_only=True)}
        assert exported_rows["A001"][9:13] == ("2026-10-28", "09:02:03", "2026-10-28", "12:05:06")
    response = client.post("/reports/export", data={"machine_serial": "E27-01"})
    assert response.status_code == 200
    assert response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert f"{datetime.now():%Y%m%d}Report.xlsx" in response.headers["Content-Disposition"]


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
