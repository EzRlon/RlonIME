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
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_MYMEMORY = "https://api.mymemory.translated.net/get"
_GOOGLE = "https://translate.googleapis.com/translate_a/single"
_UA = "RlonIME/2.6 (Windows IME; free translate)"
_MAX_CHUNK = 480          # MyMemory 匿名单次上限 500 字节，留余量
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="RlonFree")


class FreeTranslateError(Exception):
    pass


def _http_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


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
                 providers: list | None = None):
        self.enabled = bool(enabled)
        self.timeout = float(timeout)
        self.last_error = ""
        self.last_provider = ""
        self._fetch = fetcher or _http_get
        self._providers = providers or _PROVIDERS

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
        for name, fn in self._providers:
            try:
                parts = [fn(chunk, src, dst, self.timeout, self._fetch)
                         for chunk in _split(text)]
                out = " ".join(p for p in parts if p).strip()
                if out:
                    self.last_provider = name
                    self.last_error = ""
                    return out, name
            except Exception as e:      # 换下一个提供方
                errors.append(f"{name}: {e}")
        self.last_error = "；".join(errors) or "无可用免费翻译服务"
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
