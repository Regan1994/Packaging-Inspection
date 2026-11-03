import hashlib
import json
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, utc_now


ALLOWED_SEMANTICS = {"unknown", "asset", "serial"}
CODE_RE = re.compile(r"^[^\s|]+$")
STATUS_LABELS = {
    "created": "未完成",
    "started": "未完成",
    "finished": "已完成",
    "cancelled": "已取消",
}
MODEL_SERIAL_PREFIXES = {"HR255": "E", "RC255": "ER", "AR210": "C", "AR170": "B", "RC210": "CR"}
ASSEMBLY_STAGES = ("旋", "傾", "尾", "組")
ASSEMBLY_STAGE_CODES = {"旋": "R", "傾": "T", "尾": "B", "組": "A"}
ASSEMBLY_STAGE_BY_CODE = {code: stage for stage, code in ASSEMBLY_STAGE_CODES.items()}
ASSEMBLY_STAGE_LABELS = {"旋": "旋轉軸", "傾": "傾斜軸", "尾": "尾座", "組": "組合"}
DEBUG_CLEAR_CHOICES = {
    "production": "報工批次與條碼",
    "scans": "掃描紀錄",
    "workers": "組裝人員",
    "models": "機型與舊機台",
}
TAIPEI_TZ = timezone(timedelta(hours=8), name="Asia/Taipei")


def infer_serial_prefix(model_name, model_code):
    for value in (model_code, model_name):
        key = "".join(char for char in (value or "").upper() if char.isalnum())
        if key in MODEL_SERIAL_PREFIXES:
            return MODEL_SERIAL_PREFIXES[key]
    return ""


def is_five_axis_model(model):
    return (model["model_name"] or "").strip().upper().startswith("F")


def validate_assembly_stage(model, assembly_stage):
    assembly_stage = (assembly_stage or "").strip()
    if is_five_axis_model(model):
        if assembly_stage not in ASSEMBLY_STAGES:
            raise ValueError("五軸機型必須選擇組裝階段：旋轉軸、傾斜軸、尾座或組合")
        return assembly_stage
    if assembly_stage:
        raise ValueError("標準四軸機型不可選擇組裝階段")
    return None


def new_id():
    return str(uuid.uuid4())


def status_label(status):
    return STATUS_LABELS.get(status, status or "-")


def assembly_stage_label(stage):
    """Return a clear report label for a five-axis assembly stage."""
    return ASSEMBLY_STAGE_LABELS.get((stage or "").strip(), "—")


def format_duration(started_at, finished_at, status=None):
    if not started_at:
        return "-"
    if not finished_at:
        return "進行中" if status == "started" else "-"
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
    except (TypeError, ValueError):
        return "-"
    seconds = max(0, int((finished - started).total_seconds()))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{days}日 {duration}" if days else duration


def format_datetime(value):
    """Format stored UTC timestamps for the Taiwan-based user interface."""
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value)


def validate_text(value, field, required=True):
    value = (value or "").strip()
    if required and not value:
        raise ValueError(f"{field}不可為空")
    if "|" in value:
        raise ValueError(f"{field}不可包含 |")
    return value


def validate_code(value, field):
    value = validate_text(value, field)
    if not CODE_RE.match(value):
        raise ValueError(f"{field}格式不正確")
    return value


def add_audit(action, entity_type, entity_id=None, details=None, operator_name=None):
    db = get_db()
    db.execute(
        "INSERT INTO audit_log(log_id, action, entity_type, entity_id, details, operator_name, created_at) VALUES (?,?,?,?,?,?,?)",
        (new_id(), action, entity_type, entity_id, json.dumps(details or {}, ensure_ascii=False), operator_name, utc_now()),
    )


def get_setting(key, default=None):
    row = get_db().execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value, operator_name="system"):
    if key == "number_semantics" and value not in ALLOWED_SEMANTICS:
        raise ValueError("number_semantics 必須是 unknown、asset 或 serial")
    db = get_db()
    db.execute(
        "INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, utc_now()),
    )
    add_audit("update_setting", "app_setting", key, {"value": value}, operator_name)
    db.commit()


def verify_access_password(password):
    password_hash = get_setting("access_password_hash")
    return bool(password_hash and password and check_password_hash(password_hash, password))


def access_password_enabled():
    return get_setting("access_password_enabled", "1") == "1"


def set_access_password_enabled(enabled, operator_name="admin"):
    set_setting("access_password_enabled", "1" if enabled else "0", operator_name)


def change_access_password(current_password, new_password, operator_name="admin"):
    if not verify_access_password(current_password):
        raise ValueError("目前密碼不正確")
    if not new_password or len(new_password) < 3:
        raise ValueError("新密碼至少需要 3 個字元")
    db = get_db()
    db.execute(
        "INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        ("access_password_hash", generate_password_hash(new_password), utc_now()),
    )
    add_audit("change_password", "app_setting", "access_password_hash", {"changed": True}, operator_name)
    db.commit()


def list_models(active_only=False):
    sql = "SELECT * FROM model_master"
    if active_only:
        sql += " WHERE is_active=1"
    return get_db().execute(sql + " ORDER BY sort_order, model_name").fetchall()


def list_assembly_workers(active_only=False):
    sql = "SELECT * FROM assembly_worker"
    if active_only:
        sql += " WHERE is_active=1"
    return get_db().execute(sql + " ORDER BY name COLLATE NOCASE").fetchall()


def list_report_operators():
    rows = get_db().execute("""
        SELECT name AS operator_name FROM assembly_worker WHERE trim(name) <> ''
        UNION
        SELECT operator_name FROM job_run WHERE trim(operator_name) <> ''
        ORDER BY operator_name COLLATE NOCASE
    """).fetchall()
    return [row["operator_name"] for row in rows]


def get_assembly_worker(name):
    name = validate_text(name, "組裝人員")
    return get_db().execute(
        "SELECT * FROM assembly_worker WHERE name=? COLLATE NOCASE AND is_active=1",
        (name,),
    ).fetchone()


