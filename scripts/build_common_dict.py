"""
构建「通用词库」（v2.3：常规输入能力的数据源）—— 可重复执行。

数据源（开源许可，随 README/license 署名）：
  - CC-CEDICT  汉英词典（词 + 英文释义）      CC BY-SA 4.0
  - jieba 词频表（词 + 词频，用于排序）       MIT

输出：data/common/common.tsv.gz
      每行：中文<TAB>全拼<TAB>原始词频(jieba)<TAB>归一词频(0-1000)<TAB>英文释义
说明：
  - 全拼一律用 pypinyin 生成（与运行时检索同一套拼音口径，保证一致性）
  - 排除已收录在 data/lexicons/*.json 的词（专业词库条目更完整，避免重复）
  - 释义清洗：去分类词条(CL:)/见…/姓…/日文等噪声；最多取两条义项
  - 原始词频供整句分段的概率归一化用；归一词频供候选打分用
"""
from __future__ import annotations

import gzip
import json
import math
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "packaging" / "tools" / "dicts"
OUT_DIR = ROOT / "data" / "common"
OUT_TSV = OUT_DIR / "common.tsv.gz"
OUT_META = OUT_DIR / "common_meta.json"

CEDICT_URL = ("https://www.mdbg.net/chinese/export/cedict/"
              "cedict_1_0_ts_utf-8_mdbg.txt.gz")
JIEBA_URL = "https://raw.githubusercontent.com/fxsjy/jieba/master/jieba/dict.txt"

# 词频归并：0.4 次幂（top 词 ≈ 1000 分；低频段保持分辨率）
_NOTE = "freq' = clamp(int(jieba_freq ** 0.4 * 4), 1, 1000)"

_IS_CJK = re.compile(r"^[\u4e00-\u9fff]+$")
_LINE = re.compile(r"^(\S+) (\S+) \[([^\]]+)\] /(.*)/\s*$")
# 释义噪声：分类词/异体字/姓氏/日文/直译提示等
_DROP = re.compile(
    r"^(CL:|variant of|old variant|see |abbr\.|surname |Japanese |"
    r"Taiwan |lit\. |fig\. |euphemism|archaic|dialect)",
    re.I)


