"""
智语输入法 — 全局键盘钩子输入内核（Windows）

这是「真正接管输入」的那一层，四件事：
1. WH_KEYBOARD_LL 全局低级键盘钩子（独立线程 + 消息循环）
2. 拼音组合状态机（吞键 / 放行决策，与 UI 完全解耦，可单测）
3. 光标精确定位（GetGUIThreadInfo.rcCaret → 屏幕坐标，含 DPI 校正）
4. SendInput Unicode 逐字上屏（不污染剪贴板、不需要目标程序获得焦点）

铁律：
- 钩子回调内绝不阻塞：只做状态判定，所有上屏动作入队，由主线程消费
- 注入事件以 dwExtraInfo 魔数标记，杜绝自我递归
- 修饰键状态自行维护，不依赖 GetAsyncKeyState（注入时会失真）
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("RlonIME 输入内核仅支持 Windows")

from core.sysime import system_ime_composing     # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------- 类型定义
_ULONG_PTR = (ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8
              else ctypes.c_ulong)
LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM,
                              wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE,
                                     wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM,
                                  wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(GUITHREADINFO))
user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.c_void_p)
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

# 句柄/指针型返回值必须显式声明，ctypes 默认按 32 位 c_int 截断，
# 会让 SetWindowsHookExW 拿到无效 hMod 并返回 err=126。
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.argtypes = ()
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetKeyState.argtypes = (ctypes.c_int,)
user32.GetKeyState.restype = ctypes.c_short
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetDC.argtypes = (wintypes.HWND,)
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
user32.ReleaseDC.restype = ctypes.c_int
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = ctypes.c_int
try:
    user32.GetDpiForWindow.argtypes = (wintypes.HWND,)
    user32.GetDpiForWindow.restype = wintypes.UINT
except AttributeError:      # Win10 1607 之前无此 API
    pass

# ---------------------------------------------------------------- 常量
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_QUIT = 0x0012

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_BACK, VK_TAB, VK_RETURN, VK_ESCAPE, VK_SPACE = 0x08, 0x09, 0x0D, 0x1B, 0x20
VK_PRIOR, VK_NEXT, VK_END, VK_HOME = 0x21, 0x22, 0x23, 0x24
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
VK_DELETE, VK_CAPITAL = 0x2E, 0x14
VK_0, VK_9 = 0x30, 0x39
VK_1 = 0x31
VK_A, VK_Z = 0x41, 0x5A
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_CONTROL, VK_SHIFT, VK_MENU = 0x11, 0x10, 0x12   # 通用修饰键（非左右区分）

# 标点键（US 布局 OEM 键位）
VK_OEM_1, VK_OEM_2 = 0xBA, 0xBF        # ; / 与 / ?
VK_OEM_PLUS, VK_OEM_MINUS = 0xBB, 0xBD   # = / -（翻页备用键）
VK_OEM_COMMA, VK_OEM_PERIOD = 0xBC, 0xBE
VK_OEM_4, VK_OEM_5, VK_OEM_6, VK_OEM_7 = 0xDB, 0xDC, 0xDD, 0xDE

# 中文标点（DY01，用户拍板"8 个常用"）：(VK, 是否按 Shift) -> 全角字符。
# 只做「非组合态 + 中文模式」；引号在 _cn_punct_for 里成对翻转。
# 组合态不转换——那时按标点应该先上屏当前词，交给原来的 commit_raw 语义。
CN_PUNCT: dict[tuple[int, bool], str] = {
    (VK_OEM_COMMA, False): "，", (VK_OEM_COMMA, True): "《",
    (VK_OEM_PERIOD, False): "。", (VK_OEM_PERIOD, True): "》",
    (VK_OEM_1, False): "；", (VK_OEM_1, True): "：",
    (VK_OEM_2, False): "、", (VK_OEM_2, True): "？",
    (VK_OEM_4, False): "【", (VK_OEM_4, True): "【",
    (VK_OEM_6, False): "】", (VK_OEM_6, True): "】",
    (VK_OEM_5, False): "、",
    (VK_1, True): "！",        # Shift+1
    (VK_9, True): "（",        # Shift+9
    (VK_0, True): "）",        # Shift+0
}

# 会被中文标点逻辑吞掉的键（含引号：它不在表里，单独翻转）
_CN_PUNCT_VKS = frozenset({vk for (vk, _sh) in CN_PUNCT} | {VK_OEM_7})

# 应用级热键：挂在同一个钩子上，不引入第二个键盘钩子。
# 热键串唯一来源是 config.hotkey()（data/config.json 的 hotkeys 段），
# 内置默认值只允许出现在 core/config.py 的 DEFAULT_HOTKEYS 里。
_VK_CTRL = {0x11, 0xA2, 0xA3}
_VK_SHIFT = {0x10, 0xA0, 0xA1}
_VK_ALT = {0x12, 0xA4, 0xA5}
_VK_WIN = {VK_LWIN, VK_RWIN}

_MOD_NAMES = ("ctrl", "alt", "shift", "win")

# 热键表条目：(修饰键集合, VK, 事件名)，事件名由 UI 层消费
HotkeyEntry = tuple[frozenset, int, str]


def parse_hotkey(spec: str) -> tuple[frozenset, int] | None:
    """把 "ctrl+alt+t" 形式的热键串解析为 (修饰键集合, VK)。

    只支持 ctrl/alt/shift/win + 单个字母或数字；解析失败返回 None，
    调用方回退内置默认并 warn。
    """
    if not spec:
        return None
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods: set[str] = set()
    for p in parts[:-1]:
        if p not in _MOD_NAMES:
            return None
        mods.add(p)
    key = parts[-1]
    if len(key) != 1:
        return None
    if "a" <= key <= "z":
        return frozenset(mods), VK_A + ord(key) - ord("a")
    if "0" <= key <= "9":
        return frozenset(mods), VK_0 + ord(key) - ord("0")
    return None


def default_hotkey_table() -> list[HotkeyEntry]:
    """用 core/config.py 的内置默认值构建热键表（无配置时的兜底）。"""
    from core.config import DEFAULT_HOTKEYS
    table: list[HotkeyEntry] = []
    for name, spec in DEFAULT_HOTKEYS.items():
        parsed = parse_hotkey(spec)
        if parsed:
            table.append((parsed[0], parsed[1], name))
    return table

MAGIC = 0x5A595A59          # 'ZYZY' 注入标记，回调里识别后直接放行


def digit_index(vk: int) -> int:
    """数字键 1-9 → 候选索引 0-8，数字键 0 → 索引 9。"""
    return (vk - VK_0 - 1) if vk > VK_0 else 9


# ================================================================ 上屏
def _ki(vk: int, scan: int, flags: int) -> INPUT:
    inp = INPUT()
    inp.type = 1            # INPUT_KEYBOARD
    inp.u.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0,
                          dwExtraInfo=MAGIC)
    return inp


def send_unicode_text(text: str) -> bool:
    """用 SendInput 逐字上屏，走 KEYEVENTF_UNICODE，绕过系统输入法。

    不写剪贴板、不需要 Ctrl+V、不要求目标程序重新获得焦点 —— 因为候选窗
    本身是 WS_EX_NOACTIVATE，焦点从未离开目标程序。
    """
    if not text:
        return False
    items: list[INPUT] = []
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:                       # 超 BMP 走代理对
            code -= 0x10000
            for c in (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)):
                items.append(_ki(0, c, KEYEVENTF_UNICODE))
                items.append(_ki(0, c, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        else:
            items.append(_ki(0, code, KEYEVENTF_UNICODE))
            items.append(_ki(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    arr = (INPUT * len(items))(*items)
    sent = user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))
    return sent == len(items)


def send_vk(vk: int, up: bool = False) -> bool:
    """补发一个普通虚拟键（用于放行场景下的标点等）。"""
    flags = KEYEVENTF_KEYUP if up else 0
    inp = _ki(vk, 0, flags)
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1


def send_chord(vk: int, ctrl: bool = False, shift: bool = False,
               alt: bool = False) -> bool:
    """原子化发送一个组合键（如 Ctrl+C）。

    所有事件带 MAGIC 标记，自己的钩子识别后直接放行，不会形成回环。
    修饰键按下与抬起在同一批 SendInput 中提交，避免与其他注入互相穿插。
    """
    mods = []
    if ctrl:
        mods.append(VK_CONTROL)
    if shift:
        mods.append(VK_SHIFT)
    if alt:
        mods.append(VK_MENU)
    items: list[INPUT] = []
    for m in mods:
        items.append(_ki(m, 0, 0))
    items.append(_ki(vk, 0, 0))
    items.append(_ki(vk, 0, KEYEVENTF_KEYUP))
    for m in reversed(mods):
        items.append(_ki(m, 0, KEYEVENTF_KEYUP))
    arr = (INPUT * len(items))(*items)
    return user32.SendInput(len(items), arr, ctypes.sizeof(INPUT)) == len(items)


# Ctrl+C / Ctrl+V 的虚拟键码，供取写层复用
VK_C, VK_V, VK_X, VK_A, VK_0_ = 0x43, 0x56, 0x58, 0x41, 0x30


# ================================================================ 光标定位
def _dpi_scale_for(hwnd) -> float:
    try:
        dpi = user32.GetDpiForWindow(hwnd)
        if dpi:
            return dpi / 96.0
    except Exception:
        pass
    try:
        gdi32 = ctypes.WinDLL("gdi32")
        dc = user32.GetDC(0)
        dpi = gdi32.GetDeviceCaps(dc, 88)       # LOGPIXELSX
        user32.ReleaseDC(0, dc)
        if dpi:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


@dataclass
class CaretInfo:
    x: int = 0                      # Qt 逻辑坐标（旧口径，仅兜底/测试注入用）
    y: int = 0
    source: str = "none"            # caret / mouse / none
    hwnd: int = 0
    # v2.6（跨屏修复）：插入符的**物理**屏幕坐标 + 该窗口所在屏的缩放比。
    # 混合 DPI 多屏下 `物理/缩放` 是错的：副屏 250% 时算出来的点会落回主屏
    # 或屏幕外（实测：主屏 1920x1080@100% + Cintiq 1536x864@250%）。
    # 正确做法是按「该点所在显示器」的物理原点 + 缩放换算，见 ui/dpi.py。
    phys_x: int = 0
    phys_y: int = 0
    scale: float = 1.0


def get_caret_info() -> CaretInfo:
    """优先取真实插入符位置（物理坐标）；取不到则退回鼠标位置。"""
    hwnd = user32.GetForegroundWindow()
    scale = 1.0
    if hwnd:
        tid = user32.GetWindowThreadProcessId(hwnd, None)
        gti = GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(GUITHREADINFO)
        if tid and user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            hcar = gti.hwndCaret or gti.hwndFocus
            if hcar:
                pt = wintypes.POINT(gti.rcCaret.left, gti.rcCaret.bottom)
                if user32.ClientToScreen(hcar, ctypes.byref(pt)):
                    scale = _dpi_scale_for(hcar)
                    return CaretInfo(int(pt.x / scale), int(pt.y / scale),
                                     "caret", int(hwnd), int(pt.x), int(pt.y),
                                     scale)
        scale = _dpi_scale_for(hwnd)

    pt = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(pt)):
        return CaretInfo(int(pt.x / scale), int(pt.y / scale), "mouse",
                         int(hwnd or 0), int(pt.x), int(pt.y), scale)
    return CaretInfo(source="none")


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def set_foreground(hwnd: int) -> bool:
    """把指定窗口拉回前台（OCR 结果「插入」前要还原截图前的窗口）。

    遮罩抢过焦点，如果直接 SendInput，字符会打到自己身上。
    """
    if not hwnd:
        return False
    try:
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow.restype = wintypes.BOOL
        return bool(user32.SetForegroundWindow(wintypes.HWND(hwnd)))
    except Exception:
        return False


def foreground_is_own_process() -> bool:
    """前台窗口是否属于本进程。

    设置中心等自身窗口需要正常打字，钩子必须在这种情况下完全放行，
    否则用户连自己的配置项都输入不了。
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def caps_lock_on() -> bool:
    """CapsLock 是否处于大写锁定。

    GetKeyState 是线程局部状态，钩子线程与用户按键同线程，读取是准确的；
    开销纳秒级，不违反「钩子回调内不阻塞」的红线。
    做成独立函数是为了可注入：单测传一个 lambda 即可覆盖大写分支。
    """
    try:
        return bool(user32.GetKeyState(VK_CAPITAL) & 0x0001)
    except Exception:
        return False


