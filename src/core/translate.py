"""
智语 — 翻译管线（五级降级）

用户输入的是中文片段，要拿到的是「符合当前行业、当前整句话语境」的英文，
不是逐词直译。为此按成本从低到高排五级，命中即返回：

  L1 记忆库精确命中     ~0.1ms  用户以前确认过的原句
  L2 记忆库模糊命中 ≥85% ~1-3ms  同一句稍作修改
  L3 术语库整段覆盖      ~0.5ms  整段就是若干个标准术语
  L4 大模型出稿          0.8-4s  本地查不到，交给 AI
  L5 术语强制对齐        ~0.1ms  AI 稿里被自由发挥的专业术语，掰回标准译名

L5 是这套方案与「随便接个大模型」的本质区别：**术语一致性 > 句式地道**。
图纸上同一个零件被译成两种叫法，比句子不够漂亮严重得多。

所有级别都失败时，返回原文并标记 failed —— 绝不吞掉用户的内容。
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.free_translate import FreeTranslator, FreeTranslateError
from core.llm import (SYSTEM_TRANSLATE, SYSTEM_TRANSLATE_BACK, LLMClient,
                      LLMError, build_glossary)
from core.memory import TranslationMemory
from core.models import SceneContext

# 术语对齐时，允许的「同一个词的不同写法」
_WORD_SPLIT = re.compile(r"[\s\-/]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class TranslationResult:
    """一次翻译的完整结果。"""

    src: str = ""
    text: str = ""
    source: str = "failed"        # memory_exact / memory_fuzzy / term / llm / failed
    confidence: float = 0.0
    locked: list[tuple[str, str]] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)      # 被强制对齐纠正的术语
    unresolved: list[str] = field(default_factory=list)  # AI 漏用的术语
    elapsed_ms: float = 0.0
    error: str = ""
    direction: str = "zh2en"
    provider: str = ""            # 免费翻译的提供方（MyMemory / Google）      # zh2en（中→英）/ en2zh（英→中，v2.4.0 N1）

    @property
    def ok(self) -> bool:
        return bool(self.text) and self.source != "failed"

    @property
    def source_label(self) -> str:
        if self.direction == "en2zh":
            return {
                "memory_exact": "记忆库·英中",
                "memory_fuzzy": "记忆库·英中近似",
                "term": "术语反查",
                "llm": "AI 回译",
                "free": "免费回译",
                "failed": "未翻译",
            }.get(self.source, self.source)
        return {
            "memory_exact": "记忆库",
            "memory_fuzzy": "记忆库·近似",
            "term": "术语库",
            "llm": "AI 翻译",
            "free": "免费翻译",
            "failed": "未翻译",
        }.get(self.source, self.source)


class Translator:
    """五级翻译管线。"""

    def __init__(self, engine, memory: TranslationMemory | None = None,
                 llm: LLMClient | None = None, fuzzy_threshold: float = 0.85,
                 free: FreeTranslator | None = None):
        self.engine = engine
        self.memory = memory or TranslationMemory()
        self.llm = llm or LLMClient()
        # v2.6：没有 API Key 时的免费在线翻译（MyMemory → Google）。
        # None = 关闭（纯本地 + AI 二选一）。
        self.free = free
        self.fuzzy_threshold = fuzzy_threshold
        self._zh_map: dict[str, str] | None = None     # 中文术语 -> 标准英文
        self._en_map: dict[str, str] | None = None     # 英文术语(小写) -> 中文
        self._max_term_len = 1

    # ---------------- 术语表（懒构建，只在词库变化时重建） ----------------
    def term_map(self) -> dict[str, str]:
        if self._zh_map is not None:
            return self._zh_map
        m: dict[str, str] = {}
        try:
            terms = self.engine.lexicons.all_terms
        except Exception:
            terms = []
        # 按词频从高到低写入，高词频的译法覆盖低词频的（保留最常用译名）
        for t in sorted(terms, key=lambda x: x.freq, reverse=True):
            if len(t.zh) < 2 or not t.en:
                continue          # 单字术语噪声太大，一律排除
            m.setdefault(t.zh, t.en)
        self._zh_map = m
        self._max_term_len = max((len(k) for k in m), default=1)
        return m

    def invalidate(self) -> None:
        self._zh_map = None
        self._en_map = None

    # ---------------- 反向术语表（英 → 中，N1） ----------------
    def term_map_back(self) -> dict[str, str]:
        """英文（整词/别名，小写）-> 标准中文，用于英→中术语反查。

        高词频术语优先占位；只收长度 ≥3 的英文键，避免 "a"/"of" 这类
        噪声把任意短语命中成术语。
        """
        if self._en_map is not None:
            return self._en_map
        m: dict[str, str] = {}
        try:
            terms = self.engine.lexicons.all_terms
        except Exception:
            terms = []
        for t in sorted(terms, key=lambda x: x.freq, reverse=True):
            if not t.en or len(t.zh) < 2:
                continue
            keys = [t.en] + list(t.var_en or [])
            for alias in keys:
                a = re.sub(r"\s+", " ", str(alias or "").strip().lower())
                if len(a) >= 3:
                    m.setdefault(a, t.zh)
        self._en_map = m
        return m

    def extract_terms_back(self, src: str, limit: int = 24
                           ) -> list[tuple[str, str]]:
        """从英文源句里抽出术语对（中文, 标准英文），供反向提示词做术语表。"""
        words = [w for w in re.split(r"[^A-Za-z\-]+", src or "")
                 if len(w) >= 3]
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for w in words:
            try:
                hits = self.engine.lexicons.find_by_en(w)[:3]
            except Exception:
                hits = []
            for t in hits:
                if not t.en or len(t.zh) < 2 or t.zh in seen:
                    continue
                seen.add(t.zh)
                out.append((t.zh, t.en))
                break
            if len(out) >= limit:
                break
        return out

    # ---------------- 反向主入口（英 → 中，N1） ----------------
    def check_terms_zh(self, zh: str, locked: list[tuple[str, str]]
                       ) -> list[str]:
        """英→中方向的术语一致性检查（只报告，绝不硬替换）。

        为什么不在中文侧做替换：中文语序与英文完全不同，强插标准译名
        很容易产出病句（把 "flange connection" 里的词替换成"法兰"看似对，
        但把 "weld" 换成"焊接"就会写出"焊接焊接件"）。所以这里只发现
        「术语库里明明有标准中文名、译文却没用到」，如实报告给用户判断。

        返回形如 ["flange(应作 法兰)", ...]；空列表表示全部对得上。
        """
        miss: list[str] = []
        text = zh or ""
        for zh_term, en in locked or []:
            if not zh_term:
                continue
            if zh_term not in text:
                miss.append(f"{en}(应作 {zh_term})")
        return miss

    def translate_back_local(self, src: str,
                             scene: SceneContext | None = None
                             ) -> TranslationResult | None:
        """只用本地资源做英→中：记忆库（精确/模糊）+ 整段术语反查。"""
        src = (src or "").strip()
        if not src:
            return None
        industry = getattr(scene, "profession_key", "") or ""
        t0 = time.perf_counter()

        p = self.memory.exact(src, industry)
        if p is not None and p.score >= 0.99:
            return TranslationResult(
                src=src, text=p.dst, source="memory_exact", confidence=1.0,
                direction="en2zh", locked=self.extract_terms_back(src),
                elapsed_ms=(time.perf_counter() - t0) * 1000)

        if p is None:
            p = self.memory.fuzzy(src, industry, self.fuzzy_threshold)
        if p is not None and p.score >= self.fuzzy_threshold:
            return TranslationResult(
                src=src, text=p.dst, source="memory_fuzzy",
                confidence=round(p.score, 3), direction="en2zh",
                locked=self.extract_terms_back(src),
                elapsed_ms=(time.perf_counter() - t0) * 1000)

        # 整段就是一条标准英文术语 → 直接反查（不做逐词硬拼，守不伪造红线）
        key = re.sub(r"\s+", " ", src.strip().strip(" .,;:")).lower()
        zh = self.term_map_back().get(key)
        if zh:
            return TranslationResult(
                src=src, text=zh, source="term", confidence=0.9,
                direction="en2zh", locked=[(zh, key)],
                elapsed_ms=(time.perf_counter() - t0) * 1000)
        return None

    def translate_back_async(self, src: str, scene: SceneContext | None,
                             callback: Callable[[TranslationResult], None],
                             allow_llm: bool = True
                             ) -> TranslationResult | None:
        """英→中异步：本地能出就同步返回，否则后台调 AI。"""
        local = self.translate_back_local(src, scene)
        if local is not None:
            return local
        locked = self.extract_terms_back(src)
        if not allow_llm or not self.llm.available:
            if allow_llm and self.free is not None and self.free.available:
                self._free_back(src, locked, callback)
                return None
            callback(TranslationResult(
                src=src, source="failed", locked=locked, direction="en2zh",
                error="未启用 AI，且词库无此英文术语"))
            return None

        industry = getattr(scene, "profession_key", "") or ""
        cache_key = f"{src}|{industry}|back"
        hit = self.llm.cached(cache_key)
        if hit:
            callback(TranslationResult(
                src=src, text=hit, source="llm", confidence=0.8,
                locked=locked, direction="en2zh",
                unresolved=self.check_terms_zh(hit, locked)))
            return None

        def _done(content: str | None, err: str) -> None:
            if content is None:
                callback(TranslationResult(src=src, source="failed",
                                           locked=locked, direction="en2zh",
                                           error=err))
                return
            self.llm.put_cache(cache_key, content)
            callback(TranslationResult(
                src=src, text=content, source="llm", confidence=0.8,
                locked=locked, direction="en2zh",
                unresolved=self.check_terms_zh(content, locked)))

        head = ""
        if scene is not None and getattr(scene, "hierarchy", ""):
            head = f"背景：{scene.hierarchy}\n"
        self.llm.chat_async(
            SYSTEM_TRANSLATE_BACK,
            f"{head}{build_glossary(locked)}待译英文：\n{src}",
            _done, temperature=0.2, max_tokens=768, timeout=15.0)
        return None

    def extract_terms(self, src: str, industry: str = "",
                      limit: int = 24) -> list[tuple[str, str]]:
        """从源句里贪心最长匹配出术语对（中文, 标准英文）。

        贪心最长匹配：从每个位置起尽量吃掉最长的已知术语，
        避免把「螺纹孔」切成「螺纹」+「孔」。
        """
        m = self.term_map()
        if not m:
            return []
        text = src or ""
        n = len(text)
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        i = 0
        while i < n:
            hit = ""
            upper = min(self._max_term_len, n - i)
            for L in range(upper, 1, -1):       # 至少两字，单字术语噪声太大
                cand = text[i:i + L]
                if cand in m:
                    hit = cand
                    break
            if hit:
                if hit not in seen:
                    seen.add(hit)
                    out.append((hit, m[hit]))
                i += len(hit)
            else:
                i += 1
            if len(out) >= limit:
                break
        return out

    # ---------------- 只读预检 ----------------
    def probe_local(self, src: str, scene: SceneContext | None = None) -> str | None:
        """只读预检（UX-P1-3「译」候选用）：本地管线（记忆库精确/模糊 +
        术语覆盖拼接）能给出的译文，给不出返回 None。

        与 translate_local 的唯一区别是只回文本、什么都不写
        （不碰记忆库计数、不写缓存），供候选条探测「值不值得出『译』候选」。
        """
        r = self.translate_local(src, scene)
        return r.text if r is not None and r.ok else None

    # ---------------- 主入口 ----------------
    def translate_local(self, src: str, scene: SceneContext | None = None) -> TranslationResult | None:
        """只用本地资源（L1-L3）。命中返回结果，未命中返回 None。"""
        src = (src or "").strip()
        if not src:
            return None
        industry = getattr(scene, "profession_key", "") or ""
        t0 = time.perf_counter()

        # ---- L1 记忆库精确 ----
        p = self.memory.exact(src, industry)
        if p is not None and p.score >= 0.99:
            return TranslationResult(
                src=src, text=p.dst, source="memory_exact", confidence=1.0,
                locked=self.extract_terms(src, industry),
                elapsed_ms=(time.perf_counter() - t0) * 1000)

        # ---- L2 记忆库模糊 ----
        if p is None:
            p = self.memory.fuzzy(src, industry, self.fuzzy_threshold)
        if p is not None and p.score >= self.fuzzy_threshold:
            return TranslationResult(
                src=src, text=p.dst, source="memory_fuzzy",
                confidence=round(p.score, 3),
                locked=self.extract_terms(src, industry),
                elapsed_ms=(time.perf_counter() - t0) * 1000)

        # ---- L3 术语库整段覆盖 ----
        locked = self.extract_terms(src, industry)
        if locked:
            covered = sum(len(zh) for zh, _ in locked)
            only = re.sub(r"[\s\u3000，。、；：,.;:!?（）()【】\[\]]+", "", src)
            covered = sum(len(zh) for zh, _ in locked if zh in only)
            ratio = covered / max(len(only), 1)
            # 整段几乎就是若干标准术语 → 直接拼出标准译名
            if ratio >= 0.9:
                text = " / ".join(en for _, en in locked)
                return TranslationResult(
                    src=src, text=text, source="term", confidence=0.9,
                    locked=locked,
                    elapsed_ms=(time.perf_counter() - t0) * 1000)

        return None

    def translate(self, src: str, scene: SceneContext | None = None,
                  allow_llm: bool = True) -> TranslationResult:
        """同步完整管线（会阻塞；UI 请用 translate_async）。"""
        t0 = time.perf_counter()
        local = self.translate_local(src, scene)
        if local is not None:
            return local

        locked = self.extract_terms(src, getattr(scene, "profession_key", "") or "")
        if not allow_llm or not self.llm.available:
            return TranslationResult(
                src=src, source="failed", locked=locked,
                error="未启用 AI，且本地词库无匹配",
                elapsed_ms=(time.perf_counter() - t0) * 1000)

        try:
            raw = self._call_llm(src, scene, locked)
        except LLMError as e:
            return TranslationResult(
                src=src, source="failed", locked=locked, error=str(e),
                elapsed_ms=(time.perf_counter() - t0) * 1000)

        text, fixes, unresolved = self.apply_term_lock(raw, locked)
        return TranslationResult(
            src=src, text=text, source="llm", confidence=0.8,
            locked=locked, fixes=fixes, unresolved=unresolved,
            elapsed_ms=(time.perf_counter() - t0) * 1000)

    def translate_async(self, src: str, scene: SceneContext | None,
                        callback: Callable[[TranslationResult], None],
                        allow_llm: bool = True) -> TranslationResult | None:
        """本地能出就立刻返回（同步）；否则后台调 AI，结果走 callback。

        返回值为「本地命中的结果」或 None（表示已转异步）。
        """
        local = self.translate_local(src, scene)
        if local is not None:
            return local
        locked = self.extract_terms(src, getattr(scene, "profession_key", "") or "")

        if not allow_llm or not self.llm.available:
            # v2.6：没有 Key（或用户选了纯本地以外的情况）→ 走免费在线翻译。
            if allow_llm and self.free is not None and self.free.available:
                self._free_forward(src, locked, callback)
                return None
            callback(TranslationResult(
                src=src, source="failed", locked=locked,
                error="未启用 AI，且本地词库无匹配"))
            return None

        industry = getattr(scene, "profession_key", "") or ""
        cache_key = f"{src}|{industry}"
        hit = self.llm.cached(cache_key)
        if hit:
            text, fixes, unresolved = self.apply_term_lock(hit, locked)
            callback(TranslationResult(src=src, text=text, source="llm",
                                       confidence=0.8, locked=locked,
                                       fixes=fixes, unresolved=unresolved))
            return None

        def _done(content: str | None, err: str) -> None:
            if content is None:
                callback(TranslationResult(src=src, source="failed",
                                           locked=locked, error=err))
                return
            self.llm.put_cache(cache_key, content)
            text, fixes, unresolved = self.apply_term_lock(content, locked)
            callback(TranslationResult(src=src, text=text, source="llm",
                                       confidence=0.8, locked=locked,
                                       fixes=fixes, unresolved=unresolved))

        self.llm.chat_async(
            SYSTEM_TRANSLATE,
            self._build_prompt(src, scene, locked),
            _done, temperature=0.2, max_tokens=768, timeout=15.0)
        return None


    # ---------------- 免费在线翻译（无 Key 兜底，v2.6） ----------------
    def _free_forward(self, src: str, locked: list[tuple[str, str]],
                      callback: Callable[[TranslationResult], None]) -> None:
        """中→英：免费引擎出稿 + 术语强制对齐（与 AI 路径同一把尺子）。"""

        def _done(text: str | None, provider: str, err: str) -> None:
            if text is None:
                callback(TranslationResult(
                    src=src, source="failed", locked=locked,
                    error=err or "免费翻译失败（网络不可达）"))
                return
            out, fixes, unresolved = self.apply_term_lock(text, locked)
            callback(TranslationResult(
                src=src, text=out, source="free", confidence=0.7,
                locked=locked, fixes=fixes, unresolved=unresolved,
                provider=provider))

        self.free.translate_async(src, "zh-CN", "en", _done)

    def _free_back(self, src: str, locked: list[tuple[str, str]],
                   callback: Callable[[TranslationResult], None]) -> None:
        """英→中：免费引擎回译（术语表已在中文侧，不再做反向强制对齐）。"""

        def _done(text: str | None, provider: str, err: str) -> None:
            if text is None:
                callback(TranslationResult(
                    src=src, source="failed", locked=locked, direction="en2zh",
                    error=err or "免费回译失败（网络不可达）"))
                return
            callback(TranslationResult(
                src=src, text=text, source="free", confidence=0.7,
                locked=locked, direction="en2zh", provider=provider,
                unresolved=self.check_terms_zh(text, locked)))

        self.free.translate_async(src, "en", "zh-CN", _done)

    # ---------------- 提示词 ----------------
    def _build_prompt(self, src: str, scene: SceneContext | None,
                      locked: list[tuple[str, str]]) -> str:
        ctx = []
        if scene is not None:
            if getattr(scene, "hierarchy", ""):
                ctx.append(f"行业岗位：{scene.hierarchy}")
            if getattr(scene, "software_name", ""):
                ctx.append(f"使用软件：{scene.software_name}")
        head = ("背景：" + "；".join(ctx) + "\n") if ctx else ""
        return (f"{head}{build_glossary(locked)}"
                f"待译中文：\n{src}")

    def _call_llm(self, src: str, scene: SceneContext | None,
                  locked: list[tuple[str, str]]) -> str:
        return self.llm.chat(SYSTEM_TRANSLATE,
                             self._build_prompt(src, scene, locked),
                             temperature=0.2, max_tokens=768, timeout=20.0)

    # ---------------- L5 术语强制对齐 ----------------
    def apply_term_lock(self, en: str, locked: list[tuple[str, str]]
                        ) -> tuple[str, list[str], list[str]]:
        """确保每个锁定术语都以标准译名出现在译文里。

        发现 AI 自由发挥时，用标准译名替换掉它最可能的错误写法。
        返回 (修正后的译文, 被纠正的术语, 仍未能对齐的术语)。
        """
        if not en or not locked:
            return en, [], []
        fixes: list[str] = []
        unresolved: list[str] = []
        out = en

        for zh, std in locked:
            if self._contains_term(out, std):
                continue
            # 尝试在译文里找到该术语的别名并替换
            replaced = False
            for alias in self._aliases_of(zh, std):
                pat = re.compile(r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])",
                                 re.IGNORECASE)
                if pat.search(out):
                    out = pat.sub(std, out, count=1)
                    replaced = True
                    break
            if replaced:
                fixes.append(f"{zh}→{std}")
            else:
                unresolved.append(f"{zh}(应作 {std})")
        return out, fixes, unresolved

    @staticmethod
    def _contains_term(text: str, term: str) -> bool:
        """宽松判定：术语是否已以标准译名（或同一词根的其他词形）出现。

        必须容忍词形变化，否则会把 "Weld the welded frame" 里的 Weld 强行换成
        "Welding"，产出一句语法都不对的英文 —— 这比术语不一致更糟。
        """
        def norm(s: str) -> str:
            return re.sub(r"[\s\-]+", "", s or "").lower()

        t = norm(term)
        s = norm(text)
        if not t:
            return True
        if t in s:
            return True
        if t.endswith("s") and t[:-1] in s:
            return True
        if (t + "s") in s:
            return True
        # 同一词根：Welding ~ welded / Chamfer ~ chamfered / Bore ~ bored
        stem = t
        for suf in ("ings", "ing", "eds", "ed", "es", "s"):
            if stem.endswith(suf) and len(stem) - len(suf) >= 3:
                stem = stem[:-len(suf)]
                break
        if len(stem) >= 4:
            for w in re.split(r"[^a-z0-9]+", s):
                if len(w) >= 4 and w.startswith(stem):
                    return True
        return False

    def _aliases_of(self, zh: str, std: str) -> list[str]:
        """取该术语的非标准英文写法（词库里的别名 + 常见变体）。"""
        out: list[str] = []
        try:
            for t in self.engine.lexicons.find_by_zh(zh)[:6]:
                for a in (t.var_en or []):
                    a = a.strip()
                    if a and a.lower() != std.lower() and len(a) >= 3:
                        out.append(a)
                # 定语位置化的写法：把首词移到末尾（"welding fixture" ↔ "fixture welding"）
                words = _WORD_SPLIT.split(std)
                if len(words) >= 2:
                    out.append(" ".join(words[1:] + words[:1]))
                if len(words) >= 2:
                    out.append("".join(words))       # 去掉分隔符的连写
        except Exception:
            pass
        # 长词优先，避免短别名误伤
        return sorted(set(out), key=len, reverse=True)

    # ---------------- 闭环：学习 ----------------
    def learn(self, src: str, dst: str, scene: SceneContext | None = None,
              source: str = "user") -> bool:
        industry = getattr(scene, "profession_key", "") or ""
        ok = self.memory.learn(src, dst, industry, source)
        if ok:
            self.llm.put_cache(f"{src}|{industry}", dst)
        return ok

    def stats(self) -> dict:
        return {
            "memory": self.memory.stats(),
            "terms": len(self.term_map()),
            "llm_available": self.llm.available,
            "llm_error": self.llm.last_error,
        }


# ---------------- 自测 ----------------
if __name__ == "__main__":
    import json
    import tempfile

    from core.engine import TermEngine

    tmp = Path(tempfile.gettempdir()) / "zhiyu_tm_selftest.db"
    tmp.unlink(missing_ok=True)

    eng = TermEngine()
    mem = TranslationMemory(tmp)
    tr = Translator(eng, mem)
    scene = SceneContext(profession_key="mechanical",
                         hierarchy="机械工程 / 结构设计 / 设计员")

    print("术语表规模:", len(tr.term_map()))
    print("\n--- 术语抽取（贪心最长匹配） ---")
    for s in ["该零件底部需加工沉孔，螺纹孔与倒角按图", "立柱与顶板满焊，表面拉丝处理"]:
        print(f"  {s}\n    -> {tr.extract_terms(s)}")

    print("\n--- L1/L2 记忆库 ---")
    mem.learn("该零件底部需加工沉孔，深度 8mm",
              "A counterbore shall be machined at the bottom of this part, depth 8 mm.",
              "mechanical")
    for s in ["该零件底部需加工沉孔，深度 8mm", "该零件底部需加工沉孔，深度 10mm"]:
        r = tr.translate_local(s, scene)
        print(f"  {s}\n    -> [{r.source_label}] {r.confidence} {r.text[:60]}")

    print("\n--- L3 术语库整段覆盖 ---")
    r = tr.translate_local("沉孔、倒角", scene)
    print(f"  -> {r.source if r else None} | {r.text if r else ''}")

    print("\n--- L5 术语强制对齐 ---")
    out, fixes, un = tr.apply_term_lock(
        "The hole should be spot faced and the edge rounded off.",
        [("沉孔", "Counterbore"), ("倒角", "Chamfer")])
    print(f"  译文: {out}\n  纠正: {fixes}\n  未对齐: {un}")

    print("\n--- 管线统计 ---")
    print(json.dumps(tr.stats(), ensure_ascii=False, indent=2))
    mem.close()
    tmp.unlink(missing_ok=True)
