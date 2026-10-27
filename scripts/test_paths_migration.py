"""v4.0 品牌改名（ZhiyuIME → RlonIME）的数据迁移回归

改名最容易出事的地方不是 UI，是**用户数据**：记忆库、自定义词、配置
一旦"消失"，用户会直接判定软件把他的东西弄丢了。

    python scripts/test_paths_migration.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          f"{'' if cond else '  ' + str(extra)}")


def main() -> int:
    import core.paths as paths

    ck("新数据目录名是 RlonIME", paths.APP_DIR_NAME == "RlonIME",
       paths.APP_DIR_NAME)
    ck("旧目录名仍记录为 ZhiyuIME（迁移用）",
       paths.LEGACY_APP_DIR == "ZhiyuIME", paths.LEGACY_APP_DIR)

    real_appdata = os.environ.get("APPDATA")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["APPDATA"] = tmp
            root = Path(tmp)
            old = root / paths.LEGACY_APP_DIR
            old.mkdir()
            (old / "memory.db").write_bytes(b"SQLite format 3\x00fake")
            (old / "usage.db").write_bytes(b"usage")
            (old / "custom_terms.json").write_text('{"a":1}', encoding="utf-8")
            (old / "first_run.done").write_text("", encoding="utf-8")
            (old / "msime_export").mkdir()
            (old / "msime_export" / "x.txt").write_text("x", encoding="utf-8")

            print("[1] 旧目录存在 → 全部搬到新目录")
            n = paths.migrate_legacy_data()
            new = root / "RlonIME"
            ck("迁移计数 = 4 个文件 + 1 个目录", n == 4, n)
            for name in ("memory.db", "usage.db", "custom_terms.json",
                         "first_run.done"):
                ck(f"{name} 已复制", (new / name).exists())
            ck("msime_export 整目录复制",
               (new / "msime_export" / "x.txt").exists())
            ck("旧目录原样保留（可回退旧版）", (old / "memory.db").exists())

            print("[2] 幂等：再跑一次不重复搬、不改内容")
            (new / "memory.db").write_bytes(b"user-edited")
            n2 = paths.migrate_legacy_data()
            ck("第二次迁移 0 项", n2 == 0, n2)
            ck("不覆盖新目录里已有的文件",
               (new / "memory.db").read_bytes() == b"user-edited",
               (new / "memory.db").read_bytes())

            print("[3] 旧目录不存在 → 安全返回 0")
            old2 = root / paths.LEGACY_APP_DIR
            for f in sorted(old2.rglob("*"), reverse=True):
                f.unlink() if f.is_file() else f.rmdir()
            old2.rmdir()
            ck("没有旧目录时不报错、返回 0", paths.migrate_legacy_data() == 0)

            print("[4] bootstrap 顺序：先迁配置，再释放资源，再迁用户数据")
            import inspect
            src = inspect.getsource(paths.bootstrap)
            i1 = src.find("migrate_legacy_install")
            i2 = src.find("ensure_seed_data")
            i3 = src.find("migrate_legacy_data")
            ck("顺序 = install → seed → data", 0 <= i1 < i2 < i3,
               (i1, i2, i3))
    finally:
        if real_appdata is not None:
            os.environ["APPDATA"] = real_appdata

    print()
    print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("  失败项:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
