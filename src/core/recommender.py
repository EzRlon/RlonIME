"""
智语输入法 — 智能推荐引擎

两层设计，离线优先、可选增强：

【本地层】默认启用，零依赖、零延迟
  1. 词组共现：从词库的 scene/brief 文本中挖掘术语关联，构建共现图谱
  2. 场景权重：结合三级细分（行业/职业/职位）的 focus_tags 加权
  3. 表单/列表等结构化输入场景的字段名匹配
  4. 序列推荐：根据用户最近上屏的词，预测下一个可能需要的术语

【增强层】可选启用，用户填入 API Key 后生效
  - 调用大模型根据当前输入 + 场景 + 三级细分，生成更贴合语境的词组建
  - 失败自动回退到本地层，不影响可用性

不重复造轮子：
  - 中文分词/关键词抽取：复用 pypinyin 的分词能力 + 简易 n-gram
  - 缓存：标准库 functools.lru_cache
  - 网络请求：标准库 urllib（不引入额外依赖）
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import secrets as _secrets                    # noqa: E402  S03
from core.config import get_config
# S04/S07：端点校验与响应上限直接复用 llm 模块，避免两份实现漂移（无循环依赖）
from core.llm import read_limited, validate_endpoint    # noqa: E402
from core.models import Candidate, SceneContext, Term


# ============================================================
# 共现图谱：从术语的文本描述中挖掘关联
# ============================================================

# 中文关键词抽取：按标点/空格切分，取长度 >= 2 的中文片段
_CN_SPLIT = re.compile(r"[，。、；：,.;:/\s（）()【】\[\]—\-]+")


def extract_keywords(text: str) -> list[str]:
    """从描述文本中抽取候选关键词。"""
    if not text:
        return []
    parts = _CN_SPLIT.split(text)
    out = []
    for p in parts:
        p = p.strip()
        if len(p) >= 2:
            out.append(p)
        # 长片段再做 2-gram 抽取，捕获「工程图」「螺纹孔」这类内嵌词
        if len(p) >= 4:
            for i in range(len(p) - 1):
                g = p[i:i + 2]
                if all("\u4e00" <= c <= "\u9fff" for c in g):
                    out.append(g)
    return out


class CooccurrenceGraph:
    """
    术语共现图谱。

    构建方式：对每个术语，把它的 scene / brief / standard 等文本抽成关键词，
    术语本身与这些关键词建立双向边。查询时，用当前输入命中的术语去查共同
    关键词，从而找到"经常一起出现"的术语。
    """

    def __init__(self):
        # keyword -> {term_key: weight}
        self.kw_to_terms: dict[str, Counter] = defaultdict(Counter)
        # term_key -> set(keyword)
        self.term_kws: dict[str, set[str]] = {}
        self._built = False

    @staticmethod
    def term_key(t: Term) -> str:
        return f"{t.profession}|{t.zh}"

    def build(self, terms: list[Term]) -> "CooccurrenceGraph":
        for t in terms:
            tk = self.term_key(t)
            kws: set[str] = set()
            kws.update(extract_keywords(t.scene))
            kws.update(extract_keywords(t.brief))
            # 中文术语自身也作为关键词（如"沉孔"出现在"沉头螺钉"描述里）
            if t.zh and len(t.zh) >= 2:
                kws.add(t.zh)
            self.term_kws[tk] = kws
            for kw in kws:
                # 词频高的术语在共现中权重略高，但做归一化避免压倒性
                self.kw_to_terms[kw][tk] += 1.0 + t.freq / 2000.0
        self._built = True
        return self

    def related(self, seed_terms: list[Term], topn: int = 40) -> Counter:
        """
        给定种子术语，返回关联术语及得分。
        得分 = 与所有种子共享关键词的累计权重。
        """
        scores: Counter = Counter()
        for seed in seed_terms:
            sk = self.term_key(seed)
            for kw in self.term_kws.get(sk, ()):  # type: ignore[arg-type]
                for tk, w in self.kw_to_terms.get(kw, {}).items():
                    if tk != sk:
                        scores[tk] += w
        return scores.most_common(topn)  # type: ignore[return-value]


# ============================================================
# 推荐结果
# ============================================================

@dataclass
class Suggestion:
    """智能推荐结果。"""

    term: Term
    score: float = 0.0
    reason: str = ""          # 推荐原因，如 "常与 沉孔 一起使用"
    source: str = "local"     # local / llm
    detail: dict[str, float] = field(default_factory=dict)


# ============================================================
# 智能推荐引擎
# ============================================================

class SmartRecommender:
    """智能词组推荐：本地共现 + 场景权重，可选大模型增强。"""

    def __init__(self, engine=None, lexicon_manager=None):
        self.config = get_config()
        self.engine = engine
        self.mgr = lexicon_manager or (engine.lexicons if engine else None)
        self.graph = CooccurrenceGraph()
        if self.mgr is not None:
            self.graph.build(self.mgr.all_terms)
        self._llm_cfg = self.config.llm
        self._llm_cache: dict[str, list[str]] = {}

    # ---------------- 主入口 ----------------
    def suggest(
        self,
        query: str,
        scene: SceneContext,
        accepted: list[str] | None = None,
        limit: int = 6,
        use_llm: bool | None = None,
    ) -> list[Suggestion]:
        """
        推荐词组建。

        query:    当前输入（拼音或中文）
        scene:    当前场景（含三级细分）
        accepted: 用户最近已上屏的词（用于序列推荐）
        """
        out: list[Suggestion] = []
        out.extend(self._recommend_by_context(scene, accepted or [], limit))
        out.extend(self._recommend_by_cooccurrence(query, scene, accepted or [], limit))

        # 去重（保留高分）
        merged: dict[str, Suggestion] = {}
        for s in sorted(out, key=lambda x: x.score, reverse=True):
            k = CooccurrenceGraph.term_key(s.term)
            if k not in merged:
                merged[k] = s
        result = list(merged.values())[:limit]

        # 大模型增强（可选）
        if use_llm is None:
            use_llm = bool(self._llm_cfg.get("enabled")) and bool(self._llm_cfg.get("api_key"))
        if use_llm:
            result = self._enhance_with_llm(query, scene, accepted or [], result, limit)

        for s in result:
            s.score = round(s.score, 2)
        return result

    # ---------------- 本地策略 1：场景 + 三级细分 ----------------
    def _recommend_by_context(
        self, scene: SceneContext, accepted: list[str], limit: int
    ) -> list[Suggestion]:
        """
        基于场景标签与三级细分 focus_tags 推荐。
        职位 boost 越高，该场景下的术语权重越大。
        """
        if self.mgr is None:
            return []

        focus = set(scene.focus_tags or [])
        boost = float(scene.position_boost or 1.0)
        terms = self.mgr.get_terms(scene.profession_key)
        if not terms:
            return []

        accepted_set = set(accepted)
        out: list[Suggestion] = []

        for t in terms:
            if t.zh in accepted_set:
                continue
            hit = set(t.scene_tags) & focus
            if not hit:
                continue
            # 命中 focus_tags 的场景越多、词频越高，推荐越靠前
            sc = len(hit) * 30.0 * boost + t.freq / 25.0
            reason = f"贴合当前岗位（{'/'.join(sorted(hit))}）"
            out.append(Suggestion(term=t, score=sc, reason=reason, source="local",
                                  detail={"focus": len(hit) * boost}))

        out.sort(key=lambda s: s.score, reverse=True)
        return out[:limit * 2]

    # ---------------- 本地策略 2：共现图谱 ----------------
    def _recommend_by_cooccurrence(
        self, query: str, scene: SceneContext, accepted: list[str], limit: int
    ) -> list[Suggestion]:
        """基于已输入/已上屏的词，推荐常一起使用的术语。"""
        if self.mgr is None:
            return []

        # 收集种子：查询命中的 Top 术语 + 用户刚上屏的词
        seeds: list[Term] = []
        if query.strip():
            try:
                hits = self.engine.search(query, scene, limit=3) if self.engine else []
                seeds.extend(c.term for c in hits)
            except Exception:
                pass
        for zh in accepted[:5]:
            found = self.mgr.find_by_zh(zh)
            if found:
                seeds.append(found[0])
        if not seeds:
            return []

        seed_zh = {s.zh for s in seeds}
        related = self.graph.related(seeds, topn=60)

        out: list[Suggestion] = []
        by_key = {CooccurrenceGraph.term_key(t): t for t in self.mgr.all_terms}

        for tk, w in related:
            t = by_key.get(tk)
            if t is None or t.zh in seed_zh:
                continue
            # 同领域优先，跨领域也给少量机会（工程图场景）
            same = t.profession == scene.profession_key
            if not same and "drawing_2d" not in scene.scene_tags:
                continue
            sc = w * (12.0 if same else 4.0)
            # 场景标签叠加
            if set(t.scene_tags) & set(scene.scene_tags or []):
                sc *= 1.25
            anchor = seeds[0].zh
            out.append(Suggestion(
                term=t, score=sc,
                reason=f"常与「{anchor}」配合使用",
                source="local", detail={"cooccur": w},
            ))

        out.sort(key=lambda s: s.score, reverse=True)
        return out[:limit * 2]

    # ---------------- 增强层：大模型 ----------------
    def _enhance_with_llm(
        self, query: str, scene: SceneContext, accepted: list[str],
        base: list[Suggestion], limit: int
    ) -> list[Suggestion]:
        """
        调用大模型生成推荐词组。

        设计要点：
        - 只把「当前输入 + 岗位链 + 候选术语」发给模型，控制 token 成本
        - 结果按名称在本地词库中反查；查不到的新词作为“建议新增”返回
        - 任何异常都静默回退到本地结果，绝不阻塞用户输入
        """
        cfg = self._llm_cfg
        cache_key = f"{query}|{scene.describe()}|{','.join(accepted[:3])}"
        if cache_key in self._llm_cache:
            names = self._llm_cache[cache_key]
        else:
            names = self._call_llm(query, scene, accepted, base)
            self._llm_cache[cache_key] = names
        if not names:
            return base[:limit]

        # 反查本地词库
        enhanced: list[Suggestion] = []
        seen: set[str] = set()
        if self.mgr is not None:
            for name in names:
                hits = self.mgr.find_by_zh(name) or self.mgr.find_by_en(name)
                if hits:
                    t = hits[0]
                    k = CooccurrenceGraph.term_key(t)
                    if k in seen:
                        continue
                    seen.add(k)
                    enhanced.append(Suggestion(
                        term=t, score=200.0, source="llm",
                        reason="AI 推荐", detail={"llm": 1.0},
                    ))

        # AI 结果优先，本地结果补齐
        if self.mgr is not None:
            for s in base:
                k = CooccurrenceGraph.term_key(s.term)
                if k not in seen:
                    seen.add(k)
                    enhanced.append(s)
        return enhanced[:limit]

    def _call_llm(
        self, query: str, scene: SceneContext, accepted: list[str],
        base: list[Suggestion]
    ) -> list[str]:
        """实际发起大模型请求（OpenAI 兼容接口）。失败返回空列表。

        S03：api_key 可能是 DPAPI 密文或旧明文，统一解密后再放进 Authorization。
        S04：请求发出前校验端点；S07：响应体有字节上限。任何异常都降级为空列表。
        """
        # S03：兼容配置里的密文（新）与明文（老用户）
        api_key = _secrets.unprotect(str(self._llm_cfg.get("api_key", "") or ""))
        base_url = str(self._llm_cfg.get(
            "base_url", "https://api.openai.com/v1") or "").rstrip("/")
        model = self._llm_cfg.get("model", "gpt-4o-mini")
        if not api_key:
            return []

        candidates = "、".join(s.term.zh for s in base[:12])
        prompt = (
            f"你是{scene.describe()}岗位的专业技术助手。"
            f"用户正在此岗位语境下输入，当前输入为「{query or '(空)'}」。"
            + (f"刚使用了：{'、'.join(accepted[:3])}。" if accepted else "")
            + f"已有候选：{candidates}。"
            "请推荐 6 个该岗位在此语境下最可能需要的专业中文术语，"
            "只返回术语名称，用「、」分隔，不要解释。"
        )

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 120,
        }
        try:
            # S04：拒绝不安全的自定义端点，绝不静默改用别的地址
            ok, why = validate_endpoint(base_url)
            if not ok:
                raise ValueError(f"接口地址不安全：{why}")
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            # S07：带字节上限读取，防被劫持端点把托盘进程撑爆
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(read_limited(resp).decode("utf-8"))
            text = data["choices"][0]["message"]["content"]
            return [p.strip() for p in re.split(r"[、,，\n]", text) if p.strip()][:8]
        except Exception:
            return []

    # ---------------- 自检 ----------------
    def self_test(self) -> dict[str, object]:
        return {
            "graph_built": self.graph._built,
            "keywords": len(self.graph.kw_to_terms),
            "terms_indexed": len(self.graph.term_kws),
            "llm_enabled": bool(self._llm_cfg.get("enabled")),
            "llm_has_key": bool(self._llm_cfg.get("api_key")),
        }


if __name__ == "__main__":
    from core.engine import TermEngine
    from scene.detector import SceneDetector

    eng = TermEngine()
    rec = SmartRecommender(engine=eng, lexicon_manager=eng.lexicons)
    det = SceneDetector()

    print("=" * 76)
    print("  智能推荐引擎自检")
    print("=" * 76)
    print(json.dumps(rec.self_test(), ensure_ascii=False, indent=2))

    scene = det.lock(profession="mechanical", software="solidworks")
    scene.scene_tags = ["drawing_2d", "annotation"]
    scene.focus_tags = ["drawing_2d", "annotation"]
    scene.position_boost = 1.3
    scene.hierarchy = "机械工程 / 结构设计 / 审图/校核"

    print()
    print("=" * 76)
    print(f"  场景：{scene.hierarchy}（审图校核，boost=1.3）")
    print("=" * 76)
    for q, acc in [("chenkong", []), ("", ["沉孔"]), ("gongcha", ["形位公差"])]:
        print(f"\n  输入[{q or '空'}] 已用{acc or '无'}:")
        for s in rec.suggest(q, scene, accepted=acc, limit=5):
            print(f"    {s.term.zh:12s} | {s.term.en:26s} | {s.score:7.2f} | {s.reason}")
