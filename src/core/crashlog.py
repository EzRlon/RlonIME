"""
智语输入法 — 崩溃日志（只本地、零网络、绝不含用户文本）

三件套：
1. sys.excepthook        —— 主线程未捕获异常（含 Qt 槽函数抛出的异常）
2. threading.excepthook  —— 子线程未捕获异常
3. faulthandler.enable   —— 原生层崩溃（段错误等）时的 Python 堆栈转储

隐私红线（与 license.txt 口径一致）：
    日志只含版本号、Python/OS 信息、异常类型与堆栈；
    **不含任何按键内容、剪贴板内容、用户选区文本**，且永不联网上传。

行为约定：
    Qt 事件循环内的未捕获异常经 excepthook 落盘后**保持进程存活**——
    托盘程序不因单个槽函数异常退出（写日志即可，调用方自行提示）。
"""
from __future__ import annotations

import faulthandler
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import TextIO

_KEEP = 20                      # 滚动保留最近 20 份崩溃日志
_installed = False
_log_dir: Path | None = None
_fh_file: TextIO | None = None  # faulthandler 输出句柄（必须常驻引用）


def install(log_dir: Path) -> None:
    """启动早期安装一次（bootstrap 之后、QApplication 之前）。"""
    global _installed, _log_dir, _fh_file
    if _installed:
        return
    _installed = True
    _log_dir = Path(log_dir)
    try:
        _log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return                      # 日志目录不可写也不阻塞启动

    try:
        _fh_file = open(_log_dir / "crash-faulthandler.log", "a",
                        encoding="utf-8")
        faulthandler.enable(file=_fh_file)
    except Exception:
        pass
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    _rotate(_log_dir, _KEEP)


# ---------------------------------------------------------------- 内部
def _app_version() -> str:
    """尽力取版本号；配置加载本身挂了也不影响崩溃日志。"""
    try:
        from core.config import get_config
        return get_config().version
    except Exception:
        return "unknown"


def _write_crash(title: str, tb_text: str) -> None:
    """落一份 crash-YYYYMMDD-HHMMSS.log；任何失败都静默（不二次崩溃）。"""
    if _log_dir is None:
        return
    try:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = _log_dir / f"crash-{ts}.log"
        seq = 1
        while path.exists():        # 同一秒多次崩溃不互相覆盖
            path = _log_dir / f"crash-{ts}-{seq}.log"
            seq += 1
        header = [
            f"RlonIME 崩溃报告  ({ts})",
            f"app:     v{_app_version()}",
            f"python:  {sys.version.split()[0]} ({sys.executable})",
            f"os:      {platform.platform()}",
            f"frozen:  {bool(getattr(sys, 'frozen', False))}",
            f"thread:  {threading.current_thread().name}",
            "",
            title,
            "",
        ]
        # 注意：只写堆栈与版本信息。绝不写入按键/剪贴板/用户文本。
        path.write_text("\n".join(header) + tb_text, encoding="utf-8",
                        errors="replace")
        _rotate(_log_dir, _KEEP)
    except Exception:
        pass


def _excepthook(exc_type, exc, tb) -> None:
    """主线程未捕获异常：落盘后保持进程存活。"""
    tb_text = "".join(traceback.format_exception(exc_type, exc, tb))
    _write_crash(f"未捕获异常: {exc_type.__name__}: {exc}", tb_text)
    # 同时走 stderr，保持开发态可见
    sys.__excepthook__(exc_type, exc, tb)


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    tb_text = "".join(traceback.format_exception(
        args.exc_type, args.exc_value, args.exc_traceback))
    tname = args.thread.name if args.thread else "unknown"
    _write_crash(
        f"子线程未捕获异常 [{tname}]: {args.exc_type.__name__}: {args.exc_value}",
        tb_text)


def _rotate(log_dir: Path, keep: int = _KEEP) -> None:
    """滚动清理：crash-*.log 只保留最近 keep 份。"""
    try:
        files = sorted(log_dir.glob("crash-*.log"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass
