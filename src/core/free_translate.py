"""
RlonIME — 无 Key 免费翻译（v2.6）

背景：AI 增强要用户自己申请 API Key（还有网络与费用门槛）。但「选中即译」
是这个软件的核心动作，没有 Key 就完全不能用，体验上是断的。

方案：两级**完全免费、无需注册**的公共翻译接口，按可靠性排序——
  1. MyMemory（api.mymemory.translated.net）：匿名可用，国内可达性好；
     单次请求有长度限制，超长自动切段。
  2. Google 翻译的免 Key 端点（translate.googleapis.com/translate_a/single）：
     质量更好，但国内可能不可达，故只作后备。

三条原则：
  · **只在用户显式动作时使用**（Ctrl+Alt+Q / 悬浮「译」钮），绝不后台偷跑；
  · **首用明确告知**：文字会发到公开翻译服务，`assist.free_translate` 可关；
  · **失败静默降级**：网络不通就保留原文并提示，绝不吞用户内容。

术语一致性：免费引擎不懂术语表，所以回来后仍走 `apply_term_lock`
（中→英）把标准译名掰回来——这是与"随便接个免费翻译"的本质区别。
"""
from __future__ import annotations

import html
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_MYMEMORY = "https://api.mymemory.translated.net/get"
_GOOGLE = "https://translate.googleapis.com/translate_a/single"
_UA = "RlonIME/2.6 (Windows IME; free translate)"
_MAX_CHUNK = 480          # MyMemory 匿名单次上限 500 字节，留余量
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="RlonFree")

# 本机常见代理端口（Clash / Clash Verge / v2rayN / 通用 HTTP 代理）。
# 只在"直连被系统拒绝"之后才探测，正常网络下一次都不会碰。
_PROXY_PORTS = ("127.0.0.1:7890", "127.0.0.1:7897", "127.0.0.1:10809",
                "127.0.0.1:10808", "127.0.0.1:8080")

# 触发代理回退的错误码。10013 = WSAEACCES（用户看到的 18013 是同一语义的
# 另一种表述："以一种访问权限不允许的方式做了一个访问套接字的尝试"），
# 10061/10060/10065/10051/10054 分别对应拒绝、超时、不可达、网络不可达、连接重置。
# 这些都属于"链路被环境挡住"，换代理有机会通；HTTP 4xx/5xx 不在此列。
_BLOCKED_WINERR = {10013, 18013, 10061, 10060, 10065, 10051, 10054}

# None = 还没探测过；"" = 默认方式（系统/环境代理）可用；其它 = 可用的代理地址。
_WORKING_ROUTE: str | None = None
_ROUTE_LOCK = threading.Lock()
# 端口探测结果缓存（host:port -> (是否可连, 时间)），避免网络不通时反复等超时。
_PORT_STATE: dict[str, tuple[bool, float]] = {}
_PORT_TTL = 30.0


class FreeTranslateError(Exception):
    pass


def _split_hostport(value: str) -> str:
    """把各种写法的代理串归一成 host:port（去掉 scheme、路径、user:pass）。"""
    v = (value or "").strip()
    v = re.sub(r"^(https?|socks5?h?)://", "", v, flags=re.IGNORECASE)
    v = v.split("/")[0].strip()
    if "@" in v:                       # user:pass@host:port
        v = v.rsplit("@", 1)[-1]
    if v.count(":") == 1 and v.rsplit(":", 1)[1].isdigit():
        return v
    return ""


def _candidate_proxies() -> list[str]:
    """按可信度收集候选代理：环境变量 → 系统代理设置 → 常见本地端口。"""
    out: list[str] = []
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
        hp = _split_hostport(os.environ.get(key, ""))
        if hp and hp not in out:
            out.append(hp)
    # 注册表：很多用户开了 Clash 但没开"系统代理"，ProxyEnable=0 时
    # ProxyServer 仍留着上一次的地址，值得一试（连不上会被 _port_open 挡掉）。
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            raw = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "")
        finally:
            key.Close()
        for part in raw.split(";"):
            hp = _split_hostport(part.split("=", 1)[-1])
            if hp and hp not in out:
                out.append(hp)
    except Exception:
        pass
    for hp in _PROXY_PORTS:
        if hp not in out:
            out.append(hp)
    return out


