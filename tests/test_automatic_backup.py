import logging
import sqlite3
from datetime import datetime

import app as app_package
import pytest

from app.automatic_backup import TAIPEI_TZ, create_sqlite_backup, run_daily_auto_backup, verify_sqlite_file


def create_database(path, value="first"):
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE sample(value TEXT NOT NULL)")
        database.execute("INSERT INTO sample(value) VALUES (?)", (value,))


def test_daily_backup_uses_sqlite_api_and_runs_only_once_per_taipei_day(tmp_path):
    database_path = tmp_path / "inspection.db"
    backup_directory = tmp_path / "backups"
    create_database(database_path)

    first, created = run_daily_auto_backup(
        database_path,
        backup_directory,
        now=datetime(2026, 11, 2, 8, 30, tzinfo=TAIPEI_TZ),
    )
    second, second_created = run_daily_auto_backup(
        database_path,
        backup_directory,
        now=datetime(2026, 11, 2, 23, 59, tzinfo=TAIPEI_TZ),
    )

    assert created is True
    assert second_created is False
    assert second == first
    assert first.name.startswith("inspection_auto_20261102_")
    assert verify_sqlite_file(first) == first
    with sqlite3.connect(first) as backup:
        assert backup.execute("SELECT value FROM sample").fetchone()[0] == "first"


def test_daily_backup_retains_only_automatic_backups(tmp_path):
    database_path = tmp_path / "inspection.db"
    backup_directory = tmp_path / "backups"
    create_database(database_path)
    manual_backup = backup_directory / "inspection_backup_20261001_120000_manual.db"
    backup_directory.mkdir()
    create_database(manual_backup, "manual")

    for day in range(1, 5):
        run_daily_auto_backup(
            database_path,
            backup_directory,
            retention=3,
            now=datetime(2026, 11, day, 8, 0, tzinfo=TAIPEI_TZ),
        )

    automatic = sorted(backup_directory.glob("inspection_auto_*.db"))
    assert len(automatic) == 3
    assert automatic[0].name.startswith("inspection_auto_20261102_")
    assert manual_backup.is_file()


def test_corrupt_backup_does_not_suppress_a_valid_same_day_backup(tmp_path):
    database_path = tmp_path / "inspection.db"
    backup_directory = tmp_path / "backups"
    create_database(database_path)
    backup_directory.mkdir()
    corrupt = backup_directory / "inspection_auto_20261102_070000_deadbeef.db"
    corrupt.write_bytes(b"not sqlite")

    backup, created = run_daily_auto_backup(
        database_path,
        backup_directory,
        now=datetime(2026, 11, 2, 8, 0, tzinfo=TAIPEI_TZ),
    )

    assert created is True
    assert backup != corrupt
    assert verify_sqlite_file(backup) == backup


def test_failed_sqlite_backup_removes_temporary_file(tmp_path):
    corrupt_database = tmp_path / "inspection.db"
    target = tmp_path / "backups" / "inspection_auto_20261102_080000_deadbeef.db"
    corrupt_database.write_bytes(b"not sqlite")

    with pytest.raises(sqlite3.DatabaseError):
        create_sqlite_backup(corrupt_database, target)

    assert not target.is_file()
    assert not target.with_suffix(".db.tmp").exists()


def test_application_startup_creates_at_most_one_daily_backup(tmp_path):
    config = {
        "TESTING": True,
        "DATABASE": str(tmp_path / "inspection.db"),
        "BACKUP_DIRECTORY": str(tmp_path / "backups"),
        "SECRET_KEY": "test",
        "AUTO_BACKUP_ENABLED": True,
    }

    app_package.create_app(config)
    first_backups = list((tmp_path / "backups").glob("inspection_auto_*.db"))
    app_package.create_app(config)
    second_backups = list((tmp_path / "backups").glob("inspection_auto_*.db"))

    assert len(first_backups) == 1
    assert second_backups == first_backups


def test_automatic_backup_failure_is_logged_without_blocking_startup(tmp_path, monkeypatch, caplog):
    def fail_backup(*args, **kwargs):
        raise OSError("backup disk unavailable")

    monkeypatch.setattr(app_package, "run_daily_auto_backup", fail_backup)
    caplog.set_level(logging.ERROR)

    application = app_package.create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "inspection.db"),
            "BACKUP_DIRECTORY": str(tmp_path / "backups"),
            "SECRET_KEY": "test",
            "AUTO_BACKUP_ENABLED": True,
        }
    )

    assert application is not None
    assert "automatic database backup failed; application startup will continue" in caplog.text

