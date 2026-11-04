import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app import launcher
from app import single_instance


class FakeSingleInstance:
    def __init__(self, acquired):
        self.acquired = acquired
        self.release_calls = 0

    def acquire(self):
        return self.acquired

    def release(self):
        self.release_calls += 1


def test_second_instance_opens_existing_system_without_creating_app(monkeypatch):
    lock = FakeSingleInstance(False)
    events = []
    monkeypatch.setattr(launcher, "open_browser", lambda delay=0: events.append(("browser", delay)))
    monkeypatch.setattr(launcher, "show_already_running", lambda: events.append(("notice", 0)))
    monkeypatch.setattr(launcher, "create_app", lambda: pytest.fail("duplicate launch must not create the app"))

    assert launcher.main(lock) == 0
    assert events == [("browser", 0), ("notice", 0)]
    assert lock.release_calls == 0


def test_backup_command_does_not_acquire_application_mutex(monkeypatch, capsys):
    lock = FakeSingleInstance(False)
    events = []

    class FakeContext:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    class FakeApp:
        config = {
            "DATABASE": "C:/data/inspection.db",
            "BACKUP_DIRECTORY": "C:/data/backups",
            "AUTO_BACKUP_RETENTION": 30,
        }
        logger = type("Logger", (), {"info": lambda self, *args: events.append(args)})()

        def app_context(self):
            return FakeContext()

    monkeypatch.setattr(launcher, "create_app", lambda config=None: FakeApp())
    monkeypatch.setattr(
        launcher,
        "run_daily_auto_backup",
        lambda database, backup_directory, retention: (Path("C:/data/backups/inspection_auto_test.db"), True),
    )

    assert launcher.main(lock, ["--backup"]) == 0
    assert lock.release_calls == 0
    assert "automatic database backup created" in capsys.readouterr().out
    assert events


def test_verify_backup_command_reports_success(monkeypatch, capsys):
    verified = []
    monkeypatch.setattr(launcher, "verify_sqlite_file", lambda path: verified.append(path) or path)

    assert launcher.main(argv=["--verify-backup", "C:/data/backup.db"]) == 0
    assert verified == [Path("C:/data/backup.db")]
    assert "backup verification passed" in capsys.readouterr().out


def test_primary_instance_releases_mutex_after_server_stops(monkeypatch):
    lock = FakeSingleInstance(True)
    run_arguments = {}

    class FakeApp:
        pass

    monkeypatch.delattr(launcher.sys, "frozen", raising=False)
    monkeypatch.setattr(launcher, "create_app", FakeApp)
    monkeypatch.setattr(launcher, "serve", lambda application, **kwargs: run_arguments.update(kwargs))

    assert launcher.main(lock) == 0
    assert run_arguments == {"host": launcher.Config.HOST, "port": launcher.Config.PORT, "threads": launcher.Config.WAITRESS_THREADS}
    assert lock.release_calls == 1


def test_primary_instance_releases_mutex_when_startup_fails(monkeypatch):
    lock = FakeSingleInstance(True)
    monkeypatch.setattr(launcher, "create_app", lambda: (_ for _ in ()).throw(RuntimeError("startup failed")))

    with pytest.raises(RuntimeError, match="startup failed"):
        launcher.main(lock)

    assert lock.release_calls == 1


def test_windows_mutex_rejects_second_process_and_closes_duplicate_handle(monkeypatch):
    handles_closed = []
    monkeypatch.setattr(single_instance.sys, "platform", "win32")
    monkeypatch.setattr(single_instance, "_create_windows_mutex", lambda name: (456, True))
    monkeypatch.setattr(single_instance, "_close_windows_handle", handles_closed.append)

    lock = single_instance.SingleInstance()

    assert lock.acquire() is False
    assert handles_closed == [456]


def test_windows_mutex_is_released(monkeypatch):
    handles_closed = []
    monkeypatch.setattr(single_instance.sys, "platform", "win32")
    monkeypatch.setattr(single_instance, "_create_windows_mutex", lambda name: (123, False))
    monkeypatch.setattr(single_instance, "_close_windows_handle", handles_closed.append)

    lock = single_instance.SingleInstance()

    assert lock.acquire() is True
    lock.release()
    assert handles_closed == [123]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named Mutex integration test")
def test_windows_mutex_blocks_a_second_process():
    mutex_name = f"Global\\TJRInspectionReporting.Test.{uuid.uuid4().hex}"
    child_code = (
        "import sys\n"
        "from app.single_instance import SingleInstance\n"
        f"lock = SingleInstance({mutex_name!r})\n"
        "print(lock.acquire(), flush=True)\n"
        "sys.stdin.read(1)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "True"
        assert single_instance.SingleInstance(mutex_name).acquire() is False
    finally:
        child.stdin.write("x")
        child.stdin.flush()
        child.communicate(timeout=5)

