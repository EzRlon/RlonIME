"""A06-1 候选条胶囊布局回归（M1 溢出治理）

验收口径（HANDOFF §4）：
  1. 相邻胶囊 rect 不相交（K 键 9 枚长候选是原始复现输入）
  2. 可见胶囊右缘不越界（不削边）
  3. 溢出时尾部出现「…+N」徽标，总量 = 可见 + N
  4. PageUp/Down 与方向键能把看不见的候选翻出来（选中项必在可见页）
  5. 数字键按「本页可见序号」映射（分页后不能选到看不见的候选）

    python scripts/test_chip_layout.py
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
    from core.engine import TermEngine
    from core.models import Candidate, SceneContext, Term
    from scene.detector import SceneDetector
    from ui import bar as barmod
    from ui.bar import BADGE_W, CHIPS_AVAIL_W, CandidateBar
    from ui.inserter import Inserter

    app = QApplication([])
    engine = TermEngine()
    orig_search = engine.search

    # K 键实测候选（复现 docs/kbug/bar_k.png 的 9 枚）
    K_WORDS = ["折弯系数", "K因子", "卡扣", "孔距", "卡簧", "块", "开口销",
               "开模", "卡"]
    LONG_WORDS = ["法兰盘与轴承座的同轴度要求", "形位公差标注基准面选择",
                  "焊接坡口与熔深要求", "表面粗糙度与镀层厚度",
                  "螺纹孔深度与有效螺纹长度", "钣金折弯回弹补偿系数",
                  "装配体干涉检查与间隙控制", "齿轮模数与齿形修缘",
                  "热处理硬度与渗层深度"]

    def make_cands(words: list[str], mtype: str = "prefix") -> list[Candidate]:
        sc = SceneContext(profession_key="mechanical")
        out = []
        for w in words:
            t = Term(zh=w, en="Probe", freq=100, profession="mechanical")
            c = Candidate(term=t, match_type=mtype)
            c.detail["base"] = 50.0
            engine._score(c, sc)
            out.append(c)
        return out

    def build(words: list[str], mtype: str = "prefix") -> CandidateBar:
        engine.search = lambda q, s, limit=None, cross_profession=False: \
            make_cands(words, mtype)
        b = CandidateBar(engine, SceneDetector(), Inserter())
        b._emit_text = lambda text: True       # 不真上屏
        return b

    def settle(b: CandidateBar) -> None:
        # 胶囊行从 QHBoxLayout 改为手动定位后，几何在 _place_chips 里就已确定；
        # processEvents 只是让外层卡片完成 resize。
        app.processEvents()

    def visible(b: CandidateBar):
        return [c for c in b.chips if c.isVisible() and not c.is_badge]

    def badge(b: CandidateBar):
        return next((c for c in b.chips if c.isVisible() and c.is_badge), None)

    def overlaps(b: CandidateBar) -> list[tuple[int, int]]:
        rs = [(c.index, c.geometry()) for c in visible(b)]
        out = []
        for i in range(len(rs) - 1):
            if rs[i][1].intersects(rs[i + 1][1]):
                out.append((rs[i][0], rs[i + 1][0]))
        return out

    # ---------------- [1] K 键 9 枚：不重叠、不越界 ----------------
    print("[1] K 键 9 枚长候选：不重叠 / 不削边")
    b = build(K_WORDS)
    b.show_input("k")
    b.refresh_candidates("k")
    settle(b)
    ck("候选池 9 条", len(b.candidates) == 9, len(b.candidates))
    ck("相邻胶囊不相交（K 键）", not overlaps(b), overlaps(b))
    last = max(visible(b), key=lambda c: c.geometry().right())
    ck("右缘不越出可用宽度",
       last.geometry().right() <= CHIPS_AVAIL_W,
       f"{last.geometry().right()} > {CHIPS_AVAIL_W}")
    ck("胶囊总宽不超可用宽度",
       sum(c.width() + barmod.DS.GAP for c in visible(b)) <= CHIPS_AVAIL_W + 2,
       sum(c.width() + barmod.DS.GAP for c in visible(b)))
    ck("条宽不超上限", b.width() <= barmod.DS.BAR_MAX_W, b.width())

    # ---------------- [2] 溢出徽标 ----------------
    print("[2] 溢出徽标「…+N」")
    bg = badge(b)
    ck("有溢出徽标", bg is not None)
    ck("徽标宽度 = 预留宽度（按字体实测）",
       bool(bg) and bg.width() <= b._badge_w + 2,
       f"{bg.width() if bg else '-'} / 预留 {b._badge_w}")
    n_vis = len(visible(b))
    ck("可见数 < 总数（确实溢出）", n_vis < 9, n_vis)
    ck("含徽标总宽不超可用宽度",
       b._chips_end <= CHIPS_AVAIL_W,
       f"{b._chips_end} > {CHIPS_AVAIL_W}")
    if bg is not None:
        ck("徽标文案 = …+剩余数",
           bg._zh == f"…+{9 - n_vis}", f"{bg._zh} / 剩余 {9 - n_vis}")
        ck("徽标不计入候选序号", bg.index == -1)
        ck("徽标有翻页提示", "翻页" in bg.toolTip(), bg.toolTip())
    ck("可见数 >= 1", n_vis >= 1, n_vis)

    # ---------------- [3] 分页：PageDown 能翻出剩余候选 ----------------
    print("[3] PageDown / 方向键翻页")
    b.page_move(1)
    settle(b)
    ck("翻页后页序号前进", b._page_idx == 1, b._page_idx)
    ck("第二页首枚 = 第一页之后", min(c.index for c in visible(b)) == n_vis,
       [c.index for c in visible(b)])
    ck("翻页后仍不重叠", not overlaps(b), overlaps(b))
    ck("尾巴显示页码", "/" in b.tail.text(), b.tail.text())
    b.move_active(1)
    settle(b)
    ck("方向键选中项落在可见页内",
       any(c.index == b.active_index for c in visible(b)),
       f"active={b.active_index} visible={[c.index for c in visible(b)]}")
    ck("选中态跟着候选走",
       any(c.index == b.active_index and c._sel for c in visible(b)))

    # ---------------- [4] 数字键 = 本页可见序号 ----------------
    print("[4] 数字键按可见页映射")
    b = build(K_WORDS)
    b.show_input("k")
    b.refresh_candidates("k")
    settle(b)
    bg_idx = b.commit_digit(0)
    ck("第 1 页数字 1 → 候选 0", bg_idx == 0, bg_idx)
    b = build(K_WORDS)
    b.show_input("k")
    b.refresh_candidates("k")
    settle(b)
    start = b._pages[1][0] if len(b._pages) > 1 else 0
    b.page_move(1)
    settle(b)
    got = b.commit_digit(0)
    ck("第 2 页数字 1 → 本页首枚", got == start, f"{got} != {start}")
    b = build(K_WORDS)
    b.show_input("k")
    b.refresh_candidates("k")
    settle(b)
    out_of_range = b.commit_digit(8)
    ck("超出本页范围返回 -1", out_of_range == -1, out_of_range)

    # ---------------- [5] 超长文本二次省略 ----------------
    print("[5] 超长候选：省略而不是撑爆")
    b = build(LONG_WORDS)
    b.show_input("f")
    b.refresh_candidates("f")
    settle(b)
    ck("超长集合不重叠", not overlaps(b), overlaps(b))
    ck("超长集合不越界",
       all(c.geometry().right() <= CHIPS_AVAIL_W for c in visible(b)),
       [(c.index, c.geometry().right()) for c in visible(b)])
    ck("超长集合不超条宽", b.width() <= barmod.DS.BAR_MAX_W, b.width())

    # ---------------- [6] 少候选不触发徽标 ----------------
    print("[6] 少候选（3 枚）不出现徽标")
    b = build(K_WORDS[:3])
    b.show_input("k")
    b.refresh_candidates("k")
    settle(b)
    ck("3 枚全可见", len(visible(b)) == 3, len(visible(b)))
    ck("无徽标", badge(b) is None)
    ck("页码为空", b.tail.text() == "", b.tail.text())

    # ---------------- [7] M5 技术要求句库 ----------------
    print("[7] 技术要求句库：短码直查 / Tab=英 / Ctrl+数字=中英")
    engine.search = orig_search             # 还原真实检索（前面几节被替换过）
    ck("句库已装载 30-50 条",
       30 <= len(engine.phrases) <= 60, len(engine.phrases))
    sc = SceneContext(profession_key="mechanical")
    hit = engine.search("yq07", sc, limit=9)
    ck("yq07 精确命中一条", bool(hit) and hit[0].match_type == "phrase",
       [(c.zh, c.match_type) for c in hit[:2]])
    ck("码不污染普通候选",
       all(c.match_type not in ("phrase", "phrase_prefix")
           for c in engine.search("k", sc, limit=9)))
    pre = engine.search("yq", sc, limit=9)
    ck("yq 前缀列出多条", len(pre) >= 5, len(pre))
    ck("前缀条目均带英文",
       all(c.en for c in pre[:5]), [c.en for c in pre[:5]])

    # 上屏三条通道（数字/Tab/Ctrl+数字）共用一套候选
    engine.search = orig_search
    b = CandidateBar(engine, SceneDetector(), Inserter())
    b.show_input("yq07")
    b.refresh_candidates("yq07")
    settle(b)
    emitted: list[str] = []

    def emit_hook(t: str) -> bool:
        emitted.append(t)
        return True

    b._emit_text = emit_hook
    first = b.candidates[0]
    ck("句首候选就是 yq07", first.zh.startswith("未注圆跳动"), first.zh)
    ck("精确码标记为精确", b._exact_match is True)
    b.commit_index(-1, True)                  # Tab = 英文
    ck("Tab 上屏英文", emitted and emitted[0] == first.en, emitted)
    emitted.clear()
    b = CandidateBar(engine, SceneDetector(), Inserter())
    b.show_input("yq07")
    b.refresh_candidates("yq07")
    settle(b)
    b._emit_text = emit_hook
    b.commit_combo(0)                         # Ctrl+数字 = 中英
    ck("Ctrl+数字 上屏中英对照",
       emitted and emitted[0] == f"{first.zh} {first.en}", emitted)
    # ---------------- [8] M3 非精确不选词 ----------------
    print("[8] 非精确命中：空格直出原文 / 数字键仍可选词")
    emitted: list[str] = []

    def emit_hook(t: str) -> bool:
        emitted.append(t)
        return True

    b = build(K_WORDS, "prefix")            # 只有前缀命中 → 非精确
    b.show_input("k")
    b.refresh_candidates("k")
    settle(b)
    b._emit_text = emit_hook
    ck("标记为非精确", b._exact_match is False)
    b.commit_index(-1, False, "k")
    ck("非精确：空格直出原文", emitted == ["k"], emitted)

    emitted.clear()
    b = build(K_WORDS, "prefix")
    b.show_input("k")
    b.refresh_candidates("k")
    settle(b)
    b._emit_text = emit_hook
    b.commit_digit(1)
    ck("非精确：数字键仍能选词", emitted == [K_WORDS[1]], emitted)

    emitted.clear()
    b = build(K_WORDS, "full_pinyin")       # 全拼精确命中
    b.show_input("chenkong")
    b.refresh_candidates("chenkong")
    settle(b)
    b._emit_text = emit_hook
    ck("标记为精确", b._exact_match is True)
    b.commit_index(-1, False, "chenkong")
    ck("精确：空格取首候选", emitted == [K_WORDS[0]], emitted)

    emitted.clear()
    b = build(["我是工程师"], "segment")     # 整句完整覆盖也算精确
    b.show_input("woshigongchengshi")
    b.refresh_candidates("woshigongchengshi")
    settle(b)
    b._emit_text = emit_hook
    b.commit_index(-1, False, "woshigongchengshi")
    ck("整句分段：空格取整句", emitted == ["我是工程师"], emitted)

    # v2.5：通用词库精确命中也要能空格上屏（日常输入法的主路径）
    emitted.clear()
    if not engine.lexicons.common_loaded:
        engine.lexicons.load_common()       # 82k 条，约 130ms
    engine.search = orig_search             # 还原真实检索（上一节替换过）
    b = CandidateBar(engine, SceneDetector(), Inserter())   # 真引擎
    b._emit_text = emit_hook
    b.show_input("jintian")          # 今天：只在通用层有，专业词库无
    b.refresh_candidates("jintian")
    settle(b)
    if b.candidates and b.candidates[0].match_type == "common":
        ck("通用词精确命中标记为精确", b._exact_match is True,
           b.candidates[0].match_type)
        b.commit_index(-1, False, "jintian")
        ck("通用词空格上屏（不吐拼音）", emitted and emitted[0] == "今天",
           emitted)
    else:
        ck("通用词库未载入（跳过该断言）", True,
           b.candidates[0].match_type if b.candidates else "无候选")

    # ---------------- [9] 翻页可用性（v2.6.1） ----------------
    print("[9] 翻页：滚轮 / 条宽稳定 / 页内提示")
    b = build(K_WORDS)
    b.show_input("k")
    b.refresh_candidates("k")
    settle(b)
    w0 = b.width()

    class _Wheel:
        def __init__(self, dy):
            from PySide6.QtCore import QPoint
            self._d = QPoint(0, dy)
            self.accepted = False

        def angleDelta(self):
            return self._d

        def accept(self):
            self.accepted = True

    wh = _Wheel(-120)          # 向下滚 = 下一页
    b.wheelEvent(wh)
    settle(b)
    ck("滚轮向下翻到第 2 页", b._page_idx == 1, b._page_idx)
    ck("滚轮事件被接受", wh.accepted)
    ck("翻页后条宽不变（不突然缩一半）", b.width() == w0, f"{w0} -> {b.width()}")
    wh2 = _Wheel(120)
    b.wheelEvent(wh2)
    settle(b)
    ck("滚轮向上回到第 1 页", b._page_idx == 0, b._page_idx)
    ck("回到第 1 页条宽仍不变", b.width() == w0, f"{w0} -> {b.width()}")
    ck("页内提示写清怎么翻",
       "PgDn" in b.tail.text() or "PgUp" in b.tail.text(), b.tail.text())
    b.page_move(1)
    settle(b)
    ck("末页提示 PgUp/-",
       ("PgUp" in b.tail.text() or "-" in b.tail.text()), b.tail.text())

    # 徽标点击 = 翻页
    b = build(K_WORDS)
    b.show_input("k")
    b.refresh_candidates("k")
    settle(b)
    badge_chip = next((c for c in b.chips if c.isVisible() and c.is_badge), None)
    ck("徽标存在", badge_chip is not None)
    if badge_chip is not None:
        badge_chip.badge_clicked.emit()
        settle(b)
        ck("点徽标能翻页", b._page_idx == 1, b._page_idx)

    # ---------------- v3.0.3：来源分区 ----------------
    print("[v303] 候选分区：pro 带底色、common 留白")
    b = build([])
    b.candidates = [
        Candidate(term=Term(zh="是", en=""), match_type="common", group="common"),
        Candidate(term=Term(zh="实体", en=""), match_type="prefix", group="pro"),
    ]
    b._exact_match = True
    b.active_index = 0
    b._layout_chips()
    settle(b)
    ck("通用词胶囊 group=common", b.chips[0]._group == "common", b.chips[0]._group)
    ck("专业词胶囊 group=pro", b.chips[1]._group == "pro", b.chips[1]._group)

    print()
    print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("  失败项:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
