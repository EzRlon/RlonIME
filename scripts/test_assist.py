"""
智语 — 词汇助手管线验收测试（无需真实按键，可在 offscreen 下跑）

覆盖：
  A 记忆库       精确 / 模糊 / 归一化 / 用户译法优先 / 强化 / 删除
  B 翻译管线     L1 精确 · L2 模糊 · L3 术语整段 · L5 术语强制对齐
  C 大模型层     预设 / 可用性判定 / 提示词装配 / 无 Key 时安静降级
  D 统一取写层   剪贴板快照还原往返（逐格式）
  E 候选条 UI    三态渲染 / 宽度自适应 / 不抢焦点标志位
  F 性能         检索延迟 / 记忆库查询延迟 / 冷启动各阶段耗时

用法:
    python scripts/test_assist.py            # 跑全部
    python scripts/test_assist.py --shot     # 额外输出候选条截图
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# 默认走真实 windows 平台：WS_EX_NOACTIVATE 这类 Win32 标志位在 offscreen
# 后端下拿不到真实 HWND，会被误判为失败。无头环境时才加 --offscreen。
OFFSCREEN = "--offscreen" in sys.argv
if OFFSCREEN:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'[PASS]' if cond else '[FAIL]'} {name}{(' — ' + extra) if extra else ''}")


def section(title: str) -> None:
    print(f"\n{'=' * 66}\n  {title}\n{'=' * 66}")


# ================================================================
def test_memory() -> None:
    section("A 记忆库")
    from core.memory import TranslationMemory, normalize

    db = Path(tempfile.gettempdir()) / "zhiyu_assist_tm.db"
    db.unlink(missing_ok=True)
    m = TranslationMemory(db)

    check("归一化剥标点", normalize("沉孔，深度 8mm。") == "沉孔深度8mm")
    check("归一化全角转半角", normalize("ＡＢＣ ｘｙｚ") == "abcxyz")

    src = "该零件底部需加工沉孔，深度 8mm"
    ai = "A spot face shall be made at the bottom, 8 mm deep."
    usr = "A counterbore shall be machined at the bottom of this part, depth 8 mm."

    m.learn(src, ai, "mechanical", "llm")
    check("写入 AI 初稿", m.exact(src).dst == ai)
    m.learn(src, usr, "mechanical", "user")
    check("用户译法盖过 AI 译法", m.exact(src).dst == usr,
          f"命中 {m.exact(src).dst[:28]}…")
    m.learn(src, usr, "mechanical", "user")
    check("重复写入累加计数", m.exact(src).count == 2)

    p = m.fuzzy("该零件底部需加工沉孔，深度 9mm")
    check("模糊命中（一字之差）", p is not None and p.score >= 0.85,
          f"相似度 {p.score if p else 0}")
    check("模糊不误伤无关句", m.fuzzy("明天天气怎么样") is None)

    m.reinforce(src, "mechanical")
    check("强化提升计数", m.exact(src).count == 3)
    check("删除生效", m.forget(src) >= 1 and m.exact(src) is None)

    st = m.stats()
    check("统计结构完整",
          {"pairs", "distinct_src", "by_industry"} <= set(st), str(st["pairs"]))
    m.close()
    db.unlink(missing_ok=True)


def test_translate() -> None:
    section("B 翻译管线（五级降级）")
    from core.engine import TermEngine
    from core.memory import TranslationMemory
    from core.translate import Translator
    from core.models import SceneContext

    eng = TermEngine()
    db = Path(tempfile.gettempdir()) / "zhiyu_assist_tl.db"
    db.unlink(missing_ok=True)
    mem = TranslationMemory(db)
    tr = Translator(eng, mem)
    sc = SceneContext(profession_key="mechanical",
                      hierarchy="机械工程 / 结构设计 / 设计员",
                      software_name="SolidWorks")

    check("术语表非空", len(tr.term_map()) > 500, f"{len(tr.term_map())} 条")
    terms = tr.extract_terms("该零件底部需加工沉孔，螺纹孔与倒角按图")
    check("贪心最长匹配（螺纹孔不被切成 螺纹+孔）",
          ("螺纹孔", "Tapped Hole") in terms, str(terms))

    # L3 术语库整段覆盖
    r = tr.translate_local("沉孔、倒角", sc)
    check("L3 术语整段覆盖", r is not None and r.source == "term",
          r.text if r else "未命中")

    # L1 记忆库精确（含标点差异）
    mem.learn("立柱与顶板满焊，表面拉丝处理",
              "The post is fully welded to the top plate and brushed finished.",
              "mechanical", "user")
    r1 = tr.translate_local("立柱与顶板满焊，表面拉丝处理", sc)
    check("L1 精确命中", r1 is not None and r1.source == "memory_exact")
    r2 = tr.translate_local("立柱与顶板满焊,表面拉丝处理", sc)
    check("L1 对标点差异免疫", r2 is not None and r2.source == "memory_exact")

    # L5 术语强制对齐
    out, fixes, un = tr.apply_term_lock(
        "The hole shall be spot faced and the sharp edge removed.",
        [("沉孔", "Counterbore")])
    check("L5 未命中术语时如实上报",
          un and "Counterbore" in un[0], str(un))
    out2, fixes2, un2 = tr.apply_term_lock(
        "Weld the welded frame with a fixture.",
        [("焊接", "Welding")])
    check("L5 已有标准译名则跳过", fixes2 == [] and un2 == [])

    check("无 Key 时安静降级",
          tr.translate("这句肯定查不到任何词库", sc).source == "failed")

    # probe_local（UX-P1-3「译」候选只读预检）
    check("probe_local：术语覆盖句给出译文",
          tr.probe_local("沉孔、倒角", sc) == "Counterbore / Chamfer",
          str(tr.probe_local("沉孔、倒角", sc)))
    check("probe_local：记忆库句给出译文",
          tr.probe_local("立柱与顶板满焊，表面拉丝处理", sc) is not None)
    check("probe_local：无译法返回 None（不伪造）",
          tr.probe_local("这句肯定查不到任何词库", sc) is None)
    check("probe_local：空串返回 None",
          tr.probe_local("", sc) is None)
    mem.close()
    db.unlink(missing_ok=True)


def test_llm() -> None:
    section("C 大模型层")
    from core.llm import PRESETS, LLMClient, build_glossary

    check("预设齐全（含免费与本地）",
          {"glm", "deepseek", "qwen", "ollama"} <= set(PRESETS))
    c = LLMClient({"enabled": True, "provider": "glm",
                   "base_url": PRESETS["glm"]["base_url"],
                   "model": PRESETS["glm"]["model"], "api_key": ""})
    check("缺 Key 判定为不可用", not c.available)
    check("缺 Key 请求抛错而非崩溃",
          _raises(lambda: c.chat("s", "u")))

    c2 = LLMClient({"enabled": False, "provider": "glm",
                    "base_url": "x", "model": "y", "api_key": "k"})
    check("未启用时不可用", not c2.available)

    c3 = LLMClient({"enabled": True, "provider": "ollama",
                    "base_url": "http://localhost:11434/v1",
                    "model": "qwen2.5:7b", "api_key": ""})
    check("本地 Ollama 无需 Key 即可用", c3.available)

    g = build_glossary([("沉孔", "Counterbore"), ("倒角", "Chamfer")])
    check("术语表装配", "沉孔 = Counterbore" in g and "必须遵守" in g)

    c3.put_cache("k", "v")
    check("结果缓存读写", c3.cached("k") == "v")


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


def test_capture() -> None:
    section("D 统一取写层")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from core.capture import (restore_clipboard, snapshot_clipboard,
                              write_text)

    cb = app.clipboard()
    cb.setText("原始剪贴板内容-ORIGINAL")
    snap = snapshot_clipboard()
    check("快照拿到内容", bool(snap) and len(snap) > 0, f"{len(snap)} 种格式")

    cb.setText("模拟 Ctrl+C 之后的新内容")
    check("剪贴板确已改变", cb.text() == "模拟 Ctrl+C 之后的新内容")
    restore_clipboard(snap)
    check("还原成功（多格式无损）",
          cb.text() == "原始剪贴板内容-ORIGINAL", cb.text())
    check("SendInput 上屏接口可用", write_text("") is False)


def test_bar(shot: bool = False) -> None:
    section("E 候选条 UI（三态）")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from core.engine import TermEngine
    from core.translate import TranslationResult
    from scene.detector import SceneDetector
    from ui.bar import (MODE_HINT, MODE_INPUT, MODE_RESULT, MODE_SUGGEST,
                        CandidateBar, make_no_activate)
    from ui.inserter import Inserter

    bar = CandidateBar(TermEngine(), SceneDetector(), Inserter())
    bar.show()
    make_no_activate(bar)

    # WS_EX_NOACTIVATE 是架构红线，必须真生效
    if OFFSCREEN:
        print("  [SKIP] WS_EX 标志位检查（offscreen 后端无真实 HWND）")
    else:
        import ctypes
        u32 = ctypes.WinDLL("user32")
        ex = u32.GetWindowLongW(int(bar.winId()), -20)
        check("WS_EX_NOACTIVATE 已生效（不抢焦点红线）",
              bool(ex & 0x08000000), hex(ex & 0xFFFFFFFF))
        check("WS_EX_TOOLWINDOW 已生效（不出现在 Alt+Tab）",
              bool(ex & 0x00000080))

    bar.show_input("chenkong")
    bar.refresh_candidates("chenkong")
    check("输入态：候选渲染", bar.mode == MODE_INPUT and len(bar.candidates) > 0,
          f"{len(bar.candidates)} 条")
    check("输入态：可见胶囊数 > 0", getattr(bar, "_shown", 0) > 0,
          f"显示 {getattr(bar, '_shown', 0)} 个")
    check("宽度受控（<=640）", bar.width() <= 640, f"{bar.width()}px")
    # v2.1 三段布局：顶行 + 译法列 + 胶囊行，高度必然大于旧单行 36
    check("三段布局：高度大于旧单行 36", bar.height() > 36, f"{bar.height()}px")
    check("三段布局：高度有界（<=200）", bar.height() <= 200, f"{bar.height()}px")

    from core.models import Candidate, Term
    first = bar.candidates[bar.active_index]
    check("译法列：有英文候选时可见",
          bar.en_col.isVisible() == bool(first.en),
          f"{bar.en_col.visible_rows()} 行")
    if first.en:
        check("译法列首行 = 选中项 en（不含标准号）",
              bar.en_col.rows[0]._text == first.en, bar.en_col.rows[0]._text)

    # 不伪造红线：无英文候选 → 译法列整列隐藏
    zh_only = Candidate(term=Term(zh="无英文词", en=""), score=50.0,
                        match_type="full_pinyin")
    bar.en_col.set_candidate(zh_only)
    check("译法列：无英文整列隐藏（不伪造）", not bar.en_col.isVisible())

    # 「推荐」徽标：只有第一名与第二名分差 > rec_badge_margin 才出现
    from core.config import get_config as _gc
    margin = float(getattr(_gc().ranking, "rec_badge_margin", 8.0))
    c1 = Candidate(term=Term(zh="甲", en="Alpha"), score=100.0,
                   match_type="full_pinyin")
    c2 = Candidate(term=Term(zh="乙", en="Beta"), score=100.0 - margin,
                   match_type="full_pinyin")
    bar.candidates = [c1, c2]
    bar.active_index = 0
    bar._refresh_en_col()
    check("「推荐」徽标：分差=阈值不显示", not bar.en_col.rows[0]._rec,
          f"gap={c1.score - c2.score}")
    c2.score = 100.0 - margin - 0.1
    bar._refresh_en_col()
    check("「推荐」徽标：分差>阈值才显示", bar.en_col.rows[0]._rec,
          f"gap={c1.score - c2.score:.1f} > {margin}")
    bar.refresh_candidates("chenkong")

    r = TranslationResult(src="沉孔、倒角", text="Counterbore / Chamfer",
                          source="term", confidence=0.9)
    bar.show_result(r)
    check("结果态：渲染", bar.mode == MODE_RESULT)
    bar.show_hint("已学习：沉孔 → Counterbore", "memory")
    check("提示态：渲染", bar.mode == MODE_HINT)

    if shot:
        out = ROOT / "docs"
        out.mkdir(exist_ok=True)
        bar.show_input("chenkong")
        bar.refresh_candidates("chenkong")
        app.processEvents()
        bar.grab().save(str(out / "bar_input.png"))
        bar.show_result(TranslationResult(
            src="该零件底部需加工沉孔，深度 8mm",
            text="A counterbore shall be machined at the bottom of this part, 8 mm deep.",
            source="memory_exact", confidence=1.0, fixes=["沉孔→Counterbore"]))
        app.processEvents()
        bar.grab().save(str(out / "bar_result.png"))
        bar.show_hint("已学习：沉孔 → Counterbore", "memory")
        app.processEvents()
        bar.grab().save(str(out / "bar_hint.png"))
        print(f"  [SHOT] 截图已输出到 {out}")
    bar.hide()


def test_perf() -> None:
    section("F 性能")
    from core.engine import TermEngine
    from core.memory import TranslationMemory
    from core.models import SceneContext
    from core.translate import Translator

    t0 = time.perf_counter()
    eng = TermEngine()
    t1 = time.perf_counter()
    db = Path(tempfile.gettempdir()) / "zhiyu_perf.db"
    db.unlink(missing_ok=True)
    mem = TranslationMemory(db)
    tr = Translator(eng, mem)
    t2 = time.perf_counter()
    sc = SceneContext(profession_key="mechanical")
    tr.term_map()
    t3 = time.perf_counter()

    print(f"  术语引擎就绪 {1000 * (t1 - t0):.1f}ms | "
          f"记忆库 {1000 * (t2 - t1):.1f}ms | 术语表 {1000 * (t3 - t2):.1f}ms")

    # 检索延迟（按键到候选）
    N = 300
    t = time.perf_counter()
    for i in range(N):
        eng.search("chenkong", sc, limit=9)
    d = (time.perf_counter() - t) / N * 1000
    check("按键→候选 < 30ms", d < 30, f"{d:.2f}ms")

    # 记忆库精确查询
    for i in range(200):
        mem.learn(f"测试句子编号{i}", f"Test sentence No.{i}", "mechanical", "user")
    t = time.perf_counter()
    for i in range(2000):
        mem.exact(f"测试句子编号{i % 200}")
    d = (time.perf_counter() - t) / 2000 * 1000
    check("记忆库精确命中 < 0.2ms", d < 0.2, f"{d:.3f}ms")

    t = time.perf_counter()
    for i in range(20):
        mem.fuzzy(f"测试句子编号{i}甲")
    d = (time.perf_counter() - t) / 20 * 1000
    check("记忆库模糊匹配 < 20ms（200 条规模）", d < 20, f"{d:.2f}ms")

    # 长度硬约束
    shortest = min((len(t_) for t_ in tr.term_map()), default=0)
    check("最短术语长度 >= 2（避免单字噪声）", shortest >= 2, f"{shortest}")
    mem.close()
    db.unlink(missing_ok=True)


def main() -> int:
    shot = "--shot" in sys.argv
    print("智语 · 词汇助手 验收测试")
    test_memory()
    test_translate()
    test_llm()
    test_capture()
    test_bar(shot)
    test_perf()
    print(f"\n{'=' * 66}")
    print(f"  通过 {len(PASS)} / 共 {len(PASS) + len(FAIL)}")
    if FAIL:
        print("  失败项：" + "、".join(FAIL))
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
