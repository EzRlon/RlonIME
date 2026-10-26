"""智语输入法 — 检索性能基准哨兵（设计 §1.8）

用途：engine.search 当前是逐键全量前缀扫描，~1600 条无压力。
触发换索引（首 1-2 个拼音字母分桶）的阈值：
    总词条 > 8000，或本脚本实测单键检索 > 5ms。
本脚本只做观察哨，不改任何行为：50 键随机拼音计时，输出统计。
"""
from __future__ import annotations

import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.engine import TermEngine      # noqa: E402
from core.models import SceneContext    # noqa: E402

SAMPLES = 50
WARN_MS = 5.0           # 超过即触发换索引评估（设计 §1.8）
SEED = 20261008         # 固定种子，多次运行可比


def main() -> int:
    engine = TermEngine()
    stats = engine.lexicons.stats()
    total = stats["total_terms"]
    scene = SceneContext(profession_key="mechanical")

    # 50 键随机拼音：从词库索引真实 key 里抽样（含长短混合）
    keys = list(engine.lexicons.term_index.keys())
    rng = random.Random(SEED)
    samples = [rng.choice(keys)[: rng.randint(1, 4)] for _ in range(SAMPLES)]

    lat: list[float] = []
    for q in samples:
        t0 = time.perf_counter()
        engine.search(q, scene)
        lat.append((time.perf_counter() - t0) * 1000.0)

    avg = statistics.mean(lat)
    p95 = sorted(lat)[int(len(lat) * 0.95) - 1]
    worst = max(lat)
    print(f"词条总数: {total}")
    print(f"样本: {SAMPLES} 键随机拼音（种子 {SEED}）")
    print(f"单键检索: avg={avg:.2f}ms  p95={p95:.2f}ms  max={worst:.2f}ms")
    print(f"换索引阈值: 词条>8000 或单键>{WARN_MS:.0f}ms")
    trigger = total > 8000 or p95 > WARN_MS
    print("结论:", "⚠ 已达阈值，建议换分桶索引（§1.8）" if trigger
          else "OK 未达阈值，维持全量前缀扫描")
    return 0


if __name__ == "__main__":
    sys.exit(main())
