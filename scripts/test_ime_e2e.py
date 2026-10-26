"""智语输入法 — 接管能力端到端验证

不是单元测试：这里用 SendInput 发出「无注入标记」的真实按键序列，
让低级键盘钩子当成真人在敲，验证完整链路：

    敲 chenkong → 字母被吞（接收窗应为空）
                → 钩子更新组合缓冲
                → 敲数字 1 → 上屏候选文本
                → 接收窗出现「沉孔」

接收窗是同进程的 QTextEdit，既验证「吞键」，也验证「上屏」。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTextEdit, QVBoxLayout, QWidget

from core.ime import (INPUT, KEYBDINPUT, KEYEVENTF_KEYUP, ImeController,
                      user32)

user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.ShowWindow.restype = wintypes.BOOL

# ------------------------------------------------------------ 模拟真人按键
_S = ctypes.sizeof(INPUT)


def raw_key(vk: int, up: bool = False) -> None:
    """不带 MAGIC 标记的按键 —— 对钩子而言就是真人敲的。"""
    inp = INPUT()
    inp.type = 1
    inp.u.ki = KEYBDINPUT(wVk=vk, wScan=0,
                          dwFlags=(KEYEVENTF_KEYUP if up else 0),
                          time=0, dwExtraInfo=0)
    user32.SendInput(1, ctypes.byref(inp), _S)


def raw_tap(vk: int) -> None:
    raw_key(vk, False)
    time.sleep(0.012)
    raw_key(vk, True)


def vk_of(ch: str) -> int:
    return ord(ch.upper())


# ------------------------------------------------------------ 假引擎
LOOKUP = {
    "chenkong": ["沉孔", "沉孔标注", "沉孔工艺"],
    "kong": ["孔", "孔径", "孔位"],
}


class Harness:
    def __init__(self, app: QApplication, edit: QTextEdit, ctrl: ImeController):
        self.app = app
        self.edit = edit
        self.ctrl = ctrl
        self.cands: list[str] = []
        self.commits: list[str] = []
        self.updates: list[str] = []

    def pump(self, ms: int) -> None:
        end = time.time() + ms / 1000.0
        while time.time() < end:
            self.app.processEvents()
            self.poll()
            time.sleep(0.01)

    def poll(self) -> None:
        """模拟 UI 层消费者：内核出事件 → 查候选 → 需要时上屏。"""
        for ev in self.ctrl.drain():
            k = ev.kind
            if k == "update":
                self.updates.append(ev.data["pinyin"])
                self.cands = LOOKUP.get(ev.data["pinyin"], [])
            elif k == "commit":
                idx = ev.data.get("index", -1)
                if not self.cands:
                    continue
                text = self.cands[-1] if idx == -1 else (
                    self.cands[idx] if idx < len(self.cands) else self.cands[0])
                self.commits.append(text)
                self.ctrl.send(text)
                self.ctrl.core.reset(emit=False)
                self.cands = []
            elif k in ("cancel",):
                self.cands = []
            elif k == "commit_raw":
                p = ev.data.get("pinyin", "")
                if p:
                    self.commits.append(p)
                    self.ctrl.send(p)
                self.cands = []


def main() -> int:
    app = QApplication(sys.argv)
    win = QWidget()
    win.setWindowTitle("智语输入法 · 接管验证接收窗")
    win.resize(560, 160)
    lay = QVBoxLayout(win)
    edit = QTextEdit()
    edit.setPlaceholderText("等待输入…")
    lay.addWidget(edit)
    win.show()
    win.raise_()
    win.activateWindow()
    edit.setFocus()

    # 同进程接收窗：关掉「自身进程放行」，否则按键会被当作本程序自己的输入而放行
    ctrl = ImeController(enabled=True, own_process_passthrough=False)
    if not ctrl.start():
        print("FATAL: 钩子未启动 →", ctrl.hook.error)
        return 2

    app.processEvents()
    time.sleep(0.15)
    app.processEvents()

    # 焦点是本测试的命门：拿不到前台焦点，上屏断言全部失真。
    def ensure_focus(timeout: float = 3.0) -> bool:
        hwnd = int(win.winId())
        end = time.time() + timeout
        while time.time() < end:
            if int(user32.GetForegroundWindow() or 0) == hwnd:
                return True
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            win.raise_()
            win.activateWindow()
            edit.setFocus()
            app.processEvents()
            time.sleep(0.04)
        return False

    focus_ok = ensure_focus()
    print(f"[焦点] {'已取得前台焦点' if focus_ok else '⚠ 未取得前台焦点，上屏断言可能失真'}")

    h = Harness(app, edit, ctrl)
    h.pump(400)

    results: list[tuple[str, bool, str]] = []

    def ck(name, cond, extra=""):
        results.append((name, bool(cond), extra))
        print(f"  {'OK ' if cond else 'FAIL'} {name}{'' if cond else '  ' + extra}")

    print("[A] 模拟真人敲 chenkong")
    ensure_focus()
    for ch in "chenkong":
        raw_tap(vk_of(ch))
    h.pump(500)

    typed = edit.toPlainText()
    ck("组合缓冲收到全文", ctrl.core.pinyin == "chenkong", repr(ctrl.core.pinyin))
    ck("update 事件逐键到达", h.updates == list("chenkong") or
       h.updates[-1:] == ["chenkong"], repr(h.updates))
    ck("字母被吞掉（接收窗为空）", typed == "", repr(typed))
    ck("候选已查出", len(h.cands) == 3, repr(h.cands))
    ck("仍处组合态", ctrl.composing)

    print("[B] 敲数字 1 选首候选")
    ensure_focus()
    raw_tap(0x31)
    h.pump(600)
    final = edit.toPlainText()
    ck("发出 commit 事件", h.commits[:1] == ["沉孔"], repr(h.commits))
    ck("文本真正上屏到焦点窗口", final == "沉孔", repr(final))
    ck("组合态已清空", not ctrl.composing and ctrl.pinyin == "")

    print("[C] 再次输入并测 Backspace")
    for ch in "kong":
        raw_tap(vk_of(ch))
    h.pump(400)
    ck("缓冲为 kong", ctrl.core.pinyin == "kong", repr(ctrl.core.pinyin))
    raw_tap(0x08)                       # Backspace
    h.pump(250)
    ck("回删一位为 kon", ctrl.core.pinyin == "kon", repr(ctrl.core.pinyin))

    print("[D] Escape 取消，接收窗不应变化")
    raw_tap(0x1B)
    h.pump(300)
    ck("已退出组合态", not ctrl.composing)
    ck("接收窗仍只有沉孔", edit.toPlainText() == "沉孔",
       repr(edit.toPlainText()))

    print("[E] Ctrl+Space 关掉接管后，字母应直通")
    ensure_focus()
    raw_key(0x11, False)                # Ctrl down
    raw_tap(0x20)                       # Space
    raw_key(0x11, True)
    h.pump(350)
    ck("开关已关闭", ctrl.enabled is False)
    before = edit.toPlainText()
    for ch in "ab":
        raw_tap(vk_of(ch))
    h.pump(450)
    ck("关闭态字母直通到窗口", edit.toPlainText().endswith("ab"),
       repr(edit.toPlainText()))

    print("[F] Ctrl+Space 重新打开接管")
    raw_key(0x11, False)
    raw_tap(0x20)
    raw_key(0x11, True)
    h.pump(350)
    ck("开关已恢复", ctrl.enabled is True)
    n_before = len(edit.toPlainText())
    for ch in "kong":
        raw_tap(vk_of(ch))
    h.pump(450)
    ck("重开后字母又被吞", len(edit.toPlainText()) == n_before,
       repr(edit.toPlainText()))

    ctrl.stop()
    ck("钩子已卸载", not ctrl.hook.running)

    print()
    bad = [r for r in results if not r[1]]
    print(f"总计 {len(results) - len(bad)} 通过 / {len(bad)} 失败")
    for r in bad:
        print("  失败项:", r[0], r[2])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
