"""Window-specific Windows Shell identity; never changes the title-bar icon."""
import ctypes
import uuid
from ctypes import wintypes


class _Guid(ctypes.Structure):
    _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _Guid), ("pid", wintypes.DWORD)]


class _CountedPointer(ctypes.Structure):
    _fields_ = [("count", wintypes.ULONG), ("pointer", ctypes.c_void_p)]


class _Value(ctypes.Union):
    _fields_ = [("text", ctypes.c_wchar_p), ("array", _CountedPointer)]


class _Variant(ctypes.Structure):
    _fields_ = [("vt", wintypes.WORD), ("reserved", wintypes.WORD * 3), ("value", _Value)]


def _guid(value):
    return _Guid.from_buffer_copy(uuid.UUID(value).bytes_le)


def _key(pid):
    return _PropertyKey(_guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid)


def _check(result):
    if result < 0:
        raise OSError(f"Windows taskbar property error: 0x{result & 0xffffffff:08X}")


def _store(window_handle):
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    getter = shell.SHGetPropertyStoreForWindow
    getter.argtypes = [wintypes.HWND, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
    getter.restype = ctypes.c_long
    store = ctypes.c_void_p()
    iid = _guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")
    _check(getter(window_handle, ctypes.byref(iid), ctypes.byref(store)))
    return store


def _method(store, index, result, *arguments):
    table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[index])


def set_taskbar_identity(window_handle, icon_path, app_id, command):
    store = _store(window_handle)
    try:
        setter = _method(store, 6, ctypes.c_long,
                         ctypes.POINTER(_PropertyKey), ctypes.POINTER(_Variant))
        # Relaunch metadata must be set before the AppUserModelID.
        for pid, text in ((2, command), (3, f"{icon_path},0"), (4, "EchoPlay"), (5, app_id)):
            buffer = ctypes.create_unicode_buffer(text)
            value = _Variant()
            value.vt = 31  # VT_LPWSTR. SetValue copies this caller-owned buffer.
            value.value.text = ctypes.cast(buffer, ctypes.c_wchar_p)
            key = _key(pid)
            _check(setter(store, ctypes.byref(key), ctypes.byref(value)))
        _check(_method(store, 7, ctypes.c_long)(store))
    finally:
        _method(store, 2, wintypes.ULONG)(store)


def taskbar_identity(window_handle):
    """Read back Shell properties for diagnostics and native integration tests."""
    store = _store(window_handle)
    ole = ctypes.WinDLL("ole32")
    ole.PropVariantClear.argtypes = [ctypes.POINTER(_Variant)]
    ole.PropVariantClear.restype = ctypes.c_long
    try:
        getter = _method(store, 5, ctypes.c_long,
                         ctypes.POINTER(_PropertyKey), ctypes.POINTER(_Variant))
        result = {}
        for name, pid in (("command", 2), ("icon", 3), ("name", 4), ("id", 5)):
            value = _Variant()
            key = _key(pid)
            _check(getter(store, ctypes.byref(key), ctypes.byref(value)))
            try:
                result[name] = value.value.text if value.vt == 31 else None
            finally:
                ole.PropVariantClear(ctypes.byref(value))
        return result
    finally:
        _method(store, 2, wintypes.ULONG)(store)
