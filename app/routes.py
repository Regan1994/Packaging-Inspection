import tempfile
import hmac
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from .barcode import code128_svg
from .db import get_db
from .services import ASSEMBLY_STAGES, DEBUG_CLEAR_CHOICES, access_password_enabled, backup_database, change_access_password, clear_debug_data, correct_actual_qty, create_assembly_worker, create_batch, create_model, debug_clear_counts, delete_batch, delete_model, ensure_barcodes, export_xlsx, get_assembly_worker, get_batch, get_job, is_five_axis_model, list_assembly_workers, list_models, list_recent_batches, list_report_operators, parse_payload, query_report, restore_database, scan, set_access_password_enabled, set_assembly_worker_active, summarize_report_by_operator, update_model, verify_access_password

bp = Blueprint("main", __name__)

REPORT_PAGE_SIZE = 25

PROTECTED_ENDPOINTS = {
    "main.models",
    "main.model_update",
    "main.model_delete",
    "main.assembly_workers",
    "main.assembly_worker_status",
    "main.recent_batches",
    "main.batch_delete",
    "main.reports",
    "main.reports_export",
    "main.quantity_update",
    "main.settings",
    "main.change_password",
    "main.password_mode",
    "main.debug_clear",
    "main.backup",
    "main.restore",
}


def safe_next_path(value):
    return value if value and value.startswith("/") and not value.startswith("//") else url_for("main.index")


@bp.before_request
def require_admin_unlock():
    if request.endpoint not in PROTECTED_ENDPOINTS or not access_password_enabled() or session.get("admin_unlocked"):
        return None
    next_path = request.full_path.rstrip("?")
    return redirect(url_for("main.unlock", next=next_path))


