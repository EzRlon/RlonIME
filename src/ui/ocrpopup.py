"""
RlonIME — OCR 结果卡（v2.8）

一张小卡片：上面是识别到的原文，下面是译文（自动判向），底部四个动作：
复制原文 / 复制译文 / 插入译文 / 关闭。

设计取舍：
  · 不抢焦点的意义在这里不成立（用户明确按了截图热键），所以这张卡正常可聚焦，
    方便用户改错字、Ctrl+C；
  · 「插入译文」需要先把**截图前的前台窗口**还原（遮罩抢过焦点），
    否则 SendInput 会打到我们自己身上——这条在主程序里处理。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton

from ui.design import DS
from ui.popups import _CardPopup


class OcrPopup(_CardPopup):
    """识别结果 + 译文 + 复制/插入。"""

    insert_requested = Signal(str)
    copy_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__("截图取词", width=380, parent=parent)
        self._src = ""
        self._dst = ""

        self.lab_meta = QLabel("")
        self.lab_meta.setStyleSheet(f"color: {DS.TEXT_MUTED}; font-size: 10px;")
        self.body.addWidget(self.lab_meta)

        self.ed_src = QPlainTextEdit()
        self.ed_src.setReadOnly(True)
        self.ed_src.setFixedHeight(84)
        self.ed_src.setPlaceholderText("识别到的文字")
        self.body.addWidget(self.ed_src)

        self.ed_dst = QPlainTextEdit()
        self.ed_dst.setReadOnly(True)
        self.ed_dst.setFixedHeight(72)
        self.ed_dst.setPlaceholderText("译文")
        self.body.addWidget(self.ed_dst)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {DS.TEXT_FAINT}; font-size: 10px;")
        self.body.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_copy_src = self._mk("复制原文", "btnGhost")
        self.btn_copy_dst = self._mk("复制译文", "btnGhost")
        self.btn_insert = self._mk("插入译文", "btnGhost")
        self.btn_close = self._mk("关闭", "btnPrimary")
        for b in (self.btn_copy_src, self.btn_copy_dst, self.btn_insert,
                  self.btn_close):
            row.addWidget(b)
        self.body.addLayout(row)

        self.btn_copy_src.clicked.connect(lambda: self._copy(self._src))
        self.btn_copy_dst.clicked.connect(lambda: self._copy(self._dst))
        self.btn_insert.clicked.connect(self._insert)
        self.btn_close.clicked.connect(self.close)

    @staticmethod
    def _mk(text: str, obj: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(obj)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    # ---------------- 填充 ----------------
    def show_result(self, src: str, lang: str, elapsed_ms: float,
                    dst: str = "", note: str = "") -> None:
        self._src = src or ""
        self._dst = dst or ""
        self.ed_src.setPlainText(self._src)
        self.ed_dst.setPlainText(self._dst)
        bits = [lang or "OCR"]
        if elapsed_ms:
            bits.append(f"{elapsed_ms:.0f} ms")
        if note:
            bits.append(note)
        self.lab_meta.setText(" · ".join(bits))
        self.status.setText(
            "Ctrl+C 可复制；「插入译文」会还原到你截图前的窗口光标处"
            if self._dst else "正在翻译…")
        self.btn_copy_dst.setEnabled(bool(self._dst))
        self.btn_insert.setEnabled(bool(self._dst))

    def set_translation(self, dst: str, note: str = "") -> None:
        self._dst = dst or ""
        self.ed_dst.setPlainText(self._dst)
        self.btn_copy_dst.setEnabled(bool(self._dst))
        self.btn_insert.setEnabled(bool(self._dst))
        self.status.setText(note or "")

    # ---------------- 动作 ----------------
    def _copy(self, text: str) -> None:
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.status.setText("已复制到剪贴板")
        self.copy_requested.emit(text)

    def _insert(self) -> None:
        if not self._dst:
            return
        self.hide()
        self.insert_requested.emit(self._dst)
