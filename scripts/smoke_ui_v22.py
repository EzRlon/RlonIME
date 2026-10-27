"""
v2.2 UI 冒烟（离屏可跑）：候选条新顶行 + 四个弹窗 + 托盘图标。

只做结构性检查（控件存在/信号能发/不崩），真实平台项
（窗口标志、SendInput、拖拽）仍以五套回归为准。

    python scripts/smoke_ui_v22.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" | {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from core.engine import TermEngine
    from scene.detector import SceneDetector
    from ui.inserter import Inserter
    from ui.bar import CandidateBar
    from ui import design
    from ui.popups import AddWordPopup, AiPopup, AboutPopup, IndustryPopup

    app = QApplication([])
    engine = TermEngine()
    bar = CandidateBar(engine, SceneDetector(), Inserter())

    # ---- 候选条顶行（D2）----
    check("顶行无把手/无设置齿轮",
          not hasattr(bar, "drag_handle") and not hasattr(bar, "btn_settings"))
    for attr in ("btn_pin", "btn_add", "btn_industry", "btn_ai",
                 "btn_pause", "btn_info", "btn_close"):
        check(f"顶行图标 {attr}", hasattr(bar, attr))
    check("图标列共 7 枚（v2.7 加 📌 固定）", len(bar._icon_btns) == 7)
    # v2.7：图标只认左键（右键以前也会触发加词条/固定/收起）
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    fired: list = []
    bar.open_add_word.connect(lambda: fired.append(1))
    _ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(2, 2), QPointF(2, 2),
                      Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
                      Qt.KeyboardModifier.NoModifier)
    bar.btn_add.mousePressEvent(_ev)
    check("右键图标不触发（只认左键）", not fired)
    check("📌 在图标列最左", bar._icon_btns[0] is bar.btn_pin)
    check("📌 初态跟随（不高亮）", bar.btn_pin._color.name().lower()
          == design.DS.TEXT_FAINT.lower())
    bar.set_pinned(True)
    check("📌 固定后高亮主色", bar.btn_pin._color.name().lower()
          == design.DS.PRIMARY.lower())
    bar.set_pinned(False)
    bar.refresh_icons()          # 不应抛异常
    check("refresh_icons 可跑", True)

    # ---- 新配色令牌（D8）----
    check("浅色主色=苹果绿 #34C759", design.LIGHT.PRIMARY == "#34C759")
    check("主色底用深墨绿字（对比度）", design.LIGHT.SEL_TEXT == "#052E16")
    check("深色主色=金", design.DARK.PRIMARY == "#E3C15E")
    check("联想色令牌存在", bool(design.LIGHT.AI_HI and design.DARK.AI_HI))

    # ---- 紧凑排版（D7）----
    check("字号 zh11/en9", (design._Base.FS_ZH, design._Base.FS_EN) == (11, 9))
    check("条宽上限 560", design._Base.BAR_MAX_W == 560)

    # ---- 加词条弹窗（D5）----
    saved: list = []
    engine.lexicons.save_custom = lambda p: saved.append(p) or 0
    add = AddWordPopup(engine)
    got: list = []
    add.added.connect(lambda zh: got.append(zh))
    add.ed_zh.setText("手感测试词")
    add.ed_en.setText("SmokeTerm")
    add._submit()
    check("加词条成功信号", got == ["手感测试词"])
    check("加词条落盘调用", bool(saved))
    check("加词条词库可见",
          any(t.zh == "手感测试词" for t in engine.lexicons.all_terms))
    add.ed_zh.clear()
    add._submit()                # 空中文 → 应在 status 报错不崩
    check("加词条空输入拦截", "不能为空" in add.status.text())
    add.close()

    # ---- 行业弹窗（D3 一级）----
    ind = IndustryPopup("mechanical")
    picked: list = []
    ind.picked.connect(lambda k: picked.append(k))
    ind._on_pick("electrical")
    check("行业弹窗选择信号", picked == ["electrical"])
    ind.close()

    # ---- AI 弹窗（D3）----
    ai = AiPopup({"enabled": False, "base_url": "http://x", "model": "m",
                  "api_key": ""})
    outs: list = []
    ai.saved.connect(lambda d: outs.append(d))
    ai.chk_llm.setChecked(True)
    ai._save()
    check("AI 弹窗保存信号", bool(outs) and outs[0]["enabled"] is True)
    ai.close()

    # ---- 关于弹窗（D3）----
    ab = AboutPopup(version="2.2.0")
    from PySide6.QtWidgets import QLabel
    texts = [w.text() for w in ab.findChildren(QLabel)]
    check("关于弹窗含 RlonIME/版本",
          any("RlonIME" in t for t in texts)
          and any("2.2.0" in t for t in texts))
    ab.close()

    # ---- 多屏跟随（v2.3.1）：贴边一律按「光标所在屏幕」 ----
    import ui.bar as bar_mod
    from PySide6.QtCore import QRect

    class _FakeScreen:
        def __init__(self, rect):
            self._r = rect

        def availableGeometry(self):
            return self._r

    bar.adjustSize()
    bw, bh = bar.width(), bar.height()

    # ① 主屏右侧副屏：不得被拉回主屏（旧版 bug：按主屏右缘夹取）
    bar_mod.get_caret_info = lambda: bar_mod.CaretInfo(2500, 300, "caret")
    bar._screen_for_point = lambda x, y: _FakeScreen(QRect(1920, 0, 2560, 1440))
    bar.follow_caret()
    check("副屏光标不再被拉回主屏", bar.x() == 2502 and bar.y() == 320,
          f"pos=({bar.x()},{bar.y()}) bar={bw}x{bh}")

    # ② 副屏右缘贴边：按副屏右边界收敛
    bar_mod.get_caret_info = lambda: bar_mod.CaretInfo(4400, 300, "caret")
    bar.follow_caret()
    check("副屏右缘贴边按副屏收敛", bar.x() == 4479 - bw - 6,
          f"x={bar.x()} 期望 {4479 - bw - 6}")

    # ③ 无屏幕信息（多屏拼接的凹口）：就近归到最近一块屏，绝不悬空
    #    （v2.7 QA：以前原样放在空隙里 → 任何屏幕都不属于，用户看到"条子消失"）
    bar._screen_for_point = lambda x, y: None
    bar_mod.get_caret_info = lambda: bar_mod.CaretInfo(2500, 300, "caret")
    bar.follow_caret()
    _geo = QApplication.primaryScreen().availableGeometry()
    check("布局空隙时就近归一屏", _geo.contains(bar.pos()),
          f"pos=({bar.x()},{bar.y()}) geo={_geo}")

    # ④ 底部翻边：转贴光标上方（按所在屏）
    bar._screen_for_point = lambda x, y: _FakeScreen(QRect(0, 0, 1920, 1080))
    bar_mod.get_caret_info = lambda: bar_mod.CaretInfo(500, 1040, "caret")
    bar.follow_caret()
    check("底部翻边到上方", bar.y() == 1040 - bh - 14,
          f"y={bar.y()} 期望 {1040 - bh - 14}")

    # ---- 托盘图标（D9）----
    from main import make_tray_icon
    ic = make_tray_icon()
    check("托盘图标非空", not ic.isNull())

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项 -> {FAILED}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
