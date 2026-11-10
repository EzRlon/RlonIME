"""
智语 — 统一取写层（Capture Layer）

三条不可动摇的底座（这是全项目覆盖率最高的方案，也是唯一能通吃
SolidWorks 工程图标注框、AutoCAD 命令行、自绘控件、Chromium 沙箱页面的方案）：

  1. 取文本 = 系统剪贴板。Windows 唯一的全局文本通道，一切能打字的控件都支持。
  2. 写文本 = SendInput Unicode（见 core/ime.py），不模拟按键、不占用剪贴板。
  3. 不依赖任何单一软件的 API，不装插件，不改配置。

读取流程严格无损：
    备份剪贴板（逐格式）→ 发 Ctrl+C → 轮询剪贴板序列号 → 取文本 → 还原剪贴板

为什么用剪贴板序列号而不是比较内容：
    GetClipboardSequenceNumber 是 O(1) 的内核计数，比读取+比对内容快一个
    数量级，而且当「复制的内容和上次完全一样」时依然能正确判定完成。
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

sys.path.insert(0, __import__("pathlib").Path(__file__).resolve().parents[1])

from core.ime import VK_C, send_chord, send_unicode_text   # noqa: E402

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.GetClipboardSequenceNumber.argtypes = ()
_u32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_u32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
_u32.GetAsyncKeyState.restype = ctypes.c_short

# 会污染组合键语义的修饰键（含左右专用键）
_MOD_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C,   # Shift / Ctrl / Alt / Win
            0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5)

# 一次取词的最长等待。超过就判定「没有选中任何内容」。
DEFAULT_TIMEOUT = 0.45
# 轮询间隔：8ms 是手感与 CPU 占用的平衡点（一轮最多 ~55 次 GetClipboardSequenceNumber，
# 每次为内核计数器读取，开销可忽略）。
POLL_INTERVAL = 0.008

# D13：read_selection 重入保护。同一时刻只允许一次取词，热键在
# processEvents 里重入时直接返回失败结果，不抛异常、不二次动剪贴板。
_capturing = False
_capture_lock = threading.Lock()


def wait_modifiers_released(timeout: float = 0.45) -> bool:
    """等用户把 Ctrl/Alt/Shift/Win 松开。

    这是必须的一步，不是保险起见：热键 Ctrl+Alt+T 触发后，钩子事件在 25ms 内
    就被主线程消费掉了，而人手松开 Alt 通常要 80~150ms。如果不等待，我们发出去
    的 Ctrl+C 在目标程序看来是 **Ctrl+Alt+C** —— 不是复制，取词直接失败。
    手速越快越容易踩中，属于典型的「测试环境复现、真机也复现」的竞态。
    """
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if not any(_u32.GetAsyncKeyState(v) & 0x8000 for v in _MOD_VKS):
            return True
        time.sleep(POLL_INTERVAL)
    return False


@dataclass
class CaptureResult:
    """一次取词的结果。"""

    text: str = ""
    ok: bool = False
    reason: str = ""        # ok / empty / timeout / no_clipboard

    def __bool__(self) -> bool:
        return self.ok


# ================================================================
# 剪贴板快照 / 还原
# ================================================================
def _clipboard():
    """取 Qt 剪贴板对象；无 Qt 环境返回 None。"""
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return None
    app = QApplication.instance()
    return app.clipboard() if app is not None else None


def snapshot_clipboard() -> dict[str, bytes] | None:
    """逐格式深拷贝当前剪贴板。

    注意：必须深拷贝。QClipboard.mimeData() 返回的指针在剪贴板内容变化后
    立即失效，发完 Ctrl+C 再去读它只会拿到新内容 —— 这是还原失败的头号原因。
    """
    cb = _clipboard()
    if cb is None:
        return None
    md = cb.mimeData()
    if md is None:
        return {}
    snap: dict[str, bytes] = {}
    for fmt in md.formats():
        try:
            snap[fmt] = bytes(md.data(fmt))
        except Exception:
            continue
    return snap


def restore_clipboard(snap: dict[str, bytes] | None) -> bool:
    """把快照写回剪贴板。snap 为 {} 表示原先为空，此时清空剪贴板。"""
    cb = _clipboard()
    if cb is None or snap is None:
        return False
    try:
        if not snap:
            cb.clear()
            return True
        from PySide6.QtCore import QByteArray, QMimeData
        md = QMimeData()
        for fmt, data in snap.items():
            md.setData(fmt, QByteArray(data))
        cb.setMimeData(md)
        return True
    except Exception:
        return False


# ================================================================
# 取选区文本
# ================================================================
def read_selection(timeout: float = DEFAULT_TIMEOUT,
                   restore: bool = True) -> CaptureResult:
    """取当前选中文本。

    时序：备份 → Ctrl+C → 等序列号变化 → 读文本 → 还原。
    整个过程在主线程完成，典型耗时 25~60ms，用户无感。

    D13：整段取词加重入保护，且剪贴板还原放进 finally —— 任何异常 /
    早返回路径都不会把 Ctrl+C 的新内容留在用户剪贴板里。
    """
    global _capturing
    with _capture_lock:
        if _capturing:
            # 重入（热键在 processEvents 里再次触发）：直接失败，不抛异常。
            return CaptureResult(reason="timeout")
        _capturing = True
    try:
        return _read_selection(timeout, restore)
    finally:
        with _capture_lock:
            _capturing = False


def _read_selection(timeout: float, restore: bool) -> CaptureResult:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return CaptureResult(reason="no_clipboard")
    cb = app.clipboard()
    if cb is None:
        return CaptureResult(reason="no_clipboard")

    # 必须先等热键的修饰键松开，否则 Ctrl+C 会变成 Ctrl+Alt+C
    wait_modifiers_released()

    snap: dict[str, bytes] | None = None
    try:
        snap = snapshot_clipboard() if restore else None
        seq0 = _u32.GetClipboardSequenceNumber()

        if not send_chord(VK_C, ctrl=True):
            return CaptureResult(reason="timeout")

        text = ""
        changed = False
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if _u32.GetClipboardSequenceNumber() != seq0:
                changed = True
                break
            time.sleep(POLL_INTERVAL)
            QApplication.processEvents()     # 让 Qt 感知到系统剪贴板变化

        if changed:
            # 给目标程序一点时间把数据真正写入剪贴板
            time.sleep(0.012)
            QApplication.processEvents()
            md = cb.mimeData()
            if md is not None and md.hasText():
                text = md.text().strip()

        if not changed:
            return CaptureResult(reason="timeout")
        if not text:
            return CaptureResult(reason="empty")
        return CaptureResult(text=text, ok=True, reason="ok")
    finally:
        if restore:
            restore_clipboard(snap)


# ================================================================
# 写回
# ================================================================
def write_text(text: str) -> bool:
    """把文本注入到当前焦点处。

    走到这里时选区仍然存在，SendInput 注入的字符会直接替换选区 ——
    不需要 Delete、不需要剪贴板、不需要重新激活窗口。
    """
    return send_unicode_text(text)


def replace_selection(text: str) -> bool:
    """用 text 替换当前选中内容。

    同样要等修饰键松开：否则 Alt 未松开时注入的字符会被当成菜单快捷键，
    文字进不去目标程序。
    """
    if not text:
        return False
    wait_modifiers_released()
    return send_unicode_text(text)


def clear_selection() -> bool:
    """删除当前选中内容（发一个 Delete 键的动作等价物）。"""
    if not send_chord(0x2E):            # VK_DELETE
        return False
    return True


# ================================================================
# 自检
# ================================================================
if __name__ == "__main__":
    import json
    print(json.dumps({
        "has_clipboard": _clipboard() is not None,
        "seq": _u32.GetClipboardSequenceNumber(),
    }, ensure_ascii=False, indent=2))
    if len(sys.argv) > 1 and sys.argv[1] == "read":
        t0 = time.perf_counter()
        r = read_selection()
        print(f"取词耗时 {(time.perf_counter() - t0) * 1000:.1f}ms -> "
              f"ok={r.ok} reason={r.reason} text={r.text[:60]!r}")
