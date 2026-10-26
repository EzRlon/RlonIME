"""智语 — 词汇助手真机验收（选中即译 + 记忆闭环）

启动真实的 src/main.py，在**另一个进程**里开一个接收窗，选中中文后
用真实的按键事件（dwExtraInfo=0，物理键级别）触发热键，验收：

  1. 记忆库精确命中 → 选区就地替换成记忆里的英文（零网络、零等待）
  2. 术语库整段覆盖 → 「沉孔、倒角」直接出标准译名
  3. 迁移到另一窗口仍然可用（证明是全局能力，不是绑定某个软件）
  4. 标记纠正 → 用户改过的译法进记忆库，下一次直接命中
  5. 候选条全程不抢焦点，接收窗始终是前台

APPDATA 被重定向到临时目录，不会污染用户真实记忆库。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QTextEdit, QVBoxLayout, QWidget)
from core.ime import INPUT, KEYBDINPUT, KEYEVENTF_KEYUP, user32
from core.memory import TranslationMemory

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
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD,
                                     wintypes.BOOL)
user32.GetForegroundWindow.restype = wintypes.HWND
_S = ctypes.sizeof(INPUT)

VK_CONTROL, VK_MENU, VK_A = 0x11, 0x12, 0x41
# v2.1 左手热键：选中即译 Ctrl+Alt+Q、标记纠正 Ctrl+Alt+W
VK_Q, VK_W, VK_C = 0x51, 0x57, 0x43
VK_ESCAPE = 0x1B

SEED_SRC = "该零件底部需加工沉孔，深度 8mm"
SEED_EN = "A counterbore shall be machined at the bottom of this part, 8 mm deep."
TERM_SRC = "沉孔、倒角"
FIXED_EN = "Counterbore and chamfer are required on the mounting face."


def _send(vk: int, up: bool) -> None:
    inp = INPUT()
    inp.type = 1
    inp.u.ki = KEYBDINPUT(wVk=vk, wScan=0,
                          dwFlags=(KEYEVENTF_KEYUP if up else 0),
                          time=0, dwExtraInfo=0)     # 0 = 物理键级别注入
    user32.SendInput(1, ctypes.byref(inp), _S)


def tap(vk: int, gap: float = 0.015) -> None:
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


def wait_text(recv, expect: str, timeout: float = 4.0) -> str:
    """轮询等待接收窗变成期望内容。

    固定 sleep 在冻结版 exe 上必然偶发失败（冷启动慢、注入逐字上屏）。
    改成「等到出现为止」，测试才真正确定 —— 顺便把端到端延迟量出来了。
    """
    end = time.time() + timeout
    got = recv.text().strip()
    while time.time() < end:
        got = recv.text().strip()
        if got == expect:
            return got
        QApplication.processEvents()
        time.sleep(0.02)
    return recv.text().strip()


def pump(seconds: float) -> None:
    """等待期间必须持续泵 Qt 事件循环。

    接收窗要真的处理 Ctrl+C 才会把内容写进剪贴板；如果这里直接 sleep，
    事件循环被阻塞，取写层会一直等不到剪贴板变化而超时 —— 测出来的
    「翻译不生效」全是假象。
    """
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


class Receiver(QWidget):
    """独立接收窗，模拟用户的工程软件输入框。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZhiyuReceiver")
        self.resize(720, 200)
        lay = QVBoxLayout(self)
        self.edit = QTextEdit()
        self.edit.setAcceptRichText(False)
        lay.addWidget(self.edit)

    def focus_me(self) -> None:
        """AttachThreadInput 才能稳定抢到前台。"""
        hwnd = int(self.winId())
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None)
        tid_me = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(tid_fg, tid_me, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(tid_fg, tid_me, False)
        self.edit.setFocus()
        QApplication.processEvents()

    def load(self, text: str, select_all: bool = True) -> None:
        self.edit.setPlainText(text)
        if select_all:
            self.edit.selectAll()
        QApplication.processEvents()

    def text(self) -> str:
        return self.edit.toPlainText()


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def ck(name, cond, extra=""):
        results.append((name, bool(cond), extra))
        print(f"  {'OK ' if cond else 'FAIL'} {name}{'' if cond else '  ' + extra}")

    app = QApplication(sys.argv)
    recv = Receiver()
    recv.show()
    recv.focus_me()

    # ---- 用临时 APPDATA 启动主程序，避免污染真实记忆库 ----
    tmp = Path(tempfile.mkdtemp(prefix="zhiyu_live_"))
    env = {**os.environ, "APPDATA": str(tmp)}
    db = tmp / "ZhiyuIME" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)

    print("[0] 预热记忆库 + 启动真实主程序")
    mem = TranslationMemory(db)
    mem.learn(SEED_SRC, SEED_EN, "mechanical", "user")
    mem.close()
    ck("记忆库已预置一条用户译法", True)

    proc = subprocess.Popen(target_cmd(),
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if proc.poll() is not None:
            ck("主程序存活", False, f"exit={proc.returncode}")
            return 1
        time.sleep(0.25)
        break
    pump(3.5)                             # 等钩子挂上
    ck("主程序存活", proc.poll() is None)

    try:
        recv.focus_me()
        ck("接收窗取得前台焦点",
           int(user32.GetForegroundWindow()) == int(recv.winId()))

        # ---- [1] 记忆库精确命中 ----
        print("[1] 选中中文 → Ctrl+Alt+T（应命中记忆库）")
        recv.load(SEED_SRC)
        pump(0.3)
        recv.focus_me()
        t0 = time.perf_counter()
        chord(VK_CONTROL, VK_MENU, VK_Q)
        got = wait_text(recv, SEED_EN)
        lat = time.perf_counter() - t0
        ck("选区被就地替换成记忆库英文",
           got == SEED_EN, f"实际={got[:70]!r}")
        ck("端到端延迟（含按键松开等待）< 2.5s", lat < 2.5, f"{lat:.2f}s")
        ck("接收窗全程未被候选条抢焦点",
           int(user32.GetForegroundWindow()) == int(recv.winId()))

        # ---- [2] 术语库整段覆盖（另一个窗口，验证全局性）----
        print("[2] 换到第二个接收窗，验证全局可用（不绑定任何软件）")
        recv2 = Receiver()
        recv2.setWindowTitle("ZhiyuReceiver2")
        recv2.show()
        recv2.focus_me()
        recv2.load(TERM_SRC)
        pump(0.3)
        recv2.focus_me()
        chord(VK_CONTROL, VK_MENU, VK_Q)
        got2 = wait_text(recv2, "Counterbore / Chamfer")
        ck("术语库整段覆盖生效",
           got2 == "Counterbore / Chamfer", f"实际={got2!r}")

        # ---- [3] 标记纠正 → 记忆闭环 ----
        print("[3] 改好译文 → Ctrl+Alt+M 入库")
        recv2.load(FIXED_EN)
        pump(0.3)
        recv2.focus_me()
        chord(VK_CONTROL, VK_MENU, VK_W)
        pump(1.8)
        mem2 = TranslationMemory(db)
        p = mem2.exact(TERM_SRC, "mechanical")
        ck("纠正后的译法真的进了记忆库",
           p is not None and p.dst == FIXED_EN,
           f"命中={p.dst if p else None}")
        ck("纠正条目标记为用户确认", p is not None and p.source == "user")
        mem2.close()

        # ---- [4] 再译一次同一句 → 应走记忆库（证明下次直接调用）----
        print("[4] 同一句再译一次 → 应直接命中记忆库，不再走 AI")
        recv2.load(TERM_SRC)
        pump(0.3)
        recv2.focus_me()
        t0 = time.perf_counter()
        chord(VK_CONTROL, VK_MENU, VK_Q)
        got3 = wait_text(recv2, FIXED_EN)
        dt = time.perf_counter() - t0
        ck("记忆库闭环生效（下次直接调用用户译法）",
           got3 == FIXED_EN, f"实际={got3[:60]!r}")
        ck("闭环路径端到端 < 3s（含按键松开等待与 UI 节流）",
           dt < 3.0, f"{dt:.2f}s")

        # ---- [5] 无选中内容时应给出提示而不是乱码 ----
        print("[5] 没有选中内容时按热键 → 应提示而非误上屏")
        cur = recv2.edit.textCursor()
        cur.clearSelection()
        recv2.edit.setTextCursor(cur)
        recv2.focus_me()
        before = recv2.text()
        chord(VK_CONTROL, VK_MENU, VK_Q)
        pump(1.0)
        ck("无选区时不误上屏", recv2.text() == before)
    finally:
        try:
            chord(VK_CONTROL, VK_MENU, VK_ESCAPE)
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    n_pass = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 62}\n  通过 {n_pass} / 共 {len(results)}\n{'=' * 62}")
    bad = [n for n, ok, _ in results if not ok]
    if bad:
        print("  失败项：" + "、".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
