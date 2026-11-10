"""
智语输入法 — 词库加载与索引

设计原则：不重复造轮子。
- 拼音转换：使用成熟库 pypinyin（自带多音字词典），不自己维护拼音表
- 检索：使用 Python 标准库 + 倒排索引，不自己写分词器
- 持久化：使用 SQLite（标准库），不自己写文件存储格式
- Rime 互通：可导出 Rime .dict.yaml，直接给水杉输入法加载

术语索引结构：
    term_index: 拼音全拼 -> [Term]
    abbr_index: 首字母简拼 -> [Term]
    zh_index:   中文前缀 -> [Term]
    en_index:   英文小写 -> [Term]
"""
from __future__ import annotations

import bisect
import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# 复用已安装的成熟拼音库
try:
    from pypinyin import lazy_pinyin, Style
except ImportError:  # pragma: no cover
    print("[FATAL] 缺少 pypinyin，请先执行: pip install pypinyin", file=sys.stderr)
    raise

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import get_config
from core.models import Term

# 缓存文件：避免每次启动都跑 pypinyin
CACHE_VERSION = 3

# S08：用户可写目录下词库 / 通用词库的加载上限（gzip bomb、巨型数组 DoS）。
MAX_LEXICON_BYTES = 32 * 1024 * 1024     # 单文件 32 MiB
MAX_COMMON_LINES = 200_000                # 通用词库解压后物理行数上限
_MAX_TERM_STR = 512                       # 单个字符串字段最大长度
_MAX_TERM_LIST = 256                      # 列表字段最大条目数
_MAX_ZH = 64                              # 中文术语最大长度
_MAX_TSV_LINE = 8192                      # 通用词库单行最大长度


def _coerce_term(item) -> "Term | None":
    """逐字段校验词库条目；非法条目返回 None（跳过而不是崩）。S08。

    词库 JSON 来自用户可写目录，字段类型/长度不可信。校验放在
    Term.from_dict 之前，避免畸形字段在拼音生成 / 建索引阶段抛异常。
    """
    if not isinstance(item, dict):
        return None
    zh = item.get("zh")
    if not isinstance(zh, str) or not (0 < len(zh) <= _MAX_ZH):
        return None
    cleaned: dict = {"zh": zh}      # zh 是 Term 的必填字段
    for k in ("en", "pinyin", "abbr", "brief", "scene", "standard",
              "profession"):
        if k not in item:
            continue
        v = item[k]
        if v is None:
            v = ""
        if not isinstance(v, str) or len(v) > _MAX_TERM_STR:
            return None
        cleaned[k] = v
    for k in ("pinyin_alt", "software", "scene_tags", "var_en"):
        if k not in item:
            continue
        v = item[k]
        if v is None:
            v = []
        if not isinstance(v, list) or len(v) > _MAX_TERM_LIST:
            return None
        if any(not isinstance(x, str) or len(x) > _MAX_TERM_STR for x in v):
            return None
        cleaned[k] = v
    if "freq" in item:
        f = item["freq"]
        if isinstance(f, bool) or not isinstance(f, int) or f < 0:
            return None
        cleaned["freq"] = f
    if "custom" in item:
        c = item["custom"]
        if not isinstance(c, bool):
            return None
        cleaned["custom"] = c
    try:
        return Term.from_dict(cleaned)
    except Exception:
        return None


def pinyin_of(text: str) -> str:
    """汉字 -> 全拼小写，无分隔。'螺纹孔' -> 'luowenkong'"""
    return "".join(lazy_pinyin(text, style=Style.NORMAL))


def abbr_of(text: str) -> str:
    """汉字 -> 首字母简拼。'螺纹孔' -> 'lwk'"""
    return "".join(lazy_pinyin(text, style=Style.FIRST_LETTER))


