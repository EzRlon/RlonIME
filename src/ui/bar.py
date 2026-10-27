"""
RlonIME — 横向候选条（主界面，v2.2 去按钮化三段布局）

布局（输入态，纵向三段，都在同一张卡片内）：

    ┌ card ───────────────────────────────────────────┐
    │ [拼音 lead] [modeHint]      ＋ 🏷 Ⓐ ⏸ Ⓘ ✕      │ 顶行空白=拖动区
    │ ┌ 译法列 TranslationColumn（池化 3 行）────────┐ │  拖动即钉住 · 双击恢复跟随
    │ │ ★ Counterbore · GB/T 152.3  [推荐]          │ │ ← 当前选中项译法，高亮
    │ │   Counter Bore                              │ │ ← var_en 次译法
    │ └─────────────────────────────────────────────┘ │
    │ [1 沉孔] [2 埋头孔] [3 螺纹孔] [4 盲孔]           │ 中文胶囊行
    └─────────────────────────────────────────────────┘

    结果态  [⇄ 记忆库 │ A counterbore shall be machined ...]
    提示态  [✓ │ 已学习：沉孔 → Counterbore]
    联想态  [沉孔 ✓ 联想→ [通孔] [埋头孔]]（T04，MODE_SUGGEST）

顶行图标列（v2.2）：＋=加词条弹窗 · 🏷(四宫格)=行业弹窗 ·
Ⓐ=AI 增强设置（开启时高亮）· ⏸/▶=暂停/恢复接管 · Ⓘ=关于 · ✕=退出。

三条设计红线：
  1. **不抢焦点**——WS_EX_NOACTIVATE + WindowDoesNotAcceptFocus 双保险。
     拖动一律用鼠标事件直移窗口，绝不 activateWindow。
  2. **常驻复用**——窗口、胶囊、译法行全部池化预建，刷新只改文字不改控件树。
  3. **键盘优先，鼠标可选**——点击译法行=该译法英文上屏（不含标准号），
     点击中文胶囊=中文上屏；上屏只走 _emit_text（SendInput Unicode）。
"""
from __future__ import annotations

import queue
import sys
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QSizePolicy, QVBoxLayout, QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.assoc import MAX_SUGGESTIONS, AssociationProvider
from core.config import get_config
from core.ime import CaretInfo, get_caret_info
from core.models import Candidate, SceneContext
from ui.design import DS
from ui.dpi import cursor_logical, logical_point
from ui.icons import IconWidget

WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20

MODE_INPUT, MODE_RESULT, MODE_HINT, MODE_SUGGEST = (
    "input", "result", "hint", "suggest")

# 胶囊池大小：9 枚候选 + 1 枚「…+N」溢出徽标（M1）。
# 内核候选上限也是 9，所以池子 10 就够，永远不会有更多可见胶囊。
MAX_CHIPS = 10
HINT_MS = 2800           # 提示态自动淡出
RESULT_MS = 4200         # 结果态自动淡出
SUGGEST_MS = 3000        # 联想态自动收起
LEAD_CAP = 120          # 拼音回显区最大宽度
EN_COL_MAX_W = 420      # 译法列最大内容宽

# M1 溢出治理：胶囊行可用的真实内容宽度。
# = 条宽上限 - 左右内边距 - 卡片 1px 边框 *2 - 2px 取整余量。
# 布局前按这个宽度「能放几枚放几枚」，绝不把总宽交给 QHBoxLayout 去挤
# （挤的结果就是 K 键 9 枚候选互相覆盖、序号被盖、末枚被切）。
CHIPS_AVAIL_W = DS.BAR_MAX_W - 2 * DS.PAD_X - 4
BADGE_W = 42            # 「…+N」徽标预留宽度（最多 "…+8"，够用）
DRAG_MIN_PX = 4         # 拖动判定阈值：手抖 1px 不算拖动（v2.6）


def make_no_activate(widget) -> bool:
    """Win32 层再加一道 WS_EX_NOACTIVATE，与 Qt 侧形成双保险。"""
    try:
        import ctypes
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        u32.GetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int)
        u32.GetWindowLongW.restype = ctypes.c_long
        u32.SetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int,
                                       ctypes.c_long)
        u32.SetWindowLongW.restype = ctypes.c_long
        hwnd = int(widget.winId())
        style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                           style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        return True
    except Exception:
        return False


# ================================================================
# 候选胶囊
# ================================================================
class Chip(QWidget):
    """单个候选：序号 + 中文。自绘以获得一致的圆角与选中态。"""

    picked = Signal(int)
    hovered = Signal(int)
    unhovered = Signal()
    badge_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.index = -1
        self.is_badge = False       # 「…+N」溢出徽标（不参与选词/序号）
        self._group = ""            # v3.0.3：pro/common，用于视觉分区
        self._zh = ""
        self._sel = False
        self._hover = False
        self._w = 40
        self._idx_w = 11
        # 配色覆写：(选中渐变顶, 选中渐变底, 悬停底色)；None = 主色蓝
        self._pal: tuple[str, str, str] | None = None
        self.setFixedHeight(DS.CHIP_H)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        d = DS
        self._f_zh = QFont("Microsoft YaHei UI", d.FS_ZH, QFont.Weight.DemiBold)
        self._f_idx = QFont("Consolas", d.FS_IDX)

    # ---------- 配色 ----------
    def set_palette(self, sel_hi: str, sel: str, soft: str) -> None:
        """覆写胶囊配色（联想/「译」等特殊条目用强调紫）。"""
        self._pal = (sel_hi, sel, soft)
        self.update()

    def reset_palette(self) -> None:
        if self._pal is not None:
            self._pal = None
            self.update()

    # ---------- 内容 ----------
    def set_fonts(self, family: str, fs_zh: int | None = None) -> None:
        self._f_zh = QFont(family, fs_zh or DS.FS_ZH, QFont.Weight.DemiBold)
        self._f_idx = QFont("Consolas", DS.FS_IDX)

    def measure(self, index: int, zh: str, cap: int = 400) -> int:
        """按内容测量胶囊宽度（只读，不改控件状态）。

        M1：序号宽度用字体实测（Consolas 9px 的 "1" 与 "9" 等宽，但不同
        字号/字体下不一定），文本按 cap 省略后再测。布局按这个值累加，
        总宽超了就少放几枚 —— 不再交给 QHBoxLayout 硬挤。
        """
        fm_zh = QFontMetrics(self._f_zh)
        text = fm_zh.elidedText(zh or "", Qt.TextElideMode.ElideRight, cap)
        if index >= 0:
            idx_w = max(11, QFontMetrics(self._f_idx).horizontalAdvance(
                str(index + 1)))
            fixed = 6 + idx_w + 4 + 8
        else:
            fixed = 6 + 8
        return max(fixed + fm_zh.horizontalAdvance(text), 30)

    def set_content(self, index: int, zh: str, cap: int = 400,
                    max_w: int = 0) -> None:
        """设置内容；max_w>0 时按像素上限二次省略（单枚超宽的兜底）。"""
        self.index = index
        fm_zh = QFontMetrics(self._f_zh)
        text = fm_zh.elidedText(zh or "", Qt.TextElideMode.ElideRight, cap)
        if index >= 0:
            self._idx_w = max(11, QFontMetrics(self._f_idx).horizontalAdvance(
                str(index + 1)))
            fixed = 6 + self._idx_w + 4 + 8
        else:
            self._idx_w = 0
            fixed = 6 + 8
        if max_w > 0 and fixed + fm_zh.horizontalAdvance(text) > max_w:
            text = fm_zh.elidedText(zh or "", Qt.TextElideMode.ElideRight,
                                    max(16, max_w - fixed))
        self._zh = text
        self._w = max(fixed + fm_zh.horizontalAdvance(text), 30)
        self.setFixedWidth(self._w)
        self.update()

    def set_group(self, group: str) -> None:
        """候选来源分组：pro（工程/智能词库）带极浅主色底，common 留白。

        用户要的是"一眼看出哪几个是工程词、哪几个是日常字"，所以用底色
        而不是边框 —— 边框在浅色主题下几乎看不见。
        """
        g = group or ""
        if self._group != g:
            self._group = g
            self.update()

    def set_selected(self, on: bool) -> None:
        if self._sel != on:
            self._sel = on
            self.update()

    # ---------- 交互 ----------
    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        if self.index >= 0:
            self.hovered.emit(self.index)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        self.unhovered.emit()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.is_badge:
            self.badge_clicked.emit()
        elif self.index >= 0:
            self.picked.emit(self.index)

    # ---------- 绘制 ----------
    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = DS
        if self._pal is not None:
            pal_hi, pal_sel, pal_soft = self._pal
        elif getattr(self, "_sug", False):
            pal_hi, pal_sel, pal_soft = sug_palette()
        else:
            pal_hi, pal_sel, pal_soft = (
                d.PRIMARY_HI, d.PRIMARY, d.PRIMARY_SOFT)
        r = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        if self._sel:
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0.0, QColor(pal_hi))
            g.setColorAt(1.0, QColor(pal_sel))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(r, d.CHIP_RADIUS, d.CHIP_RADIUS)
        elif self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal_soft))
            p.drawRoundedRect(r, d.CHIP_RADIUS, d.CHIP_RADIUS)
        elif self._group == "pro":
            tint = QColor(d.PRIMARY)
            tint.setAlpha(26)          # ~10%：能看出分区，不抢文字
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tint)
            p.drawRoundedRect(r, d.CHIP_RADIUS, d.CHIP_RADIUS)

        sel = self._sel
        x = 6
        if self.index >= 0:
            p.setFont(self._f_idx)
            p.setPen(QColor(d.SEL_SUB if sel else d.TEXT_FAINT))
            p.drawText(QRectF(x, 0, self._idx_w, self.height()),
                       int(Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignLeft), str(self.index + 1))
            x += self._idx_w + 4
        fm = QFontMetrics(self._f_zh)
        p.setFont(self._f_zh)
        p.setPen(QColor(d.SEL_TEXT if sel else d.TEXT))
        zw = fm.horizontalAdvance(self._zh)
        p.drawText(QRectF(x, 0, zw + 1, self.height()),
                   int(Qt.AlignmentFlag.AlignVCenter
                       | Qt.AlignmentFlag.AlignLeft), self._zh)
        p.end()