def _port_open(hostport: str, timeout: float = 0.12) -> bool:
    """本地代理端口是否在监听（连接成功即算）。失败一律 False，绝不抛。

    结果缓存 30 秒：网络不通时每个候选都要等一次超时，连续翻译会明显卡顿。
    """
    hit = _PORT_STATE.get(hostport)
    now = time.monotonic()
    if hit and now - hit[1] < _PORT_TTL:
        return hit[0]
    try:
        host, port = hostport.rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout):
            ok = True
    except Exception:
        ok = False
    _PORT_STATE[hostport] = (ok, now)
    return ok


def _is_link_blocked(exc: BaseException) -> bool:
    """是否属于"链路被环境挡住"（值得换代理重试）。"""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror):      # DNS 被污染/不可用
        return True
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    code = 0
    if isinstance(reason, OSError):
        code = int(getattr(reason, "winerror", 0) or getattr(reason, "errno", 0) or 0)
    return code in _BLOCKED_WINERR


def _open(url: str, timeout: float, proxy: str) -> str:
    """按指定路由发一次 GET。proxy="" 表示用系统/环境默认方式。"""
    if proxy:
        handler = urllib.request.ProxyHandler(
            {"http": f"http://{proxy}", "https": f"http://{proxy}"})
        opener = urllib.request.build_opener(handler)
    else:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def reset_route() -> None:
    """用户在设置里改了代理 / 网络刚恢复时调用，清掉路由与端口缓存。"""
    global _WORKING_ROUTE
    with _ROUTE_LOCK:
        _WORKING_ROUTE = None
        _PORT_STATE.clear()


def _http_get(url: str, timeout: float, prefer: str = "") -> str:
    """GET 一次；直连被系统拒绝时自动改用本机代理并记住可用路由。

    为什么需要这一层（2026-10-27 用户反馈）：本机直连公共接口被系统策略
    拒绝（WinError 10013/18013），而走 Clash 之类本地代理完全正常。打包成
    exe 从桌面启动时**不继承 shell 的 HTTP_PROXY 环境变量**，于是必然直连、
    必然失败，用户看到的就是"免费翻译用不了"。

    prefer 是用户在设置里手填的代理，优先级最高；它不可用时继续自动探测，
    绝不因为一个手填错的值把整条链路堵死。
    """
    global _WORKING_ROUTE
    with _ROUTE_LOCK:
        known = _WORKING_ROUTE
    if known is not None and not prefer:
        return _open(url, timeout, known)

    first: BaseException | None = None
    routes: list[str] = []
    if prefer:
        routes.append(prefer)
    routes.append("")                       # 系统/环境默认（大多数机器只走这条）
    for route in routes:
        try:
            body = _open(url, timeout, route)
        except Exception as e:
            if first is None:
                first = e
            if not _is_link_blocked(e):
                raise
            continue
        with _ROUTE_LOCK:
            _WORKING_ROUTE = route
        return body

    for candidate in _candidate_proxies():
        if candidate == prefer or not _port_open(candidate):
            continue
        try:
            body = _open(url, timeout, candidate)
        except Exception:
            continue
        with _ROUTE_LOCK:
            _WORKING_ROUTE = candidate
        print(f"[INFO] 免费翻译改走本机代理 {candidate}", file=sys.stderr)
        return body
    raise first if first is not None else urllib.error.URLError("网络不可用")


# ---------------------------------------------------------------- 提供方
def _mymemory(text: str, src: str, dst: str, timeout: float,
              fetch=_http_get) -> str:
    url = (f"{_MYMEMORY}?q={urllib.parse.quote(text)}"
           f"&langpair={urllib.parse.quote(src + '|' + dst)}")
    raw = fetch(url, timeout).strip()
    if not raw.startswith("{"):
        raise FreeTranslateError("MyMemory 返回非 JSON")
    data = json.loads(raw)
    try:
        status = int(data.get("responseStatus") or 0)
    except (TypeError, ValueError):
        status = 0
    out = html.unescape(str((data.get("responseData") or {})
                            .get("translatedText") or "")).strip()
    up = out.upper()
    if status != 200 or not out or "MYMEMORY WARNING" in up or "QUERY LIMIT" in up:
        raise FreeTranslateError(f"MyMemory 无有效译文(status={status})")
    return out


