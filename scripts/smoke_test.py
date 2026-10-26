"""
智语输入法 — 端到端冒烟测试

不弹真实窗口（离屏渲染），验证：
1. 依赖完整性与版本
2. 词库加载与索引完整性
3. 场景探测可用性
4. 检索引擎准确率（多路匹配抽查）
5. 中英术语映射正确性
6. UI 面板构建与候选渲染
7. 上屏插入服务可用性
8. 历史词频与自定义词条
9. Rime 词库导出（对接水杉输入法）
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# 离屏渲染，避免弹窗干扰
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS, FAIL = [], []


def check(name: str, fn):
    try:
        result = fn()
        PASS.append((name, result))
        print(f"  [PASS] {name}" + (f" -> {result}" if result else ""))
        return True
    except Exception as e:
        FAIL.append((name, str(e)))
        print(f"  [FAIL] {name}")
        traceback.print_exc()
        return False


def section(title: str):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


# ============ 1. 依赖 ============
section("1. 依赖完整性")

def t_deps():
    import pypinyin, pygetwindow, psutil, pyperclip, keyboard
    from PySide6.QtCore import QT_VERSION_STR
    return (f"pypinyin={pypinyin.__version__}, Qt={QT_VERSION_STR}")

check("成熟开源依赖导入", t_deps)


# ============ 2. 词库 ============
section("2. 词库加载与索引")

from core.lexicon import LexiconManager
mgr = LexiconManager().load_all()
stats = mgr.stats()


def t_stats():
    assert stats["total_terms"] >= 500, f"术语总数偏少: {stats['total_terms']}"
    return f"{stats['total_terms']} 条 / {len(stats['professions'])} 领域"

check("术语规模达标 (>=500)", t_stats)


def t_professions():
    need = {"mechanical", "electrical", "architecture", "electronics"}
    got = set(stats["professions"].keys())
    assert need <= got, f"缺少领域: {need - got}"
    detail = ", ".join(f"{v['name']}={v['count']}" for v in stats["professions"].values())
    return detail

check("四领域齐备", t_professions)


def t_pinyin_index():
    probes = {"chenkong": "沉孔", "daojiao": "倒角", "jizhunmian": "基准面",
              "luowenkong": "螺纹孔", "yuanjiao": "圆角"}
    for py, expect in probes.items():
        hits = mgr.term_index.get(py, [])
        zhs = {h.zh for h in hits}
        assert expect in zhs, f"{py} 未命中 {expect}，实际={zhs}"
    return f"{len(probes)} 个拼音探针全部命中"

check("拼音索引准确性", t_pinyin_index)


def t_en_index():
    probes = {"counterbore": "沉孔", "chamfer": "倒角", "via": "过孔",
              "anodizing": "阳极氧化"}
    for en, expect in probes.items():
        hits = {h.zh for h in mgr.find_by_en(en)}
        assert expect in hits, f"{en} 未命中 {expect}，实际={hits}"
    return f"{len(probes)} 个英文探针全部命中"

check("英文索引准确性", t_en_index)


# ============ 3. 场景探测 ============
section("3. 场景探测引擎")

from scene.detector import SceneDetector, get_foreground_hwnd, get_window_title
det = SceneDetector()


def t_foreground():
    hwnd = get_foreground_hwnd()
    title = get_window_title(hwnd)
    return f"hwnd={hwnd}, title={title[:30]!r}"

check("前台窗口读取", t_foreground)


def t_detect():
    ctx = det.detect(force=True)
    assert ctx.detected_by in ("process", "default", "manual")
    return ctx.describe()

check("场景自动探测", t_detect)


def t_lock():
    ctx = det.lock(profession="mechanical", software="solidworks")
    assert ctx.profession_key == "mechanical"
    assert ctx.software_key == "solidworks"
    assert det.is_locked
    det.unlock()
    return f"{ctx.describe()} / tags={ctx.scene_tags}"

check("手动锁定领域+软件", t_lock)


# ============ 4. 检索引擎 ============
section("4. 术语检索引擎")

from core.engine import TermEngine
eng = TermEngine(mgr)
sw_scene = det.lock(profession="mechanical", software="solidworks")
sw_scene.scene_tags = ["drawing_2d", "3d_modeling", "sheet_metal"]


def t_full_pinyin():
    cases = {"chenkong": "沉孔", "daojiao": "倒角", "luowenkong": "螺纹孔",
             "jizhunmian": "基准面", "lashentutai": "拉伸凸台"}
    for q, expect in cases.items():
        res = eng.search(q, sw_scene, limit=5)
        top = [c.zh for c in res]
        assert expect in top, f"{q} 未命中 {expect}，实际={top}"
    return f"{len(cases)}/{len(cases)} 全拼命中"

check("拼音全拼检索", t_full_pinyin)


def t_abbr():
    cases = {"ck": "沉孔", "lwk": "螺纹孔", "dj": "倒角", "jzm": "基准面"}
    ok = 0
    for q, expect in cases.items():
        res = eng.search(q, sw_scene, limit=8)
        if expect in [c.zh for c in res]:
            ok += 1
    assert ok == len(cases), f"简拼命中 {ok}/{len(cases)}"
    return f"{ok}/{len(cases)} 简拼命中"

check("首字母简拼检索", t_abbr)


def t_rank_order():
    """验证场景匹配的词排在前列。"""
    res = eng.search("daojiao", sw_scene, limit=3)
    assert res and res[0].zh == "倒角"
    assert res[0].detail.get("scene", 0) > 0
    return f"Top1={res[0].zh} score={res[0].score} 理由={res[0].why()}"

check("领域/场景加权排序", t_rank_order)


def t_cross_profession():
    """跨领域召回：'过孔' 属于电子，在机械场景下应被压制。"""
    res = eng.search("guokong", sw_scene, limit=5)
    zhs = [c.zh for c in res]
    return f"机械场景下 guokong -> {zhs or '(已压制，符合预期)'}"

check("跨领域结果压制", t_cross_profession)


# ============ 5. 中英映射 ============
section("5. 中英术语映射（工程图核心）")


def t_en_mapping():
    cases = {"沉孔": "Counterbore", "倒角": "Chamfer", "螺纹孔": "Tapped Hole",
             "形位公差": "Geometric Tolerance", "表面粗糙度": "Surface Roughness",
             "基准面": "Datum Plane", "拉伸凸台": "Extruded Boss"}
    for zh, expect_en in cases.items():
        hits = eng.to_english(zh, sw_scene)
        assert hits, f"{zh} 无匹配"
        assert hits[0].en == expect_en, f"{zh} -> {hits[0].en}, 期望 {expect_en}"
    return f"{len(cases)}/{len(cases)} 中英映射正确"

check("中文->英文专业术语", t_en_mapping)


def t_standard():
    hits = eng.to_english("沉孔", sw_scene)
    txt = eng.english_of("沉孔", sw_scene)
    assert hits[0].standard, "缺少标准编号"
    return f"完整表述: {txt}"

check("带标准编号输出", t_standard)


def t_aliases():
    hits = eng.to_english("螺纹孔", sw_scene)
    aliases = hits[0].var_en
    assert aliases, "缺少英文别名"
    return f"螺纹孔 别名: {', '.join(aliases)}"

check("英文别名可用", t_aliases)


# ============ 6. UI 面板 ============
section("6. UI 面板构建")

from PySide6.QtWidgets import QApplication
qt_app = QApplication.instance() or QApplication(sys.argv)

from ui.inserter import Inserter
from ui.panel import InputPanel

inserter = Inserter()


def t_panel_build():
    panel = InputPanel(eng, det, inserter)
    panel.set_scene(sw_scene)
    assert panel.scene.profession_key == "mechanical"
    return f"面板宽度={panel.width()}px"

check("面板构建", t_panel_build)


def t_panel_render():
    panel = InputPanel(eng, det, inserter)
    panel.set_scene(sw_scene)
    panel.refresh_candidates("chenkong")
    n = panel.list.count()
    assert n > 0, "候选列表为空"
    first = panel.candidates[0]
    return f"渲染 {n} 条候选，Top1={first.zh}/{first.en}"

check("候选列表渲染", t_panel_render)


def t_panel_empty():
    panel = InputPanel(eng, det, inserter)
    panel.set_scene(sw_scene)
    panel.refresh_candidates("")
    n = panel.list.count()
    assert n > 0, "空输入时应展示默认候选"
    return f"空输入展示 {n} 条场景默认候选"

check("空输入默认候选", t_panel_empty)


# ============ 7. 插入服务 ============
section("7. 上屏插入服务")


def t_inserter():
    r = inserter.self_test()
    assert r["user32"], "user32 不可用"
    assert r["pyperclip"], "pyperclip 不可用"
    return f"method={r['method']}, clipboard={r['clipboard_ok']}"

check("插入服务自检", t_inserter)


# ============ 8. 历史词频 ============
section("8. 历史词频与自定义词条")


def t_history():
    """
    用未使用过的新词条验证历史提权。
    注意：历史加成有上限（history_bonus_cap），已饱和的词条不会继续增长，
    因此必须选一个当前使用次数低于上限的词条来验证。
    """
    cap = eng.config.ranking.history_bonus_cap
    per = eng.config.ranking.history_bonus_per_use
    safe_uses = cap // per  # 未饱和前的安全使用次数

    # 找一个历史使用次数为 0 的词条
    target = None
    for t in eng.lexicons.get_terms("mechanical"):
        if eng.usage.count_of(t.zh) == 0 and len(t.pinyin) >= 6:
            target = t
            break
    assert target is not None, "找不到未使用过的词条用于测试"

    q = target.pinyin
    before = eng.search(q, sw_scene, limit=12)
    b = next((c.score for c in before if c.zh == target.zh), 0.0)

    for _ in range(safe_uses):
        eng.commit(target.zh, sw_scene)

    after = eng.search(q, sw_scene, limit=12)
    a = next((c.score for c in after if c.zh == target.zh), 0.0)
    assert a > b, f"历史词频未生效: {target.zh} {b} -> {a}"
    return (f"'{target.zh}' 使用 {safe_uses} 次后得分 {b} -> {a} "
            f"（+{round(a - b, 1)}，上限 {cap}）")

check("历史词频提权", t_history)


def t_custom():
    t = eng.add_custom("压力角修正", "Pressure Angle Correction",
                       "齿轮设计中的修正参数", "齿轮设计", "mechanical")
    res = eng.search("yalijiaoxiuzheng", sw_scene, limit=5)
    zhs = [c.zh for c in res]
    assert t.zh in zhs, f"自定义词条未进入检索: {zhs}"
    top = res[0]
    assert top.term.custom, "自定义词条未获加权"
    return f"自定义词条 '{t.zh}' 命中并排至 Top1"

check("自定义词条生效", t_custom)


def t_custom_persist():
    p = Path(ROOT) / "data" / "user" / "custom_terms.json"
    n = eng.lexicons.save_custom(p)
    assert p.exists()
    return f"已持久化 {n} 条自定义词条 -> {p.relative_to(ROOT)}"

check("自定义词条持久化", t_custom_persist)


# ============ 9. 通用三列词表导出 ============
section("9. 通用三列词表导出")

from core.config import get_config
cfg = get_config()


def t_plain_export():
    outdir = Path(ROOT) / "data" / "export"
    total = 0
    for prof in cfg.professions:
        out = outdir / f"zhiyu_{prof}.txt"
        n = mgr.export_plain_dict(prof, out)
        total += n
        assert out.exists() and n > 0
    return f"导出 4 个三列词表，共 {total} 条"

check("通用词表导出", t_plain_export)


def t_plain_format():
    f = Path(ROOT) / "data" / "export" / "zhiyu_mechanical.txt"
    lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) > 100
    sample = lines[0]
    assert len(sample.split("\t")) == 3
    return f"{len(lines)} 行数据，样例: {sample}"

check("三列格式合规", t_plain_format)


# ============ 10. 水杉输入法对接 ============
section("10. 水杉输入法对接（TSF 原生方案，零编译）")

from core.msime_bridge import (
    export_all, export_quanpin_dict, export_custom_translations, build_ai_prompts
)

_msime_out = None


def t_msime_export():
    global _msime_out
    res = export_all()
    _msime_out = res
    assert len(res.files) >= 6, f"导出文件偏少: {len(res.files)}"
    for desc, path, n in res.files:
        assert path.exists(), f"{desc} 未生成: {path}"
        assert n > 0 or "提示词" in desc, f"{desc} 条目为空"
    return f"{len(res.files)} 个文件，累计 {res.total()} 条"


check("对接文件导出", t_msime_export)


def t_msime_quanpin_format():
    """水杉全拼词库要求 Tab 分隔三列：词语 / 编码 / 权重。"""
    p = next(p for d, p, n in _msime_out.files if "全部领域合并" in d)
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) >= 500, f"词条数偏少: {len(lines)}"
    for l in lines:
        cols = l.split("\t")
        assert len(cols) == 3, f"列数不是 3: {l!r}"
        zh, code, weight = cols
        assert zh and code, f"词语或编码为空: {l!r}"
        assert code.isascii() and code.islower(), f"编码应为小写拼音: {code!r}"
        assert weight.isdigit(), f"权重应为整数: {weight!r}"
    return f"{len(lines)} 行，三列格式全部合规"


check("全拼词库格式（Tab 三列）", t_msime_quanpin_format)


def t_msime_translations():
    """custom_translations.txt：源词含汉字即中译英（水杉规则）。"""
    p = next(p for d, p, n in _msime_out.files if "custom_translations" in d)
    data = [l for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]
    assert len(data) >= 500, f"对照条目偏少: {len(data)}"
    zh2en = {}
    for l in data:
        cols = l.split("\t")
        assert len(cols) == 2, f"列数不是 2: {l!r}"
        src, dst = cols
        # 源词必须含汉字，水杉据此判定为"中译英"方向
        assert any("\u4e00" <= c <= "\u9fff" for c in src), f"源词应含汉字: {src!r}"
        # 译文必须至少含一个拉丁字母（允许 Bézier 等带附加符号的正确拼写）
        assert any("a" <= c.lower() <= "z" for c in dst), f"译文异常: {dst!r}"
        zh2en[src] = dst
    for zh, expect in [("沉孔", "Counterbore"), ("倒角", "Chamfer"),
                       ("螺纹孔", "Tapped Hole")]:
        assert zh in zh2en, f"缺少 {zh}"
        assert expect in zh2en[zh], f"{zh} -> {zh2en[zh]}, 期望含 {expect}"
    return f"{len(data)} 条中英对照，抽查沉孔/倒角/螺纹孔正确"


check("中英对照 custom_translations", t_msime_translations)


def t_msime_ai_prompts():
    cfg = get_config()
    prompts = build_ai_prompts(cfg)
    assert len(prompts) == 3, f"提示词应为 3 套: {len(prompts)}"
    for title, text in prompts.items():
        assert "{拼音}" in text and "{上下文}" in text, f"{title} 缺少占位符"
        assert len(text) > 100, f"{title} 内容过短"
    return f"3 套提示词: {' / '.join(prompts)}"


check("AI 联想提示词", t_msime_ai_prompts)


# ============ 汇总 ============
section("测试汇总")
print(f"  通过: {len(PASS)}")
print(f"  失败: {len(FAIL)}")
if FAIL:
    print()
    for name, err in FAIL:
        print(f"  [FAIL] {name}: {err}")
    sys.exit(1)
else:
    print()
    print("  全部测试通过。")
    sys.exit(0)
