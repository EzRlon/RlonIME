"""
智语 — 联想提供器（UX-P1-2）

上屏成功后给出「下一个可能要打的词」。两路数据源，都不命中则不触发：

1. **关联对直查**：data/associations.json 人工维护的 pairs 表；
2. **同场景共现**：与该词共享 ≥2 个 scene_tags、同行业（profession），
   按词频 freq 降序补足（Q5 裁决：静态 scene_tags 共现，本轮不做
   基于真实上屏序列的动态共现）。

两条红线：
- **白名单校验**：返回词必须 `lexicons.find_by_zh(w)` 非空（词库中真实
  存在），否则丢弃——联想词上屏等价于一次正常候选上屏，数据同源可查；
- **与 engine 零共享**：独立文件、独立索引（自建 zh→Term 字典）、
  独立实例；TermEngine 不 import 本模块，联想词永远不会混进
  engine.search 的候选池。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.models import SceneContext, Term              # noqa: E402
from core.paths import data_root, resource_root         # noqa: E402

MAX_SUGGESTIONS = 4             # 联想槽位上限（SugChip 池大小）
COOCCUR_MIN_SHARED = 2          # 同场景共现：最少共享 scene_tags 数


def default_assoc_path() -> Path:
    """关联对文件：优先可写数据根（用户可改），回退内置只读资源。"""
    local = data_root() / "associations.json"
    if local.exists():
        return local
    return resource_root() / "data" / "associations.json"


class AssociationProvider:
    """联想提供器。只读使用 LexiconManager（白名单 + 共现特征），
    不写、不改、不共享 engine 的任何检索状态。"""

    def __init__(self, lexicons=None, path: Path | None = None):
        # lexicons：core.lexicon.LexiconManager 实例（只读）。
        # 延迟到 load/suggest 再访问，允许先建空实例后接线。
        self._lexicons = lexicons
        self._pairs: dict[str, list[str]] = {}
        # 独立索引：zh -> Term（取每个 zh 词频最高的一条作为共现特征源）
        self._by_zh: dict[str, Term] = {}
        if self._lexicons is not None:
            self._build_index()
        self.load(path if path is not None else default_assoc_path())

    # ---------------- 装载 ----------------
    def attach_lexicons(self, lexicons) -> None:
        """后接线词库管理器（只读），并重建独立索引。"""
        self._lexicons = lexicons
        self._build_index()

    def load(self, path: Path) -> bool:
        """加载关联对 JSON。文件缺失/损坏 → 空表（不报错，联想不触发）。"""
        self._pairs = {}
        try:
            p = Path(path)
            if not p.exists():
                return False
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
            pairs = raw.get("pairs", {}) if isinstance(raw, dict) else {}
            for zh, words in pairs.items():
                if not isinstance(zh, str) or not isinstance(words, list):
                    continue
                cleaned = [w.strip() for w in words
                           if isinstance(w, str) and w.strip() and w.strip() != zh]
                if cleaned:
                    self._pairs[zh] = cleaned
            return True
        except Exception as e:
            print(f"[WARN] 关联对加载失败 {path}: {e}", file=sys.stderr)
            self._pairs = {}
            return False

    def _build_index(self) -> None:
        """自建 zh->Term 索引（与 engine 的检索索引完全独立）。"""
        self._by_zh = {}
        terms = getattr(self._lexicons, "all_terms", None) or []
        for t in sorted(terms, key=lambda x: x.freq):
            # 升序写入，词频最高的留在最后（覆盖低词频同名词）
            if t.zh:
                self._by_zh[t.zh] = t

    # ---------------- 白名单 ----------------
    def _whitelist(self, w: str) -> bool:
        """词库中真实存在才允许作为联想词（不伪造红线）。"""
        if self._lexicons is None:
            return False
        try:
            return bool(self._lexicons.find_by_zh(w))
        except Exception:
            return False

    # ---------------- 查询 ----------------
    def suggest(self, zh: str, scene: SceneContext | None = None) -> list[str]:
        """返回 zh 的联想词（最多 MAX_SUGGESTIONS 个，已过白名单）。

        两路均无结果 → 空列表（调用方不进入联想态）。
        """
        zh = (zh or "").strip()
        if not zh:
            return []
        out: list[str] = []

        # ① 关联对直查（保持人工维护的顺序）
        for w in self._pairs.get(zh, []):
            if w not in out and self._whitelist(w):
                out.append(w)

        # ② 同场景共现补足：共享 ≥2 scene_tags 且同行业，按词频降序
        if len(out) < MAX_SUGGESTIONS:
            out.extend(self._cooccur(zh, scene, exclude=set(out)))

        return out[:MAX_SUGGESTIONS]

    def _cooccur(self, zh: str, scene: SceneContext | None,
                 exclude: set[str]) -> list[str]:
        src = self._by_zh.get(zh)
        if src is None or not src.scene_tags:
            return []
        profession = (getattr(scene, "profession_key", "") or src.profession)
        tags = set(src.scene_tags)
        scored: list[tuple[int, str]] = []
        for w, t in self._by_zh.items():
            if w == zh or w in exclude:
                continue
            if profession and t.profession and t.profession != profession:
                continue
            shared = len(tags & set(t.scene_tags))
            if shared >= COOCCUR_MIN_SHARED:
                scored.append((t.freq, w))
        scored.sort(key=lambda x: -x[0])
        return [w for _, w in scored
                ][:MAX_SUGGESTIONS * 2]  # 多取一些，白名单再筛


if __name__ == "__main__":
    # 手动自检：python src/core/assoc.py 沉孔
    from core.lexicon import LexiconManager

    provider = AssociationProvider(LexiconManager().load_all())
    zh = sys.argv[1] if len(sys.argv) > 1 else "沉孔"
    print(f"{zh} → {provider.suggest(zh)}")
