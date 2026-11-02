import ctypes
import sys
import threading
import time
import webbrowser

from config import Config
from . import create_app
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


def main(single_instance=None):
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