class Lexicon:
    """单个领域的术语库。"""

    def __init__(self, profession_key: str, path: Path,
                 override_profession: bool = True):
        self.profession = profession_key
        self.path = path
        self.override_profession = override_profession   # False=共享层保留空值
        self.name = profession_key
        self.description = ""
        self.version = "0.0.0"
        self.terms: list[Term] = []
        self._loaded = False

    def load(self) -> "Lexicon":
        if not self.path.exists():
            raise FileNotFoundError(f"词库文件不存在: {self.path}")
        # S08：单文件大小上限，挡住可写目录下被替换的巨型 JSON。
        if self.path.stat().st_size > MAX_LEXICON_BYTES:
            raise ValueError(f"词库文件过大(>{MAX_LEXICON_BYTES}B): {self.path}")

        with open(self.path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"词库根节点不是对象: {self.path}")
        terms = raw.get("terms", [])
        if not isinstance(terms, list):
            raise ValueError(f"词库 terms 不是数组: {self.path}")

        self.name = raw.get("name", self.profession)
        self.description = raw.get("description", "")
        self.version = raw.get("version", "0.0.0")

        seen: set[str] = set()
        for item in terms:
            t = _coerce_term(item)          # S08：非法条目跳过而不是崩
            if t is None:
                continue
            if self.override_profession:
                t.profession = self.profession
            # 拼音由 pypinyin 自动生成（若词库未显式提供）
            if not t.pinyin:
                t.pinyin = pinyin_of(t.zh)
            if not t.abbr:
                t.abbr = abbr_of(t.zh)
            if t.zh in seen:
                continue
            seen.add(t.zh)
            self.terms.append(t)

        self._loaded = True
        return self

    def __len__(self) -> int:
        return len(self.terms)


