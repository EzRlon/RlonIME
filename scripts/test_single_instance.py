"""智语输入法 — 单实例防护自动化测试（设计 §1.2 双入口验证方案）

流程：
1. 起进程 A（带 --selftest-raise-flag <path> 测试开关），等它的
   QLocalServer named pipe 就绪；
2. 起进程 B（无参），断言 B 自行快速退出（不建钩子不开窗）；
3. 断言 A 存活、且收到 raise 命令并写出标志文件。

注意：A 是真实实例，会挂全局键盘钩子，请在无其他测试并行时运行。
"""
from __future__ import annotations

import ctypes
import getpass
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src" / "main.py"

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK ' if cond else 'FAIL'} {name}{'' if cond else '  ' + extra}")


def wait_named_pipe(name: str, timeout_s: float = 20.0) -> bool:
    """等主实例的 named pipe 出现（WaitNamedPipe 不消耗连接）。"""
    pipe = rf"\\.\pipe\{name}"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        # NMPWAIT_USE_DEFAULT_WAIT=0：立即返回，只探测存在性
        if ctypes.windll.kernel32.WaitNamedPipeW(pipe, 0):
            return True
        time.sleep(0.3)
    return False


def main() -> int:
    user = getpass.getuser() or "user"
    server_name = f"ZhiyuIME-SingleInstance-{user}"
    flag = Path(tempfile.gettempdir()) / f"zhiyu_raise_{int(time.time())}.flag"

    print("[1] 启动进程 A（主实例）")
    a = subprocess.Popen([sys.executable, str(MAIN),
                          "--selftest-raise-flag", str(flag)])
    try:
        pipe_ok = wait_named_pipe(server_name, 20.0)
        ck("A 单实例管道就绪", pipe_ok)
        ck("A 进程存活", a.poll() is None)
        if not pipe_ok:
            return _summary(a, flag)

        print("[2] 启动进程 B（第二实例，无参）")
        t0 = time.monotonic()
        b = subprocess.run([sys.executable, str(MAIN)],
                           capture_output=True, timeout=15)
        elapsed = time.monotonic() - t0
        ck("B 自行退出（returncode=0）", b.returncode == 0,
           f"rc={b.returncode} stderr={b.stderr.decode('utf-8', 'ignore')[-200:]}")
        ck("B 在 3s 级退出（<6s 含启动开销）", elapsed < 6.0,
           f"elapsed={elapsed:.1f}s")

        print("[3] 验证 A 收到 raise")
        got_flag = False
        for _ in range(30):
            if flag.exists():
                got_flag = True
                break
            time.sleep(0.2)
        ck("A 收到 raise 并写标志文件", got_flag)
        if got_flag:
            content = flag.read_text(encoding="utf-8").strip()
            ck("标志内容为 raise", content == "raise", content)
        ck("A 进程仍存活", a.poll() is None)

        return _summary(a, flag)
    finally:
        a.terminate()
        try:
            a.wait(timeout=5)
        except Exception:
            a.kill()
        flag.unlink(missing_ok=True)


def _summary(a, flag) -> int:
    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
