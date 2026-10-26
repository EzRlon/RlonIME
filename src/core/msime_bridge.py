"""
智语输入法 — 水杉输入法（Metasequoia IME）对接导出器

水杉输入法是基于 Windows 原生 TSF 的 C++ 输入法（GPL-3.0），不依赖 Rime。
它提供两个**官方扩展点**，无需编译其源码即可接入专业词库：

  1) 词库批量导入
     设置 → 词库 → 批量导入，Tab 分隔三列：词语 <TAB> 编码 <TAB> 权重
     语言：全拼词库 / 五笔词库 / 英文词库

  2) custom_translations.txt（自定义候选窗翻译）
     放数据目录（默认 %LOCALAPPDATA%\\metasequoiaime），Tab 分隔源词/译文
     源词含汉字即中译英，全英文即英译中，以 # 开头为注释，UTF-8

本模块把智语的四领域专业词库转成上述两种格式。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import get_config
from core.lexicon import LexiconManager, pinyin_of
from core.models import Term
# 水杉数据目录（默认位置）
MSIME_DATA_DIR_HINT = r"%LOCALAPPDATA%\metasequoiaime"


def has_cjk(text: str) -> bool:
    """判断字符串是否含汉字（水杉据此判定 custom_translations 的方向）。"""
    return any("\u4e00" <= c <= "\u9fff" for c in text)


def normalize_pinyin_code(zh: str, pinyin: str = "") -> str:
    """
    生成水杉可接受的全拼编码。

    水杉全拼方案的编码是纯小写 ASCII 拼音串，不含空格。
    但工程术语常混入拉丁字母（如 "O 型圈"、"BGA 封装"、"BIM 模型"），
    pypinyin 会原样保留这些字母并留下空格，因此需要：
      1. 去掉所有空白
      2. 拉丁字母统一转小写
    例："O 型圈" -> pypinyin 得 "O xingquan" -> 规范化 "oxingquan"
        "BGA 封装" -> "BGA fengzhuang" -> "bgafengzhuang"
    """
    code = pinyin or pinyin_of(zh)
    code = "".join(code.split())          # 去所有空白
    code = code.lower()                   # 拉丁字母转小写
    return "".join(c for c in code if c.isascii() and (c.isalpha() or c.isdigit()))


# ============================================================
# 1) 全拼词库（设置 → 词库 → 批量导入）
# ============================================================

def export_quanpin_dict(
    mgr: LexiconManager,
    out_path: Path,
    profession: str | None = None,
    min_freq: int = 1,
) -> int:
    """
    导出「词语<TAB>编码<TAB>权重」三列的全拼词库。

    编码用 pypinyin 生成的无分隔全拼（与水杉全拼方案一致）。
    权重直接用 zh 词条的 freq（0-1000），水杉按权重排序候选。
    """
    terms = mgr.get_terms(profession) if profession else mgr.all_terms
    lines: list[str] = []
    seen: set[str] = set()

    for t in terms:
        if t.freq < min_freq:
            continue
        code = normalize_pinyin_code(t.zh, t.pinyin)
        if not code:
            continue
        key = f"{t.zh}\t{code}"
        if key in seen:
            continue
        seen.add(key)
        # 词语内含空格也可保留（Tab 分隔，水杉原文说明支持）
        lines.append(f"{t.zh}\t{code}\t{t.freq}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


# ============================================================
# 2) 五笔词库（可选，编码需五笔码表，此处按需留空）
# ============================================================

def export_wubi_dict(mgr: LexiconManager, out_path: Path, profession: str | None = None) -> int:
    """
    五笔码表无法由拼音推导，需外部码表。此处若词条未带 wubi 字段则跳过。
    保留接口以便后续接入码表。
    """
    return 0


# ============================================================
# 3) custom_translations.txt — 中英术语映射（工程图核心）
# ============================================================

def export_custom_translations(
    mgr: LexiconManager,
    out_path: Path,
    profession: str | None = None,
    include_aliases: bool = True,
) -> int:
    """
    生成水杉的 custom_translations.txt：中译英候选窗翻译。

    格式：源词<TAB>译文
      - 源词含汉字 → 中译英
      - 译文用标准英文表述；有标准号时附在后面
      - 别名作为额外行一并输出，提高命中率
    """
    terms = mgr.get_terms(profession) if profession else mgr.all_terms
    lines: list[str] = [
        "# RlonIME · 工程领域专业术语中英对照",
        "# 由「RlonIME」自动生成，供水杉输入法 custom_translations.txt 使用",
        "# 格式：源词<TAB>译文（源词含汉字为中译英）",
        "# 修改后重启输入法生效",
        "#",
    ]
    n = 0
    seen: set[str] = set()

    for t in terms:
        if not t.en or t.zh in seen:
            continue
        # 水杉按"源词是否含汉字"判定方向。源词为纯英文的条目
        # （如 FOB / CIF 等贸易术语）会被判成英译中，与文件用途冲突，
        # 因此这类条目改由词库导入 + 英文别名承载，此处跳过。
        if not has_cjk(t.zh):
            continue
        seen.add(t.zh)
        trans = t.en
        if t.standard:
            trans = f"{t.en} ({t.standard})"
        lines.append(f"{t.zh}\t{trans}")
        n += 1
        # 别名：同一中文对应多个英文写法，追加为独立条目会被后写覆盖，
        # 因此合并进主译文的括号里，保持一条中文一项译文
        if include_aliases and t.var_en:
            lines[-1] = f"{t.zh}\t{trans}"
            for a in t.var_en:
                if a.lower() == t.en.lower():
                    continue
                lines[-1] += f" / {a}"
                break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n


# ============================================================
# 4) 中文纯词组（人名类）：水杉可自动生成拼音
# ============================================================

def export_zh_only(mgr: LexiconManager, out_path: Path, profession: str | None = None) -> int:
    """只导出中文词，交给水杉自己生成拼音（适合人名/生僻词组）。"""
    terms = mgr.get_terms(profession) if profession else mgr.all_terms
    seen: set[str] = set()
    lines: list[str] = []
    for t in terms:
        if t.zh and t.zh not in seen:
            seen.add(t.zh)
            lines.append(t.zh)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


# ============================================================
# 5) AI 联想提示词（水杉 AI 辅助支持自定义提示词）
# ============================================================

AI_PROMPT_TEMPLATE = """你是「{hierarchy}」岗位的专业术语联想引擎。

