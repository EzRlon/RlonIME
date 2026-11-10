"""
RlonIME — 首启引导 / 快捷键速查（一页图文，最小实现）

三个入口：
1. 首启引导：%APPDATA%\\RlonIME\\first_run.done 不存在时由 main 弹出
   （模态，勾选「不再显示」关闭后写标记；标记放用户目录，卸载不删、
   重装不重复弹）。
2. 快捷键速查：WelcomeDialog().open_reference() 可随时重开（纯展示）。
3. 免费翻译 opt-in 同意门：request_free_translate_consent()（S14），由 main
   在首次启用前调用；只返回 bool，是否持久化由主程序决定，本模块不读写配置。

截图复用 docs/bar_*.png（复制为 data/guide/*.png，随 spec datas 打包）。
候选条 UI 改版时需同步更新 data/guide/（维护项）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import DEFAULT_HOTKEYS, get_config   # noqa: E402
from core.paths import data_root, user_data_root      # noqa: E402
from ui.design import DS                              # noqa: E402

GUIDE_IMAGES: tuple[tuple[str, str], ...] = (
    ("bar_input.png", "敲拼音 · 专业术语即刻出候选"),
    ("bar_result.png", "选中即译 · 就地替换成英文"),
    ("bar_hint.png", "纠正入库 · 下次直接命中"),
)


def marker_path() -> Path:
    """首启标记：空文件即标记；走 user_data_root()，卸载不删。"""
    return user_data_root() / "first_run.done"


def request_free_translate_consent(parent: QWidget | None = None) -> bool:
    """S14 首次 opt-in 同意门：模态询问是否启用「免费在线翻译」。

    文案列出全部接收方并说明「选中的文字会发送到公开服务，其中含境外端点」；
    默认按钮是「暂不开启」，关闭 / Esc / 点「暂不开启」一律返回 False，
    绝不默认同意。本函数只返回 bool，是否持久化由主程序负责，这里不读写配置。
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("启用免费在线翻译？")
    dlg.setModal(True)
    dlg.setMinimumWidth(470)

    root = QVBoxLayout(dlg)
    root.setContentsMargins(22, 18, 22, 16)
    root.setSpacing(12)

    title = QLabel("是否启用「免费在线翻译」？")
    title.setStyleSheet(
        f"color: {DS.TEXT}; font-size: 15px; font-weight: 700;")
    root.addWidget(title)

    body = QLabel(
        "启用后，你「选中的文字」会发送到以下公开翻译服务：\n"
        "  · 腾讯交互翻译 TranSmart（境内）\n"
        "  · 有道翻译（境内）\n"
        "  · MyMemory（境外）\n"
        "  · Google 免 Key 端点（境外）\n"
        "选中的文字会发送到公开服务，其中含境外端点；图纸、合同、病历等"
        "敏感内容请谨慎。该功能默认不开启，之后可在候选条 Ⓐ 里随时关闭。")
    body.setWordWrap(True)
    body.setStyleSheet(f"color: {DS.TEXT_SUB}; font-size: 12px;")
    root.addWidget(body)

    warn = QLabel("提示：选中的文字会离开发送到公开服务，其中含境外端点。")
    warn.setWordWrap(True)
    warn.setStyleSheet(
        f"color: {DS.WARN}; background: {DS.WARN_SOFT};"
        f" border-radius: 6px; padding: 8px 10px; font-size: 12px;")
    root.addWidget(warn)

    state = {"on": False}

    def _enable() -> None:
        state["on"] = True
        dlg.accept()

    foot = QHBoxLayout()
    foot.addStretch(1)
    btn_on = QPushButton("开启")
    btn_on.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_on.setStyleSheet(
        f"QPushButton {{ background: {DS.PRIMARY}; color: {DS.SEL_TEXT};"
        f" border: none; border-radius: 6px; padding: 7px 18px;"
        f" font-weight: 600; }}"
        f"QPushButton:hover {{ background: {DS.PRIMARY_HI}; }}")
    btn_off = QPushButton("暂不开启")
    btn_off.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_off.setStyleSheet(
        f"QPushButton {{ background: transparent; color: {DS.TEXT_SUB};"
        f" border: 1px solid {DS.LINE}; border-radius: 6px;"
        f" padding: 7px 16px; }}"
        f"QPushButton:hover {{ background: {DS.BG_HOVER}; }}")
    btn_off.setDefault(True)        # 默认按钮 = 「暂不开启」：绝不默认同意
    btn_off.setFocus()
    btn_on.clicked.connect(_enable)
    btn_off.clicked.connect(dlg.reject)
    foot.addWidget(btn_on)
    foot.addWidget(btn_off)
    root.addLayout(foot)

    dlg.exec()
    return bool(state["on"])


def _fmt(spec: str) -> str:
    """ctrl+alt+t → Ctrl+Alt+T（仅展示用）。"""
    return "+".join(p.capitalize() for p in spec.split("+") if p)


