# -*- coding: utf-8 -*-
"""本地密钥安全存储。

Windows 优先使用 DPAPI（当前用户范围）。非 Windows 开发环境使用带完整性校验
的本地派生密钥，仅用于测试和离线开发；生产 Windows EXE 使用 DPAPI。
"""

import base64
import getpass
import hashlib
import hmac
import json
import os
import platform
import secrets
import uuid


def _path(name, root):
    safe = "".join(ch for ch in str(name) if ch.isalnum() or ch in "_-.") or "secret"
    return os.path.join(root, safe + ".secret")


def _dpapi_protect(value):
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    raw = value.encode("utf-8")
    buf = ctypes.create_string_buffer(raw)
    blob = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
        return None
    try:
        return b"DPAPI1:" + ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _dpapi_unprotect(data):
    if os.name != "nt" or not data.startswith(b"DPAPI1:"):
        return None
    import ctypes
    from ctypes import wintypes
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    raw = data[len(b"DPAPI1:"):]
    buf = ctypes.create_string_buffer(raw)
    blob = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _fallback_key(root):
    seed = f"{getpass.getuser()}|{platform.node()}|{uuid.getnode()}|{os.path.abspath(root)}".encode()
    return hashlib.sha256(seed).digest()


def _fallback_protect(value, root):
    key = _fallback_key(root)
    raw = value.encode("utf-8")
    nonce = secrets.token_bytes(16)
    encrypted = bytes(byte ^ key[index % len(key)] ^ nonce[index % len(nonce)] for index, byte in enumerate(raw))
    mac = hmac.new(key, nonce + encrypted, hashlib.sha256).digest()
    return b"LOCAL1:" + base64.urlsafe_b64encode(nonce + mac + encrypted)


def _fallback_unprotect(data, root):
    if not data.startswith(b"LOCAL1:"):
        return None
    try:
        decoded = base64.urlsafe_b64decode(data[len(b"LOCAL1:"):])
        nonce, mac, encrypted = decoded[:16], decoded[16:48], decoded[48:]
        key = _fallback_key(root)
        if not hmac.compare_digest(mac, hmac.new(key, nonce + encrypted, hashlib.sha256).digest()):
            return None
        raw = bytes(byte ^ key[index % len(key)] ^ nonce[index % len(nonce)] for index, byte in enumerate(encrypted))
        return raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def set_secret(name, value, root):
    os.makedirs(root, exist_ok=True)
    protected = _dpapi_protect(str(value)) or _fallback_protect(str(value), root)
    path = _path(name, root)
    temporary = path + ".tmp"
    with open(temporary, "wb") as stream:
        stream.write(protected)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return path


def get_secret(name, root):
    try:
        with open(_path(name, root), "rb") as stream:
            data = stream.read()
    except OSError:
        return None
    return _dpapi_unprotect(data) or _fallback_unprotect(data, root)


def delete_secret(name, root):
    try:
        os.remove(_path(name, root))
        return True
    except FileNotFoundError:
        return False
