"""A06-3 选区悬浮「译」钮 + 英→中回译回归（M4 / N1）

验收口径：
  1. 只有「拖选」才浮钮：单击/双击不触发；落在自己窗口上不触发
  2. 浮钮定位按光标所在屏幕夹取（多屏红线：不用 primaryScreen 硬夹）
  3. 点击 = 先收钮再翻译（不挡住原程序选区），并有超时自动消失
  4. 英→中：术语反查能出标准中文；无把握时返回 None（不伪造）
  5. 方向判定：含汉字 → 中→英，纯英文 → 英→中

    python scripts/test_floatbtn.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          f"{'' if cond else '  ' + str(extra)}")


def main() -> int:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QWidget
    from core.engine import TermEngine
    from core.memory import TranslationMemory
    from core.models import SceneContext
    from core.translate import Translator
    from ui import floatbtn as fb

    app = QApplication([])
    scene = SceneContext(profession_key="mechanical")

    # ---------------- [1] 拖选检测 ----------------
    print("[1] 拖选检测：只有真拖选才浮钮")
    hits: list = []
    w = fb.SelectionWatcher()
    w.selected.connect(lambda p: hits.append(p))

    ck("按下不触发", w.feed(True, QPoint(100, 100)) is False)
    ck("原地松手（单击）不触发",
       w.feed(False, QPoint(101, 101)) is False and not hits)
    ck("双击两下都不触发",
       w.feed(True, QPoint(100, 100)) is False
       and w.feed(False, QPoint(100, 100)) is False
       and w.feed(True, QPoint(100, 100)) is False
       and w.feed(False, QPoint(100, 100)) is False and not hits)
    ck("拖选触发", w.feed(True, QPoint(100, 100)) is True
       or w.feed(True, QPoint(100, 100)) is False)
    w.feed(True, QPoint(100, 100))
    fired = w.feed(False, QPoint(160, 130))
    ck("拖 60px 后松手 → 浮钮", fired is True and len(hits) == 1, (fired, hits))
    ck("释放点即浮钮锚点",
       bool(hits) and hits[-1] == QPoint(160, 130), hits)

    # 落在自己窗口上：不浮（否则拖候选条会误触）
    own = QWidget()
    own.setGeometry(300, 300, 200, 120)
    own.show()
    app.processEvents()
    hits.clear()
    w.feed(True, QPoint(310, 310))
    fired = w.feed(False, QPoint(360, 360))
    ck("落点在自己窗口上不触发", fired is False and not hits, (fired, hits))
    own.hide()

    # 前台是本进程（如正在用设置弹窗）：不浮
    real_own = fb.foreground_is_own_process
    try:
        fb.foreground_is_own_process = lambda: True
        hits.clear()
        w.feed(True, QPoint(10, 10))
        fired = w.feed(False, QPoint(80, 80))
        ck("前台是本进程时不触发", fired is False and not hits, (fired, hits))
    finally:
        fb.foreground_is_own_process = real_own

    # 抬起但从未按下 → 不触发
    w2 = fb.SelectionWatcher()
    ck("孤立 keyup 不触发", w2.feed(False, QPoint(50, 50)) is False)

    # ---------------- [2] 定位：多屏夹取 ----------------
    print("[2] 浮钮定位：贴边不越界")
    scr = QApplication.primaryScreen()
    geo = scr.availableGeometry()
    btn = fb.FloatTranslateButton()
    normal = btn.place_for(QPoint(geo.left() + 200, geo.top() + 200))
    ck("常规位置在光标右下", normal.x() > geo.left() + 200
       and normal.y() > geo.top() + 200, normal)
    near_rb = btn.place_for(QPoint(geo.right() - 3, geo.bottom() - 3))
    ck("右下角仍完整可见",
       near_rb.x() + btn.W <= geo.right() + 1
       and near_rb.y() + btn.H <= geo.bottom() + 1, near_rb)
    near_lt = btn.place_for(QPoint(geo.left(), geo.top()))
    ck("左上角不出屏",
       near_lt.x() >= geo.left() and near_lt.y() >= geo.top(), near_lt)

    # ---------------- [3] 点击语义 ----------------
    print("[3] 点击 = 先收钮再翻译")
    clicks: list = []
    btn.clicked.connect(lambda: clicks.append(1))
    btn.popup_at(QPoint(geo.left() + 300, geo.top() + 300))
    app.processEvents()
    ck("浮钮已显示", btn.isVisible())
    ck("自动消失计时已启动", btn._timer.isActive())
    ck("已加 NOACTIVATE（不抢焦点）", btn._noact is True)
    btn.activate()
    ck("点击发出 clicked", clicks == [1], clicks)
    ck("点击后立即隐藏", btn.isVisible() is False)
    ck("计时已停止", btn._timer.isActive() is False)
    btn.dismiss()                        # 幂等，不抛异常
    ck("重复 dismiss 安全", True)

    # ---------------- [4] 英→中术语反查 ----------------
    print("[4] 英→中：术语反查不伪造")
    eng = TermEngine()
    tmp = Path(tempfile.gettempdir()) / "rlon_test_floatbtn.db"
    tmp.unlink(missing_ok=True)
    mem = TranslationMemory(tmp)
    tr = Translator(eng, mem)

    r = tr.translate_back_local("Counterbore", scene)
    ck("Counterbore → 沉孔", bool(r) and r.ok and r.text == "沉孔",
       r.text if r else None)
    ck("来源=术语反查", bool(r) and r.source == "term"
       and r.source_label == "术语反查", r.source if r else None)
    ck("方向=英→中", bool(r) and r.direction == "en2zh")
    ck("大小写不敏感",
       bool(tr.translate_back_local("COUNTERBORE", scene)))
    r2 = tr.translate_back_local("Chamfer", scene)
    ck("Chamfer → 倒角", bool(r2) and r2.text == "倒角",
       r2.text if r2 else None)

    bad = tr.translate_back_local("Hello there, nice weather today", scene)
    ck("无把握的长句返回 None（不硬凑）", bad is None, bad)

    # 记忆库优先
    mem.learn("Counterbore shall be 8 mm deep", "沉孔深度 8 mm",
              "mechanical", "user")
    r3 = tr.translate_back_local("Counterbore shall be 8 mm deep", scene)
    ck("记忆库命中优先", bool(r3) and r3.source == "memory_exact"
       and r3.text == "沉孔深度 8 mm", r3.source if r3 else None)
    ck("记忆库命中方向仍为英→中", bool(r3) and r3.direction == "en2zh")

    # ---------------- [5] 方向判定 ----------------
    print("[5] 方向判定")
    from main import _has_cjk
    ck("中文 → 中→英", _has_cjk("沉孔深度 8 mm"))
    ck("纯英文 → 英→中", not _has_cjk("Counterbore depth 8 mm"))
    ck("中英混排按中文处理", _has_cjk("沉孔 Counterbore"))

    # ---------------- [6] 回译提示词就位 ----------------
    print("[6] 回译提示词")
    from core.llm import SYSTEM_TRANSLATE_BACK
    ck("回译提示词存在且含术语口径",
       "沉孔" in SYSTEM_TRANSLATE_BACK and "只输出中文" in SYSTEM_TRANSLATE_BACK)

    mem.close()
    tmp.unlink(missing_ok=True)
    print()
    print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("  失败项:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
