"""
RlonIME — 截图取词遮罩（v2.8，OCR 的前半截）

交互：按 Ctrl+Alt+O → 光标所在屏变暗 → 拖一个框 → 松手即识别 → Esc 取消。

两条实现约束：
  · **一屏一个覆盖窗**：只在光标所在屏铺满，跨屏框选会让混合 DPI 下的坐标
    与截图两边都不一致（本机就是 100% + 250%），得不偿失；
  · 截图前必须先 `hide()` 再等一拍：遮罩本身也是屏幕内容，
    直接 grab 会把半透明黑一起拍进去。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ui.design import DS

MIN_SIZE = 6          # 小于它视为误点


class CaptureOverlay(QWidget):
    """全屏半透明遮罩，拖框后发出 captured(QImage)。"""

    captured = Signal(QImage)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self._origin: QPoint | None = None
        self._cur: QPoint | None = None
        self._screen = None
        self._grabbing = False

    # ---------------- 生命周期 ----------------
    def start(self) -> bool:
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if scr is None:
            return False
        self._screen = scr
        self._origin = None
        self._cur = None
        self._grabbing = False
        self.setGeometry(scr.geometry())
        self.show()
        self.raise_()
        self.activateWindow()          # 遮罩需要键盘（Esc），这里可以抢焦点
        self.setFocus()
        return True

    def _selection(self) -> QRect:
        if self._origin is None or self._cur is None:
            return QRect()
        return QRect(self._origin, self._cur).normalized()

    # ---------------- 交互 ----------------
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._origin = e.position().toPoint()
            self._cur = self._origin
            self.update()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._origin is not None:
            self._cur = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton or self._grabbing:
            return
        self._cur = e.position().toPoint()
        rect = self._selection()
        if rect.width() < MIN_SIZE or rect.height() < MIN_SIZE:
            self._origin = None
            self._cur = None
            self.update()
            return
        self._grabbing = True
        self.grab_region(rect)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.finish_cancel()

    def finish_cancel(self) -> None:
        self._grabbing = False
        self.hide()
        self.cancelled.emit()

    # ---------------- 截图 ----------------
    def grab_region(self, rect: QRect) -> None:
        """先隐藏自己再抓屏，否则会把遮罩拍进去。"""
        self.hide()
        QTimer.singleShot(70, lambda r=QRect(rect): self._do_grab(r))

    def _do_grab(self, rect: QRect) -> None:
        img = QImage()
        try:
            if self._screen is not None:
                img = self._screen.grabWindow(0, rect.x(), rect.y(),
                                              rect.width(), rect.height())
        except Exception:
            img = QImage()
        self._grabbing = False
        if img.isNull() or img.width() < MIN_SIZE:
            self.cancelled.emit()
            return
        self.captured.emit(img)

    # ---------------- 绘制 ----------------
    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        sel = self._selection()
        if sel.isValid() and not sel.isEmpty():
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(sel, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(DS.PRIMARY), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(sel)
            tip = f"{sel.width()} × {sel.height()}  ·  松手识别  ·  Esc 取消"
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.setPen(QColor(DS.TEXT))
            box = p.fontMetrics().boundingRect(tip).adjusted(-8, -4, 8, 4)
            ty = sel.top() - box.height() - 6
            if ty < 4:
                ty = sel.bottom() + 6
            box.moveTo(sel.left(), ty)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 232))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor(DS.TEXT))
            p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), tip)
        else:
            p.setFont(QFont("Microsoft YaHei UI", 13))
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter),
                       "拖拽框选要识别的文字 · Esc 取消")
        p.end()
