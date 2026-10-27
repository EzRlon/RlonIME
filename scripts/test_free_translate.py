"""v2.6 无 Key 免费翻译回归（全程离线，不真联网）

验的是"没有 API Key 也能翻译"这条兜底链路：
  1. MyMemory 解析（含 HTML 实体反转义）
  2. MyMemory 失败 → 自动落到 Google 免 Key 端点
  3. 两个都失败 → 明确报错，不吞内容
  4. 长句自动切段（免费接口有长度上限）
  5. 与 Translator 的接线：无 LLM 才走免费；有 LLM 不走；纯本地不走
  6. 免费译稿仍过术语强制对齐（与 AI 路径同一把尺子）

    python scripts/test_free_translate.py
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          f"{'' if cond else '  ' + str(extra)}")


def mymemory_payload(text: str) -> str:
    return json.dumps({
        "responseData": {"translatedText": text, "match": 1.0},
        "responseStatus": 200,
    }, ensure_ascii=False)


def google_payload(text: str) -> str:
    return json.dumps([[[text, "src", None, None]], None, "en"], ensure_ascii=False)


def make_fetch(mymemory=None, google=None, calls=None):
    """按域名返回不同 payload；传 Exception 实例表示该源失败。"""
    def _fetch(url: str, timeout: float) -> str:
        if calls is not None:
            calls.append(url)
        if "mymemory" in url:
            if isinstance(mymemory, Exception):
                raise mymemory
            return mymemory if isinstance(mymemory, str) else mymemory_payload("X")
        if "googleapis" in url:
            if isinstance(google, Exception):
                raise google
            return google if isinstance(google, str) else google_payload("Y")
        raise AssertionError(f"未知 URL: {url}")
    return _fetch


def main() -> int:
    from core.free_translate import (FreeTranslateError, FreeTranslator,
                                     _split)

    print("[1] MyMemory 解析")
    ft = FreeTranslator(fetcher=make_fetch(
        mymemory=mymemory_payload("A counterbore 8 mm deep &amp; deburred")))
    out, name = ft.translate("该零件底部需加工沉孔，深度 8 mm")
    ck("取出译文", out.startswith("A counterbore 8 mm"), out)
    ck("HTML 实体已反转义", "&amp;" not in out and "& deburred" in out, out)
    ck("提供方=MyMemory", name == "MyMemory", name)

    print("[2] MyMemory 故障 → 自动落 Google")
    calls: list[str] = []
    ft = FreeTranslator(fetcher=make_fetch(
        mymemory=RuntimeError("timeout"),
        google=google_payload("A counterbore shall be machined."),
        calls=calls))
    out, name = ft.translate("该零件底部需加工沉孔")
    ck("仍然拿到译文", out == "A counterbore shall be machined.", out)
    ck("提供方=Google", name == "Google", name)
    ck("两个源都试过", len(calls) == 2, calls)

    print("[3] 两源全失败 → 明确报错")
    ft = FreeTranslator(fetcher=make_fetch(
        mymemory=RuntimeError("dns fail"), google=RuntimeError("blocked")))
    try:
        ft.translate("测试")
        ck("应抛 FreeTranslateError", False, "没抛")
    except FreeTranslateError as e:
        msg = str(e)
        ck("抛 FreeTranslateError", True)
        ck("错误信息含两个源", "MyMemory" in msg and "Google" in msg, msg)

    print("[4] 长句切段")
    long_text = "，".join(["该零件底部需加工沉孔并去毛刺"] * 60)   # > 480 字
    chunks = _split(long_text)
    ck("长文本被切成多段", len(chunks) > 1, len(chunks))
    ck("每段不超上限", all(len(c) <= 480 for c in chunks),
       max(len(c) for c in chunks))
    ck("拼回原文不丢字", "".join(chunks) == long_text)
    calls = []
    ft = FreeTranslator(fetcher=make_fetch(mymemory=mymemory_payload("ok"),
                                           calls=calls))
    ft.translate(long_text)
    ck("分段各发一次请求", len(calls) == len(chunks), (len(calls), len(chunks)))

    print("[5] 关闭开关 → 不联网")
    ft = FreeTranslator(enabled=False, fetcher=make_fetch())
    try:
        ft.translate("测试")
        ck("关闭后应拒绝", False)
    except FreeTranslateError:
        ck("关闭后拒绝翻译（不偷偷联网）", True)

    # ---------------- [6] 与 Translator 的接线 ----------------
    print("[6] Translator 接线：无 Key 才走免费")
    from core.engine import TermEngine
    from core.llm import LLMClient
    from core.memory import TranslationMemory
    from core.models import SceneContext
    from core.translate import Translator
    import tempfile

    tmp = Path(tempfile.gettempdir()) / "rlon_free_selftest.db"
    tmp.unlink(missing_ok=True)
    eng = TermEngine()
    mem = TranslationMemory(tmp)
    scene = SceneContext(profession_key="mechanical")
    src = "今天下午三点开会"

    def run(tr):
        done = threading.Event()
        box: list = []

        def cb(r):
            box.append(r)
            done.set()

        tr.translate_async(src, scene, cb)
        done.wait(4.0)
        return box[0] if box else None

    # 6.1 无 LLM + 免费开
    llm_off = LLMClient({"enabled": False})
    free = FreeTranslator(fetcher=make_fetch(
        mymemory=mymemory_payload("We will meet at three this afternoon.")))
    r = run(Translator(eng, mem, llm_off, 0.85, free=free))
    ck("无 Key 时走免费翻译", bool(r) and r.source == "free",
       r.source if r else None)
    ck("来源标签=免费翻译", bool(r) and r.source_label == "免费翻译",
       r.source_label if r else None)
    ck("带提供方", bool(r) and r.provider == "MyMemory",
       r.provider if r else None)
    ck("译文可用", bool(r) and r.text.startswith("We will meet"), r.text if r else "")

    # 6.2 免费关 + 无 LLM → failed
    r = run(Translator(eng, mem, llm_off, 0.85,
                       free=FreeTranslator(enabled=False)))
    ck("关掉免费且无 Key → failed", bool(r) and r.source == "failed",
       r.source if r else None)
    ck("失败带原因", bool(r) and r.error, r.error if r else "")

    # 6.3 纯本地（allow_llm=False）→ 不调免费
    calls = []
    free = FreeTranslator(fetcher=make_fetch(mymemory=mymemory_payload("x"),
                                             calls=calls))
    done = threading.Event()
    box = []
    Translator(eng, mem, llm_off, 0.85, free=free).translate_async(
        src, scene, lambda x: (box.append(x), done.set()), allow_llm=False)
    done.wait(2.0)
    ck("纯本地模式不联网", not calls, calls)
    ck("纯本地模式返回 failed", box and box[0].source == "failed",
       box[0].source if box else None)

    # 6.4 英→中免费回译
    free = FreeTranslator(fetcher=make_fetch(
        mymemory=mymemory_payload("今天下午三点开会")))
    done = threading.Event()
    box = []
    Translator(eng, mem, llm_off, 0.85, free=free).translate_back_async(
        "We will meet at three this afternoon", scene,
        lambda x: (box.append(x), done.set()))
    done.wait(4.0)
    r = box[0] if box else None
    ck("英→中走免费回译", bool(r) and r.source == "free" and r.direction == "en2zh",
       (r.source, r.direction) if r else None)
    ck("回译标签=免费回译", bool(r) and r.source_label == "免费回译",
       r.source_label if r else None)

    mem.close()
    tmp.unlink(missing_ok=True)

    # ---------------- v3.0.1：直连被系统拒绝 → 自动改走本机代理 ----------------
    print("[8] 直连被拒（WinError 10013/18013）时的代理回退")
    import urllib.error
    import core.free_translate as ft

    ck("18013 判定为链路受限",
       ft._is_link_blocked(urllib.error.URLError(OSError(18013, "denied"))))
    ck("10013 判定为链路受限",
       ft._is_link_blocked(urllib.error.URLError(OSError(10013, "denied"))))
    ck("DNS 失败判定为链路受限",
       ft._is_link_blocked(urllib.error.URLError(
           __import__("socket").gaierror(11001, "getaddrinfo failed"))))
    ck("HTTP 逻辑错误不当作链路问题",
       not ft._is_link_blocked(ValueError("bad json")))

    ck("代理串归一：带 scheme", ft._split_hostport("http://127.0.0.1:7890") == "127.0.0.1:7890")
    ck("代理串归一：带账号", ft._split_hostport("user:pw@127.0.0.1:7897") == "127.0.0.1:7897")
    ck("代理串归一：非法串丢弃", ft._split_hostport("not-a-proxy") == "")

    real_open, real_port, real_cand = ft._open, ft._port_open, ft._candidate_proxies
    calls: list[str] = []

    def fake_open(url, timeout, proxy, data=None, headers=None):
        calls.append(proxy)
        if proxy == "":
            raise urllib.error.URLError(OSError(10013, "拒绝访问"))
        return '{"ok":1}'

    try:
        ft._WORKING_ROUTE = None
        ft._open = fake_open
        ft._port_open = lambda hp, timeout=0.12: hp == "127.0.0.1:7890"
        ft._candidate_proxies = lambda: ["127.0.0.1:7891", "127.0.0.1:7890"]
        body = ft._http_get("https://example.invalid", 3.0)
        ck("直连被拒后改走可用代理", body == '{"ok":1}', body)
        ck("记住可用路由", ft._WORKING_ROUTE == "127.0.0.1:7890", ft._WORKING_ROUTE)
        ck("跳过没在监听的端口", calls == ["", "127.0.0.1:7890"], calls)

        calls.clear()
        ft._http_get("https://example.invalid", 3.0)
        ck("之后直接用缓存路由（不再试直连）",
           calls == ["127.0.0.1:7890"], calls)

        # 非链路错误不做代理探测
        ft._WORKING_ROUTE = None
        calls.clear()
        ft._open = lambda url, timeout, proxy, data=None, headers=None: (
            _ for _ in ()).throw(ValueError("坏 JSON"))
        try:
            ft._http_get("https://example.invalid", 3.0)
            raised = False
        except ValueError:
            raised = True
        ck("非链路错误直接抛出", raised and calls == [], calls)

        # 用户看到的文案
        def boom(text, src, dst, timeout, fetch):
            raise urllib.error.URLError(OSError(18013, "拒绝访问"))

        tr_bad = FreeTranslator(providers=[("MyMemory", boom)])
        try:
            tr_bad.translate("沉孔")
            msg = ""
        except ft.FreeTranslateError as e:
            msg = str(e)
        ck("网络受限给可操作提示（不甩 urlopen 堆栈）",
           "网络受限" in msg and "urlopen" not in msg, msg)
        ck("原始错误留在 last_detail 供诊断",
           "18013" in tr_bad.last_detail or "WinError" in tr_bad.last_detail,
           tr_bad.last_detail)

        # 手填代理（设置里的「代理」）优先于自动探测
        ft._WORKING_ROUTE = None
        ft._open = fake_open                 # proxy=="" 时失败，其余成功
        calls.clear()
        tr_p = FreeTranslator(proxy="http://127.0.0.1:7890")
        tr_p._providers = [(
            "MyMemory",
            lambda text, src, dst, timeout, fetch: fetch("https://x", timeout))]
        out = tr_p.translate("沉孔")
        ck("手填代理作为首选路由", calls == ["127.0.0.1:7890"], calls)
        ck("手填代理能拿到译文", out[0] == '{"ok":1}', out)

        # 手填错了不能把链路堵死：继续回落自动探测
        ft._WORKING_ROUTE = None
        calls.clear()

        def open_by_route(url, timeout, proxy, data=None, headers=None):
            calls.append(proxy)
            if proxy in ("", "127.0.0.1:9999"):
                raise urllib.error.URLError(OSError(10013, "denied"))
            return '{"ok":2}'

        ft._open = open_by_route
        ft._candidate_proxies = lambda: ["127.0.0.1:7890"]
        ft._port_open = lambda hp, timeout=0.12: True
        tr_p2 = FreeTranslator(proxy="127.0.0.1:9999")
        tr_p2._providers = [(
            "MyMemory",
            lambda text, src, dst, timeout, fetch: fetch("https://x", timeout))]
        out2 = tr_p2.translate("沉孔")
        ck("手填代理不可用时继续自动探测",
           out2[0] == '{"ok":2}' and calls == ["127.0.0.1:9999", "", "127.0.0.1:7890"],
           calls)
    finally:
        ft._open, ft._port_open, ft._candidate_proxies = real_open, real_port, real_cand
        ft._WORKING_ROUTE = None

    # ---------------- [9] 国内免费接口（v4.0.3） ----------------
    print("[9] 国内免费接口：腾讯交互翻译 / 有道")
    from core.free_translate import (_PROVIDERS, _transmart, _youdao,
                                     _yd_lang, _tm_lang)

    ck("默认首选=腾讯翻译", _PROVIDERS[0][0] == "腾讯翻译", _PROVIDERS[0][0])
    ck("第二备选=有道翻译", _PROVIDERS[1][0] == "有道翻译", _PROVIDERS[1][0])
    ck("语言码：腾讯 zh/en", (_tm_lang("zh-CN"), _tm_lang("en")) == ("zh", "en"))
    ck("语言码：有道 zh-CHS/en",
       (_yd_lang("zh-CN"), _yd_lang("en")) == ("zh-CHS", "en"))

    class _Stub:
        """假 fetcher：只记录调用并返回预置 JSON。"""

        def __init__(self, raw: str):
            self.raw = raw
            self.calls: list = []

        def post_json(self, url, payload, timeout):
            self.calls.append(("json", url, payload))
            return self.raw

        def post_form(self, url, fields, timeout):
            self.calls.append(("form", url, fields))
            return self.raw

    stub = _Stub(json.dumps(
        {"header": {"ret_code": "succ"},
         "auto_translation": ["Counterbore 8 mm deep."]}))
    out = _transmart("沉孔深8毫米", "zh-CN", "en", 4.0, stub)
    ck("腾讯：取出译文", out == "Counterbore 8 mm deep.", out)
    _kind, _url, payload = stub.calls[0]
    ck("腾讯：POST JSON + fn=auto_translation",
       _kind == "json" and payload["header"]["fn"] == "auto_translation",
       payload.get("header"))
    ck("腾讯：源/目标语言正确",
       payload["source"]["lang"] == "zh" and payload["target"]["lang"] == "en",
       (payload["source"], payload["target"]))

    stub2 = _Stub(json.dumps(
        {"errorCode": 0, "translateResult": [[{"t": "Counterbore"}],
                                             [{"t": " 8 mm"}]]}))
    out = _youdao("沉孔8毫米", "zh-CN", "en", 4.0, stub2)
    ck("有道：拼接多段译文", out == "Counterbore 8 mm", out)
    ck("有道：POST 表单",
       stub2.calls[0][0] == "form" and stub2.calls[0][2]["from"] == "zh-CHS",
       stub2.calls[0][2])

    class _BadStub(_Stub):
        def post_json(self, url, payload, timeout):
            return json.dumps({"auto_translation": []})

    try:
        _transmart("x", "zh", "en", 4.0, _BadStub("{}"))
        ck("腾讯空译文应报错", False)
    except Exception:
        ck("腾讯空译文 → 抛错（换下一个源）", True)

    # 提供方链：第一个失败，第二个成功
    def _boom(*a, **k):
        raise RuntimeError("transmart down")

    chain = FreeTranslator(
        fetcher=make_fetch(), providers=[("腾讯翻译", _boom),
                                         ("有道翻译", lambda *a, **k: "ok")])
    out2, name2 = chain.translate("测试")
    ck("链路回退：第一个挂了下一位接上", out2 == "ok" and name2 == "有道翻译",
       (out2, name2))

    print()
    print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print("  失败项:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
