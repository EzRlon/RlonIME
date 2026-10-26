r"""
RlonIME — 物理屏幕坐标 → Qt 逻辑坐标（混合 DPI 多屏唯一换算入口，v2.6）

为什么需要它：
    `GetGUIThreadInfo` 给的插入符坐标是**物理像素**。旧代码用
    `物理 / 该窗口的缩放比` 直接当 Qt 坐标用，在「主屏 100% + 副屏 250%」
    的组合下必然算错——副屏上的点会被算到主屏去，候选条就像"没跟随光标"。

正确换算（三段）：
    1. 用该物理点找到它真正所属的**显示器**（MonitorFromPoint，
       靠近边缘的点也归最近的那块，不会因为 1px 抖动跳到隔壁屏）；
    2. 取这块显示器的物理矩形（GetMonitorInfoW.rcMonitor）；
    3. 逻辑坐标 = 该 QScreen 的逻辑原点 + (物理点 - 物理原点) / 缩放比。

    注意：不能用 QScreen.name() 去匹配 szDevice —— Qt 6 返回的是显示器
    EDID 友好名（"Cintiq Pro 17"），而 szDevice 是 "\\.\DISPLAY1"。
    这里用「物理尺寸 ≈ 逻辑尺寸 × dpr」来配对，跨驱动/跨缩放都稳。

所有窗口定位（候选条、弹窗、浮钮）都必须经这里，禁止再写 `物理/scale`。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication, QCursor

MONITOR_DEFAULTTONEAREST = 2


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def _monitor_info(px: int, py: int):
    """返回包含该物理点的显示器信息（靠近边缘时取最近的）。"""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR,
                                           ctypes.POINTER(_MONITORINFOEXW))
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        hmon = user32.MonitorFromPoint(wintypes.POINT(int(px), int(py)),
                                       MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return None
        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return None
        return mi
    except Exception:
        return None


def _pick_index(mon_left: int, mon_top: int, mw: int, mh: int,
                cands: list[tuple[float, float, float, float, float]]) -> int:
    """在候选屏里挑出该显示器对应的那一块（返回下标，-1=无候选）。

    cands 每项是 (逻辑 x, 逻辑 y, 逻辑宽, 逻辑高, dpr)。
    纯函数，便于用合成数据单测"两台同型号显示器"这种歧义场景。
    """
    hit = [i for i, (x, y, w, h, dpr) in enumerate(cands)
           if abs(w * dpr - mw) <= 2 and abs(h * dpr - mh) <= 2]
    if not hit:
        return -1
    if len(hit) == 1:
        return hit[0]

    def dist(i: int) -> float:
        x, y, _w, _h, dpr = cands[i]
        # 同一 dpr 组内，逻辑原点与物理原点只差一个缩放
        return abs(mon_left / dpr - x) + abs(mon_top / dpr - y)

    return min(hit, key=dist)


def _screen_for_monitor(mi, screens):
    """把 Win32 显示器配到 Qt 的 QScreen。

    ⚠️ 不能只按"物理尺寸≈逻辑尺寸×dpr"配对：**两台同型号同分辨率**的显示器
    尺寸完全一样，会永远命中第一块（QA 实测：副屏 (2500,500) 被算到主屏 580）。
    """
    mw = int(mi.rcMonitor.right - mi.rcMonitor.left)
    mh = int(mi.rcMonitor.bottom - mi.rcMonitor.top)
    cands = [(sc.geometry().x(), sc.geometry().y(),
              sc.geometry().width(), sc.geometry().height(),
              float(sc.devicePixelRatio() or 1.0)) for sc in screens]
    i = _pick_index(int(mi.rcMonitor.left), int(mi.rcMonitor.top), mw, mh, cands)
    return screens[i] if i >= 0 else None


def logical_point(px: int, py: int) -> QPoint:
    """物理屏幕点 → Qt 全局逻辑点（按该点所在显示器的缩放比换算）。"""
    mi = _monitor_info(px, py)
    screens = QGuiApplication.screens()
    if mi is not None:
        sc = _screen_for_monitor(mi, screens)
        if sc is not None:
            g = sc.geometry()
            dpr = float(sc.devicePixelRatio() or 1.0)
            return QPoint(
                int(g.x() + round((px - mi.rcMonitor.left) / dpr)),
                int(g.y() + round((py - mi.rcMonitor.top) / dpr)))
    # 兜底：按主屏缩放折算（单屏环境/异常情况）
    s = QGuiApplication.primaryScreen()
    dpr = float((s.devicePixelRatio() if s else 1.0) or 1.0)
    return QPoint(int(round(px / dpr)), int(round(py / dpr)))


def cursor_logical() -> QPoint:
    """鼠标位置。QCursor.pos() 本来就是 Qt 逻辑坐标，直接用，别再除。"""
    return QCursor.pos()
