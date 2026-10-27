"""
智语输入法 — 术语检索引擎

核心职责：
1. 多路匹配：全拼 / 简拼 / 中文前缀 / 英文片段
2. 多维加权：领域匹配 + 场景匹配 + 词频 + 历史使用 + 自定义
3. 中英双语候选：命中中文时，同步给出英文专业表述（工程图一键插入）

不重复造轮子：
- 拼音转换复用 pypinyin（见 core/lexicon.py）
- 历史与词频持久化复用 SQLite（标准库）
- 排序复用 heapq / 内建 sorted，不自己写排序算法
"""
from __future__ import annotations

import math
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import get_config
from core.lexicon import LexiconManager, pinyin_of, abbr_of
from core.models import Candidate, SceneContext, Term
from core.phrases import PhraseStore

# 分段概率归一化常数：jieba 语料总词次（约 6.5e7）
_LOG_TOTAL = math.log(6.5e7)


class UsageStore:
    """
    使用历史与词频存储（SQLite，标准库）。

    表结构：
      usage(zh TEXT PRIMARY KEY, count INT, last_used REAL, profession TEXT)
    用途：实现"词频排序 + 历史记录"，让常用术语自动靠前。
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage (
                zh          TEXT PRIMARY KEY,
                count       INTEGER NOT NULL DEFAULT 0,
                last_used   REAL    NOT NULL DEFAULT 0,
                profession  TEXT    NOT NULL DEFAULT ''
            )
            """
        )
        self.conn.commit()
        self._cache: dict[str, int] = {}
        self._reload()

    def _reload(self) -> None:
        cur = self.conn.execute("SELECT zh, count FROM usage")
        self._cache = {zh: cnt for zh, cnt in cur.fetchall()}

    def count_of(self, zh: str) -> int:
        return self._cache.get(zh, 0)

    def record(self, zh: str, profession: str = "") -> int:
        now = time.time()
        self.conn.execute(
            """
            INSERT INTO usage (zh, count, last_used, profession)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(zh) DO UPDATE SET
                count = count + 1,
                last_used = excluded.last_used
            """,
            (zh, now, profession),
        )
        self.conn.commit()
        self._cache[zh] = self._cache.get(zh, 0) + 1
        return self._cache[zh]

    def recent(self, limit: int = 10) -> list[tuple[str, int]]:
        cur = self.conn.execute(
            "SELECT zh, count FROM usage ORDER BY last_used DESC LIMIT ?", (limit,)
        )
        return list(cur.fetchall())


