"""
RlonIME — 选区悬浮「译」钮（v2.4.0 / N1）

交互：在任何程序里拖选一段文字 → 松开左键 → 光标旁浮出一枚「译」钮
→ 点它 = 就地翻译（中→英 / 英→中 自动判向）并替换选区。

三条设计红线（与候选条同源）：
  1. **不抢焦点**：Frameless + Tool + WindowDoesNotAcceptFocus +
     WA_ShowWithoutActivating + Win32 WS_EX_NOACTIVATE 双保险。
     一旦抢焦点，原程序的选区就没了，取词和写回全部落空。
  2. **不打扰**：3.2s 无操作自动消失；点候选条/自己的弹窗不触发；
     鼠标只是单击（未拖选）不触发。
  3. **手动定位**：定位一律 `QApplication.screenAt(点)` 逐屏判断，
     禁止用 primaryScreen 夹取（副屏会被拉回主屏，v2.3.1 已踩过）。

检测方式：轮询 GetAsyncKeyState(VK_LBUTTON)（90ms 一次，纯读状态，无钩子）。
不用鼠标钩子是因为全局鼠标钩子会拖慢所有点击，而这里只需要"拖选结束"。
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.ime import foreground_is_own_process
from ui.bar import make_no_activate
from ui.design import DS

VK_LBUTTON = 0x01
DRAG_THRESHOLD = 10        # 位移小于它 = 单击，不是拖选
POLL_MS = 90
AUTO_HIDE_MS = 3200


def _left_button_down() -> bool:
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
    except Exception:
        return False


class SelectionWatcher(QObject):
    """拖选结束检测：只读鼠标状态，不装任何全局钩子。

    feed() 是纯状态机（可注入按下/位置），单测直接驱动它，
    不需要真的挪鼠标。
    """

    selected = Signal(QPoint)     # 拖选结束（浮出「译」钮）
    clicked = Signal(QPoint)      # 单击落点（v2.7：候选条就地跟随新输入位置）

    def __init__(self, parent=None, button_down=None, cursor=None):
        super().__init__(parent)
        self._button_down = button_down or _left_button_down
        self._cursor = cursor or self._qt_cursor
        self._pressed_at: QPoint | None = None
        self._timer: QTimer | None = None

    @staticmethod
    def _qt_cursor() -> QPoint:
        from PySide6.QtGui import QCursor
        return QCursor.pos()

    # ---------- 生命周期 ----------
    def start(self, interval: int = POLL_MS) -> None:
        if self._timer is not None:
            return
        self._timer = QTimer(self)
        self._timer.setInterval(interval)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def reset(self) -> None:
        self._pressed_at = None

    # ---------- 判定 ----------
    def _poll(self) -> None:
        try:
            self.feed(self._button_down(), self._cursor())
        except Exception:
            pass

    def feed(self, pressed: bool, pos: QPoint) -> bool:
        """喂一次鼠标状态；检测到「拖选结束」返回 True 并发出 selected。"""
        if pressed:
            if self._pressed_at is None:
                self._pressed_at = pos
            return False
        if self._pressed_at is None:
            return False
        start, self._pressed_at = self._pressed_at, None
        if foreground_is_own_process():
            return False                      # 在我们自己的窗口里点/拖的
        if QApplication.widgetAt(pos) is not None:
            return False                      # 落点压在自己的候选条/弹窗上
        if (pos - start).manhattanLength() < DRAG_THRESHOLD:
            # 单击：把"换了个输入位置"这件事告诉主程序（候选条需要跟过去）
            self.clicked.emit(pos)
            return False
        self.selected.emit(pos)
        return True


class FloatTranslateButton(QWidget):
    """一枚只有「译」字的浮钮，点一下就翻译当前选区。"""

    clicked = Signal()

    W, H = 34, 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("翻译选区（中→英 / 英→中）")
        self._hover = False
        self._noact = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    # ---------- 定位 ----------
    @staticmethod
    def place_for(pos: QPoint, size: tuple[int, int] = (W, H)) -> QPoint:
        """按光标所在屏幕夹取落点（多屏红线：screenAt 逐屏判断）。"""
        w, h = size
        x, y = pos.x() + 10, pos.y() + 14
        scr = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if scr is not None:
            geo = scr.availableGeometry()
            if x + w > geo.right():
                x = max(geo.left() + 4, pos.x() - w - 10)
            if y + h > geo.bottom():
                y = max(geo.top() + 4, pos.y() - h - 12)
            x = max(geo.left() + 4, min(x, geo.right() - w - 4))
            y = max(geo.top() + 4, min(y, geo.bottom() - h - 4))
        return QPoint(int(x), int(y))

    def popup_at(self, pos: QPoint) -> None:
        self.move(self.place_for(pos, (self.W, self.H)))
        self.show()
        if not self._noact:
            make_no_activate(self)
            self._noact = True
        self.raise_()
        self._timer.start(AUTO_HIDE_MS)

    def dismiss(self) -> None:
        self._timer.stop()
        self._hover = False
        self.hide()

    # ---------- 交互 ----------
    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self._timer.stop()
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self._timer.start(900)
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.activate()

    def activate(self) -> None:
        """触发翻译并立刻收钮（点击后不能挡住原程序的选区）。"""
        self.dismiss()
        self.clicked.emit()

    # ---------- 绘制 ----------
    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = DS
        r = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        bg = QColor(d.PRIMARY if self._hover else d.BG)
        fg = QColor(d.SEL_TEXT if self._hover else d.PRIMARY)
        p.setPen(QPen(QColor(d.PRIMARY if self._hover else d.LINE), 1.0))
        p.setBrush(bg)
        p.drawRoundedRect(r, 8, 8)
        p.setFont(QFont("Microsoft YaHei UI", 13, QFont.Weight.DemiBold))
        p.setPen(fg)
        p.drawText(QRectF(0, 0, self.width(), self.height()),
                   int(Qt.AlignmentFlag.AlignCenter), "译")
        p.end()
