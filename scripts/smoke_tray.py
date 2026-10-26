"""v2.5 托盘菜单 + ✕ 语义冒烟（必须真实平台跑，offscreen 会假失败）

验的是三条用户拍板的交互契约：
  1. 托盘右键有菜单（显示/隐藏 · 暂停/恢复 · 快捷键速查 · 配置目录 · 退出）
  2. 候选条 ✕ = **收起进托盘**，进程与键盘钩子继续活着（退出只走托盘菜单）
  3. 菜单文案随状态同步，不出现"已经暂停了还写暂停"的陈旧文案

    python scripts/smoke_tray.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          f"{'' if cond else '  ' + str(extra)}")


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from main import ZhiyuApp

    app = QApplication([])
    zh = ZhiyuApp()                     # 不拿单实例守卫，测完自己 quit
    try:
        # ---------------- [1] 菜单存在且完整 ----------------
        print("[1] 托盘右键菜单")
        menu = zh.tray.contextMenu()
        ck("托盘已挂右键菜单", menu is not None)
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        ck("菜单 5 项（不含分隔线）", len(labels) == 5, labels)
        for want in ("候选条", "拼音接管", "快捷键速查", "配置目录", "退出"):
            ck(f"含「{want}」", any(want in x for x in labels), labels)

        # ---------------- [2] 文案随状态同步 ----------------
        print("[2] 菜单文案跟随状态")
        zh._sync_tray_menu()
        ck("初始为「暂停拼音接管」（当前开启）",
           zh.act_pause.text() == "暂停拼音接管", zh.act_pause.text())
        zh.toggle_ime(False)
        zh._sync_tray_menu()
        ck("暂停后变「恢复拼音接管」",
           zh.act_pause.text() == "恢复拼音接管", zh.act_pause.text())
        zh.toggle_ime(True)
        zh._sync_tray_menu()
        ck("恢复后回到「暂停」", zh.act_pause.text() == "暂停拼音接管",
           zh.act_pause.text())

        # ---------------- [3] ✕ = 收起，不是退出 ----------------
        print("[3] ✕ 收起进托盘（U02）")
        zh.bar.show_input("k")
        app.processEvents()
        ck("候选条已显示", zh.bar.isVisible())
        zh._sync_tray_menu()
        ck("显示时菜单写「隐藏候选条」",
           zh.act_show.text() == "隐藏候选条", zh.act_show.text())
        hooked_before = zh.ime.hook.running
        zh.bar.quit_clicked.emit()          # 等价于点 ✕
        app.processEvents()
        ck("✕ 后候选条已收起", zh.bar.isVisible() is False)
        ck("✕ 后进程仍活着（没有退出）", zh.ime.enabled is True)
        ck("✕ 后键盘钩子仍在", zh.ime.hook.running == hooked_before,
           f"{hooked_before} -> {zh.ime.hook.running}")
        zh._sync_tray_menu()
        ck("收起后菜单写「显示候选条」",
           zh.act_show.text() == "显示候选条", zh.act_show.text())

        # ---------------- [3b] 持久化接线 + 收起不被击穿（v2.7 QA） ----------------
        print("[3b] 位置落盘接线 / 收起态")
        ck("bar.ui_persist 已接线（否则拖动位置永不落盘）",
           callable(getattr(zh.bar, "ui_persist", None)))
        zh.bar.show_input("k")
        app.processEvents()
        zh.hide_to_tray()
        app.processEvents()
        ck("✕ 收起后标记 collapsed", zh.bar.collapsed is True)
        zh.bar.show_hint("后台提示")
        app.processEvents()
        ck("收起后任何提示都不弹条", zh.bar.isVisible() is False)

        # ---------------- [4] 托盘单击 = 显隐（I06） ----------------
        print("[4] 托盘单击显隐")
        from PySide6.QtWidgets import QSystemTrayIcon
        zh._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        app.processEvents()
        ck("单击托盘 → 候选条出现", zh.bar.isVisible())
        zh._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        app.processEvents()
        ck("再单击托盘 → 候选条收起", zh.bar.isVisible() is False)

        # ---------------- [5] 退出入口仍在菜单 ----------------
        print("[5] 退出入口")
        ck("退出动作存在且可点",
           zh.act_quit.isEnabled() and "退出" in zh.act_quit.text(),
           zh.act_quit.text())
        # ✕ 不再退出已经由 [3] 的行为断言覆盖（钩子仍在、进程仍在）
    finally:
        zh.quit()

    print()
    print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("  失败项:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
