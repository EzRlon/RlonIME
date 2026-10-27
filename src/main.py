"""
RlonIME · 专业领域词汇助手 — 主程序入口

产品定位（v2）：
    不是「又一个输入法」，而是**贴着光标工作的词汇助手**。两条主线：

    ① 中文输入增强 —— 全系统接管键盘，敲拼音出专业术语候选，
       数字/空格选词，Tab 上屏英文，Ctrl+数字 上屏「中文 English (标准号)」。

    ② 选中即译 + 记忆闭环 —— 选中中文片段按 Ctrl+Alt+Q，
       就地替换成符合当前行业与整句语境的英文；译得不满意就改，
       改完按 Ctrl+Alt+W 标记，这条译法永久进入本机记忆库，
       下次同样的中文直接命中，不再走 AI。

架构分层（自下而上，互不越界）：
    core/ime.py        全局键盘钩子 + 拼音状态机 + SendInput 上屏
    core/capture.py    统一取写层（剪贴板取字 / SendInput 写回）
    core/memory.py     翻译记忆库（%APPDATA%，不删不传）
    core/llm.py        大模型客户端（可选，后台线程，失败静默降级）
    core/translate.py  五级翻译管线 + 术语强制对齐
    ui/bar.py          横向候选条（不抢焦点，v2.2 顶行图标列）
    ui/popups.py       配套弹窗（加词条/行业/AI/关于；设置中心已并入）

v2.2：托盘净化（仅图标）；显示名 RlonIME，内部进程名与
数据目录保留 ZhiyuIME（老用户数据免迁移）。
v2.3.1：多显示器下候选条/弹窗按「光标所在屏幕」贴合（修复副屏被拉回主屏）；
托盘双击不再退出（防误触），改为显示/隐藏候选条，退出走候选条 ✕。
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QObject, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import crashlog                    # noqa: E402
from core.capture import read_selection      # noqa: E402
from core.config import (DEFAULT_HOTKEYS, Config,    # noqa: E402
                         get_config)
from core.engine import TermEngine           # noqa: E402
from core.free_translate import FreeTranslator  # noqa: E402
from core.ime import ImeController, foreground_is_own_process   # noqa: E402
from core.llm import PRESETS, LLMClient      # noqa: E402
from core.memory import TranslationMemory    # noqa: E402
from core.paths import bootstrap, user_data_root  # noqa: E402
from core.single_instance import SingleInstanceGuard  # noqa: E402
from core.sysime import system_ime_composing  # noqa: E402
from core.translate import Translator        # noqa: E402
from scene.detector import (SceneDetector, get_foreground_hwnd,  # noqa: E402
                            get_process_name, get_window_pid)
from ui.bar import CandidateBar              # noqa: E402
from ui.floatbtn import FloatTranslateButton, SelectionWatcher  # noqa: E402
from ui.inserter import Inserter             # noqa: E402
from ui.onboarding import WelcomeDialog, marker_path  # noqa: E402
from ui.popups import (AboutPopup, AddWordPopup,      # noqa: E402
                       AiPopup, IndustryPopup)

# CAD 直通默认进程表（S1-A / M2）：AutoCAD / 中望 / 浩辰 / CAXA 电子图板 /
# BricsCAD。config.json 的 ime.cad_processes 可覆盖。
DEFAULT_CAD_PROCESSES = [
    "acad.exe", "acadlt.exe", "zwcad.exe", "gcad.exe", "bricscad.exe",
    "caxa.exe", "eb.exe", "caxadraft.exe",
]


def _has_cjk(text: str) -> bool:
    """是否含汉字（决定选中即译的方向）。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def make_tray_icon(state: str = "") -> QIcon:
    """纯代码绘制托盘图标（v2.2 D9）：浅色=翠青底白"工"字，深色=黑金版。

    v2.4.0（I07）：state="paused" / "cad" 时右下角叠一枚半透明琥珀点，
    让「为什么打不出中文」一眼可见（暂停接管 / CAD 直通）。
    """
    theme = str(getattr(get_config().ui, "theme", "light")).lower()
    dark = theme.startswith("dark")
    bg = QColor("#141519" if dark else "#0D9488")
    fg = QColor("#E3C15E" if dark else "#FFFFFF")
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    if dark:                                # 黑金版：金描边
        pen = QPen(fg)
        pen.setWidthF(3.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(5.5, 5.5, 53, 53, 12, 12)
    # "工"字：两横一竖（横画作拼音格线），圆头描边
    pen = QPen(fg)
    pen.setWidthF(6.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(18, 22, 46, 22)
    p.drawLine(32, 22, 32, 44)
    p.drawLine(16, 44, 48, 44)
    if state in ("paused", "cad"):
        # 右下角琥珀点：暂停接管 / CAD 直通（I07 状态可视化）
        dot = QColor("#E0A34B")
        dot.setAlpha(225)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, 90)))
        p.drawEllipse(44, 44, 18, 18)
        p.setBrush(QBrush(dot))
        p.drawEllipse(45, 45, 16, 16)
    p.end()
    return QIcon(pix)