class TermEngine:
    """术语检索引擎。"""

    def __init__(self, lexicon_manager: LexiconManager | None = None):
        self.config = get_config()
        self.lexicons = lexicon_manager or LexiconManager().load_all()
        self.usage = UsageStore(self.config.user_data_dir / "usage.db")
        # v2.4.0（S3）：图纸技术要求句库，只认短码（yq / yq07），
        # 不进 pinyin/abbr 索引 —— 绝不污染常规候选（K 键那类场景尤其致命）。
        self.phrases = PhraseStore()

    # ---------------- 检索主入口 ----------------
    def search(
        self,
        query: str,
        scene: SceneContext,
        limit: int | None = None,
        cross_profession: bool = False,
    ) -> list[Candidate]:
        """
        query: 用户输入的拼音串或中文
        scene: 当前场景（决定领域与场景加权）
        cross_profession: 是否跨领域召回（默认只召当前领域 + 无领域属性词）
        """
        q = (query or "").strip()
        if not q:
            return self._default_candidates(scene, limit)

        limit = limit or self.config.ranking.max_candidates
        pool: dict[str, Candidate] = {}

        def add(term: Term, match_type: str, base: float) -> None:
            key = f"{term.profession}|{term.zh}"
            if key in pool:
                # 同一词根多路命中，取最高基础分
                if base > pool[key].detail.get("base", 0):
                    pool[key].detail["base"] = base
                    pool[key].match_type = match_type
                return
            c = Candidate(term=term, match_type=match_type, group="pro")
            c.detail["base"] = base
            pool[key] = c

        q_low = q.lower()

        # 0) 技术要求句库（v2.4.0 / S3）：短码直查（yq / yq07）。
        #    整句带标点、中英成对，只走短码这一条路，绝不进拼音索引。
        for pc in self.phrases.search(q_low, limit):
            add(pc.term, pc.match_type, float(pc.detail.get("base", 0.0)))

        # 1) 拼音全拼 / 前缀
        #    精确匹配权重最高；前缀匹配按"多打出的字母越少越相近"递减，
        #    这样即使用户完整输入，列表也不会只剩一条。
        exact_hit = False
        prefix_terms: list[tuple[int, Term]] = []
        for key, terms in self.lexicons.term_index.items():
            if key == q_low:
                exact_hit = True
                for t in terms:
                    add(t, "full_pinyin", 100.0)
            elif key.startswith(q_low) and len(q_low) >= 1:
                extra = len(key) - len(q_low)
                prefix_terms.append((extra, key, terms))
        # 前缀补充：距离越近分越高。
        # UX-P0-7 精确命中降噪：有全拼精确命中时，前缀分硬压一档
        # （<=79，在精确档 100 之下、简拼 75 之上仅一线），
        # 召回上限同时从 limit*3 收窄到 limit —— 精确命中时用户要的就是那个词，
        # 其余前缀词只是噪声。
        prefix_cap = limit if exact_hit else limit * 3
        for extra, key, terms in sorted(prefix_terms, key=lambda x: x[0])[:prefix_cap]:
            score = max(30.0, 88.0 - extra * 9.0)
            if exact_hit:
                score = min(score, 79.0)
            for t in terms:
                add(t, "prefix", score)
        c_detail = {"exact": exact_hit}

        # 2) 简拼
        abbr_exact = False
        for key, terms in self.lexicons.abbr_index.items():
            if key == q_low and len(q_low) >= 2:
                abbr_exact = True
                for t in terms:
                    add(t, "abbr", 75.0)

        # 3) 中文前缀（用户直接输入中文时）
        if any("\u4e00" <= ch <= "\u9fff" for ch in q):
            for t in self.lexicons.all_terms:
                if t.zh.startswith(q):
                    add(t, "zh_prefix", 95.0)
                elif q in t.zh:
                    add(t, "zh_contains", 60.0)

        # 4) 英文片段（用于反查中文）
        # UX-P1-5 反查置主位：拼音全拼/简拼都无精确命中时，英文命中不再是
        # 辅助信息而是用户的主要意图（敲 counter 就是想要 沉孔），base 70→96
        # 压过前缀档；有拼音精确命中时维持 70（英文只是补充展示）。
        # v2.6（日常手感）：**1~2 个字母不做英文反查主位**。实测敲 `k` 会把
        # 「折弯系数」（别名 K Factor）顶到第一、敲 `a` 顶出「线轮廓度」——
        # 日常用户只想打"卡/啊"。短查询时英文命中降为辅助档（base 70）。
        if q_low.isascii() and " " not in q_low:
            short = len(q_low) < 3
            en_base = 70.0 if (exact_hit or abbr_exact or short) else 96.0
            # 短查询用独立 match_type：它不进领域加权（见 _score 的 weak 判定），
            # 否则「k → 折弯系数」这种别名命中仍会被领域分顶到第一。
            mtype = "en_short" if short else "en"
            for t in self.lexicons.find_by_en(q_low):
                add(t, mtype, en_base)

        # 5) 通用词库（v2.3 常规输入）—— 没有匹配就回归「输入法本来的能力」
        #    低优先级层：profession 为空、不加领域分；专业精确命中时仅少量补位。
        #    数据：CC-CEDICT × jieba 词频（含英文释义，候选译法列直接可用）。
        # v3.0.3：short = 1~2 个字母。此时"精确单字"几乎没有意义
        # （k → 卡/喀/咔/咖 全是生僻字），用户一定在打更长的词，
        # 所以前缀常用字要给足位置、并把分提到精确档附近。
        short_q = len(q_low) <= 2
        common_pool: list[Candidate] = []
        if q_low.isascii() and self.lexicons.common_loaded:
            zh_have = {c.term.zh for c in pool.values()}
            cap = 3 if (exact_hit or abbr_exact) and not short_q else limit

            def _add_common(zh: str, en: str, fq: int, code: str,
                            base: float, mtype: str) -> None:
                if (zh in zh_have or zh in self.lexicons.zh_set
                        or len(common_pool) >= cap):
                    return
                zh_have.add(zh)
                t = Term(zh=zh, en=en, freq=fq, profession="")
                t.pinyin = code
                c = Candidate(term=t, match_type=mtype, group="common")
                c.detail["base"] = base
                common_pool.append(c)

            exact_base = 78.0 if short_q else 100.0
            for zh, en, fq, _raw in self.lexicons.common_exact(q_low)[:cap]:
                _add_common(zh, en, fq, q_low,
                            exact_base + min(fq, 500) / 100.0, "common")
            if len(common_pool) < cap:
                # 短查询要跨拼音组挑高频（否则 k 的前几个永远是 ka 组的
                # 卡/咖/喀/咔，看不到 开/看/可/口）——多取几倍再按词频排。
                need = cap - len(common_pool)
                if short_q:
                    starts = self.lexicons.common_top_prefix(q_low, need * 2)
                else:
                    starts = self.lexicons.common_starts(q_low, need)
                for zh, en, fq, code, extra in starts:
                    if short_q:
                        # 单字母/双字母：前缀词按词频给分（开/看/可 要压过 喀/咔）
                        b = 80.0 + min(fq, 900) / 60.0 - extra * 1.5
                    else:
                        b = max(30.0, 88.0 - extra * 9.0)
                        if exact_hit or abbr_exact:
                            b = min(b, 79.0)
                    _add_common(zh, en, fq, code, b, "common_prefix")

        # ---------------- 打分 ----------------
        candidates = list(pool.values())
        candidates.extend(common_pool)
        for c in candidates:
            self._score(c, scene)

        # 领域过滤：非跨领域模式下，压制其他领域结果（不硬删，保留但降权）
        # v3.0.3：先按「匹配完整度」分层，再按分数排 —— 见 _rank_of 的说明。
        candidates.sort(key=lambda c: (self._rank_of(c, short_q), -c.score))

        ranked = [c for c in candidates if c.score > 0]
        if not cross_profession:
            # 优先保留当前领域 + 通用词，其他领域若分数很低则剔除
            active = scene.profession_key
            if exact_hit and active:
                # UX-P0-7：精确命中时跨行业硬过滤——用户全拼都打完了，
                # 其他行业的同音词（score>=60 逃逸口）一律不得入列。
                ranked = [c for c in ranked
                          if c.term.profession in (active, "")]
            else:
                # 无精确命中时维持现状：跨行业高分词仍可见，保召回
                ranked = [
                    c for c in ranked
                    if c.term.profession == active
                    or c.detail.get("cross", 0) > 0
                    or c.term.profession == ""
                    or c.score >= 60
                ]
        # v2.3：同词去重（按分数序，专业版通常更高分故优先）。
        # 必须放在场景过滤**之后**：否则「专业版将被场景过滤、共享版已被
        # 提前去重」会导致整词消失（量产/验收在跨行业场景实测踩坑）。
        dedup: list[Candidate] = []
        seen_zh: set[str] = set()
        for c in ranked:
            if c.term.zh in seen_zh:
                continue
            seen_zh.add(c.term.zh)
            dedup.append(c)
        ranked = dedup
        # 分区配额（v3.0.3）：整词命中的一堆日常字不能把专业词全挤掉。
        # 打 chen 时前 6 位是陈/称/趁/填/宸/尘，第 7 位起要能看到「沉孔」——
        # 这正是用户说的"智能匹配 + 正常输入法匹配两个区域"。
        if not short_q:
            top = [c for c in ranked if self._rank_of(c, short_q) == 0]
            rest = [c for c in ranked if self._rank_of(c, short_q) > 0]
            if rest and len(top) >= limit:
                keep = max(limit - 3, (limit * 5) // 9)
                ranked = top[:keep] + rest[:limit - keep]
            else:
                ranked = ranked[:limit]
        else:
            ranked = ranked[:limit]

        # ★ 一个都不命中 → 前缀退化，绝不允许凭空造候选。
        # 上一版在这里做了「补足同领域高频词」，结果用户敲 zhizhang 会看到
        # 沉孔/圆角/倒角/拉伸凸台 —— 与输入毫无关系的词。这比候选空着严重得多：
        # 用户会直接判定整个工具是坏的。
        if not ranked:
            seg = self._segment_candidates(q_low, scene)
            if seg:
                return seg
            return self._prefix_fallback(q, scene, limit)

        # ★ 版本三：把「候选不足 4 条就补同领域高频词」整段删除。
        #
        # 前两版都在给这段打补丁（先加 ranked 非空前置，再加"必须存在精确命中"
        # 前置），但 `wx` / `che` / `xyz` 依然漏「沉孔」—— 因为 `che` 恰好是
        # 「车」的全拼、`wx`/`xyz` 恰好是若干词的简拼，精确命中的前置条件形同虚设。
        #
        # 真正的病根不在门槛高低，而在这段兜底**本身与输入无关**：
        # 它的候选池是"当前领域按 freq 排序的全部词"，所以无论你打什么，
        # 排第一的永远是同一个高频词。给定同一个输入，兜底产出恒定不变 ——
        # 这从结构上就不可能不出错，只是错多错少的区别。
        #
        # 而且它想解决的问题（候选窗显得空）根本不存在：候选条是横向的，
        # 一条候选也占不满，写 `chenkong` 只该出「沉孔」。商业输入法在完整
        # 输入后也只给真正相关的词。宁少勿滥 —— 少一条用户不会察觉，
        # 多一条无关词用户会认为整个输入法是坏的。
        #
        # 相关的近音/近形候选已由上面两路覆盖，无需兜底：
        #   · `chenkog` 少打字母 → 前缀匹配 + _prefix_fallback 救回
        #   · `chen`    想继续打 → 前缀匹配已列出 chen* 全部词
        return ranked

    # ---------------- 匹配档次（v3.0.3） ----------------
    # 用户反馈：打 shei 出不来「谁」，打 shi 前 8 位全是实体/蚀刻/石墨 ——
    # 「是」根本看不见。根因是纯分数混排：专业词有领域加权，前缀命中
    # （base 88）能压过通用词的精确命中（base 100 但零加权）。
    #
    # 现在改成「先看匹配得有多完整，再看分」：
    #   0 整词对上（全拼/简拼/句库/通用词/中文前缀）
    #   1 英文反查
    #   2 专业前缀延伸
    #   3 通用前缀延伸
    # 1~2 个字母是特例：用户几乎总在打常用字（k→开/看/可），
    # 此时通用前缀提到第 1 层，专业短别名（K Factor）沉到后面。
    _RANK0 = frozenset({"full_pinyin", "abbr", "phrase", "common",
                        "segment", "zh_prefix"})

    def _rank_of(self, c: Candidate, short: bool) -> int:
        mt = c.match_type
        if mt in self._RANK0:
            return 0
        if short:
            if mt == "common_prefix":
                return 1
            if mt in ("en", "en_short"):
                return 2
            return 3
        if mt in ("en", "en_short", "zh_contains"):
            return 1
        if mt in ("prefix", "prefix_partial", "en_prefix"):
            return 2
        return 3

    # ---------------- 整句分段（v2.3 常规输入的最后一块拼图） ----------------
    def _segment_candidates(self, q: str, scene: SceneContext
                            ) -> list[Candidate]:
        """整串无单词命中时，用「全拼音节 DP」把整句拆成词条拼接的候选。

        仅当整串能被词条（专业/共享/通用三层）**完整覆盖**且至少两段时才
        返回——绝不做部分匹配的硬凑；失败则交给 _prefix_fallback 或交还系统。

        例：woshigongchengshi -> 我是工程师；fanganwenti -> 方案问题。
        """
        lex = self.lexicons
        if not (len(q) >= 3 and q.isascii() and lex.common_loaded):
            return []
        codes = lex.seg_codes()
        if not codes:
            return []
        n = len(q)
        neg = float("-inf")
        best = [neg] * (n + 1)
        path: list[list[tuple[str, str, int, int]]] = [[] for _ in range(n + 1)]
        best[n] = 0.0
        for i in range(n - 1, -1, -1):
            for j in range(i + 1, min(i + 12, n) + 1):
                rec = codes.get(q[i:j])
                if rec is None or best[j] == neg:
                    continue
                _zh, _en, _fq, raw = rec
                # 归一化对数概率：多分段不再自动加分（每段都要“值得”）
                tot = math.log(raw) - _LOG_TOTAL + best[j]
                if tot > best[i]:
                    best[i] = tot
                    path[i] = [rec] + path[j]
        parts = path[0]
        if best[0] == neg or len(parts) < 2:
            return []
        zh = "".join(p[0] for p in parts)
        if len(zh) > 30:
            return []
        # 整句不拼凑英文（逐词直拼会出现 "I be engineer" 类错句）——
        # 句子的英文交给「选中即译」管线（记忆库/AI），单词语义仍走译法列。
        t = Term(zh=zh, en="", freq=min(p[2] for p in parts), profession="")
        t.pinyin = q
        c = Candidate(term=t, match_type="segment")
        c.detail["base"] = 92.0
        self._score(c, scene)
        return [c]

    # ---------------- 前缀退化（无匹配时的唯一合法兜底） ----------------
    def _prefix_fallback(self, q: str, scene: SceneContext,
                         limit: int) -> list[Candidate]:
        """整串无匹配时，只做「多打了一两个字母」的回退。

        为什么不做深层回退：输入法在**每次按键**都会调进来，用户敲 zhizhang
        的过程中已经路过 zhiz、zhizh… 每一级都见过对应候选了。到这里说明
        「整串不存在」。此时再退到 zhi 把 制造费用 之类的东西端出来，只会
        让人以为系统在乱猜。所以只容忍末尾多打 1~2 个字母（chenkongg → chenkong），
        这是真能救回来的打字失误；再退就返回空，让候选条老实说「无匹配」。
        """
        ql = (q or "").lower()
        if not ql.isascii() or len(ql) < 4:
            return []
        for cut in (len(ql) - 1, len(ql) - 2):
            if cut < 3:
                continue
            pref = ql[:cut]
            hit_keys = [k for k in self.lexicons.term_index if k.startswith(pref)]
            if not hit_keys:
                continue
            seen: set[str] = set()
            cands: list[Candidate] = []
            for k in hit_keys:
                for t in self.lexicons.term_index[k]:
                    key = f"{t.profession}|{t.zh}"
                    if key in seen:
                        continue
                    seen.add(key)
                    c = Candidate(term=t, match_type="prefix_partial")
                    c.detail["base"] = 44.0
                    c.detail["prefix_len"] = float(cut)
                    self._score(c, scene)
                    cands.append(c)
            if not cands:
                continue
            cands.sort(
                key=lambda c: (c.term.profession == scene.profession_key, c.score),
                reverse=True)
            return cands[:limit]
        return []

    # ---------------- 打分模型 ----------------
    def _score(self, c: Candidate, scene: SceneContext) -> None:
        r = self.config.ranking
        t = c.term
        base = c.detail.get("base", 0.0)

        # v2.6：1~2 个字母的英文别名命中（en_short）只是"可能相关"，
        # 不参与任何加权 —— 否则敲 k 会出现「折弯系数 K Factor」压过"块/孔距"。
        if c.match_type == "en_short":
            # 短查询的英文别名命中：不占领域/场景/软件/词频加权，
            # 但用户自己用过/自定义过的词该有的分照样给（QA 指出清零过头）。
            # 历史分要封顶 10：否则"经常用的词"会因为一个字母的别名命中
            # 又爬回第一（K 键 折弯系数 的老毛病），前缀候选反而被压住。
            used = self.usage.count_of(t.zh)
            hist = float(min(used * r.history_bonus_per_use, 10,
                             r.history_bonus_cap))
            custom = float(r.custom_word_bonus if t.custom else 0.0)
            c.score = round(base + hist + custom, 2)
            c.detail.update({"profession": 0.0, "scene": 0.0, "software": 0.0,
                             "freq": 0.0, "history": hist, "custom": custom})
            return

        # 领域匹配
        # v2.6（日常输入法化）：**只有"真命中"才享受全量领域加权**。
        # 以前前缀匹配也拿 +30，导致敲 shi 时「实体/视口」压过「是」、
        # 敲 dianhua 时「点划线/电化铝」压过「电话」——用户感觉"这不是输入法"。
        # 前缀/残缺命中只给 10 分（仍有优先，但不再垄断）。
        weak_match = c.match_type in ("prefix", "prefix_partial",
                                      "common_prefix", "en_short")
        profession_bonus = 0.0
        if scene.profession_key and t.profession == scene.profession_key:
            # 前缀/短查询不加领域分：它们不是"命中"，只是"还没打完"。
            profession_bonus = (0.0 if weak_match
                                else r.profession_match_bonus)
        elif t.profession and t.profession != scene.profession_key:
            # 跨领域：在工程图/英文场景下允许少量跨域召回。
            # v2.7：前缀类（weak）不受跨域加分/扣分影响 —— 它还没"命中"。
            cross = 0.0 if weak_match else (
                6.0 if "drawing_2d" in scene.scene_tags else -12.0)
            c.detail["cross"] = cross
            profession_bonus = cross

        # 场景标签匹配（含三级细分 focus_tags，职位 boost 放大）
        scene_bonus = 0.0
        if not weak_match and scene.scene_tags and t.scene_tags:
            inter = set(scene.scene_tags) & set(t.scene_tags)
            if inter:
                scene_bonus = r.scene_match_bonus * min(len(inter), 2) / 2.0
        # 三级细分加权：命中职位 focus_tags 的词额外提权
        if scene.focus_tags and not weak_match:
            focus_hit = set(scene.focus_tags) & set(t.scene_tags)
            if focus_hit:
                scene_bonus += 18.0 * min(len(focus_hit), 2) * float(scene.position_boost or 1.0)

        # 软件匹配：术语标注了当前软件则额外加权
        sw_bonus = 0.0
        if not weak_match and scene.software_key and scene.software_key in t.software:
            sw_bonus = 10.0

        # 词频
        freq_score = (t.freq / 1000.0) * r.frequency_weight * 100

        # 历史使用
        used = self.usage.count_of(t.zh)
        history_bonus = min(used * r.history_bonus_per_use, r.history_bonus_cap)

        # 自定义词条
        custom_bonus = r.custom_word_bonus if t.custom else 0.0

        c.detail.update({
            "profession": profession_bonus,
            "scene": scene_bonus,
            "software": sw_bonus,
            "freq": freq_score,
            "history": float(history_bonus),
            "custom": custom_bonus,
        })
        c.score = (base + profession_bonus + scene_bonus + sw_bonus
                   + freq_score + history_bonus + custom_bonus)
        c.score = round(c.score, 2)

    # ---------------- 默认候选（空输入时） ----------------
    def _default_candidates(self, scene: SceneContext, limit: int | None) -> list[Candidate]:
        """未输入时展示：当前软件/领域最常用的术语 + 最近使用过的词。"""
        limit = limit or self.config.ranking.max_candidates
        terms = self.lexicons.get_terms(scene.profession_key)

        # 最近使用过的词优先
        recent_zh = [zh for zh, _ in self.usage.recent(limit * 2)]
        by_zh = {t.zh: t for t in terms}

        out: list[Candidate] = []
        seen: set[str] = set()

        for zh in recent_zh:
            t = by_zh.get(zh)
            if t and zh not in seen:
                c = Candidate(term=t, match_type="recent")
                self._score(c, scene)
                c.score += 25.0
                out.append(c)
                seen.add(zh)

        # 按场景标签 + 词频补足
        rest = sorted(
            (t for t in terms if t.zh not in seen),
            key=lambda t: (
                len(set(scene.scene_tags) & set(t.scene_tags)) * 100 + t.freq
            ),
            reverse=True,
        )
        for t in rest:
            if len(out) >= limit:
                break
            c = Candidate(term=t, match_type="scene_default")
            self._score(c, scene)
            out.append(c)

        out.sort(key=lambda c: c.score, reverse=True)
        return out[:limit]

    # ---------------- 中英映射（工程图场景核心） ----------------
    def to_english(self, zh: str, scene: SceneContext | None = None) -> list[Term]:
        """
        中文 -> 英文专业术语。用于"输入中文自动匹配英文标准表述"。
        返回按领域相关性排序的候选（含标准编号与别名）。
        """
        hits = self.lexicons.find_by_zh(zh)
        if not hits:
            # 尝试拼音匹配
            py = pinyin_of(zh)
            hits = list(self.lexicons.term_index.get(py, []))
        if scene and scene.profession_key:
            hits.sort(
                key=lambda t: (t.profession == scene.profession_key, t.freq),
                reverse=True,
            )
        else:
            hits.sort(key=lambda t: t.freq, reverse=True)
        return hits

    def english_of(self, zh: str, scene: SceneContext | None = None) -> str:
        """单次调用取最佳英文表述，带标准编号时的完整表述。"""
        hits = self.to_english(zh, scene)
        if not hits:
            return ""
        best = hits[0]
        if best.standard:
            return f"{best.en} ({best.standard})"
        return best.en

    # ---------------- 自定义词条 ----------------
    def add_custom(self, zh: str, en: str = "", brief: str = "",
                   scene_desc: str = "", profession: str = "") -> Term:
        t = Term(
            zh=zh, en=en, brief=brief, scene=scene_desc,
            profession=profession or self.config.profile.get("active_profession", ""),
            custom=True, freq=500,
        )
        t.pinyin = pinyin_of(zh)
        t.abbr = abbr_of(zh)
        # 绑定当前场景标签，便于后续加权
        t.scene_tags = ["drawing_2d", "3d_modeling"]
        self.lexicons.add_custom(t)
        return t

    def commit(self, zh: str, scene: SceneContext) -> int:
        """用户上屏某词条时调用，累积历史词频。"""
        return self.usage.record(zh, scene.profession_key)


# ---------------- 自测 ----------------
if __name__ == "__main__":
    from scene.detector import SceneDetector

    eng = TermEngine()
    det = SceneDetector()

    print("=" * 68)
    print("场景 A：SolidWorks 机械绘图")
    print("=" * 68)
    sw = det.lock(profession="mechanical", software="solidworks")
    sw.scene_tags = ["drawing_2d", "3d_modeling"]
    print("场景:", sw.describe())
    for q in ["chenkong", "luowenkong", "daojiao", "jizhunmian", "ck", "lwk"]:
        res = eng.search(q, sw, limit=4)
        print(f"\n输入 [{q}] ->")
        for c in res:
            print(f"   {c.zh:8s} | {c.en:26s} | {c.score:6.1f} | {c.match_type:12s} | {c.why()}")

    print()
    print("=" * 68)
    print("中英映射：输入中文 -> 英文标准表述 (工程图场景)")
    print("=" * 68)
    for zh in ["沉孔", "倒角", "形位公差", "表面粗糙度", "螺纹孔"]:
        detailed = eng.to_english(zh, sw)
        if detailed:
            b = detailed[0]
            print(f"  {zh:8s} -> {b.en:24s} | 标准: {b.standard or '-':14s} | 别名: {', '.join(b.var_en) or '-'}")
        else:
            print(f"  {zh:8s} -> (无匹配)")
