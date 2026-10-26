"""
智语输入法 — 统一路径管理（开发态 / 打包态自适应）

开发态：  <项目根>/data/...
打包态：  <exe 同级>/data/...      （首次运行自动从内置资源释放）
         用户可写数据放 %APPDATA%/ZhiyuIME

设计意图：
- 词库/配置属于"只读资源"，打包时打进 exe；首次运行释放到 exe 同级目录，
  便于用户查看、修改、扩展到自己的词库。
- 使用记录/自定义词条属于"用户数据"，放 %APPDATA%，卸载不丢。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "ZhiyuIME"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的环境中。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """内置只读资源根目录（打包后为 _MEIPASS）。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def install_root() -> Path:
    """安装目录：开发态=项目根，打包态=exe 所在目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """可写数据根目录（配置、词库、缓存）。"""
    root = install_root() / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def user_data_root() -> Path:
    """用户个人数据（使用记录、自定义词条），放 %APPDATA%。"""
    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_seed_data() -> None:
    """
    打包态首次运行时，把内置资源释放到安装目录。

    v2.5（SZ04）：**只释放用户会改的东西**。
    `common.tsv.gz`（1.7 MB 通用词库）是只读资源，用户永远不会编辑，
    以前也复制一份 → 装完之后磁盘上是两份（`_internal/data` + `{app}/data`）。
    现在跳过它，读取走 `config.common_dict_path` 的 `resource_root()` 回退。
    已存在则不覆盖，避免覆盖用户的词库修改。
    """
    if not is_frozen():
        return
    src = resource_root() / "data"
    if not src.exists():
        return
    dst = install_root() / "data"
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if rel.parts and rel.parts[0] == "common":
            continue                    # 只读大件：不复制（SZ04）
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(item, target)
            except Exception:
                pass


def bootstrap() -> None:
    """启动早期调用一次。"""
    ensure_seed_data()
