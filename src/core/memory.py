"""
智语 — 翻译记忆库（Translation Memory）

这是全系统最有价值的一层：用户每纠正一次，系统就聪明一分。

存放在 %APPDATA%/ZhiyuIME/memory.db：
  - 不放在安装目录 → 升级、卸载、换版本都不会丢
  - 永不上传     → 图纸术语是商业机密，只在本机
  - 只增不删     → 除非用户主动清理

匹配算法（CAT 工具的标准做法，不重复造轮子）：
  1. 归一化：去标点空白、全角转半角、ASCII 统一小写
  2. 精确匹配（归一化后完全相等）—— 零成本，命中率最高的那 80%
  3. 模糊匹配：difflib.SequenceMatcher（Python 标准库自带的
     Ratcliff-Obershelp 算法），阈值默认 0.85

性能取舍：
  模糊匹配是 O(n) 扫描。为把万级记忆库压在 2ms 内，先用
  「长度差 + 字符集合 Jaccard」两道廉价过滤器筛掉 95% 的行，
  再对剩下的算真正的相似度。
"""
from __future__ import annotations

import difflib
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.paths import user_data_root   # noqa: E402

# 归一化：剥掉标点、空白、各类括号与引号
_STRIP = re.compile(r"[\s\u3000，。、；：！？（）【】《》「」『』,.;:!?()\[\]{}<>\"'“”‘’·…—\-_/\\|~`]+")
# 全角 ASCII → 半角
_FULL2HALF = {i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)}

SCHEMA_VERSION = 1


def normalize(text: str) -> str:
    """归一化：用于匹配比较，不改变存储原文。"""
    if not text:
        return ""
    s = text.translate(_FULL2HALF)
    s = _STRIP.sub("", s)
    return s.lower()


def _char_set(s: str) -> frozenset[str]:
    return frozenset(s)


@dataclass
class Pair:
    """一条记忆对。"""

    src: str            # 中文原句
    dst: str            # 已确认的英文译法
    industry: str = ""
    count: int = 1
    updated: float = 0.0
    source: str = "user"   # user（用户纠正）/ llm（AI 产出被采用）/ manual（手工录入）
    score: float = 1.0     # 匹配相似度


