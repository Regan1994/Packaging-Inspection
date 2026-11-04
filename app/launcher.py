import ctypes
import sys
import threading
import time
import webbrowser
from pathlib import Path

from config import Config
from . import create_app
from .automatic_backup import run_daily_auto_backup, verify_sqlite_file
from .single_instance import SingleInstance


def system_url():
    return f"http://{Config.BROWSER_HOST}:{Config.PORT}"


def open_browser(delay=0):
    if delay:
        time.sleep(delay)
    webbrowser.open(system_url())


def show_already_running():
    message = "TJR校機報工系統已在執行，將開啟現有系統頁面。"
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "TJR校機報工系統", 0x40)
    else:
        print(message)


def run_backup_command():
    application = create_app({"AUTO_BACKUP_ENABLED": False})
    with application.app_context():
        backup_path, created = run_daily_auto_backup(
            application.config["DATABASE"],
            application.config["BACKUP_DIRECTORY"],
            retention=application.config["AUTO_BACKUP_RETENTION"],
        )
        message = "automatic database backup created" if created else "automatic database backup already exists"
        application.logger.info("%s: %s", message, backup_path)
        print(f"{message}: {backup_path}")
    return 0


def run_verify_backup_command(path):
    verified = verify_sqlite_file(Path(path))
    print(f"backup verification passed: {verified}")
    return 0


def main(single_instance=None, argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--backup" in argv:
        return run_backup_command()
    if "--verify-backup" in argv:
        index = argv.index("--verify-backup")
        if index + 1 >= len(argv) or not argv[index + 1].strip():
            raise SystemExit("--verify-backup 需要指定備份檔路徑")
        return run_verify_backup_command(argv[index + 1])

    instance = single_instance or SingleInstance()
    if not instance.acquire():
        open_browser()
        show_already_running()
        return 0

    try:
        application = create_app()
        if getattr(sys, "frozen", False):
            threading.Thread(target=open_browser, kwargs={"delay": 1}, daemon=True).start()
        application.run(host=Config.HOST, port=Config.PORT, debug=False, use_reloader=False)
        return 0
    finally:
        instance.release()

