"""v2.7 候选条「可移动 / 可固定 / 会跟随」回归（必须真实平台）

这四条是用户反复报过的一类问题（"修好这个坏了那个"），所以单独成套：

  1. **能移动**：拖着顶行走，位置真的变；松手即固定（老行为不变）
  2. **手抖不算拖动**：位移 <4px 不固定 —— 防"轻点一下之后再也不会跟随"
  3. **📌 固定按钮**：点一下固定/再点恢复跟随；固定时 follow_caret 不动它
  4. **单击跟随**：点进另一个输入位置时条子立刻跟过去（不等敲第一个键）
  5. **跨屏**：给副屏的物理插入符坐标，条子落在副屏可视区内

    python scripts/test_bar_move.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          f"{'' if cond else '  ' + str(extra)}")


def main() -> int:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from core.engine import TermEngine
    from core.ime import CaretInfo
    from scene.detector import SceneDetector
    from ui import bar as bm
    from ui import floatbtn as fb
    from ui.bar import DRAG_MIN_PX, CandidateBar
    from ui.inserter import Inserter

    app = QApplication([])
    eng = TermEngine()
    persisted: list[tuple] = []

    def settle(ms: int = 220) -> None:
        """等入场动画跑完（show_bar 有 120ms 的 pos/opacity 动画）。"""
        import time as _t
        end = _t.time() + ms / 1000.0
        while _t.time() < end:
            app.processEvents()
            _t.sleep(0.01)

    def make_bar() -> CandidateBar:
        # 每个用例都从"未固定"开始（config 是单例，上一个用例会把它写成 true）
        from core.config import get_config
        get_config().ui.bar_pinned = False
        get_config().ui.bar_pos = []
        b = CandidateBar(eng, SceneDetector(), Inserter())
        b.ui_persist = lambda sec, vals: persisted.append((sec, dict(vals)))
        b.show_input("c")
        b.refresh_candidates("c")
        app.processEvents()
        b.move(400, 400)
        settle(260)                  # 等 show_bar 的 pos 动画跑完，否则位置还在飞
        return b

    def ev(kind, local: QPoint, glob: QPoint, buttons):
        return QMouseEvent(kind, QPointF(local), QPointF(glob),
                           Qt.MouseButton.LeftButton, buttons,
                           Qt.KeyboardModifier.NoModifier)

    def drag(b: CandidateBar, dx: int, dy: int = 0) -> None:
        p0 = b.pos()
        b.mousePressEvent(ev(QEvent.Type.MouseButtonPress, QPoint(40, 8),
                             QPoint(p0.x() + 40, p0.y() + 8),
                             Qt.MouseButton.LeftButton))
        for step in (1, 2, 3):
            ddx, ddy = dx * step // 3, dy * step // 3
            b.mouseMoveEvent(ev(QEvent.Type.MouseMove, QPoint(40 + ddx, 8 + ddy),
                                QPoint(p0.x() + 40 + ddx, p0.y() + 8 + ddy),
                                Qt.MouseButton.LeftButton))
        b.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease,
                               QPoint(40 + dx, 8 + dy),
                               QPoint(p0.x() + 40 + dx, p0.y() + 8 + dy),
                               Qt.MouseButton.NoButton))
        app.processEvents()

    # ---------------- [1] 能移动 ----------------
    print("[1] 拖动移动（这条曾因 DRAG_MIN_PX 未定义而彻底失效）")
    ck("DRAG_MIN_PX 常量存在", isinstance(DRAG_MIN_PX, int) and DRAG_MIN_PX > 0,
       DRAG_MIN_PX)
    b = make_bar()
    p0 = b.pos()
    drag(b, 80, 30)
    ck("拖动后位置改变", b.pos() != p0, f"{p0} -> {b.pos()}")
    ck("拖动=自动固定", b.bar_pinned is True)
    ck("固定位置已持久化",
       any(v.get("bar_pinned") for _, v in persisted), persisted[-1:])

    # ---------------- [2] 手抖不算拖动 ----------------
    print("[2] 手抖 2px 不固定（防误钉住）")
    b = make_bar()
    p0 = b.pos()
    drag(b, 2, 1)
    ck("位移 3px 仍算点击：位置不变", b.pos() == p0, f"{p0} -> {b.pos()}")
    ck("位移 3px 不固定", b.bar_pinned is False)

    # ---------------- [3] 📌 固定按钮 ----------------
    print("[3] 📌 固定/跟随切换")
    b = make_bar()
    ck("按钮存在", hasattr(b, "btn_pin"))
    ck("跟随态初始不固定", b.bar_pinned is False)
    b._on_pin_clicked()
    app.processEvents()
    settle()
    ck("点击后固定", b.bar_pinned is True)
    ck("固定位置=当前位置", b._bar_pos == [b.x(), b.y()], b._bar_pos)
    # 固定点可能贴边被 clamp 一次（防越界），先让它稳定，再验"不再漂移"
    b.follow_caret()
    settle()
    before = b.pos()
    bm.get_caret_info = lambda: CaretInfo(0, 0, "caret", 0, 3000, 2000, 1.0)
    b.follow_caret()
    app.processEvents()
    ck("固定态不跟随光标", b.pos() == before, f"{before} -> {b.pos()}")
    b._on_pin_clicked()
    app.processEvents()
    ck("再点恢复跟随", b.bar_pinned is False)
    ck("恢复后立刻回到光标处", b.pos() != before, f"{before} -> {b.pos()}")

    # ---------------- [4] 单击跟随 ----------------
    print("[4] 单击新的输入位置 → 条子跟过去")
    b.hide()                      # 先把自家窗口收起来，免得落点被判定"压在自己窗口上"
    app.processEvents()
    hits: list = []
    w = fb.SelectionWatcher()
    w.clicked.connect(lambda p: hits.append(p))
    empty = QPoint(8, 8)          # 屏幕左上角，桌面/RDP 桌面位置，不压自窗
    real_own = fb.foreground_is_own_process
    try:
        fb.foreground_is_own_process = lambda: False
        w.feed(True, empty)
        fired = w.feed(False, empty)                 # 原地点击（0 位移）
    finally:
        fb.foreground_is_own_process = real_own
    ck("单击发出 clicked 信号", len(hits) == 1 and fired is False, (hits, fired))

    # 主程序侧：可见 + 未固定 → 调 follow_caret
    class _FakeIme:
        def __init__(self, composing=True):
            self.composing = composing

    class _FakeBar:
        def __init__(self, visible, pinned):
            self.visible, self.pinned, self.calls = visible, pinned, 0

        def isVisible(self):
            return self.visible

        @property
        def bar_pinned(self):
            return self.pinned

        def follow_caret(self):
            self.calls += 1

    from main import ZhiyuApp
    for visible, pinned, composing, want in ((True, False, True, 1),
                                             (True, True, True, 0),
                                             (False, False, True, 0),
                                             (True, False, False, 0)):
        fake = _FakeBar(visible, pinned)
        host = type("S", (), {"bar": fake, "ime": _FakeIme(composing),
                              "_last_follow": 0.0})()
        ZhiyuApp._on_click_follow(host, QPoint(10, 10))
        ck(f"点击跟随：visible={visible} pinned={pinned} 组合态={composing}"
           f" → 调用 {want} 次", fake.calls == want, fake.calls)

    # ---------------- [5] 跨屏 ----------------
    print("[5] 跨屏：物理点换算（条子落点由 test_dpi_position 专测）")
    import ctypes
    from ctypes import wintypes
    from ui.dpi import logical_point

    class _MI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    u32 = ctypes.WinDLL("user32")
    mons: list = []
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def _cb(hmon, hdc, rect, lp):
        mi = _MI()
        mi.cbSize = ctypes.sizeof(_MI)
        if u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            mons.append(mi)
        return True

    u32.EnumDisplayMonitors(None, None, PROC(_cb), 0)
    for mi in mons:
        px = (mi.rcMonitor.left + mi.rcMonitor.right) // 2
        py = (mi.rcMonitor.top + mi.rcMonitor.bottom) // 2
        mw = mi.rcMonitor.right - mi.rcMonitor.left
        mh = mi.rcMonitor.bottom - mi.rcMonitor.top
        lp = logical_point(px, py)
        target = QApplication.screenAt(lp)
        expect = next((sc for sc in QApplication.screens()
                       if abs(sc.geometry().width() * sc.devicePixelRatio() - mw) <= 2
                       and abs(sc.geometry().height() * sc.devicePixelRatio() - mh) <= 2),
                      None)
        ck(f"物理中心换算落在 {expect.name() if expect else '?'}",
           target is not None and expect is not None
           and target.name() == expect.name(),
           f"({px},{py}) -> ({lp.x()},{lp.y()}) on "
           f"{target.name() if target else None}")

    # ---------------- [6] 固定按钮不该破坏候选（QA 阻断项） ----------------
    print("[6] 📌 的 UI 副作用 / 收起态 / 屏外固定")
    b = make_bar()
    b._on_pin_clicked()
    settle()
    ck("固定后仍是输入态（没被提示态顶掉）", b.mode == "input", b.mode)
    ck("固定后候选胶囊仍可见",
       any(c.isVisible() for c in b.chips if not c.is_badge))
    b._on_pin_clicked()
    settle()

    b.close_panel()
    app.processEvents()
    b.collapsed = True
    b.show_hint("后台提示不该弹条")
    app.processEvents()
    ck("收起态下 show_hint 不弹条", b.isVisible() is False)

    class _R:
        ok = True
        text = "x"
        source = "free"
        source_label = "免费翻译"
        locked: list = []
        fixes: list = []
        unresolved: list = []
        error = ""

    b.show_result(_R())
    app.processEvents()
    ck("收起态下 show_result 也不弹条", b.isVisible() is False)
    b.collapsed = False

    b = make_bar()
    b.bar_pinned = True
    b._bar_pos = [99999, 99999]          # 模拟拔掉副屏/改分辨率后的废弃坐标
    b.follow_caret()
    app.processEvents()
    ck("固定点在屏外时自动回到跟随", b.bar_pinned is False, b._bar_pos)
    ck("且不会停在屏外", QApplication.screenAt(b.pos()) is not None, b.pos())

    b = make_bar()
    b.bar_pinned = True
    b._bar_pos = ["oops", 3]             # 配置被手改坏
    b.follow_caret()
    app.processEvents()
    ck("固定坐标非法时不崩、退回跟随", b.bar_pinned is False)

    print()
    print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("  失败项:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
