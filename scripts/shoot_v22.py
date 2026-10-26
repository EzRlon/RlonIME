"""
v2.2 UI 目视截图（离屏渲染，不依赖真实显示器）：
候选条四态 + 四个弹窗 + 图标表 -> docs/v22_preview/*.png

    python scripts/shoot_v22.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = ROOT / "docs" / "v22_preview"


def grab(widget, name: str, pad: int = 14) -> None:
    """抓取控件：外扩 pad 填浅色底便于观察圆角/阴影边缘。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    widget.adjustSize()
    img = widget.grab().toImage()
    canvas = QImage(img.width() + pad * 2, img.height() + pad * 2,
                    QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#EDF2F0"))
    p = QPainter(canvas)
    p.drawImage(pad, pad, img)
    p.end()
    path = OUT / f"{name}.png"
    canvas.save(str(path))
    print(f"  saved {path.name}  ({canvas.width()}x{canvas.height()})")


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from core.engine import TermEngine
    from scene.detector import SceneDetector
    from ui.inserter import Inserter
    from ui.bar import CandidateBar
    from ui.popups import AddWordPopup, AiPopup, AboutPopup, IndustryPopup

    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])

    engine = TermEngine()
    bar = CandidateBar(engine, SceneDetector(), Inserter())
    bar.prime_window()

    # ① 输入态
    bar.show_input("chenkong")
    bar.refresh_candidates("chenkong")
    grab(bar, "01_bar_input")

    # ② 联想态
    bar.show_suggestions(["通孔", "平孔", "螺纹孔"], "沉孔")
    grab(bar, "02_bar_suggest")

    # ③ 提示态
    bar.show_hint("已加入词库：氮化处理", "ok")
    grab(bar, "03_bar_hint")

    # ④ 结果态
    class _R:
        ok = True
        source = "llm"
        source_label = "AI"
        text = "Counterbore the hole before tapping."
        fixes = None
        error = ""
    bar.show_result(_R())
    grab(bar, "04_bar_result")

    # ⑤⑥⑦⑧ 四个弹窗
    add = AddWordPopup(engine)
    grab(add, "05_popup_add")
    ind = IndustryPopup("mechanical")
    ind.adjustSize()
    grab(ind, "06_popup_industry", pad=10)
    ai = AiPopup({"enabled": True, "base_url": "https://open.bigmodel.cn/api/paas/v4",
                  "model": "glm-4-flash", "api_key": "sk-xxxx"})
    grab(ai, "07_popup_ai")
    ab = AboutPopup(version="2.2.0")
    grab(ab, "08_popup_about")

    # ⑨ 图标表（顶行 6 图标 + 托盘双版图标）
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QIcon
    from ui import design
    from ui.icons import paint_icon
    from main import make_tray_icon
    names = ["plus", "grid", "ai", "pause", "play", "info", "close", "check"]
    cell, margin = 48, 12
    sheet = QImage(cell * len(names) + margin * 2, cell * 2 + margin * 3,
                   QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#FFFFFF"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    from PySide6.QtGui import QColor as C
    for i, n in enumerate(names):
        x = margin + i * cell
        p.save()
        p.translate(x + 8, margin + 8)
        paint_icon(p, n, 32, C(design.LIGHT.TEXT_FAINT))
        p.restore()
        p.save()
        p.translate(x + 8, margin + cell + 8)
        paint_icon(p, n, 32, C(design.LIGHT.PRIMARY))
        p.restore()
    # 托盘图标两版
    tray_light = make_tray_icon().pixmap(40)
    p.drawPixmap(margin, margin + cell * 2, tray_light)
    p.end()
    sheet.save(str(OUT / "09_icons.png"))
    print(f"  saved 09_icons.png ({sheet.width()}x{sheet.height()})")

    del app
    print("done ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
