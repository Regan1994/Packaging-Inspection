import ctypes
import sys


ERROR_ALREADY_EXISTS = 183


def _create_windows_mutex(name):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    create_mutex.restype = ctypes.c_void_p

    ctypes.set_last_error(0)
    handle = create_mutex(None, False, name)
    error_code = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error_code)
    return handle, error_code == ERROR_ALREADY_EXISTS


def _close_windows_handle(handle):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_bool
    close_handle(handle)


class SingleInstance:
    """Keep one server process per interactive Windows session."""

    def __init__(self, name="Global\\TJRInspectionReporting.MainServer"):
        self.name = name
        self._handle = None

    def acquire(self):
        if self._handle is not None:
            return True
        if sys.platform != "win32":
            return True

        handle, already_exists = _create_windows_mutex(self.name)
        if already_exists:
            _close_windows_handle(handle)
            return False

        self._handle = handle
        return True

    def release(self):
        if self._handle is not None:
            _close_windows_handle(self._handle)
            self._handle = None

