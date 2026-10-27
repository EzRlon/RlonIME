"""
RlonIME — 首启引导 / 快捷键速查（一页图文，最小实现）

两个入口：
1. 首启引导：%APPDATA%\\RlonIME\\first_run.done 不存在时由 main 弹出
   （模态，勾选「不再显示」关闭后写标记；标记放用户目录，卸载不删、
   重装不重复弹）。
2. 快捷键速查：WelcomeDialog().open_reference() 可随时重开（纯展示）。

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
