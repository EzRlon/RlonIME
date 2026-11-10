"""
RlonIME — 无 Key 免费翻译（v2.6）

背景：AI 增强要用户自己申请 API Key（还有网络与费用门槛）。但「选中即译」
是这个软件的核心动作，没有 Key 就完全不能用，体验上是断的。

方案：四级**完全免费、无需注册**的公共接口，国内优先——
  1. 腾讯交互翻译（transmart.qq.com/api/imt）：网页端同款，国内直连、质量最好；
  2. 有道智云 demo（aidemo.youdao.com/trans）：免 Key，国内可达；
  3. MyMemory（api.mymemory.translated.net）：匿名可用，长句要切段；
  4. Google 免 Key 端点：境外，只作最后兜底。

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

# 国内免费端点（v4.0.3 起为首选：无需注册、无需 Key、国内直连可达）。
# 腾讯交互翻译是网页端在用的接口，返回质量明显好于 MyMemory；
# 有道智云 demo 端点作为第二备选。
_TRANSMART = "https://transmart.qq.com/api/imt"
_YOUDAO = "https://aidemo.youdao.com/trans"
_TM_KEY = "browser-chrome-120.0.0-Windows-10"
_MYMEMORY = "https://api.mymemory.translated.net/get"
_GOOGLE = "https://translate.googleapis.com/translate_a/single"
_UA = "RlonIME/2.6 (Windows IME; free translate)"
_MAX_CHUNK = 480          # MyMemory 匿名单次上限 500 字节，留余量
MAX_RESP_BYTES = 1 << 20  # S07：单次响应体上限 1 MiB
MAX_TEXT_CHARS = 4000     # S05：待译文本总字符上限（超出直接拒绝，不静默截断）
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


def _open(url: str, timeout: float, proxy: str,
          data: bytes | None = None,
          headers: dict | None = None) -> str:
    """按指定路由发一次请求（GET/POST 由 data 决定）。proxy="" 用系统默认。"""
    if proxy:
        handler = urllib.request.ProxyHandler(
            {"http": f"http://{proxy}", "https": f"http://{proxy}"})
        opener = urllib.request.build_opener(handler)
    else:
        opener = urllib.request.build_opener()
    hdrs = {"User-Agent": _UA}
    hdrs.update(headers or {})
    req = urllib.request.Request(
        url, data=data, headers=hdrs,
        method="POST" if data is not None else "GET")
    with opener.open(req, timeout=timeout) as resp:
        # S07：被劫持端点可返回无限大响应体撑爆进程，先校验声明长度再限量读。
        cl = resp.headers.get("Content-Length")
        if cl is not None:
            try:
                if int(cl) > MAX_RESP_BYTES:
                    raise FreeTranslateError("响应过大（Content-Length 超限）")
            except (TypeError, ValueError):
                pass
        data = resp.read(MAX_RESP_BYTES + 1)
        if len(data) > MAX_RESP_BYTES:
            raise FreeTranslateError("响应过大，已丢弃")
        return data.decode("utf-8", "ignore")


def reset_route() -> None:
    """用户在设置里改了代理 / 网络刚恢复时调用，清掉路由与端口缓存。"""
    global _WORKING_ROUTE
    with _ROUTE_LOCK:
        _WORKING_ROUTE = None
        _PORT_STATE.clear()


def _http_get(url: str, timeout: float, prefer: str = "",
              data: bytes | None = None,
              headers: dict | None = None) -> str:
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
        return _open(url, timeout, known, data, headers)

    first: BaseException | None = None
    routes: list[str] = []
    if prefer:
        routes.append(prefer)
    routes.append("")                       # 系统/环境默认（大多数机器只走这条）
    for route in routes:
        try:
            body = _open(url, timeout, route, data, headers)
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
            body = _open(url, timeout, candidate, data, headers)
        except Exception:
            continue
        with _ROUTE_LOCK:
            _WORKING_ROUTE = candidate
        print(f"[INFO] 免费翻译改走本机代理 {candidate}", file=sys.stderr)
        return body
    raise first if first is not None else urllib.error.URLError("网络不可用")


# ---------------------------------------------------------------- HTTP 门面
class _Fetcher:
    """provider 用的 HTTP 门面：GET 走代理回退，POST 供国内接口使用。

    测试注入的是「只支持 GET」的普通函数，所以 POST 在注入模式下直接报错
    （由 translate 捕获后换下一个 provider），不会真的联网。
    """

    def __init__(self, get=None, prefer: str = ""):
        self._custom = get is not None
        # D12：默认 GET 不再闭包捕获局部 prefer，改为读实例属性 self.prefer，
        # 这样 FreeTranslator.proxy setter 改 prefer 后无需重建即生效。
        self._getter = get or (lambda url, timeout: _http_get(url, timeout, self.prefer))
        self.prefer = prefer

    def __call__(self, url: str, timeout: float) -> str:
        return self._getter(url, timeout)

    def post_json(self, url: str, payload: dict, timeout: float) -> str:
        return self._post(url, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          "application/json", timeout)

    def post_form(self, url: str, fields: dict, timeout: float) -> str:
        return self._post(url, urllib.parse.urlencode(fields).encode("utf-8"),
                          "application/x-www-form-urlencoded", timeout)

    def _post(self, url: str, data: bytes, ctype: str, timeout: float) -> str:
        if self._custom:
            raise FreeTranslateError("注入模式不支持 POST")
        return _http_get(url, timeout, prefer=self.prefer, data=data,
                         headers={"Content-Type": ctype})


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


def _tm_lang(code: str) -> str:
    """腾讯交互翻译只认 zh / en 两个粗粒度代码。"""
    return "zh" if (code or "").lower().startswith("zh") else "en"


def _yd_lang(code: str) -> str:
    """有道用 zh-CHS / en。"""
    return "zh-CHS" if (code or "").lower().startswith("zh") else "en"


def _transmart(text: str, src: str, dst: str, timeout: float,
               fetch: _Fetcher = _http_get) -> str:
    """腾讯交互翻译（网页端同款接口，无需 Key）。

    为什么放第一位：国内直连可达、质量明显好于 MyMemory，
    且不需要任何注册 —— 正是用户要的「国内免费、不过大模型 API」。
    """
    payload = {
        "header": {"fn": "auto_translation", "client_key": _TM_KEY},
        "type": "plain", "model_category": "normal",
        "source": {"lang": _tm_lang(src), "text_list": [text]},
        "target": {"lang": _tm_lang(dst)},
    }
    raw = fetch.post_json(_TRANSMART, payload, timeout)
    data = json.loads(raw)
    segs = data.get("auto_translation") or []
    if isinstance(segs, str):
        out = segs.strip()
    else:
        out = "".join(str(x) for x in segs).strip()
    if not out:
        raise FreeTranslateError("腾讯翻译无有效译文")
    return out


def _youdao(text: str, src: str, dst: str, timeout: float,
            fetch: _Fetcher = _http_get) -> str:
    """有道智云 demo 端点（免 Key，国内可达）。"""
    raw = fetch.post_form(_YOUDAO, {"q": text, "from": _yd_lang(src),
                                    "to": _yd_lang(dst)}, timeout)
    data = json.loads(raw)
    rows = data.get("translateResult") or []
    out = ""
    for row in rows:
        if row and isinstance(row, list) and row[0]:
            out += str(row[0].get("t") or "")
    out = out.strip()
    if not out:
        raise FreeTranslateError("有道翻译无有效译文")
    return out


# 提供方签名：(文本, 源语言, 目标语言, 超时, fetcher) -> 译文
# 顺序 = 先用国内（无 Key、可达性好），境外两个只做兜底。
_PROVIDERS: list[tuple[str, Callable[..., str]]] = [
    ("腾讯翻译", _transmart),
    ("有道翻译", _youdao),
    ("MyMemory", _mymemory),
    ("Google", _google),
]


def _split(text: str, limit: int = _MAX_CHUNK) -> list[str]:
    """长句按标点/空白切段（免费接口有长度上限）。

    D08：单个无标点 piece 超过 limit 时按 limit 硬切成多段，
    否则上千字的连续文本会作为 1 个 chunk 突破接口上限导致全链降级。
    """
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    buf = ""
    for piece in re.split(r"(?<=[，。；：！？,.;:!?\s])", text):
        if not piece:
            continue
        if len(piece) > limit:
            # 先结算已攒的 buf，再对超长 piece 按 limit 硬切。
            if buf:
                out.append(buf)
                buf = ""
            for i in range(0, len(piece), limit):
                out.append(piece[i:i + limit])
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
        # 审计 S05/S16：默认只在境内两家（腾讯 transmart / 有道）之间降级；
        # 境外端点（MyMemory / Google）必须用户显式开启才兜底。
        self.allow_overseas = False
        self.timeout = float(timeout)
        self.last_error = ""
        self.last_detail = ""       # 原始错误串（日志/诊断用，不直接给用户看）
        self.last_provider = ""
        # 用户在设置里手填的代理（host:port）；留空 = 自动探测。
        self.proxy = _split_hostport(proxy)      # 走 setter（此时 _fetch 尚未建）
        # 测试可注入纯 GET 函数；生产走带代理回退的 _Fetcher（GET + POST）
        self._fetch = _Fetcher(fetcher, self._proxy)
        self._providers = providers or _PROVIDERS

    # D12：main.py 的 `self.free_translator.proxy = ...` 会走这个 setter，
    # 同步 _Fetcher.prefer（默认 GET 闭包 / POST 都读它），新代理立即生效；
    # 保持 self.proxy 可读、构造参数不变。
    @property
    def proxy(self) -> str:
        return self._proxy

    @proxy.setter
    def proxy(self, value: str) -> None:
        self._proxy = _split_hostport(value) if isinstance(value, str) else ""
        fetch = getattr(self, "_fetch", None)
        if fetch is not None:
            fetch.prefer = self._proxy

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
        # S05：待译文本总字符上限，超出直接拒绝，绝不静默截断后外发。
        if len(text) > MAX_TEXT_CHARS:
            msg = f"选中文本过长（{len(text)} 字，上限 {MAX_TEXT_CHARS}），已取消外发"
            self.last_error = msg
            self.last_detail = msg
            raise FreeTranslateError(msg)
        errors: list[str] = []
        blocked = False
        # S05：真联网（生产）模式失败即停止、不换家重发全文，避免同一份
        # 选中文本被发往多家第三方；离线注入模式（测试用本地假 fetcher，
        # 不产生任何外发）保留完整回退链以维持兼容与可测性。
        providers = self._providers
        if not self._fetch._custom:
            # 生产路径：境内两家足够兜底，且不会把同一份文本发往 4 家。
            # 离线注入模式（测试用假 fetcher、零外发）保留完整回退链。
            providers = providers if self.allow_overseas else providers[:2]
        # D21：英→中长文本段间不插空格，其余语言用单个空格。
        joiner = "" if (dst or "").lower().startswith("zh") else " "
        for name, fn in providers:
            try:
                parts = [fn(chunk, src, dst, self.timeout, self._fetch)
                         for chunk in _split(text)]
                out = joiner.join(p for p in parts if p).strip()
                if out:
                    self.last_provider = name
                    self.last_error = ""
                    self.last_detail = ""
                    return out, name
            except Exception as e:      # 生产模式仅此一家、记录即停；离线模式换下一位
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