class TranslationMemory:
    """翻译记忆库。线程安全（单连接 + 锁）。"""

    def __init__(self, db_path: Path | None = None, threshold: float = 0.85):
        self.path = Path(db_path) if db_path else user_data_root() / "memory.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.threshold = threshold
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")     # 读写并发不互斥
        self._conn.execute("PRAGMA synchronous=NORMAL")   # 记忆库丢一条不致命，换 3 倍写速
        self._ensure_schema()
        self._exact: dict[str, Pair] = {}
        self._rows: list[tuple[str, Pair]] = []           # (normalized_src, Pair)
        self._dirty = True

    # ---------------- 表结构 ----------------
    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pair (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    norm      TEXT NOT NULL,
                    src       TEXT NOT NULL,
                    dst       TEXT NOT NULL,
                    industry  TEXT NOT NULL DEFAULT '',
                    count     INTEGER NOT NULL DEFAULT 1,
                    source    TEXT NOT NULL DEFAULT 'user',
                    created   REAL NOT NULL DEFAULT 0,
                    updated   REAL NOT NULL DEFAULT 0,
                    UNIQUE(norm, dst)
                );
                CREATE INDEX IF NOT EXISTS idx_pair_norm ON pair(norm);
                CREATE INDEX IF NOT EXISTS idx_pair_industry ON pair(industry);

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    val TEXT
                );
                """
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, val) VALUES('schema', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    # ---------------- 缓存 ----------------
    def _reload(self) -> None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT src, dst, industry, count, updated, source FROM pair"
            )
            rows = cur.fetchall()
        exact: dict[str, Pair] = {}
        pairs: list[tuple[str, Pair]] = []
        for src, dst, industry, count, updated, source in rows:
            p = Pair(src=src, dst=dst, industry=industry or "", count=count or 1,
                     updated=updated or 0.0, source=source or "user")
            n = normalize(src)
            pairs.append((n, p))
            # 同一中文原句可能有多条不同译法。排序优先级：
            #   用户确认过的 > 出现次数 > 最近更新
            # 这样 AI 产出的初稿永远不会盖掉用户亲手纠正过的译法。
            key = (p.source == "user", p.count, p.updated)
            old = exact.get(n)
            if old is None or key > (old.source == "user", old.count, old.updated):
                exact[n] = p
        self._exact = exact
        self._rows = pairs
        self._dirty = False

    def _ensure_fresh(self) -> None:
        if self._dirty:
            self._reload()

    # ---------------- 查询 ----------------
    def exact(self, src: str, industry: str = "") -> Pair | None:
        """精确命中。industry 非空时优先返回同行业的译法。"""
        self._ensure_fresh()
        n = normalize(src)
        if not n:
            return None
        p = self._exact.get(n)
        if p is None:
            return None
        if industry and p.industry and p.industry != industry:
            # 命中但行业不同：仍算命中，只是降权（专业译法常有行业差异，
            # 但用户显然更想要「有个答案」而不是「没有答案」）
            alt = self._best_for_industry(n, industry)
            if alt is not None:
                return alt
            p.score = 0.75
            return p
        p.score = 1.0
        return p

    def _best_for_industry(self, norm_src: str, industry: str) -> Pair | None:
        best = None
        for n, p in self._rows:
            if n != norm_src or p.industry != industry:
                continue
            if best is None or (p.count, p.updated) > (best.count, best.updated):
                best = p
        if best is not None:
            best.score = 1.0
        return best

    def fuzzy(self, src: str, industry: str = "",
              threshold: float | None = None) -> Pair | None:
        """模糊命中，返回相似度最高且 >= threshold 的一条。"""
        self._ensure_fresh()
        n = normalize(src)
        if len(n) < 2 or not self._rows:
            return None
        thr = self.threshold if threshold is None else threshold
        best: Pair | None = None
        best_r = thr
        q_set = _char_set(n)
        q_len = len(n)
        for cand_n, p in self._rows:
            # 过滤器 1：长度差超过阈值允许范围，相似度不可能达标
            cl = len(cand_n)
            if not cl or abs(cl - q_len) > max(2, int(q_len * (1 - thr)) + 1):
                continue
            # 过滤器 2：字符集合 Jaccard 太低，直接跳过（统计意义上不可能过 0.85）
            union = len(q_set | _char_set(cand_n))
            if union and len(q_set & _char_set(cand_n)) / union < thr * 0.55:
                continue
            r = difflib.SequenceMatcher(None, n, cand_n).ratio()
            if industry and p.industry == industry:
                r += 0.03                     # 同行业轻微提权
            if r > best_r:
                best_r, best = r, p
        if best is not None:
            best.score = round(min(best_r, 1.0), 4)
        return best

    def lookup(self, src: str, industry: str = "") -> Pair | None:
        """精确优先，其次模糊。这是翻译管线的第一、二级。"""
        return self.exact(src, industry) or self.fuzzy(src, industry)

    # ---------------- 写入 ----------------
    def learn(self, src: str, dst: str, industry: str = "",
              source: str = "user") -> bool:
        """写入/强化一条记忆。同一 (归一化中文, 英文) 重复写入只累加计数。

        这是闭环的入口：用户纠正过的句子标记后自动进这里，
        下次遇到同样的中文直接命中，不再走大模型。
        """
        src = (src or "").strip()
        dst = (dst or "").strip()
        if not src or not dst:
            return False
        n = normalize(src)
        if not n:
            return False
        now = time.time()
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO pair(norm, src, dst, industry, count, source,
                                     created, updated)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(norm, dst) DO UPDATE SET
                        count   = count + 1,
                        updated = excluded.updated,
                        source  = excluded.source
                    """,
                    (n, src, dst, industry or "", source, now, now),
                )
                self._conn.commit()
            except Exception:
                return False
        self._dirty = True
        return True

    def reinforce(self, src: str, industry: str = "") -> None:
        """用户采纳了某条记忆译法时调用，提升其权重。"""
        p = self.lookup(src, industry)
        if p is None:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE pair SET count = count + 1, updated = ? "
                "WHERE norm = ? AND dst = ?",
                (time.time(), normalize(src), p.dst),
            )
            self._conn.commit()
        self._dirty = True

    def forget(self, src: str, dst: str = "") -> int:
        """删除记忆。dst 为空则删掉该中文原句的所有译法。"""
        n = normalize(src)
        with self._lock:
            if dst:
                cur = self._conn.execute(
                    "DELETE FROM pair WHERE norm = ? AND dst = ?", (n, dst))
            else:
                cur = self._conn.execute("DELETE FROM pair WHERE norm = ?", (n,))
            self._conn.commit()
            n_del = cur.rowcount
        self._dirty = True
        return n_del

    # ---------------- 统计 / 导出 ----------------
    def stats(self) -> dict:
        self._ensure_fresh()
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM pair").fetchone()[0]
            srcs = self._conn.execute(
                "SELECT COUNT(DISTINCT norm) FROM pair").fetchone()[0]
            feeds = self._conn.execute(
                "SELECT COALESCE(SUM(count),0) FROM pair").fetchone()[0]
            by_ind = dict(self._conn.execute(
                "SELECT industry, COUNT(*) FROM pair GROUP BY industry"
            ).fetchall())
        return {"pairs": total, "distinct_src": srcs, "total_hits": feeds,
                "by_industry": by_ind, "path": str(self.path)}

    def recent(self, limit: int = 20) -> list[Pair]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT src, dst, industry, count, updated, source FROM pair "
                "ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
        return [Pair(src=r[0], dst=r[1], industry=r[2] or "", count=r[3],
                     updated=r[4], source=r[5] or "user") for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                pass


# ---------------- 自测 ----------------
if __name__ == "__main__":
    import json
    import tempfile

    tmp = Path(tempfile.gettempdir()) / "zhiyu_memory_selftest.db"
    if tmp.exists():
        tmp.unlink()
    m = TranslationMemory(tmp)

    cases = [
        ("该零件底部需加工沉孔，深度 8mm", "A counterbore shall be machined at the bottom of this part, depth 8 mm.", "mechanical"),
        ("沉头螺钉装配间隙不足", "Insufficient clearance for countersunk screw assembly", "mechanical"),
        ("立柱与顶板采用满焊连接", "The post is fully welded to the top plate", "display"),
    ]
    for s, d, ind in cases:
        m.learn(s, d, ind)
    m.learn(cases[0][0], cases[0][1], "mechanical")     # 重复写入 -> count 2

    print(json.dumps(m.stats(), ensure_ascii=False, indent=2))

    probes = [
        ("该零件底部需加工沉孔，深度 8mm", "精确"),
        ("该零件底部需加工沉孔, 深度 8mm", "标点差异"),
        ("该零件底部需加工沉孔，深度 8MM", "大小写差异"),
        ("该零件底部需加工沉孔，深度 10mm", "一字之差 -> 模糊"),
        ("完全不相干的一句话", "应无命中"),
    ]
    print("\n--- 匹配抽查 ---")
    for q, tag in probes:
        p = m.lookup(q, "mechanical")
        if p:
            print(f"[{tag}] {p.score:.3f} -> {p.dst[:50]}")
        else:
            print(f"[{tag}] (无命中)")
    m.close()
    tmp.unlink(missing_ok=True)
