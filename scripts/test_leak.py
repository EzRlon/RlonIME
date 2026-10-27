"""候选泄漏回归 —— 守住「不打无关词」这条底线。

背景：上一版 engine 在「候选不足 4 条」时无条件补同领域高频词，
机械领域 freq 最高的「沉孔」于是出现在任意短前缀下，用户敲 zhizhang / ceshi
都能看到它，直接判定工具坏掉。本脚本就是那条事故的看门狗。

判据：
  1) 乱码串一律 0 条候选（绝不伪造）
  2) 无关短串不得出现「沉孔」等机械领域头部词
  3) 正常词仍要能命中
"""
from __future__ import annotations

import random
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.engine import TermEngine            # noqa: E402
from core.models import SceneContext          # noqa: E402
from core.lexicon import LexiconManager       # noqa: E402

# 用户实测截图里的输入 —— 必须 0 候选
SYMPTOM = "zhizhang"

eng = TermEngine()
n_common = eng.lexicons.load_common()      # v2.3：泄漏风险面必须带上通用词库
scene = SceneContext(profession_key="mechanical", software_key="solidworks")
scene.scene_tags = ["drawing_2d", "3d_modeling"]

fails: list[str] = []
total = 0


def check(name: str, cond: bool, info: str = "") -> None:
    global total
    total += 1
    if not cond:
        fails.append(f"{name} {info}")
        print(f"  FAIL  {name}  {info}")
    else:
        print(f"  ok    {name}  {info}")


def cands_of(q: str):
    return eng.search(q, scene, limit=9)


def terms_of(q: str) -> list[str]:
    return [c.zh for c in cands_of(q)]



print("=" * 68)
print("A. 乱码串必须 0 候选")
print("=" * 68)
check("通用词库已载入（风险面覆盖）", n_common > 60000, f"实际 {n_common}")
# 注（v2.3）：zhizhang / tiaozhuan / woshiyigedashuaige 等是「合法拼音串」，
# 启用通用词库后本就该出候选（同源校验见 A2 组），不能再列入乱码清单。
for q in ["abcxyz", "zzzzzzzz", "qqqq", "hello", "iweurywq", "asdfgh",
          "hhhkl", "wqpqpq"]:
    r = terms_of(q)
    check(f"[{q}]", not r, f"-> {r}" if r else "-> 空 ✓")

def en_words(t) -> set[str]:
    """一个术语的全部英文可检索片段：整句 + 分词 + 别名 + 标准号。"""
    import re as _re
    out: set[str] = set()
    for s in [t.en, *t.var_en, t.standard]:
        if not s:
            continue
        low = s.lower()
        out.add(low)
        out.update(w for w in _re.split(r"[\s\-/]+", low) if w)
    return out


def is_related(q: str, c) -> bool:
    """通用不变式：候选必须与输入存在拼音/简拼/字面/英文别名同源关系。

    这是本脚本的核心断言 —— 比"黑名单某些词"强得多，因为它对任意输入
    （含随机乱敲）都成立，能真正锁死"凭空造候选"这类回归。

    注意英文别名也要算相关：`y` -> 星三角启动 走的是别名 `Y-Δ Starting`，
    `x` -> 交叉引用 走的是别名 `X-Ref`，都是行业标准写法，属于正确命中。
    """
    q = q.lower()
    t = c.term
    if t.pinyin and t.pinyin.startswith(q):
        return True
    if t.abbr and t.abbr == q:
        return True
    if q in t.zh:
        return True
    if q in en_words(t):
        return True
    if c.match_type == "prefix_partial":
        # 打字失误回退：允许末尾多打 1~2 个字母
        for cut in (len(q) - 1, len(q) - 2):
            if cut >= 3 and t.pinyin and t.pinyin.startswith(q[:cut]):
                return True
    return False


print()
print("=" * 68)
print("A2. 合法拼音串（v2.3 常规输入）：允许出词，但每条必须同源")
print("=" * 68)
for q in ["zhizhang", "tiaozhuan", "nimabi", "woshiyigedashuaige"]:
    r = cands_of(q)
    stray = [c.zh for c in r if not is_related(q, c)]
    check(f"[{q}] 同源（可出词）", not stray,
          f"无关 {stray}" if stray else f"-> {[c.zh for c in r[:3]]}")