class HotkeyBridge(QObject):
    """把后台线程事件安全投递到 Qt 主线程。"""

    cycle_scope = Signal()
    toggle_llm = Signal()


class ZhiyuApp:
    def __init__(self, guard: SingleInstanceGuard | None = None):
        # 单实例门卫（main() 在构建本对象前已完成 try_acquire）
        self.guard = guard
        # 测试开关：收到第二进程唤起命令时写标志文件（scripts/test_single_instance.py）
        self._selftest_raise_flag: str | None = None

        self.config = get_config()
        self.engine = TermEngine()
        # v2.3 通用词库后台预热（约 8 万条；未就绪时搜索自动跳过该层，不影响输入）
        threading.Thread(target=self._preload_common, daemon=True).start()
        self.detector = SceneDetector()
        self.inserter = Inserter()

        # ---- 词汇助手三件套 ----
        # v3.0.1：代理是 AI 与免费翻译共用的网络通道（assist.proxy 唯一来源）
        _proxy = str(getattr(self.config.assist, "proxy", "") or "")
        _llm_cfg = dict(getattr(self.config, "llm", {}) or {})
        if _proxy:
            _llm_cfg["proxy"] = _proxy
        self.llm = LLMClient(_llm_cfg)
        self.memory = TranslationMemory(
            threshold=float(getattr(self.config.assist, "fuzzy_threshold", 0.85)))
        self._free_enabled = bool(getattr(self.config.assist,
                                          "free_translate", True)) and not bool(
            getattr(self.config.assist, "local_only", False))
        self.free_translator = FreeTranslator(enabled=self._free_enabled,
                                              proxy=_proxy)
        self.translator = Translator(self.engine, self.memory, self.llm,
                                     self.memory.threshold,
                                     free=self.free_translator)

        # ---- 界面 ----
        self.bar = CandidateBar(self.engine, self.detector, self.inserter)
        self.bar.translator = self.translator
        self.bar.hotkey_handler = self.on_hotkey
        self.bar.committed.connect(self._on_committed)
        self.bar.open_add_word.connect(self.open_add_word)
        self.bar.open_industry.connect(self.open_industry)
        self.bar.open_ai.connect(self.open_ai)
        self.bar.open_about.connect(self.open_about)
        self.bar.ui_persist = self._persist      # 拖动/固定位置落盘（QA 阻断项）
        self.bar.pause_clicked.connect(self.toggle_ime)
        # v2.5（U02 用户拍板）：候选条 ✕ = 收起进托盘，不再直接退出。
        # 退出唯一入口是托盘右键菜单——误触零代价。
        self.bar.quit_clicked.connect(self.hide_to_tray)

        # ---- 输入内核（热键表来自 config.hotkey() 唯一来源）----
        ime_cfg = getattr(self.config, "ime", None) or {}
        self.ime = ImeController(enabled=bool(ime_cfg.get("enabled", True)),
                                 config=self.config)
        self.bar.attach_ime(self.ime)
        self._ime_hooked = self.ime.start()
        if self._ime_hooked:
            self.bar.prime_window()

        # ---- 异步翻译结果回收（子线程 -> 主线程）----
        self._results: queue.Queue = queue.Queue()
        self._pump = QTimer()
        self._pump.setInterval(50)
        self._pump.timeout.connect(self._drain_results)
        self._pump.start()
        self._pending: tuple[str, str] = ("", "")   # (中文原文, 上次译文)
        self._free_told = False                      # 免费翻译首用提示只弹一次
        self._last_follow = 0.0                      # 单击跟随去抖（双击会发两次）
        self._busy = False

        # v2.2 配套弹窗（懒建；设置中心已并入候选条顶行图标）
        self._popup_add: AddWordPopup | None = None
        self._popup_ind: IndustryPopup | None = None
        self._popup_ai: AiPopup | None = None
        self._popup_about: AboutPopup | None = None
        self._tray_state = ""
        self._tray_hint_shown = False
        self._setup_tray()

        # ---- v2.4.0 M2：CAD 直通（主线程轮询前台进程，钩子内绝不查进程）----
        ime_raw = dict(getattr(self.config, "ime", {}) or {})
        self._cad_enabled = bool(ime_raw.get("cad_passthrough", True))
        self._cad_procs = {str(p).strip().lower() for p in
                           (ime_raw.get("cad_processes")
                            or DEFAULT_CAD_PROCESSES) if str(p).strip()}
        self._cad_idle = float(ime_raw.get("cad_idle_seconds", 5) or 5)
        self.ime.core.cad_idle = self._cad_idle
        self._cad_poll = QTimer()
        self._cad_poll.setInterval(600)
        self._cad_poll.timeout.connect(self._poll_cad)
        self._cad_poll.start()

        # ---- v2.4.0 M4：选区悬浮「译」钮（拖选结束 → 光标旁浮出）----
        self.float_btn = FloatTranslateButton()
        self.float_btn.clicked.connect(self.translate_selection)
        self.sel_watch = SelectionWatcher()      # 强引用保活（定时器在它身上）
        self.sel_watch.selected.connect(self._on_drag_select)
        self.sel_watch.clicked.connect(self._on_click_follow)
        self.sel_watch.start()

        # 系统 IME 组合态指示：自动放行是瞬态（不改 enabled），
        # 与用户暂停是两条独立路径；tooltip 如实反映当前处于哪条。
        self._sysime_active = False
        self._sysime_poll = QTimer()
        self._sysime_poll.setInterval(500)
        self._sysime_poll.timeout.connect(self._poll_sysime)
        self._sysime_poll.start()

        if self.guard is not None:
            # PySide6 对 Python 可调用对象（绑定方法/ lambda）只持弱引用，
            # GC 后连接静默失效（实测复现：同一 emit 里 lambda 能收、
            # 未被持有的绑定方法收不到）。必须自己持有强引用。
            self._raise_slot = self.on_raise_requested
            self.guard.raise_requested.connect(self._raise_slot)
            # 启动竞态：构建期间到达的唤起命令，接线后补投
            pending = self.guard.take_pending_command()
            if pending:
                QTimer.singleShot(
                    0, lambda c=pending: self._raise_slot(c))

    def _poll_sysime(self) -> None:
        active = bool(self.ime.enabled) and system_ime_composing()
        if active == self._sysime_active:
            return
        self._sysime_active = active
        self._refresh_tray_state()

    # ================= CAD 直通（M2 / S1-A） =================
    def _poll_cad(self) -> None:
        """前台是 AutoCAD / CAXA / ZWCAD 时默认不吞字母（L/C/M/E 是命令）。"""
        try:
            self.ime.tick_cad()          # 空闲回落即使不按键也及时生效
            if not self._cad_enabled:
                if self.ime.cad_mode:
                    self.ime.set_cad_mode(False)
                    self._refresh_tray_state()
                return
            if foreground_is_own_process():
                return                   # 自己的弹窗/候选条，不算 CAD
            name = get_process_name(get_window_pid(get_foreground_hwnd()))
            want = bool(name) and name.strip().lower() in self._cad_procs
            if want != self.ime.cad_mode:
                self.ime.set_cad_mode(want)
                self._refresh_tray_state()
        except Exception:
            pass

    def _refresh_tray_state(self) -> None:
        """托盘图标/提示 = 暂停 > CAD 直通 > 系统 IME 放行 > 常态（I07）。"""
        paused = not bool(self.ime.enabled)
        cad = bool(self.ime.cad_mode) and not paused
        state = "paused" if paused else ("cad" if cad else "")
        if state != self._tray_state:
            self._tray_state = state
            try:
                self.tray.setIcon(make_tray_icon(state))
            except Exception:
                pass
        if paused:
            tip = "RlonIME · 拼音接管已暂停（Ctrl+Space 恢复）"
        elif cad:
            tip = ("RlonIME · CAD 直通中（字母已交给 CAD，"
                   "Ctrl+Space 临时输入中文）")
        elif self._sysime_active:
            tip = "RlonIME · 自动放行中（系统输入法组合中）"
        else:
            tip = f"{self.config.app_name} · 作者 Rlon（单击托盘显示候选条）"
        self.tray.setToolTip(tip)

    # ================= 悬浮「译」钮（M4 / N1） =================
    def _on_click_follow(self, pos: QPoint) -> None:
        """点进新的输入位置（换窗口/换输入框）→ 候选条立刻跟过去。

        以前只在"敲第一个键"时定位，于是"点了输入框、条子还在上一处"
        看起来像不跟随；钉住（📌）时按用户意图不跟随。
        """
        try:
            if not self.bar.isVisible() or self.bar.bar_pinned:
                return
            # 只在"正在拼字"时跟随：否则任务栏/桌面/浏览器随便一点，
            # 候选条都会被甩到那个位置（QA 抓到）。双击=两次 clicked，去抖。
            if not self.ime.composing:
                return
            now = time.monotonic()
            if now - self._last_follow < 0.2:
                return
            self._last_follow = now
            self.bar.follow_caret()
        except Exception:
            pass

    def _on_drag_select(self, pos: QPoint) -> None:
        """拖选结束：光标旁浮出「译」钮（点它才翻译，不自动动选区）。"""
        try:
            self.float_btn.popup_at(pos)
        except Exception:
            pass

    def _preload_common(self) -> None:
        """后台预热通用词库（v2.3）；失败静默降级（仅少了常规词候选）。"""
        try:
            n = self.engine.lexicons.load_common()
            if n:
                print(f"[INFO] 通用词库就绪：{n} 条", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] 通用词库载入失败: {e}", file=sys.stderr)

    # ================= 单实例唤起 =================
    def on_raise_requested(self, command: str) -> None:
        """第二进程递来的命令：气泡提示（设置中心已并入候选条，见 v2.2）。"""
        if self._selftest_raise_flag:
            try:
                Path(self._selftest_raise_flag).write_text(
                    command or "raise", encoding="utf-8")
            except Exception:
                pass
        self.tray.showMessage(
            self.config.app_name,
            "RlonIME 已在运行（双击托盘图标可显示候选条）",
            QSystemTrayIcon.MessageIcon.Information, 2500)

    # ================= 词汇助手：选中即译（双向，N1） =================
    def translate_selection(self) -> None:
        """Ctrl+Alt+Q 或悬浮「译」钮：取选区 → 判方向 → 就地替换。

        中→英走五级管线（记忆库/术语/AI + 术语强制对齐）；
        英→中走「术语反查 + AI 回译」（v2.4.0 新增）。
        """
        if self._busy:
            self.bar.show_hint("上一句还在翻译，请稍候…", "warn")
            return
        assist = self.config.assist
        min_chars = int(getattr(assist, "min_chars", 2))

        cap = read_selection()
        if not cap.ok:
            self.bar.show_hint(
                f"请先选中要翻译的文字，再按 {self._hk('translate_selection')}"
                "（或点选区旁的「译」钮）", "warn")
            return
        src = cap.text
        if len(src) < min_chars:
            self.bar.show_hint("选中内容太短，看不出语境", "warn")
            return

        back = not _has_cjk(src)          # 纯英文（含句中夹符号）→ 英→中
        self._busy = True
        self.bar.show_hint("正在回译…" if back else "正在翻译…", "ai")

        def _done(result) -> None:
            self._results.put(("translation", result))

        allow_llm = not bool(getattr(assist, "local_only", False))
        if back:
            local = self.translator.translate_back_async(
                src, self.bar.scene, _done, allow_llm=allow_llm)
        else:
            local = self.translator.translate_async(
                src, self.bar.scene, _done, allow_llm=allow_llm)
        if local is not None:                # 本地命中，零等待
            self._busy = False
            self._apply_translation(local)

    def _apply_translation(self, result) -> None:
        if not result.ok:
            self.bar.show_hint(result.error or "翻译失败", "err")
            return
        assist = self.config.assist
        from core.capture import replace_selection
        ok = replace_selection(result.text)
        if not ok:
            self.bar.show_hint("上屏失败，目标窗口可能不接受输入", "err")
            return
        if result.source == "free" and not self._free_told:
            self._free_told = True
            self.tray.showMessage(
                "免费翻译已启用",
                f"由 {result.provider or '公共接口'} 提供：选中的文字会发送到公开"
                "翻译服务。可在候选条 Ⓐ 里关闭。",
                QSystemTrayIcon.MessageIcon.Information, 5200)
        self._pending = (result.src, result.text)
        if result.source == "llm" and getattr(assist, "store_llm_draft", True):
            # AI 初稿先入库（低频权重），用户若纠正会自动盖过它
            self.memory.learn(result.src, result.text,
                              self.bar.scene.profession_key, "llm")
        self.bar.show_result(result)

    # ================= 词汇助手：标记纠正 =================
    def mark_correction(self) -> None:
        """Ctrl+Alt+W：把「刚译的那句 + 用户改好的译文」写入记忆库。"""
        src, prev = self._pending
        if not src:
            self.bar.show_hint(
                f"还没有可标记的译文，请先 {self._hk('translate_selection')} 翻译",
                "warn")
            return
        cap = read_selection()
        corrected = cap.text if cap.ok else ""
        if not corrected:
            self.bar.show_hint(
                f"请选中你改好的英文，再按 {self._hk('mark_correction')}",
                "warn")
            return
        if corrected == prev:
            # 用户没改，只是确认 -> 提升原译法权重
            self.memory.reinforce(src, self.bar.scene.profession_key)
            self.bar.show_hint("已确认这条译法 · 记忆库已加权", "ok")
            return

        ind = self.bar.scene.profession_key
        self.memory.learn(src, corrected, ind, "user")
        self.llm.put_cache(f"{src}|{ind}", corrected)
        short = corrected if len(corrected) <= 42 else corrected[:41] + "…"
        self.bar.show_hint(f"已学习：{short}", "memory")

    def _drain_results(self) -> None:
        """主线程消费后台翻译结果。"""
        while True:
            try:
                kind, payload = self._results.get_nowait()
            except queue.Empty:
                break
            if kind == "translation":
                self._busy = False
                self._apply_translation(payload)

    # ================= 热键分发 =================
    def on_hotkey(self, name: str) -> None:
        if name == "translate_selection":
            self.translate_selection()
        elif name == "mark_correction":
            self.mark_correction()
        elif name == "toggle_panel":
            self.toggle_panel()
        elif name == "cycle_scope":
            self.cycle_profession()
        elif name == "toggle_llm":
            self.toggle_llm()

    def toggle_llm(self) -> None:
        """Ctrl+Alt+S：一键开关大模型增强（保密场景常用）。"""
        cfg = dict(getattr(self.config, "llm", {}) or {})
        cfg["enabled"] = not bool(cfg.get("enabled"))
        self.llm.configure(cfg)
        self.config.llm = cfg
        self._persist("llm", cfg)
        self.bar.refresh_icons()
        if cfg["enabled"] and not self.llm.available:
            self.bar.show_hint("AI 已开启，但还没填 API Key（点候选条 Ⓐ）",
                               "warn")
        else:
            self.bar.show_hint(
                "AI 增强已开启" if cfg["enabled"] else "AI 增强已关闭 · 纯本地",
                "ai" if cfg["enabled"] else "ok")

    # ================= 首启引导 / 快捷键速查 =================
    def maybe_show_first_run(self) -> None:
        """首启标记不存在 → 弹一页引导（模态，勾选「不再显示」后写标记）。"""
        marker = marker_path()
        if not marker.exists():
            WelcomeDialog.exec_first_run(marker)

    def show_hotkey_reference(self) -> None:
        """托盘「快捷键速查…」：随时重开同一对话框，不触碰首启标记。"""
        WelcomeDialog().open_reference()

    # ================= 配套弹窗（v2.2：设置中心已并入候选条顶行图标） =================
    def _place_popup(self, popup) -> None:
        """把弹窗放到候选条下方（左对齐）并激活。

        v2.3.1：按候选条所在屏幕夹取（旧版按主屏，副屏上弹窗会被拉回主屏）。
        """
        popup.adjustSize()
        screen = QApplication.screenAt(
            QPoint(self.bar.x() + 8, self.bar.y() + 8))
        if screen is None:
            screen = QApplication.screenAt(
                QPoint(self.bar.x(), self.bar.y()))
        x = self.bar.x()
        y = self.bar.y() + self.bar.height() + 6
        if screen is not None:
            geo = screen.availableGeometry()
            x = max(geo.left() + 6, min(x, geo.right() - popup.width() - 6))
            y = max(geo.top() + 6, min(y, geo.bottom() - popup.height() - 6))
        popup.move(int(x), int(y))
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def open_add_word(self) -> None:
        """＋：加词条弹窗（复用实例；成功即入词库并落盘，✓ 自关）。"""
        if self._popup_add is None:
            self._popup_add = AddWordPopup(self.engine)
            self._popup_add.added.connect(self._on_word_added)
        self._place_popup(self._popup_add)
        self._popup_add.reset()

    def _on_word_added(self, zh: str) -> None:
        self.bar.show_hint(f"已加入词库：{zh}", "ok")

    def open_industry(self) -> None:
        """🏷：行业弹窗（一级行业，点选即应用）。"""
        if self._popup_ind is not None:
            self._popup_ind.close()
        cur = str((self.config.profile.get("hierarchy") or {})
                  .get("industry", ""))
        pop = IndustryPopup(cur)
        pop.picked.connect(self.apply_industry)
        self._popup_ind = pop          # 强引用防 GC
        self._place_popup(pop)

    def apply_industry(self, key: str) -> None:
        """行业切换：持久化 profile + 候选即时重排。"""
        from core.hierarchy import get_industry
        ind = get_industry(key)
        if ind is None:
            return
        h = dict(self.config.profile.get("hierarchy") or {})
        h["industry"] = key
        # 职业/职位仅在仍属于该行业时保留，否则取该行业第一项（保持加权粒度；
        # 三级数据仍可手编 config.json 精调，UI 侧按 v2.2 简化为一级）
        occ = next((o for o in ind.occupations
                    if o.key == h.get("occupation")), None)
        if occ is None:
            occ = ind.occupations[0] if ind.occupations else None
            h["occupation"] = occ.key if occ else ""
        pos = next((p for p in (occ.positions if occ else [])
                    if p.key == h.get("position")), None)
        if pos is None:
            h["position"] = (occ.positions[0].key
                             if occ and occ.positions else "")
        self.config.profile["hierarchy"] = h
        self.config.profile["active_profession"] = key
        self._persist("profile", {"hierarchy": h, "active_profession": key})
        self.bar.refresh_scene()
        if self.bar.isVisible() and self.ime is not None:
            self.bar.refresh_candidates(self.ime.pinyin or "")
        self.bar.show_hint(f"行业范围：{ind.name}", "ok")

    def open_ai(self) -> None:
        """Ⓐ：AI 增强设置弹窗（开关 + 接口地址 + 模型 + API Key + 代理）。"""
        if self._popup_ai is not None:
            self._popup_ai.close()
        pop = AiPopup(dict(getattr(self.config, "llm", {}) or {}),
                      free_on=self._free_enabled,
                      proxy=str(getattr(self.config.assist, "proxy", "") or ""))
        pop.saved.connect(self.apply_llm)
        pop.free_toggled.connect(self.apply_free_translate)
        self._popup_ai = pop
        self._place_popup(pop)

    def apply_llm(self, cfg: dict) -> None:
        merged = dict(getattr(self.config, "llm", {}) or {})
        for k in ("enabled", "base_url", "model", "api_key"):
            if k in (cfg or {}):
                merged[k] = cfg[k]
        # v3.0.1：代理是「网络通道」设置，AI 与免费翻译共用，存在 assist 段。
        proxy = str((cfg or {}).get("proxy", "") or "").strip()
        if "proxy" in (cfg or {}):
            self.config.assist.proxy = proxy
            merged["proxy"] = proxy
            self._persist("assist", {"proxy": proxy})
            try:
                from core.free_translate import reset_route, _split_hostport
                if self.free_translator is not None:
                    self.free_translator.proxy = _split_hostport(proxy)
                # 用户刚改过代理，之前缓存的路由必须失效
                reset_route()
            except Exception:
                pass
        self.llm.configure(merged)
        self.config.llm = merged
        self._persist("llm", merged)
        self.bar.refresh_icons()
        on = bool(merged.get("enabled"))
        if on and not self.llm.available:
            self.bar.show_hint("AI 已勾选，但还没填 API Key", "warn")
        else:
            self.bar.show_hint(
                "AI 增强已开启" if on else "AI 增强已关闭 · 纯本地",
                "ai" if on else "ok")

    def apply_free_translate(self, on: bool) -> None:
        """v2.6：无 Key 免费翻译开关（立即生效并持久化）。"""
        on = bool(on)
        self._free_enabled = on
        self.config.assist.free_translate = on
        if self.free_translator is not None:
            self.free_translator.enabled = on
        self._persist("assist", {"free_translate": on})
        self.bar.show_hint(
            "免费在线翻译已开启（选中文本会发到公开服务）" if on
            else "免费在线翻译已关闭（仅本地词库与 AI）",
            "ai" if on else "ok")

    def open_about(self) -> None:
        """Ⓘ：关于弹窗（版本/作者 + 导出水杉词库）。"""
        if self._popup_about is not None:
            self._popup_about.close()
        pop = AboutPopup(version=str(getattr(self.config, "version", "2.2")))
        self._popup_about = pop
        self._place_popup(pop)

    # ================= 托盘（图标 + 右键菜单；v2.5 恢复菜单） =================
    def _hk(self, name: str) -> str:
        """热键展示串（Ctrl+Alt+Q），取值走 config.hotkey() 唯一来源。"""
        spec = self.config.hotkey(name, DEFAULT_HOTKEYS[name])
        return "+".join(p.capitalize() for p in spec.split("+") if p)

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_tray_icon())
        self.tray.setToolTip(
            f"{self.config.app_name} · 作者 Rlon（单击显示候选条）")
        self.tray.activated.connect(self._on_tray_activated)

        # v2.5（U03）：日常输入法用户 100% 会右键托盘找"设置/暂停/退出"，
        # v2.2 把菜单删干净后这里是个死区。恢复成 5 项极简菜单，不做设置中心。
        menu = QMenu()
        self.act_show = menu.addAction("显示候选条")
        self.act_show.triggered.connect(self.toggle_panel)
        self.act_pause = menu.addAction("暂停拼音接管")
        self.act_pause.triggered.connect(lambda: self.toggle_ime())
        menu.addSeparator()
        self.act_hotkeys = menu.addAction("快捷键速查…")
        self.act_hotkeys.triggered.connect(self.show_hotkey_reference)
        self.act_dir = menu.addAction("打开配置目录")
        self.act_dir.triggered.connect(self.open_config_dir)
        menu.addSeparator()
        self.act_quit = menu.addAction("退出 RlonIME")
        self.act_quit.triggered.connect(self.quit)
        menu.aboutToShow.connect(self._sync_tray_menu)
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.show()

    def _sync_tray_menu(self) -> None:
        """右键菜单每次弹出前同步状态（不漏陈旧文案）。"""
        try:
            self.act_show.setText(
                "隐藏候选条" if self.bar.isVisible() else "显示候选条")
            self.act_pause.setText(
                "暂停拼音接管" if self.ime.enabled else "恢复拼音接管")
        except Exception:
            pass

    def open_config_dir(self) -> None:
        """打开发置文件所在目录（排障/手编配置的唯一入口）。"""
        try:
            d = self.config.path.parent
            if d.exists():
                os.startfile(str(d))       # noqa: S606
        except Exception as e:
            print(f"[WARN] 无法打开配置目录: {e}", file=sys.stderr)

    def hide_to_tray(self) -> None:
        """✕ / 托盘"隐藏"：收起候选条，进程继续在托盘常驻（U02）。"""
        self.bar.collapsed = True
        self.bar.close_panel()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage(
                self.config.app_name,
                "已收起到托盘。右键托盘图标可暂停接管或退出。",
                QSystemTrayIcon.MessageIcon.Information, 2600)

    def _on_tray_activated(self, reason) -> None:
        # v2.3.1：双击不再退出（防误触）；v2.5（I06）单击与双击同语义。
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_panel()

    # ================= 行业 / 软件切换 =================
    def switch_profession(self, key: str) -> None:
        prof = self.config.professions.get(key)
        if not prof:
            return
        self.config.profile["active_profession"] = key
        self.bar.set_scene(self.detector.lock(profession=key))
        self.bar.show_hint(f"行业范围：{prof.name}", "ok")

    def cycle_profession(self) -> None:
        keys = list(self.config.professions.keys())
        cur = self.config.profile.get("active_profession")
        idx = (keys.index(cur) + 1) % len(keys) if cur in keys else 0
        self.switch_profession(keys[idx])

    # ================= 候选条 =================
    def toggle_panel(self) -> None:
        if self.bar.isVisible():
            self.bar.close_panel()
        else:
            self.bar.show_input(self.ime.pinyin or "")
            self.bar.refresh_candidates(self.ime.pinyin or "")

    def toggle_ime(self, checked: bool | None = None) -> None:
        """暂停/恢复拼音接管（入口：候选条 ⏸ 或 Ctrl+Space）。"""
        on = (not self.ime.enabled) if checked is None else bool(checked)
        self.ime.set_enabled(on)
        self.bar.refresh_icons()
        self._persist("ime", {"enabled": on})
        self._refresh_tray_state()
        self.bar.show_hint(
            "拼音接管已开启 · 直接敲拼音出候选" if on
            else "拼音接管已暂停 · 按键已交还系统", "ok" if on else "warn")

    def _persist(self, section: str, values: dict) -> None:
        """把设置写回 config.json（升级不丢、卸载不删）。"""
        try:
            path = self.config.path
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.setdefault(section, {}).update(values)
            path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        except Exception as e:
            print(f"[WARN] 配置未能持久化: {e}", file=sys.stderr)

    def _on_committed(self, text: str, kind: str) -> None:
        pass    # v2.2：托盘菜单已净化，上屏后无需刷新任何菜单

    def quit(self) -> None:
        fns = [self.ime.stop, self.memory.close]
        if self.guard is not None:
            fns.append(self.guard.close)
        for fn in fns:
            try:
                fn()
            except Exception:
                pass
        try:
            self.tray.hide()
        except Exception:
            pass
        QApplication.quit()


