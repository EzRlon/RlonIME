"""
RlonIME — 图纸技术要求句库（v2.4.0 / S3）

为什么单独一层：技术要求句是"整句""带标点""中英成对"的固定表述，和术语
（两三个字）在检索语义上不是一类东西。塞进 term_index 会污染单字母/前缀
候选（K 键那种场景更糟），所以它只认**短码**：`yq` 列出全部、`yq07` 精确取一条。

上屏方式完全复用现有三条通道，不新增按键：
  · 数字键    = 中文
  · Tab       = 英文
  · Ctrl+数字 = 中英对照（中文 + 英文）

数据文件：data/phrases/tech_requirements.json（用户可自行增改，重启生效）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import Candidate, Term

_PHRASE_FREQ = 600          # 句库词频：高于普通术语，让整句稳定排在码命中的首位
_BASE_EXACT = 104.0         # 短码精确（yq07）
_BASE_PREFIX = 92.0         # 短码前缀（yq）


class PhraseStore:
    """短码 -> 技术要求句。懒加载，文件缺失/损坏时静默降级为空。"""

    def __init__(self, path: Path | None = None):
        self.path = path or self._default_path()
        self.items: list[dict] = []
        self.name = "技术要求句库"
        self.version = "0.0.0"
        self._loaded = False

    @staticmethod
    def _default_path() -> Path:
        try:
            from core.config import get_config
            return get_config().phrases_path
        except Exception:
            return Path(__file__).resolve().parents[2] / "data" / "phrases" \
                / "tech_requirements.json"

    # ---------- 加载 ----------
    def load(self) -> int:
        if self._loaded:
            return len(self.items)
        self._loaded = True
        if not self.path.exists():
            return 0
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] 句库读取失败: {e}", file=sys.stderr)
            return 0
        self.name = raw.get("name", self.name)
        self.version = raw.get("version", self.version)
        items: list[dict] = []
        for it in raw.get("items", []):
            zh = str(it.get("zh", "")).strip()
            code = str(it.get("code", "")).strip().lower()
            if not zh or not code:
                continue
            items.append({
                "code": code,
                "zh": zh,
                "en": str(it.get("en", "")).strip(),
                "tag": str(it.get("tag", "")).strip(),
            })
        self.items = items
        return len(items)

    def __len__(self) -> int:
        self.load()
        return len(self.items)

    # ---------- 检索 ----------
    def search(self, code: str, limit: int = 9) -> list[Candidate]:
        """按短码检索。code 必须是小写字母数字（拼音输入自然满足）。"""
        self.load()
        q = (code or "").strip().lower()
        if not q or not q.isascii() or not q.isalnum() or len(q) < 2:
            return []
        out: list[Candidate] = []
        for prefix_pass, base, mtype in ((True, _BASE_EXACT, "phrase"),
                                         (False, _BASE_PREFIX, "phrase_prefix")):
            for it in self.items:
                hit = (it["code"] == q) if prefix_pass else it["code"].startswith(q)
                if not hit:
                    continue
                # 精确码只该出一条；前缀列表跳过完全相同的码（已在上一步给出）
                if not prefix_pass and it["code"] == q:
                    continue
                t = Term(zh=it["zh"], en=it["en"], freq=_PHRASE_FREQ,
                         profession="", scene_tags=["drawing_2d"])
                t.pinyin = it["code"]
                t.abbr = it["code"]
                t.brief = it["tag"]
                c = Candidate(term=t, match_type=mtype)
                c.detail["base"] = base
                c.detail["phrase_code"] = it["code"]     # type: ignore[assignment]
                out.append(c)
                if len(out) >= limit:
                    return out
        return out

    def stats(self) -> dict:
        self.load()
        return {"name": self.name, "version": self.version,
                "count": len(self.items)}


if __name__ == "__main__":
    st = PhraseStore()
    print(st.stats())
    for q in ("yq", "yq07", "yq50", "yq99"):
        hits = st.search(q, limit=4)
        print(f"{q:6s} -> " + " | ".join(
            f"{c.detail.get('phrase_code')} {c.zh}" for c in hits))