用户正在 {software} 中工作，当前输入拼音为：{pinyin}
已输入上下文：{context}

请优先给出该岗位在此软件场景下最常用的中文专业术语，要求：
1. 严格对应拼音切分，不得随意增删或改变读音
2. 优先专业术语，不要通用口语词（例：优先「沉孔」而非「很大的洞」）
3. 结合工程规范用词，必要时给出国标术语全称
4. 若拼音明显对应英文技术缩写或产品名，优先返回英文（例：pcb→PCB）
只返回候选词，用「、」分隔，不要解释。"""


def build_ai_prompts(cfg) -> dict[str, str]:
    """为水杉 AI 联想的自定义提示词生成三套（按行业倾向）。"""
    prof = cfg.profile
    h = prof.get("hierarchy", {}) or {}
    ind = h.get("industry", "mechanical")
    prof_def = cfg.professions.get(ind)
    prof_name = prof_def.name if prof_def else "工程"
    sw_key = prof.get("active_software", "")
    sw = cfg.softwares.get(sw_key)
    sw_name = sw.name if sw else "工程软件"

    base = AI_PROMPT_TEMPLATE.format(
        hierarchy=f"{prof_name}工程师", software=sw_name,
        pinyin="{拼音}", context="{上下文}",
    )
    return {
        "专业术语优先": base,
        "图纸标注优先": base.replace(
            "3. 结合工程规范用词，必要时给出国标术语全称",
            "3. 优先工程图标注用词，中文术语后附对应英文标准表述",
        ),
        "国标规范优先": base.replace(
            "2. 优先专业术语，不要通用口语词（例：优先「沉孔」而非「很大的洞」）",
            "2. 优先国家标准（GB）术语全称，避免行业俗称",
        ),
    }


# ============================================================
# 主入口
# ============================================================

@dataclass
class ExportResult:
    files: list[tuple[str, Path, int]]   # (说明, 路径, 条目数)

    def total(self) -> int:
        return sum(n for _, _, n in self.files)


def export_all(out_dir: Path | None = None) -> ExportResult:
    cfg = get_config()
    mgr = LexiconManager().load_all()
    out = Path(out_dir) if out_dir else cfg.user_data_dir / "msime_export"
    out.mkdir(parents=True, exist_ok=True)

    files: list[tuple[str, Path, int]] = []

    # 全领域合并的全拼词库（一次性导入最省事）
    p = out / "zhiyu_quanpin_dict.txt"
    n = export_quanpin_dict(mgr, p)
    files.append(("全拼词库（全部领域合并）", p, n))

    # 分领域导出，便于按需导入
    for prof_key in cfg.professions:
        p = out / f"zhiyu_quanpin_{prof_key}.txt"
        n = export_quanpin_dict(mgr, p, profession=prof_key)
        files.append((f"全拼词库（{cfg.professions[prof_key].name}）", p, n))

    # 中英对照（工程图标注核心）
    p = out / "custom_translations.txt"
    n = export_custom_translations(mgr, p)
    files.append(("中英术语对照（custom_translations.txt）", p, n))

    # AI 联想提示词
    prompts = build_ai_prompts(cfg)
    p = out / "msime_ai_prompts.txt"
    body = []
    for i, (title, text) in enumerate(prompts.items(), 1):
        body.append(f"===== 提示词 {i}：{title} =====")
        body.append(text)
        body.append("")
    p.write_text("\n".join(body), encoding="utf-8")
    files.append(("AI 联想提示词（3 套）", p, len(prompts)))

    return ExportResult(files)


if __name__ == "__main__":
    res = export_all()
    print("=" * 70)
    print("  水杉输入法对接文件导出完成")
    print("=" * 70)
    for desc, path, n in res.files:
        print(f"  {desc}")
        print(f"      {path}  ({n} 条)")
    print()
    print(f"  共 {len(res.files)} 个文件，累计 {res.total()} 条数据")
