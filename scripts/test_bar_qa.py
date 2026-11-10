"""D05 / D10 / D11 候选条缺陷修复回归（必须真实平台跑，别用 offscreen）。

窗口标志、show/hide、QPropertyAnimation 都依赖真实平台，沿用
scripts/test_bar_move.py 的 QApplication([]) 写法；不设 QT_QPA_PLATFORM。

    python scripts/test_bar_qa.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          f"{'' if cond else '  ' + str(extra)}")


def main() -> int:
    from PySide6.QtCore import QAbstractAnimation, QPropertyAnimation
    from PySide6.QtWidgets import QApplication
    from core.config import get_config
    from core.engine import TermEngine
    from core.models import Candidate, Term
    from scene.detector import SceneDetector
    from ui import design
    from ui.bar import CandidateBar
    from ui.inserter import Inserter

    app = QApplication([])

    def settle(ms: int = 200) -> None:
        """等入场动画跑完（show_bar 有 120ms 的 pos/opacity 动画）。"""
        end = time.time() + ms / 1000.0
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    def make_bar(words: list[str]) -> CandidateBar:
        # 每个用例重置单例配置，避免上一个用例把 bar_pinned 写脏
        get_config().ui.bar_pinned = False
        get_config().ui.bar_pos = []
        eng = TermEngine()

        def search(q, s, limit=None, cross_profession=False, mode="auto"):
            return [Candidate(term=Term(zh=w, en="Probe", freq=100,
                                        profession="mechanical"),
                              match_type="prefix") for w in words]

        eng.search = search
        b = CandidateBar(eng, SceneDetector(), Inserter())
        b.ui_persist = lambda sec, vals: None
        b.show_input("k")
        b.refresh_candidates("k")
        app.processEvents()
        return b

    # ---------------- [1] D05：close_panel 统一收起语义 ----------------
    print("[1] D05 close_panel 统一置 collapsed / 后台结果不弹回")
    b = make_bar(["沉孔", "埋头孔", "螺纹孔"])
    ck("前置：条子已显示", b.isVisible() is True)
    b.close_panel()
    app.processEvents()
    ck("close_panel 后 collapsed=True", b.collapsed is True, b.collapsed)
    b.show_hint("后台异步提示不该弹条", "ok")
    app.processEvents()
    ck("close_panel 后 show_hint 不弹条", b.isVisible() is False)

    class _R:
        ok = True
        text = "A counterbore shall be machined"
        source = "free"
        source_label = "免费翻译"
        fixes: list = []
        unresolved: list = []
        confidence = 0
        error = ""

    b.show_result(_R())
    app.processEvents()
    ck("close_panel 后 show_result 不弹条", b.isVisible() is False)
    # show_input 必须复位 collapsed（否则用户再也打不出候选条）
    b.show_input("k")
    ck("show_input 复位 collapsed=False", b.collapsed is False)
    b.close_panel()
    app.processEvents()

    # ---------------- [2] D10：动画单实例 / close 停动画 ----------------
    print("[2] D10 入场动画复用单实例、close 停动画并复位 opacity")
    b = make_bar(["沉孔", "埋头孔"])
    b._animate_in()
    n1 = len(b.findChildren(QPropertyAnimation))
    a1, p1 = b._anim, b._anim_pos
    ck("首次 _animate_in 建好两个动画", a1 is not None and p1 is not None)
    b._animate_in()                       # 120ms 内再来一次（连续提示）
    app.processEvents()
    n2 = len(b.findChildren(QPropertyAnimation))
    ck("复用同一 opacity 动画实例", b._anim is a1)
    ck("复用同一 pos 动画实例", b._anim_pos is p1)
    ck("连续两次 _animate_in 不新增动画对象", n2 == n1, (n1, n2))
    running = [x for x in b.findChildren(QPropertyAnimation)
               if x.state() == QAbstractAnimation.State.Running]
    ck("无并发堆积（活动动画 <= 2）", len(running) <= 2, len(running))
    # 提示中 hide：动画必须停 + opacity 复位，否则下次显示像"消失"
    b.show_input("k")
    b._animate_in()
    settle(40)                            # 让 opacity 处在中间值
    b.close_panel()
    app.processEvents()
    # 平台把 opacity 量化到 8 位（0.99 -> 252/255≈0.9882），用像素级容差
    ck("close_panel 后 opacity 复位为 DS.OPACITY",
       abs(b.windowOpacity() - design.DS.OPACITY) < 0.01, b.windowOpacity())
    ck("close_panel 后 opacity 动画已停",
       b._anim.state() != QAbstractAnimation.State.Running, b._anim.state())
    ck("close_panel 后 pos 动画已停",
       b._anim_pos.state() != QAbstractAnimation.State.Running,
       b._anim_pos.state())
    b.show_input("k")
    b._animate_in()                       # 隐藏后再次入场仍可用
    ck("隐藏后再次入场仍复用同一实例", b._anim is a1 and b._anim_pos is p1)
    b.close_panel()

    # ---------------- [3] D11：极窄 _avail_w 分页 ----------------
    print("[3] D11 极窄 _avail_w 不空页 / 首枚超宽只占 1 枚 / 口径统一")
    long = ["法兰盘与轴承座的同轴度要求" * 3] * 9
    b = make_bar(long)
    b._avail_w = 140
    pages = b._compute_pages(140)
    ck("有分页结果", len(pages) > 0, pages)
    ck("无空页（每页 >= 1 枚）", all(cnt >= 1 for _, cnt in pages), pages)
    ck("页数 <= 候选数（不死循环）", 0 < len(pages) <= len(b.candidates),
       (len(pages), len(b.candidates)))
    ck("分页覆盖全部候选",
       sum(cnt for _, cnt in pages) == len(b.candidates), pages)
    ck("首枚超宽时每页只占 1 枚", all(cnt == 1 for _, cnt in pages), pages)
    ck("页起点严格递增",
       all(pages[i][0] < pages[i + 1][0] for i in range(len(pages) - 1)),
       pages)
    # 口径统一：_chip_width(140) 应等于 set_content(max_w=140) 后 c.width()
    probe = b._chip_width(0, 140)
    chip = b.chips[0]
    chip.set_content(0, b.candidates[0].zh, max_w=140)
    ck("_chip_width 与 set_content 后 c.width() 同口径",
       probe == chip.width(), (probe, chip.width()))
    old_measure = b.chips[0].measure(0, b.candidates[0].zh)
    ck("窄条下预估 <= _avail_w（不再按 cap=400 的 400px 算）",
       probe <= 142 and probe < old_measure, (probe, old_measure))
    b.close_panel()

    # ---------------- [4] D11：多页翻页 width() 不跳 ----------------
    print("[4] D11 多页翻页 width() 不跳")
    K = ["折弯系数", "K因子", "卡扣", "孔距", "卡簧", "块",
         "开口销", "开模", "卡"]
    b = make_bar(K)
    settle(200)
    ck("确实分成多页", len(b._pages) > 1, len(b._pages))
    w0 = b.width()
    widths = [w0]
    for _ in range(len(b._pages) - 1):
        b.page_move(1)
        app.processEvents()
        widths.append(b.width())
    ck("逐页下翻 width() 恒定不跳", all(w == w0 for w in widths), widths)
    for _ in range(len(b._pages) - 1):
        b.page_move(-1)
        app.processEvents()
    ck("逐页回翻 width() 仍不变", b.width() == w0, (w0, b.width()))
    b.close_panel()

    print()
    print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("  失败项:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