print()
print("=" * 68)
print("B. 同源不变式：短串 / 部分匹配 / 本次事故输入，候选必须条条相关")
print("=" * 68)
# zhizhang 是用户实测截图里出事的输入；后面是各类短串与部分匹配
for q in ["zhizhang", "x", "wx", "ceshi", "zh", "che", "qq", "zz", "abc",
          "xyz", "wxw", "tp", "xh", "cw", "qm", "zw", "ck", "lwk", "ckk"]:
    r = cands_of(q)
    stray = [c.zh for c in r if not is_related(q, c)]
    check(f"[{q}]", not stray,
          f"无关候选 {stray} in {[c.zh for c in r]}" if stray else f"-> {[c.zh for c in r]}")


print()
print("=" * 68)
print("C. 同源不变式：300 次随机乱敲，每条候选都必须与输入相关")
print("=" * 68)
random.seed(20260921)
bad_all = 0
rel_cnt = 0
samples: list[str] = []
for _ in range(300):
    n = random.randint(1, 9)
    q = "".join(random.choice(string.ascii_lowercase) for _ in range(n))
    res = eng.search(q, scene, limit=9)
    rel_cnt += sum(1 for c in res if c.match_type == "related")
    stray = [c.zh for c in res if not is_related(q, c)]
    if stray:
        bad_all += 1
        if len(samples) < 6:
            samples.append(f"{q}->{stray}")
check("无关候选 = 0", bad_all == 0, f"实际 {bad_all}/300  {samples}")
check("related 伪造候选 = 0", rel_cnt == 0, f"实际 {rel_cnt}")

print()
print("=" * 68)
print("D. 正常术语仍须命中（不能为了干净把功能砍了）")
print("=" * 68)
for q, want in [("chenkong", "沉孔"), ("zhidang", "止挡"), ("zhicheng", "支撑"),
                ("jiancao", "键槽"), ("daojiao", "倒角"), ("luowenkong", "螺纹孔"),
                ("ck", "沉孔"), ("lwk", "螺纹孔"), ("xingweigongcha", "形位公差")]:
    r = terms_of(q)
    check(f"[{q}] -> {want}", want in r, f"实际 {r[:4]}")

print()
print("=" * 68)
print("E. 打字失误回退：末尾多打 1~2 字母可救回")
print("=" * 68)
for q, want in [("chenkongg", "沉孔"), ("chenkonggg", "沉孔")]:
    r = terms_of(q)
    check(f"[{q}] -> {want}", want in r, f"实际 {r[:4]}")
for q in ["chenkongggg", "chenkonggggg"]:
    r = terms_of(q)
    check(f"[{q}] 不可救 -> 空", not r, f"实际 {r[:4]}")

print()
print("=" * 68)
print("F. 精确命中降噪（UX-P0-7）：跨行业硬过滤 + 前缀分压一档")
print("=" * 68)
r = cands_of("chenkong")          # 全拼精确命中 沉孔（mechanical）
check("精确命中时跨行业词不得入列",
      all(c.term.profession in ("mechanical", "") for c in r),
      str([(c.zh, c.term.profession) for c in r]))
exact = [c for c in r if c.match_type == "full_pinyin"]
prefix = [c for c in r if c.match_type == "prefix"]
check("存在全拼精确命中", bool(exact), str([(c.zh, c.match_type) for c in r[:3]]))
check("前缀噪声词分低于精确词",
      bool(exact) and all(c.score < exact[0].score for c in prefix),
      f"exact={exact[0].score if exact else '-'} "
      f"prefix={[(c.zh, c.score) for c in prefix[:3]]}")
check("前缀词分被压到 79 以下（精确命中时）",
      all(c.detail.get("base", 100.0) <= 79.0 for c in prefix),
      str([(c.zh, c.detail.get('base')) for c in prefix[:3]]))