def _arg_value(name: str) -> str | None:
    """取命令行 `--name <value>` 的值，无则 None。"""
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main() -> int:
    bootstrap()
    # 崩溃日志三件套：先于 QApplication，落 %APPDATA%\ZhiyuIME\logs
    crashlog.install(user_data_root() / "logs")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 单实例：第二进程递完命令立即退出，不建钩子不开窗
    command = "settings" if "--settings" in sys.argv else "raise"
    guard = SingleInstanceGuard()
    if not guard.try_acquire(command):
        return 0

    zh = ZhiyuApp(guard=guard)
    zh._selftest_raise_flag = _arg_value("--selftest-raise-flag")

    # 热键加载校验（v2.2：改键改为手编 config.json，校验在此兜底提示；
    # 内核 _build_hotkey_table 已对无法解析的项逐条回退默认）
    hk_errs = Config.validate_hotkeys(
        {k: zh.config.hotkey(k, DEFAULT_HOTKEYS[k])
         for k in DEFAULT_HOTKEYS})
    if hk_errs:
        print("[WARN] 热键配置校验未通过（内核已回退默认）: "
              + "；".join(hk_errs), file=sys.stderr)

    bridge = HotkeyBridge()
    bridge.cycle_scope.connect(zh.cycle_profession)
    bridge.toggle_llm.connect(zh.toggle_llm)
    _install_fallback_hotkeys(zh, bridge)

    st = zh.engine.lexicons.stats()
    mem = zh.memory.stats()
    if zh._ime_hooked:
        tip = ("全系统接管键盘：敲拼音出候选 · 数字/空格选词 · Tab 上屏英文\n"
               f"选中中文按 {zh._hk('translate_selection')} 就地翻译 · "
               f"改完按 {zh._hk('mark_correction')} 入库")
    else:
        tip = f"⚠ 键盘接管未生效：{zh.ime.hook.error}（建议以管理员身份运行）"
    if hk_errs:
        tip += "\n⚠ 热键配置有问题（已回退默认），详见 config.json"
    zh.tray.showMessage(
        "RlonIME 已启动",
        f"{st['total_terms']} 条术语 · {len(st['professions'])} 个行业 · "
        f"记忆库 {mem['distinct_src']} 句\n{tip}",
        QSystemTrayIcon.MessageIcon.Information, 4000)

    if not zh._ime_hooked:
        QTimer.singleShot(400, zh.toggle_panel)

    # 首启引导：标记不存在则弹一页图文（模态；钩子/托盘已就绪不受影响）
    zh.maybe_show_first_run()
    return app.exec()


