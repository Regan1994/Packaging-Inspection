import subprocess
import sys
import uuid

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


def test_primary_instance_releases_mutex_after_server_stops(monkeypatch):
    lock = FakeSingleInstance(True)
    run_arguments = {}

    class FakeApp:
        def run(self, **kwargs):
            run_arguments.update(kwargs)

    monkeypatch.delattr(launcher.sys, "frozen", raising=False)
    monkeypatch.setattr(launcher, "create_app", FakeApp)

    assert launcher.main(lock) == 0
    assert run_arguments == {"host": launcher.Config.HOST, "port": launcher.Config.PORT, "debug": False, "use_reloader": False}
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

