"""
智语输入法 — 单实例防护（Qt 原生 QLocalServer/QLocalSocket，零新依赖）

为什么需要它：
    智语持有全局键盘钩子与托盘。用户双击桌面图标又点了「开机自启」，
    两个进程同时接管键盘会互相吞键、托盘图标成排。本模块保证同一用户
    同一时刻只有一个主实例：第二进程把命令（raise/settings）通过
    named pipe 递给主实例后立即退出，不建钩子、不开窗。

设计要点：
- 服务名带用户名后缀：Windows named pipe 默认按会话隔离，加用户名
  后缀进一步防多用户 / RDP 场景互相误唤起。
- 主实例崩溃会留下 stale socket：listen 前先 removeServer 清残留。
- 命令是单字节级短串（"raise" / "settings"），读完即关连接。
"""
from __future__ import annotations

import getpass
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceGuard(QObject):
    """单实例门卫：try_acquire 成功者成为主实例并监听唤起命令。"""

    raise_requested = Signal(str)   # 第二进程发来的命令："raise" / "settings"

    def __init__(self, name: str | None = None, parent: QObject | None = None):
        super().__init__(parent)
        user = getpass.getuser() or "user"
        # named pipe 按会话隔离；用户名后缀防多用户/RDP 冲突
        self.name = name or f"RlonIME-SingleInstance-{user}"
        self.server: QLocalServer | None = None
        # 启动竞态缓冲：管道就绪早于 ZhiyuApp 完成构建，此间到达的唤起命令
        # 不能丢（用户双击图标后立刻再双击是真实场景），由主实例构建完后取走。
        self._pending_command: str | None = None

    # ---------- 入口 ----------
    def try_acquire(self, command: str = "raise") -> bool:
        """尝试成为主实例。

        返回 False = 已有实例在跑：命令已送达，调用方应立即 sys.exit(0)。
        返回 True  = 本进程成为主实例，self.server 已就绪。
        """
        sock = QLocalSocket()
        sock.connectToServer(self.name)
        if sock.waitForConnected(200):
            # 已有主实例：递上命令后退场
            sock.write((command or "raise").encode("utf-8"))
            sock.flush()
            sock.waitForBytesWritten(200)
            sock.disconnectFromServer()
            return False
        sock.abort()

        # 无实例（或上次崩溃留了 stale socket）：清残留后接管
        QLocalServer.removeServer(self.name)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)
        if not self.server.listen(self.name):
            # 监听失败不阻塞启动（极端权限场景），仅告警
            print(f"[WARN] 单实例监听失败：{self.server.errorString()}",
                  file=sys.stderr)
        return True

    # ---------- 监听端 ----------
    def _on_new_connection(self) -> None:
        if self.server is None:
            return
        while self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: self._read_command(c))
            if conn.bytesAvailable():
                # 数据可能先于 readyRead 连接到达
                self._read_command(conn)
            else:
                # 兜底：对端异常时连接不泄漏
                QTimer.singleShot(1500, conn, conn.deleteLater)

    def _read_command(self, conn: QLocalSocket) -> None:
        try:
            cmd = bytes(conn.readAll()).decode("utf-8", "ignore").strip()
        except Exception:
            cmd = ""
        # 先 emit 再收拾连接：在 readyRead 槽内对发送者 deleteLater 后再发
        # 信号，PySide6 会偶发丢投递（实测复现）——顺序不可颠倒。
        self._pending_command = cmd or "raise"
        self.raise_requested.emit(self._pending_command)
        conn.disconnectFromServer()
        conn.deleteLater()

    def take_pending_command(self) -> str | None:
        """取走并清空缓冲的唤起命令（主实例完成信号接线后调用一次）。"""
        cmd, self._pending_command = self._pending_command, None
        return cmd

    # ---------- 清理 ----------
    def close(self) -> None:
        if self.server is not None:
            self.server.close()
            self.server = None
