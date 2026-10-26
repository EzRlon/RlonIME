"""
智语输入法 — 配置管理
负责加载 data/config.json，提供类型安全的配置访问。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.paths import (bootstrap, data_root, install_root, resource_root,
                        user_data_root)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 发布身份（v2.2 D1）：显示名与版本以代码为准，不读用户持久化的
# config.json —— 否则老用户升级后托盘/关于页仍显示旧名与旧版本号。
# 更新发布版本时改这里 + packaging/installer.iss + data/config.json 三处。
APP_NAME = "RlonIME"
APP_VERSION = "3.0.0"

# 热键内置默认值（全项目唯一允许出现热键字符串硬编码的地方，
# 其余模块一律经 Config.hotkey() 取值）。
# v2.1 起默认全部左手位（右手不离开鼠标）：译 Q / 标 W / 面板 E / 切行业 A / AI S。
# 老用户已持久化的自定义不受影响——hotkeys 段配置值优先于此默认值。
DEFAULT_HOTKEYS: dict[str, str] = {
    "translate_selection": "ctrl+alt+q",
    "mark_correction": "ctrl+alt+w",
    "toggle_panel": "ctrl+alt+e",
    "cycle_scope": "ctrl+alt+a",
    "toggle_llm": "ctrl+alt+s",
    "ocr_capture": "ctrl+alt+o",
}

# 旧配置键 -> hotkeys 段键名（一次性兼容迁移，老配置升级不丢自定义）
_LEGACY_HOTKEY_KEYS: dict[str, tuple[str, str]] = {
    "translate_selection": ("assist", "translate_hotkey"),
    "mark_correction": ("assist", "mark_hotkey"),
}

# softwares 段旧拼写 -> 正确拼写（P2-3，老配置兼容）
_LEGACY_SOFTWARE_KEYS: dict[str, str] = {
    "epaln": "eplan",
}


@dataclass
class RankingConfig:
    scene_match_bonus: int = 30
    profession_match_bonus: int = 20
    history_bonus_per_use: int = 5
    history_bonus_cap: int = 40
    frequency_weight: float = 0.35
    custom_word_bonus: int = 50
    max_candidates: int = 9
    rec_badge_margin: float = 8.0    # 「推荐」标：第一名领先第二名的最小分差


@dataclass
class UIConfig:
    layout: str = "bar"            # bar（横向候选条，默认）/ panel（旧版大面板）
    bar_width: int = 560
    panel_width: int = 560
    row_height: int = 38
    max_visible_rows: int = 5
    font_size: int = 15
    font_size_zh: int = 11         # 候选条中文字号（v2.2 新装默认 11）
    font_size_en: int = 9          # 候选条英文/译法列字号（v2.2 新装默认 9）
    font_family: str = "Microsoft YaHei UI"
    opacity: float = 0.99
    theme: str = "light"
    show_english: bool = True
    show_scene_hint: bool = True
    bar_pinned: bool = False       # 候选条拖动后固定（=不再跟随光标）
    bar_pos: list = field(default_factory=list)   # 固定位置 [x, y]（全局一份）
    remember_bar_pos: bool = True  # 拖动结束是否持久化位置


@dataclass
class AssistConfig:
    """词汇助手（选中即译 + 记忆闭环）配置。"""

    auto_replace: bool = True
    store_llm_draft: bool = True
    local_only: bool = False
    min_chars: int = 2
    fuzzy_threshold: float = 0.85
    show_result_ms: int = 4200
    # v2.6：没有 API Key 时，选中即译走免费在线翻译（MyMemory → Google）。
    # 关掉 = 纯本地 + 大模型二选一（保密场景）。local_only=True 时强制关闭。
    free_translate: bool = True
    # v2.8：截图取词（Windows 内置 OCR，离线）。没有语言包时自动降级提示。
    ocr: bool = True


@dataclass
class SoftwareDef:
    key: str
    name: str
    processes: list[str] = field(default_factory=list)
    profession: str | None = None
    scenes: list[str] = field(default_factory=list)


@dataclass
class ProfessionDef:
    key: str
    name: str
    icon: str
    lexicon: str
    default_software: str


class Config:
    """配置中心。线程安全只读访问。"""

    def __init__(self, config_path: str | os.PathLike[str] | None = None):
        bootstrap()   # 打包态：首次运行释放内置资源
        self._default_path = data_root() / "config.json"
        if config_path:
            self.path = Path(config_path)
        elif self._default_path.exists():
            self.path = self._default_path
        else:
            self.path = PROJECT_ROOT / "data" / "config.json"
        with open(self.path, "r", encoding="utf-8") as f:
            self._raw: dict[str, Any] = json.load(f)

        self.version: str = APP_VERSION
        self.app_name: str = APP_NAME
        self.ranking = RankingConfig(**self._raw.get("ranking", {}))
        self.ui = UIConfig(**{k: v for k, v in self._raw.get("ui", {}).items()
                              if k in UIConfig.__dataclass_fields__})
        self.assist = AssistConfig(
            **{k: v for k, v in self._raw.get("assist", {}).items()
               if k in AssistConfig.__dataclass_fields__})
        self.hotkeys: dict[str, str] = self._raw.get("hotkeys", {})
        self.insertion: dict[str, Any] = self._raw.get("insertion", {})
        self.ime: dict[str, Any] = self._raw.get("ime", {})
        self.profile: dict[str, Any] = self._raw.get("profile", {})
        self.llm: dict[str, Any] = self._raw.get("llm", {}) or {}
        self._raw_paths: dict[str, str] = self._raw.get("paths", {})

        self.professions: dict[str, ProfessionDef] = {
            k: ProfessionDef(key=k, **v)
            for k, v in self._raw.get("professions", {}).items()
        }
        self.softwares: dict[str, SoftwareDef] = {
            k: SoftwareDef(key=k, **v)
            for k, v in self._normalize_software_keys(
                self._raw.get("softwares", {})).items()
        }

        # 进程名 -> 软件 key 的反向索引（小写）
        # 注意：同一进程名可能对应多个软件（如 acad.exe 同时属于 AutoCAD 与
        # AutoCAD Electrical）。此处采用"首个登记优先 + 显式优先级"策略：
        # 通用软件（autocad）优先，专用变体（autocad_electrical）由领域切换或
        # 手动指定命中，避免通用 CAD 被误判为电气专用版本。
        self._process_index: dict[str, str] = {}
        priority = ["autocad", "cadence"]  # 这些软件优先占用进程名
        ordered = sorted(
            self.softwares.items(),
            key=lambda kv: (kv[0] not in priority, list(self.softwares).index(kv[0])),
        )
        for sk, sdef in ordered:
            for proc in sdef.processes:
                self._process_index.setdefault(proc.lower(), sk)

    # ---------- 热键（唯一来源） ----------
    def hotkey(self, name: str, default: str = "") -> str:
        """热键串（"ctrl+alt+t" 格式）。

        优先读 hotkeys 段；老配置里的 assist.translate_hotkey /
        assist.mark_hotkey 命中时一次性迁移写回 hotkeys 段并删除旧键。
        都没有时返回 default（内置默认值见 DEFAULT_HOTKEYS）。
        """
        val = self.hotkeys.get(name)
        if isinstance(val, str) and val.strip():
            return val
        legacy = _LEGACY_HOTKEY_KEYS.get(name)
        if legacy:
            section, key = legacy
            old = (self._raw.get(section) or {}).get(key)
            if isinstance(old, str) and old.strip():
                self.hotkeys[name] = old
                self._migrate_legacy_hotkeys(name, old, section, key)
                return old
        return default

    def _migrate_legacy_hotkeys(self, name: str, value: str,
                                section: str, key: str) -> None:
        """把旧热键键值写回 hotkeys 段并删除旧键（持久化）。"""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            raw.setdefault("hotkeys", {})[name] = value
            if isinstance(raw.get(section), dict):
                raw[section].pop(key, None)
            self.path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            self._raw = raw
            self.hotkeys = raw.get("hotkeys", {})
        except Exception as e:
            print(f"[WARN] 旧热键配置迁移失败: {e}", file=sys.stderr)

    # ---------- softwares 旧键兼容 ----------
    @staticmethod
    def _normalize_software_keys(raw: dict[str, Any]) -> dict[str, Any]:
        """旧拼写键映射（epaln -> eplan）；正确键已存在时不覆盖。"""
        out = dict(raw)
        for old, new in _LEGACY_SOFTWARE_KEYS.items():
            if old in out and new not in out:
                out[new] = out.pop(old)
        return out

    # ---------- 热键冲突校验（设置中心与单测共用） ----------
    @staticmethod
    def validate_hotkeys(hotkeys: dict[str, str],
                         toggle_hotkey: str = "ctrl+space") -> list[str]:
        """校验热键表，返回错误文案列表（空列表 = 通过）。

        三条规则（UX-P0-5）：
        ① 每个热键串都必须能被 core.ime.parse_hotkey 解析；
        ② 任何热键不得与接管开关键（默认 ctrl+space）相同；
        ③ 五个热键互不相同。
        """
        from core.ime import parse_hotkey     # 延迟导入，避免环

        def norm(spec: str) -> str:
            return "+".join(p.strip().lower()
                            for p in str(spec).split("+") if p.strip())

        errors: list[str] = []
        seen: dict[str, str] = {}
        toggle = norm(toggle_hotkey)
        for name, spec in (hotkeys or {}).items():
            n = norm(spec)
            if parse_hotkey(n) is None:
                errors.append(
                    f"{name}: 无法识别的热键 {spec!r}"
                    f"（仅支持 ctrl/alt/shift/win + 单个字母或数字）")
                continue
            if n == toggle:
                errors.append(f"{name}: 与接管开关键 {toggle_hotkey} 冲突")
                continue
            if n in seen:
                errors.append(f"{name}: 与 {seen[n]} 重复（{n}）")
            else:
                seen[n] = name
        return errors

    # ---------- 路径 ----------
    @property
    def lexicon_dir(self) -> Path:
        """词库目录：优先用安装目录下的（用户可自行增改），否则回退内置资源。"""
        local = data_root() / "lexicons"
        if local.exists():
            return local
        return PROJECT_ROOT / self._raw_paths.get("lexicon_dir", "data/lexicons")

    @property
    def common_dict_path(self) -> Path:
        """通用词库（v2.3 常规输入）。

        v2.5（SZ04）：通用词库不再复制到安装目录（1.7 MB 只读大件）。
        打包态的 `_MEIPASS` 在不同 PyInstaller 版本里可能是「exe 同级」或
        「exe/_internal」，只认一种会在另一端静默失联（实测踩过：装完打
        jintian 出的是分段结果"进提昂"）。这里按优先级逐个探测，谁存在用谁。
        """
        candidates = [
            data_root() / "common" / "common.tsv.gz",            # 用户自换（优先）
            resource_root() / "data" / "common" / "common.tsv.gz",
            install_root() / "_internal" / "data" / "common" / "common.tsv.gz",
            PROJECT_ROOT / "data" / "common" / "common.tsv.gz",
        ]
        for p in candidates:
            if p.exists():
                return p
        return candidates[0]

    @property
    def phrases_path(self) -> Path:
        """技术要求句库（v2.4.0 / S3）：安装目录优先，用户可自行增改。"""
        local = data_root() / "phrases" / "tech_requirements.json"
        if local.exists():
            return local
        return PROJECT_ROOT / "data" / "phrases" / "tech_requirements.json"

    @property
    def user_data_dir(self) -> Path:
        """用户个人数据目录（使用记录/自定义词条），放 %APPDATA% 免卸载丢失。"""
        return user_data_root()

    @property
    def log_dir(self) -> Path:
        p = user_data_root() / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ---------- 查询 ----------
    def software_by_process(self, process_name: str) -> SoftwareDef | None:
        """根据进程名（如 SLDWORKS.exe）定位软件定义。"""
        if not process_name:
            return None
        key = self._process_index.get(process_name.lower())
        return self.softwares.get(key) if key else None

    def profession_lexicon_path(self, profession_key: str) -> Path:
        prof = self.professions.get(profession_key)
        if not prof:
            raise KeyError(f"未知职业领域: {profession_key}")
        return self.lexicon_dir / prof.lexicon

    def scene_scoped_profession(self, software: SoftwareDef) -> str | None:
        """AutoCAD 同时被机械/电气复用，优先取当前激活领域。"""
        if software.key == "autocad_electrical":
            return "electrical"
        if software.key == "autocad":
            cur = self.profile.get("active_profession")
            return cur if cur in ("mechanical", "architecture") else "mechanical"
        return software.profession

    def all_lexicon_paths(self) -> list[tuple[str, Path]]:
        return [
            (k, self.lexicon_dir / p.lexicon)
            for k, p in self.professions.items()
        ]


_config_singleton: Config | None = None


def get_config(reload: bool = False) -> Config:
    global _config_singleton
    if _config_singleton is None or reload:
        _config_singleton = Config()
    return _config_singleton


if __name__ == "__main__":
    c = get_config()
    print(f"应用: {c.app_name} v{c.version}")
    print(f"职业领域: {[p.name for p in c.professions.values()]}")
    print(f"软件数量: {len(c.softwares)}")
    print(f"进程索引: {c.software_by_process('SLDWORKS.exe')}")
    print(f"词库路径: {c.profession_lexicon_path('mechanical')}")