def _google(text: str, src: str, dst: str, timeout: float,
            fetch=_http_get) -> str:
    url = (f"{_GOOGLE}?client=gtx&sl={urllib.parse.quote(src)}"
           f"&tl={urllib.parse.quote(dst)}&dt=t&q={urllib.parse.quote(text)}")
    raw = fetch(url, timeout).strip()
    data = json.loads(raw)
    segs = (data[0] if data else None) or []
    out = "".join(str(s[0]) for s in segs if s and s[0]).strip()
    if not out:
        raise FreeTranslateError("Google 免 Key 端点无译文")
    return out


# 提供方签名：(文本, 源语言, 目标语言, 超时, fetcher) -> 译文
_PROVIDERS: list[tuple[str, Callable[..., str]]] = [
    ("MyMemory", _mymemory),
    ("Google", _google),
]


def _split(text: str, limit: int = _MAX_CHUNK) -> list[str]:
    """长句按标点/空白切段（免费接口有长度上限）。"""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    buf = ""
    for piece in re.split(r"(?<=[，。；：！？,.;:!?\s])", text):
        if not piece:
            continue
        if len(buf) + len(piece) > limit and buf:
            out.append(buf)
            buf = piece
        else:
            buf += piece
    if buf:
        out.append(buf)
    return out


class FreeTranslator:
    """无 Key 免费翻译门面（可注入 fetcher 便于离线单测）。"""

    def __init__(self, enabled: bool = True, timeout: float = 4.0,
                 fetcher: Callable[[str, float], str] | None = None,
                 providers: list | None = None, proxy: str = ""):
        self.enabled = bool(enabled)
        self.timeout = float(timeout)
        self.last_error = ""
        self.last_detail = ""       # 原始错误串（日志/诊断用，不直接给用户看）
        self.last_provider = ""
        # 用户在设置里手填的代理（host:port）；留空 = 自动探测。
        self.proxy = _split_hostport(proxy)
        self._fetch = fetcher      # 测试注入；None 时走带代理回退的 _http_get
        self._providers = providers or _PROVIDERS

    def _get(self, url: str, timeout: float) -> str:
        if self._fetch is not None:
            return self._fetch(url, timeout)
        return _http_get(url, timeout, prefer=self.proxy)

    @property
    def available(self) -> bool:
        return self.enabled

    def translate(self, text: str, src: str = "zh-CN",
                  dst: str = "en") -> tuple[str, str]:
        """同步翻译。返回 (译文, 提供方)；全失败抛 FreeTranslateError。"""
        text = (text or "").strip()
        if not text:
            raise FreeTranslateError("空文本")
        if not self.enabled:
            raise FreeTranslateError("免费翻译已关闭")
        errors: list[str] = []
        blocked = False
        for name, fn in self._providers:
            try:
                parts = [fn(chunk, src, dst, self.timeout, self._get)
                         for chunk in _split(text)]
                out = " ".join(p for p in parts if p).strip()
                if out:
                    self.last_provider = name
                    self.last_error = ""
                    self.last_detail = ""
                    return out, name
            except Exception as e:      # 换下一个提供方
                errors.append(f"{name}: {e}")
                if _is_link_blocked(e):
                    blocked = True
        self.last_detail = "；".join(errors) or "无可用免费翻译服务"
        # 给用户看的文案必须可操作。不能甩 "urlopen error [WinError 18013]…"：
        # 那是链路问题，用户能做的是开代理或配 Key，而不是改代码。
        if blocked:
            self.last_error = "网络受限 · 请开系统代理或配 AI Key"
        else:
            # 保留"两个源都试过"的信息（排查时有用），只做长度兜底，
            # 免得候选条被一整串 urlopen 细节撑爆。
            self.last_error = ("；".join(errors) or "无可用免费翻译服务")[:80]
        raise FreeTranslateError(self.last_error)

    def translate_async(self, text: str, src: str, dst: str,
                        callback: Callable[[str | None, str, str], None],
                        ) -> None:
        """后台翻译；callback(译文或None, 提供方, 错误)。"""

        def _work() -> None:
            try:
                out, name = self.translate(text, src, dst)
                callback(out, name, "")
            except Exception as e:
                callback(None, "", str(e))

        _POOL.submit(_work)


if __name__ == "__main__":
    tr = FreeTranslator()
    for text, s, d in (("该零件底部需加工沉孔，深度 8 mm", "zh-CN", "en"),
                       ("Counterbore depth 8 mm", "en", "zh-CN")):
        try:
            out, name = tr.translate(text, s, d)
            print(f"[{name}] {text}\n  -> {out}")
        except Exception as e:
            print(f"[FAIL] {text}\n  -> {e}")