def _install_fallback_hotkeys(zh, bridge: HotkeyBridge) -> None:
    """热键兜底通道。

    主通道是 core/ime.py 的全局钩子（零额外依赖、与拼音状态机同一份状态）。
    但钩子若因权限或安全软件被拦，热键就全废了 —— 这里用 keyboard 库再挂一份，
    两条通道互不冲突（keyboard 库只在钩子未生效时启用）。
    """
    if zh._ime_hooked:
        return
    try:
        import keyboard
        hk = zh.config.hotkey
        keyboard.add_hotkey(
            hk("translate_selection", DEFAULT_HOTKEYS["translate_selection"]),
            zh.translate_selection)
        keyboard.add_hotkey(
            hk("mark_correction", DEFAULT_HOTKEYS["mark_correction"]),
            zh.mark_correction)
        keyboard.add_hotkey(
            hk("toggle_panel", DEFAULT_HOTKEYS["toggle_panel"]),
            zh.toggle_panel)
        keyboard.add_hotkey(
            hk("cycle_scope", DEFAULT_HOTKEYS["cycle_scope"]),
            bridge.cycle_scope.emit)
        keyboard.add_hotkey(
            hk("toggle_llm", DEFAULT_HOTKEYS["toggle_llm"]),
            bridge.toggle_llm.emit)
    except Exception as e:
        print(f"[WARN] 兜底热键注册失败: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