# ================================================================ 事件
@dataclass
class ImeEvent:
    kind: str                       # update/commit/commit_combo/commit_raw/cancel/nav/page/toggle
    data: dict = field(default_factory=dict)


# ================================================================ 状态机
class ImeCore:
    """拼音组合状态机。纯逻辑，无 Win32 依赖，可脱离钩子单测。

    on_key() 返回 True = 吞掉该按键，False = 交还系统。
    """

    def __init__(self, enabled: bool = True,
                 hotkey_table: list[HotkeyEntry] | None = None,
                 cn_punct: bool = True,
                 caps_provider=None):
        self.enabled = enabled
        self.composing = False
        self.pinyin = ""
        self.events: deque[ImeEvent] = deque()
        self._ctrl = self._shift = self._alt = self._win = False
        self._swallowed: set[int] = set()   # 已吞下 keydown 的键，抬起要配套吞
        # CapsLock（v3.0）：开启时字母直通大写英文，与系统输入法一致。
        # 以前完全没读这个状态 —— 大写锁定开着打字母仍然拼中文，用户会
        # 以为键盘坏了。做成可注入的 provider，单测不碰真实键盘。
        self.caps_provider = caps_provider or caps_lock_on
        # 双击 Shift 切中英（v3.0，主流输入法肌肉记忆）：
        # _shift_alone 表示这次 Shift 按下后没有夹带其它键；
        # _shift_up_at 记录上一次"干净 Shift"抬起的时间，用于双击判定。
        self._shift_alone = False
        self._shift_up_at = 0.0
        self.shift_toggle_window = 0.40
        # 联想态（UX-P1-2）：上屏后联想条亮着的短暂窗口期，
        # 数字键/Esc 归联想槽位消费。只能由 UI 层 enter_suggest 进入。
        self.suggest_active = False
        self.suggest_count = 0
        # CAD 直通（S1-A / M2）：前台是 AutoCAD / CAXA / ZWCAD 时，字母键默认
        # **全放行**——L/C/M/E 在那里是命令，被拼音吃掉就没法画图了。
        # cad_mode 由主线程轮询前台进程后写入（钩子回调里查进程会阻塞，红线）。
        # Ctrl+Space 不切全局开关，只把输入法临时激活一小段；空闲 cad_idle 秒
        # 自动回落（用户切回中文标注时手不用离开键盘）。
        self.cad_mode = False
        self.cad_idle = 5.0
        self._cad_temp = False
        self._cad_last = 0.0
        # 中文标点（DY01）：默认开启，config.json 的 ime.cn_punct 可关。
        # 只在"非组合态 + 中文模式 + 非 CAD 直通"时生效，Shift 时按上表取全角。
        self.cn_punct = bool(cn_punct)
        self._quote_open = False
        self.hotkey_table: list[HotkeyEntry] = (
            hotkey_table if hotkey_table is not None
            else default_hotkey_table())

    # ---------- 公共 ----------
    def drain(self) -> list[ImeEvent]:
        out = list(self.events)
        self.events.clear()
        return out

    def reset(self, emit: bool = True) -> None:
        had = self.composing or bool(self.pinyin)
        self.composing = False
        self.pinyin = ""
        self.exit_suggest(emit=True)
        if emit and had:
            self.events.append(ImeEvent("cancel"))

    # ---------- 联想态（UX-P1-2） ----------
    def enter_suggest(self, count: int) -> None:
        """进入联想态：count = 联想词数量（0 等同不进入）。"""
        self.suggest_count = max(0, int(count))
        self.suggest_active = self.suggest_count > 0

    def exit_suggest(self, emit: bool = False) -> None:
        """退出联想态；emit=True 时补一条 suggest_close 让 UI 收条。"""
        if self.suggest_active and emit:
            self.events.append(ImeEvent("suggest_close"))
        self.suggest_active = False
        self.suggest_count = 0

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        if not on:
            self.reset()
        self.events.append(ImeEvent("toggle", {"enabled": on}))

    def _on_shift_up(self) -> bool:
        """Shift 抬起：两次"干净"抬手间隔够短 → 双击 Shift 切中英。

        只在非组合态生效：正在拼字时双击 Shift 不该把半截拼音吞掉，
        用户更可能是想输入大写字母。
        """
        if not self._shift_alone:
            return False
        self._shift_alone = False
        now = time.monotonic()
        if self.composing:
            self._shift_up_at = now
            return False
        if 0 < now - self._shift_up_at <= self.shift_toggle_window:
            self._shift_up_at = 0.0
            self.set_enabled(not self.enabled)
            return True
        self._shift_up_at = now
        return False

    # ---------- CAD 直通（S1-A / M2） ----------
    def set_cad_mode(self, on: bool) -> None:
        """前台是否处于 CAD（由主线程轮询写入，钩子内不查进程）。"""
        on = bool(on)
        if on == self.cad_mode:
            return
        self.cad_mode = on
        if not on:
            self.end_cad_temp()
        self.events.append(ImeEvent("cad", {"on": on}))

    def start_cad_temp(self) -> None:
        """Ctrl+Space：在 CAD 里临时激活输入法（不切全局开关）。"""
        self._cad_temp = True
        self._cad_last = time.monotonic()
        self.events.append(ImeEvent("cad", {"on": True, "temp": True}))

    def end_cad_temp(self) -> None:
        """退出临时激活；若正在拼字，发 cancel 让候选条收起来。"""
        if self._cad_temp:
            self._cad_temp = False
            self.reset()

    def cad_temp_active(self, now: float | None = None) -> bool:
        """临时激活是否仍然有效（空闲超时即回落）。"""
        if not self._cad_temp:
            return False
        now = time.monotonic() if now is None else now
        if now - self._cad_last > self.cad_idle:
            self.end_cad_temp()
            return False
        return True

    def tick_cad(self) -> None:
        """主线程定时调用：让空闲回落即使不按键也能及时生效。"""
        if self._cad_temp:
            self.cad_temp_active()

    # ---------- 主入口 ----------
    def on_key(self, vk: int, down: bool, injected: bool = False) -> bool:
        if injected:                                    # 自己的注入，放行
            return False

        if vk in _VK_CTRL:
            self._ctrl = down
        elif vk in _VK_SHIFT:
            self._shift = down
        elif vk in _VK_ALT:
            self._alt = down
        elif vk in _VK_WIN:
            self._win = down

        if not down:
            # 按下被吞掉的键，抬起也必须吞掉。否则目标程序收到一个孤立的
            # keyup，键盘状态机错乱后，SendInput 注入的字符会被一并丢弃。
            if vk in self._swallowed:
                self._swallowed.discard(vk)
                return True
            if vk in _VK_SHIFT:
                return self._on_shift_up()
            return False

        # Shift 是否"干净"（v3.0 双击切中英）：按下 Shift 后若夹带任何
        # 其它键（Shift+A / Ctrl+Shift+X），这次就不算独立 Shift。
        self._shift_alone = vk in _VK_SHIFT

        block = self._decide(vk)
        if block:
            self._swallowed.add(vk)
            if self._cad_temp:
                self._cad_last = time.monotonic()   # 持续输入 ⟹ 临时激活续命
        return block

    def _match_hotkey(self, vk: int) -> str | None:
        """按当前修饰键状态匹配热键表，命中返回事件名。

        ctrl/alt/win 必须严格一致；shift 只在热键串显式包含时才要求
        （与迁移前 Ctrl+Alt+X 对 Shift 不敏感的行为保持一致）。
        """
        for mods, hk_vk, name in self.hotkey_table:
            if vk != hk_vk:
                continue
            if (("ctrl" in mods) != self._ctrl
                    or ("alt" in mods) != self._alt
                    or ("win" in mods) != self._win):
                continue
            if "shift" in mods and not self._shift:
                continue
            return name
        return None

    def _might_swallow(self, vk: int) -> bool:
        """该键在当前状态下是否可能被吞（系统 IME 检测只在这些键上做）。"""
        if VK_A <= vk <= VK_Z:
            return True
        # v2.7：中文标点也会被吞，必须纳入"系统输入法组合中全放行"的判定，
        # 否则微软拼音正在组合时按逗号，会被我们抢走并注入"，"（QA 抓到的阻断）。
        if self.cn_punct and vk in _CN_PUNCT_VKS:
            return True
        if not self.composing:
            return False
        return vk in (VK_BACK, VK_ESCAPE, VK_SPACE, VK_RETURN, VK_TAB,
                      VK_UP, VK_DOWN, VK_PRIOR, VK_NEXT) or VK_0 <= vk <= VK_9

    def _cn_punct_for(self, vk: int) -> str:
        """按键 -> 全角标点；不命中返回 ""。

        引号单独处理：没有独立开/关键，按一次翻一次（“ → ”），与主流输入法一致。
        """
        if vk == VK_OEM_7:
            self._quote_open = not self._quote_open
            return "“" if self._quote_open else "”"
        return CN_PUNCT.get((vk, bool(self._shift)), "")

    def _decide(self, vk: int) -> bool:
        """按下瞬间的吞噬决策（纯判定，不维护修饰键状态）。"""
        # 开关热键：Ctrl+Space（吞掉，避免系统输入法被同时触发）
        # CAD 场景下语义不同（S1-A）：不切全局开关，只临时激活 cad_idle 秒。
        if vk == VK_SPACE and self._ctrl and not self._alt and not self._win:
            if self.cad_mode:
                self.start_cad_temp()
                return True
            self.set_enabled(not self.enabled)
            return True

        # 应用级热键（默认 Ctrl+Alt+X，可在 config.json hotkeys 段自定义）：
        # 即使拼音接管关闭也生效，因为它们属于「外部词汇助手」能力，
        # 与接管开关正交。
        name = self._match_hotkey(vk)
        if name:
            self.events.append(ImeEvent("hotkey", {"name": name}))
            return True

        if not self.enabled:
            return False

        # 修饰键自身按下：放行，且绝不能顺手丢掉正在拼的字，
        # 否则用户按 Ctrl 想用 Ctrl+1 组合上屏时组合已经被清掉了。
        if vk in _VK_CTRL or vk in _VK_SHIFT or vk in _VK_ALT or vk in _VK_WIN:
            return False

        # Ctrl+数字：图纸标注中英组合上屏。只在组合态响应，
        # 否则会抢走浏览器/Excel 里 Ctrl+1..9 的原有快捷键。
        if (self.composing and self._ctrl and not self._alt
                and not self._win and VK_0 <= vk <= VK_9):
            self.events.append(ImeEvent("commit_combo",
                                        {"index": digit_index(vk)}))
            self.reset(emit=False)
            return True

        # 其余修饰键组合一律放行（Ctrl+C / Alt+Tab / Win+R …）
        # 组合态下则此时才丢弃半截拼音
        if self._ctrl or self._alt or self._win:
            if self.composing:
                self.reset()
            return False

        # ---------------- 联想态专用键（组合已结束，联想条还亮着） ----------------
        if self.suggest_active and not self.composing:
            if VK_0 <= vk <= VK_9:
                idx = digit_index(vk)
                if idx < self.suggest_count:
                    self.events.append(ImeEvent("suggest_pick",
                                                {"index": idx}))
                    self.exit_suggest()
                else:
                    # 数字超出联想数量：不当选中，收条并吞掉，
                    # 避免在目标程序里打出一个孤立数字
                    self.exit_suggest(emit=True)
                return True
            if vk == VK_ESCAPE:
                self.exit_suggest(emit=True)
                return True
            # 其它键：结束联想态，按键继续走正常判定（字母会开新组合）
            self.exit_suggest()

        # 系统输入法组合中 → 智语全放行（UX-P0-3）。
        # 只在「接下来可能被吞」的键路径上判定（a-z 与组合态专用键），
        # imm32 调用为微秒级，不违钩子不阻塞红线。
        # 全放行 = 压根不吞，因此「吞 keydown 配套吞 keyup」的状态机不参与。
        if self.enabled and self._might_swallow(vk) and system_ime_composing():
            self.reset()                # 收掉智语自己的半截拼音（发 cancel）
            return False

        # ---------------- 中文标点（DY01） ----------------
        # 用户拍板：只做 8 个常用标点。边界很明确——
        #   · 只在「非组合态」转换（组合态按标点仍走"先上屏原文"，不打断选词）
        #   · CAD 直通时不转换（那里的 , . ; 是命令的一部分）
        #   · Ctrl/Alt/Win 组合早已在上方放行，这里只管裸按键
        if (self.cn_punct and not self.composing
                and not (self.cad_mode and not self.cad_temp_active())):
            punct = self._cn_punct_for(vk)
            if punct:
                self.events.append(ImeEvent("punct", {"text": punct}))
                return True

        # ---------------- 字母 ----------------
        if VK_A <= vk <= VK_Z:
            # CAD 直通（S1-A）：没有临时激活时，字母是命令不是拼音。
            if self.cad_mode and not self.cad_temp_active():
                return False
            # Shift 与 CapsLock 的"大写意图"是异或关系（同按=小写），
            # 与键盘本身的语义一致。v3.0 起 CapsLock 终于被感知：
            # 以前大写锁定开着打字母仍然拼中文，用户会以为键盘坏了。
            upper = bool(self._shift) != bool(self.caps_provider())
            if self.composing:
                if upper:
                    # 组合态按大写字母：先上屏当前词，再打这个大写字母。
                    # 以前会当小写追加进拼音（chen + Shift+A → chena），
                    # 用户想打 "Chen" 却越打越长。
                    self.events.append(ImeEvent(
                        "commit_char", {"pinyin": self.pinyin,
                                        "ch": chr(vk)}))
                    self.reset(emit=False)
                    return True
            elif upper:
                return False                # 非组合态 → 直接打大写英文
            self.pinyin += chr(vk).lower()
            self.composing = True
            self.events.append(ImeEvent("update", {"pinyin": self.pinyin}))
            return True

        if not self.composing:
            return False                    # 空闲态其余键全放行

        # ---------------- 组合态专用键 ----------------
        if vk == VK_BACK:
            self.pinyin = self.pinyin[:-1]
            if self.pinyin:
                self.events.append(ImeEvent("update", {"pinyin": self.pinyin}))
            else:
                self.reset()
            return True

        if vk == VK_ESCAPE:
            self.reset()
            return True

        # 编辑键（v3.0）：组合态按 ←/→/Home/End/Delete，主流输入法的行为
        # 都是「先把当前候选上屏，再执行该键」。以前这里落到文件末尾的
        # "其它标点"分支，直接把拼音原文（chenkong）吐进文档 —— 用户按左键
        # 想挪光标，文档里却多出一串英文字母，属于硬伤。
        if vk in (VK_LEFT, VK_RIGHT, VK_HOME, VK_END, VK_DELETE):
            self.events.append(ImeEvent("commit_nav",
                                        {"pinyin": self.pinyin, "vk": vk}))
            self.reset(emit=False)
            return True

        if vk == VK_SPACE or vk == VK_RETURN:
            # 带上 raw：UI 层在「一个候选都没有」时要能用它直出拼音原文，
            # 否则用户敲了串没有匹配的拼音、按空格却毫无反应，只能按 Esc。
            self.events.append(ImeEvent("commit",
                                        {"index": -1, "raw": self.pinyin}))
            self.reset(emit=False)
            return True

        if vk == VK_TAB:
            self.events.append(ImeEvent("commit", {"index": -1, "english": True}))
            self.reset(emit=False)
            return True

        if VK_0 <= vk <= VK_9:
            self.events.append(ImeEvent("commit",
                                        {"index": digit_index(vk),
                                         "raw": self.pinyin}))
            self.reset(emit=False)
            return True

        if vk == VK_DOWN:
            self.events.append(ImeEvent("nav", {"delta": 1}))
            return True
        if vk == VK_UP:
            self.events.append(ImeEvent("nav", {"delta": -1}))
            return True
        # 翻页：PageDown/PageUp 之外再加 = / -（v2.6.1）。
        # 理由：很多笔记本/数位屏上 PgUp/PgDn 要按 Fn，日常根本按不到；
        # 主流输入法都用 - / = 翻页，肌肉记忆一致。只在组合态生效，
        # 非组合态的 - / = 照常打字符。
        if vk == VK_NEXT or vk == VK_OEM_PLUS:
            self.events.append(ImeEvent("page", {"delta": 1}))
            return True
        if vk == VK_PRIOR or vk == VK_OEM_MINUS:
            self.events.append(ImeEvent("page", {"delta": -1}))
            return True

        # 组合态按标点（v2.7 修正）：以前这里 return False 把物理键先放过去，
        # 结果是 "，chenkong"（标点在前）。现在吞掉物理键，把"当前词 + 全角标点"
        # 放进同一条事件，由主线程按顺序 SendInput。
        if self.cn_punct:
            ch = self._cn_punct_for(vk)
            if ch:
                self.events.append(ImeEvent("commit_raw",
                                            {"pinyin": self.pinyin,
                                             "suffix": ch}))
                self.reset(emit=False)
                return True

        # 其它标点/符号：先上屏拼音原文，再把按键交还系统
        self.events.append(ImeEvent("commit_raw", {"pinyin": self.pinyin}))
        self.reset(emit=False)
        return False


