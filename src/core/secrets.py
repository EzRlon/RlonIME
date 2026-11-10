"""
RlonIME — 本地凭据保护（Windows DPAPI，审计 S03）

用 crypt32.dll 的 CryptProtectData / CryptUnprotectData 把明文 API Key
加密成可安全写进 JSON 的 base64 字符串。纯 ctypes，零新依赖，不用 pywin32。

为什么要做：config.json 里曾以明文保存各家 LLM / 翻译的 API Key，
该文件随安装包分发、放在安装目录，谁都能读。DPAPI 至少能做到
「换一台机器 / 换一个 Windows 账户就拿不到明文」。

关键取舍：
- dwFlags=0 → 绑定「当前 Windows 用户」。绝不设 CRYPTPROTECT_LOCAL_MACHINE
  (0x4)：那会让本机任意账户都能解密，等于没加密。代价是换了 Windows
  账户后旧密文解不开 —— 这正是我们要的（密钥不跟着文件走）。
- pOptionalEntropy 传固定应用标识字节串做域分隔：同一账户下别的程序
  即使碰到这份密文，没有这段熵也解不开。它不是密钥，改动会使历史
  配置失效，切勿随意改。
- 返回串带 "dpapi:" 前缀。unprotect() 见到没有前缀的值原样返回，
  兼容升级前写死的旧明文配置，老用户不会一升级就丢 Key。

边界：DPAPI 防的是「配置文件被别的账户 / 别的机器读走」，防不了同账户
下的恶意进程；它也不做传输加密。任何异常 / 失败一律返回空串，调用方
应把空串当作「不可用」处理，不要让加密失败把程序带崩。
"""
from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

_PREFIX = "dpapi:"
# 固定应用标识（域分隔用，不是密钥；改动会使历史密文无法解密）。
_ENTROPY = b"RlonIME/v4/credential/v1"

# Windows BYTE = unsigned char。DATA_BLOB.pbData 指向的字节可能含 \x00，
# 不能用 c_char_p（会被当 C 字符串截断），必须按字节指针 + 显式长度处理。
_BYTE = ctypes.c_ubyte
_IS_WINDOWS = sys.platform == "win32"


class DATA_BLOB(ctypes.Structure):
    """Windows x64：DWORD cbData + BYTE* pbData。

    cbData 是 32 位 DWORD，pbData 是 64 位指针；ctypes 会自动按平台
    对齐规则在两者之间补 4 字节 padding，不要手工加字段。
    """
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(_BYTE)),
    ]


_PBLOB = ctypes.POINTER(DATA_BLOB)

_crypt32 = None
_kernel32 = None


def _lib():
    """懒加载 crypt32 并写全 argtypes/restype；非 Windows / 加载失败返回 None。"""
    global _crypt32
    if not _IS_WINDOWS:
        return None
    if _crypt32 is not None:
        return _crypt32
    try:
        dll = ctypes.WinDLL("crypt32", use_last_error=True)
        # BOOL CryptProtectData(DATA_BLOB*, LPCWSTR, DATA_BLOB*, PVOID,
        #                       CRYPTPROTECT_PROMPTSTRUCT*, DWORD, DATA_BLOB*)
        dll.CryptProtectData.argtypes = (
            _PBLOB, ctypes.c_wchar_p, _PBLOB, ctypes.c_void_p,
            ctypes.c_void_p, wintypes.DWORD, _PBLOB)
        dll.CryptProtectData.restype = wintypes.BOOL
        # BOOL CryptUnprotectData(DATA_BLOB*, LPWSTR*, DATA_BLOB*, PVOID,
        #                         CRYPTPROTECT_PROMPTSTRUCT*, DWORD, DATA_BLOB*)
        dll.CryptUnprotectData.argtypes = (
            _PBLOB, ctypes.POINTER(ctypes.c_wchar_p), _PBLOB,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, _PBLOB)
        dll.CryptUnprotectData.restype = wintypes.BOOL
        _crypt32 = dll
    except Exception:
        return None
    return _crypt32


def _free(out_blob: DATA_BLOB) -> None:
    """DPAPI 用 LocalAlloc 分配输出缓冲，调用方必须 LocalFree，否则泄漏。"""
    global _kernel32
    if not out_blob.pbData:
        return
    try:
        if _kernel32 is None:
            dll = ctypes.WinDLL("kernel32", use_last_error=True)
            dll.LocalFree.argtypes = (ctypes.c_void_p,)
            dll.LocalFree.restype = ctypes.c_void_p
            _kernel32 = dll
        _kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))
    except Exception:
        pass


def _blob(data: bytes):
    """bytes → (持有内存的数组, DATA_BLOB)。数组必须与 blob 同生命周期，
    否则 pbData 会悬空（局部临时数组被 GC 回收）。"""
    arr = (_BYTE * len(data)).from_buffer_copy(data)
    return arr, DATA_BLOB(len(data), ctypes.cast(arr, ctypes.POINTER(_BYTE)))


def available() -> bool:
    """当前平台 / 环境能否使用 DPAPI（非 Windows 恒为 False）。"""
    return _lib() is not None


def is_protected(blob: str) -> bool:
    return isinstance(blob, str) and blob.startswith(_PREFIX)


def protect(text: str) -> str:
    """明文 → "dpapi:" + base64。空串 / 失败一律返回空串。"""
    if not text:
        return ""
    dll = _lib()
    if dll is None:
        return ""
    in_arr, in_blob = _blob(text.encode("utf-8"))   # in_arr 保持引用，防悬空
    ent_arr, ent_blob = _blob(_ENTROPY)              # ent_arr 同上
    out_blob = DATA_BLOB()
    try:
        ok = dll.CryptProtectData(
            ctypes.byref(in_blob),            # pDataIn
            None,                             # pwszDataDescr
            ctypes.byref(ent_blob),           # pOptionalEntropy（应用标识）
            None,                             # pvReserved
            None,                             # pPromptStruct（不弹 UI）
            0,                                # dwFlags=0 → 当前用户，非 LOCAL_MACHINE
            ctypes.byref(out_blob))          # pDataOut
        if not ok or not out_blob.pbData or not out_blob.cbData:
            return ""
        data = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return _PREFIX + base64.b64encode(data).decode("ascii")
    except Exception:
        return ""
    finally:
        _free(out_blob)


def unprotect(blob: str) -> str:
    """密文 → 明文。非 DPAPI 格式（旧明文配置）原样返回；失败返回空串。"""
    if not blob:
        return ""
    if not is_protected(blob):
        return blob
    dll = _lib()
    if dll is None:
        return ""
    try:
        data = base64.b64decode(blob[len(_PREFIX):], validate=True)
    except Exception:
        return ""
    if not data:
        return ""
    in_arr, in_blob = _blob(data)
    ent_arr, ent_blob = _blob(_ENTROPY)
    out_blob = DATA_BLOB()
    try:
        ok = dll.CryptUnprotectData(
            ctypes.byref(in_blob),            # pDataIn
            None,                             # ppszDataDescr
            ctypes.byref(ent_blob),           # pOptionalEntropy（须与加密时一致）
            None,                             # pvReserved
            None,                             # pPromptStruct
            0,                                # dwFlags=0
            ctypes.byref(out_blob))          # pDataOut
        if not ok or not out_blob.pbData or not out_blob.cbData:
            return ""
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode(
            "utf-8", errors="replace")
    except Exception:
        return ""
    finally:
        _free(out_blob)
