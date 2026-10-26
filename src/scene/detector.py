"""
智语输入法 — 场景探测引擎

不重复造轮子：
- 前台窗口信息：pygetwindow（封装 Win32 GetForegroundWindow）
- 进程信息：psutil（成熟跨平台进程库）
- 键盘状态：ctypes 调用标准 user32（无第三方依赖可替代）

职责：识别当前用户正在使用的工程软件 + 推断文档场景。
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import get_config
from core.models import SceneContext, DRAWING_TITLE_HINTS, DOC_EXTENSION_SCENES

# --- 标准库/成熟库引入 ---
try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore

try:
    import pygetwindow as gw
except ImportError:  # pragma: no cover
    gw = None  # type: ignore


# ---------- Win32 前台窗口（ctypes，标准库方式） ----------
user32 = ctypes.windll.user32 if sys.platform == "win32" else None  # type: ignore[attr-defined]


def get_foreground_hwnd() -> int:
    if user32 is None:
        return 0
    return int(user32.GetForegroundWindow())


def get_window_title(hwnd: int) -> str:
    if user32 is None or not hwnd:
        return ""
    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_window_pid(hwnd: int) -> int:
    if user32 is None or not hwnd:
        return 0
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def get_process_name(pid: int) -> str:
    if not pid:
        return ""
    if psutil is not None:
        try:
            return psutil.Process(pid).name()
        except Exception:
            return ""
    return ""


def get_process_path(pid: int) -> str:
    if not pid or psutil is None:
        return ""
    try:
        return psutil.Process(pid).exe() or ""
    except Exception:
        return ""


def get_cursor_pos() -> tuple[int, int]:
    """获取鼠标位置，用于候选窗跟随定位。"""
    if user32 is None:
        return (0, 0)

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return (int(pt.x), int(pt.y))


class SceneDetector:
    """场景探测器：前台窗口 -> 软件 -> 领域。带缓存避免频繁查询。"""

    def __init__(self, cache_ttl: float = 0.6):
        self.config = get_config()
        self.cache_ttl = cache_ttl
        self._cache: SceneContext | None = None
        self._cache_time: float = 0.0
        # 手动锁定：用户手动指定领域/软件后，忽略自动探测
        self._locked: SceneContext | None = None

    # ---------- 手动锁定 ----------
    def lock(self, profession: str | None = None, software: str | None = None) -> SceneContext:
        cur = self.detect(force=True)
        if profession:
            cur.profession_key = profession
        if software and software in self.config.softwares:
            sw = self.config.softwares[software]
            cur.software_key = sw.key
            cur.software_name = sw.name
            cur.scene_tags = list(sw.scenes)
            if not profession:
                cur.profession_key = self.config.scene_scoped_profession(sw) or cur.profession_key
        cur.detected_by = "manual"
        cur.confidence = 1.0
        self._locked = cur
        return cur

    def unlock(self) -> None:
        self._locked = None
        self._cache = None

    @property
    def is_locked(self) -> bool:
        return self._locked is not None

    # ---------- 自动探测 ----------
    def detect(self, force: bool = False) -> SceneContext:
        if self._locked is not None:
            return self._locked
        now = time.monotonic()
        if not force and self._cache and (now - self._cache_time) < self.cache_ttl:
            return self._cache

        ctx = self._probe()
        self._cache = ctx
        self._cache_time = now
        return ctx

    def _probe(self) -> SceneContext:
        ctx = SceneContext()
        try:
            hwnd = get_foreground_hwnd()
            ctx.window_title = get_window_title(hwnd)
            pid = get_window_pid(hwnd)
            ctx.process_name = get_process_name(pid)
        except Exception:
            return self._default_context()

        if not ctx.process_name:
            return self._default_context()

        sw = self.config.software_by_process(ctx.process_name)
        if sw is None:
            # 未登记软件：若标题含工程图关键词，仍按当前领域给术语
            ctx.detected_by = "default"
            ctx.confidence = 0.2
            ctx.profession_key = self.config.profile.get("active_profession", "")
            ctx.scene_tags = self._infer_scene_tags(ctx.window_title)
            return ctx

        ctx.software_key = sw.key
        ctx.software_name = sw.name
        ctx.detected_by = "process"
        ctx.confidence = 0.9

        # 领域判定（AutoCAD 可被机械/电气/建筑复用）
        prof = self.config.scene_scoped_profession(sw)
        ctx.profession_key = prof or self.config.profile.get("active_profession", "")

        # 场景标签：文档后缀/标题推断的结果优先，软件默认场景作为补充
        tags: list[str] = self._infer_scene_tags(ctx.window_title)
        for t in sw.scenes:
            if t not in tags:
                tags.append(t)

        if tags and tags[0] == "drawing_2d":
            # 标题明确指向工程图时，置信度拉高（这是"图纸标注"核心场景）
            ctx.confidence = 0.95
        ctx.scene_tags = tags
        return ctx

    def _infer_scene_tags(self, title: str) -> list[str]:
        """从窗口标题推断场景。

        策略：文档后缀是最可靠信号 —— 一旦命中，直接返回，不再叠加关键词，
        避免 ".dwg" 这类通用工程图后缀又被 "layout/plan" 等词污染。
        仅当没有可识别的后缀时，才回退到关键词匹配。
        """
        if not title:
            return []
        low = title.lower()

        # 1) 文档后缀判定（最精确，命中即返回）
        for ext, ext_tags in DOC_EXTENSION_SCENES.items():
            if ext in low:
                return list(ext_tags)

        # 2) 关键词兜底（标题未带可识别后缀时）
        tags: list[str] = []
        if any(h in low for h in DRAWING_TITLE_HINTS):
            tags.append("drawing_2d")
        if ("assembly" in low or "装配" in title) and "assembly" not in tags:
            tags.append("assembly")
        if ("part" in low or "零件" in title) and "3d_modeling" not in tags:
            tags.append("3d_modeling")
        if "钣金" in title and "sheet_metal" not in tags:
            tags.append("sheet_metal")
        if ("schematic" in low or "原理图" in title) and "schematic" not in tags:
            tags.append("schematic")
        if ("pcb" in low and "布局" in title) or "PcbDoc" in title:
            if "pcb_layout" not in tags:
                tags.append("pcb_layout")
        if ("plan" in low or "平面" in title) and not tags:
            # 仅在完全没有其他线索时，才按建筑平面图处理
            tags.append("bim_modeling")
        return tags

    def _default_context(self) -> SceneContext:
        return SceneContext(
            profession_key=self.config.profile.get("active_profession", ""),
            software_name="通用文本",
            detected_by="default",
            confidence=0.1,
            scene_tags=["general"],
        )

    # ---------- 调试 ----------
    def diagnose(self) -> dict[str, object]:
        hwnd = get_foreground_hwnd()
        pid = get_window_pid(hwnd)
        return {
            "hwnd": hwnd,
            "title": get_window_title(hwnd),
            "pid": pid,
            "process": get_process_name(pid),
            "process_path": get_process_path(pid),
            "cursor": get_cursor_pos(),
            "context": self.detect(force=True).describe(),
        }


if __name__ == "__main__":
    import json
    det = SceneDetector()
    print(json.dumps(det.diagnose(), ensure_ascii=False, indent=2))
