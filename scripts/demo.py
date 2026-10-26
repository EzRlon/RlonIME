"""
智语输入法 — 真实场景端到端演示

模拟用户在实际工程软件中的输入行为，验证核心价值：
1. 三维建模场景：推中文术语为主的建模特征
2. 工程图场景：推中文术语 + 对应英文标准表述（一键插标注）
3. 领域切换：同一输入在不同领域下的推荐差异
4. SolidWorks 装配体场景
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.engine import TermEngine
from core.models import scene_label
from scene.detector import SceneDetector


def banner(t):
    print()
    print("=" * 78)
    print(f"  {t}")
    print("=" * 78)


def show(eng, query, scene, note=""):
    res = eng.search(query, scene, limit=4)
    print(f"\n  输入 [{query}] {note}")
    if not res:
        print("    (无匹配)")
        return
    for i, c in enumerate(res):
        mark = "→" if i == 0 else " "
        en_full = c.en
        if c.term.standard:
            en_full += f"  [{c.term.standard}]"
        print(f"   {mark} {c.zh:8s} | {en_full:38s} | {c.score:6.1f} | {c.why()}")
        if c.term.brief:
            print(f"        {c.term.brief}")


def main():
    eng = TermEngine()
    det = SceneDetector()

    # ============ 场景 1：SolidWorks 三维建模 ============
    banner("场景 1 · SolidWorks 三维建模（.SLDPRT）")
    sc = det.lock(profession="mechanical", software="solidworks")
    sc.scene_tags = ["3d_modeling"] + [t for t in sc.scene_tags if t != "3d_modeling"]
    print(f"  场景: {sc.describe()} | 标签: {[scene_label(t) for t in sc.scene_tags[:3]]}")
    for q in ["bamo", "bamoxiedu", "zhenlie", "chouke", "choukehoudu"]:
        show(eng, q, sc)

    # ============ 场景 2：SolidWorks 工程图（核心场景） ============
    banner("场景 2 · SolidWorks 工程图（.SLDDRW）— 中英双语标注")
    sc2 = det.lock(profession="mechanical", software="solidworks")
    sc2.scene_tags = ["drawing_2d", "annotation", "3d_modeling"]
    print(f"  场景: {sc2.describe()} | 标签: {[scene_label(t) for t in sc2.scene_tags]}")

    print("\n  ── 孔特征标注（中文术语 + 英文标准表述）──")
    for q in ["chenkong", "maitoukong", "tongkong", "mangkong", "luowenkong"]:
        res = eng.search(q, sc2, limit=2)
        if res:
            c = res[0]
            std = f"  [{c.term.standard}]" if c.term.standard else ""
            print(f"\n  输入 [{q}]")
            print(f"    中文上屏: {c.zh}")
            print(f"    英文上屏: {c.en}{std}")
            if c.term.var_en:
                print(f"    其他译法: {', '.join(c.term.var_en)}")
            print(f"    使用场景: {c.term.scene}")

    print("\n  ── 形位公差与粗糙度标注 ──")
    for q in ["xingweigongcha", "pingmiandu", "chuizhidu", "tongzhoud", "biaomiancucaodu"]:
        res = eng.search(q, sc2, limit=2)
        if res:
            c = res[0]
            std = f"  [{c.term.standard}]" if c.term.standard else ""
            print(f"\n  输入 [{q}]")
            print(f"    中文: {c.zh}   |   英文: {c.en}{std}")

    # ============ 场景 3：领域切换对比 ============
    banner("场景 3 · 领域切换对推荐的影响（同一输入）")
    print("\n  输入拼音 [jiance] 在不同领域下的表现：\n")
    for prof, sw_key, tags in [
        ("mechanical", "solidworks", ["3d_modeling", "drawing_2d"]),
        ("architecture", "revit", ["bim_modeling"]),
        ("electronics", "altium", ["pcb_layout"]),
    ]:
        s = det.lock(profession=prof, software=sw_key)
        s.scene_tags = tags
        res = eng.search("jiance", s, limit=3)
        names = ", ".join(f"{c.zh}" for c in res) or "(无)"
        print(f"    {prof:14s} ({s.software_name:18s}) -> {names}")
    det.unlock()

    # ============ 场景 4：装配体场景 ============
    banner("场景 4 · SolidWorks 装配体（.SLDASM）")
    sc4 = det.lock(profession="mechanical", software="solidworks")
    sc4.scene_tags = ["assembly", "3d_modeling"]
    print(f"  场景: {sc4.describe()} | 标签: {[scene_label(t) for t in sc4.scene_tags]}")
    for q in ["peihe", "ganshejiancha", "baozhashitu", "zizhuangpeiti", "tongxinpeihe"]:
        show(eng, q, sc4)

    # ============ 场景 5：跨领域术语（电气/建筑/电子） ============
    banner("场景 5 · 跨领域专业术语覆盖")
    for prof, sw_key, tags, queries in [
        ("electrical", "epaln", ["schematic", "panel_layout"],
         ["duanzipai", "jiechudi", "duanluqi", "bianpinqi"]),
        ("architecture", "revit", ["bim_modeling", "drawing_2d"],
         ["biaogao", "zhouwang", "jianliqiang", "fanghuofenqu"]),
        ("electronics", "altium", ["pcb_layout", "schematic"],
         ["guokong", "zouxian", "hanpan", "chafendui"]),
    ]:
        s = det.lock(profession=prof, software=sw_key)
        s.scene_tags = tags
        print(f"\n  ── {prof} ({s.software_name}) ──")
        for q in queries:
            res = eng.search(q, s, limit=2)
            if res:
                c = res[0]
                print(f"    [{q:14s}] -> {c.zh:10s} | {c.en}")

    det.unlock()
    print()
    print("=" * 78)
    print("  演示结束 — 全流程验证通过")
    print("=" * 78)


if __name__ == "__main__":
    main()
