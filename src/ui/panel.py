"""
智语输入法 — 悬浮候选窗（紧凑重设计版）

不重复造轮子：
- 窗口/布局/样式：PySide6 现成控件体系
- 无边框 + 置顶 + 半透明：Qt 原生 WindowFlags 与属性
- 矢量图标：ui/icons.py（QPainter 路径绘制，非字符）
- 入场动画：QPropertyAnimation 原生能力

布局（自上而下，全部为 1px 分隔线，无多余留白）：
┌──────────────────────────────────────────────────────┐
│ [瞄准] SolidWorks · 工程图        [场景标签]     26px │  ← 顶栏
├──────────────────────────────────────────────────────┤
│ [键盘] chenkong                       3 项    [×] 36px │  ← 输入行
├──────────────────────────────────────────────────────┤
│ 1  沉孔           Counterbore     工程图孔标注       38px │  ← 候选行
│ 2  埋头孔         Countersink     工程图孔标注           │
│ 3  螺纹孔         Tapped Hole     异型孔向导             │
├──────────────────────────────────────────────────────┤
│ [人像]机械工程›结构设计›设计员  [列表]候选 [星]推荐  28px │  ← 底栏
└──────────────────────────────────────────────────────┘

重设计要点（对比旧版）：
- 宽度 720 → 560，行高 52 → 38，可见行 6 → 5：整体面积压缩约 55%
- 删除独立的身份面包屑条与提示条，身份入口下沉到底栏，与功能按钮同排
- 简述列由固定 218px 改为弹性，避免宽度浪费
- 全部字符图标（⌨ ⚙ ⚡ ↩ ›）替换为功能语义矢量图标
- 统一设计变量：圆角 10、边距 10/8、字号阶梯 15/13/12/11
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, Signal, QPoint, QSize
)
from PySide6.QtGui import QFont, QColor, QKeyEvent, QCursor, QIcon, QPainter, QBrush
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QFrame, QSizePolicy, QGraphicsDropShadowEffect,
    QPushButton
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import get_config
from core.engine import TermEngine
from core.hierarchy import resolve_focus_tags, describe_chain
from core.ime import CaretInfo, get_caret_info
from core.models import Candidate, SceneContext, scene_label
from core.recommender import SmartRecommender
from scene.detector import SceneDetector, get_cursor_pos
from ui.icons import IconWidget, make_icon
from ui.inserter import Inserter

# 候选窗必须以「不激活」方式存在：一旦抢走焦点，SendInput 就会把字打到
# 输入法自己身上，目标编辑器反而收不到 —— 这正是剪贴板粘贴方案绕不过去的坑。
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20


def make_no_activate(widget) -> bool:
    """在 Win32 层再加一道 WS_EX_NOACTIVATE，双保险。"""
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


# ============================================================
# 统一设计变量：全部来自 ui/design.py（全应用单一视觉来源）
# ============================================================
from ui.design import DS                                  # noqa: E402


# ============================================================
# 行控件
# ============================================================
class CandidateRow(QWidget):
    """候选行：序号 | 中文 | 英文 | 简述 | 标记。"""

    def __init__(self, candidate: Candidate, index: int, parent=None):
        super().__init__(parent)
        self.candidate = candidate
        self._desc_full = candidate.term.brief or candidate.term.scene or ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(DS.PAD_X, 0, DS.PAD_X, 0)
        lay.setSpacing(8)

        # 序号
        n = str(index + 1) if index < 9 else "·"
        self.idx_label = QLabel(n)
        self.idx_label.setObjectName("idx")
        self.idx_label.setFixedWidth(12)
        self.idx_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 中文
        self.zh_label = QLabel(candidate.zh)
        self.zh_label.setObjectName("zh")
        self.zh_label.setFixedWidth(96)

        # 英文
        self.en_label = QLabel(candidate.en or "—")
        self.en_label.setObjectName("en")
        self.en_label.setFixedWidth(150)
        self.en_label.setToolTip(candidate.en or "")

        # 简述（弹性）
        self.desc_label = QLabel()
        self.desc_label.setObjectName("desc")
        self.desc_label.setToolTip(self._desc_full)
        self.desc_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        # 标记（右侧，固定小宽度）
        self.why_label = QLabel(candidate.why())
        self.why_label.setObjectName("why")
        self.why_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.why_label.setMinimumWidth(38)
        self.why_label.setToolTip(candidate.why())

        lay.addWidget(self.idx_label)
        lay.addWidget(self.zh_label)
        lay.addWidget(self.en_label)
        lay.addWidget(self.desc_label, 1)
        lay.addWidget(self.why_label)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._desc_full:
            return
        avail = max(self.desc_label.width() - 4, 30)
        fm = self.desc_label.fontMetrics()
        self.desc_label.setText(
            fm.elidedText(self._desc_full, Qt.TextElideMode.ElideRight, avail)
        )


class SuggestionRow(QWidget):
    """智能推荐行：来源图标 | 中文 | 英文 | 推荐原因。"""

    def __init__(self, s, index: int, parent=None):
        super().__init__(parent)
        self.suggestion = s

        lay = QHBoxLayout(self)
        lay.setContentsMargins(DS.PAD_X, 0, DS.PAD_X, 0)
        lay.setSpacing(8)

        # 来源图标：AI 用芯片星，本地推荐用四角星
        is_llm = getattr(s, "source", "") == "llm"
        self.tag = IconWidget("ai" if is_llm else "suggest", 13,
                              DS.ACCENT_SUG)
        self.tag.setFixedWidth(14)

        zh = QLabel(s.term.zh)
        zh.setObjectName("sugZh")
        zh.setFixedWidth(96)

        en = QLabel(s.term.en or "—")
        en.setObjectName("sugEn")
        en.setFixedWidth(150)
        en.setToolTip(s.term.en or "")

        reason = QLabel(s.reason)
        reason.setObjectName("sugReason")
        reason.setToolTip(s.reason)
        reason.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)

        lay.addWidget(self.tag)
        lay.addWidget(zh)
        lay.addWidget(en)
        lay.addWidget(reason, 1)


# ============================================================
# 主面板
# ============================================================
class InputPanel(QWidget):
    """悬浮候选主面板。"""

    committed = Signal(str, str)   # (文本, 类型: zh/en/combo)
    closed = Signal()
    open_settings = Signal()
    toggle_branch = Signal()       # 切换：词库候选 / 智能推荐

    def __init__(self, engine: TermEngine, detector: SceneDetector,
                 inserter: Inserter):
        super().__init__()
        self.config = get_config()
        self.engine = engine
        self.detector = detector
        self.inserter = inserter
        self.recommender = SmartRecommender(
            engine=engine, lexicon_manager=engine.lexicons)
        self.candidates: list[Candidate] = []
        self.suggestions: list = []
        self.active_index = 0
        self.accepted: list[str] = []
        self.branch = "terms"
        self.ime = None                     # attach_ime() 注入输入内核
        self._ime_poll = None
        self.scene: SceneContext = self.detector.detect(force=True)

        self._setup_window()
        self._build_ui()
        self._apply_style()

        self.apply_hierarchy(initial=True)
        self.refresh_scene()
        self.refresh_candidates("")

    # ---------------- 窗口 ----------------
    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus     # 关键：绝不抢焦点
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(DS.WIDTH)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.container = QFrame()
        self.container.setObjectName("container")
        root.addWidget(self.container)

        box = QVBoxLayout(self.container)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        # ===== 顶栏：场景 =====
        self.scene_bar = QFrame()
        self.scene_bar.setObjectName("sceneBar")
        self.scene_bar.setFixedHeight(DS.TOP_H)
        sb = QHBoxLayout(self.scene_bar)
        sb.setContentsMargins(DS.PAD_X, 0, DS.PAD_X, 0)
        sb.setSpacing(6)

        self.scene_icon = IconWidget("scene", 12, DS.PRIMARY)
        # 顶栏点击可手动锁定/解锁场景
        self.scene_label = QLabel()
        self.scene_label.setObjectName("sceneLabel")

        sb.addWidget(self.scene_icon)
        sb.addWidget(self.scene_label)
        sb.addStretch(1)

        self.tag_row = QHBoxLayout()
        self.tag_row.setSpacing(4)
        sb.addLayout(self.tag_row)
        box.addWidget(self.scene_bar)

        # ===== 输入行 =====
        input_wrap = QFrame()
        input_wrap.setObjectName("inputWrap")
        input_wrap.setFixedHeight(DS.INPUT_H)
        iw = QHBoxLayout(input_wrap)
        iw.setContentsMargins(DS.PAD_X, 0, DS.PAD_X, 0)
        iw.setSpacing(7)

        self.prefix = IconWidget("keyboard", 14, DS.TEXT_MUTED)
        # 只读：键盘输入由全局钩子接管，这里只做拼音串的可视回显
        self.input = QLineEdit()
        self.input.setPlaceholderText("直接敲拼音，无需点选本窗口")
        self.input.setObjectName("inputBox")
        self.input.setReadOnly(True)
        self.input.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.input.textChanged.connect(self.on_text_changed)

        self.count_label = QLabel("")
        self.count_label.setObjectName("countLabel")
        self.count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.btn_close = IconWidget("close", 12, DS.TEXT_FAINT)
        self.btn_close.setFixedSize(18, 18)
        self.btn_close.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.mousePressEvent = (          # type: ignore[method-assign]
            lambda e: self.close_panel())

        iw.addWidget(self.prefix)
        iw.addWidget(self.input, 1)
        iw.addWidget(self.count_label)
        iw.addWidget(self.btn_close)
        box.addWidget(input_wrap)

        # ===== 候选列表 =====
        self.list = QListWidget()
        self.list.setObjectName("candList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setFixedHeight(DS.ROW_H * DS.VISIBLE_ROWS + 2)
        self.list.itemClicked.connect(self._on_item_clicked)
        box.addWidget(self.list)

        # ===== 推荐列表（与候选列表互斥）=====
        self.sug_list = QListWidget()
        self.sug_list.setObjectName("sugList")
        self.sug_list.setFrameShape(QFrame.Shape.NoFrame)
        self.sug_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sug_list.setFixedHeight(DS.ROW_H * DS.VISIBLE_ROWS + 2)
        self.sug_list.itemClicked.connect(self._on_sug_clicked)
        self.sug_list.hide()
        box.addWidget(self.sug_list)

        # ===== 底栏：身份 + 分支切换 =====
        self.footer = QFrame()
        self.footer.setObjectName("footer")
        self.footer.setFixedHeight(DS.FOOT_H)
        fb = QHBoxLayout(self.footer)
        fb.setContentsMargins(DS.PAD_X, 0, DS.PAD_X, 0)
        fb.setSpacing(8)

        # 身份入口（点击打开设置中心的身份细分）
        self.btn_identity = QFrame()
        self.btn_identity.setObjectName("identChip")
        self.btn_identity.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_identity.setToolTip("选择行业 / 职业 / 职位，让推荐更贴合你的工作")
        ib = QHBoxLayout(self.btn_identity)
        ib.setContentsMargins(6, 0, 8, 0)
        ib.setSpacing(5)
        self.ident_icon = IconWidget("identity", 12, DS.PRIMARY)
        self.ident_label = QLabel()
        self.ident_label.setObjectName("identText")
        ib.addWidget(self.ident_icon)
        ib.addWidget(self.ident_label)
        self.btn_identity.mousePressEvent = (       # type: ignore[method-assign]
            lambda e: self.open_settings.emit())

        fb.addWidget(self.btn_identity)
        fb.addStretch(1)

        # 分支切换按钮
        self.btn_branch = QFrame()
        self.btn_branch.setObjectName("branchChip")
        self.btn_branch.setCursor(Qt.CursorShape.PointingHandCursor)
        bb = QHBoxLayout(self.btn_branch)
        bb.setContentsMargins(6, 0, 8, 0)
        bb.setSpacing(5)
        self.branch_icon = IconWidget("suggest", 12, DS.ACCENT_SUG)
        self.branch_label = QLabel("智能推荐")
        self.branch_label.setObjectName("branchText")
        bb.addWidget(self.branch_icon)
        bb.addWidget(self.branch_label)
        self.btn_branch.mousePressEvent = (         # type: ignore[method-assign]
            lambda e: self.toggle_branch.emit())
        fb.addWidget(self.btn_branch)

        # 快捷键提示（极简）
        self.key_hint = QLabel("F2 切换")
        self.key_hint.setObjectName("keyHint")
        fb.addWidget(self.key_hint)

        box.addWidget(self.footer)

    # ---------------- 样式 ----------------
    def _apply_style(self) -> None:
        d = DS
        ui = self.config.ui
        base = ui.font_family
        self.setStyleSheet(f"""
            #container {{
                background: {d.BG};
                border: 1px solid {d.LINE};
                border-radius: {d.RADIUS}px;
            }}
            /* ---------- 顶栏 ---------- */
            #sceneBar {{
                background: {d.BG_BAR};
                border-top-left-radius: {d.RADIUS}px;
                border-top-right-radius: {d.RADIUS}px;
                border-bottom: 1px solid {d.LINE};
            }}
            #sceneLabel {{
                color: {d.TEXT};
                font-size: 12px;
                font-weight: 600;
                font-family: "{base}";
            }}
            #tagChip {{
                color: {d.PRIMARY};
                background: {d.PRIMARY_SOFT};
                border-radius: 3px;
                padding: 1px 6px;
                font-size: 11px;
                font-family: "{base}";
            }}
            /* ---------- 输入行 ---------- */
            #inputWrap {{
                background: {d.BG};
                border-bottom: 1px solid {d.LINE};
            }}
            #inputBox {{
                border: none;
                background: transparent;
                color: {d.TEXT};
                font-size: 15px;
                font-family: "{base}";
                selection-background-color: #CFE1F5;
            }}
            #countLabel {{
                color: {d.TEXT_FAINT};
                font-size: 11px;
                font-family: "{base}";
            }}
            /* ---------- 候选列表 ---------- */
            #candList, #sugList {{
                background: {d.BG};
                outline: none;
                border: none;
            }}
            #candList::item {{
                border-bottom: 1px solid {d.LINE_SOFT};
            }}
            #candList::item:selected, #sugList::item:selected {{
                background: {d.PRIMARY_SOFT};
            }}
            QWidget[active="true"] {{
                background: {d.PRIMARY_SOFT};
            }}
            #idx {{
                color: {d.TEXT_FAINT};
                font-size: 11px;
                font-family: "Consolas";
            }}
            #zh {{
                color: {d.TEXT};
                font-size: 14px;
                font-weight: 600;
                font-family: "{base}";
            }}
            #en {{
                color: {d.ACCENT_EN};
                font-size: 12px;
                font-family: "Consolas", "{base}";
            }}
            #desc {{
                color: {d.TEXT_MUTED};
                font-size: 11px;
                font-family: "{base}";
            }}
            #why {{
                color: {d.ACCENT_OK};
                background: {d.OK_SOFT};
                border-radius: 3px;
                padding: 1px 5px;
                font-size: 10px;
                font-family: "{base}";
            }}
            /* ---------- 推荐列表 ---------- */
            #sugList::item {{
                border-bottom: 1px solid #F5F3FF;
            }}
            #sugZh {{
                color: {d.TEXT};
                font-size: 14px;
                font-weight: 600;
                font-family: "{base}";
            }}
            #sugEn {{
                color: {d.ACCENT_SUG};
                font-size: 12px;
                font-family: "Consolas", "{base}";
            }}
            #sugReason {{
                color: {d.TEXT_MUTED};
                font-size: 11px;
                font-family: "{base}";
            }}
            /* ---------- 底栏 ---------- */
            #footer {{
                background: {d.BG_BAR};
                border-bottom-left-radius: {d.RADIUS}px;
                border-bottom-right-radius: {d.RADIUS}px;
                border-top: 1px solid {d.LINE};
            }}
            #identChip, #branchChip {{
                background: {d.BG};
                border: 1px solid {d.LINE};
                border-radius: 5px;
            }}
            #identChip:hover {{
                background: {d.PRIMARY_SOFT};
                border: 1px solid {d.PRIMARY_SOFT_2};
            }}
            #branchChip:hover {{
                background: #F3F0FF;
                border: 1px solid #E4DCFF;
            }}
            #identText {{
                color: {d.PRIMARY};
                font-size: 11px;
                font-family: "{base}";
            }}
            #branchText {{
                color: {d.ACCENT_SUG};
                font-size: 11px;
                font-family: "{base}";
            }}
            #keyHint {{
                color: {d.TEXT_FAINT};
                font-size: 10px;
                font-family: "{base}";
            }}
            /* ---------- 滚动条 ---------- */
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: #D5DBE4;
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

    # ---------------- 场景标签 ----------------
    def _render_tags(self, tags: list[str]) -> None:
        """重建顶栏场景标签（先清空旧控件）。"""
        while self.tag_row.count():
            it = self.tag_row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for t in tags[:2]:
            lab = QLabel(scene_label(t))
            lab.setObjectName("tagChip")
            self.tag_row.addWidget(lab)

    # ---------------- 三级细分 ----------------
    def apply_hierarchy(self, initial: bool = False) -> None:
        prof = self.config.profile
        h = prof.get("hierarchy", {}) or {}
        ind = h.get("industry") or prof.get("active_profession", "")
        occ = h.get("occupation", "")
        pos = h.get("position", "")

        self.scene.industry_key = ind
        self.scene.occupation_key = occ
        self.scene.position_key = pos
        tags, boost = resolve_focus_tags(ind, occ, pos)
        self.scene.focus_tags = tags
        self.scene.position_boost = boost
        self.scene.hierarchy = describe_chain(ind, occ, pos)
        if ind:
            self.scene.profession_key = ind

        # 底栏身份链：行业 › 职业 › 职位，过长时省略中段
        parts = [p.strip() for p in (self.scene.hierarchy or "").split("/")
                 if p.strip()]
        if not parts:
            self.ident_label.setText("未设置身份")
        elif len(parts) == 1:
            self.ident_label.setText(parts[0])
        elif len(parts) == 2:
            self.ident_label.setText(f"{parts[0]} › {parts[1]}")
        else:
            self.ident_label.setText(f"{parts[0]} › {parts[-1]}")

        if initial:
            self._sync_scene_tags_with_profession()

    def _sync_scene_tags_with_profession(self) -> None:
        if self.detector.is_locked:
            sw = self.config.softwares.get(self.scene.software_key)
            if sw:
                self.scene.scene_tags = list(sw.scenes)

    def reload_hierarchy(self) -> None:
        self.config = get_config(reload=True)
        self.scene = self.detector.detect(force=True)
        for k in ("industry_key", "occupation_key", "position_key"):
            setattr(self.scene, k, getattr(self.scene, k, "") or "")
        self.apply_hierarchy()
        self.refresh_scene()
        self.refresh_candidates(self.input.text())
        self.refresh_suggestions(self.input.text())

    # ---------------- 场景刷新 ----------------
    def refresh_scene(self) -> None:
        self.scene = self.detector.detect()
        self.apply_hierarchy()
        self._paint_scene()

    def _paint_scene(self) -> None:
        self.scene_label.setText(self.scene.describe())
        self._render_tags(self.scene.scene_tags[:2])

    def set_scene(self, scene: SceneContext) -> None:
        self.scene = scene
        self.apply_hierarchy()
        self._paint_scene()
        self.refresh_candidates(self.input.text())
        self.refresh_suggestions(self.input.text())

    # ---------------- 候选 ----------------
    def on_text_changed(self, text: str) -> None:
        self.refresh_candidates(text)
        if self.branch == "suggest":
            self.refresh_suggestions(text)

    def refresh_candidates(self, query: str) -> None:
        # v3.0.4：沿用候选条上的「智/普/专」模式（旧面板已弃用，仅保持一致）
        _mode = str(getattr(self.config.ui, "candidate_mode", "auto") or "auto")
        self.candidates = self.engine.search(query, self.scene, mode=_mode)
        self.active_index = 0
        self.list.clear()

        for i, c in enumerate(self.candidates):
            item = QListWidgetItem(self.list)
            item.setSizeHint(QSize(0, DS.ROW_H))
            self.list.addItem(item)
            self.list.setItemWidget(item, CandidateRow(c, i))

        if self.candidates:
            self.list.setCurrentRow(0)
        self.count_label.setText(
            f"{len(self.candidates)} 项" if self.candidates else "无匹配")

    def refresh_suggestions(self, query: str = "") -> None:
        try:
            self.suggestions = self.recommender.suggest(
                query, self.scene, accepted=self.accepted, limit=6)
        except Exception:
            self.suggestions = []
        self.sug_list.clear()
        for i, s in enumerate(self.suggestions):
            item = QListWidgetItem(self.sug_list)
            item.setSizeHint(QSize(0, DS.ROW_H))
            self.sug_list.addItem(item)
            self.sug_list.setItemWidget(item, SuggestionRow(s, i))
        if self.suggestions:
            self.sug_list.setCurrentRow(0)

    def show_branch(self, branch: str) -> None:
        self.branch = branch
        is_sug = branch == "suggest"
        if is_sug:
            self.refresh_suggestions(self.input.text())
        self.list.setVisible(not is_sug)
        self.sug_list.setVisible(is_sug)
        # 分支按钮：图标与文案跟随状态切换，语义一致
        if is_sug:
            self.branch_icon.set_icon("terms")
            self.branch_icon.set_color(DS.PRIMARY)
            self.branch_label.setText("词库候选")
        else:
            self.branch_icon.set_icon("suggest")
            self.branch_icon.set_color(DS.ACCENT_SUG)
            self.branch_label.setText("智能推荐")
        # 不 setFocus：焦点必须留在目标程序

    def toggle_branch_view(self) -> None:
        self.show_branch("terms" if self.branch == "suggest" else "suggest")

    # ---------------- 键盘 ----------------
    # 候选窗自身不再处理键盘：所有按键由 core/ime.py 的全局钩子在系统层拦截，
    # 内核通过 ImeEvent 驱动本窗口（见 on_ime_events）。窗口不接收焦点，
    # 因此这里也没有 eventFilter / keyPressEvent 的必要。

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.active_index = self.list.row(item)
        self._commit(english=False)

    def _on_sug_clicked(self, item: QListWidgetItem) -> None:
        self.active_index = self.sug_list.row(item)
        self._commit_suggestion()

    # ---------------- 输入内核对接 ----------------
    def attach_ime(self, controller) -> None:
        """绑定全局键盘钩子内核，接管候选窗的显示与上屏。"""
        self.ime = controller
        self._ime_poll = QTimer(self)
        self._ime_poll.setInterval(25)          # 25ms：够跟手，又不空转
        self._ime_poll.timeout.connect(self.on_ime_events)
        self._ime_poll.start()

    def _emit_text(self, text: str) -> bool:
        """统一上屏出口：走 SendInput 逐字注入，不碰剪贴板。

        因为本窗口是 WS_EX_NOACTIVATE，焦点始终留在目标程序，
        注入的字符会直接落到用户正在编辑的位置。
        """
        if not text:
            return False
        if self.ime is not None:
            return self.ime.send(text)
        return self.inserter.insert(text)

    def on_ime_events(self) -> None:
        """主线程消费钩子事件：刷新候选、响应选词、随光标移动。"""
        if self.ime is None:
            return
        for ev in self.ime.drain():
            kind = ev.kind
            if kind == "update":
                self.show_composition(ev.data.get("pinyin", ""))
            elif kind == "cancel":
                self.input.clear()
                self.close_panel()
            elif kind == "commit":
                self.select_index(ev.data.get("index", -1),
                                  bool(ev.data.get("english")))
            elif kind == "commit_combo":
                self._commit_combo(ev.data.get("index", 0))
            elif kind == "commit_raw":
                raw = ev.data.get("pinyin", "")
                if raw:
                    self._emit_text(raw)
                self.input.clear()
                self.refresh_candidates("")
                self.close_panel()
            elif kind == "nav":
                self._move_active(ev.data.get("delta", 1))
            elif kind == "page":
                self._page(ev.data.get("delta", 1))
            elif kind == "toggle":
                self._paint_ime_state()

    def show_composition(self, pinyin: str) -> None:
        """组合态：显示拼音串、贴住光标、弹出候选。"""
        if self.input.text() != pinyin:
            self.input.setText(pinyin)          # 触发 on_text_changed 刷新候选
        else:
            self.refresh_candidates(pinyin)
        self.refresh_suggestions(pinyin)
        if not self.isVisible():
            self.show_panel(focus=False)
        else:
            self._move_to_cursor()

    def _paint_ime_state(self) -> None:
        on = bool(self.ime and self.ime.enabled)
        self.prefix.set_icon("keyboard" if on else "english")
        self.prefix.set_color(DS.PRIMARY if on else DS.TEXT_FAINT)
        self.prefix.update()

    def _current_pool(self):
        return self.suggestions if self.branch == "suggest" else self.candidates

    def _move_active(self, delta: int) -> None:
        pool = self._current_pool()
        if not pool:
            return
        n = len(pool)
        self.active_index = (self.active_index + delta) % n
        target = self.sug_list if self.branch == "suggest" else self.list
        target.setCurrentRow(self.active_index)
        target.scrollToItem(target.currentItem())
        self._move_to_cursor()

    def _page(self, delta: int) -> None:
        pool = self._current_pool()
        if not pool:
            return
        step = DS.VISIBLE_ROWS
        self.active_index = max(0, min(len(pool) - 1,
                                       self.active_index + delta * step))
        target = self.sug_list if self.branch == "suggest" else self.list
        target.setCurrentRow(self.active_index)
        target.scrollToItem(target.currentItem())

    def select_index(self, idx: int, english: bool = False) -> None:
        """内核选词回调：idx=-1 表示取当前高亮项。"""
        if idx < 0:
            self.active_index = max(0, min(self.active_index,
                                           max(0, len(self._current_pool()) - 1)))
        else:
            self.active_index = idx
        if self.branch == "suggest":
            self._commit_suggestion()
        else:
            self._commit(english=english)

    # ---------------- 上屏 ----------------
    def _remember(self, zh: str) -> None:
        if not zh:
            return
        self.accepted.insert(0, zh)
        del self.accepted[8:]

    def _after_commit(self) -> None:
        """上屏后收窗，但不碰焦点：焦点必须留在目标程序里。"""
        self.active_index = 0
        self.input.clear()
        self.close_panel()

    def _commit(self, english: bool) -> None:
        if not self.candidates:
            return
        c = self.candidates[self.active_index]
        text = c.en if english else c.zh
        if not text:
            return
        if english and c.term.standard:
            text = f"{c.en} ({c.term.standard})"
        self.engine.commit(c.zh, self.scene)
        self._remember(c.zh)
        self.committed.emit(text, "en" if english else "zh")
        self._emit_text(text)
        self.input.clear()
        self.refresh_candidates("")
        if self.branch == "suggest":
            self.refresh_suggestions("")
        self._after_commit()

    def _commit_combo(self, idx: int) -> None:
        """Ctrl+数字：一次插入「中文 English (标准号)」，用于图纸标注。"""
        pool = self.suggestions if self.branch == "suggest" else self.candidates
        if idx >= len(pool):
            return
        term = pool[idx].term
        parts = [term.zh]
        if term.en:
            parts.append(term.en)
            if term.standard:
                parts[-1] = f"{parts[-1]} ({term.standard})"
        text = " ".join(parts)
        self.engine.commit(term.zh, self.scene)
        self._remember(term.zh)
        self.committed.emit(text, "combo")
        self._emit_text(text)
        self.input.clear()
        self.refresh_candidates("")
        if self.branch == "suggest":
            self.refresh_suggestions("")
        self._after_commit()

    def _commit_suggestion(self) -> None:
        if not self.suggestions:
            return
        t = self.suggestions[self.active_index].term
        if not t.zh:
            return
        self.engine.commit(t.zh, self.scene)
        self._remember(t.zh)
        self.committed.emit(t.zh, "zh")
        self._emit_text(t.zh)
        self.refresh_suggestions(self.input.text())
        self._after_commit()

    # ---------------- 显示 / 隐藏 ----------------
    def prime_window(self) -> None:
        """映射一次窗口以取得 HWND 并打上 WS_EX_NOACTIVATE，然后隐藏。

        必须在真正使用前完成，否则第一次弹出候选窗时会短暂抢焦。
        """
        self.setWindowOpacity(0.0)
        self.show()
        make_no_activate(self)
        self._noact_applied = True
        self.hide()
        self.setWindowOpacity(self.config.ui.opacity)

    def show_panel(self, focus: bool = False) -> None:
        """focus=True 仅用于托盘/设置中心主动呼出。

        输入法场景恒为 False：候选窗以 WS_EX_NOACTIVATE 浮在目标程序上方，
        焦点从不离开编辑器，注入的字符才会落在正确位置。
        """
        self.refresh_scene()
        self.refresh_candidates(self.input.text())
        self._move_to_cursor()
        self.show()
        if not getattr(self, "_noact_applied", False) and self.isVisible():
            make_no_activate(self)
            self._noact_applied = True
        if focus:
            self.raise_()
        self._animate_in()

    def _move_to_cursor(self) -> None:
        """贴住真实插入符，取不到就退回鼠标位置；越界自动翻边。"""
        info: CaretInfo = get_caret_info()
        x, y = info.x, info.y
        screen = QApplication.primaryScreen()
        w = self.width()
        h = self.sizeHint().height()
        nx, ny = x + 4, y + 22                 # 默认落在插入符下方
        if screen is not None:
            geo = screen.availableGeometry()
            if nx + w > geo.right():
                nx = max(geo.left() + 8, x - w - 4)
            if ny + h > geo.bottom():
                ny = max(geo.top() + 8, y - h - 14)   # 下方不够就上翻
        self.move(nx, ny)

    def _animate_in(self) -> None:
        self.setWindowOpacity(0.0)
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(100)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(self.config.ui.opacity)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.start()
        target = self.pos()
        self._pos_anim = QPropertyAnimation(self, b"pos")
        self._pos_anim.setDuration(100)
        self._pos_anim.setStartValue(QPoint(target.x(), target.y() + 6))
        self._pos_anim.setEndValue(target)
        self._pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pos_anim.start()

    def close_panel(self) -> None:
        self.hide()
        if self.input.text():
            self.input.clear()
        self.closed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    engine = TermEngine()
    detector = SceneDetector()
    inserter = Inserter()
    panel = InputPanel(engine, detector, inserter)
    panel.show_panel()
    sys.exit(app.exec())
