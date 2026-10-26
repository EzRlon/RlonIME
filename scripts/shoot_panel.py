"""
截图工具：真实渲染新版候选窗（含身份面包屑 + 智能推荐分支），输出 PNG。
用于交付前视觉验收。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.engine import TermEngine
from scene.detector import SceneDetector
from ui.inserter import Inserter
from ui.panel import InputPanel

OUT = ROOT / "docs"


def shoot() -> None:
    app = QApplication(sys.argv)
    eng = TermEngine()
    det = SceneDetector()
    panel = InputPanel(eng, det, eng and Inserter())

    scene = det.lock(profession="mechanical", software="solidworks")
    panel.set_scene(scene)
    panel.move(80, 80)
    panel.show()
    panel.raise_()

    shots = []

    def shot_terms():
        panel.show_branch("terms")
        panel.input.setText("chenkong")
        panel.refresh_candidates("chenkong")
        app.processEvents()
        p = OUT / "panel_terms.png"
        panel.grab().save(str(p))
        shots.append(p)

    def shot_suggest():
        panel.accepted = ["沉孔"]
        panel.show_branch("suggest")
        panel.refresh_suggestions("chenkong")
        app.processEvents()
        p = OUT / "panel_suggest.png"
        panel.grab().save(str(p))
        shots.append(p)

    def done():
        for s in shots:
            print("saved:", s, s.stat().st_size, "bytes")
        app.quit()

    QTimer.singleShot(600, shot_terms)
    QTimer.singleShot(1400, shot_suggest)
    QTimer.singleShot(2000, done)
    app.exec()


if __name__ == "__main__":
    shoot()
