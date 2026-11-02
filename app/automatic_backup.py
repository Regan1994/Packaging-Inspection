import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


TAIPEI_TZ = timezone(timedelta(hours=8), name="Asia/Taipei")
AUTO_BACKUP_RE = re.compile(r"^inspection_auto_(\d{8})_(\d{6})_[0-9a-f]{8}\.db$")


def verify_sqlite_file(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("備份檔不存在或為空檔")
    database = sqlite3.connect(path)
    try:
        integrity = database.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        database.close()
    if integrity != "ok":
        raise ValueError("備份檔 integrity_check 失敗")
    return path


def create_sqlite_backup(source_path, target_path):
    source_path = Path(source_path)
    target_path = Path(target_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"找不到資料庫：{source_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path.with_suffix(target_path.suffix + ".tmp")
    temporary_path.unlink(missing_ok=True)
    try:
        source = sqlite3.connect(source_path)
        try:
            target = sqlite3.connect(temporary_path)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        verify_sqlite_file(temporary_path)
        temporary_path.replace(target_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return target_path


def _automatic_backups(backup_directory):
    return sorted(
        path
        for path in Path(backup_directory).iterdir()
        if path.is_file() and AUTO_BACKUP_RE.fullmatch(path.name)
    )


def _prune_automatic_backups(backup_directory, retention):
    backups = _automatic_backups(backup_directory)
    for path in backups[:-retention]:
        path.unlink()


def run_daily_auto_backup(database_path, backup_directory, retention=30, now=None):
    if retention < 1:
        raise ValueError("自動備份保留數量必須至少為 1")

    backup_directory = Path(backup_directory)
    backup_directory.mkdir(parents=True, exist_ok=True)
    if now and now.tzinfo is None:
        raise ValueError("自動備份時間必須包含時區")
    local_now = now.astimezone(TAIPEI_TZ) if now else datetime.now(TAIPEI_TZ)
    date_token = local_now.strftime("%Y%m%d")
    todays_backups = [
        path for path in _automatic_backups(backup_directory) if path.name.startswith(f"inspection_auto_{date_token}_")
    ]
    for path in reversed(todays_backups):
        try:
            verify_sqlite_file(path)
        except (OSError, sqlite3.Error, ValueError):
            continue
        _prune_automatic_backups(backup_directory, retention)
        return path, False

    filename = f"inspection_auto_{local_now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.db"
    target = create_sqlite_backup(database_path, backup_directory / filename)
    _prune_automatic_backups(backup_directory, retention)
    return target, True