def _hotkey_lines() -> list[str]:
    """三条核心快捷键文案，一律从 config.hotkey() 取（唯一来源）。"""
    cfg = get_config()
    hk_translate = _fmt(cfg.hotkey("translate_selection",
                                   DEFAULT_HOTKEYS["translate_selection"]))
    hk_mark = _fmt(cfg.hotkey("mark_correction",
                              DEFAULT_HOTKEYS["mark_correction"]))
    ime_cfg = getattr(cfg, "ime", None) or {}
    hk_toggle = _fmt(str(ime_cfg.get("toggle_hotkey", "ctrl+space")))
    return [
        f"{hk_toggle}　开关键盘接管，随时交还系统",
        f"{hk_translate}　选中即译：选中中文 → 就地替换成英文",
        f"{hk_mark}　标记纠正：改好的译文入库，下次直接命中",
    ]


class WelcomeDialog(QDialog):
    """一页式欢迎对话框：三张截图 + 三条核心快捷键 + 「不再显示」。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用 RlonIME")
        self.setModal(True)
        self.setMinimumWidth(680)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)

        title = QLabel("欢迎使用 RlonIME")
        tf = QFont(self.font())
        tf.setPointSize(16)
        tf.setBold(True)
        title.setFont(tf)
        root.addWidget(title)

        subtitle = QLabel("贴着光标工作的专业词汇助手，三个动作上手：")
        subtitle.setStyleSheet(f"color: {DS.TEXT_MUTED};")
        root.addWidget(subtitle)

        # ---- 三张引导截图（并列） ----
        shots = QHBoxLayout()
        shots.setSpacing(10)
        guide_dir = data_root() / "guide"
        for filename, caption in GUIDE_IMAGES:
            cell = QFrame()
            cell.setStyleSheet(
                f"QFrame {{ background: {DS.BG_BOTTOM};"
                f" border: 1px solid {DS.LINE}; border-radius: 8px; }}")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(8, 8, 8, 8)
            cl.setSpacing(6)
            img = QLabel()
            img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pix = QPixmap(str(guide_dir / filename))
            if not pix.isNull():
                img.setPixmap(pix.scaledToWidth(
                    190, Qt.TransformationMode.SmoothTransformation))
            else:
                img.setText(f"（截图缺失：{filename}）")
                img.setStyleSheet(f"color: {DS.TEXT_FAINT};")
            cap = QLabel(caption)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setWordWrap(True)
            cap.setStyleSheet(f"color: {DS.TEXT_SUB}; font-size: 11px;")
            cl.addWidget(img)
            cl.addWidget(cap)
            shots.addWidget(cell)
        root.addLayout(shots)

        # ---- 三条核心快捷键 ----
        keys_box = QFrame()
        keys_box.setStyleSheet(
            f"QFrame {{ background: {DS.PRIMARY_SOFT}; border-radius: 8px; }}")
        kl = QVBoxLayout(keys_box)
        kl.setContentsMargins(14, 10, 14, 10)
        kl.setSpacing(6)
        for line in _hotkey_lines():
            row = QLabel(line)
            row.setStyleSheet(
                f"color: {DS.MD3_ON_PRIMARY_CONTAINER}; font-size: 13px;")
            kl.addWidget(row)
        root.addWidget(keys_box)

        # ---- 底部：不再显示 + 开始使用 ----
        foot = QHBoxLayout()
        self.dont_show_again = QCheckBox("不再显示")
        self.dont_show_again.setChecked(True)
        foot.addWidget(self.dont_show_again, 1)
        btn = QPushButton("开始使用")
        btn.setDefault(True)
        btn.setStyleSheet(
            f"QPushButton {{ background: {DS.PRIMARY}; color: {DS.SEL_TEXT};"
            f" border: none; border-radius: 6px; padding: 8px 22px;"
            f" font-weight: 600; }}"
            f"QPushButton:hover {{ background: {DS.PRIMARY_HI}; }}")
        btn.clicked.connect(self.accept)
        foot.addWidget(btn)
        root.addLayout(foot)

    # ---------- 入口 ----------
    @classmethod
    def exec_first_run(cls, marker: Path | None = None,
                       parent: QWidget | None = None) -> bool:
        """首启引导：模态弹出；勾选「不再显示」关闭后写标记。返回是否已写标记。"""
        dlg = cls(parent)
        dlg.exec()
        if not dlg.dont_show_again.isChecked():
            return False
        marker = marker or marker_path()
        try:
            marker.write_text("", encoding="utf-8")
        except Exception as e:
            print(f"[WARN] 首启标记写入失败: {e}", file=sys.stderr)
            return False
        return True

    def open_reference(self) -> None:
        """托盘「快捷键速查…」重开：纯展示，不写首启标记。"""
        self.dont_show_again.hide()
        self.exec()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    WelcomeDialog.exec_first_run()
