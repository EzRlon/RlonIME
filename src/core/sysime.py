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
# 64 位下不声明 argtypes 会把 HWND 截断成 32 位（历史上踩过，pid 恒为 0）。
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.restype = ctypes.c_int

# D23：EnumWindows 不声明 argtypes 时，64 位下 LPARAM 会被按 32 位传递，
# 回调原型也不匹配 —— 补上完整签名。WNDENUMPROC 提到模块级，供下面
# _enum_proc 的装饰器直接复用（不再在函数内重复构造类型）。
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.EnumWindows.restype = wintypes.BOOL

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


def _ime_candidate_visible(fg_hwnd: int) -> bool:
    """兜底判据：**前台窗口所属进程**存在可见的 IME 候选窗。

    v4.0.1 修：以前只按类名全局枚举，任何常驻的候选窗宿主都会被当成
    「系统输入法正在组合」。本机实测微信常驻一个可见的
    `Microsoft.IME.UIManager.CandidateWindow.Host / CandidateWindow`（pid 属于微信），
    于是 system_ime_composing() 恒为 True —— 智语全程放行所有按键，
    表现为"输入法突然不管用了"，且端到端测试随机全红。
    别的进程的候选窗与本窗口输入状态无关，必须按 pid 过滤。
    """
    fg_pid = wintypes.DWORD(0)
    if not user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(fg_pid)):
        return False
    if not fg_pid.value:
        return False
    found = False

    @WNDENUMPROC
    def _enum_proc(hwnd, _lparam):
        nonlocal found
        if found or not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(64)
        if not user32.GetClassNameW(hwnd, buf, 64):
            return True
        name = buf.value.lower()
        # MSCTFIME UI / Default IME 是每个线程都有的 IME 宿主窗口，
        # 开着 IME 就存在，不代表"正在组合"—— 一律跳过。
        if name in ("msctfime ui", "default ime", "ime"):
            return True
        if "cand" not in name and not name.startswith(("sopy", "qqpinyin")):
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != fg_pid.value:
            return True                  # 别的程序的候选窗，与本窗口无关
        found = True
        return False

    try:
        user32.EnumWindows(_enum_proc, 0)
    except Exception:
        return False
    return found


def system_ime_composing(hwnd: int | None = None) -> bool:
    """系统输入法是否正处于组合态（有未决拼音串 / 候选窗可见）。

    任一判据命中即 True；任何异常一律返回 False（宁放行，不误吞）。
    ⚠️ 审计 D01：本函数会做全局 EnumWindows 与跨进程 ImmGetContext，
       只能在主线程轮询路径调用，结果写入 ImeCore.sys_ime_active；
       **绝不可在 WH_KEYBOARD_LL 回调内同步调用**。
    """
    try:
        if hwnd is None:
            hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return False
        if _has_composition(hwnd):
            return True
        return _ime_candidate_visible(hwnd)
    except Exception:
        return False
