"""v2.6 跨屏定位回归（必须真实平台跑，offscreen 只有一个虚拟屏）

背景：插入符坐标是**物理像素**。旧代码 `物理/该窗口缩放比` 在
「主屏 100% + 副屏 250%」的组合下会把副屏的点算回主屏——用户看到的就是
"点了输入位置，候选条却跑到另一块屏"。

本用例不依赖挪窗口：直接拿每块显示器的物理中心点做换算，断言
  1. 换算结果落在**尺寸匹配**的那块屏里；
  2. 旧口径（物理/dpr）在同一批点上会算错（证明修复有必要）；
  3. 候选条 follow_caret 收到物理坐标后，真的贴在目标屏内。

    python scripts/test_dpi_position.py
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          f"{'' if cond else '  ' + str(extra)}")


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def enum_monitors() -> list[_MONITORINFOEXW]:
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    out: list[_MONITORINFOEXW] = []
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR,
                              wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                              wintypes.LPARAM)

    def _cb(hmon, hdc, rect, lp):
        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            out.append(mi)
        return True

    u32.EnumDisplayMonitors(None, None, PROC(_cb), 0)
    return out


def main() -> int:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from ui.dpi import logical_point

    app = QApplication([])
    screens = QApplication.screens()
    print(f"屏幕 {len(screens)} 块：" + ", ".join(
        f"{s.name()}({s.geometry().width()}x{s.geometry().height()}@{s.devicePixelRatio():g}x)"
        for s in screens))

    mons = enum_monitors()
    ck("枚举到显示器", len(mons) >= 1, len(mons))
    ck("显示器数量与 Qt 一致", len(mons) == len(screens),
       f"{len(mons)} vs {len(screens)}")

    mixed = len({s.devicePixelRatio() for s in screens}) > 1
    print(f"[信息] 混合 DPI：{'是' if mixed else '否'}")

    print("[1] 每块屏的物理中心 → 逻辑坐标落在同一块屏")
    bad = []
    naive_bad = 0
    for mi in mons:
        cx = (mi.rcMonitor.left + mi.rcMonitor.right) // 2
        cy = (mi.rcMonitor.top + mi.rcMonitor.bottom) // 2
        lp = logical_point(cx, cy)
        scr = QApplication.screenAt(lp)
        mw = mi.rcMonitor.right - mi.rcMonitor.left
        mh = mi.rcMonitor.bottom - mi.rcMonitor.top
        expect = [s for s in screens
                  if abs(s.geometry().width() * s.devicePixelRatio() - mw) <= 2
                  and abs(s.geometry().height() * s.devicePixelRatio() - mh) <= 2]
        ok = bool(scr is not None and expect and scr.name() == expect[0].name())
        if not ok:
            bad.append((mi.szDevice, cx, cy, lp.x(), lp.y(),
                        scr.name() if scr else None,
                        expect[0].name() if expect else None))
        # 旧口径对照：物理 / 该屏 dpr
        dpr = (expect[0].devicePixelRatio() if expect else 1.0) or 1.0
        naive = QPoint(int(cx / dpr), int(cy / dpr))
        n_scr = QApplication.screenAt(naive)
        if not (n_scr is not None and expect and n_scr.name() == expect[0].name()):
            naive_bad += 1
    ck("每块屏都换算正确", not bad, bad[:2])
    if mixed:
        ck("旧口径确实会算错（证明修复必要）", naive_bad > 0,
           f"naive 错 {naive_bad} 块（若为 0 说明本机布局恰好碰巧对上）")

    print("[2] 候选条 follow_caret：给物理坐标就贴到目标屏")
    from core.engine import TermEngine
    from core.ime import CaretInfo
    from scene.detector import SceneDetector
    from ui import bar as bar_mod
    from ui.bar import CandidateBar
    from ui.inserter import Inserter

    eng = TermEngine()
    bar = CandidateBar(eng, SceneDetector(), Inserter())
    bar.bar_pinned = False
    bar.show_input("k")

    targets = [s for s in screens if s.name() != QApplication.primaryScreen().name()] \
        or list(screens)
    for s in targets:
        g = s.geometry()
        cw = g.width() * s.devicePixelRatio()
        ch = g.height() * s.devicePixelRatio()
        # 取该屏物理矩形中心附近（用 monitor 的 rcMonitor 更准）
        mi = next((m for m in mons
                   if abs((m.rcMonitor.right - m.rcMonitor.left) - cw) <= 2
                   and abs((m.rcMonitor.bottom - m.rcMonitor.top) - ch) <= 2),
                  None)
        px = (mi.rcMonitor.left + mi.rcMonitor.right) // 2 if mi else int(cw / 2)
        py = (mi.rcMonitor.top + mi.rcMonitor.bottom) // 2 if mi else int(ch / 2)
        bar_mod.get_caret_info = lambda px=px, py=py: CaretInfo(
            0, 0, "caret", 0, px, py, s.devicePixelRatio())
        bar.follow_caret()
        app.processEvents()
        geo = s.availableGeometry()
        inside = geo.contains(bar.pos()) and geo.contains(
            bar.pos() + QPoint(bar.width() - 1, bar.height() - 1))
        ck(f"候选条落在 {s.name()} 内", inside,
           f"bar=({bar.x()},{bar.y()},{bar.width()}x{bar.height()}) "
           f"screen={geo.x()},{geo.y()},{geo.width()}x{geo.height()}")

    # 复原（避免污染后续用例）
    bar_mod.get_caret_info = bar_mod.CaretInfo  # type: ignore[assignment]

    print("[3] 同尺寸双屏歧义（QA 案例：两台 1920x1080@100%）")
    from ui.dpi import _pick_index
    same = [(0, 0, 1920, 1080, 1.0), (1920, 0, 1920, 1080, 1.0)]
    ck("右屏物理点 (2500,500) 配到第 2 块",
       _pick_index(2500, 500, 1920, 1080, same) == 1,
       _pick_index(2500, 500, 1920, 1080, same))
    ck("左屏物理点 (500,500) 配到第 1 块",
       _pick_index(500, 500, 1920, 1080, same) == 0,
       _pick_index(500, 500, 1920, 1080, same))
    stacked = [(0, 0, 1920, 1080, 1.0), (0, 1080, 1920, 1080, 1.0)]
    ck("上下叠放：下方物理点配到第 2 块",
       _pick_index(800, 1500, 1920, 1080, stacked) == 1,
       _pick_index(800, 1500, 1920, 1080, stacked))

    print()
    print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("  失败项:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
