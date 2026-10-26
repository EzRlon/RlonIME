"""
智语输入法 — 系统 IME 组合态检测（imm32 / 纯 ctypes，零新依赖）

为什么需要它（UX-P0-3）：
    用户用微软拼音/搜狗等系统输入法打字时，智语的全局钩子会把拼音字母
    一并吞掉，两边抢键盘 —— 系统 IME 组合串打到一半被智语截胡。
    解法：智语在吞键前问一句「系统 IME 正在组合吗」，是就全放行。

双判据：
1. 主判据：ImmGetContext(前台窗口) + ImmGetCompositionStringW(GCS_COMPSTR)
   有未决组合串 → 组合中。对 IMM32 系输入法（微软拼音多数版本/搜狗）有效。
2. 兜底判据：TSF 输入法 IMM32 兼容可能失效，枚举顶层可见窗口，
   类名含 "ime"/"candidate" 的可见窗口 = 系统 IME 候选窗正在显示，
   视为组合态。

铁律：任何异常一律返回 False —— 宁可放行智语（退化为旧行为），
绝不因检测本身制造新的吞键事故。调用点为微秒级，不违钩子不阻塞红线。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    imm32 = ctypes.WinDLL("imm32", use_last_error=True)
except OSError:                      # 极端精简系统无 imm32：主判据整体失效
    imm32 = None

GCS_COMPSTR = 0x0008

user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL

if imm32 is not None:
    imm32.ImmGetContext.argtypes = (wintypes.HWND,)
    imm32.ImmGetContext.restype = ctypes.c_void_p            # HIMC
    imm32.ImmReleaseContext.argtypes = (wintypes.HWND, ctypes.c_void_p)
    imm32.ImmReleaseContext.restype = wintypes.BOOL
    imm32.ImmGetCompositionStringW.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD)
    imm32.ImmGetCompositionStringW.restype = ctypes.c_long


def _has_composition(hwnd: int) -> bool:
    """主判据：前台窗口的 IME 上下文里有未决组合串。"""
    if imm32 is None or not hwnd:
        return False
    himc = imm32.ImmGetContext(hwnd)
    if not himc:
        return False
    try:
        # 传 NULL 缓冲区取长度；>0 即有组合串（负数为错误码）
        n = imm32.ImmGetCompositionStringW(himc, GCS_COMPSTR, None, 0)
        return n > 0
    finally:
        imm32.ImmReleaseContext(hwnd, himc)


def _ime_candidate_visible() -> bool:
    """兜底判据：存在可见的系统 IME 候选窗（类名含 ime/candidate）。"""
    found = False

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_proc(hwnd, _lparam):
        nonlocal found
        if found or not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(64)
        if user32.GetClassNameW(hwnd, buf, 64):
            name = buf.value.lower()
            if "ime" in name or "candidate" in name:
                found = True
                return False
        return True

    try:
        user32.EnumWindows(_enum_proc, 0)
    except Exception:
        return False
    return found


def system_ime_composing(hwnd: int | None = None) -> bool:
    """系统输入法是否正处于组合态（有未决拼音串 / 候选窗可见）。

    任一判据命中即 True；任何异常一律返回 False（宁放行，不误吞）。
    调用成本微秒级，可在键盘钩子决策路径内同步调用。
    """
    try:
        if hwnd is None:
            hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return False
        if _has_composition(hwnd):
            return True
        return _ime_candidate_visible()
    except Exception:
        return False