# ================================================================ 钩子
class KeyboardHook:
    """低级键盘钩子，跑在独立线程的消息循环上。"""

    def __init__(self, core: ImeCore, on_key=None,
                 own_process_passthrough: bool = True):
        self.core = core
        self.on_key = on_key or core.on_key
        self.own_process_passthrough = own_process_passthrough
        self._thread: threading.Thread | None = None
        self._hook = None
        self._proc = None            # 必须常驻引用，否则被 GC 回收 → 崩溃
        self._tid = 0
        self.running = False
        self.error: str = ""
        self._ready = threading.Event()

    # ---------- 生命周期 ----------
    def start(self, timeout: float = 3.0) -> bool:
        if self.running:
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="ZhiyuHook",
                                        daemon=True)
        self._thread.start()
        return self._ready.wait(timeout) and self.running

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None
        self.running = False

    # ---------- 线程体 ----------
    def _run(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()

        def _callback(n_code, w_param, l_param):
            # 铁律：这里绝不能阻塞。只做判定，动作入队。
            try:
                if n_code == 0:
                    kb = ctypes.cast(l_param,
                                     ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    own = (self.own_process_passthrough
                           and foreground_is_own_process())
                    if kb.dwExtraInfo != MAGIC and not own:
                        block = self.on_key(
                            kb.vkCode,
                            w_param in (WM_KEYDOWN, WM_SYSKEYDOWN),
                            False)
                        if block:
                            return 1
            except Exception:
                pass
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = HOOKPROC(_callback)
        # WH_KEYBOARD_LL 的 hMod 必须传 NULL。
        # 打包成 exe 后，钩子过程代码在 python DLL 里而不在 exe 模块中，
        # 此时传 GetModuleHandleW(None) 会因模块不含该过程而失败
        # （ERROR_MOD_NOT_FOUND=126），钩子悄悄不生效 —— 表现就是按键直通。
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                              None, 0)
        if not self._hook:
            # 极少数环境只认显式模块句柄，作为兜底再试一次
            self._hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._proc,
                kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            self.error = f"SetWindowsHookEx 失败 (err={ctypes.get_last_error()})"
            self.running = False
            self._ready.set()
            return

        self.running = True
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self.running = False


# ================================================================ 控制器
class ImeController:
    """把状态机 + 钩子 + 上屏 + 光标定位组合成对 UI 友好的门面。"""

    def __init__(self, enabled: bool = True,
                 own_process_passthrough: bool = True,
                 config=None):
        self.hotkey_table = self._build_hotkey_table(config)
        cn_punct = True
        if config is not None:
            cn_punct = bool((getattr(config, "ime", {}) or {})
                            .get("cn_punct", True))
        self.core = ImeCore(enabled=enabled, hotkey_table=self.hotkey_table,
                            cn_punct=cn_punct)
        self.hook = KeyboardHook(self.core,
                                 own_process_passthrough=own_process_passthrough)

    @staticmethod
    def _build_hotkey_table(cfg) -> list[HotkeyEntry]:
        """按配置构建热键表；cfg 为 None 或某项解析失败时回退内置默认并 warn。"""
        if cfg is None:
            return default_hotkey_table()
        from core.config import DEFAULT_HOTKEYS
        table: list[HotkeyEntry] = []
        for name, default_spec in DEFAULT_HOTKEYS.items():
            spec = cfg.hotkey(name, default_spec)
            parsed = parse_hotkey(spec)
            if parsed is None:
                print(f"[WARN] 热键配置无效：{name}={spec!r}，"
                      f"回退默认 {default_spec}", file=sys.stderr)
                parsed = parse_hotkey(default_spec)
            if parsed:
                table.append((parsed[0], parsed[1], name))
        return table or default_hotkey_table()

    # ---------- 热键热更新 ----------
    def reload_hotkeys(self, config) -> None:
        """设置中心改键后即时生效：重建热键表并同步给状态机。

        钩子回调读 self.core.hotkey_table；列表引用整体替换是原子的，无需加锁。
        """
        table = self._build_hotkey_table(config)
        self.hotkey_table = table
        self.core.hotkey_table = table

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        ok = self.hook.start()
        if not ok:
            print(f"[WARN] 键盘钩子未启动：{self.hook.error}", file=sys.stderr)
        return ok

    def stop(self) -> None:
        self.hook.stop()

    # ---------- 供主线程调用 ----------
    def drain(self) -> list[ImeEvent]:
        return self.core.drain()

    def send(self, text: str) -> bool:
        return send_unicode_text(text)

    def set_enabled(self, on: bool) -> None:
        self.core.set_enabled(on)

    # ---------- CAD 直通（S1-A / M2） ----------
    def set_cad_mode(self, on: bool) -> None:
        self.core.set_cad_mode(on)

    def tick_cad(self) -> None:
        self.core.tick_cad()

    @property
    def cad_mode(self) -> bool:
        return self.core.cad_mode

    @property
    def cad_temp_active(self) -> bool:
        return self.core.cad_temp_active()

    # ---------- 联想态（UX-P1-2，由候选条在上屏后进入） ----------
    def enter_suggest(self, count: int) -> None:
        self.core.enter_suggest(count)

    def exit_suggest(self) -> None:
        self.core.exit_suggest()

    @property
    def suggest_active(self) -> bool:
        return self.core.suggest_active

    @property
    def enabled(self) -> bool:
        return self.core.enabled

    @property
    def composing(self) -> bool:
        return self.core.composing

    @property
    def pinyin(self) -> str:
        return self.core.pinyin

    def caret(self) -> CaretInfo:
        return get_caret_info()

    def status(self) -> dict:
        return {
            "hooked": self.hook.running,
            "enabled": self.core.enabled,
            "composing": self.core.composing,
            "elevated": is_elevated(),
            "error": self.hook.error,
        }


if __name__ == "__main__":
    import json
    c = ImeController()
    ok = c.start()
    print(json.dumps({"started": ok, **c.status()}, ensure_ascii=False, indent=2))
    if ok:
        input("钩子已启用，随便敲几个字母试试（回车退出）...")
        c.stop()