def fetch(url: str, name: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if not p.exists():
        print(f"  下载 {url}")
        urllib.request.urlretrieve(url, p)
    return p


def clean_gloss(parts: list[str]) -> str:
    out: list[str] = []
    for g in parts:
        g = g.strip()
        if not g or _DROP.match(g):
            continue
        g = re.sub(r"(^|;\s*)to\s+", r"\1", g)     # "to produce" -> "produce"
        if "(" in g:                               # 括号注释直接去掉
            g = re.sub(r"\([^)]*\)", "", g).strip()
        g = g[:26]
        if g and g not in out:
            out.append(g)
        if len(out) >= 2:
            break
    s = " / ".join(out)
    return s[:44]


def main() -> int:
    print("[1/4] 准备数据源")
    cgz = fetch(CEDICT_URL, "cedict.txt.gz")
    jd = fetch(JIEBA_URL, "jieba_dict.txt")

    freq: dict[str, int] = {}
    for line in jd.read_text(encoding="utf-8").splitlines():
        parts = line.split(" ")
        if len(parts) >= 2:
            try:
                freq[parts[0]] = int(parts[1])
            except ValueError:
                pass
    print(f"  jieba 词条 {len(freq)}")

    existing: set[str] = set()
    for p in (ROOT / "data" / "lexicons").glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            existing.update(t["zh"] for t in d.get("terms", []) if t.get("zh"))
        except Exception:
            pass
    print(f"  既有词库词条 {len(existing)}（从通用词库中排除）")

    from pypinyin import lazy_pinyin, pinyin, Style

    def cedict_code(pin: str) -> str:
        """CC-CEDICT 的 "shui2 zi5" → "shuizi"（去声调数字）。"""
        return "".join(re.sub(r"[^a-z]", "", p.lower()) for p in pin.split())

    # pypinyin 的 heteronym 来自《汉语大字典》，含大量方言/古音（完 kuan、
    # 刻 kei、呈 kuang、混 gun）—— 全量启用会让正常输入冒出无关候选
    # （敲 k 弹出「完/刻/呈」，实测）。只对**日常真有异读**的字启用。
    _HETERONYMS = set(
        "谁血薄剥削给秘露熟色角觉率差参藏行乐重长便得地着了还都曾单解降系传转")

    def heteronym_codes(ch: str) -> list[str]:
        """单字的多读音（谁 → shei/shui）。词级读音由 pypinyin 词组规则负责。"""
        if ch not in _HETERONYMS:
            return []
        try:
            rs = pinyin(ch, style=Style.NORMAL, heteronym=True)[0]
        except Exception:
            return []
        out: list[str] = []
        for r in rs:
            r = re.sub(r"[^a-z]", "", (r or "").lower())
            if r and r not in out:
                out.append(r)
        return out

    print("[2/4] 解析 CC-CEDICT")
    entries: dict[str, str] = {}
    # 同一简体词在 CC-CEDICT 里可能有多条（不同读音，如 便宜 bian4yi2 / pian2yi5）。
    # 以前只留了最后一条释义、拼音完全弃用，于是 "shei" 这类异读永远打不出字。
    pins: dict[str, set[str]] = {}
    with gzip.open(cgz, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            m = _LINE.match(line.strip())
            if not m:
                continue
            _trad, simp, _pin, gloss = m.groups()
            if simp not in entries:
                entries[simp] = gloss
            _c = cedict_code(_pin)
            if _c:
                pins.setdefault(simp, set()).add(_c)
    print(f"  cedict 词条 {len(entries)}")

    print("[3/4] 合并词频 + 生成全拼 + 清洗释义")
    rows: list[tuple[str, str, int, int, str]] = []
    stat = {"non_cjk": 0, "too_long": 0, "existing": 0, "no_freq": 0,
            "no_gloss": 0, "bad_py": 0}
    for simp, gloss_raw in entries.items():
        if not _IS_CJK.match(simp):
            stat["non_cjk"] += 1
            continue
        if not 1 <= len(simp) <= 5:
            stat["too_long"] += 1
            continue
        if simp in existing:
            stat["existing"] += 1
            continue
        jf = freq.get(simp, 0)
        if jf <= 0 and len(simp) > 1:
            stat["no_freq"] += 1
            continue
        g = clean_gloss(gloss_raw.split("/"))
        if not g:
            stat["no_gloss"] += 1
            continue
        codes: list[str] = []
        base = "".join(lazy_pinyin(simp, style=Style.NORMAL))
        if base:
            codes.append(base)
        for c in sorted(pins.get(simp, ())):
            if c not in codes:
                codes.append(c)
        if len(simp) == 1:                      # 单字补异读：谁 shei/shui
            for c in heteronym_codes(simp):
                if c not in codes:
                    codes.append(c)
        codes = [c for c in codes if c.isascii() and c.isalpha()][:3]
        if not codes:
            stat["bad_py"] += 1
            continue
        stat["multi_code"] = stat.get("multi_code", 0) + (1 if len(codes) > 1 else 0)
        if jf > 0:
            # 0.4 次幂归并：高频词上限 1000，低频段仍有分辨率（避免大量并列）
            fq = min(1000, max(1, int((jf ** 0.4) * 4)))
        else:
            fq = 1
        for code in codes:
            rows.append((code, simp, jf if jf > 0 else 1, fq, g))
    print(f"  收录 {len(rows)} 条；过滤明细 {stat}")

    print("[4/4] 写出 common.tsv.gz")
    rows.sort(key=lambda r: (r[0], -r[3]))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT_TSV, "wt", encoding="utf-8") as f:
        for code, zh, raw, fq, g in rows:
            f.write(f"{zh}\t{code}\t{raw}\t{fq}\t{g}\n")
    OUT_META.write_text(json.dumps({
        "count": len(rows),
        "built": __import__("datetime").date.today().isoformat(),
        "sources": [
            {"name": "CC-CEDICT", "license": "CC BY-SA 4.0",
             "url": CEDICT_URL},
            {"name": "jieba dict", "license": "MIT", "url": JIEBA_URL},
        ],
        "note": "全拼由 pypinyin 生成；词频为 jieba 频次归并到 0-1000",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    size = OUT_TSV.stat().st_size / 1048576
    print(f"  完成：{OUT_TSV.name}  {len(rows)} 条  {size:.2f} MB(gz)")

    probe = {c: [] for c in ("fangan", "shishi", "shengchan", "cailiao",
                             "caizhi", "gongyi", "huakuai", "shengchanliang")}
    for code, zh, raw, fq, g in rows:
        if code in probe and len(probe[code]) < 4:
            probe[code].append(f"{zh}({fq}) {g[:20]}")
    for k, v in probe.items():
        print(f"  {k:14s} -> {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