print()
print("=" * 68)
print("G. 联想隔离（UX-P1-2）：联想词不入搜索池 + 白名单外词丢弃")
print("=" * 68)
import json as _json
import tempfile as _tf
from core.assoc import AssociationProvider            # noqa: E402

assoc = AssociationProvider(eng.lexicons)
sug = assoc.suggest("沉孔", scene)
check("沉孔联想非空（关联对直查）", bool(sug), f"-> {sug}")
check("联想词全部过白名单（词库真实存在）",
      all(eng.lexicons.find_by_zh(w) for w in sug), f"-> {sug}")
# 隔离断言①：上屏沉孔后出现的联想词，不得混入无关输入的候选集
r_ck = terms_of("chenkong")
leaked = [w for w in sug if w in r_ck and "chenkong" not in (
    eng.lexicons.find_by_zh(w)[0].pinyin if eng.lexicons.find_by_zh(w) else "")]
check("联想词不污染 chenkong 候选（除非本身可由输入命中）",
      not leaked, f"泄漏 {leaked} in {r_ck}")
# 隔离断言②：联想词本身仍可由自己的输入正常命中（数据同源）
if sug:
    w0 = sug[0]
    py0 = eng.lexicons.find_by_zh(w0)[0].pinyin
    check(f"联想词 {w0} 可由自身拼音命中", w0 in terms_of(py0),
          f"[{py0}] -> {terms_of(py0)[:4]}")

# 白名单外词丢弃：临时关联对里放词库没有的词
_tmp = Path(_tf.gettempdir()) / "zhiyu_assoc_test.json"
_tmp.write_text(_json.dumps({
    "pairs": {"沉孔": ["锪孔", "通孔", "不存在的词XYZ"]}},
    ensure_ascii=False), encoding="utf-8")
assoc2 = AssociationProvider(eng.lexicons, _tmp)
sug2 = assoc2.suggest("沉孔", scene)
check("白名单外词被丢弃（锪铣复合 / 不存在的词XYZ）",
      "锪铣复合" not in sug2 and "不存在的词XYZ" not in sug2, f"-> {sug2}")
check("白名单内词保留（通孔）", "通孔" in sug2, f"-> {sug2}")
_tmp.unlink(missing_ok=True)

# 空表不触发；与 engine 零共享（engine 未 import assoc）
check("未知词联想为空（不触发联想态）",
      assoc.suggest("不存在的词XYZ", scene) == [])
import core.engine as _eng_mod
check("TermEngine 零共享（未 import assoc）",
      "assoc" not in set(_eng_mod.__dict__) and
      not any("assoc" in m for m in dir(_eng_mod)), "")
import inspect as _inspect
check("engine.py 源码无 assoc 引用",
      "assoc" not in _inspect.getsource(_eng_mod), "")

# 同场景共现补足：关联对为空时，共享 ≥2 scene_tags 的同行业词顶上
_empty = Path(_tf.gettempdir()) / "zhiyu_assoc_empty.json"
_empty.write_text(_json.dumps({"pairs": {}}, ensure_ascii=False),
                  encoding="utf-8")
assoc3 = AssociationProvider(eng.lexicons, _empty)
t_ck = eng.lexicons.find_by_zh("沉孔")[0]
sug3 = assoc3.suggest("沉孔", scene)
if len(t_ck.scene_tags) >= 2:
    check("共现补足结果均与源词共享 ≥2 scene_tags 且同行业",
          all(len(set(t_ck.scene_tags)
                  & set(eng.lexicons.find_by_zh(w)[0].scene_tags)) >= 2
              and eng.lexicons.find_by_zh(w)[0].profession in (
                  "mechanical", "") for w in sug3),
          f"-> {sug3}")
else:
    check("共现补足（源词标签不足，跳过）", True, "")
_empty.unlink(missing_ok=True)

print()
print("=" * 68)
print(f"结果：{total - len(fails)}/{total} 通过")
if fails:
    print("失败项：")
    for f in fails:
        print("  -", f)
print("=" * 68)
sys.exit(1 if fails else 0)
