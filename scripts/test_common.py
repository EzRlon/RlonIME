"""v2.3 通用输入回归 —— 「常规中文输入 + 英文翻译」能力看门狗。

覆盖三块（对应 2026-10-16 用户反馈）：
  A. 词库扩量：材料/表面处理/工艺/制造流程/通用件（含 -ider 英文词、idea 流程链）
  B. 常规输入：通用词库命中 + 整句分词拼接（woshigongchengshi -> 我是工程师）
  C. 优先级与去重：专业词 > 共享工程词 > 通用词；同词绝不重复出条
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.engine import TermEngine              # noqa: E402
from core.models import SceneContext            # noqa: E402

fails: list[str] = []
total = 0
eng = TermEngine()
loaded = eng.lexicons.load_common()
scene = SceneContext(profession_key="mechanical", software_key="solidworks")
scene.scene_tags = ["drawing_2d", "3d_modeling"]
scene_ee = SceneContext(profession_key="electrical", software_key="")


def check(name: str, cond: bool, info: str = "") -> None:
    global total
    total += 1
    if not cond:
        fails.append(f"{name} {info}")
        print(f"  FAIL  {name}  {info}")
    else:
        print(f"  ok    {name}  {info}")


def zh_of(q: str, sc: SceneContext | None = None) -> list[str]:
    return [c.zh for c in eng.search(q, sc or scene, limit=9)]


def probe(q: str, want: str, sc: SceneContext | None = None) -> None:
    r = zh_of(q, sc)
    check(f"[{q}] -> {want}", want in r[:5], f"实际 {r[:5]}")


print("=" * 68)
print("A. 词库扩量（材料/表面处理/工艺/流程/通用件）")
print("=" * 68)
check("通用词库已载入 ≥60000 条", loaded >= 60000, f"实际 {loaded}")

# A1 材料与材料属性
for q, want in [("cailiao", "材料"), ("caizhi", "材质"),
                ("lvhejin", "铝合金"), ("taihejin", "钛合金"),
                ("gongchengsuliao", "工程塑料"), ("yingzhihejin", "硬质合金"),
                ("buxiugang", "不锈钢"), ("zhutie", "铸铁")]:
    probe(q, want)

# A2 表面处理
for q, want in [("yangjiyanghua", "阳极氧化"), ("duge", "镀铬"),
                ("pensha", "喷砂"), ("penwan", "喷丸"), ("lasi", "拉丝"),
                ("cuihuo", "淬火"), ("shentan", "渗碳")]:
    probe(q, want)

# A3 加工工艺
for q, want in [("chexiao", "车削"), ("xixiao", "铣削"), ("zuanxiao", "钻削"),
                ("jiaokong", "铰孔"), ("xianqiege", "线切割"),
                ("zhewan", "折弯"), ("zhuzao", "铸造"), ("duanzao", "锻造"),
                ("hanjie", "焊接"), ("rechuli", "热处理")]:
    probe(q, want)

# A4 制造流程（idea 链）
for q, want in [("chuangyi", "创意"), ("fangan", "方案"), ("shishi", "实施"),
                ("shengchan", "生产"), ("liangchan", "量产"),
                ("shichan", "试产"), ("yangji", "样机"), ("yanshou", "验收"),
                ("gongxu", "工序"), ("gongyingshang", "供应商"),
                ("wuliaoqingdan", "物料清单"), ("wuliaobianma", "物料编码")]:
    probe(q, want)

# A5 通用件（-ider 英文词）
for q, want in [("huakuai", "滑块"), ("daogui", "导轨"), ("fengui", "分规"),
                ("fengeqi", "分割器"), ("biaoshifu", "标识符"),
                ("lianzhouqi", "联轴器"), ("jiaju", "夹具")]:
    probe(q, want)

print()
print("=" * 68)
print("B. 常规输入（通用词库 + 整句分段 + 英文翻译）")
print("=" * 68)
probe("biaomianchuli", "表面处理")
probe("wuliaoqingdan", "物料清单")
probe("zhegecailiao", "这个材料")

check("普通词有英文翻译（材料）",
      any(c.en for c in eng.search("cailiao", scene) if c.zh == "材料"), "")
check("普通词有英文翻译（创意）",
      any(c.en for c in eng.search("chuangyi", scene) if c.zh == "创意"), "")

for q, want in [("woshigongchengshi", "我是工程师"),
                ("fanganwenti", "方案问题")]:
    r = eng.search(q, scene, limit=3)
    check(f"整句分段 [{q}] -> {want}", bool(r) and r[0].zh == want,
          f"实际 {[c.zh for c in r]}")
check("分段不拼凑英文（避免错句）",
      all(not c.en for c in eng.search("woshigongchengshi", scene)), "")
check("乱码不硬凑（wqpqpq）", zh_of("wqpqpq") == [], f"实际 {zh_of('wqpqpq')}")

for q, want in [("slider", "滑块"), ("divider", "分规"), ("identifier", "标识符"),
                ("idea", "创意")]:
    probe(q, want)
check("[bom] -> 物料清单/材料明细表",
      any(w in zh_of("bom")[:5] for w in ("物料清单", "材料明细表")),
      f"实际 {zh_of('bom')[:5]}")

print()
print("=" * 68)
print("C. 优先级与去重")
print("=" * 68)
r = zh_of("chenkong")
check("专业词优先：[chenkong] 首位=沉孔", r and r[0] == "沉孔", f"实际 {r[:3]}")
r = zh_of("daojiao")
check("专业词优先：[daojiao] 首位=倒角", r and r[0] == "倒角", f"实际 {r[:3]}")

r_ee = zh_of("buxiugang", scene_ee)
check("跨场景可见（电气场景 buxiugang 含不锈钢）",
      any("不锈钢" in z for z in r_ee), f"实际 {r_ee[:4]}")
check("跨场景可见（电气场景 yanshou 含验收）",
      "验收" in zh_of("yanshou", scene_ee),
      f"实际 {zh_of('yanshou', scene_ee)[:4]}")

PROBES = ["chenkong", "daojiao", "fangan", "shishi", "shengchan", "bom",
          "yanshou", "cailiao", "huakuai", "liangchan", "biaomianchuli",
          "woshigongchengshi", "slider"]
dup_bad: list[str] = []
for q in PROBES:
    zs = zh_of(q)
    if len(zs) != len(set(zs)):
        dup_bad.append(f"{q}:{zs}")
check("同词不重复出条（13 组探针）", not dup_bad, str(dup_bad[:3]))


print()
print("=== v2.6 日常输入法手感：普通词该像正常输入法，专业词命中才优先 ===")
r = zh_of("shi")
check("[shi] 首位=是（不再被 实体/视口 压住）", r and r[0] == "是", f"实际 {r[:4]}")
r = zh_of("zhe")
check("[zhe] 首位=这", r and r[0] == "这", f"实际 {r[:4]}")
r = zh_of("a")
check("[a] 首位=啊（不是英文别名反查）", r and r[0] == "啊", f"实际 {r[:4]}")
r = zh_of("k")
check("[k] 前三不含英文别名命中（折弯系数/K因子）",
      all(z not in ("折弯系数", "K因子") for z in r[:3]), f"实际 {r[:4]}")
r = zh_of("gongsi")
check("[gongsi] 专业词精确仍优先（首位=攻丝）", r and r[0] == "攻丝", f"实际 {r[:4]}")
r = zh_of("dangquan")
check("[dangquan] 挡圈 首位（专业命中优先）", r and r[0] == "挡圈", f"实际 {r[:4]}")
r = zh_of("jintian")
check("[jintian] 今天 首位（通用词正常命中）", r and r[0] == "今天", f"实际 {r[:4]}")
r = zh_of("counter")
check("[counter] 英文反查仍置主位（首位=沉孔）", r and r[0] == "沉孔", f"实际 {r[:4]}")

print()
print("=" * 68)
print("D. 材料 / 材质 / 表面处理 / 工艺（v4.0.1 扩充）")
print("=" * 68)
# 这四个维度是用户点名要的：每条都在「打错一个字母就出不来」的边缘上，
# 所以固定成回归项 —— 以后改词库/改拼音优先规则，先过这一组。
check("[cailiao] 首位=材料", (lambda x: x and x[0] == "材料")(zh_of("cailiao")),
      f"实际 {zh_of('cailiao')[:4]}")
check("[caizhi] 首位=材质", (lambda x: x and x[0] == "材质")(zh_of("caizhi")),
      f"实际 {zh_of('caizhi')[:4]}")
check("[biaomianchuli] 表面处理",
      "表面处理" in zh_of("biaomianchuli"), f"实际 {zh_of('biaomianchuli')[:4]}")
check("[gongyi] 首位=工艺", (lambda x: x and x[0] == "工艺")(zh_of("gongyi")),
      f"实际 {zh_of('gongyi')[:4]}")
check("[lengdungang] 冷镦钢（镦 dūn，不是 duī）",
      "冷镦钢" in zh_of("lengdungang"), f"实际 {zh_of('lengdungang')[:4]}")
check("[qiexiaosudu] 切削速度（削 xiāo，不是 xuē）",
      "切削速度" in zh_of("qiexiaosudu"), f"实际 {zh_of('qiexiaosudu')[:4]}")
check("[yingzhiyanghua] 硬质氧化",
      "硬质氧化" in zh_of("yingzhiyanghua"), f"实际 {zh_of('yingzhiyanghua')[:4]}")
check("[moju] 模具（mó 读音）", "模具" in zh_of("moju"), f"实际 {zh_of('moju')[:4]}")
check("[muju] 模具（mú 读音也认）", "模具" in zh_of("muju"), f"实际 {zh_of('muju')[:4]}")
check("[duyingge] 镀硬铬（铬 gè）",
      "镀硬铬" in zh_of("duyingge"), f"实际 {zh_of('duyingge')[:4]}")

print()
print("=" * 68)
print(f"结果：{total - len(fails)}/{total} 通过")
if fails:
    print("失败项：")
    for f in fails:
        print("  -", f)
print("=" * 68)
sys.exit(1 if fails else 0)