def create_assembly_worker(name, changed_by="admin"):
    name = validate_text(name, "組裝人員")
    db = get_db()
    now = utc_now()
    try:
        cur = db.execute(
            "INSERT INTO assembly_worker(name,created_at,updated_at) VALUES(?,?,?)",
            (name, now, now),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("此組裝人員已存在") from exc
    add_audit("create", "assembly_worker", str(cur.lastrowid), {"name": name}, changed_by)
    db.commit()
    return cur.lastrowid


def set_assembly_worker_active(worker_id, is_active, changed_by="admin"):
    db = get_db()
    worker = db.execute("SELECT name FROM assembly_worker WHERE id=?", (worker_id,)).fetchone()
    if not worker:
        raise ValueError("找不到組裝人員")
    db.execute(
        "UPDATE assembly_worker SET is_active=?,updated_at=? WHERE id=?",
        (int(bool(is_active)), utc_now(), worker_id),
    )
    add_audit("update", "assembly_worker", str(worker_id), {"name": worker["name"], "is_active": bool(is_active)}, changed_by)
    db.commit()


def create_model(model_name, model_code=None, description="", operator_name="system", serial_prefix=None):
    model_name = validate_text(model_name, "機型名稱")
    if get_db().execute("SELECT 1 FROM model_master WHERE model_name=? COLLATE NOCASE", (model_name,)).fetchone():
        raise ValueError("機型名稱已存在")
    model_code = validate_code(model_name, "機型名稱") if model_code is None else validate_code(model_code, "機型代碼")
    serial_prefix = validate_code(serial_prefix, "製號字首") if serial_prefix else infer_serial_prefix(model_name, model_code)
    db = get_db()
    now = utc_now()
    try:
        cur = db.execute(
            "INSERT INTO model_master(model_name,model_code,serial_prefix,description,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (model_name, model_code, serial_prefix, description.strip(), now, now),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("機型代碼已存在") from exc
    add_audit("create", "model_master", str(cur.lastrowid), {"model_code": model_code}, operator_name)
    db.commit()
    return cur.lastrowid


def update_model(model_id, model_name, model_code=None, description="", is_active=True, operator_name="system", serial_prefix=None):
    model_name = validate_text(model_name, "機型名稱")
    db = get_db()
    current = db.execute("SELECT model_code FROM model_master WHERE id=?", (model_id,)).fetchone()
    if not current:
        raise ValueError("找不到機型")
    if db.execute("SELECT 1 FROM model_master WHERE model_name=? COLLATE NOCASE AND id<>?", (model_name, model_id)).fetchone():
        raise ValueError("機型名稱已存在")
    model_code = validate_code(current["model_code"] if model_code is None else model_code, "機型代碼")
    serial_prefix = validate_code(serial_prefix, "製號字首") if serial_prefix else infer_serial_prefix(model_name, model_code)
    try:
        db.execute("UPDATE model_master SET model_name=?,model_code=?,serial_prefix=?,description=?,is_active=?,updated_at=? WHERE id=?", (model_name, model_code, serial_prefix, description.strip(), int(bool(is_active)), utc_now(), model_id))
    except sqlite3.IntegrityError as exc:
        raise ValueError("機型代碼已存在") from exc
    add_audit("update", "model_master", str(model_id), {"model_code": model_code, "is_active": bool(is_active)}, operator_name)
    db.commit()


def delete_model(model_id, expected_name, backup_dir, operator_name="admin"):
    db = get_db()

    def check_deletable():
        model = db.execute("SELECT model_name FROM model_master WHERE id=?", (model_id,)).fetchone()
        if not model:
            raise ValueError("找不到機型，可能已經刪除")
        if model["model_name"] != expected_name:
            raise ValueError("機型名稱已變更，請重新整理後再刪除")
        if db.execute("SELECT 1 FROM job_run WHERE model_id=? LIMIT 1", (model_id,)).fetchone():
            raise ValueError("此機型已有報工紀錄，不能刪除；請改用停用")
        if db.execute("SELECT 1 FROM machine_master WHERE model_id=? LIMIT 1", (model_id,)).fetchone():
            raise ValueError("此機型仍有舊機台資料，不能刪除；請改用停用")
        return model

    check_deletable()
    backup = backup_database(backup_dir)
    try:
        db.execute("BEGIN IMMEDIATE")
        model = check_deletable()
        db.execute("DELETE FROM model_master WHERE id=?", (model_id,))
        add_audit("delete", "model_master", str(model_id), {"model_name": model["model_name"], "backup": str(backup)}, operator_name)
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise ValueError("此機型仍有關聯資料，不能刪除；請改用停用") from exc
    except Exception:
        db.rollback()
        raise
    return backup


def list_machines(model_id=None, active_only=False):
    clauses, params = [], []
    if model_id:
        clauses.append("model_id=?")
        params.append(model_id)
    if active_only:
        clauses.append("is_active=1")
    sql = "SELECT * FROM machine_master"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return get_db().execute(sql + " ORDER BY sort_order, number_value, display_no", params).fetchall()


def machine_number_prefix(model_id):
    machines = get_db().execute(
        "SELECT display_no FROM machine_master WHERE model_id=? AND is_active=1 ORDER BY id",
        (model_id,),
    ).fetchall()
    prefixes = set()
    for machine in machines:
        prefix, separator, suffix = machine["display_no"].rpartition("-")
        if not separator or not prefix or not suffix.isdigit():
            return ""
        prefixes.add(prefix + separator)
    return next(iter(prefixes)) if len(prefixes) == 1 else ""


def machine_ids_from_numbers(model_id, machine_numbers):
    tokens = [token.strip() for token in (machine_numbers or "").split(",")]
    if not tokens or any(not token for token in tokens):
        raise ValueError("請以逗號分隔輸入機台編號")
    normalized = [token.casefold() for token in tokens]
    if len(normalized) != len(set(normalized)):
        raise ValueError("機台編號不可重複")

    available = get_db().execute(
        "SELECT id, display_no FROM machine_master WHERE model_id=? AND is_active=1",
        (model_id,),
    ).fetchall()
    machines_by_number = {row["display_no"].casefold(): row["id"] for row in available}
    prefix = machine_number_prefix(model_id)
    machine_numbers = [prefix + token if prefix and token.isdigit() else token for token in tokens]
    invalid = [number for number, token in zip(machine_numbers, tokens) if number.casefold() not in machines_by_number or (prefix and not token.isdigit())]
    if invalid:
        raise ValueError("機台不存在、未啟用或不屬於所選機型：" + ", ".join(invalid))
    return [machines_by_number[number.casefold()] for number in machine_numbers]


def _machine_semantics(value):
    value = value or get_setting("number_semantics", "unknown")
    if value not in ALLOWED_SEMANTICS:
        raise ValueError("編號語意必須是 unknown、asset 或 serial")
    return value


def create_machine(model_id, display_no, number_prefix="", number_value=None, number_width=None, semantics=None, operator_name="system"):
    display_no = validate_code(display_no, "機台編號")
    semantics = _machine_semantics(semantics)
    db = get_db()
    now = utc_now()
    try:
        cur = db.execute(
            "INSERT INTO machine_master(model_id,display_no,number_prefix,number_value,number_width,number_semantics,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (model_id, display_no, number_prefix.strip(), number_value, number_width, semantics, now, now),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("同一機型的機台編號不可重複") from exc
    add_audit("create", "machine_master", str(cur.lastrowid), {"display_no": display_no, "number_semantics": semantics}, operator_name)
    db.commit()
    return cur.lastrowid


def create_machine_range(model_id, prefix, start, end, width, semantics="unknown", operator_name="system"):
    prefix = validate_code(prefix, "機台前綴")
    semantics = _machine_semantics(semantics)
    if start < 0 or end < start or width < 1:
        raise ValueError("機台範圍或固定寬度不正確")
    db = get_db()
    now = utc_now()
    ids = []
    try:
        for value in range(start, end + 1):
            display_no = f"{prefix}-{value:0{width}d}"
            cur = db.execute("INSERT INTO machine_master(model_id,display_no,number_prefix,number_value,number_width,number_semantics,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (model_id, display_no, prefix, value, width, semantics, now, now))
            ids.append(cur.lastrowid)
            add_audit("create", "machine_master", str(cur.lastrowid), {"display_no": display_no, "number_semantics": semantics}, operator_name)
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise ValueError("同一機型的機台編號不可重複") from exc
    return ids


def update_machine(machine_id, display_no, semantics, is_active, operator_name="system"):
    display_no = validate_code(display_no, "機台編號")
    semantics = _machine_semantics(semantics)
    db = get_db()
    try:
        db.execute("UPDATE machine_master SET display_no=?,number_semantics=?,is_active=?,updated_at=? WHERE id=?", (display_no, semantics, int(bool(is_active)), utc_now(), machine_id))
    except sqlite3.IntegrityError as exc:
        raise ValueError("同一機型的機台編號不可重複") from exc
    add_audit("update", "machine_master", str(machine_id), {"display_no": display_no, "number_semantics": semantics, "is_active": bool(is_active)}, operator_name)
    db.commit()


def get_batch(batch_id):
    return get_db().execute("SELECT * FROM production_batch WHERE batch_id=?", (batch_id,)).fetchone()


def list_recent_batches(limit=20):
    """Return recent batches with details needed for reprinting."""
    limit = max(1, min(int(limit), 100))
    db = get_db()
    batches = db.execute("SELECT * FROM production_batch ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    recent = []
    for batch in batches:
        jobs = db.execute(
            "SELECT product_serial,machine_no_snapshot,model_name_snapshot FROM job_run WHERE batch_id=? ORDER BY id",
            (batch["batch_id"],),
        ).fetchall()
        serials = [job["product_serial"] or job["machine_no_snapshot"] for job in jobs if job["product_serial"] or job["machine_no_snapshot"]]
        model_names = [job["model_name_snapshot"] for job in jobs if job["model_name_snapshot"]]
        if len(serials) > 1:
            serial_range = f"{serials[0]}~{serials[-1]}"
        else:
            serial_range = serials[0] if serials else "-"
        recent.append(
            {
                "batch_id": batch["batch_id"],
                "created_at": batch["created_at"],
                "model_name": model_names[0] if model_names else "-",
                "assembly_stage": batch["assembly_stage"],
                "serial_range": serial_range,
                "product_count": len(jobs),
                "operator_name": batch["operator_name"],
            }
        )
    return recent


def delete_batch(batch_id, expected_batch_id, confirmation, backup_dir, operator_name="admin"):
    batch_id = validate_text(batch_id, "批次識別碼")
    if batch_id != validate_text(expected_batch_id, "刪除確認"):
        raise ValueError("刪除確認與批次不一致")
    if confirmation != "刪除":
        raise ValueError("請輸入「刪除」確認")

    db = get_db()
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError("找不到報工批次")
    job_ids = [row["job_run_id"] for row in db.execute("SELECT job_run_id FROM job_run WHERE batch_id=?", (batch_id,)).fetchall()]
    barcode_count = db.execute(
        "SELECT COUNT(*) FROM barcode_record WHERE batch_id=? OR job_run_id IN (SELECT job_run_id FROM job_run WHERE batch_id=?)",
        (batch_id, batch_id),
    ).fetchone()[0]
    scan_count = db.execute(
        "SELECT COUNT(*) FROM scan_event WHERE payload IN (SELECT payload FROM barcode_record WHERE batch_id=? OR job_run_id IN (SELECT job_run_id FROM job_run WHERE batch_id=?)) OR job_run_id IN (SELECT job_run_id FROM job_run WHERE batch_id=?)",
        (batch_id, batch_id, batch_id),
    ).fetchone()[0]

    # Create a verified snapshot before any DELETE statement.
    backup = backup_database(backup_dir)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "DELETE FROM scan_event WHERE payload IN (SELECT payload FROM barcode_record WHERE batch_id=? OR job_run_id IN (SELECT job_run_id FROM job_run WHERE batch_id=?)) OR job_run_id IN (SELECT job_run_id FROM job_run WHERE batch_id=?)",
            (batch_id, batch_id, batch_id),
        )
        db.execute("DELETE FROM barcode_record WHERE batch_id=? OR job_run_id IN (SELECT job_run_id FROM job_run WHERE batch_id=?)", (batch_id, batch_id))
        db.execute("DELETE FROM job_run WHERE batch_id=?", (batch_id,))
        db.execute("DELETE FROM production_batch WHERE batch_id=?", (batch_id,))
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("資料關聯檢查失敗，刪除已取消")
        add_audit(
            "delete",
            "production_batch",
            batch_id,
            {"job_count": len(job_ids), "barcode_count": barcode_count, "scan_count": scan_count, "backup": str(backup)},
            operator_name,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return backup, {"job_run": len(job_ids), "barcode_record": barcode_count, "scan_event": scan_count, "production_batch": 1}


def get_job(job_run_id):
    return get_db().execute("SELECT * FROM job_run WHERE job_run_id=?", (job_run_id,)).fetchone()


def create_batch(model_id, manufacturing_year, starting_number, product_count, operator_name, assembly_stage=None):
    try:
        manufacturing_year = int(manufacturing_year)
        starting_number = int(starting_number)
        product_count = int(product_count)
    except (TypeError, ValueError) as exc:
        raise ValueError("製作年份、起始流水號與台數都必須是正整數") from exc
    if not 2000 <= manufacturing_year <= 2099 or starting_number < 1 or product_count < 1:
        raise ValueError("製作年份需為 2000–2099，起始流水號與台數必須是正整數")
    operator_name = validate_text(operator_name, "組裝人員")
    db = get_db()
    model = db.execute("SELECT * FROM model_master WHERE id=? AND is_active=1", (model_id,)).fetchone()
    if not model:
        raise ValueError("機型不存在或已停用")
    assembly_stage = validate_assembly_stage(model, assembly_stage)
    serial_prefix = model["serial_prefix"] or infer_serial_prefix(model["model_name"], model["model_code"])
    if not serial_prefix:
        raise ValueError("此機型尚未設定製號字首，請至機型主檔設定")
    year_marker = f"{(manufacturing_year + 1) % 100:02d}"
    serials = [f"{serial_prefix}{year_marker}-{number:02d}" for number in range(starting_number, starting_number + product_count)]
    placeholders = ",".join("?" for _ in serials)
    if assembly_stage:
        used = db.execute(
            f"SELECT product_serial FROM job_run WHERE product_serial IN ({placeholders}) AND assembly_stage=?",
            [*serials, assembly_stage],
        ).fetchall()
    else:
        used = db.execute(
            f"SELECT product_serial FROM job_run WHERE product_serial IN ({placeholders}) AND assembly_stage IS NULL",
            serials,
        ).fetchall()
    legacy_used = db.execute(f"SELECT machine_no_snapshot FROM job_run WHERE machine_no_snapshot IN ({placeholders})", serials).fetchall()
    if used or legacy_used:
        duplicates = {row[0] for row in [*used, *legacy_used]}
        raise ValueError("以下製號已存在，請更換起始流水號：" + ", ".join(sorted(duplicates)))
    now = utc_now()
    batch_id = new_id()
    batch_no = "B-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + batch_id[:6].upper()
    db.execute("INSERT INTO production_batch(batch_id,batch_no,selection_mode,assembly_stage,operator_name,created_at) VALUES(?,?,?,?,?,?)", (batch_id, batch_no, "serial_range", assembly_stage, operator_name, now))
    job_ids = []
    for serial in serials:
        job_id = new_id()
        job_ids.append(job_id)
        db.execute("INSERT INTO job_run(job_run_id,batch_id,model_id,machine_id,product_serial,manufacturing_year,assembly_stage,model_name_snapshot,model_code_snapshot,machine_no_snapshot,number_semantics_snapshot,planned_qty,status,operator_name,created_at,updated_at) VALUES(?,?,?,NULL,?,?,?,?,?,NULL,'unknown',NULL,'created',?,?,?)", (job_id, batch_id, model_id, serial, manufacturing_year, assembly_stage, model["model_name"], model["model_code"], operator_name, now, now))
    add_audit("create", "production_batch", batch_id, {"job_count": len(job_ids), "manufacturing_year": manufacturing_year, "assembly_stage": assembly_stage, "serials": serials}, operator_name)
    db.commit()
    return batch_id, job_ids


def checksum_for(parts):
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:8].upper()


def compact_batch_token(batch_id):
    return batch_id.replace("-", "").upper()[:8]


def make_payload(kind, job):
    parts = ["INTJR", "V1", kind, job["batch_id"], job["job_run_id"], job["model_code_snapshot"], job["machine_no_snapshot"]]
    return "|".join(parts + [checksum_for(parts)])


def make_batch_payload(kind, batch, jobs):
    if batch["assembly_stage"]:
        prefix = "I2" + ("S" if kind == "START" else "F")
        token = compact_batch_token(batch["batch_id"])
        stage_code = ASSEMBLY_STAGE_CODES[batch["assembly_stage"]]
        return prefix + token + stage_code + checksum_for([prefix, token, stage_code])[:4]
    prefix = "I1" + ("S" if kind == "START" else "F")
    token = compact_batch_token(batch["batch_id"])
    return prefix + token + checksum_for([prefix, token])[:4]


def parse_payload(payload):
    raw = (payload or "").strip()
    if len(raw) == 16 and raw[:2] == "I2" and raw[2] in {"S", "F"}:
        prefix, token, stage_code, check = raw[:3], raw[3:11], raw[11], raw[12:]
        if stage_code not in ASSEMBLY_STAGE_BY_CODE or checksum_for([prefix, token, stage_code])[:4] != check:
            raise ValueError("條碼 checksum 或組裝階段不正確")
        return {"system": "INTJR", "version": "2", "type": "START" if raw[2] == "S" else "FINISH", "batch_token": token, "assembly_stage": ASSEMBLY_STAGE_BY_CODE[stage_code], "checksum": check, "scope": "batch"}
    if len(raw) == 15 and raw[:2] == "I1" and raw[2] in {"S", "F"}:
        prefix, token, check = raw[:3], raw[3:11], raw[11:]
        if checksum_for([prefix, token])[:4] != check:
            raise ValueError("條碼 checksum 不正確")
        return {"system": "INTJR", "version": "1", "type": "START" if raw[2] == "S" else "FINISH", "batch_token": token, "checksum": check, "scope": "batch"}
    parts = raw.split("|")
    if len(parts) == 5 and parts[0] == "INTJR" and parts[1] in {"1", "V1"}:
        if parts[2] not in {"S", "F", "START", "FINISH"} or checksum_for(parts[:-1]) != parts[-1]:
            raise ValueError("條碼 checksum 或類型不正確")
        return {"system": parts[0], "version": parts[1], "type": {"S": "START", "F": "FINISH"}.get(parts[2], parts[2]), "batch_token": parts[3], "checksum": parts[4], "scope": "batch"}
    if len(parts) != 8 or parts[0] != "INTJR":
        raise ValueError("無效條碼格式")
    if parts[1] != "V1":
        raise ValueError("不支援的條碼版本")
    if parts[2] not in {"START", "FINISH"}:
        raise ValueError("不支援的條碼類型")
    if checksum_for(parts[:-1]) != parts[-1]:
        raise ValueError("條碼 checksum 不正確")
    return {"system": parts[0], "version": parts[1], "type": parts[2], "batch_id": parts[3], "job_run_id": parts[4], "model_code": parts[5], "machine_no": parts[6], "checksum": parts[7], "scope": "job_run"}


def ensure_barcodes(batch_id):
    db = get_db()
    jobs = db.execute("SELECT * FROM job_run WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
    now = utc_now()
    batch = get_batch(batch_id)
    if batch["selection_mode"] == "serial_range" or get_setting("batch_scan_mode", "batch") == "batch":
        for kind in ("START", "FINISH"):
            payload = make_batch_payload(kind, batch, jobs)
            db.execute("INSERT OR IGNORE INTO barcode_record(batch_id,barcode_scope,barcode_type,payload,created_at) VALUES(?,?,?,?,?)", (batch_id, "batch", kind, payload, now))
    else:
        for job in jobs:
            for kind in ("START", "FINISH"):
                payload = make_payload(kind, job)
                db.execute("INSERT OR IGNORE INTO barcode_record(job_run_id,barcode_scope,barcode_type,payload,created_at) VALUES(?,?,?,?,?)", (job["job_run_id"], "job_run", kind, payload, now))
    db.commit()
    return jobs


def scan(payload, operator_name="scanner"):
    db = get_db()
    event_id = new_id()
    scanned_at = utc_now()
    try:
        parsed = parse_payload(payload)
        kind = parsed["type"]
        if parsed.get("scope") == "batch":
            candidates = db.execute("SELECT * FROM production_batch WHERE upper(replace(batch_id, '-', '')) LIKE ?", (parsed["batch_token"] + "%",)).fetchall()
            if len(candidates) != 1:
                raise ValueError("找不到相符的報工批次")
            batch_id = candidates[0]["batch_id"]
            if parsed.get("assembly_stage") and candidates[0]["assembly_stage"] != parsed["assembly_stage"]:
                raise ValueError("條碼組裝階段與報工批次不一致")
            jobs = db.execute("SELECT * FROM job_run WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
            if not jobs:
                raise ValueError("找不到相符的報工批次")
            if kind == "START":
                pending = [job for job in jobs if job["status"] == "created"]
                if pending:
                    db.executemany("UPDATE job_run SET status='started',started_at=?,updated_at=? WHERE job_run_id=?", [(scanned_at, scanned_at, job["job_run_id"]) for job in pending])
                    result, message = "success", f"整批 START 成功：{len(pending)} 台產品"
                elif all(job["status"] == "started" for job in jobs):
                    result, message = "duplicate", "此批次已完成 START"
                else:
                    result, message = "invalid", "此批次已完成或已取消"
            else:
                started = [job for job in jobs if job["status"] == "started"]
                created = [job for job in jobs if job["status"] == "created"]
                if created:
                    result, message = "invalid", "批次內仍有產品未 START，不能 FINISH"
                elif started:
                    db.executemany("UPDATE job_run SET status='finished',actual_qty=CASE WHEN product_serial IS NULL THEN planned_qty ELSE actual_qty END,finished_at=?,updated_at=? WHERE job_run_id=?", [(scanned_at, scanned_at, job["job_run_id"]) for job in started])
                    result, message = "success", f"整批 FINISH 成功：{len(started)} 台產品"
                elif all(job["status"] == "finished" for job in jobs):
                    result, message = "duplicate", "此批次已完成 FINISH"
                else:
                    result, message = "invalid", "此批次已取消或沒有可完成的產品"
            db.execute("INSERT INTO scan_event(event_id,payload,barcode_type,job_run_id,result,message,scanned_at,operator_name) VALUES(?,?,?,?,?,?,?,?)", (event_id, payload, kind, None, result, message, scanned_at, operator_name))
            db.commit()
            return {"event_id": event_id, "result": result, "message": message, "scanned_at": scanned_at}

        job = get_job(parsed["job_run_id"])
        if not job or job["batch_id"] != parsed["batch_id"] or job["model_code_snapshot"] != parsed["model_code"] or job["machine_no_snapshot"] != parsed["machine_no"]:
            raise ValueError("找不到相符的報工資料")
        if kind == "START":
            if job["status"] == "created":
                db.execute("UPDATE job_run SET status='started',started_at=?,updated_at=? WHERE job_run_id=?", (scanned_at, scanned_at, job["job_run_id"]))
                result, message = "success", f"START 成功：{job['machine_no_snapshot']}"
            elif job["status"] == "started":
                result, message = "duplicate", f"已於 {format_datetime(job['started_at'])} START"
            else:
                result, message = "invalid", "此報工已完成或已取消"
        else:
            if job["status"] == "started":
                db.execute("UPDATE job_run SET status='finished',actual_qty=CASE WHEN product_serial IS NULL THEN planned_qty ELSE actual_qty END,finished_at=?,updated_at=? WHERE job_run_id=?", (scanned_at, scanned_at, job["job_run_id"]))
                result, message = "success", f"FINISH 成功：{job['machine_no_snapshot']}"
            elif job["status"] == "created":
                result, message = "invalid", "必須先掃描 START"
            elif job["status"] == "finished":
                result, message = "duplicate", f"已於 {format_datetime(job['finished_at'])} 完成"
            else:
                result, message = "invalid", "此報工已取消"
        db.execute("INSERT INTO scan_event(event_id,payload,barcode_type,job_run_id,result,message,scanned_at,operator_name) VALUES(?,?,?,?,?,?,?,?)", (event_id, payload, kind, job["job_run_id"], result, message, scanned_at, operator_name))
    except ValueError as exc:
        result, message = "invalid", str(exc)
        db.execute("INSERT INTO scan_event(event_id,payload,result,message,scanned_at,operator_name) VALUES(?,?,?,?,?,?)", (event_id, payload, result, message, scanned_at, operator_name))
    db.commit()
    return {"event_id": event_id, "result": result, "message": message, "scanned_at": scanned_at}


def correct_actual_qty(job_run_id, actual_qty, operator_name):
    try:
        actual_qty = int(actual_qty)
    except (TypeError, ValueError) as exc:
        raise ValueError("完成量必須是非負整數") from exc
    if actual_qty < 0:
        raise ValueError("完成量必須是非負整數")
    db = get_db()
    job = get_job(job_run_id)
    if not job:
        raise ValueError("找不到報工")
    if job["status"] != "finished":
        raise ValueError("只有已完成報工可更正完成量")
    old_qty = job["actual_qty"]
    db.execute("UPDATE job_run SET actual_qty=?,updated_at=? WHERE job_run_id=?", (actual_qty, utc_now(), job_run_id))
    add_audit("correct_actual_qty", "job_run", job_run_id, {"old_actual_qty": old_qty, "new_actual_qty": actual_qty}, operator_name)
    db.commit()


def query_report(filters):
    db = get_db()
    clauses, params = [], []
    status_group = (filters.get("status_group") or "").strip()
    if status_group == "incomplete":
        clauses.append("j.status IN ('created','started')")
    elif status_group == "finished":
        clauses.append("j.status='finished'")
    elif status_group == "cancelled":
        clauses.append("j.status='cancelled'")
    mapping = {"model_name": "j.model_name_snapshot", "model_code": "j.model_code_snapshot", "machine_no": "COALESCE(j.product_serial,j.machine_no_snapshot)", "status": "j.status"}
    for key, column in mapping.items():
        value = (filters.get(key) or "").strip()
        if value:
            clauses.append(f"{column} LIKE ?")
            params.append(f"%{value}%")
    operator_name = (filters.get("operator_name") or "").strip()
    if operator_name:
        clauses.append("j.operator_name = ?")
        params.append(operator_name)
    for key, op in (("date_from", ">="), ("date_to", "<=")):
        value = (filters.get(key) or "").strip()
        if value:
            clauses.append(f"substr(j.created_at,1,10) {op} ?")
            params.append(value)
    sql = "SELECT j.* FROM job_run j"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return db.execute(sql + " ORDER BY j.created_at DESC, j.id DESC", params).fetchall()


def summarize_report_by_operator(rows):
    """Summarize the currently filtered report rows by assembly worker."""
    summaries = {}
    for row in rows:
        operator_name = (row["operator_name"] or "").strip() or "未指定"
        summary = summaries.setdefault(
            operator_name,
            {
                "operator_name": operator_name,
                "assembly_quantity": 0,
                "finished": 0,
                "started": 0,
                "created": 0,
                "cancelled": 0,
            },
        )
        status = row["status"]
        if status in summary:
            summary[status] += 1
        if status != "cancelled":
            summary["assembly_quantity"] += 1

    result = []
    for summary in summaries.values():
        quantity = summary["assembly_quantity"]
        summary["completion_rate"] = round(summary["finished"] * 100 / quantity, 1) if quantity else 0.0
        result.append(summary)
    return sorted(result, key=lambda item: (-item["assembly_quantity"], item["operator_name"].casefold()))


def export_xlsx(rows, path):
    from copy import copy
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    headers = ["機型", "製號", "組裝階段", "狀態", "開始時間", "結束時間", "時長", "組裝人員"]
    wb = Workbook()
    ws = wb.active
    ws.title = "明細"
    ws.append(headers)
    def excel_value(value):
        if not isinstance(value, str):
            return value
        return "'" + value if value[:1] in {"=", "+", "-", "@"} else value

    for row in rows:
        values = [row["model_name_snapshot"], row["product_serial"] or row["machine_no_snapshot"], assembly_stage_label(row["assembly_stage"]), status_label(row["status"]), format_datetime(row["started_at"]), format_datetime(row["finished_at"]), format_duration(row["started_at"], row["finished_at"], row["status"]), row["operator_name"]]
        ws.append([excel_value(v) for v in values])
    for cell in ws[1]:
        header_font = copy(cell.font)
        header_font.bold = True
        cell.font = header_font
    for idx, header in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(idx)].width = min(max(len(header) + 2, 12), 24)

    stats = wb.create_sheet("員工統計")
    stats_headers = ["組裝人員", "組裝數量", "已完成", "進行中", "未開始", "已取消", "完成率"]
    stats.append(stats_headers)
    for summary in summarize_report_by_operator(rows):
        stats.append(
            [
                excel_value(summary["operator_name"]),
                summary["assembly_quantity"],
                summary["finished"],
                summary["started"],
                summary["created"],
                summary["cancelled"],
                summary["completion_rate"] / 100,
            ]
        )
    for cell in stats[1]:
        header_font = copy(cell.font)
        header_font.bold = True
        cell.font = header_font
    for cell in stats["G"][1:]:
        cell.number_format = "0.0%"
    for idx, header in enumerate(stats_headers, 1):
        stats.column_dimensions[get_column_letter(idx)].width = min(max(len(header) + 4, 12), 24)

    wb.save(path)


def backup_database(target_dir):
    db = get_db()
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    target = target_dir / f"intjr_backup_{timestamp}.db"
    check = sqlite3.connect(target)
    try:
        db.backup(check)
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if integrity != "ok" or target.stat().st_size <= 0:
        raise ValueError("備份驗證失敗")
    add_audit("backup", "database", str(target), {"integrity": integrity})
    db.commit()
    return target


def debug_clear_counts():
    db = get_db()
    tables = ("production_batch", "job_run", "barcode_record", "scan_event", "assembly_worker", "model_master", "machine_master")
    return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}


def clear_debug_data(selected, confirmation, backup_dir, operator_name="admin"):
    selected = set(selected)
    if not selected or selected - DEBUG_CLEAR_CHOICES.keys():
        raise ValueError("請選擇有效的清除項目")
    if confirmation != "清除":
        raise ValueError("請輸入「清除」確認")

    db = get_db()
    counts = debug_clear_counts()
    if "production" not in selected and counts["job_run"] and selected & {"workers", "models"}:
        raise ValueError("仍有報工資料；若要清除主檔，請同時選擇報工批次與條碼")

    # A verified SQLite snapshot is created before any DELETE statement.
    backup = backup_database(backup_dir)
    removed = {}
    try:
        db.execute("BEGIN IMMEDIATE")
        if selected & {"production", "scans"}:
            db.execute("DELETE FROM scan_event")
            removed["scan_event"] = counts["scan_event"]
        if "production" in selected:
            for table in ("barcode_record", "job_run", "production_batch"):
                db.execute(f"DELETE FROM {table}")
                removed[table] = counts[table]
        if "workers" in selected:
            db.execute("DELETE FROM assembly_worker")
            removed["assembly_worker"] = counts["assembly_worker"]
        if "models" in selected:
            for table in ("machine_master", "model_master"):
                db.execute(f"DELETE FROM {table}")
                removed[table] = counts[table]
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("資料關聯檢查失敗，清除已取消")
        add_audit("debug_clear", "database", details={"selected": sorted(selected), "removed": removed, "backup": str(backup)}, operator_name=operator_name)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return backup, removed


def verify_sqlite_backup(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("備份檔不存在或為空檔")
    check = sqlite3.connect(path)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if integrity != "ok":
        raise ValueError("備份檔 integrity_check 失敗")
    return path


def restore_database(source_path, target_path, backup_dir, operator_name="admin"):
    source_path = verify_sqlite_backup(source_path)
    safety_backup = backup_database(backup_dir)
    target_path = Path(target_path)
    temp_target = target_path.with_suffix(target_path.suffix + ".restore.tmp")
    shutil.copy2(source_path, temp_target)
    verify_sqlite_backup(temp_target)
    shutil.copy2(temp_target, target_path)
    temp_target.unlink(missing_ok=True)
    add_audit("restore", "database", str(target_path), {"source": str(source_path), "safety_backup": str(safety_backup)}, operator_name)
    get_db().commit()
    return safety_backup


# ---------------------------------------------------------------------------
# Final-inspection workflow.  The legacy assembly helpers above are retained
# only for source compatibility; all application routes below use these
# inspection-prefixed tables and never touch the assembly database.

INSPECTION_STATUS_LABELS = {
    "pending": "待校機",
    "started": "校機中",
    "completed": "已完成",
    "cancelled": "已取消",
}
INSPECTION_AXIS_LABELS = {"4": "四軸", "5": "五軸"}
MATERIAL_ACTION_LABELS = {
    "SHORTAGE": "缺料",
    "REPLENISH": "補料",
}
PAUSE_ACTION_LABEL = "暫停"
SCANNER_AIM_PREFIXES = ("]C0", "]C1", "]C2")
DEBUG_CLEAR_CHOICES = {
    "orders": "訂單與訂單條碼",
    "scans": "掃描紀錄",
    "workers": "校機人員",
    "models": "機型",
}


def status_label(status):
    return INSPECTION_STATUS_LABELS.get(status, status or "-")


def axis_type_label(axis_type):
    return INSPECTION_AXIS_LABELS.get(str(axis_type), axis_type or "-")


def format_seconds(total_seconds):
    try:
        seconds = max(0, int(round(float(total_seconds or 0))))
    except (TypeError, ValueError):
        return "-"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    prefix = f"{days}日 " if days else ""
    return f"{prefix}{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_duration(started_at, finished_at, status=None, paused_seconds=0):
    if not started_at:
        return "-"
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at) if finished_at else datetime.now(timezone.utc)
        seconds = max(0, int((end - start).total_seconds()) - int(round(float(paused_seconds or 0))))
    except (TypeError, ValueError, OverflowError):
        return "-"
    return format_seconds(seconds)


def format_work_duration(started_at, finished_at, pause_intervals=None):
    """Format work time in eligible windows, excluding only overlapping pause time."""
    if not started_at:
        return "-"
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at) if finished_at else datetime.now(timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=TAIPEI_TZ)
        if end.tzinfo is None:
            end = end.replace(tzinfo=TAIPEI_TZ)
        start = start.astimezone(TAIPEI_TZ)
        end = end.astimezone(TAIPEI_TZ)
        if end <= start:
            return format_seconds(0)

        def has_scan_on_day(day, after):
            return any(moment.date() == day and moment.time() >= after for moment in (start, end))

        work_intervals = []
        current_date = start.date()
        end_date = end.date()
        while current_date <= end_date:
            is_weekend = current_date.weekday() >= 5
            is_workday = not is_weekend or has_scan_on_day(current_date, time(8, 0))
            if is_workday:
                windows = [(time(8, 0), time(12, 0)), (time(13, 0), time(17, 0))]
                if has_scan_on_day(current_date, time(17, 30)):
                    windows.append((time(17, 30), time(20, 30)))
                for window_start, window_end in windows:
                    work_start = datetime.combine(current_date, window_start, tzinfo=TAIPEI_TZ)
                    work_end = datetime.combine(current_date, window_end, tzinfo=TAIPEI_TZ)
                    overlap_start = max(start, work_start)
                    overlap_end = min(end, work_end)
                    if overlap_end > overlap_start:
                        work_intervals.append((overlap_start, overlap_end))
            current_date += timedelta(days=1)

        total_seconds = sum(int((work_end - work_start).total_seconds()) for work_start, work_end in work_intervals)

        # Accepting a numeric value keeps older callers compatible, but new report
        # callers pass the actual pause ranges so breaks outside work windows are
        # never deducted from calibration time.
        if isinstance(pause_intervals, (int, float)):
            return format_seconds(max(0, total_seconds - int(round(pause_intervals))))

        pause_overlaps = []
        for pause in pause_intervals or []:
            try:
                pause_start = datetime.fromisoformat(pause["paused_at"])
                pause_end = datetime.fromisoformat(pause["resumed_at"]) if pause["resumed_at"] else end
                if pause_start.tzinfo is None:
                    pause_start = pause_start.replace(tzinfo=TAIPEI_TZ)
                if pause_end.tzinfo is None:
                    pause_end = pause_end.replace(tzinfo=TAIPEI_TZ)
                pause_start = pause_start.astimezone(TAIPEI_TZ)
                pause_end = pause_end.astimezone(TAIPEI_TZ)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            for work_start, work_end in work_intervals:
                overlap_start = max(work_start, pause_start)
                overlap_end = min(work_end, pause_end)
                if overlap_end > overlap_start:
                    pause_overlaps.append((overlap_start, overlap_end))

        # A material pause and a manual pause should not be deducted twice if an
        # old record happens to overlap another pause record.
        deducted_seconds = 0
        latest_end = None
        for overlap_start, overlap_end in sorted(pause_overlaps):
            if latest_end is None or overlap_start > latest_end:
                deducted_seconds += int((overlap_end - overlap_start).total_seconds())
                latest_end = overlap_end
            elif overlap_end > latest_end:
                deducted_seconds += int((overlap_end - latest_end).total_seconds())
                latest_end = overlap_end
        total_seconds = max(0, total_seconds - deducted_seconds)
    except (TypeError, ValueError, OverflowError):
        return "-"
    return format_seconds(total_seconds)


def format_datetime(value):
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value)


def format_date(value):
    formatted = format_datetime(value)
    return formatted.split(" ", 1)[0]


def format_time(value):
    formatted = format_datetime(value)
    return formatted.split(" ", 1)[1] if " " in formatted else "-"


def _inspection_text(value, field, required=True):
    value = (value or "").strip()
    if required and not value:
        raise ValueError(f"{field}不可為空")
    if "|" in value or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{field}含有不支援的字元")
    return value


def _inspection_code(value, field):
    value = _inspection_text(value, field)
    if any(ord(ch) > 127 for ch in value):
        raise ValueError(f"{field}請使用英數字或常見符號")
    return value


def _inspection_axis_type(value):
    value = str(value or "4").strip()
    if value not in INSPECTION_AXIS_LABELS:
        raise ValueError("軸別只能選擇四軸或五軸")
    return value


def _infer_inspection_axis_type(model_name, machine_serial=""):
    model_key = (model_name or "").strip().upper()
    serial_key = (machine_serial or "").strip().upper()
    return "5" if model_key.startswith(("F", "M")) or serial_key.startswith("S") else "4"


def _generated_model_code(model_name, db):
    base = re.sub(r"[^A-Za-z0-9]+", "-", model_name).strip("-").upper()
    base = base or "MODEL-" + checksum_for(["MODEL", model_name])[:8]
    candidate = base
    suffix = 2
    while db.execute("SELECT 1 FROM inspection_model_master WHERE model_code=?", (candidate,)).fetchone():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _inspection_id():
    return uuid.uuid4().hex


def _inspection_audit(action, entity_type, entity_id=None, details=None, operator_name="system"):
    get_db().execute(
        "INSERT INTO inspection_audit_log(log_id,action,entity_type,entity_id,details,operator_name,created_at) VALUES(?,?,?,?,?,?,?)",
        (_inspection_id(), action, entity_type, str(entity_id) if entity_id is not None else None, json.dumps(details or {}, ensure_ascii=False), operator_name, utc_now()),
    )


def _inspection_setting(key, default=None):
    row = get_db().execute("SELECT value FROM inspection_app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def access_password_enabled():
    return _inspection_setting("access_password_enabled", "1") == "1"


def verify_access_password(password):
    stored = _inspection_setting("access_password_hash")
    return bool(stored and check_password_hash(stored, password or ""))


def set_access_password_enabled(enabled, operator_name="admin"):
    db = get_db()
    db.execute("UPDATE inspection_app_settings SET value=?,updated_at=? WHERE key=?", ("1" if enabled else "0", utc_now(), "access_password_enabled"))
    _inspection_audit("update", "app_setting", "access_password_enabled", {"enabled": bool(enabled)}, operator_name)
    db.commit()


def change_access_password(current_password, new_password, operator_name="admin"):
    if not verify_access_password(current_password):
        raise ValueError("目前密碼錯誤")
    if len(new_password or "") < 3:
        raise ValueError("新密碼至少需要 3 個字元")
    db = get_db()
    db.execute("UPDATE inspection_app_settings SET value=?,updated_at=? WHERE key=?", (generate_password_hash(new_password), utc_now(), "access_password_hash"))
    _inspection_audit("change_password", "app_setting", "access_password_hash", {"changed": True}, operator_name)
    db.commit()


def list_models(active_only=False):
    sql = "SELECT * FROM inspection_model_master"
    if active_only:
        sql += " WHERE is_active=1"
    return get_db().execute(sql + " ORDER BY model_name COLLATE NOCASE").fetchall()


def create_model(model_name, model_code=None, description="", operator_name="admin", serial_prefix=None, axis_type="4"):
    del serial_prefix
    model_name = _inspection_text(model_name, "機型名稱")
    del axis_type
    axis_type = _infer_inspection_axis_type(model_name)
    db = get_db()
    model_code = _generated_model_code(model_name, db) if model_code is None else _inspection_code(model_code, "機型代碼")
    try:
        cur = db.execute("INSERT INTO inspection_model_master(model_name,model_code,description,axis_type,created_at,updated_at) VALUES(?,?,?,?,?,?)", (model_name, model_code, (description or "").strip(), axis_type, utc_now(), utc_now()))
    except sqlite3.IntegrityError as exc:
        raise ValueError("機型名稱或代碼已存在") from exc
    _inspection_audit("create", "model_master", cur.lastrowid, {"model_name": model_name, "model_code": model_code}, operator_name)
    db.commit()
    return cur.lastrowid


def update_model(model_id, model_name, model_code=None, description="", is_active=True, operator_name="admin", serial_prefix=None, axis_type=None):
    del serial_prefix
    model_name = _inspection_text(model_name, "機型名稱")
    db = get_db()
    current = db.execute("SELECT * FROM inspection_model_master WHERE id=?", (model_id,)).fetchone()
    if not current:
        raise ValueError("找不到機型")
    model_code = _inspection_code(model_code or current["model_code"], "機型代碼")
    del axis_type
    axis_type = _infer_inspection_axis_type(model_name)
    try:
        db.execute("UPDATE inspection_model_master SET model_name=?,model_code=?,description=?,axis_type=?,is_active=?,updated_at=? WHERE id=?", (model_name, model_code, (description or "").strip(), axis_type, int(bool(is_active)), utc_now(), model_id))
        db.execute("UPDATE inspection_order SET axis_type_snapshot=CASE WHEN UPPER(TRIM(?)) LIKE 'F%' OR UPPER(TRIM(?)) LIKE 'M%' OR UPPER(TRIM(machine_serial)) LIKE 'S%' THEN '5' ELSE '4' END WHERE model_id=?", (model_name, model_name, model_id))
    except sqlite3.IntegrityError as exc:
        raise ValueError("機型名稱或代碼已存在") from exc
    _inspection_audit("update", "model_master", model_id, {"model_name": model_name, "axis_type": axis_type, "is_active": bool(is_active)}, operator_name)
    db.commit()


def delete_model(model_id, expected_name, backup_dir, operator_name="admin"):
    db = get_db()
    model = db.execute("SELECT * FROM inspection_model_master WHERE id=?", (model_id,)).fetchone()
    if not model or model["model_name"] != expected_name:
        raise ValueError("機型不存在或名稱已變更")
    if db.execute("SELECT 1 FROM inspection_order WHERE model_id=? LIMIT 1", (model_id,)).fetchone():
        raise ValueError("此機型已有訂單紀錄，不能刪除；請改用停用")
    backup = backup_database(backup_dir)
    db.execute("DELETE FROM inspection_model_master WHERE id=?", (model_id,))
    _inspection_audit("delete", "model_master", model_id, {"model_name": model["model_name"], "backup": str(backup)}, operator_name)
    db.commit()
    return backup


def _worker_barcode(employee_no):
    parts = ["IW1", f"{int(employee_no):08X}"]
    return "".join(parts) + checksum_for(parts)[:4]


def _compact_worker_barcode(worker_id):
    parts = ["IW1", f"{int(worker_id):08X}"]
    return "".join(parts) + checksum_for(parts)[:4]


def list_inspection_workers(active_only=False):
    sql = "SELECT * FROM inspection_worker"
    if active_only:
        sql += " WHERE is_active=1"
    return get_db().execute(sql + " ORDER BY employee_no COLLATE NOCASE").fetchall()


def create_inspection_worker(employee_no, name, changed_by="admin"):
    employee_no = _inspection_code(employee_no, "員工編號")
    if len(employee_no) > 4:
        raise ValueError("員工編號最多四碼")
    name = _inspection_text(name, "員工姓名")
    db = get_db()
    try:
        cur = db.execute("INSERT INTO inspection_worker(employee_no,name,barcode,created_at,updated_at) VALUES(?,?,?,?,?)", (employee_no, name, "TMP-" + _inspection_id(), utc_now(), utc_now()))
        db.execute("UPDATE inspection_worker SET barcode=? WHERE id=?", (_compact_worker_barcode(cur.lastrowid), cur.lastrowid))
    except sqlite3.IntegrityError as exc:
        raise ValueError("員工編號或員工條碼已存在") from exc
    _inspection_audit("create", "inspection_worker", cur.lastrowid, {"employee_no": employee_no, "name": name}, changed_by)
    db.commit()
    return cur.lastrowid


def ensure_worker_barcode(worker_id):
    db = get_db()
    worker = get_inspection_worker(worker_id)
    if not worker:
        raise ValueError("找不到校機人員")
    compact = _compact_worker_barcode(worker_id)
    if worker["barcode"] != compact:
        db.execute("UPDATE inspection_worker SET barcode=?,updated_at=? WHERE id=?", (compact, utc_now(), worker_id))
        db.commit()
    return get_inspection_worker(worker_id)


def set_inspection_worker_active(worker_id, is_active, changed_by="admin"):
    db = get_db()
    worker = db.execute("SELECT * FROM inspection_worker WHERE id=?", (worker_id,)).fetchone()
    if not worker:
        raise ValueError("找不到校機人員")
    db.execute("UPDATE inspection_worker SET is_active=?,updated_at=? WHERE id=?", (int(bool(is_active)), utc_now(), worker_id))
    _inspection_audit("update", "inspection_worker", worker_id, {"employee_no": worker["employee_no"], "is_active": bool(is_active)}, changed_by)
    db.commit()


def update_inspection_worker(worker_id, employee_no, name, changed_by="admin"):
    employee_no = _inspection_code(employee_no, "員工編號")
    if len(employee_no) > 4:
        raise ValueError("員工編號最多四碼")
    name = _inspection_text(name, "員工姓名")
    db = get_db()
    worker = db.execute("SELECT * FROM inspection_worker WHERE id=?", (worker_id,)).fetchone()
    if not worker:
        raise ValueError("找不到校機人員")
    try:
        db.execute(
            "UPDATE inspection_worker SET employee_no=?,name=?,updated_at=? WHERE id=?",
            (employee_no, name, utc_now(), worker_id),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("員工編號已存在") from exc
    _inspection_audit(
        "update",
        "inspection_worker",
        worker_id,
        {"old_employee_no": worker["employee_no"], "old_name": worker["name"], "employee_no": employee_no, "name": name},
        changed_by,
    )
    db.commit()


def delete_inspection_worker(worker_id, expected_employee_no, confirmation, backup_dir, changed_by="admin"):
    if confirmation != "刪除":
        raise ValueError("請輸入「刪除」確認")
    db = get_db()
    worker = db.execute("SELECT * FROM inspection_worker WHERE id=?", (worker_id,)).fetchone()
    if not worker or worker["employee_no"] != (expected_employee_no or "").strip():
        raise ValueError("人員不存在或員工編號已變更")
    if db.execute("SELECT 1 FROM inspection_order WHERE worker_id=? LIMIT 1", (worker_id,)).fetchone():
        raise ValueError("此人員已有訂單紀錄，不能刪除；請改用停用")
    backup = backup_database(backup_dir)
    db.execute("DELETE FROM inspection_worker WHERE id=?", (worker_id,))
    _inspection_audit("delete", "inspection_worker", worker_id, {"employee_no": worker["employee_no"], "name": worker["name"], "backup": str(backup)}, changed_by)
    db.commit()
    return backup


def get_inspection_worker(worker_id):
    return get_db().execute("SELECT * FROM inspection_worker WHERE id=?", (worker_id,)).fetchone()


def _active_worker(worker_id):
    return get_db().execute("SELECT * FROM inspection_worker WHERE id=? AND is_active=1", (worker_id,)).fetchone()


def create_order(order_no, model_id=None, machine_serial=None, customer_name="", expected_ship_date="", reinspection_reason="", created_by="admin", model_name=None, axis_type="4"):
    order_no = _inspection_code(order_no, "訂單號碼")
    machine_serial = _inspection_code(machine_serial, "機台製號")
    customer_name = _inspection_text(customer_name, "客戶名稱", required=False)
    reinspection_reason = _inspection_text(reinspection_reason, "重新檢驗原因", required=False)
    del axis_type
    expected_ship_date = (expected_ship_date or "").strip() or None
    if expected_ship_date:
        try:
            datetime.strptime(expected_ship_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("預計出貨日期格式不正確") from exc
    db = get_db()
    if model_id is not None:
        model = db.execute("SELECT * FROM inspection_model_master WHERE id=? AND is_active=1", (model_id,)).fetchone()
        if not model:
            raise ValueError("機型不存在或已停用")
    else:
        model_name = _inspection_text(model_name, "機型名稱")
        model = db.execute("SELECT * FROM inspection_model_master WHERE model_name=? COLLATE NOCASE", (model_name,)).fetchone()
        if model and not model["is_active"]:
            raise ValueError("此機型已停用")
        if model:
            model_id = model["id"]
        else:
            model_code = _generated_model_code(model_name, db)
            now = utc_now()
            cur_model = db.execute("INSERT INTO inspection_model_master(model_name,model_code,description,axis_type,created_at,updated_at) VALUES(?,?,?,?,?,?)", (model_name, model_code, "由建立訂單時輸入", _infer_inspection_axis_type(model_name), now, now))
            model_id = cur_model.lastrowid
            model = db.execute("SELECT * FROM inspection_model_master WHERE id=?", (model_id,)).fetchone()
            _inspection_audit("create", "model_master", model_id, {"model_name": model_name, "model_code": model_code, "source": "order"}, created_by)
    now = utc_now()
    order_axis_type = _infer_inspection_axis_type(model["model_name"], machine_serial)
    try:
        cur = db.execute("INSERT INTO inspection_order(order_no,model_id,model_name_snapshot,model_code_snapshot,axis_type_snapshot,machine_serial,customer_name,expected_ship_date,reinspection_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (order_no, model_id, model["model_name"], model["model_code"], order_axis_type, machine_serial, customer_name, expected_ship_date, reinspection_reason, now, now))
    except sqlite3.IntegrityError as exc:
        if "inspection_order.order_no" in str(exc):
            raise ValueError("訂單號碼已存在") from exc
        raise ValueError(f"訂單資料無法建立：{exc}") from exc
    order_id = cur.lastrowid
    _inspection_audit("create", "inspection_order", order_id, {"order_no": order_no, "machine_serial": machine_serial}, created_by)
    db.commit()
    ensure_order_barcodes(order_id)
    return order_id


def get_order(order_id):
    return get_db().execute(
        """
        SELECT o.*,
               EXISTS(SELECT 1 FROM inspection_material_pause p WHERE p.order_id=o.id AND p.resumed_at IS NULL) AS material_paused,
               EXISTS(SELECT 1 FROM inspection_manual_pause p WHERE p.order_id=o.id AND p.resumed_at IS NULL) AS manual_paused,
               CAST(ROUND(COALESCE((
                   SELECT SUM((julianday(COALESCE(p.resumed_at, ?)) - julianday(p.paused_at)) * 86400.0)
                   FROM inspection_material_pause p WHERE p.order_id=o.id
               ), 0)) AS INTEGER) AS material_pause_seconds
               ,CAST(ROUND(COALESCE((
                   SELECT SUM((julianday(COALESCE(p.resumed_at, ?)) - julianday(p.paused_at)) * 86400.0)
                   FROM inspection_manual_pause p WHERE p.order_id=o.id
               ), 0)) AS INTEGER) AS manual_pause_seconds
        FROM inspection_order o
        WHERE o.id=?
        """,
        (utc_now(), utc_now(), order_id),
    ).fetchone()


def list_material_pauses(order_id):
    now = utc_now()
    rows = get_db().execute(
        "SELECT * FROM inspection_material_pause WHERE order_id=? ORDER BY paused_at",
        (order_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            start = datetime.fromisoformat(row["paused_at"])
            end = datetime.fromisoformat(row["resumed_at"] or now)
            item["duration_seconds"] = max(0, int((end - start).total_seconds()))
        except (TypeError, ValueError):
            item["duration_seconds"] = 0
        result.append(item)
    return result


def list_manual_pauses(order_id):
    now = utc_now()
    rows = get_db().execute(
        "SELECT * FROM inspection_manual_pause WHERE order_id=? ORDER BY paused_at",
        (order_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            start = datetime.fromisoformat(row["paused_at"])
            end = datetime.fromisoformat(row["resumed_at"] or now)
            item["duration_seconds"] = max(0, int((end - start).total_seconds()))
        except (TypeError, ValueError):
            item["duration_seconds"] = 0
        result.append(item)
    return result


def list_recent_orders(limit=50):
    limit = max(1, min(int(limit), 200))
    return get_db().execute("SELECT * FROM inspection_order ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def make_order_payload(kind, order_id):
    """Create a compact, stable barcode token; full order data stays in SQLite."""
    prefix = "I" + ("S" if kind == "START" else "F")
    value = int(order_id)
    if value >= 36 ** 6:
        raise ValueError("訂單數量已超過短版條碼容量")
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    token = "0" * 6
    encoded = ""
    while value:
        value, remainder = divmod(value, 36)
        encoded = digits[remainder] + encoded
    token = (token + encoded)[-6:]
    return prefix + token + checksum_for([prefix, token])[:4]


def make_material_action_payload(action):
    if action not in MATERIAL_ACTION_LABELS:
        raise ValueError("不支援的缺料操作")
    prefix = "IM1" + ("S" if action == "SHORTAGE" else "R")
    return prefix + checksum_for([prefix])[:4]


def make_pause_action_payload():
    prefix = "IM1P"
    return prefix + checksum_for([prefix])[:4]


def material_action_barcodes():
    return [
        {"action": action, "label": label, "payload": make_material_action_payload(action)}
        for action, label in MATERIAL_ACTION_LABELS.items()
    ]


def pause_action_barcodes():
    return [{"action": "PAUSE", "label": PAUSE_ACTION_LABEL, "payload": make_pause_action_payload()}]


def parse_material_action_payload(payload):
    raw = (payload or "").strip()
    for record in material_action_barcodes():
        if raw == record["payload"]:
            return record["action"]
    return None


def parse_pause_action_payload(payload):
    raw = (payload or "").strip().upper()
    for record in pause_action_barcodes():
        if raw == record["payload"]:
            return record["action"]
    return None


def ensure_order_barcodes(order_id):
    db = get_db()
    order = get_order(order_id)
    if not order:
        raise ValueError("找不到訂單")
    now = utc_now()
    for kind in ("START", "FINISH"):
        compact_payload = make_order_payload(kind, order_id)
        existing = db.execute("SELECT id,payload FROM inspection_barcode_record WHERE order_id=? AND barcode_type=?", (order_id, kind)).fetchone()
        if existing and existing["payload"] != compact_payload:
            db.execute("UPDATE inspection_barcode_record SET payload=? WHERE id=?", (compact_payload, existing["id"]))
        else:
            db.execute("INSERT OR IGNORE INTO inspection_barcode_record(order_id,barcode_type,payload,created_at) VALUES(?,?,?,?)", (order_id, kind, compact_payload, now))
    db.commit()
    return get_order(order_id)


def parse_order_payload(payload):
    raw = (payload or "").strip()
    if len(raw) == 12 and raw[:2] in {"IS", "IF"}:
        prefix, token, check = raw[:2], raw[2:8], raw[8:]
        if checksum_for([prefix, token])[:4] != check:
            raise ValueError("訂單條碼 checksum 不正確")
        return {"type": "START" if raw[1] == "S" else "FINISH", "order_id": int(token, 36)}
    if len(raw) == 15 and raw[:2] == "I1" and raw[2] in {"S", "F"}:
        prefix, token, check = raw[:3], raw[3:11], raw[11:]
        if checksum_for([prefix, token])[:4] != check:
            raise ValueError("訂單條碼 checksum 不正確")
        return {"type": "START" if raw[2] == "S" else "FINISH", "order_id": int(token, 16)}
    parts = raw.split("|")
    if len(parts) == 5 and parts[:2] == ["INSP", "V1"] and parts[2] in {"START", "FINISH"} and checksum_for(parts[:-1]) == parts[-1]:
        return {"type": parts[2], "order_no": parts[3]}
    raise ValueError("無效訂單條碼")


def parse_worker_payload(payload):
    parts = (payload or "").strip().split("|")
    if len(parts) != 5 or parts[:3] != ["INSP", "V1", "EMP"] or checksum_for(parts[:-1]) != parts[-1]:
        raise ValueError("無效員工條碼")
    return parts[3]


def normalize_scanned_payload(payload):
    """Remove common scanner wrappers without changing the barcode itself."""
    raw = re.sub(r"[\x00-\x1f\x7f]", "", str(payload or "")).strip()
    for prefix in SCANNER_AIM_PREFIXES:
        if raw[:len(prefix)].upper() == prefix:
            raw = raw[len(prefix):].strip()
            break
    upper = raw.upper()
    if any(
        re.fullmatch(pattern, upper)
        for pattern in (
            r"IW1[0-9A-F]{12}",
            r"I[SF][0-9A-Z]{10}",
            r"I1[SF][0-9A-F]{12}",
            r"IM1[SR][0-9A-F]{4}",
            r"IM1P[0-9A-F]{4}",
        )
    ):
        return upper
    return raw


def _record_scan(event_id, payload, barcode_type, order_id, employee_no, result, message, scanned_at):
    get_db().execute("INSERT INTO inspection_scan_event(event_id,payload,barcode_type,order_id,employee_no,result,message,scanned_at) VALUES(?,?,?,?,?,?,?,?)", (event_id, payload, barcode_type, order_id, employee_no, result, message, scanned_at))


def _active_material_pause(db, order_id):
    return db.execute(
        "SELECT * FROM inspection_material_pause WHERE order_id=? AND resumed_at IS NULL",
        (order_id,),
    ).fetchone()


def _active_manual_pause(db, order_id):
    return db.execute(
        "SELECT * FROM inspection_manual_pause WHERE order_id=? AND resumed_at IS NULL",
        (order_id,),
    ).fetchone()


def _apply_material_action(db, order, current_worker, action, scanned_at):
    label = MATERIAL_ACTION_LABELS[action]
    if order["status"] != "started":
        if order["status"] == "pending":
            return "invalid", f"必須先 START 才能登記{label}"
        return "invalid", "此訂單已完成或已取消"
    if order["worker_id"] != current_worker["id"]:
        return "invalid", f"只有原認領人員可以登記{label}"

    active_pause = _active_material_pause(db, order["id"])
    if action == "SHORTAGE":
        if active_pause:
            return "duplicate", f"此訂單已於 {format_datetime(active_pause['paused_at'])} 進入缺料暫停"
        try:
            if order["started_at"] and datetime.fromisoformat(scanned_at) < datetime.fromisoformat(order["started_at"]):
                return "invalid", "缺料時間不能早於 START 時間"
        except (TypeError, ValueError):
            pass
        inserted = db.execute(
            """
            INSERT OR IGNORE INTO inspection_material_pause(
                order_id,paused_at,paused_by_worker_id,paused_by_employee_no,paused_by_name
            ) VALUES(?,?,?,?,?)
            """,
            (order["id"], scanned_at, current_worker["id"], current_worker["employee_no"], current_worker["name"]),
        ).rowcount
        if not inserted:
            active_pause = _active_material_pause(db, order["id"])
            return "duplicate", f"此訂單已於 {format_datetime(active_pause['paused_at']) if active_pause else '稍早'} 進入缺料暫停"
        _inspection_audit(
            "material_shortage",
            "inspection_order",
            order["id"],
            {"order_no": order["order_no"], "employee_no": current_worker["employee_no"], "paused_at": scanned_at},
            current_worker["name"],
        )
        return "success", f"缺料暫停成功：{order['order_no']} 已停止計算工時"

    if not active_pause:
        return "invalid", "此訂單目前沒有缺料暫停，不能補料"
    try:
        if datetime.fromisoformat(scanned_at) < datetime.fromisoformat(active_pause["paused_at"]):
            return "invalid", "補料時間不能早於缺料時間"
    except (TypeError, ValueError):
        pass
    db.execute(
        """
        UPDATE inspection_material_pause
        SET resumed_at=?,resumed_by_worker_id=?,resumed_by_employee_no=?,resumed_by_name=?
        WHERE id=? AND resumed_at IS NULL
        """,
        (scanned_at, current_worker["id"], current_worker["employee_no"], current_worker["name"], active_pause["id"]),
    )
    _inspection_audit(
        "material_replenish",
        "inspection_order",
        order["id"],
        {"order_no": order["order_no"], "employee_no": current_worker["employee_no"], "resumed_at": scanned_at},
        current_worker["name"],
    )
    return "success", f"補料成功：{order['order_no']} 已繼續計算工時"


def _apply_manual_pause(db, order, current_worker, scanned_at):
    if order["status"] != "started":
        if order["status"] == "pending":
            return "invalid", "必須先 START 才能暫停"
        return "invalid", "此訂單已完成或已取消"
    if order["worker_id"] != current_worker["id"]:
        return "invalid", "只有原認領人員可以暫停此訂單"
    if _active_material_pause(db, order["id"]):
        return "invalid", "此訂單目前為缺料暫停中，請先完成補料"

    if _active_manual_pause(db, order["id"]):
        return "duplicate", "此訂單已在人工暫停中，請直接掃描原訂單 START 恢復"

    try:
        if order["started_at"] and datetime.fromisoformat(scanned_at) < datetime.fromisoformat(order["started_at"]):
            return "invalid", "暫停時間不能早於 START 時間"
    except (TypeError, ValueError):
        pass
    inserted = db.execute(
        """
        INSERT OR IGNORE INTO inspection_manual_pause(
            order_id,paused_at,paused_by_worker_id,paused_by_employee_no,paused_by_name
        ) VALUES(?,?,?,?,?)
        """,
        (order["id"], scanned_at, current_worker["id"], current_worker["employee_no"], current_worker["name"]),
    ).rowcount
    if not inserted:
        return "duplicate", "此訂單已在人工暫停中"
    _inspection_audit(
        "manual_pause",
        "inspection_order",
        order["id"],
        {"order_no": order["order_no"], "employee_no": current_worker["employee_no"], "paused_at": scanned_at},
        current_worker["name"],
    )
    return "success", f"暫停成功：{order['order_no']} 已停止計算實際工時"


def _resume_manual_pause(db, order, current_worker, scanned_at):
    active_pause = _active_manual_pause(db, order["id"])
    if not active_pause:
        return "invalid", "此訂單目前沒有人工暫停"
    try:
        if datetime.fromisoformat(scanned_at) < datetime.fromisoformat(active_pause["paused_at"]):
            return "invalid", "恢復時間不能早於暫停時間"
    except (TypeError, ValueError):
        pass
    db.execute(
        """
        UPDATE inspection_manual_pause
        SET resumed_at=?,resumed_by_worker_id=?,resumed_by_employee_no=?,resumed_by_name=?
        WHERE id=? AND resumed_at IS NULL
        """,
        (scanned_at, current_worker["id"], current_worker["employee_no"], current_worker["name"], active_pause["id"]),
    )
    _inspection_audit(
        "manual_pause_resume",
        "inspection_order",
        order["id"],
        {"order_no": order["order_no"], "employee_no": current_worker["employee_no"], "resumed_at": scanned_at},
        current_worker["name"],
    )
    return "success", f"恢復成功：{order['order_no']} 已繼續計算實際工時"


def scan(payload, worker_id=None, operator_name="scanner", pending_material_action=None, pending_pause_action=None):
    del operator_name
    db = get_db()
    payload = normalize_scanned_payload(payload)
    event_id, scanned_at = _inspection_id(), utc_now()
    worker = db.execute("SELECT * FROM inspection_worker WHERE barcode=? COLLATE NOCASE", (payload,)).fetchone()
    if not worker:
        try:
            legacy_employee_no = parse_worker_payload(payload)
        except ValueError:
            legacy_employee_no = None
        if legacy_employee_no:
            worker = db.execute(
                "SELECT * FROM inspection_worker WHERE employee_no=? COLLATE NOCASE",
                (legacy_employee_no,),
            ).fetchone()
    if worker:
        if not worker["is_active"]:
            message = "此校機人員已停用，不能報工"
            _record_scan(event_id, payload, "EMPLOYEE", None, worker["employee_no"], "invalid", message, scanned_at)
            db.commit()
            return {"event_id": event_id, "result": "invalid", "message": message, "scanned_at": scanned_at, "scan_kind": "employee"}
        message = f"已辨識：{worker['name']}（{worker['employee_no']}），請掃描訂單 START、FINISH、缺料、補料或暫停"
        _record_scan(event_id, payload, "EMPLOYEE", None, worker["employee_no"], "success", message, scanned_at)
        db.commit()
        return {"event_id": event_id, "result": "success", "message": message, "scanned_at": scanned_at, "scan_kind": "employee", "worker_id": worker["id"], "employee_no": worker["employee_no"], "worker_name": worker["name"]}

    current_worker = _active_worker(worker_id) if worker_id else None
    employee_no = current_worker["employee_no"] if current_worker else None
    material_action = parse_material_action_payload(payload)
    if material_action:
        if not current_worker:
            result, message = "invalid", "請先掃描有效且啟用中的員工條碼"
        else:
            result = "success"
            message = f"已選擇{MATERIAL_ACTION_LABELS[material_action]}，請掃描該機台的訂單 START 條碼"
        _record_scan(event_id, payload, material_action, None, employee_no, result, message, scanned_at)
        db.commit()
        response = {"event_id": event_id, "result": result, "message": message, "scanned_at": scanned_at, "scan_kind": "material_action"}
        if result == "success":
            response["material_action"] = material_action
        return response

    pause_action = parse_pause_action_payload(payload)
    if pause_action:
        if not current_worker:
            result, message = "invalid", "請先掃描有效且啟用中的員工條碼"
        else:
            result = "success"
            message = f"已選擇{PAUSE_ACTION_LABEL}，請掃描該訂單的 START 條碼"
        _record_scan(event_id, payload, "PAUSE", None, employee_no, result, message, scanned_at)
        db.commit()
        response = {"event_id": event_id, "result": result, "message": message, "scanned_at": scanned_at, "scan_kind": "pause_action"}
        if result == "success":
            response["pause_action"] = pause_action
        return response

    stored_barcode = db.execute(
        """
        SELECT o.*, b.barcode_type AS scanned_barcode_type
        FROM inspection_barcode_record b
        JOIN inspection_order o ON o.id=b.order_id
        WHERE b.payload=? COLLATE NOCASE
        """,
        (payload,),
    ).fetchone()
    if stored_barcode:
        order = stored_barcode
        parsed = {"type": stored_barcode["scanned_barcode_type"], "order_id": stored_barcode["id"]}
    else:
        try:
            parsed = parse_order_payload(payload)
        except ValueError as exc:
            counts = db.execute(
                "SELECT (SELECT COUNT(*) FROM inspection_worker) AS workers, (SELECT COUNT(*) FROM inspection_order) AS orders"
            ).fetchone()
            if counts["workers"] == 0 and counts["orders"] == 0:
                message = "目前程式讀到空白資料庫，請確認舊版 instance 資料夾已放在 InspectionReporting.exe 同一層"
            elif str(exc) == "無效訂單條碼":
                message = "無法辨識條碼，請確認掃描的是本系統的員工、訂單、缺料或補料條碼"
            else:
                message = str(exc)
            _record_scan(event_id, payload, None, None, employee_no, "invalid", message, scanned_at)
            db.commit()
            return {"event_id": event_id, "result": "invalid", "message": message, "scanned_at": scanned_at, "scan_kind": "unknown"}

        if parsed.get("order_id"):
            order = db.execute("SELECT * FROM inspection_order WHERE id=?", (parsed["order_id"],)).fetchone()
        else:
            order = db.execute("SELECT * FROM inspection_order WHERE order_no=? COLLATE NOCASE", (parsed["order_no"],)).fetchone()

    pending_material_action = pending_material_action if pending_material_action in MATERIAL_ACTION_LABELS else None
    pending_pause_action = pending_pause_action if pending_pause_action == "PAUSE" else None
    result, message = "invalid", ""
    if not current_worker:
        message = "請先掃描有效且啟用中的員工條碼"
    elif not order:
        message = "找不到相符的訂單"
    elif pending_material_action:
        if parsed["type"] != "START":
            message = f"已選擇{MATERIAL_ACTION_LABELS[pending_material_action]}，請掃描該訂單的 START 條碼"
        else:
            result, message = _apply_material_action(db, order, current_worker, pending_material_action, scanned_at)
    elif pending_pause_action:
        if parsed["type"] != "START":
            message = f"已選擇{PAUSE_ACTION_LABEL}，請掃描該訂單的 START 條碼"
        else:
            result, message = _apply_manual_pause(db, order, current_worker, scanned_at)
    elif parsed["type"] == "START":
        if order["status"] == "pending":
            updated = db.execute("UPDATE inspection_order SET status='started',worker_id=?,worker_no_snapshot=?,worker_name_snapshot=?,started_at=?,updated_at=? WHERE id=? AND status='pending'", (current_worker["id"], current_worker["employee_no"], current_worker["name"], scanned_at, scanned_at, order["id"])).rowcount
            if updated:
                result, message = "success", f"START 成功：{order['order_no']} 已由 {current_worker['name']} 認領"
                _inspection_audit("start", "inspection_order", order["id"], {"order_no": order["order_no"], "employee_no": current_worker["employee_no"]}, current_worker["name"])
            else:
                result, message = "invalid", "訂單狀態已變更，請重新掃描"
        elif order["status"] == "started" and order["worker_id"] == current_worker["id"]:
            if _active_manual_pause(db, order["id"]):
                result, message = _resume_manual_pause(db, order, current_worker, scanned_at)
            else:
                result, message = "duplicate", "此訂單已由你開始校機"
        elif order["status"] == "started":
            message = "此訂單已由其他校機人員認領"
        else:
            message = "此訂單已完成或已取消"
    elif order["status"] != "started":
        if order["status"] == "pending":
            message = "必須先掃描訂單 START"
        elif order["status"] == "completed":
            result, message = "duplicate", "此訂單已完成 FINISH"
        else:
            message = "此訂單已取消"
    elif order["worker_id"] != current_worker["id"]:
        message = "只有原認領人員可以 FINISH 此訂單"
    elif _active_material_pause(db, order["id"]):
        message = "此訂單目前為缺料暫停中，請先完成補料掃描"
    elif _active_manual_pause(db, order["id"]):
        message = "此訂單目前為人工暫停中，請先恢復校機"
    else:
        try:
            starts_after_finish = order["started_at"] and datetime.fromisoformat(scanned_at) < datetime.fromisoformat(order["started_at"])
        except (TypeError, ValueError):
            starts_after_finish = False
        if starts_after_finish:
            message = "FINISH 時間不能早於 START 時間"
        else:
            updated = db.execute("UPDATE inspection_order SET status='completed',finished_at=?,updated_at=? WHERE id=? AND status='started'", (scanned_at, scanned_at, order["id"])).rowcount
            if updated:
                result, message = "success", f"FINISH 成功：{order['order_no']}"
                _inspection_audit("finish", "inspection_order", order["id"], {"order_no": order["order_no"], "employee_no": current_worker["employee_no"]}, current_worker["name"])
            else:
                result, message = "invalid", "訂單狀態已變更，請重新掃描"
    recorded_type = pending_pause_action or pending_material_action or parsed["type"]
    _record_scan(event_id, payload, recorded_type, order["id"] if order else None, employee_no, result, message, scanned_at)
    db.commit()
    return {"event_id": event_id, "result": result, "message": message, "scanned_at": scanned_at, "scan_kind": "order", "clear_worker": True, "clear_material_action": True, "clear_pause_action": True, "order_no": order["order_no"] if order else parsed.get("order_no", ""), "barcode_type": recorded_type}


def cancel_order(order_id, cancelled_by="admin"):
    db = get_db()
    order = get_order(order_id)
    if not order:
        raise ValueError("找不到訂單")
    if order["status"] == "completed":
        raise ValueError("已完成訂單不能取消")
    if order["status"] == "cancelled":
        raise ValueError("訂單已取消")
    now = utc_now()
    db.execute(
        """
        UPDATE inspection_material_pause
        SET resumed_at=?,resumed_by_name=?
        WHERE order_id=? AND resumed_at IS NULL
        """,
        (now, f"{cancelled_by}（取消訂單）", order_id),
    )
    db.execute(
        """
        UPDATE inspection_manual_pause
        SET resumed_at=?,resumed_by_name=?
        WHERE order_id=? AND resumed_at IS NULL
        """,
        (now, f"{cancelled_by}（取消訂單）", order_id),
    )
    db.execute("UPDATE inspection_order SET status='cancelled',cancelled_at=?,cancelled_by=?,updated_at=? WHERE id=?", (now, cancelled_by, now, order_id))
    _inspection_audit("cancel", "inspection_order", order_id, {"order_no": order["order_no"]}, cancelled_by)
    db.commit()


def start_order_manually(order_id, worker_id, started_at, reason, operator_name, password, backup_dir):
    reason, operator_name = _require_admin_correction(password, reason, operator_name)
    db = get_db()
    order = get_order(order_id)
    if not order:
        raise ValueError("找不到訂單")
    if order["status"] != "pending":
        raise ValueError("只有待校機訂單可以補登開始時間")

    try:
        worker_id = int(worker_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("請選擇校機人員") from exc
    worker = _active_worker(worker_id)
    if not worker:
        raise ValueError("校機人員不存在或已停用")

    raw_started_at = (started_at or "").strip()
    if not raw_started_at:
        raise ValueError("請輸入實際開始時間")
    try:
        parsed = datetime.fromisoformat(raw_started_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TAIPEI_TZ)
        parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError("開始時間格式不正確") from exc

    now = datetime.fromisoformat(utc_now())
    if parsed > now:
        raise ValueError("開始時間不能晚於現在")
    started_at_utc = parsed.isoformat(timespec="seconds")
    backup = backup_database(backup_dir)
    try:
        db.execute("BEGIN IMMEDIATE")
        updated = db.execute(
            """
            UPDATE inspection_order
            SET status='started',worker_id=?,worker_no_snapshot=?,worker_name_snapshot=?,started_at=?,updated_at=?
            WHERE id=? AND status='pending'
            """,
            (worker["id"], worker["employee_no"], worker["name"], started_at_utc, utc_now(), order_id),
        ).rowcount
        if not updated:
            raise ValueError("訂單狀態已變更，請重新開啟訂單")
        _inspection_audit(
            "manual_start",
            "inspection_order",
            order_id,
            {
                "order_no": order["order_no"],
                "old": {"status": order["status"], "worker_id": order["worker_id"], "started_at": order["started_at"]},
                "new": {"status": "started", "worker_id": worker["id"], "employee_no": worker["employee_no"], "started_at": started_at_utc},
                "reason": reason,
                "backup": str(backup),
            },
            operator_name,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return backup


def _parse_admin_datetime(value, label, allow_blank=False):
    raw = (value or "").strip()
    if not raw and allow_blank:
        return None
    if not raw:
        raise ValueError(f"請輸入{label}")
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TAIPEI_TZ)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}格式不正確") from exc


def _require_admin_correction(password, reason, operator_name):
    if not verify_access_password(password):
        raise ValueError("管理密碼錯誤")
    reason = (reason or "").strip()
    operator_name = (operator_name or "").strip()
    if not reason:
        raise ValueError("請輸入修改原因")
    if not operator_name:
        raise ValueError("請輸入修改人")
    return reason, operator_name


def _pause_table(pause_type):
    tables = {
        "material": "inspection_material_pause",
        "manual": "inspection_manual_pause",
    }
    try:
        return tables[pause_type]
    except KeyError as exc:
        raise ValueError("不支援的暫停類型") from exc


def _validate_pause_interval(db, order, paused_at, resumed_at, exclude_table=None, exclude_id=None):
    if not order["started_at"]:
        raise ValueError("訂單尚未開始，不能補登暫停紀錄")
    order_started = datetime.fromisoformat(order["started_at"])
    order_finished = datetime.fromisoformat(order["finished_at"]) if order["finished_at"] else None
    if paused_at < order_started:
        raise ValueError("暫停時間不能早於 START 時間")
    if resumed_at and resumed_at < paused_at:
        raise ValueError("恢復時間不能早於暫停時間")
    if order_finished:
        if resumed_at is None:
            raise ValueError("已完成訂單的暫停紀錄必須填寫恢復時間")
        if resumed_at > order_finished:
            raise ValueError("恢復時間不能晚於 FINISH 時間")
    else:
        now = datetime.fromisoformat(utc_now())
        if paused_at > now or (resumed_at and resumed_at > now):
            raise ValueError("暫停或恢復時間不能晚於現在")

    candidate_end = resumed_at or datetime.max.replace(tzinfo=timezone.utc)
    for table in ("inspection_material_pause", "inspection_manual_pause"):
        rows = db.execute(f"SELECT id,paused_at,resumed_at FROM {table} WHERE order_id=?", (order["id"],)).fetchall()
        for row in rows:
            if table == exclude_table and row["id"] == exclude_id:
                continue
            existing_start = datetime.fromisoformat(row["paused_at"])
            existing_end = datetime.fromisoformat(row["resumed_at"]) if row["resumed_at"] else datetime.max.replace(tzinfo=timezone.utc)
            if paused_at < existing_end and existing_start < candidate_end:
                raise ValueError("暫停區間不可與既有缺料或人工暫停重疊")


def correct_order_timeline(order_id, worker_id, started_at, finished_at, reason, operator_name, password, backup_dir):
    reason, operator_name = _require_admin_correction(password, reason, operator_name)
    db = get_db()
    order = get_order(order_id)
    if not order:
        raise ValueError("找不到訂單")
    if order["status"] == "cancelled":
        raise ValueError("已取消訂單不能修正報工時間")
    try:
        worker_id = int(worker_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("請選擇校機人員") from exc
    worker = get_inspection_worker(worker_id)
    if not worker:
        raise ValueError("找不到校機人員")

    start = _parse_admin_datetime(started_at, "START 時間")
    finish = _parse_admin_datetime(finished_at, "FINISH 時間", allow_blank=True)
    now = datetime.fromisoformat(utc_now())
    if start > now:
        raise ValueError("START 時間不能晚於現在")
    if finish and finish > now:
        raise ValueError("FINISH 時間不能晚於現在")
    if finish and finish < start:
        raise ValueError("FINISH 時間不能早於 START 時間")

    pauses = []
    for table in ("inspection_material_pause", "inspection_manual_pause"):
        pauses.extend(db.execute(f"SELECT paused_at,resumed_at FROM {table} WHERE order_id=?", (order_id,)).fetchall())
    for pause in pauses:
        pause_start = datetime.fromisoformat(pause["paused_at"])
        pause_end = datetime.fromisoformat(pause["resumed_at"]) if pause["resumed_at"] else None
        if pause_start < start:
            raise ValueError("START 時間不能晚於既有暫停時間")
        if finish and (pause_end is None or pause_end > finish):
            raise ValueError("FINISH 前必須完成所有暫停，且 FINISH 不可早於恢復時間")

    old_values = {
        "worker_id": order["worker_id"],
        "employee_no": order["worker_no_snapshot"],
        "worker_name": order["worker_name_snapshot"],
        "started_at": order["started_at"],
        "finished_at": order["finished_at"],
        "status": order["status"],
    }
    new_values = {
        "worker_id": worker["id"],
        "employee_no": worker["employee_no"],
        "worker_name": worker["name"],
        "started_at": start.isoformat(timespec="seconds"),
        "finished_at": finish.isoformat(timespec="seconds") if finish else None,
        "status": "completed" if finish else "started",
    }
    if old_values == new_values:
        raise ValueError("修正內容與目前資料相同")

    backup = backup_database(backup_dir)
    try:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT worker_id,worker_no_snapshot AS employee_no,worker_name_snapshot AS worker_name,started_at,finished_at,status FROM inspection_order WHERE id=?",
            (order_id,),
        ).fetchone()
        if not current or dict(current) != old_values:
            raise ValueError("訂單資料已被其他操作更新，請重新開啟後再修正")
        current_pauses = []
        for table in ("inspection_material_pause", "inspection_manual_pause"):
            current_pauses.extend(db.execute(f"SELECT paused_at,resumed_at FROM {table} WHERE order_id=?", (order_id,)).fetchall())
        for pause in current_pauses:
            pause_start = datetime.fromisoformat(pause["paused_at"])
            pause_end = datetime.fromisoformat(pause["resumed_at"]) if pause["resumed_at"] else None
            if pause_start < start:
                raise ValueError("START 時間不能晚於既有暫停時間")
            if finish and (pause_end is None or pause_end > finish):
                raise ValueError("FINISH 前必須完成所有暫停，且 FINISH 不可早於恢復時間")
        db.execute(
            """
            UPDATE inspection_order
            SET worker_id=?,worker_no_snapshot=?,worker_name_snapshot=?,started_at=?,finished_at=?,status=?,updated_at=?
            WHERE id=?
            """,
            (
                new_values["worker_id"],
                new_values["employee_no"],
                new_values["worker_name"],
                new_values["started_at"],
                new_values["finished_at"],
                new_values["status"],
                utc_now(),
                order_id,
            ),
        )
        _inspection_audit(
            "admin_correct_order",
            "inspection_order",
            order_id,
            {"order_no": order["order_no"], "old": old_values, "new": new_values, "reason": reason, "backup": str(backup)},
            operator_name,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return backup


def correct_pause_record(order_id, pause_type, pause_id, paused_at, resumed_at, reason, operator_name, password, backup_dir):
    reason, operator_name = _require_admin_correction(password, reason, operator_name)
    table = _pause_table(pause_type)
    db = get_db()
    order = get_order(order_id)
    if not order:
        raise ValueError("找不到訂單")
    if order["status"] == "cancelled":
        raise ValueError("已取消訂單不能修正暫停紀錄")
    try:
        pause_id = int(pause_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("暫停紀錄編號不正確") from exc
    pause = db.execute(f"SELECT * FROM {table} WHERE id=? AND order_id=?", (pause_id, order_id)).fetchone()
    if not pause:
        raise ValueError("找不到暫停紀錄")
    start = _parse_admin_datetime(paused_at, "暫停時間")
    finish = _parse_admin_datetime(resumed_at, "恢復時間", allow_blank=True)
    _validate_pause_interval(db, order, start, finish, exclude_table=table, exclude_id=pause_id)
    old_values = {"paused_at": pause["paused_at"], "resumed_at": pause["resumed_at"]}
    new_values = {
        "paused_at": start.isoformat(timespec="seconds"),
        "resumed_at": finish.isoformat(timespec="seconds") if finish else None,
    }
    if old_values == new_values:
        raise ValueError("修正內容與目前資料相同")

    backup = backup_database(backup_dir)
    try:
        db.execute("BEGIN IMMEDIATE")
        current_order = get_order(order_id)
        current_pause = db.execute(f"SELECT * FROM {table} WHERE id=? AND order_id=?", (pause_id, order_id)).fetchone()
        if not current_order or not current_pause or {"paused_at": current_pause["paused_at"], "resumed_at": current_pause["resumed_at"]} != old_values:
            raise ValueError("暫停紀錄已被其他操作更新，請重新開啟後再修正")
        _validate_pause_interval(db, current_order, start, finish, exclude_table=table, exclude_id=pause_id)
        db.execute(f"UPDATE {table} SET paused_at=?,resumed_at=? WHERE id=? AND order_id=?", (new_values["paused_at"], new_values["resumed_at"], pause_id, order_id))
        _inspection_audit(
            "admin_correct_pause",
            "inspection_order",
            order_id,
            {"order_id": order_id, "order_no": order["order_no"], "pause_type": pause_type, "pause_record_id": pause_id, "old": old_values, "new": new_values, "reason": reason, "backup": str(backup)},
            operator_name,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return backup


def add_pause_record(order_id, pause_type, paused_at, resumed_at, reason, operator_name, password, backup_dir):
    reason, operator_name = _require_admin_correction(password, reason, operator_name)
    table = _pause_table(pause_type)
    db = get_db()
    order = get_order(order_id)
    if not order:
        raise ValueError("找不到訂單")
    if order["status"] == "cancelled":
        raise ValueError("已取消訂單不能補登暫停紀錄")
    if not order["worker_id"]:
        raise ValueError("訂單尚未指定校機人員")
    start = _parse_admin_datetime(paused_at, "暫停時間")
    finish = _parse_admin_datetime(resumed_at, "恢復時間", allow_blank=True)
    _validate_pause_interval(db, order, start, finish)
    worker = get_inspection_worker(order["worker_id"])
    if not worker:
        raise ValueError("找不到訂單校機人員")

    backup = backup_database(backup_dir)
    try:
        db.execute("BEGIN IMMEDIATE")
        current_order = get_order(order_id)
        if not current_order or current_order["status"] != order["status"] or current_order["updated_at"] != order["updated_at"]:
            raise ValueError("訂單資料已被其他操作更新，請重新開啟後再補登")
        _validate_pause_interval(db, current_order, start, finish)
        cursor = db.execute(
            f"""
            INSERT INTO {table}(
                order_id,paused_at,resumed_at,
                paused_by_worker_id,paused_by_employee_no,paused_by_name,
                resumed_by_worker_id,resumed_by_employee_no,resumed_by_name
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                order_id,
                start.isoformat(timespec="seconds"),
                finish.isoformat(timespec="seconds") if finish else None,
                worker["id"], worker["employee_no"], worker["name"],
                worker["id"] if finish else None,
                worker["employee_no"] if finish else None,
                worker["name"] if finish else None,
            ),
        )
        values = {"paused_at": start.isoformat(timespec="seconds"), "resumed_at": finish.isoformat(timespec="seconds") if finish else None}
        _inspection_audit(
            "admin_add_pause",
            "inspection_order",
            order_id,
            {"order_id": order_id, "order_no": order["order_no"], "pause_type": pause_type, "pause_record_id": cursor.lastrowid, "old": None, "new": values, "reason": reason, "backup": str(backup)},
            operator_name,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return backup


def list_order_corrections(order_id):
    rows = get_db().execute(
        """
        SELECT * FROM inspection_audit_log
        WHERE (action LIKE 'admin_%' OR action='manual_start') AND entity_type='inspection_order' AND entity_id=?
        ORDER BY created_at DESC
        """,
        (str(order_id),),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["details_data"] = json.loads(row["details"] or "{}")
        except (TypeError, ValueError):
            item["details_data"] = {}
        result.append(item)
    return result


def delete_order(order_id, expected_order_no, confirmation, backup_dir, operator_name="admin"):
    if confirmation != "刪除":
        raise ValueError("請輸入「刪除」確認")
    db = get_db()
    order = get_order(order_id)
    if not order or order["order_no"] != (expected_order_no or "").strip():
        raise ValueError("訂單不存在或訂單號碼已變更")
    if order["status"] != "pending":
        raise ValueError("已開始或已完成的訂單不能刪除；請保留歷史紀錄")

    backup = backup_database(backup_dir)
    removed = {"scan_events": 0, "barcodes": 0, "order": 0}
    try:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT order_no,status FROM inspection_order WHERE id=?", (order_id,)).fetchone()
        if not current or current["order_no"] != order["order_no"]:
            raise ValueError("訂單不存在或訂單號碼已變更")
        if current["status"] != "pending":
            raise ValueError("已開始或已完成的訂單不能刪除；請保留歷史紀錄")
        removed["scan_events"] = db.execute("SELECT COUNT(*) FROM inspection_scan_event WHERE order_id=?", (order_id,)).fetchone()[0]
        removed["barcodes"] = db.execute("SELECT COUNT(*) FROM inspection_barcode_record WHERE order_id=?", (order_id,)).fetchone()[0]
        db.execute("DELETE FROM inspection_scan_event WHERE order_id=?", (order_id,))
        db.execute("DELETE FROM inspection_manual_pause WHERE order_id=?", (order_id,))
        db.execute("DELETE FROM inspection_material_pause WHERE order_id=?", (order_id,))
        db.execute("DELETE FROM inspection_barcode_record WHERE order_id=?", (order_id,))
        removed["order"] = db.execute("DELETE FROM inspection_order WHERE id=?", (order_id,)).rowcount
        _inspection_audit("delete", "inspection_order", order_id, {"order_no": order["order_no"], "removed": removed, "backup": str(backup)}, operator_name)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return backup, removed


def list_report_workers():
    rows = get_db().execute("SELECT employee_no,name FROM inspection_worker UNION SELECT worker_no_snapshot,worker_name_snapshot FROM inspection_order WHERE worker_no_snapshot IS NOT NULL ORDER BY employee_no").fetchall()
    return rows


def _report_pause_intervals(order_ids):
    """Return all material/manual pause intervals grouped by order ID."""
    if not order_ids:
        return {}
    placeholders = ",".join("?" for _ in order_ids)
    rows = get_db().execute(
        f"""
        SELECT order_id, paused_at, resumed_at
        FROM inspection_material_pause
        WHERE order_id IN ({placeholders})
        UNION ALL
        SELECT order_id, paused_at, resumed_at
        FROM inspection_manual_pause
        WHERE order_id IN ({placeholders})
        ORDER BY order_id, paused_at
        """,
        [*order_ids, *order_ids],
    ).fetchall()
    intervals = {order_id: [] for order_id in order_ids}
    for row in rows:
        intervals[row["order_id"]].append(dict(row))
    return intervals


def query_report(filters):
    clauses, params = [], []
    status_group = (filters.get("status_group") or "").strip()
    if status_group == "incomplete":
        clauses.append("o.status IN ('pending','started')")
    elif status_group == "material_paused":
        clauses.append("o.status='started' AND EXISTS(SELECT 1 FROM inspection_material_pause active_pause WHERE active_pause.order_id=o.id AND active_pause.resumed_at IS NULL)")
    elif status_group == "manual_paused":
        clauses.append("o.status='started' AND EXISTS(SELECT 1 FROM inspection_manual_pause active_pause WHERE active_pause.order_id=o.id AND active_pause.resumed_at IS NULL)")
    elif status_group == "started":
        clauses.append("o.status='started' AND NOT EXISTS(SELECT 1 FROM inspection_material_pause active_pause WHERE active_pause.order_id=o.id AND active_pause.resumed_at IS NULL) AND NOT EXISTS(SELECT 1 FROM inspection_manual_pause active_pause WHERE active_pause.order_id=o.id AND active_pause.resumed_at IS NULL)")
    elif status_group in INSPECTION_STATUS_LABELS:
        clauses.append("o.status=?")
        params.append(status_group)
    mapping = {"order_no": "o.order_no", "model_name": "o.model_name_snapshot", "machine_serial": "o.machine_serial", "customer_name": "o.customer_name", "status": "o.status"}
    mapping["axis_type"] = "o.axis_type_snapshot"
    for key, column in mapping.items():
        value = (filters.get(key) or "").strip()
        if value:
            clauses.append(f"{column} LIKE ?")
            params.append(f"%{value}%")
    worker_name = (filters.get("worker_name") or "").strip()
    if worker_name:
        clauses.append("o.worker_name_snapshot=?")
        params.append(worker_name)
    sql = """
        SELECT o.*,
               EXISTS(SELECT 1 FROM inspection_material_pause active_pause WHERE active_pause.order_id=o.id AND active_pause.resumed_at IS NULL) AS material_paused,
               EXISTS(SELECT 1 FROM inspection_manual_pause active_pause WHERE active_pause.order_id=o.id AND active_pause.resumed_at IS NULL) AS manual_paused,
               CAST(ROUND(COALESCE((
                   SELECT SUM((julianday(COALESCE(p.resumed_at, ?)) - julianday(p.paused_at)) * 86400.0)
                   FROM inspection_material_pause p WHERE p.order_id=o.id
               ), 0)) AS INTEGER) AS material_pause_seconds
               ,CAST(ROUND(COALESCE((
                   SELECT SUM((julianday(COALESCE(p.resumed_at, ?)) - julianday(p.paused_at)) * 86400.0)
                   FROM inspection_manual_pause p WHERE p.order_id=o.id
               ), 0)) AS INTEGER) AS manual_pause_seconds
        FROM inspection_order o
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sort_by = (filters.get("sort_by") or "created_desc").strip()
    sort_orders = {
        "created_desc": "o.created_at DESC, o.id DESC",
        "order_no_desc": "o.order_no DESC, o.id DESC",
        "expected_ship_date_asc": "CASE WHEN o.expected_ship_date IS NULL OR o.expected_ship_date='' THEN 1 ELSE 0 END, o.expected_ship_date ASC, o.id DESC",
        "expected_ship_date_desc": "CASE WHEN o.expected_ship_date IS NULL OR o.expected_ship_date='' THEN 1 ELSE 0 END, o.expected_ship_date DESC, o.id DESC",
    }
    rows = get_db().execute(sql + " ORDER BY " + sort_orders.get(sort_by, sort_orders["created_desc"]), [utc_now(), utc_now(), *params]).fetchall()
    intervals_by_order = _report_pause_intervals([row["id"] for row in rows])
    result = []
    for row in rows:
        item = dict(row)
        item["pause_intervals"] = intervals_by_order.get(row["id"], [])
        result.append(item)
    return result


def summarize_report_by_worker(rows):
    summary = {}
    for row in rows:
        key = row["worker_name_snapshot"] or "未認領"
        item = summary.setdefault(key, {"worker_name": key, "total": 0, "completed": 0, "started": 0, "material_paused": 0, "manual_paused": 0, "pending": 0, "cancelled": 0})
        item["total"] += 1
        if row["status"] == "started" and row["material_paused"]:
            item["material_paused"] += 1
        elif row["status"] == "started" and row["manual_paused"]:
            item["manual_paused"] += 1
        else:
            item[row["status"]] += 1
    return sorted(summary.values(), key=lambda item: item["worker_name"])


def summarize_report_by_worker_axis_model(rows):
    """Summarize non-cancelled inspection rows into one worksheet per worker."""
    summaries = {}
    for row in rows:
        if row["status"] == "cancelled":
            continue
        worker_name = row["worker_name_snapshot"] or "未認領"
        item = summaries.setdefault(
            worker_name,
            {
                "worker_name": worker_name,
                "axis_models": {
                    "4": {"total": 0, "models": {}},
                    "5": {"total": 0, "models": {}},
                },
            },
        )
        axis_type = "5" if str(row["axis_type_snapshot"] or "4") == "5" else "4"
        axis_summary = item["axis_models"][axis_type]
        axis_summary["total"] += 1
        model_name = (row["model_name_snapshot"] or "-").strip() or "-"
        axis_summary["models"][model_name] = axis_summary["models"].get(model_name, 0) + 1

    result = []
    for item in summaries.values():
        result.append(
            {
                "worker_name": item["worker_name"],
                "axis_models": {
                    axis_type: {
                        "total": item["axis_models"][axis_type]["total"],
                        "models": sorted(item["axis_models"][axis_type]["models"].items(), key=lambda pair: pair[0].casefold()),
                    }
                    for axis_type in ("4", "5")
                },
            }
        )
    return sorted(result, key=lambda item: item["worker_name"])


def summarize_report_by_axis(rows):
    summary = {}
    for row in rows:
        axis_type = str(row["axis_type_snapshot"] or "4")
        item = summary.setdefault(axis_type, {"axis_type": axis_type, "axis_label": axis_type_label(axis_type), "total": 0, "completed": 0, "started": 0, "material_paused": 0, "manual_paused": 0, "pending": 0, "cancelled": 0})
        item["total"] += 1
        if row["status"] == "started" and row["material_paused"]:
            item["material_paused"] += 1
        elif row["status"] == "started" and row["manual_paused"]:
            item["manual_paused"] += 1
        else:
            item[row["status"]] += 1
    return [summary.get(axis_type, {"axis_type": axis_type, "axis_label": axis_type_label(axis_type), "total": 0, "completed": 0, "started": 0, "material_paused": 0, "manual_paused": 0, "pending": 0, "cancelled": 0}) for axis_type in ("4", "5")]


def export_xlsx(rows, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    def excel_value(value):
        if not isinstance(value, str):
            return value
        return "'" + value if value[:1] in {"=", "+", "-", "@"} else value

    workbook = Workbook()
    detail = workbook.active
    detail.title = "訂單明細"
    used_sheet_names = {detail.title.casefold(), "軸別統計".casefold()}

    def worksheet_name(worker_name):
        base_name = re.sub(r'[:\\/?*\[\]]', " ", worker_name).strip() or "未認領"
        base_name = base_name[:31]
        candidate = base_name
        suffix_number = 2
        while candidate.casefold() in used_sheet_names:
            suffix = f" ({suffix_number})"
            candidate = base_name[:31 - len(suffix)] + suffix
            suffix_number += 1
        used_sheet_names.add(candidate.casefold())
        return candidate

    header_fill = PatternFill("solid", fgColor="1F4E78")
    axis_fills = {"4": PatternFill("solid", fgColor="E8F1FA"), "5": PatternFill("solid", fgColor="E7F4F2")}
    for index, item in enumerate(summarize_report_by_worker_axis_model(rows)):
        sheet = workbook.create_sheet(worksheet_name(item["worker_name"]), index)
        sheet.append(["校機人員", excel_value(item["worker_name"])])
        sheet.append(["四軸總台數", item["axis_models"]["4"]["total"], "五軸總台數", item["axis_models"]["5"]["total"]])
        sheet.append([])
        sheet.append(["軸別", "機型", "台數"])
        for cell in sheet[4]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
        for axis_type in ("4", "5"):
            axis_label = axis_type_label(axis_type)
            axis_summary = item["axis_models"][axis_type]
            for model_name, count in axis_summary["models"]:
                sheet.append([axis_label, excel_value(model_name), count])
                for cell in sheet[sheet.max_row]:
                    cell.fill = axis_fills[axis_type]
            sheet.append([f"{axis_label}小計", "", axis_summary["total"]])
            for cell in sheet[sheet.max_row]:
                cell.font = Font(bold=True)
                cell.fill = axis_fills[axis_type]
        sheet.freeze_panes = "A5"

    headers = ["訂單號碼", "機型", "軸別", "機台製號", "客戶", "預計出貨日", "校機人員", "建立時間", "狀態", "開始日期", "開始時間", "結束日期", "結束時間", "總經過時間", "缺料時間", "人工暫停時間", "實際校機工時", "重新檢驗原因"]
    detail.append(headers)
    for cell in detail[1]:
        cell.font = Font(bold=True)

    for row in rows:
        displayed_status = "缺料暫停" if row["material_paused"] else ("人工暫停" if row["manual_paused"] else status_label(row["status"]))
        detail.append([excel_value(value) for value in [row["order_no"], row["model_name_snapshot"], axis_type_label(row["axis_type_snapshot"]), row["machine_serial"], row["customer_name"], row["expected_ship_date"] or "", row["worker_name_snapshot"] or "", format_datetime(row["created_at"]), displayed_status, format_date(row["started_at"]), format_time(row["started_at"]), format_date(row["finished_at"]), format_time(row["finished_at"]), format_duration(row["started_at"], row["finished_at"], row["status"]), format_seconds(row["material_pause_seconds"]), format_seconds(row["manual_pause_seconds"]), format_work_duration(row["started_at"], row["finished_at"], row["pause_intervals"]), row["reinspection_reason"]]])
    axis_sheet = workbook.create_sheet("軸別統計")
    axis_sheet.append(["軸別", "訂單數", "已完成", "校機中", "缺料暫停", "待校機", "已取消"])
    for cell in axis_sheet[1]:
        cell.font = Font(bold=True)
    for item in summarize_report_by_axis(rows):
        axis_sheet.append([item["axis_label"], item["total"], item["completed"], item["started"], item["material_paused"], item["pending"], item["cancelled"]])
    for sheet in workbook.worksheets:
        if sheet.freeze_panes is None:
            sheet.freeze_panes = "A2"
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(max(max(len(str(cell.value or "")) for cell in column) + 2, 12), 28)
    workbook.save(path)


def debug_clear_counts():
    db = get_db()
    return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("inspection_order", "inspection_barcode_record", "inspection_scan_event", "inspection_material_pause", "inspection_manual_pause", "inspection_worker", "inspection_model_master")}


def clear_debug_data(selected, confirmation, backup_dir, operator_name="admin"):
    if confirmation != "清除":
        raise ValueError("請輸入「清除」確認")
    selected = set(selected or [])
    unknown = selected - set(DEBUG_CLEAR_CHOICES)
    if unknown:
        raise ValueError("包含不支援的清除項目")
    db = get_db()
    if "workers" in selected and db.execute("SELECT 1 FROM inspection_order WHERE worker_id IS NOT NULL LIMIT 1").fetchone():
        raise ValueError("已有歷史訂單綁定校機人員，不能清除人員")
    if "models" in selected and db.execute("SELECT 1 FROM inspection_order LIMIT 1").fetchone():
        raise ValueError("已有訂單資料，不能清除機型")
    backup = backup_database(backup_dir)
    removed = {"inspection_order": 0, "inspection_barcode_record": 0, "inspection_scan_event": 0, "inspection_material_pause": 0, "inspection_manual_pause": 0, "inspection_worker": 0, "inspection_model_master": 0}
    if "orders" in selected:
        for table in ("inspection_scan_event", "inspection_manual_pause", "inspection_material_pause", "inspection_barcode_record", "inspection_order"):
            removed[table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            db.execute(f"DELETE FROM {table}")
    if "scans" in selected:
        removed["inspection_scan_event"] = db.execute("SELECT COUNT(*) FROM inspection_scan_event").fetchone()[0]
        db.execute("DELETE FROM inspection_scan_event")
    if "workers" in selected:
        removed["inspection_worker"] = db.execute("SELECT COUNT(*) FROM inspection_worker").fetchone()[0]
        db.execute("DELETE FROM inspection_worker")
    if "models" in selected:
        removed["inspection_model_master"] = db.execute("SELECT COUNT(*) FROM inspection_model_master").fetchone()[0]
        db.execute("DELETE FROM inspection_model_master")
    _inspection_audit("debug_clear", "database", None, {"selected": sorted(selected), "backup": str(backup)}, operator_name)
    db.commit()
    return backup, removed


def backup_database(target_dir):
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    source = Path(__import__("flask").current_app.config["DATABASE"])
    target = target_dir / f"inspection_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{_inspection_id()[:8]}.db"
    source_db = sqlite3.connect(source)
    target_db = sqlite3.connect(target)
    try:
        source_db.backup(target_db)
    finally:
        target_db.close()
        source_db.close()
    return verify_sqlite_backup(target)


def verify_sqlite_backup(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("備份檔不存在或為空檔")
    db = sqlite3.connect(path)
    try:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        db.close()
    if integrity != "ok":
        raise ValueError("備份檔 integrity_check 失敗")
    return path


def restore_database(source_path, target_path, backup_dir, operator_name="admin"):
    source_path = verify_sqlite_backup(source_path)
    safety = backup_database(backup_dir)
    target_path = Path(target_path)
    temp = target_path.with_suffix(target_path.suffix + ".restore.tmp")
    shutil.copy2(source_path, temp)
    verify_sqlite_backup(temp)
    shutil.copy2(temp, target_path)
    temp.unlink(missing_ok=True)
    _inspection_audit("restore", "database", str(target_path), {"source": str(source_path), "safety_backup": str(safety)}, operator_name)
    get_db().commit()
    return safety

