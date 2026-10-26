"""GUI 真实启动测试 — 启动后 3 秒自动截图并退出"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from core.engine import TermEngine
from scene.detector import SceneDetector
from ui.inserter import Inserter
from ui.panel import InputPanel

app = QApplication(sys.argv)
engine = TermEngine()
detector = SceneDetector()
inserter = Inserter()

panel = InputPanel(engine, detector, inserter)
# 模拟用户在 SolidWorks 工程图场景输入
scene = detector.lock(profession="mechanical", software="solidworks")
scene.scene_tags = ["drawing_2d", "3d_modeling"]
panel.set_scene(scene)
panel.input.setText("chenkong")
panel.refresh_candidates("chenkong")

panel.show()
panel.move(120, 120)

shot_path = ROOT / "docs" / "panel_preview.png"


def capture():
    screen = app.primaryScreen()
    pix = screen.grabWindow(0, panel.x() - 10, panel.y() - 10,
                            panel.width() + 20, panel.height() + 20)
    ok = pix.save(str(shot_path))
    print(f"screenshot saved: {ok} -> {shot_path}")
    print(f"panel visible: {panel.isVisible()}, size: {panel.width()}x{panel.height()}")
    print(f"candidates: {panel.list.count()}")
    for c in panel.candidates[:5]:
        print(f"  {c.zh} | {c.en} | {c.score} | {c.why()}")
    app.quit()


QTimer.singleShot(1500, capture)
sys.exit(app.exec())