def render_home(scan_result=None):
    stats = get_db().execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN status IN ('created','started') THEN 1 ELSE 0 END), 0) AS incomplete,
            COALESCE(SUM(CASE WHEN status='finished' THEN 1 ELSE 0 END), 0) AS finished,
            COALESCE(SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END), 0) AS cancelled
        FROM job_run
        """
    ).fetchone()
    cards = [
        {"label": "未完成", "count": stats["incomplete"], "filter": "incomplete"},
        {"label": "已完成", "count": stats["finished"], "filter": "finished"},
        {"label": "已取消", "count": stats["cancelled"], "filter": "cancelled"},
    ]
    return render_template("index.html", stats=cards, scan_result=scan_result)


@bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        session.pop("admin_unlocked", None)
    result = scan(request.form.get("payload", ""), "scanner") if request.method == "POST" else None
    return render_home(result)


@bp.route("/unlock", methods=["GET", "POST"])
def unlock():
    next_path = safe_next_path(request.form.get("next") if request.method == "POST" else request.args.get("next"))
    if request.method == "POST":
        if verify_access_password(request.form.get("password", "")):
            session["admin_unlocked"] = True
            return redirect(next_path)
        flash("密碼錯誤，請重新輸入", "error")
    return render_template("unlock.html", next_path=next_path)


@bp.route("/models", methods=["GET", "POST"])
def models():
    if request.method == "POST":
        try:
            create_model(request.form.get("model_name"), description=request.form.get("description", ""), operator_name=request.form.get("operator_name", "admin"), serial_prefix=request.form.get("serial_prefix"))
            flash("機型已建立", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    token = session.get("model_delete_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["model_delete_token"] = token
    return render_template("models.html", models=list_models(), delete_token=token)


@bp.post("/models/<int:model_id>/update")
def model_update(model_id):
    try:
        update_model(model_id, request.form.get("model_name"), description=request.form.get("description", ""), is_active=request.form.get("is_active") == "1", operator_name=request.form.get("operator_name", "admin"), serial_prefix=request.form.get("serial_prefix"))
        flash("機型已更新", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.models"))


@bp.post("/models/<int:model_id>/delete")
def model_delete(model_id):
    token = session.get("model_delete_token", "")
    if not token or not hmac.compare_digest(token, request.form.get("csrf_token", "")):
        flash("刪除確認已失效，請重新開啟機型主檔", "error")
        return redirect(url_for("main.models"))
    try:
        backup = delete_model(model_id, request.form.get("expected_name", ""), current_app.config["BACKUP_DIRECTORY"])
        session.pop("model_delete_token", None)
        flash(f"機型已刪除；資料庫備份：{backup.name}", "success")
    except (ValueError, OSError, sqlite3.Error) as exc:
        flash(f"沒有刪除機型：{exc}", "error")
    return redirect(url_for("main.models"))


@bp.route("/machines", methods=["GET", "POST"])
def machines():
    flash("新產品報工不需要先建立機台主檔，請直接從建立報工輸入機型、年份、起始流水號與台數。", "info")
    return redirect(url_for("main.job_new"))


@bp.post("/machines/<int:machine_id>/update")
def machine_update(machine_id):
    flash("新流程不再維護機台主檔；舊機台資料僅保留供歷史資料相容。", "info")
    return redirect(url_for("main.job_new"))


@bp.route("/assembly-workers", methods=["GET", "POST"])
def assembly_workers():
    if request.method == "POST":
        try:
            create_assembly_worker(request.form.get("name"), request.form.get("changed_by", "admin"))
            flash("組裝人員已新增", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("assembly_workers.html", workers=list_assembly_workers())


@bp.post("/assembly-workers/<int:worker_id>/status")
def assembly_worker_status(worker_id):
    try:
        set_assembly_worker_active(worker_id, request.form.get("is_active") == "1", request.form.get("changed_by", "admin"))
        flash("組裝人員狀態已更新", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.assembly_workers"))


@bp.get("/jobs/new")
def job_new():
    model_id = request.args.get("model_id")
    models = list_models(True)
    selected = next((model for model in models if str(model["id"]) == str(model_id)), None)
    return render_template("job_new.html", models=models, workers=list_assembly_workers(True), selected_model=model_id, selected_model_record=selected, is_five_axis=bool(selected and is_five_axis_model(selected)), assembly_stages=ASSEMBLY_STAGES, current_year=datetime.now().year)


@bp.post("/jobs")
def job_create():
    try:
        model_id = int(request.form["model_id"])
        worker = get_assembly_worker(request.form.get("operator_name", ""))
        if not worker:
            raise ValueError("請先選擇有效的組裝人員；如清單沒有該人員，請先新增組裝人員")
        batch_id, _ = create_batch(model_id, request.form.get("manufacturing_year"), request.form.get("starting_number"), request.form.get("product_count"), worker["name"], request.form.get("assembly_stage"))
        ensure_barcodes(batch_id)
        return redirect(url_for("main.print_batch", batch_id=batch_id))
    except (ValueError, KeyError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.job_new", model_id=request.form.get("model_id")))


@bp.get("/batches/<batch_id>/print")
def print_batch(batch_id):
    batch = get_batch(batch_id)
    if not batch:
        return "找不到批次", 404
    jobs = ensure_barcodes(batch_id)
    records = get_db().execute("SELECT * FROM barcode_record WHERE batch_id=? OR job_run_id IN (SELECT job_run_id FROM job_run WHERE batch_id=?) ORDER BY barcode_scope, CASE barcode_type WHEN 'START' THEN 0 WHEN 'FINISH' THEN 1 ELSE 2 END, job_run_id", (batch_id, batch_id)).fetchall()
    return render_template("print_batch.html", batch=batch, jobs=jobs, records=records, code128_svg=code128_svg)


@bp.get("/batches/recent")
def recent_batches():
    token = session.get("batch_delete_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["batch_delete_token"] = token
    return render_template("batches.html", batches=list_recent_batches(), csrf_token=token)


@bp.post("/batches/<batch_id>/delete")
def batch_delete(batch_id):
    token = session.get("batch_delete_token", "")
    if not token or not hmac.compare_digest(token, request.form.get("csrf_token", "")):
        flash("刪除確認已失效，請重新開啟最近批次", "error")
        return redirect(url_for("main.recent_batches"))
    try:
        backup, removed = delete_batch(
            batch_id,
            request.form.get("expected_batch_id", ""),
            request.form.get("confirmation", ""),
            current_app.config["BACKUP_DIRECTORY"],
        )
        flash(f"批次已刪除；已先備份至 {backup.name}。刪除筆數：{sum(removed.values())}", "success")
    except (ValueError, OSError, sqlite3.Error) as exc:
        flash(f"沒有刪除批次：{exc}", "error")
    return redirect(url_for("main.recent_batches"))


@bp.route("/scan", methods=["GET", "POST"])
def scan_page():
    if request.method == "POST":
        payload = request.form.get("payload", "")
        result = scan(payload, "scanner")
        result["payload"] = payload
        return render_template("scan.html", result=result)
    return render_template("scan.html", result=None)


@bp.post("/api/scan")
def api_scan():
    result = scan(request.json.get("payload", "") if request.is_json else request.form.get("payload", ""), request.json.get("operator_name", "scanner") if request.is_json else request.form.get("operator_name", "scanner"))
    return jsonify(result), 200 if result["result"] in {"success", "duplicate"} else 400


@bp.route("/reports", methods=["GET", "POST"])
def reports():
    filters = request.form.to_dict() if request.method == "POST" else request.args.to_dict()
    rows = query_report(filters)
    return render_template("reports.html", rows=rows, summaries=summarize_report_by_operator(rows), filters=filters, operators=list_report_operators())


@bp.post("/reports/export")
def reports_export():
    filters = request.form.to_dict()
    rows = query_report(filters)
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    export_xlsx(rows, tmp.name)
    filename = "INTJR_報工報表_" + datetime.now().strftime("%Y%m%d_%H%M") + ".xlsx"
    return send_file(tmp.name, as_attachment=True, download_name=filename)


@bp.post("/jobs/<job_run_id>/quantity")
def quantity_update(job_run_id):
    try:
        correct_actual_qty(job_run_id, request.form.get("actual_qty"), request.form.get("operator_name", "admin"))
        flash("完成量已更正並寫入稽核紀錄", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.reports"))


@bp.get("/settings")
def settings():
    return render_template("settings.html", password_enabled=access_password_enabled())


@bp.post("/settings/password")
def change_password():
    try:
        new_password = request.form.get("new_password", "")
        if new_password != request.form.get("confirm_password", ""):
            raise ValueError("兩次輸入的新密碼不一致")
        change_access_password(request.form.get("current_password", ""), new_password)
        flash("密碼已更新", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.settings"))


@bp.post("/settings/password-mode")
def password_mode():
    enabled = request.form.get("password_enabled") == "1"
    set_access_password_enabled(enabled)
    if not enabled:
        session.pop("admin_unlocked", None)
        flash("功能密碼已暫時停用，方便排查", "success")
    else:
        flash("功能密碼已重新啟用", "success")
    return redirect(url_for("main.settings"))


@bp.route("/debug/clear", methods=["GET", "POST"])
def debug_clear():
    if request.method == "POST":
        token = session.get("debug_clear_token", "")
        if not token or not hmac.compare_digest(token, request.form.get("csrf_token", "")):
            flash("清除確認已失效，請重新開啟頁面", "error")
            return redirect(url_for("main.debug_clear"))
        try:
            backup, removed = clear_debug_data(
                request.form.getlist("items"),
                request.form.get("confirmation", ""),
                current_app.config["BACKUP_DIRECTORY"],
            )
            session.pop("debug_clear_token", None)
            flash(f"清除完成；已先備份至 {backup}。清除筆數：{sum(removed.values())}", "success")
        except (ValueError, OSError, sqlite3.Error) as exc:
            flash(f"沒有清除資料：{exc}", "error")
        return redirect(url_for("main.debug_clear"))
    token = session.get("debug_clear_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["debug_clear_token"] = token
    return render_template("debug_clear.html", counts=debug_clear_counts(), choices=DEBUG_CLEAR_CHOICES, csrf_token=token)


@bp.route("/backup", methods=["GET", "POST"])
def backup():
    try:
        target = backup_database(current_app.config["BACKUP_DIRECTORY"])
        flash(f"備份完成：{target.name}", "success")
    except Exception as exc:
        flash(f"備份失敗：{exc}", "error")
    return redirect(url_for("main.index"))


@bp.post("/restore")
def restore():
    source = request.form.get("source_path", "").strip()
    try:
        restore_database(source, current_app.config["DATABASE"], current_app.config["BACKUP_DIRECTORY"], request.form.get("operator_name", "admin"))
        flash("還原完成，請重新啟動應用程式以重新建立資料庫連線", "success")
    except Exception as exc:
        flash(f"還原失敗：{exc}", "error")
    return redirect(url_for("main.settings"))


# The original assembly blueprint is intentionally left above for historical
# reference.  The registered blueprint below is the final-inspection UI and
# uses only inspection-prefixed service functions/tables.
from .services import (
    access_password_enabled,
    axis_type_label,
    backup_database,
    cancel_order,
    change_access_password,
    clear_debug_data,
    create_inspection_worker,
    create_model,
    create_order,
    debug_clear_counts,
    delete_order,
    delete_model,
    ensure_order_barcodes,
    ensure_worker_barcode,
    export_xlsx,
    get_inspection_worker,
    get_order,
    list_material_pauses,
    list_inspection_workers,
    list_models,
    list_report_workers,
    material_action_barcodes,
    query_report,
    restore_database,
    scan,
    set_access_password_enabled,
    set_inspection_worker_active,
    status_label,
    summarize_report_by_axis,
    summarize_report_by_worker,
    delete_inspection_worker,
    update_inspection_worker,
    update_model,
    verify_access_password,
)

bp = Blueprint("main", __name__)
PROTECTED_ENDPOINTS = {
    "main.models", "main.model_update", "main.model_delete", "main.inspection_workers",
    "main.inspection_worker_status", "main.inspection_worker_update", "main.inspection_worker_delete", "main.worker_print", "main.orders_new", "main.orders",
    "main.order_edit", "main.order_print", "main.order_cancel", "main.order_delete", "main.reports", "main.reports_export",
    "main.settings", "main.change_password", "main.password_mode", "main.debug_clear",
    "main.backup", "main.restore", "main.material_barcodes_print",
}


def safe_next_path(value):
    return value if value and value.startswith("/") and not value.startswith("//") else url_for("main.index")


@bp.before_request
def require_admin_unlock():
    if request.endpoint not in PROTECTED_ENDPOINTS or not access_password_enabled() or session.get("admin_unlocked"):
        return None
    next_path = request.full_path.rstrip("?")
    return redirect(url_for("main.unlock", next=next_path))


def _home_stats():
    row = get_db().execute(
        """
        SELECT
            SUM(o.status='pending') pending,
            SUM(o.status='started' AND NOT EXISTS(SELECT 1 FROM inspection_material_pause p WHERE p.order_id=o.id AND p.resumed_at IS NULL)) started,
            SUM(o.status='started' AND EXISTS(SELECT 1 FROM inspection_material_pause p WHERE p.order_id=o.id AND p.resumed_at IS NULL)) material_paused,
            SUM(o.status='completed') completed,
            SUM(o.status='cancelled') cancelled
        FROM inspection_order o
        """
    ).fetchone()
    labels = {"pending": "待校機", "started": "校機中", "material_paused": "缺料暫停", "completed": "已完成", "cancelled": "已取消"}
    return [{"label": labels[key], "count": row[key] or 0, "filter": key} for key in ("pending", "started", "material_paused", "completed", "cancelled")]


def _home_axis_stats():
    rows = get_db().execute("SELECT axis_type_snapshot,COUNT(*) AS count FROM inspection_order GROUP BY axis_type_snapshot").fetchall()
    counts = {str(row["axis_type_snapshot"] or "4"): row["count"] for row in rows}
    return [{"axis_type": axis_type, "label": axis_type_label(axis_type), "count": counts.get(axis_type, 0)} for axis_type in ("4", "5")]


@bp.route("/", methods=["GET"])
def index():
    current_worker = get_inspection_worker(session["scan_worker_id"]) if session.get("scan_worker_id") else None
    return render_template("dashboard.html", stats=_home_stats(), axis_stats=_home_axis_stats(), current_worker=current_worker)


@bp.route("/unlock", methods=["GET", "POST"])
def unlock():
    next_path = safe_next_path(request.form.get("next") if request.method == "POST" else request.args.get("next"))
    if request.method == "POST":
        if verify_access_password(request.form.get("password", "")):
            session["admin_unlocked"] = True
            return redirect(next_path)
        flash("密碼錯誤，請重新輸入", "error")
    return render_template("unlock.html", next_path=next_path)


@bp.route("/models", methods=["GET", "POST"])
def models():
    if request.method == "POST":
        try:
            create_model(request.form.get("model_name"), request.form.get("model_code"), request.form.get("description", ""))
            flash("機型已建立", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("inspection_models.html", models=list_models())


@bp.post("/models/<int:model_id>/update")
def model_update(model_id):
    try:
        update_model(model_id, request.form.get("model_name"), request.form.get("model_code"), request.form.get("description", ""), request.form.get("is_active") == "1")
        flash("機型已更新", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.models"))


@bp.post("/models/<int:model_id>/delete")
def model_delete(model_id):
    try:
        backup = delete_model(model_id, request.form.get("expected_name", ""), current_app.config["BACKUP_DIRECTORY"])
        flash(f"機型已刪除；資料庫備份：{backup.name}", "success")
    except (ValueError, OSError, sqlite3.Error) as exc:
        flash(f"沒有刪除機型：{exc}", "error")
    return redirect(url_for("main.models"))


@bp.route("/inspection-workers", methods=["GET", "POST"])
def inspection_workers():
    if request.method == "POST":
        try:
            create_inspection_worker(request.form.get("employee_no"), request.form.get("name"))
            flash("校機人員已新增", "success")
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("inspection_workers.html", workers=list_inspection_workers())


@bp.post("/inspection-workers/<int:worker_id>/status")
def inspection_worker_status(worker_id):
    try:
        set_inspection_worker_active(worker_id, request.form.get("is_active") == "1")
        flash("校機人員狀態已更新", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.inspection_workers"))


@bp.post("/inspection-workers/<int:worker_id>/update")
def inspection_worker_update(worker_id):
    try:
        update_inspection_worker(worker_id, request.form.get("employee_no"), request.form.get("name"))
        flash("校機人員已更新", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.inspection_workers"))


@bp.post("/inspection-workers/<int:worker_id>/delete")
def inspection_worker_delete(worker_id):
    try:
        backup = delete_inspection_worker(
            worker_id,
            request.form.get("expected_employee_no", ""),
            request.form.get("confirmation", ""),
            current_app.config["BACKUP_DIRECTORY"],
        )
        flash(f"校機人員已刪除；已先備份至 {backup.name}", "success")
    except (ValueError, OSError, sqlite3.Error) as exc:
        flash(f"沒有刪除校機人員：{exc}", "error")
    return redirect(url_for("main.inspection_workers"))


@bp.get("/inspection-workers/print")
def worker_print():
    raw_ids = request.args.getlist("ids")
    ids = {int(value) for value in raw_ids if value.isdigit()}
    workers = [ensure_worker_barcode(worker["id"]) for worker in list_inspection_workers() if worker["id"] in ids]
    return render_template("print_workers.html", workers=workers, code128_svg=code128_svg)


@bp.route("/orders/new", methods=["GET", "POST"])
def orders_new():
    if request.method == "POST":
        try:
            order_id = create_order(request.form.get("order_no"), machine_serial=request.form.get("machine_serial"), customer_name=request.form.get("customer_name"), expected_ship_date=request.form.get("expected_ship_date"), reinspection_reason=request.form.get("reinspection_reason"), model_name=request.form.get("model_name"))
            return redirect(url_for("main.order_print", order_id=order_id))
        except (ValueError, KeyError) as exc:
            flash(str(exc), "error")
    return render_template("inspection_order_new.html")


@bp.get("/orders/recent")
def orders():
    return redirect(url_for("main.reports"))


@bp.get("/orders/<int:order_id>/edit")
def order_edit(order_id):
    order = get_order(order_id)
    if not order:
        return "找不到訂單", 404
    token = session.get("order_delete_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["order_delete_token"] = token
    return render_template(
        "inspection_order_edit.html",
        order=order,
        material_pauses=list_material_pauses(order_id),
        delete_token=token,
        delete_next=url_for("main.reports"),
        edit_next=url_for("main.order_edit", order_id=order_id),
    )


@bp.get("/orders/<int:order_id>/print")
def order_print(order_id):
    order = get_order(order_id)
    if not order:
        return "找不到訂單", 404
    ensure_order_barcodes(order_id)
    records = get_db().execute("SELECT * FROM inspection_barcode_record WHERE order_id=? ORDER BY CASE barcode_type WHEN 'START' THEN 0 ELSE 1 END", (order_id,)).fetchall()
    return render_template("inspection_print_order.html", order=order, records=records, code128_svg=code128_svg)


@bp.get("/material-barcodes/print")
def material_barcodes_print():
    return render_template(
        "inspection_print_material_actions.html",
        records=material_action_barcodes(),
        code128_svg=code128_svg,
    )


@bp.post("/orders/<int:order_id>/cancel")
def order_cancel(order_id):
    try:
        cancel_order(order_id)
        flash("訂單已取消，歷史資料保留", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(safe_next_path(request.form.get("next")))


@bp.post("/orders/<int:order_id>/delete")
def order_delete(order_id):
    next_path = safe_next_path(request.form.get("next"))
    token = session.get("order_delete_token", "")
    if not token or not hmac.compare_digest(token, request.form.get("csrf_token", "")):
        flash("刪除確認已失效，請重新開啟訂單明細", "error")
        return redirect(next_path)
    try:
        backup, removed = delete_order(order_id, request.form.get("expected_order_no", ""), request.form.get("confirmation", ""), current_app.config["BACKUP_DIRECTORY"])
        session.pop("order_delete_token", None)
        flash(f"訂單已刪除；已先備份至 {backup.name}", "success")
    except (ValueError, OSError, sqlite3.Error) as exc:
        flash(f"沒有刪除訂單：{exc}", "error")
    return redirect(next_path)


@bp.post("/scan/reset")
def scan_reset():
    session.pop("scan_worker_id", None)
    session.pop("scan_material_action", None)
    flash("已清除目前員工與待處理的缺料／補料操作", "success")
    return redirect(url_for("main.scan_page"))


@bp.route("/scan", methods=["GET", "POST"])
def scan_page():
    result = None
    if request.method == "POST":
        payload = request.form.get("payload", "")
        result = scan(payload, session.get("scan_worker_id"), pending_material_action=session.get("scan_material_action"))
        result["payload"] = payload
        if result.get("scan_kind") == "employee" and result.get("worker_id"):
            session["scan_worker_id"] = result["worker_id"]
            session.pop("scan_material_action", None)
        elif result.get("scan_kind") == "material_action" and result.get("material_action"):
            session["scan_material_action"] = result["material_action"]
        elif result.get("scan_kind") == "order":
            session.pop("scan_worker_id", None)
            session.pop("scan_material_action", None)
    current_worker = get_inspection_worker(session["scan_worker_id"]) if session.get("scan_worker_id") else None
    pending_action = session.get("scan_material_action")
    pending_action_label = {"SHORTAGE": "缺料", "REPLENISH": "補料"}.get(pending_action)
    return render_template("inspection_scan.html", result=result, current_worker=current_worker, pending_action_label=pending_action_label)


@bp.post("/api/scan")
def api_scan():
    result = scan(
        request.json.get("payload", "") if request.is_json else request.form.get("payload", ""),
        session.get("scan_worker_id"),
        pending_material_action=session.get("scan_material_action"),
    )
    if result.get("scan_kind") == "employee" and result.get("worker_id"):
        session["scan_worker_id"] = result["worker_id"]
        session.pop("scan_material_action", None)
    elif result.get("scan_kind") == "material_action" and result.get("material_action"):
        session["scan_material_action"] = result["material_action"]
    elif result.get("scan_kind") == "order":
        session.pop("scan_worker_id", None)
        session.pop("scan_material_action", None)
    return jsonify(result), 200 if result["result"] in {"success", "duplicate"} else 400


@bp.route("/reports", methods=["GET", "POST"])
def reports():
    filters = request.form.to_dict() if request.method == "POST" else request.args.to_dict()
    try:
        current_page = max(1, int(filters.pop("page", "1") or 1))
    except ValueError:
        current_page = 1
    all_rows = query_report(filters)
    total_rows = len(all_rows)
    total_pages = max(1, (total_rows + REPORT_PAGE_SIZE - 1) // REPORT_PAGE_SIZE)
    current_page = min(current_page, total_pages)
    page_start = (current_page - 1) * REPORT_PAGE_SIZE
    rows = all_rows[page_start : page_start + REPORT_PAGE_SIZE]
    pagination_links = []
    for page_number in range(1, total_pages + 1):
        page_filters = dict(filters)
        page_filters["page"] = page_number
        pagination_links.append({
            "number": page_number,
            "url": url_for("main.reports", **page_filters),
        })
    axis_summary_rows = query_report({})
    token = session.get("order_delete_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["order_delete_token"] = token
    return render_template(
        "inspection_reports.html",
        rows=rows,
        total_rows=total_rows,
        current_page=current_page,
        total_pages=total_pages,
        pagination_links=pagination_links,
        summaries=summarize_report_by_worker(all_rows),
        axis_summaries=summarize_report_by_axis(axis_summary_rows),
        filters=filters,
        workers=list_report_workers(),
        delete_token=token,
        delete_next=request.full_path.rstrip("?"),
    )


@bp.get("/manager")
def manager():
    """Read-only report entry for the business manager viewer."""
    filters = request.args.to_dict()
    detail_id = (filters.pop("detail_id", "") or "").strip()
    detail = None
    material_pauses = []
    if detail_id.isdigit():
        detail = get_order(int(detail_id))
        if detail:
            material_pauses = list_material_pauses(detail["id"])

    rows = query_report(filters)
    return render_template(
        "manager.html",
        rows=rows,
        filters=filters,
        workers=list_report_workers(),
        detail=detail,
        material_pauses=material_pauses,
    )


@bp.post("/reports/export")
def reports_export():
    filters = request.form.to_dict()
    rows = query_report(filters)
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    export_xlsx(rows, tmp.name)
    filename = datetime.now().strftime("%Y%m%d") + "Report.xlsx"
    return send_file(tmp.name, as_attachment=True, download_name=filename)


@bp.get("/settings")
def settings():
    return render_template("settings.html", password_enabled=access_password_enabled())


@bp.post("/settings/password")
def change_password():
    try:
        if request.form.get("new_password") != request.form.get("confirm_password"):
            raise ValueError("兩次輸入的新密碼不一致")
        change_access_password(request.form.get("current_password"), request.form.get("new_password"))
        flash("密碼已更新", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.settings"))


@bp.post("/settings/password-mode")
def password_mode():
    enabled = request.form.get("password_enabled") == "1"
    set_access_password_enabled(enabled)
    if not enabled:
        session.pop("admin_unlocked", None)
    flash("功能密碼設定已更新", "success")
    return redirect(url_for("main.settings"))


@bp.route("/debug/clear", methods=["GET", "POST"])
def debug_clear():
    if request.method == "POST":
        try:
            backup, removed = clear_debug_data(request.form.getlist("items"), request.form.get("confirmation", ""), current_app.config["BACKUP_DIRECTORY"])
            flash(f"清除完成；已先備份至 {backup.name}。清除筆數：{sum(removed.values())}", "success")
        except (ValueError, OSError, sqlite3.Error) as exc:
            flash(f"沒有清除資料：{exc}", "error")
        return redirect(url_for("main.debug_clear"))
    return render_template("inspection_debug_clear.html", counts=debug_clear_counts(), choices=DEBUG_CLEAR_CHOICES)


@bp.post("/backup")
def backup():
    try:
        target = backup_database(current_app.config["BACKUP_DIRECTORY"])
        flash(f"備份完成：{target.name}", "success")
    except Exception as exc:
        flash(f"備份失敗：{exc}", "error")
    return redirect(url_for("main.index"))


@bp.post("/restore")
def restore():
    try:
        restore_database(request.form.get("source_path", "").strip(), current_app.config["DATABASE"], current_app.config["BACKUP_DIRECTORY"])
        flash("還原完成，請重新啟動應用程式", "success")
    except Exception as exc:
        flash(f"還原失敗：{exc}", "error")
    return redirect(url_for("main.settings"))