class LexiconManager:
    """多领域词库管理器 + 拼音倒排索引。"""

    def __init__(self):
        self.config = get_config()
        self.lexicons: dict[str, Lexicon] = {}
        self.all_terms: list[Term] = []

        # 倒排索引
        self.term_index: dict[str, list[Term]] = defaultdict(list)   # 全拼
        self.abbr_index: dict[str, list[Term]] = defaultdict(list)   # 简拼
        self.zh_index: dict[str, list[Term]] = defaultdict(list)     # 中文
        self.en_index: dict[str, list[Term]] = defaultdict(list)     # 英文
        self.zh_set: set[str] = set()                                # 全部中文（去重）
        self._indexed = False

        # 通用词库（v2.3 常规输入）：约 8 万条，独立于 term_index，后台可载
        self.common_index: dict[str, list[tuple[str, str, int, int]]] = {}
        self.common_keys: list[str] = []
        self.common_loaded = False
        self.common_count = 0
        self._seg_codes: dict[str, tuple[str, str, int, int]] | None = None

    # ---------- 加载 ----------
    def load_all(self) -> "LexiconManager":
        # D15：幂等 —— 每次都从空表重建（同时保留配置热重载能力），
        # 重复调用不会让 all_terms / 各索引翻倍。
        self.lexicons.clear()
        self.all_terms.clear()
        for prof_key, lex_path in self.config.all_lexicon_paths():
            try:
                lex = Lexicon(prof_key, lex_path).load()
                self.lexicons[prof_key] = lex
                self.all_terms.extend(lex.terms)
            except Exception as e:
                print(f"[WARN] 词库加载失败 {prof_key}: {e}", file=sys.stderr)
        # v2.3 共享层：工程通用词库（profession 为空，全场景可命中）
        shared = self.config.lexicon_dir / "engineering.json"
        if shared.exists():
            try:
                lex = Lexicon("__shared__", shared,
                              override_profession=False).load()
                self.lexicons["__shared__"] = lex
                self.all_terms.extend(lex.terms)
            except Exception as e:
                print(f"[WARN] 工程通用词库加载失败: {e}", file=sys.stderr)
        self._build_index()
        return self

    def _build_index(self) -> None:
        self.term_index.clear()
        self.abbr_index.clear()
        self.zh_index.clear()
        self.en_index.clear()

        for t in self.all_terms:
            if t.pinyin:
                self.term_index[t.pinyin].append(t)
            for alt in t.pinyin_alt:
                if alt and alt != t.pinyin:
                    self.term_index[alt].append(t)
            if t.abbr:
                self.abbr_index[t.abbr].append(t)
            # 中文按前缀建索引，支持"螺" -> "螺纹孔"
            for i in range(1, len(t.zh) + 1):
                self.zh_index[t.zh[:i]].append(t)
            if t.en:
                # 英文索引用集合去重：整句 + 单词 + 别名可能命中同一 key
                en_keys: set[str] = {t.en.lower()}
                for w in re.split(r"[\s\-/]+", t.en.lower()):
                    if w:
                        en_keys.add(w)
                for alias in t.var_en:
                    a = alias.lower().strip()
                    if a:
                        en_keys.add(a)
                        for w in re.split(r"[\s\-/]+", a):
                            if w:
                                en_keys.add(w)
                for k in en_keys:
                    self.en_index[k].append(t)

        self.zh_set = {t.zh for t in self.all_terms}
        self._indexed = True
        # D04：索引重建后整句分段码表必须失效，下次 seg_codes() 重算。
        self._seg_codes = None

    # ---------- 查询辅助 ----------
    def get_terms(self, profession: str | None = None) -> list[Term]:
        if profession and profession in self.lexicons:
            return self.lexicons[profession].terms
        return self.all_terms

    def find_by_zh(self, zh: str) -> list[Term]:
        return self.zh_index.get(zh, [])

    def find_by_en(self, en: str) -> list[Term]:
        return self.en_index.get(en.lower().strip(), [])

    # ---------- 通用词库（v2.3 常规输入） ----------
    def load_common(self, path: Path | None = None) -> int:
        """载入通用词库（幂等）。约 8 万条，建议后台线程调用。

        数据：data/common/common.tsv.gz
             zh TAB 全拼 TAB 原始词频 TAB 归一词频 TAB 英文
        文件按 (全拼, -归一词频) 排序，故各键内候选天然按词频降序。
        内部存储 (zh, en, scaled, raw)：scaled 用于候选打分，raw 用于分段归一。
        """
        if self.common_loaded:
            return self.common_count
        p = path or self.config.common_dict_path
        if not p.exists():
            return 0
        # S08：压缩文件设大小上限；解压后物理行数另设上限防 gzip bomb，
        # 超长单行也直接跳过（84545 行的正常词库远低于这些阈值）。
        if p.stat().st_size > MAX_LEXICON_BYTES:
            return 0
        idx: dict[str, list[tuple[str, str, int, int]]] = {}
        count = 0
        lines = 0
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                lines += 1
                if lines > MAX_COMMON_LINES:
                    break
                if len(line) > _MAX_TSV_LINE:
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 5:
                    continue
                zh, code, raw, fq, en = parts
                if not zh or not code:
                    continue
                try:
                    rawi, fqi = int(raw), int(fq)
                except ValueError:
                    rawi, fqi = 1, 1
                idx.setdefault(code, []).append((zh, en, fqi, rawi))
                count += 1
        self.common_index = idx
        self.common_keys = sorted(idx)
        self.common_count = count
        self.common_loaded = True
        # D04：通用词库就位后整句分段码表必须失效，下次重算才含通用词条。
        self._seg_codes = None
        return count

    def common_exact(self, code: str) -> list[tuple[str, str, int, int]]:
        """全拼精确：返回 (zh, en, scaled_freq, raw_freq)（词频降序）。"""
        return self.common_index.get(code, [])

    def common_starts(self, code: str, limit: int) -> list[
            tuple[str, str, int, str, int]]:
        """前缀匹配（多打 1~3 个字母）：返回 (zh, en, 归一词频, 完整码, extra)。

        键按字典序连续，bisect 定位后顺序扫描；按 (extra 升序, 词频降序) 取前 limit。
        """
        if not self.common_keys:
            return []
        keys = self.common_keys
        i = bisect.bisect_left(keys, code)
        out: list[tuple[str, str, int, str, int]] = []
        scanned = 0
        while i < len(keys) and scanned < 500 and len(out) < limit * 4:
            k = keys[i]
            i += 1
            scanned += 1
            if not k.startswith(code):
                break
            extra = len(k) - len(code)
            if extra > 3:
                continue
            for zh, en, fq, _raw in self.common_index[k]:
                out.append((zh, en, fq, k, extra))
        out.sort(key=lambda r: (r[4], -r[2]))
        return out[:limit]

    def common_top_prefix(self, code: str, limit: int) -> list[
            tuple[str, str, int, str, int]]:
        """短查询（1~2 字母）专用：跨拼音组按词频取前 limit 个。

        为什么不能直接用 common_starts：它按字典序扫描，敲 `k` 时先扫完
        ka 组（卡/咖/喀/咔/胩…），`开/看/可/口` 根本进不来。这里每个拼音组
        只取最高频那条，再全局按词频排，不同后缀的常用字才有机会。
        """
        if not self.common_keys:
            return []
        keys = self.common_keys
        i = bisect.bisect_left(keys, code)
        out: list[tuple[str, str, int, str, int]] = []
        while i < len(keys):
            k = keys[i]
            i += 1
            if not k.startswith(code):
                break
            extra = len(k) - len(code)
            if extra > 4:
                continue
            recs = self.common_index.get(k)
            if recs:
                zh, en, fq, _raw = recs[0]
                out.append((zh, en, fq, k, extra))
        out.sort(key=lambda r: -r[2])
        return out[:limit]

    def seg_codes(self) -> dict[str, tuple[str, str, int, int]]:
        """整句分段用码表：全拼 -> 最佳 (zh, en, 归一词频, 原始词频)（惰性构建）。

        合并通用词库与专业/共享词库；同一码以专业/共享词条优先（词条更精）。
        原始词频：通用词条用 jieba 原值，专业词条按 freq 反推近似值（同尺度）。
        """
        if self._seg_codes is not None:
            return self._seg_codes
        codes: dict[str, tuple[str, str, int, int]] = dict(
            (code, recs[0]) for code, recs in self.common_index.items() if recs)
        for code, terms in self.term_index.items():
            if not terms:
                continue
            best = max(terms, key=lambda t: t.freq)
            raw = int(min(1_000_000, (max(best.freq, 40) / 4.0) ** 2.5))
            codes[code] = (best.zh, best.en, best.freq, raw)
        self._seg_codes = codes
        return codes

    def stats(self) -> dict[str, object]:
        profs = {k: v for k, v in self.lexicons.items()
                 if not k.startswith("__")}
        return {
            "total_terms": len(self.all_terms),
            "professions": {
                k: {"name": v.name, "count": len(v), "version": v.version}
                for k, v in profs.items()
            },
            "shared_terms": len(self.lexicons["__shared__"].terms)
            if "__shared__" in self.lexicons else 0,
            "common_terms": self.common_count,
            "pinyin_keys": len(self.term_index),
            "abbr_keys": len(self.abbr_index),
            "en_keys": len(self.en_index),
        }

    # ---------- 自定义词条 ----------
    def add_custom(self, term: Term) -> None:
        term.custom = True
        if not term.pinyin:
            term.pinyin = pinyin_of(term.zh)
        if not term.abbr:
            term.abbr = abbr_of(term.zh)
        self.all_terms.append(term)
        self.term_index[term.pinyin].append(term)
        self.abbr_index[term.abbr].append(term)
        # D14：补中文前缀索引与去重集，自定义词才能被中文反查 / 参与去重。
        for i in range(1, len(term.zh) + 1):
            self.zh_index[term.zh[:i]].append(term)
        self.zh_set.add(term.zh)
        if term.en:
            self.en_index[term.en.lower()].append(term)
        # D04：新增词条后整句分段码表失效。
        self._seg_codes = None

    def save_custom(self, path: Path) -> int:
        customs = [t.to_dict() for t in self.all_terms if t.custom]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"terms": customs}, f, ensure_ascii=False, indent=2)
        return len(customs)

    def load_custom(self, path: Path) -> int:
        if not path.exists():
            return 0
        # S08：自定义词库同样来自用户可写目录，套用大小 / 条目校验。
        if path.stat().st_size > MAX_LEXICON_BYTES:
            return 0
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return 0
        terms = raw.get("terms", [])
        if not isinstance(terms, list):
            return 0
        n = 0
        for item in terms:
            t = _coerce_term(item)
            if t is None:
                continue
            prof = t.profession or self.config.profile.get("active_profession", "")
            t.profession = prof
            self.add_custom(t)
            n += 1
        return n

    # ---------- 词库导出 ----------
    # 注：水杉输入法采用纯 TSF 自研架构，**不依赖 Rime**，
    #     .dict.yaml 无法被它加载。对接水杉请使用 core/msime_bridge.py
    #     （导出全拼词库三列格式 + custom_translations.txt）。
    def export_plain_dict(self, profession: str, out_path: Path) -> int:
        """
        导出「词语<TAB>拼音<TAB>权重」通用三列词表。
        这是 Rime 与多数拼音输入法共通的格式，便于向其他输入法迁移。
        """
        terms = self.get_terms(profession)
        lines: list[str] = []
        for t in terms:
            if not t.pinyin:
                continue
            lines.append(f"{t.zh}\t{t.pinyin}\t{t.freq}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(lines)


if __name__ == "__main__":
    mgr = LexiconManager().load_all()
    import json as _json
    print(_json.dumps(mgr.stats(), ensure_ascii=False, indent=2))

    print("\n--- 拼音检索抽查 ---")
    for probe in ["chenkong", "daojiao", "jizhunmian", "luowenkong"]:
        hits = mgr.term_index.get(probe, [])[:3]
        print(f"{probe:14s} -> " + ", ".join(f"{h.zh}/{h.en}" for h in hits))

    print("\n--- 英文反查抽查 ---")
    for probe in ["counterbore", "chamfer", "via"]:
        hits = mgr.find_by_en(probe)[:3]
        print(f"{probe:14s} -> " + ", ".join(f"{h.zh}/{h.en}({h.profession})" for h in hits))
