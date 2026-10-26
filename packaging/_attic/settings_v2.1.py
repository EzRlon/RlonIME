"""
智语输入法 — 设置中心（v2.1 MD3 重写）

Material Design 3 卡片式布局：身份 / 输入 / 快捷键 / 词条 / 水杉 / AI 六组。
从候选窗 "⚙ 设置" 入口打开，选择结果即时生效并持久化到 config.json。

契约（main.py 依赖，勿改）：
  - 构造签名 SettingsDialog(engine=..., ime=..., parent=None)
  - 信号 settings_changed = Signal(dict)，emit 的 dict 必含 "industry" 字段
  - 所有改动写回 config.json（main.py 走 get_config(reload=True) 读取）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QFrame, QGroupBox, QFormLayout, QLineEdit, QCheckBox, QMessageBox,
    QTabWidget, QWidget, QListWidget, QListWidgetItem
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import get_config, Config, DEFAULT_HOTKEYS
from core.hierarchy import (
    INDUSTRIES, get_industry, get_occupation, get_position,
    describe_chain, resolve_focus_tags
)
from core.models import scene_label
from ui.design import DS
from ui.icons import make_icon, IconWidget

# 五个可改热键 → 中文名（顺序即界面行序）
HOTKEY_ROWS: list[tuple[str, str]] = [
    ("translate_selection", "选中即译"),
    ("mark_correction", "标记纠正入库"),
    ("toggle_panel", "显示 / 隐藏候选条"),
    ("cycle_scope", "循环切换行业身份"),
    ("toggle_llm", "开关 AI 翻译增强"),
]


class Md3Switch(QCheckBox):
    """MD3 开关：自绘轨道 + 滑钮（QCheckBox + paintEvent，无新依赖）。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(26)

    def paintEvent(self, ev):  # noqa: N802
        d = DS
        tw, th, knob = d.MD3_SWITCH_W, d.MD3_SWITCH_H, d.MD3_SWITCH_KNOB
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        x, y = 2, (self.height() - th) // 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(d.MD3_PRIMARY if on else d.MD3_SWITCH_TRACK_OFF))
        p.drawRoundedRect(x, y, tw, th, th / 2, th / 2)
        p.setBrush(QColor(d.MD3_SWITCH_THUMB if on else d.MD3_OUTLINE))
        kx = x + tw - knob - 3 if on else x + 3
        p.drawEllipse(kx, y + (th - knob) // 2, knob, knob)
        p.setPen(QColor(d.MD3_ON_SURFACE))
        p.setFont(self.font())
        p.drawText(x + tw + 10, 0, self.width() - tw - 12, self.height(),
                   Qt.AlignmentFlag.AlignVCenter, self.text())
        p.end()


class SettingsDialog(QDialog):
    """设置中心对话框（MD3）。"""

    settings_changed = Signal(dict)   # 发出新的设置

    def __init__(self, engine=None, parent=None, ime=None):
        super().__init__(parent)
        self.config = get_config()
        self.engine = engine
        self.ime = ime                    # 输入内核，用于展示接管状态
        self.setWindowTitle("智语输入法 · 设置中心")
        self.setMinimumSize(700, 600)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )

        self._build_ui()
        self._load_current()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部标题
        head = QFrame()
        head.setObjectName("head")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 16, 20, 14)
        hl.setSpacing(10)
        hl.addWidget(IconWidget("settings", 22, DS.MD3_PRIMARY),
                     alignment=Qt.AlignmentFlag.AlignTop)
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        t1 = QLabel("设置中心")
        t1.setObjectName("title")
        t2 = QLabel("细分身份 / 输入行为 / 快捷键，让智语更贴合你的实际工作")
        t2.setObjectName("subtitle")
        title_box.addWidget(t1)
        title_box.addWidget(t2)
        hl.addLayout(title_box, 1)
        root.addWidget(head)

        # Tab 页
        self.tabs = QTabWidget()
        self.tabs.setObjectName("tabs")
        self.tabs.setIconSize(QSize(16, 16))
        primary = DS.MD3_PRIMARY
        self.tabs.addTab(self._build_hierarchy_tab(),
                         make_icon("identity", 16, primary), "身份")
        self.tabs.addTab(self._build_input_tab(),
                         make_icon("keyboard", 16, primary), "输入")
        self.tabs.addTab(self._build_hotkey_tab(),
                         make_icon("language", 16, primary), "快捷键")
        self.tabs.addTab(self._build_custom_tab(),
                         make_icon("terms", 16, primary), "词条")
        self.tabs.addTab(self._build_msime_tab(),
                         make_icon("settings", 16, primary), "水杉对接")
        self.tabs.addTab(self._build_llm_tab(),
                         make_icon("ai", 16, primary), "AI 增强")
        root.addWidget(self.tabs, 1)

        # 底部按钮
        foot = QFrame()
        foot.setObjectName("foot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 12, 20, 12)
        fl.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("btnGhost")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("保存并应用")
        btn_ok.setObjectName("btnPrimary")
        btn_ok.clicked.connect(self._save)
        fl.addWidget(btn_cancel)
        fl.addWidget(btn_ok)
        root.addWidget(foot)

        self._apply_style()

    # ---- Tab 1：身份 ----
    def _build_hierarchy_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        self.chain_preview = QLabel()
        self.chain_preview.setObjectName("chainPreview")
        self.chain_preview.setWordWrap(True)
        lay.addWidget(self.chain_preview)

        self.focus_preview = QLabel()
        self.focus_preview.setObjectName("focusPreview")
        self.focus_preview.setWordWrap(True)
        lay.addWidget(self.focus_preview)

        box = QGroupBox("身份细分")
        form = QFormLayout(box)
        form.setContentsMargins(16, 20, 16, 16)
        form.setSpacing(12)

        self.cb_industry = QComboBox()
        self.cb_industry.setMaxVisibleItems(24)
        _last_group = None
        for ind in INDUSTRIES:
            g = ind.group or "其他"
            if g != _last_group:
                self.cb_industry.insertSeparator(self.cb_industry.count())
                _last_group = g
            self.cb_industry.addItem(
                f"{g} · {ind.name}" if g != "其他" else ind.name, ind.key)
        self.cb_industry.currentIndexChanged.connect(self._on_industry_changed)

        self.cb_occupation = QComboBox()
        self.cb_occupation.currentIndexChanged.connect(self._on_occupation_changed)
        self.cb_position = QComboBox()
        self.cb_position.currentIndexChanged.connect(self._on_position_changed)

        form.addRow(self._label("行业"), self.cb_industry)
        form.addRow(self._label("职业"), self.cb_occupation)
        form.addRow(self._label("职位"), self.cb_position)
        lay.addWidget(box)

        self.desc_label = QLabel()
        self.desc_label.setObjectName("descBox")
        self.desc_label.setWordWrap(True)
        lay.addWidget(self.desc_label)
        lay.addStretch(1)
        return page

    # ---- Tab 2：输入 ----
    def _build_input_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        grp = QGroupBox("接管行为")
        gl = QVBoxLayout(grp)
        gl.setContentsMargins(16, 20, 16, 16)
        gl.setSpacing(10)
        ime_cfg = self.config.ime or {}
        self.chk_ime = Md3Switch("启用输入接管（任意软件里直接敲拼音出候选）")
        self.chk_ime.setChecked(bool(ime_cfg.get("enabled", True)))
        self.chk_caret = Md3Switch("候选条跟随输入光标（关闭则跟随鼠标）")
        self.chk_caret.setChecked(bool(ime_cfg.get("caret_follow", True)))
        self.chk_remember = Md3Switch("记住候选条拖动位置（重启后保持）")
        self.chk_remember.setChecked(
            bool(getattr(self.config.ui, "remember_bar_pos", True)))
        gl.addWidget(self.chk_ime)
        gl.addWidget(self.chk_caret)
        gl.addWidget(self.chk_remember)
        lay.addWidget(grp)

        fs = QGroupBox("候选条字号")
        form = QFormLayout(fs)
        form.setContentsMargins(16, 20, 16, 16)
        form.setSpacing(10)
        self.cb_fs_zh = QComboBox()
        self.cb_fs_zh.addItems(["10", "11", "12", "13", "14"])
        self.cb_fs_zh.setCurrentText(
            str(getattr(self.config.ui, "font_size_zh", 12)))
        self.cb_fs_en = QComboBox()
        self.cb_fs_en.addItems(["8", "9", "10", "11", "12"])
        self.cb_fs_en.setCurrentText(
            str(getattr(self.config.ui, "font_size_en", 10)))
        form.addRow(self._label("中文"), self.cb_fs_zh)
        form.addRow(self._label("英文/译法"), self.cb_fs_en)
        lay.addWidget(fs)

        keys = QLabel(
            "Ctrl+Space　　　　　　开关接管\n"
            "数字 1-9 / 空格 / 回车　　选词上屏\n"
            "Tab　　　　　　　　　 上屏对应英文术语\n"
            "Ctrl+数字　　　　　　 中文 + 英文 + 标准号 一次上屏\n"
            "↑ ↓ / PgUp / PgDn　　　移动候选高亮\n"
            "Esc　　　　　　　　　 取消本次输入\n"
            "Shift+字母　　　　　　直接输入大写英文"
        )
        keys.setObjectName("keySheet")
        lay.addWidget(keys)

        self.ime_status = QLabel()
        self.ime_status.setObjectName("statusLine")
        self._refresh_ime_status()
        lay.addWidget(self.ime_status)
        lay.addStretch(1)
        return page

    def _refresh_ime_status(self) -> None:
        if not hasattr(self, "ime_status"):
            return
        if self.ime is None:
            self.ime_status.setText("")
            return
        try:
            st = self.ime.status()
        except Exception:
            self.ime_status.setText("")
            return
        hooked = "已生效" if st.get("hooked") else f"未生效（{st.get('error')}）"
        elev = "已提权" if st.get("elevated") else "普通权限 · 对管理员窗口不生效"
        self.ime_status.setText(f"内核状态：{hooked} · {elev}")

    # ---- Tab 3：快捷键 ----
    def _build_hotkey_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        tip = QLabel(
            "默认全部左手位（右手不离开鼠标）。格式：ctrl+alt+字母/数字，"
            "可用修饰键 ctrl / alt / shift / win。改动保存后即时生效。")
        tip.setObjectName("subtitle")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        box = QGroupBox("改键")
        form = QFormLayout(box)
        form.setContentsMargins(16, 20, 16, 16)
        form.setSpacing(10)
        self.hk_edits: dict[str, QLineEdit] = {}
        for name, label in HOTKEY_ROWS:
            ed = QLineEdit()
            ed.setObjectName("hkEdit")
            ed.setText(self.config.hotkey(name, DEFAULT_HOTKEYS[name]))
            form.addRow(self._label(label, width=150), ed)
            self.hk_edits[name] = ed
        lay.addWidget(box)

        row = QHBoxLayout()
        btn_default = QPushButton("恢复默认（左手位）")
        btn_default.setObjectName("btnGhost")
        btn_default.clicked.connect(self._reset_hotkeys)
        row.addWidget(btn_default)
        row.addStretch(1)
        lay.addLayout(row)

        self.hk_error = QLabel()
        self.hk_error.setObjectName("hkError")
        self.hk_error.setWordWrap(True)
        self.hk_error.hide()
        lay.addWidget(self.hk_error)
        lay.addStretch(1)
        return page

    def _reset_hotkeys(self) -> None:
        for name, _ in HOTKEY_ROWS:
            self.hk_edits[name].setText(DEFAULT_HOTKEYS[name])
        self.hk_error.hide()

    @staticmethod
    def _norm_hotkey(spec: str) -> str:
        return "+".join(p.strip().lower()
                        for p in str(spec).split("+") if p.strip())

    # ---- Tab 4：词条 ----
    def _build_custom_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        tip = QLabel("添加你自己的专业术语，输入时会优先推荐。")
        tip.setObjectName("subtitle")
        lay.addWidget(tip)

        form_box = QGroupBox("新增词条")
        form = QFormLayout(form_box)
        form.setContentsMargins(16, 20, 16, 16)
        form.setSpacing(10)

        self.ed_zh = QLineEdit()
        self.ed_zh.setPlaceholderText("中文术语，如：氮化处理")
        self.ed_en = QLineEdit()
        self.ed_en.setPlaceholderText("英文表述，如：Nitriding")
        self.ed_brief = QLineEdit()
        self.ed_brief.setPlaceholderText("简述，如：表面硬化处理工艺")
        self.ed_scene = QLineEdit()
        self.ed_scene.setPlaceholderText("使用场景，如：工艺要求")

        form.addRow(self._label("中文"), self.ed_zh)
        form.addRow(self._label("英文"), self.ed_en)
        form.addRow(self._label("简述"), self.ed_brief)
        form.addRow(self._label("场景"), self.ed_scene)
        lay.addWidget(form_box)

        btn_add = QPushButton("添加到词库")
        btn_add.setObjectName("btnPrimary")
        btn_add.clicked.connect(self._add_custom)
        lay.addWidget(btn_add, alignment=Qt.AlignmentFlag.AlignRight)

        self.custom_list = QListWidget()
        self.custom_list.setObjectName("termList")
        lay.addWidget(self.custom_list, 1)

        self._reload_custom_list()
        return page

    # ---- Tab 5：水杉对接 ----
    def _build_msime_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        tip = QLabel(
            "水杉输入法是一款开源的 Windows 原生 TSF 输入法。\n"
            "它自带两个官方扩展点，无需改动其源码即可接入本词库：\n"
            "  ① 设置 → 词库 → 批量导入（Tab 分隔：词语 / 编码 / 权重）\n"
            "  ② 自定义候选窗翻译 custom_translations.txt（输入中文直接出英文）"
        )
        tip.setObjectName("subtitle")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        box = QGroupBox("导出对接文件")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(16, 20, 16, 16)
        bl.setSpacing(10)
        row = QHBoxLayout()
        self.btn_export = QPushButton("导出全部对接文件")
        self.btn_export.setObjectName("btnPrimary")
        self.btn_export.clicked.connect(self._export_msime)
        self.btn_open_dir = QPushButton("打开导出目录")
        self.btn_open_dir.setObjectName("btnGhost")
        self.btn_open_dir.clicked.connect(self._open_export_dir)
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_open_dir)
        row.addStretch(1)
        bl.addLayout(row)
        lay.addWidget(box)

        self.export_list = QListWidget()
        self.export_list.setObjectName("termList")
        lay.addWidget(self.export_list, 1)

        steps = QLabel(
            "导入步骤：\n"
            "1. 点「导出全部对接文件」，得到全拼词库与 custom_translations.txt\n"
            "2. 复制 custom_translations.txt 到 %LOCALAPPDATA%\\metasequoiaime\\ 下，重启输入法\n"
            "3. 打开水杉「设置 → 词库 → 批量导入」，选择 zhiyu_quanpin_dict.txt\n"
            "4. 若要按领域分开管理，可分别导入 zhiyu_quanpin_<领域>.txt\n"
            "5. AI 联想提示词在「实用功能 / AI 辅助」页填入，内容见 msime_ai_prompts.txt"
        )
        steps.setObjectName("descBox")
        steps.setWordWrap(True)
        lay.addWidget(steps)
        return page

    def _export_msime(self) -> None:
        try:
            from core.msime_bridge import export_all
            res = export_all()
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return
        self.export_list.clear()
        for desc, path, n in res.files:
            self.export_list.addItem(f"{desc}    ({n} 条)")
        self._export_dir = res.files[0][1].parent
        QMessageBox.information(
            self, "导出完成",
            f"已生成 {len(res.files)} 个文件，共 {res.total()} 条数据。\n\n"
            f"位置：{self._export_dir}"
        )

    def _open_export_dir(self) -> None:
        d = getattr(self, "_export_dir", None)
        if d is None:
            try:
                from core.msime_bridge import export_all
                d = export_all().files[0][1].parent
            except Exception:
                d = None
        if d and Path(d).exists():
            os.startfile(str(d))  # noqa: S606
        else:
            QMessageBox.information(self, "提示", "请先执行一次导出")

    # ---- Tab 6：AI 增强 ----
    def _build_llm_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        tip = QLabel(
            "默认使用本地智能推荐（完全离线、零延迟）。\n"
            "如需更强的语境理解，可填入 OpenAI 兼容接口的 API Key 启用 AI 增强；\n"
            "网络异常时会自动回退到本地推荐，不影响正常使用。"
        )
        tip.setObjectName("subtitle")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        box = QGroupBox("AI 增强配置")
        form = QFormLayout(box)
        form.setContentsMargins(16, 20, 16, 16)
        form.setSpacing(10)

        self.chk_llm = Md3Switch("启用 AI 增强推荐")
        self.ed_base_url = QLineEdit()
        self.ed_base_url.setPlaceholderText("https://api.openai.com/v1")
        self.ed_model = QLineEdit()
        self.ed_model.setPlaceholderText("gpt-4o-mini")
        self.ed_api_key = QLineEdit()
        self.ed_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_api_key.setPlaceholderText("sk-...（仅保存在本机）")

        form.addRow("", self.chk_llm)
        form.addRow(self._label("接口地址", width=76), self.ed_base_url)
        form.addRow(self._label("模型", width=76), self.ed_model)
        form.addRow(self._label("API Key", width=76), self.ed_api_key)
        lay.addWidget(box)

        self.llm_status = QLabel()
        self.llm_status.setObjectName("descBox")
        lay.addWidget(self.llm_status)
        lay.addStretch(1)
        return page

    @staticmethod
    def _label(text: str, width: int = 72) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("fieldLabel")
        lab.setFixedWidth(width)
        return lab

    # ---------------- 数据加载 ----------------
    def _load_current(self) -> None:
        h = self.config.profile.get("hierarchy", {}) or {}
        ind_key = h.get("industry") or self.config.profile.get(
            "active_profession", "mechanical")
        occ_key = h.get("occupation", "")
        pos_key = h.get("position", "")

        idx = self.cb_industry.findData(ind_key)
        self.cb_industry.setCurrentIndex(max(idx, 0))
        self._fill_occupations(ind_key, occ_key)
        self._fill_positions(ind_key, occ_key, pos_key)
        self._update_preview()

        llm = self.config.llm or {}
        self.chk_llm.setChecked(bool(llm.get("enabled")))
        self.ed_base_url.setText(llm.get("base_url", "https://api.openai.com/v1"))
        self.ed_model.setText(llm.get("model", "gpt-4o-mini"))
        self.ed_api_key.setText(llm.get("api_key", ""))
        self._update_llm_status()

    def _fill_occupations(self, ind_key: str, select: str = "") -> None:
        self.cb_occupation.blockSignals(True)
        self.cb_occupation.clear()
        ind = get_industry(ind_key)
        if ind:
            for o in ind.occupations:
                self.cb_occupation.addItem(o.name, o.key)
        if select:
            i = self.cb_occupation.findData(select)
            if i >= 0:
                self.cb_occupation.setCurrentIndex(i)
        self.cb_occupation.blockSignals(False)

    def _fill_positions(self, ind_key: str, occ_key: str,
                        select: str = "") -> None:
        self.cb_position.blockSignals(True)
        self.cb_position.clear()
        occ = get_occupation(ind_key, occ_key)
        if occ:
            for p in occ.positions:
                self.cb_position.addItem(p.name, p.key)
        if select:
            i = self.cb_position.findData(select)
            if i >= 0:
                self.cb_position.setCurrentIndex(i)
        self.cb_position.blockSignals(False)

    # ---------------- 级联事件 ----------------
    def _on_industry_changed(self) -> None:
        ind_key = self.cb_industry.currentData()
        self._fill_occupations(ind_key)
        occ_key = self.cb_occupation.currentData()
        self._fill_positions(ind_key, occ_key)
        self._update_preview()

    def _on_occupation_changed(self) -> None:
        ind_key = self.cb_industry.currentData()
        occ_key = self.cb_occupation.currentData()
        self._fill_positions(ind_key, occ_key)
        self._update_preview()

    def _on_position_changed(self) -> None:
        self._update_preview()

    def _update_preview(self) -> None:
        ind_key = self.cb_industry.currentData() or ""
        occ_key = self.cb_occupation.currentData() or ""
        pos_key = self.cb_position.currentData() or ""

        chain = describe_chain(ind_key, occ_key, pos_key)
        self.chain_preview.setText(f"当前身份：{chain}")

        tags, boost = resolve_focus_tags(ind_key, occ_key, pos_key)
        if tags:
            tag_text = " · ".join(scene_label(t) for t in tags)
            self.focus_preview.setText(
                f"重点关注场景：{tag_text}    |    词库加权系数：×{boost}"
            )
        else:
            self.focus_preview.setText("重点关注场景：通用")

        occ = get_occupation(ind_key, occ_key)
        pos = get_position(ind_key, occ_key, pos_key)
        parts = []
        if occ and occ.desc:
            parts.append(f"【{occ.name}】{occ.desc}")
        if pos and pos.desc:
            parts.append(f"【{pos.name}】{pos.desc}")
        self.desc_label.setText("\n".join(parts) if parts else "")

    # ---------------- 自定义词条 ----------------
    def _add_custom(self) -> None:
        zh = self.ed_zh.text().strip()
        if not zh:
            QMessageBox.warning(self, "提示", "中文术语不能为空")
            return
        if self.engine is None:
            QMessageBox.warning(self, "提示", "引擎未就绪")
            return
        try:
            self.engine.add_custom(
                zh=zh,
                en=self.ed_en.text().strip(),
                brief=self.ed_brief.text().strip(),
                scene_desc=self.ed_scene.text().strip(),
                profession=self.config.profile.get("active_profession", ""),
            )
            self.ed_zh.clear(); self.ed_en.clear()
            self.ed_brief.clear(); self.ed_scene.clear()
            self._reload_custom_list()
        except Exception as e:
            QMessageBox.critical(self, "失败", f"添加失败：{e}")

    def _reload_custom_list(self) -> None:
        self.custom_list.clear()
        if self.engine is None:
            return
        customs = [t for t in self.engine.lexicons.all_terms if t.custom]
        for t in customs:
            self.custom_list.addItem(
                QListWidgetItem(f"{t.zh}    {t.en}    {t.brief}"))
        if not customs:
            self.custom_list.addItem(QListWidgetItem("（暂无自定义词条）"))

    # ---------------- AI 状态 ----------------
    def _update_llm_status(self) -> None:
        llm = self.config.llm or {}
        if llm.get("enabled") and llm.get("api_key"):
            self.llm_status.setText("状态：AI 增强已启用")
        elif llm.get("enabled"):
            self.llm_status.setText("状态：已勾选启用，但未填 API Key，将使用本地推荐")
        else:
            self.llm_status.setText("状态：使用本地智能推荐（离线，零延迟）")

    # ---------------- 保存 ----------------
    def _save(self) -> None:
        # ① 热键冲突校验：不通过则红字提示并禁止保存
        hotkeys = {name: self._norm_hotkey(ed.text())
                   for name, ed in self.hk_edits.items()}
        errors = Config.validate_hotkeys(hotkeys)
        if errors:
            label_map = dict(HOTKEY_ROWS)
            pretty = [label_map.get(n, n) + "：" + msg.split("：", 1)[-1]
                      if "：" in msg else msg
                      for msg in errors for n in [msg.split(":", 1)[0]]]
            self.hk_error.setText("⚠ " + "；".join(errors))
            self.hk_error.show()
            self.tabs.setCurrentIndex(2)
            return
        self.hk_error.hide()

        ind_key = self.cb_industry.currentData() or "mechanical"
        occ_key = self.cb_occupation.currentData() or ""
        pos_key = self.cb_position.currentData() or ""

        result = {
            "industry": ind_key,
            "occupation": occ_key,
            "position": pos_key,
            "hotkeys": hotkeys,
            "llm": {
                "enabled": self.chk_llm.isChecked(),
                "provider": "openai_compatible",
                "base_url": self.ed_base_url.text().strip()
                            or "https://api.openai.com/v1",
                "model": self.ed_model.text().strip() or "gpt-4o-mini",
                "api_key": self.ed_api_key.text().strip(),
            },
        }

        # ② 持久化到 config.json（main.py 走 reload 读取，必须落盘）
        try:
            path = self.config.path
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.setdefault("profile", {})
            raw["profile"]["hierarchy"] = {
                "industry": ind_key, "occupation": occ_key,
                "position": pos_key,
            }
            raw["profile"]["active_profession"] = ind_key
            raw["llm"] = result["llm"]
            # 输入接管 + 字号 + 拖动位置
            raw.setdefault("ime", {})
            raw["ime"]["enabled"] = self.chk_ime.isChecked()
            raw["ime"]["caret_follow"] = self.chk_caret.isChecked()
            raw.setdefault("ui", {})
            raw["ui"]["font_size_zh"] = int(self.cb_fs_zh.currentText())
            raw["ui"]["font_size_en"] = int(self.cb_fs_en.currentText())
            raw["ui"]["remember_bar_pos"] = self.chk_remember.isChecked()
            # 热键唯一来源 hotkeys 段
            raw["hotkeys"] = hotkeys
            # 清理旧版 assist.*_hotkey 残留键
            if isinstance(raw.get("assist"), dict):
                raw["assist"].pop("translate_hotkey", None)
                raw["assist"].pop("mark_hotkey", None)
            # 自定义词条落盘
            if self.engine is not None:
                self.engine.lexicons.save_custom(
                    self.config.user_data_dir / "custom_terms.json"
                )
            path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return

        self.settings_changed.emit(result)
        self.accept()

    # ---------------- 样式（MD3 tonal，令牌来自 ui.design） ----------------
    def _apply_style(self) -> None:
        d = DS
        base = self.config.ui.font_family
        r = d.MD3_CARD_RADIUS
        self.setStyleSheet(f"""
            QDialog {{ background: {d.MD3_SURFACE_DIM}; }}
            #head {{ background: {d.MD3_SURFACE};
                     border-bottom: 1px solid {d.MD3_OUTLINE_VARIANT}; }}
            #title {{ font-size: 18px; font-weight: 600;
                      color: {d.MD3_ON_SURFACE}; font-family: "{base}"; }}
            #subtitle {{ font-size: 12px; color: {d.MD3_ON_SURFACE_VARIANT};
                         font-family: "{base}"; }}
            #chainPreview {{ font-size: 14px; font-weight: 600;
                color: {d.MD3_ON_PRIMARY_CONTAINER};
                background: {d.MD3_PRIMARY_CONTAINER};
                border-radius: {r}px; padding: 10px 12px;
                font-family: "{base}"; }}
            #focusPreview {{ font-size: 12px; color: {d.MD3_PRIMARY};
                background: {d.MD3_SURFACE_CONTAINER_HIGH};
                border-radius: {r}px; padding: 8px 12px;
                font-family: "{base}"; }}
            #descBox {{ font-size: 12px; color: {d.MD3_ON_SURFACE_VARIANT};
                        padding: 4px 2px; font-family: "{base}"; }}
            #fieldLabel {{ font-size: 13px; color: {d.MD3_ON_SURFACE_VARIANT};
                           font-family: "{base}"; }}
            #foot {{ background: {d.MD3_SURFACE};
                     border-top: 1px solid {d.MD3_OUTLINE_VARIANT}; }}
            #keySheet {{ font-size: 12px; color: {d.MD3_ON_SURFACE};
                line-height: 22px; background: {d.MD3_SURFACE_CONTAINER};
                border-radius: {r}px; padding: 12px 14px;
                font-family: "{base}"; }}
            #statusLine {{ font-size: 11px;
                color: {d.MD3_ON_SURFACE_VARIANT}; font-family: "{base}"; }}
            #hkEdit {{ font-family: "Consolas", "{base}"; }}
            #hkError {{ font-size: 12px; color: {d.MD3_ERROR};
                background: {d.MD3_ERROR_CONTAINER}; border-radius: {r}px;
                padding: 8px 12px; font-family: "{base}"; }}
            QGroupBox {{ font-size: 13px; font-weight: 600;
                color: {d.MD3_ON_SURFACE};
                background: {d.MD3_SURFACE_CONTAINER};
                border: none; border-radius: {r}px;
                margin-top: 12px; padding-top: 8px;
                font-family: "{base}"; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 14px;
                                padding: 0 6px; color: {d.MD3_ON_SURFACE}; }}
            QComboBox, QLineEdit {{
                border: 1px solid {d.MD3_OUTLINE};
                border-radius: 8px; padding: 7px 10px; font-size: 13px;
                background: {d.MD3_SURFACE}; color: {d.MD3_ON_SURFACE};
                font-family: "{base}"; }}
            QComboBox:focus, QLineEdit:focus {{
                border: 2px solid {d.MD3_PRIMARY}; }}
            QComboBox::drop-down {{ border: none; width: 22px; }}
            QComboBox QAbstractItemView {{
                border: 1px solid {d.MD3_OUTLINE_VARIANT};
                background: {d.MD3_SURFACE};
                selection-background-color: {d.MD3_PRIMARY_CONTAINER};
                selection-color: {d.MD3_ON_PRIMARY_CONTAINER};
                font-family: "{base}"; font-size: 13px; outline: none; }}
            QPushButton#btnPrimary {{ background: {d.MD3_PRIMARY};
                color: {d.MD3_ON_PRIMARY}; border: none; border-radius: 20px;
                padding: 9px 22px; font-size: 13px; font-weight: 600;
                font-family: "{base}"; }}
            QPushButton#btnPrimary:hover {{ background: {d.PRIMARY_HI}; }}
            QPushButton#btnGhost {{ background: transparent;
                color: {d.MD3_PRIMARY};
                border: 1px solid {d.MD3_OUTLINE}; border-radius: 20px;
                padding: 9px 22px; font-size: 13px; font-family: "{base}"; }}
            QPushButton#btnGhost:hover {{
                background: {d.MD3_PRIMARY_CONTAINER}; }}
            QTabWidget::pane {{ border: none; background: {d.MD3_SURFACE_DIM}; }}
            QTabBar::tab {{ background: transparent;
                color: {d.MD3_ON_SURFACE_VARIANT};
                padding: 9px 18px; font-size: 13px; border-radius: 10px;
                margin: 4px 2px 0 2px; font-family: "{base}"; }}
            QTabBar::tab:selected {{
                background: {d.MD3_PRIMARY_CONTAINER};
                color: {d.MD3_ON_PRIMARY_CONTAINER}; font-weight: 600; }}
            QListWidget {{ border: none; border-radius: {r}px;
                background: {d.MD3_SURFACE_CONTAINER};
                font-size: 12px; color: {d.MD3_ON_SURFACE_VARIANT};
                font-family: "{base}"; padding: 4px; }}
            QListWidget::item {{ padding: 4px 6px; border-radius: 6px; }}
            QListWidget::item:selected {{
                background: {d.MD3_PRIMARY_CONTAINER};
                color: {d.MD3_ON_PRIMARY_CONTAINER}; }}
            QScrollBar:vertical {{ background: transparent; width: 8px;
                                   margin: 0px; }}
            QScrollBar::handle:vertical {{ background: {d.MD3_OUTLINE};
                border-radius: 4px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px; }}
        """)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    dlg = SettingsDialog()
    dlg.exec()
