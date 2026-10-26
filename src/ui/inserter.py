"""
智语输入法 — 上屏插入服务

不重复造轮子：
- 剪贴板读写：pyperclip（成熟跨平台剪贴板库）
- 按键模拟：ctypes 调用标准 user32 keybd_event / SendInput
- 用户数据目录：复用自己的 config

插入策略（按可靠性排序）：
1. clipboard_paste：写入剪贴板 -> 模拟 Ctrl+V -> 恢复原剪贴板（最通用，支持任意软件）
2. send_input：逐字符 Unicode 输入（无剪贴板污染，但部分软件不支持）
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import get_config

try:
    import pyperclip
except ImportError:  # pragma: no cover
    pyperclip = None  # type: ignore

user32 = ctypes.windll.user32 if sys.platform == "win32" else None  # type: ignore[attr-defined]

# 虚拟键码
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class Inserter:
    """将术语插入到当前焦点位置。"""

    def __init__(self):
        self.config = get_config()
        cfg = self.config.insertion
        self.method: str = cfg.get("method", "clipboard_paste")
        self.restore_clipboard: bool = cfg.get("restore_clipboard", True)
        self.paste_delay_ms: int = cfg.get("paste_delay_ms", 120)

    # ---------- 主入口 ----------
    def insert(self, text: str, method: str | None = None) -> bool:
        if not text:
            return False
        m = method or self.method
        if m == "send_input":
            return self._send_unicode(text)
        return self._clipboard_paste(text)

    # ---------- 方案 1：剪贴板粘贴 ----------
    def _clipboard_paste(self, text: str) -> bool:
        if user32 is None or pyperclip is None:
            return False
        backup: str | None = None
        try:
            if self.restore_clipboard:
                try:
                    backup = pyperclip.paste()
                except Exception:
                    backup = None

            pyperclip.copy(text)
            time.sleep(self.paste_delay_ms / 1000.0)

            # 模拟 Ctrl+V
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(VK_V, 0, 0, 0)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

            if self.restore_clipboard and backup is not None:
                # 延迟恢复，避免抢在目标程序读取剪贴板之前
                time.sleep(self.paste_delay_ms / 1000.0)
                try:
                    pyperclip.copy(backup)
                except Exception:
                    pass
            return True
        except Exception:
            return False

    # ---------- 方案 2：Unicode 直接输入 ----------
    def _send_unicode(self, text: str) -> bool:
        if user32 is None:
            return False
        try:
            for ch in text:
                code = ord(ch)
                user32.keybd_event(0, code, KEYEVENTF_UNICODE, 0)
                user32.keybd_event(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)
                time.sleep(0.008)
            return True
        except Exception:
            return False

    # ---------- 辅助 ----------
    def self_test(self) -> dict[str, object]:
        return {
            "platform": sys.platform,
            "user32": user32 is not None,
            "pyperclip": pyperclip is not None,
            "method": self.method,
            "clipboard_ok": self._clipboard_available(),
        }

    def _clipboard_available(self) -> bool:
        if pyperclip is None:
            return False
        try:
            pyperclip.paste()
            return True
        except Exception:
            return False


if __name__ == "__main__":
    import json
    ins = Inserter()
    print(json.dumps(ins.self_test(), ensure_ascii=False, indent=2))
