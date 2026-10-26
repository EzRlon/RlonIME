"""
智语 — 悬停说明卡（UX-P1-4）

悬停任一候选胶囊 400ms 弹出：中文 + 英文标准译名（含标准号）+ 简述 +
推荐理由（场景/历史/词频等非零打分明细逐行列出）。

红线一致：与候选条同为 WS_EX_NOACTIVATE 不抢焦点窗口（make_no_activate），
移出/候选刷新/收条立即关闭，绝不驻留。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ui.bar import make_no_activate          # noqa: E402
from ui.design import DS                     # noqa: E402

HOVER_DELAY_MS = 400


class HoverCard(QWidget):
    """无边框说明卡：show_for() 直接弹出，schedule() 延迟弹出。"""

    def __init__(self, parent: QWidget | None = None):
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
        self._noact_applied = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        self.card.setObjectName("hoverCard")
        root.addWidget(self.card)
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.lab_zh = QLabel()
        self.lab_zh.setObjectName("hoverZh")
        self.lab_en = QLabel()
        self.lab_en.setObjectName("hoverEn")
        head.addWidget(self.lab_zh)
        head.addWidget(self.lab_en, 1)
        lay.addLayout(head)

        self.lab_brief = QLabel()
        self.lab_brief.setObjectName("hoverBrief")
        self.lab_brief.setWordWrap(True)
        lay.addWidget(self.lab_brief)

        self.lab_reason = QLabel()
        self.lab_reason.setObjectName("hoverReason")
        self.lab_reason.setWordWrap(True)
        lay.addWidget(self.lab_reason)

        self._apply_style()

        self._pending: tuple | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._do_show)

    # ---------------- 内容 ----------------
    def _apply_style(self) -> None:
        d = DS
        self.setStyleSheet(f"""
            #hoverCard {{
                background: {d.BG};
                border: 1px solid {d.LINE};
                border-radius: 8px;
            }}
            #hoverZh {{ color: {d.TEXT}; font-size: 13px; font-weight: 700; }}
            #hoverEn {{ color: {d.EN}; font-size: 11px; font-weight: 600; }}
            #hoverBrief {{ color: {d.TEXT_SUB}; font-size: 11px; }}
            #hoverReason {{ color: {d.TEXT_MUTED}; font-size: 10px; }}
        """)

    @staticmethod
    def _reason_lines(c, history_n: int) -> list[str]:
        """打分明细非零项逐行列出（复用 detail 字典）。"""
        from core.models import scene_label
        lines: list[str] = []
        det = c.detail
        t = c.term
        if t.custom or det.get("custom", 0) > 0:
            lines.append("自定义词条")
        if det.get("scene", 0) > 0 and t.scene_tags:
            lines.append("当前场景=" + "·".join(
                scene_label(s) for s in t.scene_tags[:2]))
        if det.get("profession", 0) > 0:
            lines.append("领域匹配")
        if history_n > 0:
            lines.append(f"你最近用过 {history_n} 次")
        elif det.get("freq", 0) > 20:
            lines.append("高频术语")
        if det.get("cross", 0) > 0:
            lines.append("跨领域召回")
        return lines

    # ---------------- 弹出 ----------------
    def show_for(self, c, anchor: QRect, history_n: int = 0) -> None:
        """在 anchor（胶囊的全局矩形）上方弹出说明卡。"""
        t = c.term
        self.lab_zh.setText(t.zh)
        en = t.en or ""
        if t.standard:
            en = f"{en} · {t.standard}" if en else t.standard
        self.lab_en.setText(en)
        self.lab_en.setVisible(bool(en))
        brief = t.brief or t.scene or ""
        self.lab_brief.setText(brief)
        self.lab_brief.setVisible(bool(brief))
        reasons = self._reason_lines(c, history_n)
        self.lab_reason.setText(
            "推荐理由：" + "；".join(reasons) if reasons else "")
        self.lab_reason.setVisible(bool(reasons))

        self.adjustSize()
        x = anchor.left()
        y = anchor.top() - self.height() - 6
        if y < 0:                       # 顶部没地方就放到胶囊下方
            y = anchor.bottom() + 6
        self.move(x, y)
        if not self._noact_applied:
            self.show()
            make_no_activate(self)
            self._noact_applied = True
        self.show()

    def schedule(self, ms: int, c, anchor: QRect, history_n: int = 0) -> None:
        """延迟 ms 毫秒弹出（期间 cancel/再次 schedule 会覆盖）。"""
        self._pending = (c, anchor, history_n)
        self._timer.start(ms)

    def _do_show(self) -> None:
        if self._pending is not None:
            c, anchor, history_n = self._pending
            self._pending = None
            self.show_for(c, anchor, history_n)

    def cancel(self) -> None:
        """移出 / 刷新 / 收条：立即撤销并关闭。"""
        self._pending = None
        self._timer.stop()
        self.hide()
