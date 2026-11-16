"""Windows DPAPI-protected persistence for the embedded browser login session."""

from __future__ import annotations

from ctypes import POINTER, Structure, byref, c_byte, c_void_p, cast
from ctypes import wintypes
from pathlib import Path
import ctypes
import json
import os


class _DataBlob(Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", POINTER(c_byte))]


def _session_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    folder = base / "EchoPlay"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "bilibili-session.dpapi"


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("安全保存登录状态目前仅支持 Windows。")
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), cast(buffer, POINTER(c_byte)))
    encrypted = _DataBlob()
    crypt32 = ctypes.WinDLL("Crypt32", use_last_error=True)
    protect = crypt32.CryptProtectData
    protect.argtypes = [POINTER(_DataBlob), wintypes.LPCWSTR, POINTER(_DataBlob), c_void_p,
                        c_void_p, wintypes.DWORD, POINTER(_DataBlob)]
    protect.restype = wintypes.BOOL
    if not protect(byref(source), "EchoPlay Bilibili session", None, None, None, 1, byref(encrypted)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(encrypted.pbData, encrypted.cbData)
    finally:
        _local_free(encrypted.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("安全读取登录状态目前仅支持 Windows。")
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), cast(buffer, POINTER(c_byte)))
    decrypted = _DataBlob()
    description = c_void_p()
    crypt32 = ctypes.WinDLL("Crypt32", use_last_error=True)
    unprotect = crypt32.CryptUnprotectData
    unprotect.argtypes = [POINTER(_DataBlob), POINTER(c_void_p), POINTER(_DataBlob), c_void_p,
                          c_void_p, wintypes.DWORD, POINTER(_DataBlob)]
    unprotect.restype = wintypes.BOOL
    if not unprotect(byref(source), byref(description), None, None, None, 1, byref(decrypted)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(decrypted.pbData, decrypted.cbData)
    finally:
        _local_free(decrypted.pbData)
        if description:
            _local_free(description)


def _local_free(pointer) -> None:
    kernel32 = ctypes.WinDLL("Kernel32", use_last_error=True)
    local_free = kernel32.LocalFree
    local_free.argtypes = [c_void_p]
    local_free.restype = c_void_p
    local_free(cast(pointer, c_void_p))


def save_session(cookies: dict[str, str]) -> None:
    required = {"SESSDATA", "bili_jct"}
    if not required.issubset(cookies):
        raise RuntimeError("登录成功但 B 站未返回完整会话 Cookie，请重新扫码登录。")
    payload = json.dumps(cookies, ensure_ascii=False).encode("utf-8")
    _session_path().write_bytes(_dpapi_protect(payload))


def load_session() -> dict[str, str]:
    path = _session_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(_dpapi_unprotect(path.read_bytes()).decode("utf-8"))
        return {str(key): str(item) for key, item in value.items()}
    except Exception:
        return {}


def clear_session() -> None:
    try:
        _session_path().unlink(missing_ok=True)
    except OSError:
        pass


