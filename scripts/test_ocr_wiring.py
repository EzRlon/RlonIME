"""v4.1 截图取词（OCR）接线回归 —— 覆盖审计 A01：零接线死代码。

不启动真实输入法进程、不弹模态框；OCR 识别用自生成的 PNG 走真实系统引擎。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK ' if cond else 'FAIL'} {name}{'' if cond else '  ' + str(extra)}")


app = QApplication([])

print("[1] 热键 / 配置 / 打包接线")
from core.config import DEFAULT_HOTKEYS  # noqa: E402
import json  # noqa: E402

ck("DEFAULT_HOTKEYS 含 ocr_capture",
   DEFAULT_HOTKEYS.get("ocr_capture") == "ctrl+alt+o",
   DEFAULT_HOTKEYS.get("ocr_capture"))

cfg = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))
ck("免费翻译默认关闭（S14）", cfg["assist"].get("free_translate") is False)
ck("境外兜底默认关闭（S16）", cfg["assist"].get("allow_overseas") is False)
spec = (ROOT / "packaging" / "zhiyu.spec").read_text(encoding="utf-8")
ck("spec 收集 winrt 命名空间", 'collect_submodules("winrt")' in spec)
_fb = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
ck("兜底热键通道也注册 ocr_capture",
   '"cycle_scope", "toggle_llm", "ocr_capture"' in _fb)

print("[2] 主程序分发链")
import main  # noqa: E402
from ui.onboarding import request_free_translate_consent  # noqa: E402

ck("同意门可导入", callable(request_free_translate_consent))
for name in ("start_ocr", "_on_ocr_captured", "_show_ocr_result",
             "_ocr_insert", "_ensure_free_consent"):
    ck(f"ZhiyuApp.{name} 存在", hasattr(main.ZhiyuApp, name))


class _FakeApp:
    def __init__(self):
        self.called: list[str] = []

    def start_ocr(self):
        self.called.append("ocr")

    def translate_selection(self):
        self.called.append("translate")


f = _FakeApp()
main.ZhiyuApp.on_hotkey(f, "ocr_capture")
ck("on_hotkey('ocr_capture') 调到 start_ocr（不再吞键无动作）",
   f.called == ["ocr"], f.called)

print("[3] OCR 结果回收（工作线程 -> 主线程队列）")
import queue  # noqa: E402


class _DrainHost:
    def __init__(self):
        self._fallback_hotkeys = queue.Queue()
        self._results = queue.Queue()
        self._busy = True
        self._translate_gen = 0
        self._ocr_busy = True
        self.shown = []

    def on_hotkey(self, name):
        self.hotkey = name

    def _show_ocr_result(self, res, err):
        self.shown.append((res, err))

    def _apply_translation(self, result, hwnd=0):
        raise AssertionError("OCR 结果不应走翻译上屏")


h = _DrainHost()
h._results.put(("ocr", ("RESULT", "")))
main.ZhiyuApp._drain_results(h)
ck("ocr 队列项被主线程消费", h.shown == [("RESULT", "")], h.shown)
ck("消费后解除 busy", h._ocr_busy is False)

print("[4] 结果卡：无译文时「插入」可用且插入识别原文")
from ui.ocrpopup import OcrPopup  # noqa: E402

pop = OcrPopup()
pop.show_result("技术要求：未注圆角 R2", "zh-Hans-CN", 42.0,
                note="离线识别")
ck("无译文时插入按钮可用", pop.btn_insert.isEnabled())
hits: list[str] = []
pop.insert_requested.connect(lambda t: hits.append(t))
pop._insert()
ck("插入的是识别原文", hits == ["技术要求：未注圆角 R2"], hits)

print("[5] 真实系统 OCR 识别（自生成图片，无外部文件）")
from core import ocr  # noqa: E402

img = QImage(760, 120, QImage.Format.Format_RGB32)
img.fill(QColor("white"))
p = QPainter(img)
p.setPen(QColor("black"))
p.setFont(QFont("Microsoft YaHei UI", 22))
p.drawText(18, 70, "技术要求 未注圆角 沉孔深度")
p.end()
buf = QBuffer()
buf.open(QIODevice.OpenModeFlag.WriteOnly)
img.save(buf, "PNG")
png = bytes(buf.data())
buf.close()
ck("PNG 编码成功", len(png) > 200, len(png))

if ocr.is_available():
    try:
        res = ocr.recognize(png)
        ck("识别到文字", bool(res.text), repr(res.text[:40]))
        ck("中文汉字间空格已清理", "技 术 要 求" not in res.text,
           repr(res.text[:40]))
        ck("识别引擎标注语言", bool(res.lang), res.lang)
    except Exception as e:
        ck("真实 OCR 识别", False, str(e)[:120])
else:
    print("  SKIP 本机未安装 Windows OCR 语言包")

print()
print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("  失败项:", FAIL)
sys.exit(1 if FAIL else 0)
