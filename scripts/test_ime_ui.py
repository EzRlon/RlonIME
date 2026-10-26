"""v3.0 输入法合格性 —— 候选条侧接线回归

覆盖（都是 v3.0 修的硬伤，防止再退回）：
  1. 组合态编辑键：上屏「文字」在前、原按键在后（不是吐拼音）
  2. 精确命中才取候选，前缀命中直出原文（与空格同一口径）
  3. 组合态大写字母：先词后字母
  4. 中 / EN 状态徽标：attach 即显示、toggle 事件即时同步、点击可切
  5. 翻译结果：术语待核标签、点击复制全文、时长随长度自适应

    python scripts/test_ime_ui.py
"""
from __future__ import annotations

import os
import sys
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
    from PySide6.QtWidgets import QApplication
    import core.ime as imemod
    from core.engine import TermEngine
    from core.models import Candidate, SceneContext, Term
    from scene.detector import SceneDetector
    from ui.bar import CandidateBar
    from ui.inserter import Inserter

    app = QApplication([])
    engine = TermEngine()

    def build(words: list[str], mtype: str = "prefix") -> CandidateBar:
        engine.search = lambda q, s, limit=None, cross_profession=False: [
            Candidate(term=Term(zh=w, en=""), match_type=mtype) for w in words]
        b = CandidateBar(engine, SceneDetector(), Inserter())
        b.sent: list[str] = []
        b._emit_text = lambda text: (b.sent.append(text), True)[1]
        return b

    print("[1] 组合态编辑键：文字在前，原按键在后")
    sent_keys: list[int] = []
    real_send_vk = imemod.send_vk
    imemod.send_vk = lambda vk, up=False: (sent_keys.append(vk), True)[1]
    try:
        b = build(["沉孔"])
        b.candidates = [Candidate(term=Term(zh="沉孔", en=""), match_type="full_pinyin")]
        b._exact_match = True
        b._commit_nav("chenkong", 0x25)          # ←
        ck("编辑键：先上屏候选词", b.sent == ["沉孔"], b.sent)
        ck("编辑键：随后补发原按键", sent_keys == [0x25, 0x25], sent_keys)
        ck("编辑键：候选与面板已收起",
           b.candidates == [] and not b.isVisible())

        b2 = build(["沉孔"])
        b2.candidates = [Candidate(term=Term(zh="沉孔", en=""), match_type="prefix")]
        b2._exact_match = False                   # 前缀命中不替用户选词
        b2._commit_nav("chenk", 0x27)
        ck("编辑键：非精确命中直出原文", b2.sent == ["chenk"], b2.sent)
    finally:
        imemod.send_vk = real_send_vk

    print("[2] 组合态大写字母：先词后字母")
    b = build(["沉孔"])
    b.candidates = [Candidate(term=Term(zh="沉孔", en=""), match_type="full_pinyin")]
    b._exact_match = True
    b._commit_char("chen", "K")
    ck("大写：顺序为 词 → 大写字母", b.sent == ["沉孔", "K"], b.sent)

    print("[3] 中 / EN 状态徽标")

    class FakeIme:
        enabled = True

        def set_enabled(self, on):
            self.enabled = on

    b = build(["沉孔"])
    b.attach_ime(FakeIme())
    ck("attach 即显示「中」", b.state_label.text() == "中", b.state_label.text())
    b.on_ime_events = b.on_ime_events  # 保持接口
    b._update_state_label(False)
    ck("enabled=False 显示 EN", b.state_label.text() == "EN", b.state_label.text())
    b._toggle_state()
    ck("点徽标把开关翻面（事件回流后标签同步）", b.ime.enabled is False,
       b.ime.enabled)
    b._update_state_label()
    ck("回流后标签为 EN", b.state_label.text() == "EN", b.state_label.text())
    # toggle 事件回流
    b.ime.enabled = False
    b._update_state_label(False)
    ck("EN 态 tooltip 说明双击 Shift",
       "双击 Shift" in b.state_label.toolTip(), b.state_label.toolTip())

    print("[4] 翻译结果：术语待核 / 点击复制 / 时长自适应")

    class R:
        ok = True
        source = "llm"
        text = "The flange is welded to the frame."
        error = ""
        fixes = []
        unresolved = ["法兰(应作 法兰盘)", "焊接(应作 焊合)"]
        confidence = 0.8
        source_label = "AI 翻译"

    b = build([])
    b.show_result(R())
    ck("结果态显示「术语待核 2」",
       "术语待核 2" in b._preview_full, b._preview_full)
    ck("复制用的是全文（不含标签）",
       b._result_text == R.text, b._result_text)
    before = b._hide_timer.remainingTime()
    ck("长译文停留时间 ≥ 4.2s", before >= 4000, before)
    b._copy_result()
    ck("点击复制写入剪贴板",
       QApplication.clipboard().text() == R.text,
       QApplication.clipboard().text())

    b2 = build([])

    class Short:
        ok = True
        source = "memory_exact"
        text = "沉孔"
        error = ""
        fixes = []
        unresolved = []
        confidence = 1.0
        source_label = "记忆库"

    b2.show_result(Short())
    ck("短译文不被拉长到 14s", b2._hide_timer.remainingTime() <= 14000,
       b2._hide_timer.remainingTime())
    ck("无术语问题时标签为空",
       "术语" not in b2._preview_full, b2._preview_full)

    print()
    print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("  失败项:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
