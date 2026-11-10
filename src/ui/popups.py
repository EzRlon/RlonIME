"""
RlonIME — 候选条配套弹窗（v2.2 D3/D5）

设置中心删除后，四个低频配置入口全部收进候选条顶行图标：

    ＋ → AddWordPopup    加词条（中文/英文/简述，✓ 自关）
    🏷 → IndustryPopup   行业范围（一级行业，点选即应用）
    Ⓐ → AiPopup          AI 增强（开关 + 接口 + Key）
    Ⓘ → AboutPopup       关于（版本/作者 + 导出水杉词库）

风格与候选条同一张卡片语言（frameless + 圆角 + 同款令牌）。
与候选条的区别：弹窗**需要接受焦点**（里面要打字），因此不打
WS_EX_NOACTIVATE；输入法内核靠「前台属于本进程全放行」保证打字正常。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget, QCheckBox,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import secrets as _secrets                   # noqa: E402  S03
from core.config import get_config                    # noqa: E402
from ui.design import DS                              # noqa: E402
from ui.icons import IconWidget                       # noqa: E402

HEADER_H = 30


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
        p.drawText(x + tw + 8, 0, self.width() - tw - 10, self.height(),
                   Qt.AlignmentFlag.AlignVCenter, self.text())
        p.end()


class _CardPopup(QWidget):
    """弹窗基座：frameless 卡片 + 标题行（✕ 关闭 + 拖动）+ Esc 关闭。"""

    def __init__(self, title: str, width: int = 240, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(width)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        self.card.setObjectName("popCard")
        root.addWidget(self.card)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(*DS.SHADOW))
        self.card.setGraphicsEffect(shadow)

        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(12, 8, 12, 12)
        self.body.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.lab_title = QLabel(title)
        self.lab_title.setObjectName("popTitle")
        btn_close = IconWidget("close", 11, DS.TEXT_FAINT)
        btn_close.setFixedSize(16, 16)
        btn_close.set_clickable(DS.PRIMARY)
        btn_close.setToolTip("关闭 (Esc)")
        btn_close.mousePressEvent = lambda e: self.close()  # type: ignore[method-assign]
        head.addWidget(self.lab_title)
        head.addStretch(1)
        head.addWidget(btn_close)
        self.body.addLayout(head)
        self._head_host = head

        self._drag_from: QPoint | None = None
        self._apply_style()

    def _apply_style(self) -> None:
        d = DS
        fam = get_config().ui.font_family
        self.setStyleSheet(f"""
            #popCard {{
                background: {d.BG};
                border: 1px solid {d.LINE};
                border-radius: {d.MD3_CARD_RADIUS}px;
            }}
            #popTitle {{ color: {d.TEXT}; font-size: 13px; font-weight: 600;
                         font-family: "{fam}"; }}
            QLabel {{ color: {d.TEXT_SUB}; font-size: 12px;
                      font-family: "{fam}"; }}
            QLineEdit {{
                background: {d.MD3_SURFACE_CONTAINER}; color: {d.TEXT};
                border: 1px solid {d.LINE}; border-radius: 6px;
                padding: 3px 8px; font-size: 12px; font-family: "{fam}";
                selection-background-color: {d.PRIMARY_SOFT};
            }}
            QLineEdit:focus {{ border: 1px solid {d.PRIMARY}; }}
            #btnPrimary {{
                background: {d.PRIMARY}; color: {d.SEL_TEXT};
                border: none; border-radius: 6px; padding: 5px 14px;
                font-size: 12px; font-weight: 600; font-family: "{fam}";
            }}
            #btnPrimary:hover {{ background: {d.PRIMARY_HI}; }}
            #btnGhost {{
                background: transparent; color: {d.TEXT_SUB};
                border: 1px solid {d.LINE}; border-radius: 6px;
                padding: 5px 12px; font-size: 12px; font-family: "{fam}";
            }}
            #btnGhost:hover {{ background: {d.BG_HOVER}; }}
        """)

    # ---- 标题行拖动 ----
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if (self._drag_from is not None
                and e.buttons() & Qt.MouseButton.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._drag_from = None

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)


# ================================================================
# ① 加词条弹窗（D5：＋ → 独立小窗，三字段，✓ 自关）
# ================================================================
class AddWordPopup(_CardPopup):
    added = Signal(str)               # 成功添加的中文词

    def __init__(self, engine, parent=None):
        super().__init__("添加词条", width=248, parent=parent)
        self.engine = engine

        form = QVBoxLayout()
        form.setSpacing(6)
        self.ed_zh = QLineEdit()
        self.ed_zh.setPlaceholderText("中文术语，如：氮化处理")
        self.ed_en = QLineEdit()
        self.ed_en.setPlaceholderText("英文，如：Nitriding")
        self.ed_brief = QLineEdit()
        self.ed_brief.setPlaceholderText("简述，如：表面硬化工艺")
        for lab, ed in (("中文", self.ed_zh), ("英文", self.ed_en),
                        ("简述", self.ed_brief)):
            row = QHBoxLayout()
            row.setSpacing(6)
            t = QLabel(lab)
            t.setFixedWidth(30)
            row.addWidget(t)
            row.addWidget(ed, 1)
            form.addLayout(row)
        self.body.addLayout(form)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        row2 = QHBoxLayout()
        row2.addStretch(1)
        self.btn_add = QPushButton("添加")
        self.btn_add.setObjectName("btnPrimary")
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.clicked.connect(self._submit)
        row2.addWidget(self.btn_add)
        self.body.addLayout(row2)

        self.ed_zh.returnPressed.connect(self._submit)
        self.ed_en.returnPressed.connect(self._submit)
        self.ed_brief.returnPressed.connect(self._submit)

    def reset(self) -> None:
        for ed in (self.ed_zh, self.ed_en, self.ed_brief):
            ed.clear()
        self.status.setText("")
        self.btn_add.setEnabled(True)
        self.ed_zh.setFocus()

    def _submit(self) -> None:
        zh = self.ed_zh.text().strip()
        if not zh:
            self.status.setStyleSheet(f"color: {DS.ERR};")
            self.status.setText("中文术语不能为空")
            return
        if self.engine is None:
            self.status.setStyleSheet(f"color: {DS.ERR};")
            self.status.setText("引擎未就绪，无法添加")
            return
        try:
            self.engine.add_custom(
                zh=zh,
                en=self.ed_en.text().strip(),
                brief=self.ed_brief.text().strip(),
                scene_desc="",
                profession=self.engine.config.profile.get(
                    "active_profession", ""),
            )
            udir = Path(self.engine.config.user_data_dir)
            self.engine.lexicons.save_custom(udir / "custom_terms.json")
        except Exception as e:
            self.status.setStyleSheet(f"color: {DS.ERR};")
            self.status.setText(f"添加失败：{e}")
            return
        self.status.setStyleSheet(f"color: {DS.OK};")
        self.status.setText(f"✓ 已加入：{zh}")
        self.btn_add.setEnabled(False)
        self.added.emit(zh)
        QTimer.singleShot(800, self.close)


# ================================================================
# ② 行业弹窗（D3：一级行业，点选即应用）
# ================================================================
class _IndRow(QFrame):
    picked = Signal(str)

    def __init__(self, key: str, name: str, checked: bool, parent=None):
        super().__init__(parent)
        self.key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(6)
        dot = QLabel("●" if checked else "○")
        dot.setFixedWidth(12)
        dot.setStyleSheet(
            f"color: {DS.PRIMARY};" if checked else f"color: {DS.TEXT_FAINT};")
        lab = QLabel(name)
        lab.setStyleSheet(
            f"color: {DS.TEXT};" if checked else f"color: {DS.TEXT_SUB};")
        lay.addWidget(dot)
        lay.addWidget(lab, 1)
        self.setStyleSheet(
            f"_IndRow {{ border-radius: 5px; }}"
            f"_IndRow:hover {{ background: {DS.BG_HOVER}; }}")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.key)


class IndustryPopup(_CardPopup):
    picked = Signal(str)              # 选中的行业 key

    def __init__(self, current_key: str = "", parent=None):
        super().__init__("行业范围", width=228, parent=parent)

        tip = QLabel("选择所在行业，候选按行业词库加权。")
        tip.setWordWrap(True)
        self.body.addWidget(tip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(330)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(1)

        try:
            from core.hierarchy import INDUSTRIES
        except Exception:
            INDUSTRIES = []
        last_group = None
        for ind in INDUSTRIES:
            g = ind.group or "其他"
            if g != last_group:
                cap = QLabel(g)
                cap.setStyleSheet(
                    f"color: {DS.TEXT_MUTED}; font-size: 10px;"
                    f" padding-top: 6px;")
                lay.addWidget(cap)
                last_group = g
            row = _IndRow(ind.key, ind.name, ind.key == current_key)
            row.picked.connect(self._on_pick)
            lay.addWidget(row)
        lay.addStretch(1)
        scroll.setWidget(inner)
        self.body.addWidget(scroll)

    def _on_pick(self, key: str) -> None:
        self.picked.emit(key)
        self.close()


# ================================================================
# ③ AI 增强弹窗（D3：Ⓐ → 开关 + 接口 + Key）
# ================================================================
class AiPopup(_CardPopup):
    saved = Signal(dict)              # {enabled, base_url, model, api_key(密文), proxy}
    free_toggled = Signal(bool)       # v2.6：无 Key 时的免费在线翻译

    def __init__(self, llm_cfg: dict, parent=None, free_on: bool = True,
                 proxy: str = ""):
        super().__init__("AI 增强", width=320, parent=parent)
        cfg = llm_cfg or {}

        self.chk_llm = Md3Switch("启用 AI 增强推荐")
        self.chk_llm.setChecked(bool(cfg.get("enabled")))
        self.body.addWidget(self.chk_llm)

        # v2.6：没有 API Key 也能翻译 —— 走免费公共接口（会发送选中文本）
        self.chk_free = Md3Switch("没有 Key 时用免费在线翻译")
        self.chk_free.setChecked(bool(free_on))
        self.chk_free.toggled.connect(self._on_free_toggled)
        self.body.addWidget(self.chk_free)
        free_tip = QLabel("开启后会把「选中的文字」发送到公开服务。"
                          "默认只走境内两家：腾讯交互翻译(TranSmart)、有道；"
                          "MyMemory / Google 等境外端点默认关闭，"
                          "需在配置里显式开启 allow_overseas 才会兜底。"
                          "纯本地/保密场景请保持关闭。"
                          "若提示「网络受限」，在下面「代理」填本机代理地址即可。")
        free_tip.setWordWrap(True)
        free_tip.setStyleSheet(f"color: {DS.TEXT_FAINT}; font-size: 10px;")
        self.body.addWidget(free_tip)

        form = QVBoxLayout()
        form.setSpacing(6)
        self.ed_base_url = QLineEdit()
        self.ed_base_url.setPlaceholderText("https://open.bigmodel.cn/api/paas/v4")
        self.ed_model = QLineEdit()
        self.ed_model.setPlaceholderText("glm-4-flash")
        self.ed_api_key = QLineEdit()
        self.ed_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_api_key.setPlaceholderText("API Key（DPAPI 加密后存本机配置）")
        # v3.0.1：网络代理。企业内网/防火墙常禁止程序直连外网，
        # 此时 AI 与免费翻译都会报 WinError 10013/18013，填这里即可。
        self.ed_proxy = QLineEdit()
        self.ed_proxy.setPlaceholderText("127.0.0.1:7890（留空=自动）")
        self.ed_base_url.setText(cfg.get("base_url", ""))
        self.ed_model.setText(cfg.get("model", ""))
        # S03：回显时把 DPAPI 密文解密成明文（仍是 Password 掩码显示）；
        # 老用户配置里的明文 Key 由 unprotect 原样返回，兼容不丢。
        self.ed_api_key.setText(
            _secrets.unprotect(str(cfg.get("api_key", "") or "")))
        self.ed_proxy.setText(proxy or "")
        for lab, ed in (("接口", self.ed_base_url),
                        ("模型", self.ed_model),
                        ("Key", self.ed_api_key),
                        ("代理", self.ed_proxy)):
            row = QHBoxLayout()
            row.setSpacing(6)
            t = QLabel(lab)
            t.setFixedWidth(30)
            row.addWidget(t)
            row.addWidget(ed, 1)
            form.addLayout(row)
        self.body.addLayout(form)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self._refresh_status()
        self.chk_llm.toggled.connect(lambda _: self._refresh_status())
        self.body.addWidget(self.status)

        tip = QLabel("默认本地推荐（离线、零延迟）。网络异常自动回退，不影响输入。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {DS.TEXT_MUTED}; font-size: 11px;")
        self.body.addWidget(tip)

        row2 = QHBoxLayout()
        row2.addStretch(1)
        btn = QPushButton("保存并应用")
        btn.setObjectName("btnPrimary")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._save)
        row2.addWidget(btn)
        self.body.addLayout(row2)

    def _refresh_status(self) -> None:
        on = self.chk_llm.isChecked()
        has_key = bool(self.ed_api_key.text().strip())
        if on and has_key:
            self.status.setStyleSheet(f"color: {DS.AI};")
            self.status.setText("状态：AI 增强已启用")
        elif on:
            self.status.setStyleSheet(f"color: {DS.WARN};")
            self.status.setText("已勾选启用，但未填 API Key，仍用本地推荐")
        else:
            self.status.setStyleSheet(f"color: {DS.TEXT_SUB};")
            self.status.setText("状态：使用本地智能推荐（离线，零延迟）")

    def _on_free_toggled(self, on: bool) -> None:
        self.free_toggled.emit(bool(on))

    def _save(self) -> None:
        # S03：落盘前用 DPAPI 加密，payload 键名不变，写进 config.json 的
        # 不含明文；protect 失败返回空串（宁可让用户重填，也不写明文）。
        raw_key = self.ed_api_key.text().strip()
        self.saved.emit({
            "enabled": self.chk_llm.isChecked(),
            "base_url": self.ed_base_url.text().strip(),
            "model": self.ed_model.text().strip(),
            "api_key": _secrets.protect(raw_key) if raw_key else "",
            "proxy": self.ed_proxy.text().strip(),
        })
        self.close()


# ================================================================
# ④ 关于弹窗（v3.0.4：正常关于页 —— 版本 / 定位 / 数据来源 / 许可）
# ================================================================
class AboutPopup(_CardPopup):
    def __init__(self, version: str = "3.0", parent=None):
        super().__init__("关于", width=268, parent=parent)

        name = QLabel("RlonIME")
        name.setStyleSheet(
            f"color: {DS.TEXT}; font-size: 17px; font-weight: 700;")
        sub = QLabel(f"v{version} · 工程领域中文输入法")
        sub.setStyleSheet(f"color: {DS.TEXT_MUTED}; font-size: 11px;")
        self.body.addWidget(name)
        self.body.addWidget(sub)

        desc = QLabel(
            "贴着光标工作的输入法：敲拼音直接出工程术语，选中中文一键译成"
            "规范英文。术语优先、普通词照常，候选条永不抢焦点。")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {DS.TEXT_SUB}; font-size: 11px;")
        self.body.addWidget(desc)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {DS.LINE_SOFT};")
        self.body.addWidget(line)

        for text in (
            "作者：Rlon　·　许可：MIT License",
            "通用词库：CC-CEDICT (CC BY-SA 4.0) × jieba (MIT)",
            "术语库：本项目整理（15 个行业）",
        ):
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setStyleSheet(f"color: {DS.TEXT_FAINT}; font-size: 10px;")
            self.body.addWidget(lab)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_dir = QPushButton("打开配置目录")
        btn_dir.setObjectName("btnGhost")
        btn_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_dir.clicked.connect(self._open_dir)
        btn_ok = QPushButton("关闭")
        btn_ok.setObjectName("btnPrimary")
        btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ok.clicked.connect(self.close)
        row.addWidget(btn_dir)
        row.addWidget(btn_ok)
        self.body.addLayout(row)

    def _open_dir(self) -> None:
        try:
            from core.paths import user_data_root
            os.startfile(str(user_data_root()))     # noqa: S606
        except Exception:
            try:
                from core.config import get_config
                os.startfile(str(get_config().path.parent))   # noqa: S606
            except Exception:
                pass