# ================================================================
# 联想胶囊（MODE_SUGGEST 专用，绿系强调色随主题）
# ================================================================
def sug_palette() -> tuple[str, str, str]:
    """联想/「译」强调配色：(sel_hi, sel, hover_soft)，取主题 AI 令牌。"""
    d = DS
    return (d.AI_HI, d.AI, d.AI_SOFT)


class SugChip(Chip):
    """联想词胶囊：与中文候选同构，绿系强调以区分「词库候选」与「联想」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sug = True
        self.setFixedHeight(DS.SUG_H)


# ================================================================
# 译法列：当前选中项的英文译法（池化 3 行自绘控件）
# ================================================================
class EnRow(QWidget):
    """译法列单行：variant 0 = term.en（含标准号），1-2 = var_en 次译法。

    点击 = 该译法英文上屏（不含标准号，标准号留给 Tab/Ctrl+数字路径）。
    """

    picked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.variant_idx = -1
        self._text = ""
        self._sub = ""              # 标准号等附注
        self._star = False          # 首行 ★
        self._rec = False           # 「推荐」徽标
        self._hover = False
        self.setFixedHeight(DS.EN_ROW_H)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        d = DS
        self._f_en = QFont("Consolas", d.FS_EN, QFont.Weight.DemiBold)
        self._f_sub = QFont("Consolas", d.FS_EN - 1)
        self._f_badge = QFont("Microsoft YaHei UI", 8)

    def set_fonts(self, family: str, fs_en: int | None = None) -> None:
        size = fs_en or DS.FS_EN
        self._f_en = QFont(["Consolas", family], size, QFont.Weight.DemiBold)
        self._f_sub = QFont(["Consolas", family], max(size - 1, 7))
        self._f_badge = QFont(family, 8)

    def set_content(self, variant_idx: int, text: str, sub: str = "",
                    star: bool = False, rec: bool = False) -> None:
        self.variant_idx = variant_idx
        self._text = text or ""
        self._sub = sub or ""
        self._star = star
        self._rec = rec
        self.update()

    def text_width(self) -> int:
        fm = QFontMetrics(self._f_en)
        w = 18 + fm.horizontalAdvance(self._text)
        if self._sub:
            w += QFontMetrics(self._f_sub).horizontalAdvance(self._sub) + 8
        if self._rec:
            w += 34
        return w

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.variant_idx >= 0:
            self.picked.emit(self.variant_idx)

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = DS
        r = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        if self._star:
            # v3.0.4：首选译法整行铺底。主色换亮绿后 PRIMARY_SOFT 大面积
            # 铺开太抢眼，改用 10% 主色（与 pro 胶囊同一档），hover 再换中性底。
            if self._hover:
                tint = QColor(d.BG_HOVER)
            else:
                tint = QColor(d.PRIMARY)
                tint.setAlpha(24)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tint)
            p.drawRoundedRect(r, 5, 5)
        elif self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(d.BG_HOVER))
            p.drawRoundedRect(r, 5, 5)

        x = 6.0
        h = self.height()
        if self._star:
            p.setFont(self._f_en)
            p.setPen(QColor(d.PRIMARY))
            p.drawText(QRectF(x, 0, 12, h),
                       int(Qt.AlignmentFlag.AlignVCenter), "★")
        x += 14.0

        avail = self.width() - x - (34 if self._rec else 6)
        fm = QFontMetrics(self._f_en)
        text = fm.elidedText(self._text, Qt.TextElideMode.ElideRight,
                             max(int(avail), 30))
        p.setFont(self._f_en)
        p.setPen(QColor(d.EN if not self._star else d.PRIMARY_TEXT))
        p.drawText(QRectF(x, 0, avail, h),
                   int(Qt.AlignmentFlag.AlignVCenter
                       | Qt.AlignmentFlag.AlignLeft), text)
        tw = fm.horizontalAdvance(text)
        if self._sub:
            p.setFont(self._f_sub)
            p.setPen(QColor(d.TEXT_FAINT))
            p.drawText(QRectF(x + tw + 6, 0, avail - tw - 6, h),
                       int(Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignLeft), self._sub)
        if self._rec:
            br = QRectF(self.width() - 30.0, (h - 14) / 2.0, 26.0, 14.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(d.OK_SOFT))
            p.drawRoundedRect(br, 3, 3)
            p.setFont(self._f_badge)
            p.setPen(QColor(d.OK))
            p.drawText(br, int(Qt.AlignmentFlag.AlignCenter), "推荐")
        p.end()


class TranslationColumn(QWidget):
    """译法列：池化 3 个 EnRow。数据源 = candidates[active_index]。

    无英文的候选 → 整列隐藏（中文-only 词不造译法，守不伪造红线）。
    """

    picked = Signal(int)            # variant_idx

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.rows: list[EnRow] = []
        for _ in range(3):
            r = EnRow(self)
            r.picked.connect(self.picked)
            r.hide()
            lay.addWidget(r)
            self.rows.append(r)
        self._shown = 0
        self.hide()

    def set_fonts(self, family: str, fs_en: int | None = None) -> None:
        for r in self.rows:
            r.set_fonts(family, fs_en)

    def set_candidate(self, c: Candidate | None, show_rec: bool = False) -> int:
        """刷新三行内容，返回可见行数；无英文译法时整列隐藏。"""
        texts: list[tuple[str, str, bool]] = []   # (text, sub, star)
        if c is not None and c.en:
            sub = f"· {c.term.standard}" if c.term.standard else ""
            texts.append((c.en, sub, True))
            for v in (c.term.var_en or [])[:2]:
                if v and v.strip() and v.strip().lower() != c.en.lower():
                    texts.append((v.strip(), "", False))
        for i, row in enumerate(self.rows):
            if i < len(texts):
                text, sub, star = texts[i]
                row.set_content(i, text, sub, star=star,
                                rec=show_rec and i == 0)
                row.show()
            else:
                row.set_content(-1, "")
                row.hide()
        self._shown = len(texts)
        self.setVisible(bool(texts))
        return self._shown

    def visible_rows(self) -> int:
        return self._shown

    def content_width(self) -> int:
        return max((r.text_width() for r in self.rows if r.isVisible()),
                   default=0)


# ================================================================
# 主候选条
# ================================================================
class CandidateBar(QWidget):
    """横向候选条：输入态 / 结果态 / 提示态 / 联想态 四合一。"""

    committed = Signal(str, str)
    closed = Signal()
    open_add_word = Signal()      # ＋：加词条弹窗
    open_industry = Signal()      # 🏷：行业弹窗
    open_ai = Signal()            # Ⓐ：AI 增强设置弹窗
    open_about = Signal()         # Ⓘ：关于弹窗
    pause_clicked = Signal()      # ⏸/▶：暂停/恢复接管
    quit_clicked = Signal()       # ✕：退出（与托盘双击同语义）
    toggle_branch = Signal()
    chip_picked = Signal(int)

    def __init__(self, engine, detector, inserter):
        super().__init__()
        self.config = get_config()
        self.engine = engine
        self.detector = detector
        self.inserter = inserter

        self.scene: SceneContext = detector.detect(force=True)
        self.candidates: list[Candidate] = []
        self.active_index = 0
        self.mode = MODE_INPUT
        self.lead_text = ""
        self.ime = None
        self.translator = None
        self.hotkey_handler = None
        self._ime_poll = None
        self._shown = 0
        # M1 溢出治理：分页结果（每页 = (起始候选下标, 可见枚数)）
        self._pages: list[tuple[int, int]] = []
        self._page_idx = 0
        self._badge_w = BADGE_W
        self._chips_end = 0        # 胶囊行实际占用宽度（手动定位后回写）
        self._stable_w = 0         # 多页时取最宽页，翻页不改变条宽
        # M3：本次输入是否存在「全拼/简拼/整串完整覆盖」级别的精确命中。
        # 只有精确命中时空格/回车才取首候选；否则直出原文（不替用户选词）。
        self._exact_match = False
        # v3.0.4 候选模式：auto（智能）/ plain（只通用词）/ pro（只专业词）
        _m = str(getattr(self.config.ui, "candidate_mode", "auto") or "auto")
        self.candidate_mode = _m if _m in ("auto", "plain", "pro") else "auto"
        self._noact_applied = False
        self.collapsed = False       # 用户点了 ✕ 收起（后台提示不得再弹条）
        self._anim = None
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.close_panel)

        # ---- T04 联想槽位 + 「译」候选 ----
        # 联想提供器：独立实例，只读共享 engine 的 LexiconManager（白名单），
        # 不经 engine.search 的任何数据通路。
        self.assoc = AssociationProvider(engine.lexicons)
        self._sug_words: list[str] = []
        self._sug_source = ""
        # 「译」候选：{"src", "text", "via": "llm"|"local"} 或 None
        self._translate_offer: dict | None = None
        self._last_query = ""
        # AI「译」路径的异步结果队列（回调在子线程，主线程定时消费）
        self._offer_results: queue.Queue = queue.Queue()
        self._offer_timer = QTimer(self)
        self._offer_timer.setInterval(80)
        self._offer_timer.timeout.connect(self._drain_offer_results)
        self._offer_timer.start()

        # 拖动固定（UX-P1-1，全局一份）
        self.bar_pinned = bool(getattr(self.config.ui, "bar_pinned", False))
        self._bar_pos: list[int] = list(
            getattr(self.config.ui, "bar_pos", []) or [])
        self.ui_persist = None          # main 接线：ZhiyuApp._persist
        self._drag_origin: QPoint | None = None
        self._drag_start = QPoint(0, 0)
        self._drag_moved = False

        self._setup_window()
        self._build()
        self._apply_style()

        # 悬停说明卡（UX-P1-4）
        from ui.hovercard import HoverCard
        self.hover = HoverCard()

        self.apply_scope()
        self.refresh_scene()
        self._update_mode_label()

    # ---------------- 窗口 ----------------
    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumWidth(DS.BAR_MIN_W)
        self.setMaximumWidth(DS.BAR_MAX_W)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(*DS.SHADOW))
        self.setGraphicsEffect(shadow)

    # ---------------- 结构 ----------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.card = QFrame()
        self.card.setObjectName("card")
        root.addWidget(self.card)
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(DS.PAD_X, 3, DS.PAD_X, 5)
        lay.setSpacing(DS.GAP)

        # ---- 顶行：拼音 + modeHint + 页码 + 图标列（v2.2 去按钮化）----
        self.top_row = QWidget(self.card)
        top = QHBoxLayout(self.top_row)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(DS.GAP)
        self.top_row.setFixedHeight(DS.TOP_ROW_H)
        # 整条顶行空白 = 拖动区（拖动即钉住，双击恢复跟随；无把手、无钉住按钮）
        self.top_row.setCursor(Qt.CursorShape.SizeAllCursor)
        self.top_row.setToolTip("拖动移动候选条（自动钉住）· 双击恢复跟随光标")

        # 结果/提示态的来源图标（输入态不出现）
        self.lead_icon = IconWidget("check", 12, DS.OK)
        self.lead_icon.setFixedSize(16, 16)
        self.lead_icon.hide()
        # 中/EN 状态徽标（v3.0）：以前只有托盘图标一个颜色点，候选条上
        # 完全看不出当前是中文还是英文模式，打字前全靠记忆。
        # 单击可直接切换（与双击 Shift 等价），tooltip 说明两种手势。
        self.state_label = QLabel("中")
        self.state_label.setObjectName("stateTag")
        self.state_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.state_label.setToolTip("当前：中文输入 · 单击切换中/英（也可双击 Shift）")
        self.state_label.mousePressEvent = (  # type: ignore[method-assign]
            lambda e: self._toggle_state()
            if e.button() == Qt.MouseButton.LeftButton else None)
        # 候选模式按钮（v3.0.4）：智能 / 普通 / 专业。用户反馈"专业词混进来
        # 让正常输入不像输入法"，所以给一个显式开关，而不是让他猜排序逻辑。
        self.mode_label = QLabel("智")
        self.mode_label.setObjectName("modeTag")
        self.mode_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_label.mousePressEvent = (  # type: ignore[method-assign]
            lambda e: self._cycle_candidate_mode()
            if e.button() == Qt.MouseButton.LeftButton else None)
        self.lead = QLabel("")
        self.lead.setObjectName("lead")
        # 匹配状态提示：「无匹配 · 空格直出」/「前缀 zhi」/「无译法 · 可开 AI」
        self.mode_hint = QLabel("")
        self.mode_hint.setObjectName("modeHint")
        self.mode_hint.hide()
        self.tail = QLabel("")
        self.tail.setObjectName("tail")

        # ---- 图标列：＋ 🏷 Ⓐ ⏸ Ⓘ ✕ ----
        def _mk_btn(name: str, tip: str, slot) -> IconWidget:
            b = IconWidget(name, 12, DS.TEXT_FAINT)
            b.setFixedSize(17, 17)
            b.set_clickable(DS.PRIMARY)
            b.setToolTip(tip)
            b.mousePressEvent = (  # type: ignore[method-assign]
                lambda e, s=slot: s()
                if e.button() == Qt.MouseButton.LeftButton else None)
            return b

        # v2.7（U09）：固定/跟随切换做成显式按钮 —— 以前只能"拖动即钉住、
        # 双击顶行解除"，用户根本不知道现在是不是跟随状态。
        self.btn_pin = _mk_btn(
            "pin", "固定候选条（不跟随光标）", self._on_pin_clicked)
        self.btn_add = _mk_btn(
            "plus", "加词条", lambda: self.open_add_word.emit())
        self.btn_industry = _mk_btn(
            "grid", "行业范围", lambda: self.open_industry.emit())
        self.btn_ai = _mk_btn(
            "ai", "AI 增强设置", lambda: self.open_ai.emit())
        self.btn_pause = _mk_btn(
            "pause", "暂停拼音接管", self._on_pause_clicked)
        self.btn_info = _mk_btn(
            "info", "关于 RlonIME", lambda: self.open_about.emit())
        self.btn_close = _mk_btn(
            "close", "收起到托盘（右键托盘图标可退出）",
            lambda: self.quit_clicked.emit())
        self._icon_btns = [self.btn_pin, self.btn_add, self.btn_industry,
                           self.btn_ai, self.btn_pause, self.btn_info,
                           self.btn_close]

        top.addWidget(self.state_label)
        top.addWidget(self.mode_label)
        top.addWidget(self.lead_icon)
        top.addWidget(self.lead)
        top.addWidget(self.mode_hint)
        top.addStretch(1)
        top.addWidget(self.tail)
        for b in self._icon_btns:
            top.addWidget(b)
        lay.addWidget(self.top_row)

        # ---- 译法列（输入态，无英文时整列隐藏） ----
        self.en_col = TranslationColumn(self.card)
        self.en_col.picked.connect(self.commit_en)
        lay.addWidget(self.en_col)

        # ---- 中文胶囊行 ----
        # 注意：**不用 QHBoxLayout**。实测 Qt 会缓存 setFixedWidth 之前的
        # sizeHint，布局 pass 按旧宽算 x 坐标，相邻胶囊仍会压边 6px（M1 复现）。
        # 胶囊 ≤10 枚、总宽已由 _compute_pages 控制，手动定位最可靠。
        self.chips_host = QWidget(self.card)
        self.chips_host.setFixedHeight(DS.CHIP_H)
        self.chips_host.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Fixed)
        self.chips: list[Chip] = []
        for _ in range(MAX_CHIPS):
            c = Chip(self.chips_host)
            c.picked.connect(self._on_chip_picked)
            c.badge_clicked.connect(self._on_badge_clicked)
            c.hovered.connect(self._on_chip_hovered)
            c.unhovered.connect(self._on_chip_unhovered)
            c.hide()
            self.chips.append(c)
        lay.addWidget(self.chips_host)

        # ---- 联想胶囊行（MODE_SUGGEST，独立 SugChip 池，最多 4 枚） ----
        self.sug_host = QWidget(self.card)
        sh = QHBoxLayout(self.sug_host)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(DS.GAP)
        self.sug_chips: list[SugChip] = []
        for _ in range(MAX_SUGGESTIONS):
            c = SugChip(self.sug_host)
            c.picked.connect(self.commit_suggestion)
            c.hide()
            sh.addWidget(c)
            self.sug_chips.append(c)
        sh.addStretch(1)
        self.sug_host.hide()
        lay.addWidget(self.sug_host)

        # ---- 预览行（结果态/提示态正文；输入态空候选时的指引） ----
        self.preview = QLabel("")
        self.preview.setObjectName("preview")
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Preferred)
        self.preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview.setToolTip("单击复制译文")
        self.preview.mousePressEvent = (  # type: ignore[method-assign]
            self._copy_result)
        self.preview.hide()
        lay.addWidget(self.preview)

    def _apply_style(self) -> None:
        d = DS
        cfg = self.config.ui
        fam = cfg.font_family
        fs_zh = getattr(cfg, "font_size_zh", d.FS_ZH)
        fs_en = getattr(cfg, "font_size_en", d.FS_EN)
        for c in self.chips:
            c.set_fonts(fam, fs_zh)
        # 徽标宽度按真实字体实测（"…+8" 在雅黑 11 下约 59px，不能拍 42）
        try:
            self._badge_w = max(BADGE_W, self.chips[0].measure(-1, "…+8"))
        except Exception:
            self._badge_w = BADGE_W
        self.en_col.set_fonts(fam, fs_en)
        self.setStyleSheet(f"""
            #card {{
                background: {d.BG};
                border: 1px solid {d.LINE};
                border-radius: {d.RADIUS}px;
            }}
            #lead {{
                color: {d.TEXT};
                font-size: {fs_zh}px;
                font-family: "Consolas", "{fam}";
                font-weight: 600;
            }}
            #modeTag {{
                color: {d.PRIMARY_TEXT};
                font-size: {max(9, fs_zh - 2)}px;
                font-family: "{fam}";
                font-weight: 700;
                padding: 0 3px;
                border: 1px solid {d.PRIMARY_TEXT};
                border-radius: 4px;
            }}
            #stateTag {{
                color: {d.PRIMARY_TEXT};
                font-size: {max(9, fs_zh - 2)}px;
                font-family: "{fam}";
                font-weight: 700;
                padding: 0 3px;
                border: 1px solid {d.PRIMARY_TEXT};
                border-radius: 4px;
            }}
            #modeHint {{
                color: {d.WARN};
                background: {d.WARN_SOFT};
                border-radius: 3px;
                padding: 1px 6px;
                font-size: {d.FS_IDX}px;
                font-family: "{fam}";
            }}
            #preview {{
                color: {d.TEXT_SUB};
                font-size: {d.FS_TIP}px;
                font-family: "Consolas", "{fam}";
            }}
            #tail {{
                color: {d.TEXT_FAINT};
                font-size: {d.FS_IDX}px;
                font-family: "Consolas";
            }}
        """)
        self._elide()
        self.refresh_icons()

    def refresh_icons(self) -> None:
        """同步顶行图标列的配色与状态（AI 开关高亮 / 暂停⇄恢复）。"""
        d = DS
        # 固定按钮：钉住时高亮主色，跟随态淡灰
        self.btn_pin.set_color(d.PRIMARY if self.bar_pinned else d.TEXT_FAINT)
        self.btn_pin.set_hover_color(d.PRIMARY_HI)
        self.btn_pin.setToolTip(
            "已固定：不跟随光标（点击切回跟随）" if self.bar_pinned
            else "固定候选条（不跟随光标）")
        for b in (self.btn_add, self.btn_industry, self.btn_info, self.btn_close):
            b.set_color(d.TEXT_FAINT)
            b.set_hover_color(d.PRIMARY)
        try:
            llm_on = bool((getattr(self.config, "llm", {}) or {}).get("enabled"))
        except Exception:
            llm_on = False
        self.btn_ai.set_color(d.AI if llm_on else d.TEXT_FAINT)
        self.btn_ai.set_hover_color(d.AI_HI if llm_on else d.PRIMARY)
        self.btn_ai.setToolTip(
            "AI 增强：已开启（点击配置）" if llm_on
            else "AI 增强：关闭（点击配置）")
        on = True if self.ime is None else bool(self.ime.enabled)
        self.btn_pause.set_icon("pause" if on else "play")
        self.btn_pause.set_color(d.TEXT_FAINT if on else d.WARN)
        self.btn_pause.set_hover_color(d.PRIMARY)
        self.btn_pause.setToolTip("暂停拼音接管" if on else "恢复拼音接管")

    def _on_pause_clicked(self) -> None:
        self.pause_clicked.emit()

    def set_theme(self, theme: str) -> None:
        import ui.design as design
        design.set_theme(theme)
        globals()["DS"] = design.DS
        self._apply_style()
        self.update()

    # ---------------- 文本省略 ----------------
    def _set_lead(self, text: str) -> None:
        self.lead_text = text or ""
        fm = QFontMetrics(self.lead.font())
        self.lead.setText(fm.elidedText(self.lead_text, Qt.TextElideMode.ElideRight,
                                        LEAD_CAP))
        self.lead.setFixedWidth(min(fm.horizontalAdvance(self.lead.text()) + 2,
                                    LEAD_CAP))
        self.lead.setVisible(bool(self.lead_text))

    def _set_mode_hint(self, text: str) -> None:
        self.mode_hint.setText(text or "")
        self.mode_hint.setVisible(bool(text))

    # ---------------- 中/英状态（v3.0） ----------------
    def _update_state_label(self, enabled: bool | None = None) -> None:
        """同步顶行「中 / EN」徽标。双击 Shift、Ctrl+Space、点徽标都会走到这。"""
        if enabled is None:
            enabled = bool(getattr(self.ime, "enabled", True))
        on = bool(enabled)
        self.state_label.setText("中" if on else "EN")
        color = DS.PRIMARY if on else DS.TEXT_FAINT
        self.state_label.setStyleSheet(
            f"color:{color}; border:1px solid {color};"
            f"border-radius:4px; padding:0 3px; font-weight:700;")
        self.state_label.setToolTip(
            "当前：中文输入 · 单击切换中/英（也可双击 Shift）" if on
            else "当前：英文直通 · 单击切换中/英（也可双击 Shift）")

    _MODE_ORDER = ("auto", "plain", "pro")
    _MODE_TEXT = {"auto": "智", "plain": "普", "pro": "专"}
    _MODE_TIP = {
        "auto": "候选来源：智能（工程词 + 日常词，分组显示）· 单击切换",
        "plain": "候选来源：普通（只用日常词库，不出现工程词）· 单击切换",
        "pro": "候选来源：专业（只用工程词库，不出现日常字）· 单击切换",
    }

    def _update_mode_label(self) -> None:
        m = self.candidate_mode
        self.mode_label.setText(self._MODE_TEXT.get(m, "智"))
        color = {"auto": DS.PRIMARY_TEXT, "plain": DS.TEXT_FAINT,
                 "pro": DS.AI}.get(m, DS.PRIMARY_TEXT)
        self.mode_label.setStyleSheet(
            f"color:{color}; border:1px solid {color};"
            f"border-radius:4px; padding:0 3px; font-weight:700;")
        self.mode_label.setToolTip(self._MODE_TIP.get(m, ""))

    def _cycle_candidate_mode(self) -> None:
        """点「智/普/专」循环切换，立即重算当前候选并落盘。"""
        try:
            i = self._MODE_ORDER.index(self.candidate_mode)
        except ValueError:
            i = 0
        self.candidate_mode = self._MODE_ORDER[(i + 1) % len(self._MODE_ORDER)]
        self._update_mode_label()
        self.config.ui.candidate_mode = self.candidate_mode
        if callable(self.ui_persist):
            self.ui_persist("ui", {"candidate_mode": self.candidate_mode})
        tip = {"auto": "智能：工程词 + 日常词",
               "plain": "普通：只用日常词库",
               "pro": "专业：只用工程词库"}[self.candidate_mode]
        self._set_mode_hint("候选 · " + tip)
        QTimer.singleShot(2000, lambda: self._set_mode_hint(""))
        if self._last_query:
            self.refresh_candidates(self._last_query)

    def _toggle_state(self) -> None:
        """点徽标 = 切中/英。走内核开关，事件回流后标签自动同步。"""
        if self.ime is None:
            return
        self.ime.set_enabled(not bool(self.ime.enabled))

    def _set_preview(self, text: str, color: str | None = None) -> None:
        self.preview.setStyleSheet(f"color: {color};" if color else "")
        self._preview_full = text or ""
        self._elide()

    def _copy_result(self, e=None) -> None:
        """单击结果正文 = 复制完整译文。

        显示的是省略号截断后的文本，复制的是全文 —— 长句翻译里这一步
        比"再看一眼"实用得多（用户通常要粘到图纸或邮件里）。
        """
        if e is not None and e.button() != Qt.MouseButton.LeftButton:
            return
        text = getattr(self, "_result_text", "")
        if not text:
            return
        QApplication.clipboard().setText(text)
        prev = self.lead_text
        self._set_lead("译文已复制")
        QTimer.singleShot(1100, lambda t=prev: self._set_lead(t))

    def _elide(self) -> None:
        if not hasattr(self, "_preview_full"):
            return
        avail = max(self.preview.width() - 4, 40)
        fm = QFontMetrics(self.preview.font())
        self.preview.setText(
            fm.elidedText(self._preview_full, Qt.TextElideMode.ElideRight, avail))

    def _elide_later(self) -> None:
        """布局尚未完成时，宽度还是旧值；排到事件循环下一拍再省略。"""
        QTimer.singleShot(0, self._elide)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    # ---------------- 尺寸 ----------------
    def _fit_geometry(self) -> None:
        """三段布局自适应：宽取三行内容最大需求，高 = 可见行之和。"""
        if self.mode in (MODE_RESULT, MODE_HINT):
            return                      # 结果/提示态各自定宽定高
        d = DS
        icons_w = (sum(b.width() for b in self._icon_btns)
                   + d.GAP * len(self._icon_btns))
        top_need = (self.lead.width()
                    + (self.lead_icon.width()
                       if self.lead_icon.isVisible() else 0)
                    + (self.mode_hint.width() + d.GAP
                       if self.mode_hint.isVisible() else 0)
                    + QFontMetrics(self.tail.font()).horizontalAdvance(
                        self.tail.text()) + icons_w + d.GAP * 3)
        chips_need = 0
        pool = self.sug_chips if self.mode == MODE_SUGGEST else self.chips
        for c in pool:
            if c.isVisible():
                chips_need += c.width() + d.GAP
        if self.mode == MODE_INPUT and self._stable_w:
            chips_need = max(chips_need, self._stable_w)
        en_need = self.en_col.content_width() if self.en_col.isVisible() else 0
        preview_need = (QFontMetrics(self.preview.font())
                        .horizontalAdvance(self._preview_full) + 8
                        if self.preview.isVisible()
                        and hasattr(self, "_preview_full") else 0)
        width = max(top_need, chips_need, en_need, preview_need,
                    120) + d.PAD_X * 2
        self.setFixedWidth(max(d.BAR_MIN_W, min(d.BAR_MAX_W, width)))

        h = d.TOP_ROW_H + 8
        if self.en_col.isVisible():
            h += self.en_col.visible_rows() * (d.EN_ROW_H + 2) + d.GAP
        if self.chips_host.isVisible():
            h += d.CHIP_H + d.GAP
        if self.sug_host.isVisible():
            h += d.SUG_H + d.GAP
        if self.preview.isVisible():
            h += 22 + d.GAP
        self.setFixedHeight(h)

    def _relayout(self) -> None:
        """输入态排版：空候选给指引/「译」候选；有候选铺胶囊 + 联动译法列。"""
        if self.mode != MODE_INPUT:
            return
        pool = self.candidates
        if not pool:
            self.en_col.set_candidate(None)
            self.tail.setText("")
            self._shown = 0
            self._pages = []
            self._page_idx = 0
            if self._translate_offer is not None:
                # 「译」候选：词库无命中但本地/AI 有真实译法（不伪造）
                self._set_mode_hint("")
                self.preview.hide()
                self.chips_host.show()
                self._layout_offer_chip()
            else:
                # I03：措辞从"无匹配 · 空格直出"改为更明确的一行，
                # 新用户不会再以为输入法坏了（␣ = 空格键符号）。
                self._set_mode_hint(
                    "无译法 · 可开 AI" if self._has_cjk(self._last_query)
                    else "未收录 · ␣ 直出原文")
                self.chips_host.hide()
                self._set_preview("回车 / 空格 直接上屏原文，Esc 取消",
                                  DS.TEXT_FAINT)
                self.preview.show()
            self._fit_geometry()
            return
        if pool[0].match_type == "prefix_partial":
            cut = int(pool[0].detail.get("prefix_len", 0))
            self._set_mode_hint(f"前缀匹配 {self.lead_text[:cut]}")
        else:
            self._set_mode_hint("")
        self.preview.hide()
        self.chips_host.show()

        shown = self._layout_chips()
        self._shown = max(shown, 1)
        self._refresh_en_col()
        self._update_tail()
        self._fit_geometry()

    # ---------------- M1 溢出治理：分页 ----------------
    def _chip_width(self, i: int) -> int:
        """第 i 个候选的胶囊实测宽度（用池内首个胶囊的字体）。"""
        return self.chips[0].measure(i, self.candidates[i].zh)

    def _compute_pages(self, avail: int) -> list[tuple[int, int]]:
        """按可用宽度把候选切成若干页，返回 [(起始下标, 枚数), ...]。

        规则：能放几枚放几枚；**如果后面还有候选**，本页必须给「…+N」
        徽标预留 BADGE_W + 间距。绝不让总宽超过 avail —— 这正是
        「输入 k 时折弯系数盖住后续序号」的根因（旧版无脑 show 满 9 枚）。
        """
        n = len(self.candidates)
        if n <= 0:
            return []
        gap = DS.GAP
        pages: list[tuple[int, int]] = []
        i = 0
        while i < n:
            w = 0
            cnt = 0
            while i + cnt < n:
                cw = self._chip_width(i + cnt)
                add = cw + (gap if cnt else 0)
                more_after = (i + cnt + 1) < n
                limit = avail - ((self._badge_w + gap) if more_after else 0)
                if cnt > 0 and w + add > limit:
                    break
                w += add
                cnt += 1
                if more_after and w > avail - (self._badge_w + gap):
                    break
            pages.append((i, max(cnt, 1)))
            i += max(cnt, 1)
        return pages

    def _page_of(self, index: int) -> int:
        """包含给定候选下标的页序号（找不到时取最后一页）。"""
        for p, (start, cnt) in enumerate(self._pages):
            if start <= index < start + cnt:
                return p
        return max(len(self._pages) - 1, 0)

    def _on_badge_clicked(self) -> None:
        self.page_move(1)

    def _layout_chips(self) -> int:
        """铺开当前页胶囊 + 溢出徽标，返回本页可见候选数。

        只渲染「包含当前选中项」的那一页，所以方向键/翻页不会出现
        "选中了但看不见"的情况。
        """
        self._pages = self._compute_pages(CHIPS_AVAIL_W)
        total = len(self.candidates)
        if not self._pages:
            for c in self.chips:
                c.is_badge = False
                c.reset_palette()
                c.set_group("")
                c.hide()
            self._page_idx = 0
            self._chips_end = 0
            return 0
        self._page_idx = self._page_of(self.active_index)
        start, cnt = self._pages[self._page_idx]
        has_badge = (start + cnt) < total
        # 翻页时条宽必须稳定：否则第 1 页 7 枚、第 2 页 2 枚，条子会突然
        # 缩掉一半 —— 用户第一反应是"点坏了"。取所有页里最宽的那页。
        if len(self._pages) > 1:
            widths = []
            for st, cn in self._pages:
                # 口径必须和 _fit_geometry 的 chips_need 一致（每枚都带一个
                # GAP），否则两页之间会差一个 GAP（实测 4px，条子仍会微跳）
                w = sum(self._chip_width(st + k) for k in range(cn))
                w += DS.GAP * cn
                if st + cn < total:
                    w += self._badge_w + DS.GAP
                widths.append(w)
            self._stable_w = max(widths)
        else:
            self._stable_w = 0
        for k, c in enumerate(self.chips):
            c.is_badge = False
            c.reset_palette()
            c.setToolTip("")
            if k < cnt:
                i = start + k
                max_w = (CHIPS_AVAIL_W - self._badge_w - DS.GAP
                         if has_badge and k == cnt - 1 else CHIPS_AVAIL_W)
                c.set_group(self.candidates[i].group)
                c.set_content(i, self.candidates[i].zh, max_w=max_w)
                c.set_selected(i == self.active_index)
                c.show()
            elif has_badge and k == cnt:
                c.is_badge = True
                c.set_group("")
                c.set_content(-1, f"…+{total - start - cnt}")
                c.setToolTip("还有 %d 个候选：滚轮 / PgDn / = 翻页"
                             "（点这里也行）" % (total - start - cnt))
                c.show()
            else:
                c.hide()
        self._place_chips()
        return cnt

    def _place_chips(self) -> None:
        """手动排布可见胶囊（左起 0，间距 DS.GAP），返回占用总宽。

        必须先 set_content（宽度已定）再定位 —— 这样不存在"布局按旧宽度
        算坐标"的竞态，K 键那 6px 压边由此消除。
        """
        x = 0
        for c in self.chips:
            if not c.isVisible():
                continue
            if x:
                x += DS.GAP
            c.setGeometry(x, 0, c.width(), DS.CHIP_H)
            x += c.width()
        self._chips_end = x
        return x

    def _layout_offer_chip(self) -> None:
        """「译」候选：独占胶囊行第 1 位，紫色强调，不占数字序号。"""
        offer = self._translate_offer
        if offer is None:
            return
        label = (f"译 {offer['text']} →" if offer["via"] == "local"
                 else "译 → AI 翻译")
        chip = self.chips[0]
        chip.set_palette(*sug_palette())
        chip.set_content(0, label)
        chip.set_selected(False)
        chip.show()
        self._pages = [(0, 1)]
        self._page_idx = 0
        self._shown = 1
        for c in self.chips[1:]:
            c.is_badge = False
            c.reset_palette()
            c.hide()
        self._place_chips()

    def _update_tail(self) -> None:
        pool = self.candidates
        if not pool or self._shown <= 0:
            self.tail.setText("")
            return
        n_pages = max(1, len(self._pages))
        if n_pages <= 1:
            self.tail.setText("")
            return
        # 把"还能怎么翻"直接写在条上，别指望用户去悬停看 tooltip
        hint = "PgDn/=" if self._page_idx < n_pages - 1 else "PgUp/-"
        self.tail.setText(f"{self._page_idx + 1}/{n_pages} · {hint}")

    # ---------------- 选中联动（单点收口） ----------------
    def _set_active(self, i: int, follow: bool = False) -> None:
        """方向键/翻页/鼠标选中统一入口：刷新胶囊选中态 + 译法列 + 页码。

        M1：选中项若不在当前页，先重铺该页再上色 —— 否则会出现
        「选中了但候选条上根本看不见」。
        """
        if not self.candidates:
            return
        self.active_index = max(0, min(i, len(self.candidates) - 1))
        if self._pages and self._page_of(self.active_index) != self._page_idx:
            self._layout_chips()
        else:
            for c in self.chips:
                if c.isVisible() and not c.is_badge:
                    c.set_selected(c.index == self.active_index)
        self._refresh_en_col()
        self._update_tail()
        self._fit_geometry()
        if follow:
            self.follow_caret()

    def _refresh_en_col(self) -> None:
        """译法列跟随当前选中项；「推荐」标只在第一名拉开分差时出现。"""
        if self.mode != MODE_INPUT or not self.candidates:
            self.en_col.set_candidate(None)
            return
        c = self.candidates[self.active_index]
        margin = float(getattr(self.config.ranking, "rec_badge_margin", 8.0))
        show_rec = (self.active_index == 0
                    and len(self.candidates) >= 2
                    and self.candidates[0].score - self.candidates[1].score
                    > margin)
        self.en_col.set_candidate(c, show_rec)

    # ---------------- 位置 ----------------
    def _screen_for_point(self, x: int, y: int):
        """取指定点所在屏幕（独立方法便于测试注入）。"""
        try:
            return QApplication.screenAt(QPoint(int(x), int(y)))
        except Exception:
            return None

    def _nearest_screen(self, pt: QPoint):
        """点落在屏幕布局空隙（多屏拼接的凹口）时，取最近的一块。

        以前这里直接 move(nx, ny) 原样放，条子会停在"任何屏幕都不属于"的
        区域里——用户看到的就是"条子不见了"（QA 场景：633,1468）。
        """
        best, best_d = None, None
        for scr in QApplication.screens():
            g = scr.availableGeometry()
            dx = max(g.left() - pt.x(), 0, pt.x() - g.right())
            dy = max(g.top() - pt.y(), 0, pt.y() - g.bottom())
            d = dx + dy
            if best_d is None or d < best_d:
                best, best_d = scr, d
        return best

    def follow_caret(self) -> None:
        """拖动固定后贴持久化位置；否则贴真实插入符（取不到退回鼠标）。

        v2.6 跨屏修复：插入符坐标是**物理像素**，必须经 ui/dpi.logical_point()
        按「该点所在显示器」的物理原点与缩放换算。旧口径 `物理/scale` 在
        主屏 100% + 副屏 250% 的组合下会把副屏的点算回主屏 —— 表现就是
        "点了输入位置，候选条却出现在另一块屏上"。

        v3.0.2：**固定只在同一块屏内有效**。用户换到另一块屏输入，说明固定点
        已经没有意义（条子留在旧屏 = 完全看不见），此时自动解除固定并跟随。
        这是用户反馈「在另一个屏幕点击输入，条子还在原来的屏」的真正原因：
        旧配置里 `ui.bar_pinned=true` + `bar_pos` 落在副屏，而跟随逻辑看到
        钉住就无条件返回，永远不会过去。
        """
        info: CaretInfo = get_caret_info()
        if info.source == "caret" and (info.phys_x or info.phys_y):
            anchor = logical_point(info.phys_x, info.phys_y)
        elif info.source == "caret":
            # 兼容：单测/冒烟注入的是逻辑坐标（phys 未填）
            anchor = QPoint(info.x, info.y)
        else:
            anchor = cursor_logical()

        if self.bar_pinned and len(self._bar_pos) == 2:
            try:
                px, py = int(self._bar_pos[0]), int(self._bar_pos[1])
            except (TypeError, ValueError):
                self._release_pin("位置配置无效 · 已恢复跟随")
            else:
                scr = self._screen_for_point(px + 4, py + 4)
                cur = self._screen_for_point(anchor.x(), anchor.y())
                if scr is None:
                    # 副屏被拔掉/改了分辨率：固定点已在所有屏幕之外，
                    # 继续硬贴会让条子永久不可见 → 自动回到跟随。
                    self._release_pin("固定点已失效 · 已恢复跟随")
                elif cur is not None and cur is not scr:
                    self._release_pin("跨屏输入 · 已恢复跟随")
                else:
                    geo = scr.availableGeometry()
                    self.move(max(geo.left(), min(px, geo.right() - self.width())),
                              max(geo.top(), min(py, geo.bottom() - self.height())))
                    return

        w, h = self.width(), self.height()
        nx, ny = anchor.x() + 2, anchor.y() + 20
        scr = (self._screen_for_point(anchor.x(), anchor.y())
               or self._screen_for_point(nx, ny)
               or self._nearest_screen(anchor))
        if scr is None:
            # 连一块屏都枚举不到（会话异常）：原样放置，避免崩
            self.move(int(nx), int(ny))
            return
        geo = scr.availableGeometry()
        if nx + w > geo.right():
            nx = max(geo.left() + 6, geo.right() - w - 6)
        if ny + h > geo.bottom():
            ny = max(geo.top() + 6, anchor.y() - h - 14)
        self.move(int(nx), int(ny))

    # ---------------- 固定态管理（v3.0.2） ----------------
    def _release_pin(self, hint: str = "") -> None:
        """解除固定并落盘。hint 走顶行小字，绝不切到提示态（会吞掉候选行）。"""
        self.bar_pinned = False
        self._bar_pos = []
        self.refresh_icons()
        self._persist_drag()
        if hint:
            self._set_mode_hint(hint)
            QTimer.singleShot(2200, lambda: self._set_mode_hint(""))

    def release_pin_if_other_screen(self, pt) -> bool:
        """固定点在别的屏 → 解除固定并返回 True（调用方继续跟随）。

        同一块屏内返回 False：固定是用户主动要的，不许乱解。
        """
        if not self.bar_pinned or len(self._bar_pos) != 2:
            return True
        try:
            px, py = int(self._bar_pos[0]), int(self._bar_pos[1])
        except (TypeError, ValueError):
            self._release_pin("位置配置无效 · 已恢复跟随")
            return True
        pin = self._screen_for_point(px + 4, py + 4)
        cur = self._screen_for_point(int(pt.x()), int(pt.y()))
        if pin is None or (cur is not None and cur is not pin):
            self._release_pin("跨屏点击 · 已恢复跟随")
            return True
        return False

    def sanitize_pinned(self) -> None:
        """启动体检：固定点必须落在某块屏内，否则回到跟随。

        旧版（v2.5 及更早）"拖动即固定"会写进 `ui.bar_pinned/bar_pos`；
        用户换屏或改分辨率后这个点可能已经在屏幕外，表现为"条子不见了"或
        "怎么点都不跟随"。启动时就纠正，不等用户触发一次跟随才修。
        """
        if not self.bar_pinned:
            return
        if len(self._bar_pos) != 2:
            self._release_pin()
            return
        try:
            px, py = int(self._bar_pos[0]), int(self._bar_pos[1])
        except (TypeError, ValueError):
            self._release_pin()
            return
        if self._screen_for_point(px + 4, py + 4) is None:
            self._release_pin()

    # ---------------- 拖动（鼠标事件直移窗口，绝不 activateWindow） ----------------
    def wheelEvent(self, e) -> None:  # noqa: N802
        """滚轮翻页（v2.6.1）：鼠标用户最自然的方式，不用记快捷键。"""
        delta = e.angleDelta().y()
        if not delta:
            return
        if self.page_move(-1 if delta > 0 else 1):
            e.accept()
        else:
            e.ignore()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            p = e.globalPosition().toPoint()
            self._drag_start = p
            self._drag_origin = p - self.pos()
            self._drag_moved = False

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if (self._drag_origin is None
                or not (e.buttons() & Qt.MouseButton.LeftButton)):
            return
        cur = e.globalPosition().toPoint()
        if not self._drag_moved:
            # v2.6：手抖 1px 不能算拖动。以前只要动一下就"钉住"，
            # 之后候选条再也不跟随光标——用户会以为定位坏了。
            if (cur - self._drag_start).manhattanLength() < DRAG_MIN_PX:
                return
            self._drag_moved = True
        self.move(cur - self._drag_origin)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._drag_origin is not None:
            if self._drag_moved:
                # 拖动即固定（老行为），但走统一入口以同步图标状态
                self.bar_pinned = True
                self._bar_pos = [self.pos().x(), self.pos().y()]
                self._persist_drag()
                self.refresh_icons()
                # v3.0.2：固定是被动进入的状态（用户只是想把条子挪开），
                # 必须让他知道"现在不跟光标了"，否则下一次换输入位置就会
                # 认为是"跟随坏了"。顶行小字，不切提示态、不吞候选。
                self._set_mode_hint("已固定 · 点 📌 恢复跟随")
                QTimer.singleShot(2600, lambda: self._set_mode_hint(""))
            self._drag_origin = None
            self._drag_moved = False

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            # 双击顶行空白 = 恢复跟随（保留老手势，主入口是 📌 按钮）
            tl = self.top_row.mapTo(self, QPoint(0, 0))
            if QRect(tl, self.top_row.size()).contains(
                    e.position().toPoint()):
                self.set_pinned(False)

    def _on_pin_clicked(self) -> None:
        self.set_pinned(not self.bar_pinned)

    def set_pinned(self, on: bool) -> None:
        """固定/跟随切换（显式按钮 + 拖动自动固定 + 双击解除，三处共用）。"""
        on = bool(on)
        if on:
            self.bar_pinned = True
            if self.isVisible():
                self._bar_pos = [self.x(), self.y()]
            elif len(self._bar_pos) != 2:
                self._bar_pos = [self.x(), self.y()]
        else:
            self.bar_pinned = False
            self._bar_pos = []
        self._persist_drag()
        self.refresh_icons()
        if not on:
            self.follow_caret()
        # 只用顶行小字反馈：show_hint 会把 mode 切成 HINT、胶囊整排隐藏，
        # 点一下 📌 候选就"消失"了（QA 抓到）。
        if self.isVisible() and self.mode == MODE_INPUT:
            self._set_mode_hint("已固定 · 不跟随" if on else "跟随光标")
            self.mode_hint.show()

    def _persist_drag(self) -> None:
        """拖动位置持久化（v2.2：拖动即钉住，自动持久化，全局一份）。

        `ui.remember_bar_pos=False` 时只改内存、不落盘（QA 指出这个配置
        以前是死的，从没被读过）。
        """
        self.config.ui.bar_pinned = self.bar_pinned
        self.config.ui.bar_pos = list(self._bar_pos) if self.bar_pinned else []
        if not bool(getattr(self.config.ui, "remember_bar_pos", True)):
            return
        if callable(self.ui_persist):
            self.ui_persist("ui", {
                "bar_pinned": self.bar_pinned,
                "bar_pos": list(self._bar_pos) if self.bar_pinned else [],
            })

    # ---------------- 生命周期 ----------------
    def prime_window(self) -> None:
        """先映射一次拿到 HWND 并打上 WS_EX_NOACTIVATE，再隐藏。"""
        self.setWindowOpacity(0.0)
        self.show()
        make_no_activate(self)
        self._noact_applied = True
        self.hide()
        self.setWindowOpacity(DS.OPACITY)

    def show_bar(self, focus: bool = False) -> None:
        if not self._noact_applied:
            self.show()
            make_no_activate(self)
            self._noact_applied = True
        self.follow_caret()
        self.show()
        self._animate_in()
        if focus:
            self.raise_()

    def close_panel(self) -> None:
        self._hide_timer.stop()
        self.hover.cancel()
        if self.mode == MODE_SUGGEST and self.ime is not None:
            self.ime.exit_suggest()
        self.sug_host.hide()
        self.hide()
        self.closed.emit()

    def _animate_in(self) -> None:
        self.setWindowOpacity(0.0)
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(120)          # I03：100→120ms 更柔和
        a.setStartValue(0.0)
        a.setEndValue(DS.OPACITY)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.start()
        self._anim = a
        pos = self.pos()
        b = QPropertyAnimation(self, b"pos", self)
        b.setDuration(120)
        b.setStartValue(QPoint(pos.x(), pos.y() + 5))
        b.setEndValue(pos)
        b.setEasingCurve(QEasingCurve.Type.OutCubic)
        b.start()
        self._anim_pos = b

    # ---------------- 四态 ----------------
    def show_input(self, pinyin: str) -> None:
        self.collapsed = False       # 一旦开始打字，收起状态自动解除
        self._hide_timer.stop()
        if self.mode == MODE_SUGGEST and self.ime is not None:
            self.ime.exit_suggest()
        self.mode = MODE_INPUT
        self.sug_host.hide()
        self.lead_icon.hide()
        self.refresh_icons()
        self._set_lead(pinyin or "")
        self._set_mode_hint("")
        self.top_row.show()
        self.tail.show()
        if not self.isVisible():
            self.show_bar()
        else:
            self._relayout()
            self.follow_caret()
        self._elide_later()

    def refresh_candidates(self, query: str) -> None:
        self.hover.cancel()
        self._last_query = query or ""
        self.candidates = self.engine.search(query, self.scene,
                                             mode=self.candidate_mode)
        # M3：判定本次是否有「精确命中」。segment（整句完整覆盖）也算精确，
        # 因为整串拼音被逐音节吃干净了，不存在"半截输入"。
        # v2.5：**common（通用词库全拼精确）必须算精确** —— 否则日常打
        # yanshou/今天 这类普通词按空格会吐出拼音（专业词库精确才算，
        # 通用层不算的话，等于把日常输入法最常用的一条路堵死）。
        # 注意 en（英文反查）故意不算：打英文单词时按空格应该直出英文原文。
        self._exact_match = any(
            c.match_type in ("full_pinyin", "abbr", "segment", "phrase",
                             "common")
            for c in self.candidates)
        self.active_index = 0
        # 「译」候选（UX-P1-3，主理人裁决①口径）：query 含中文且词库无命中
        # 时才探测；拼音 query 不做拼音→中文还原。
        self._translate_offer = None
        if not self.candidates and self._has_cjk(self._last_query):
            self._translate_offer = self._probe_translate(self._last_query)
        if self.mode == MODE_INPUT:
            self._relayout()

    # ---------------- 「译」候选（UX-P1-3） ----------------
    @staticmethod
    def _has_cjk(text: str) -> bool:
        return any("一" <= ch <= "鿿" for ch in (text or ""))

    def _probe_translate(self, query: str) -> dict | None:
        """同步探测译法来源：AI 开 → llm 路径；AI 关 → probe_local 只读预检。
        两者都无 → None（候选不出现，modeHint 提示「无译法 · 可开 AI」）。
        """
        tr = self.translator
        if tr is None:
            return None
        try:
            llm = getattr(tr, "llm", None)
            if (llm is not None and getattr(llm, "enabled", False)
                    and getattr(llm, "available", False)):
                return {"src": query, "text": "", "via": "llm"}
            text = tr.probe_local(query, self.scene)
            if text:
                return {"src": query, "text": text, "via": "local"}
        except Exception:
            pass
        return None

    def _commit_translate_offer(self) -> None:
        """点击「译」候选：本地路径同步上屏；AI 路径异步翻译后上屏。"""
        offer, self._translate_offer = self._translate_offer, None
        if offer is None:
            return
        if offer["via"] == "local" and offer["text"]:
            self.committed.emit(offer["text"], "en")
            self._emit_text(offer["text"])
            self.close_panel()
            return
        tr = self.translator
        if tr is None:
            self.close_panel()
            return
        self.show_hint("正在翻译…", "ai")

        def _done(result) -> None:
            # 回调发生在子线程，入队由主线程定时器消费
            self._offer_results.put(result)

        local = tr.translate_async(offer["src"], self.scene, _done)
        if local is not None:
            self._offer_results.put(local)

    def _drain_offer_results(self) -> None:
        """主线程消费 AI「译」结果：上屏 + 记忆库学习（store_llm_draft 沿用）。"""
        while True:
            try:
                result = self._offer_results.get_nowait()
            except queue.Empty:
                return
            if getattr(result, "ok", False):
                self._emit_text(result.text)
                if result.source == "llm":
                    try:
                        assist = self.config.assist
                        if getattr(assist, "store_llm_draft", True):
                            self.translator.memory.learn(
                                result.src, result.text,
                                self.scene.profession_key, "llm")
                    except Exception:
                        pass
                self.show_result(result)
            else:
                self.show_hint(getattr(result, "error", "") or "翻译失败",
                               "err")

    def show_result(self, result) -> None:
        """结果态：显示译文的来源与内容，数秒后自动淡出。

        v2.7：与 show_hint 同样尊重"收起"——异步翻译 1~2s 后返回时，
        用户可能已经点了 ✕，此时不能把条子自己弹回来（QA 复检）。
        """
        if getattr(self, "collapsed", False):
            return
        self.mode = MODE_RESULT
        ok = getattr(result, "ok", False)
        src = getattr(result, "source", "")
        self.en_col.set_candidate(None)
        self.chips_host.hide()
        self.sug_host.hide()
        self.lead_icon.show()
        self.lead_icon.set_icon("ai" if src == "llm" else "check")
        self.lead_icon.set_color(DS.AI if src == "llm" else (DS.OK if ok else DS.ERR))
        self._set_lead(getattr(result, "source_label", "") or "翻译")
        self._set_mode_hint("")
        tag = ""
        unresolved = getattr(result, "unresolved", None) or []
        if getattr(result, "fixes", None):
            tag = " · 术语已校正"
        elif unresolved:
            # 英→中方向不做硬替换（会写出病句），只如实提示"有几个术语
            # 没用标准中文名"，点开 tooltip 看具体是哪几个。
            tag = f" · 术语待核 {len(unresolved)}"
        elif src == "memory_fuzzy" and getattr(result, "confidence", 0):
            tag = f" · {int(result.confidence * 100)}%"
        text = getattr(result, "text", "") or getattr(result, "error", "")
        self._result_text = text if ok else ""
        self.preview.setToolTip(
            "单击复制译文" + ("\n术语待核：\n· " + "\n· ".join(unresolved)
                            if unresolved else ""))
        self.preview.show()
        self._set_preview(text + tag, DS.TEXT if ok else DS.ERR)
        self.tail.setText("")
        self.setFixedWidth(DS.BAR_MAX_W)
        self.setFixedHeight(DS.BAR_H + 8)
        self.show_bar()
        self._elide_later()
        # 译文越长留得越久（4.2s 读不完一句 80 词的英文），上限 14s；
        # 鼠标点一下可复制，不必抢时间。
        base_ms = int(getattr(self.config.assist, "show_result_ms",
                              RESULT_MS) or RESULT_MS)
        self._hide_timer.start(min(14000, max(base_ms, 80 * len(text))))

    def show_hint(self, text: str, kind: str = "ok") -> None:
        """提示态：一句话反馈，自动淡出。kind: ok / warn / err / ai / memory。

        v2.7：用户点了 ✕ 收起（collapsed=True）之后，任何后台提示都不得把
        候选条弹回来——否则 U02「收起到托盘」会被翻译失败/开关提示击穿。
        """
        if getattr(self, "collapsed", False):
            return
        self.mode = MODE_HINT
        ic, col = {
            "ok": ("check", DS.OK), "warn": ("scene", DS.WARN),
            "err": ("close", DS.ERR), "ai": ("ai", DS.AI),
            "memory": ("layers", DS.OK),
        }.get(kind, ("check", DS.OK))
        self.en_col.set_candidate(None)
        self.chips_host.hide()
        self.sug_host.hide()
        self.lead_icon.show()
        self.lead_icon.set_icon(ic)
        self.lead_icon.set_color(col)
        self._set_lead("")
        self._set_mode_hint("")
        self.preview.show()
        self._set_preview(text, col)
        self.tail.setText("")
        self.setFixedWidth(min(DS.BAR_MAX_W, 150 + 8 * min(len(text), 46)))
        self.setFixedHeight(DS.BAR_H + 8)
        self.show_bar()
        self._elide_later()
        self._hide_timer.start(HINT_MS)

    # ---------------- 交互 ----------------
    def _on_chip_picked(self, index: int) -> None:
        """点击中文胶囊 = 该候选中文上屏；无候选时唯一的紫胶囊 = 「译」。"""
        if not self.candidates and self._translate_offer is not None:
            self._commit_translate_offer()
            return
        self._set_active(index)
        self.chip_picked.emit(index)
        self.commit_index(index)

    def _on_chip_hovered(self, index: int) -> None:
        """悬停 400ms 出说明卡（译法 + 标准号 + 推荐理由）。"""
        if self.mode != MODE_INPUT or not (0 <= index < len(self.candidates)):
            return
        # 分页后控件位置 != 候选下标，按 index 反查真正的胶囊
        chip = next((c for c in self.chips
                     if c.isVisible() and not c.is_badge and c.index == index),
                    None)
        if chip is None:
            return
        anchor = QRect(chip.mapToGlobal(QPoint(0, 0)), chip.size())
        c = self.candidates[index]
        self.hover.schedule(400, c, anchor,
                            self.engine.usage.count_of(c.zh))

    def _on_chip_unhovered(self) -> None:
        self.hover.cancel()

    # ---------------- 场景 / 身份 ----------------
    def apply_scope(self) -> None:
        h = (self.config.profile.get("hierarchy") or {})
        self.scene.industry_key = h.get("industry", "")
        self.scene.occupation_key = h.get("occupation", "")
        self.scene.position_key = h.get("position", "")
        try:
            from core.hierarchy import describe_chain, resolve_focus_tags
            tags, boost = resolve_focus_tags(
                self.scene.industry_key, self.scene.occupation_key,
                self.scene.position_key)
            self.scene.focus_tags = tags
            self.scene.position_boost = boost
            self.scene.hierarchy = describe_chain(
                self.scene.industry_key, self.scene.occupation_key,
                self.scene.position_key)
            if self.scene.industry_key:
                self.scene.profession_key = self.scene.industry_key
        except Exception:
            pass

    def refresh_scene(self) -> None:
        self.scene = self.detector.detect()
        self.apply_scope()

    def set_scene(self, scene: SceneContext) -> None:
        self.scene = scene

    def reload_hierarchy(self) -> None:
        self.config = get_config(reload=True)
        self._apply_style()
        self.refresh_scene()
        if self.mode == MODE_INPUT and self.isVisible():
            self._relayout()

    def toggle_branch_view(self) -> None:
        self.toggle_branch.emit()

    # ---------------- 与输入内核对接 ----------------
    def attach_ime(self, controller) -> None:
        self.ime = controller
        self._update_state_label()
        self._ime_poll = QTimer(self)
        self._ime_poll.setInterval(25)       # 25ms：够跟手，又不空转
        self._ime_poll.timeout.connect(self.on_ime_events)
        self._ime_poll.start()

    def _emit_text(self, text: str) -> bool:
        if not text:
            return False
        if self.ime is not None:
            return self.ime.send(text)
        return self.inserter.insert(text)

    def _chosen_text(self, pinyin: str) -> str:
        """组合态"该上屏什么"：精确命中取当前高亮候选，否则用拼音原文。

        与空格/回车的口径完全一致（M3）：只有精确命中才替用户选词，
        前缀命中时直出原文，绝不假装用户已经打完了。
        """
        p = (pinyin or "").strip()
        if not p:
            return ""
        cands = self.candidates
        exact = self._exact_match
        if not cands:
            # v4.0：候选条可能刚被 Esc/上屏关掉，或 25ms 定时器还没跑完 ——
            # 此时按 ← 不能因为"内存里没有候选"就把拼音原文吐进文档。
            # 现场再算一次（0.2ms 级），拿不到才退回原文。
            try:
                cands = self.engine.search(
                    p, self.scene,
                    mode=getattr(self, "candidate_mode", "auto"))
                exact = any(c.match_type in ("full_pinyin", "abbr", "segment",
                                             "phrase", "common")
                            for c in cands)
            except Exception:
                cands, exact = [], False
        if cands and exact:
            i = self.active_index if 0 <= self.active_index < len(cands) else 0
            return cands[i].zh or p
        return p

    def _commit_nav(self, pinyin: str, vk: int) -> None:
        """组合态按编辑键：先上屏当前内容，再把该键交给目标程序。

        v3.0 修：以前 ←/→/Home/End/Delete 会落到"其它标点"分支，
        直接把拼音原文吐进文档。现在顺序是「文字 → 按键」，两条都走
        SendInput，同一线程内保序；物理键已被内核吞掉，不会重复执行。
        """
        text = self._chosen_text(pinyin)
        if text:
            self._emit_text(text)
        self.candidates = []
        self.close_panel()
        if vk:
            from core.ime import send_vk
            send_vk(vk, False)
            send_vk(vk, True)

    def _commit_char(self, pinyin: str, ch: str) -> None:
        """组合态按大写字母：先上屏当前词，再打这个大写字母。"""
        text = self._chosen_text(pinyin)
        if text:
            self._emit_text(text)
        if ch:
            self._emit_text(ch)
        self.candidates = []
        self.close_panel()

    def on_ime_events(self) -> None:
        if self.ime is None:
            return
        for ev in self.ime.drain():
            k, d = ev.kind, ev.data
            if k == "update":
                self.show_input(d.get("pinyin", ""))
                self.refresh_candidates(d.get("pinyin", ""))
            elif k == "cancel":
                self.candidates = []
                self.close_panel()
            elif k == "commit":
                idx = int(d.get("index", -1))
                english = bool(d.get("english"))
                raw = d.get("raw", "")
                if idx >= 0 and self.candidates:
                    if self.commit_digit(idx, english, raw) < 0:
                        # 本页没有这个序号：收条，绝不替用户选看不见的词
                        self.candidates = []
                        self.close_panel()
                else:
                    self.commit_index(idx, english, raw)
            elif k == "commit_combo":
                self.commit_combo(d.get("index", 0))
            elif k == "punct":
                # DY01 中文标点：非组合态把关 8 个常用标点转成全角后上屏。
                # 走同一条 SendInput 通道，不弹候选条、不打断当前输入。
                self._emit_text(d.get("text", ""))
            elif k == "commit_raw":
                # suffix：组合态按标点时，同一条事件里带上全角标点，
                # 保证顺序是"词 + 标点"而不是被物理键抢先。
                raw = d.get("pinyin", "") + d.get("suffix", "")
                if raw:
                    self._emit_text(raw)
                self.candidates = []
                self.close_panel()
            elif k == "commit_nav":
                # 组合态按 ←/→/Home/End/Delete：先上屏再执行该键（v3.0）
                self._commit_nav(d.get("pinyin", ""), int(d.get("vk", 0)))
            elif k == "commit_char":
                # 组合态按大写字母：先上屏当前词，再打大写（v3.0）
                self._commit_char(d.get("pinyin", ""), d.get("ch", ""))
            elif k == "toggle":
                self._update_state_label(bool(d.get("enabled", True)))
            elif k == "nav":
                self.move_active(d.get("delta", 1))
            elif k == "page":
                self.page_move(d.get("delta", 1))
            elif k == "suggest_pick":
                self.commit_suggestion(d.get("index", -1))
            elif k == "suggest_close":
                if self.mode == MODE_SUGGEST:
                    self.close_panel()
            elif k == "cad":
                # M2：CAD 里临时激活只亮一条短提示，避免用户不知道
                # 「刚才按的 Ctrl+Space 到底生效了没有」。
                if d.get("temp"):
                    self.show_hint("CAD 临时输入 · 空闲数秒自动回落", "ok")
            elif k == "hotkey":
                if callable(self.hotkey_handler):
                    self.hotkey_handler(d.get("name", ""))

    # ---------------- 候选操作 ----------------
    def move_active(self, delta: int) -> None:
        if not self.candidates:
            return
        self._set_active((self.active_index + delta) % len(self.candidates),
                         follow=True)

    def page_move(self, delta: int) -> bool:
        """翻页。返回是否真的移动了（滚轮据此决定是否 accept）。"""
        if not self.candidates:
            return False
        if not self._pages:
            self._pages = self._compute_pages(CHIPS_AVAIL_W)
        n = len(self._pages)
        if n <= 1:
            before = self.active_index
            self._set_active(self.active_index + delta * max(self._shown, 1))
            return self.active_index != before
        cur = self._page_of(self.active_index)
        tgt = max(0, min(cur + delta, n - 1))
        moved = tgt != cur
        self._set_active(self._pages[tgt][0], follow=True)
        return moved

    def commit_digit(self, digit_index: int, english: bool = False,
                     raw: str = "") -> int:
        """数字键选词：数字对应**本页可见**的第 N 枚，不是绝对下标。

        M1 分页后 1..9 必须跟着当前页走，否则第二页上按 1 会选中一条
        用户看不见的候选。超出本页范围 → 返回 -1（调用方放弃，不硬凑）。
        """
        start, cnt = (self._pages[self._page_idx]
                      if self._pages else (0, self._shown))
        if not (0 <= digit_index < cnt):
            return -1
        idx = start + digit_index
        if idx >= len(self.candidates):
            return -1
        self.commit_index(idx, english, raw)
        return idx

    def commit_index(self, idx: int, english: bool = False,
                     raw: str = "") -> None:
        # 一个候选都没有时按空格/回车 → 直出拼音原文。
        if not self.candidates:
            if raw:
                self._emit_text(raw)
            self.close_panel()
            return
        # M3（S1-B 非精确不选词）：空格/回车只在「精确命中」（全拼/简拼/
        # 整串完整覆盖）时才取首候选。只有前缀命中时替用户选一个词，等于
        # 替他决定了他还没打完的输入 —— 直出原文更诚实。
        # 数字键不走这里（明确选词，不受影响）；Tab 上英文也不受此限。
        if idx < 0 and raw and not self._exact_match:
            self._emit_text(raw)
            self.candidates = []
            self.close_panel()
            return
        i = self.active_index if idx < 0 else idx
        if i < 0 or i >= len(self.candidates):
            i = 0
        c = self.candidates[i]
        text = (c.en or c.zh) if english else c.zh
        if not text:
            self.close_panel()
            return
        if english and c.term.standard:
            text = f"{c.en} ({c.term.standard})"
        self.engine.commit(c.zh, self.scene)
        self.committed.emit(text, "en" if english else "zh")
        self._emit_text(text)
        zh = c.zh
        self.candidates = []
        self.close_panel()
        self._after_commit(zh)

    def commit_en(self, variant_idx: int = 0) -> None:
        """点击译法行 = 该译法英文上屏（不含标准号）。"""
        if not self.candidates or not (0 <= self.active_index
                                       < len(self.candidates)):
            return
        c = self.candidates[self.active_index]
        if variant_idx == 0:
            text = c.en or ""
        else:
            variants = [v for v in (c.term.var_en or [])
                        if v and v.strip()
                        and v.strip().lower() != (c.en or "").lower()]
            text = (variants[variant_idx - 1].strip()
                    if 0 < variant_idx <= len(variants) else "")
        if not text:
            return
        self.engine.commit(c.zh, self.scene)
        self.committed.emit(text, "en")
        self._emit_text(text)
        zh = c.zh
        self.candidates = []
        self.close_panel()
        self._after_commit(zh)

    def _after_commit(self, zh: str) -> None:
        """上屏成功后的联想钩子（UX-P1-2）：有联想词则进入 MODE_SUGGEST。"""
        try:
            words = self.assoc.suggest(zh, self.scene)
        except Exception:
            words = []
        if words:
            self.show_suggestions(words, zh)

    # ---------------- 联想态（MODE_SUGGEST） ----------------
    def show_suggestions(self, words: list[str], zh: str = "") -> None:
        """联想态：`沉孔 ✓ 联想→ [通孔] [埋头孔]`，3 秒自动收起。

        words 必须已过 AssociationProvider 白名单（词库真实存在）。
        """
        words = [w for w in (words or []) if w][:MAX_SUGGESTIONS]
        if not words:
            return
        self._hide_timer.stop()
        self.hover.cancel()
        self.mode = MODE_SUGGEST
        self._sug_words = list(words)
        self._sug_source = zh or ""
        self.candidates = []
        self.active_index = 0
        self._translate_offer = None
        self.en_col.set_candidate(None)
        self.chips_host.hide()
        self.preview.hide()
        self.lead_icon.show()
        self.lead_icon.set_icon("check")
        self.lead_icon.set_color(DS.OK)
        self._set_lead(f"{self._sug_source} ✓ 联想→"
                       if self._sug_source else "联想→")
        self._set_mode_hint("")
        self.tail.setText("")
        for i, c in enumerate(self.sug_chips):
            if i < len(words):
                c.set_content(i, words[i])
                c.set_selected(False)
                c.show()
            else:
                c.hide()
        self.sug_host.show()
        self._fit_geometry()
        self.show_bar()
        # 内核进入联想态：数字键/Esc 归联想槽位消费
        if self.ime is not None:
            self.ime.enter_suggest(len(words))
        self._hide_timer.start(SUGGEST_MS)

    def commit_suggestion(self, index: int) -> None:
        """点击/数字键选中联想词：走 _emit_text + engine.commit（与正常
        候选上屏完全同路，数据同源可查）。"""
        if self.mode != MODE_SUGGEST or not (
                0 <= index < len(self._sug_words)):
            return
        w = self._sug_words[index]
        self.engine.commit(w, self.scene)
        self.committed.emit(w, "zh")
        self._emit_text(w)
        self._sug_words = []
        self.close_panel()

    def commit_combo(self, idx: int) -> None:
        if idx >= len(self.candidates):
            return
        t = self.candidates[idx].term
        parts = [t.zh]
        if t.en:
            parts.append(f"{t.en} ({t.standard})" if t.standard else t.en)
        text = " ".join(parts)
        self.engine.commit(t.zh, self.scene)
        self.committed.emit(text, "combo")
        self._emit_text(text)
        self.candidates = []
        self.close_panel()

    # 旧接口别名，便于平滑替换
    show_panel = show_bar
    show_composition = show_input
    select_index = commit_index
    apply_hierarchy = apply_scope
    _move_active = move_active
    _page = page_move


if __name__ == "__main__":
    from core.engine import TermEngine
    from scene.detector import SceneDetector
    from ui.inserter import Inserter

    app = QApplication(sys.argv)
    bar = CandidateBar(TermEngine(), SceneDetector(), Inserter())
    bar.show_input("chenkong")
    bar.refresh_candidates("chenkong")
    sys.exit(app.exec())
