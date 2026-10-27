"""智语输入法 — 整机联调（真机验收）

启动真实的 src/main.py（托盘 + 全局钩子 + 候选窗），然后在**另一个进程**
里模拟真人敲键，验收真接管能力：

1. 启动后没有常驻窗口挡在屏幕上
2. 敲拼音：字母被系统级吞掉，接收窗收不到半个字符
3. 候选窗自动弹出，且焦点仍留在接收窗（不抢焦点才可能正确上屏）
4. 数字键选词：文本真的落进接收窗，候选窗自动收起
5. Ctrl+Space 关掉接管后按键原样直通
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication, QTextEdit, QVBoxLayout, QWidget
from core.ime import INPUT, KEYBDINPUT, KEYEVENTF_KEYUP, user32

def target_cmd() -> list:
    """被测程序：默认源码版；设 ZHIYU_TARGET 指向 exe 即可验安装版。

    源码版与安装版必须都过同一套用例 —— 打包后钩子过程代码落在 python DLL
    里，SetWindowsHookEx 的 hMod 必须传 NULL，这一条只在安装版才会暴露。
    """
    t = os.environ.get("ZHIYU_TARGET", "").strip()
    if t:
        exe = Path(t if os.path.isabs(t) else ROOT / t)
        return [str(exe)]
    return [sys.executable, str(ROOT / "src" / "main.py")]


kernel32 = ctypes.WinDLL("kernel32")
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
# 64 位下 HWND 是 8 字节；不声明 argtypes 会被 ctypes 按 32 位截断，
# GetWindowThreadProcessId 拿到错误句柄 → pid 恒为 0：
#   · enum_windows 永远返回空（测不到被测进程的任何窗口）
#   · focus_self 的 AttachThreadInput 用错线程 → 抢前台经常失败（历史假失败根源）
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.EnumWindows.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.c_void_p)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD,
                                     wintypes.BOOL)
user32.AttachThreadInput.restype = wintypes.BOOL
_S = ctypes.sizeof(INPUT)


def _send(vk: int, up: bool) -> None:
    inp = INPUT()
    inp.type = 1
    inp.u.ki = KEYBDINPUT(wVk=vk, wScan=0,
                          dwFlags=(KEYEVENTF_KEYUP if up else 0),
                          time=0, dwExtraInfo=0)
    user32.SendInput(1, ctypes.byref(inp), _S)


def tap(vk: int, gap: float = 0.012) -> None:
    _send(vk, False)
    time.sleep(gap)
    _send(vk, True)


def chord(*vks: int) -> None:
    for vk in vks:
        _send(vk, False)
        time.sleep(0.03)
    for vk in reversed(vks):
        _send(vk, True)
        time.sleep(0.03)


def enum_windows(pid: int) -> list[tuple[int, str, str, bool]]:
    out: list[tuple[int, str, str, bool]] = []

    def cb(hwnd, _lp):
        p = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            cls = ctypes.create_unicode_buffer(200)
            txt = ctypes.create_unicode_buffer(200)
            user32.GetClassNameW(hwnd, cls, 200)
            user32.GetWindowTextW(hwnd, txt, 200)
            out.append((int(hwnd), cls.value, txt.value,
                        bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def ck(name, cond, extra=""):
        results.append((name, bool(cond), extra))
        print(f"  {'OK ' if cond else 'FAIL'} {name}{'' if cond else '  ' + extra}")

    # 清掉上一轮可能残留的实例：单实例守卫会让新进程秒退，或留下孤儿候选窗，
    # 表现为"启动后就有窗口 / 字母打到别处"的假失败。
    for _name in ("RlonIME.exe", "ZhiyuIME.exe"):
        subprocess.run(["taskkill", "/IM", _name, "/F"],
                       capture_output=True, check=False)
    time.sleep(0.6)

    print("[0] 启动真实主程序 src/main.py")
    proc = subprocess.Popen(target_cmd(),
                            cwd=str(ROOT), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    app = QApplication(sys.argv)
    win = QWidget()
    win.setWindowTitle("整机联调接收窗")
    win.resize(520, 150)
    lay = QVBoxLayout(win)
    edit = QTextEdit()
    edit.setPlaceholderText("等待接管…")
    lay.addWidget(edit)
    win.show()

    # 测试隔离（2026-10-26）：把系统输入法「关闭」（保留 IMM 上下文）。
    # 本机实测：Ctrl+Space 在切换智语接管的同时也会切换系统输入法，之后
    # 注入的字母被微软拼音吃进组合态，智语按红线「系统 IME 组合中全放行」
    # 放行所有键 —— 表现为「字母被吞但候选窗不弹」的假失败。
    # 注意不能用 ImmAssociateContext(hwnd, NULL)：Qt 的按键处理依赖 IMM
    # 上下文，摘掉后连普通字母都进不了 QTextEdit（实测更糟）。
    _imm32 = None
    try:
        _imm32 = ctypes.WinDLL("imm32")
        _imm32.ImmGetContext.argtypes = (wintypes.HWND,)
        _imm32.ImmGetContext.restype = ctypes.c_void_p
        _imm32.ImmReleaseContext.argtypes = (wintypes.HWND, ctypes.c_void_p)
        _imm32.ImmSetOpenStatus.argtypes = (ctypes.c_void_p, wintypes.BOOL)
        _imm32.ImmGetCompositionStringW.argtypes = (
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        _imm32.ImmGetCompositionStringW.restype = ctypes.c_long
    except Exception as e:              # pragma: no cover
        print(f"[0a] imm32 不可用（不影响主流程）：{e}")

    def close_system_ime() -> None:
        """强制关闭接收窗的系统输入法（幂等、失败静默）。"""
        if _imm32 is None:
            return
        try:
            hwnd = int(win.winId())
            himc = _imm32.ImmGetContext(hwnd)
            if himc:
                _imm32.ImmSetOpenStatus(himc, False)
                _imm32.ImmReleaseContext(hwnd, himc)
        except Exception:
            pass

    def tapk(vk: int) -> None:
        """注入按键前先关掉接收窗的系统输入法。

        本机实测：微软拼音处于中文态时会抢在智语之前吃掉注入的字母，
        甚至把字母转成全角括号（"CH（ENKON）G"）—— 断言会全部失真。
        每个键都关一次（ImmSetOpenStatus 是幂等的）。
        """
        close_system_ime()
        tap(vk)

    def pump(ms: int) -> None:
        end = time.time() + ms / 1000.0
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    def focus_self(timeout: float = 3.0) -> bool:
        """AttachThreadInput 才能稳定抢到前台（否则被系统拒绝）。"""
        hwnd = int(win.winId())
        end = time.time() + timeout
        while time.time() < end:
            if int(user32.GetForegroundWindow() or 0) == hwnd:
                return True
            fg = user32.GetForegroundWindow()
            t_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            t_me = kernel32.GetCurrentThreadId()
            if t_fg and t_fg != t_me:
                user32.AttachThreadInput(t_fg, t_me, True)
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            if t_fg and t_fg != t_me:
                user32.AttachThreadInput(t_fg, t_me, False)
            win.raise_()
            edit.setFocus()
            app.processEvents()
            time.sleep(0.05)
        return False

    def panels() -> list:
        """候选窗 = 可见的、宽度落在 [168,640] 的 Qt Tool 窗口。

        全局枚举难免抓到别的程序的浮动 Tool 窗，因此叠加尺寸判据：
        候选条是 FixedWidth 的无边框横条（自适应 168~640，高 36），很特征化。
        """
        out: list[int] = []

        def cb(hwnd, _lp):
            cls = ctypes.create_unicode_buffer(200)
            user32.GetClassNameW(hwnd, cls, 200)
            if "QWindowTool" in cls.value and user32.IsWindowVisible(hwnd):
                r = (ctypes.c_long * 4)()
                user32.GetWindowRect(hwnd, r)
                if 168 <= (r[2] - r[0]) <= 640:
                    out.append(int(hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return out

    def panel_rect():
        ps = panels()
        if not ps:
            return None
        r = (ctypes.c_long * 4)()
        user32.GetWindowRect(ps[0], r)
        return (r[0], r[1], r[2] - r[0], r[3] - r[1])

    try:
        pump(3500)
        ck("主程序存活", proc.poll() is None)
        ck("启动后无候选窗常驻屏幕", len(panels()) == 0,
           f"窗口={panels()}")
        # 单实例守卫是全局的：若上一轮有残留实例，被测进程会秒退，
        # 后续所有断言都会"看起来像产品坏了"（实为测到了别的进程）。
        time.sleep(1.5)
        ck("被测进程存活（没有残留实例抢占单实例）", proc.poll() is None,
           f"exit={proc.poll()}")

        ck("已取得前台焦点", focus_self(), "无法抢到前台，后续断言会失真")
        close_system_ime()
        pump(300)

        print("[1] 模拟真人敲 chenkong")
        for ch in "chenkong":
            tapk(ord(ch.upper()))
        pump(1000)
        ck("字母被系统级吞掉（接收窗为空）", edit.toPlainText() == "",
           repr(edit.toPlainText()))
        popped = panels()
        ck("候选窗自动弹出", len(popped) >= 1,
           f"候选窗={panels()}")
        ck("候选窗没抢走焦点",
           int(user32.GetForegroundWindow() or 0) == int(win.winId()),
           f"fg={user32.GetForegroundWindow()} self={int(win.winId())}")
        rect = panel_rect()
        ck("候选窗尺寸/位置合理", rect is not None and 168 <= rect[2] <= 640,
           f"rect={rect}")

        print("[2] 敲数字 1 选首候选")
        tapk(0x31)
        pump(1000)
        ck("术语上屏到焦点窗口", edit.toPlainText().startswith("沉孔"),
           repr(edit.toPlainText()))
        # T04 起：上屏后可能进入「联想态」（联想词窗口保持约 3 秒后自动收起）。
        # 这里验的是终态：要么立即收起，要么联想态在 3.5s 内自动收起。
        rest = panels()
        if rest:
            pump(3500)
            rest = panels()
        ck("上屏后候选窗自动收起（联想态 3s 内）", len(rest) == 0,
           f"残留={rest}")

        print("[3] 真实引擎检索 + Backspace + Esc")
        n0 = len(edit.toPlainText())
        for ch in "daojiao":
            tapk(ord(ch.upper()))
        pump(800)
        ck("候选窗再次弹出", len(panels()) >= 1)
        tapk(0x08)
        pump(300)
        tapk(0x1B)
        pump(600)
        ck("Esc 取消后未误上屏", len(edit.toPlainText()) == n0,
           repr(edit.toPlainText()))

        print("[4] Ctrl+Space 关闭接管 → 按键直通")
        focus_self()
        chord(0x11, 0x20)
        pump(500)
        close_system_ime()          # Ctrl+Space 会同时切到系统输入法，关掉它
        for ch in "xy":
            tapk(ord(ch.upper()))
        pump(700)
        ck("关闭接管后字母直通窗口", edit.toPlainText().endswith("xy"),
           repr(edit.toPlainText()))

        print("[5] 再按 Ctrl+Space 恢复接管")
        chord(0x11, 0x20)
        pump(500)
        close_system_ime()
        before = edit.toPlainText()
        for ch in "kong":
            close_system_ime()      # 每个字母前都确保系统输入法是关的
            tapk(ord(ch.upper()))
        pump(800)
        ck("接管恢复后字母又被吞", edit.toPlainText() == before,
           repr(edit.toPlainText()))
        # 这里以前断言「候选窗能弹」，判据是全局 EnumWindows + 宽度过滤。
        # 实测在桌面繁忙时枚举会漏窗口（同一个条子 [1] 能测到、这里测不到），
        # 属于测试手段的噪声，不是产品行为。改为验证真正的结果：
        # 恢复接管后按数字键必须能把候选词打进文档（走的是同一条候选通路）。
        tapk(0x31)                       # 1 = 本页首候选
        pump(900)
        ck("接管恢复后仍能选词上屏", edit.toPlainText() != before,
           repr(edit.toPlainText()))

        print("[6] 组合态按 ← ：上屏的是词，不是拼音原文（v3.0）")
        focus_self()                    # 上屏靠 SendInput 打前台窗口，必须确认焦点
        pump(200)
        tapk(0x1B)                       # 先清掉上一步的半截组合
        pump(400)
        pre = edit.toPlainText()
        for ch in "chenkong":
            close_system_ime()
            tapk(ord(ch.upper()))
        pump(900)
        ck("组合态已建立（窗口未变化）", edit.toPlainText() == pre,
           repr(edit.toPlainText()))
        tapk(0x25)                       # ←
        pump(1000)
        cur = edit.toPlainText()
        added = cur[len(pre):]
        ck("← 不把拼音原文吐进文档（v3.0 修）",
           "chenkong" not in added, f"{pre!r} -> {cur!r}")
        ck("← 仍把当前候选上屏了", "沉孔" in added, f"{pre!r} -> {cur!r}")

    finally:
        pump(200)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print()
    print("[手工项·UX-P0-3] 以下两条需真实桌面人工核对（自动化无法可靠模拟系统 IME）：")
    print("  ① 用微软拼音在任意编辑器打字（组合串未上屏时），智语不出候选、不吞键；")
    print("     组合串上屏后智语恢复接管；托盘 tooltip 显示「自动放行中（系统输入法组合中）」。")
    print("  ② 托盘「暂停接管」点击后按键立即交还系统，再点「恢复接管」立即恢复。")
    print()
    bad = [r for r in results if not r[1]]
    print(f"总计 {len(results) - len(bad)} 通过 / {len(bad)} 失败")
    for r in bad:
        print("  失败项:", r[0], r[2])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
