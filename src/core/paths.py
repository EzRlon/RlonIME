"""
智语输入法 — 统一路径管理（开发态 / 打包态自适应）

开发态：  <项目根>/data/...
打包态：  <exe 同级>/data/...      （首次运行自动从内置资源释放）
         用户可写数据放 %APPDATA%/RlonIME

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

APP_DIR_NAME = "RlonIME"
# v4.0 品牌统一：旧数据目录。只用于「一次性迁移」，任何新写入都走 RlonIME。
LEGACY_APP_DIR = "ZhiyuIME"


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


def _legacy_install_root() -> Path | None:
    """旧版安装目录：优先注册表 InstallLocation，其次默认 per-user 路径。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
            r"\{8F3A6C21-7B4D-4E19-9C2A-5D8E1F0A73B4}_is1")
        try:
            loc = str(winreg.QueryValueEx(key, "InstallLocation")[0] or "")
        finally:
            key.Close()
        if loc:
            p = Path(loc)
            if p.is_dir():
                return p
    except Exception:
        pass
    base = os.environ.get("LOCALAPPDATA") or ""
    if base:
        p = Path(base) / "Programs" / LEGACY_APP_DIR
        if p.is_dir():
            return p
    return None


def migrate_legacy_install() -> int:
    """v4.0：把旧安装目录里的 data/config.json（用户设置）搬到新目录。

    全量改名后安装目录从 Programs/ZhiyuIME 变成 Programs/RlonIME，
    用户的热键、AI Key、候选模式都写在 {app}/data/config.json 里 ——
    不搬等于升级后所有设置归零。必须在 ensure_seed_data 之前调用，
    否则新目录已经释放了一份默认配置，就没法判断"该不该迁"。
    """
    if not is_frozen():
        return 0
    new_cfg = install_root() / "data" / "config.json"
    if new_cfg.exists():
        return 0
    old = _legacy_install_root()
    if old is None:
        return 0
    try:
        if old.resolve() == install_root().resolve():
            return 0
    except Exception:
        pass
    src = old / "data" / "config.json"
    if not src.exists():
        return 0
    try:
        new_cfg.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, new_cfg)
        _upgrade_copied_config(new_cfg)
        print(f"[INFO] 已迁移旧配置：{src} -> {new_cfg}", file=sys.stderr)
        return 1
    except Exception:
        return 0


def _upgrade_copied_config(path: Path) -> None:
    """把旧配置里"已经被新版本改过的语义"补齐。

    只做**单向、幂等**的补齐，不动用户自定义项：
      · ranking.max_candidates < 27 → 27（v3.0.4 起候选池 27 才有 3 页可翻，
        旧配置里写死的 9 会让翻页键继续"按了没反应"）
    """
    import json
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rank = raw.setdefault("ranking", {})
        try:
            cur = int(rank.get("max_candidates", 0) or 0)
        except (TypeError, ValueError):
            cur = 0
        if cur < 27:
            rank["max_candidates"] = 27
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    except Exception:
        pass


def migrate_legacy_data() -> int:
    """v4.0：把老版本 %APPDATA%/ZhiyuIME 的用户数据搬到 RlonIME。

    为什么必须做：老用户升级后如果记忆库、自定义词条"凭空消失"，会直接
    判定软件把他的东西弄丢了 —— 这比任何功能缺失都严重。

    三条原则：
      · **复制不移动**：旧目录原样保留，用户想回退到旧版也能用；
      · **逐文件判断**：新目录已有同名文件就不覆盖；迁移失败的文件下次启动
        会重试（旧版正在运行时 memory.db 可能被占用）；
      · 只搬用户真正在乎的四样：记忆库、使用记录、自定义词、首启标记。
    """
    base = os.environ.get("APPDATA") or str(Path.home())
    old = Path(base) / LEGACY_APP_DIR
    if not old.is_dir():
        return 0
    new = Path(base) / APP_DIR_NAME
    moved = 0
    for name in ("memory.db", "usage.db", "custom_terms.json",
                 "first_run.done"):
        src, dst = old / name, new / name
        if not src.exists() or dst.exists():
            continue
        try:
            new.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            moved += 1
        except Exception:
            pass                    # 被旧进程占用：下次启动再试
    src_dir, dst_dir = old / "msime_export", new / "msime_export"
    if src_dir.is_dir() and not dst_dir.exists():
        try:
            shutil.copytree(src_dir, dst_dir)
        except Exception:
            pass
    if moved:
        print(f"[INFO] 已从旧数据目录迁移 {moved} 项到 {new}", file=sys.stderr)
    return moved


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
    """启动早期调用一次。

    顺序敏感：
      1. 先搬旧安装目录的 config.json（此时新目录还没有配置，能判断该不该迁）
      2. 再释放内置资源（补齐词库/句库等）
      3. 最后搬 %APPDATA% 的用户数据（迁移必须早于任何 user_data_root 使用）
    """
    migrate_legacy_install()
    ensure_seed_data()
    migrate_legacy_data()
