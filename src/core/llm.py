"""
智语 — 大模型客户端（可选增强层）

设计原则：
  1. **离线优先**。整个输入法的主链路（记忆库 + 术语库）完全不依赖网络，
     大模型只是「本地查不到时的那一下兜底」。
  2. **零额外依赖**。用标准库 urllib，不引入 requests / openai SDK ——
     少一个依赖，打包体积和启动时间都省一份。
  3. **绝不阻塞输入**。所有请求跑在后台线程池，主线程只收结果。
  4. **OpenAI 兼容协议**。国内主流服务（智谱 GLM、DeepSeek、通义千问、
     月之暗面 Kimi、硅基流动）和本地 Ollama 全部兼容这一套。

免费额度参考（截至打包时）：
  - 智谱 GLM-4-Flash  : 免费，中文强，延迟低   ← 默认推荐
  - DeepSeek          : 极便宜，翻译质量高
  - 通义千问 Turbo     : 有免费额度
  - 本地 Ollama       : 完全离线，零成本，适合保密场景
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 预设服务商：key -> (显示名, base_url, 默认模型, 是否需 API Key, 申请地址)
PRESETS: dict[str, dict] = {
    "glm": {
        "name": "智谱 GLM-4-Flash（免费）",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "need_key": True,
        "signup": "https://open.bigmodel.cn",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "need_key": True,
        "signup": "https://platform.deepseek.com",
    },
    "qwen": {
        "name": "通义千问（兼容模式）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-turbo",
        "need_key": True,
        "signup": "https://bailian.console.aliyun.com",
    },
    "ollama": {
        "name": "本地 Ollama（完全离线）",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b",
        "need_key": False,
        "signup": "https://ollama.com",
    },
    "openai": {
        "name": "OpenAI 兼容（自定义）",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "need_key": True,
        "signup": "",
    },
}

# 请求线程池：只留 2 条，避免用户狂按热键把服务商打爆（也避免烧钱）
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ZhiyuLLM")


class LLMError(Exception):
    pass


class LLMClient:
    """OpenAI 兼容的极简客户端。"""

    def __init__(self, cfg: dict | None = None):
        self.cfg = dict(cfg or {})
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()
        self.last_error: str = ""

    # ---------------- 配置 ----------------
    def configure(self, cfg: dict) -> None:
        with self._lock:
            self.cfg = dict(cfg or {})
            self._cache.clear()
        self.last_error = ""

    def apply_preset(self, key: str) -> dict:
        """按预设名回填 base_url / model，返回更新后的配置副本。"""
        p = PRESETS.get(key)
        if not p:
            return dict(self.cfg)
        out = dict(self.cfg)
        out["provider"] = key
        out["base_url"] = p["base_url"]
        out["model"] = p["model"]
        return out

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    @property
    def available(self) -> bool:
        if not self.cfg.get("enabled"):
            return False
        if not self.cfg.get("base_url") or not self.cfg.get("model"):
            return False
        provider = self.cfg.get("provider", "openai")
        need_key = PRESETS.get(provider, {}).get("need_key", True)
        if isinstance(provider, str) and provider.startswith("ollama"):
            need_key = False
        return bool(self.cfg.get("api_key")) or not need_key

    # ---------------- 底层请求 ----------------
    def chat(self, system: str, user: str, temperature: float = 0.2,
             max_tokens: int = 512, timeout: float = 12.0,
             json_mode: bool = False) -> str:
        """同步调用。失败抛 LLMError（调用方负责降级）。"""
        if not self.available:
            raise LLMError("大模型未启用或缺少 API Key")

        api_key = self.cfg.get("api_key", "") or "ollama"
        base_url = str(self.cfg.get("base_url", "")).rstrip("/")
        model = self.cfg.get("model", "")
        payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        # v3.0.1：企业内网/防火墙常禁止直连外网，手填代理后 AI 才能出网。
        # 这里不自动探测本地端口：base_url 可能是内网 Ollama，探测代理反而会坏事。
        proxy = str(self.cfg.get("proxy", "") or "").strip()
        try:
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler(
                        {"http": f"http://{proxy}", "https": f"http://{proxy}"}))
            else:
                opener = urllib.request.build_opener()
            with opener.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            self.last_error = f"HTTP {e.code}: {body}"
            raise LLMError(self.last_error) from e
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            reason = getattr(e, "reason", e)
            code = int(getattr(reason, "winerror", 0)
                       or getattr(reason, "errno", 0) or 0)
            if code in (10013, 18013, 10061, 10060, 10065, 10051, 10054) \
                    or isinstance(reason, TimeoutError):
                raise LLMError("网络受限 · 请在 Ⓐ 里填写代理地址") from e
            raise LLMError(self.last_error) from e

        try:
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:
            self.last_error = "响应格式异常"
            raise LLMError(self.last_error) from e

    # ---------------- 异步封装 ----------------
    def chat_async(self, system: str, user: str,
                   callback: Callable[[str | None, str], None],
                   **kw) -> None:
        """在后台线程发起请求；完成后用 callback(内容或None, 错误信息) 回传。

        回调发生在**子线程**，UI 层需自行用队列 + 定时器回到主线程。
        """

        def _work() -> None:
            try:
                out = self.chat(system, user, **kw)
                callback(out, "")
            except Exception as e:
                callback(None, str(e))

        _POOL.submit(_work)

    # ---------------- 缓存 ----------------
    def cached(self, key: str) -> str | None:
        with self._lock:
            return self._cache.get(key)

    def put_cache(self, key: str, val: str, cap: int = 512) -> None:
        with self._lock:
            if len(self._cache) >= cap:
                self._cache.clear()      # 简单粗暴但足够：翻译缓存命中窗口很短
            self._cache[key] = val


# ---------------- 提示词（集中管理，便于调优） ----------------
SYSTEM_TRANSLATE = (
    "你是资深工程翻译，服务中国的工程设计、制造与展示行业从业者。"
    "把用户给出的中文片段译成可直接写进图纸、BOM、工艺卡、技术协议的英文。"
    "要求：\n"
    "1. 术语必须使用行业标准译法，与用户提供的术语表严格一致，禁止同义词替换；\n"
    "2. 保持原句的工程语气（shall / must / typ. / ref.），该用被动就用被动；\n"
    "3. 数字与单位之间加空格（如 8 mm、M6），单位用符号（mm / ° / ×）；\n"
    "4. 只输出译文本身，不要解释、不要引号、不要 markdown；\n"
    "5. 多句时每句一行，保留原顺序。"
)

SYSTEM_REVIEW = (
    "你是工程图纸审校。用户会给你中文原文和一份已有译文，"
    "请指出译文中不符合工程英文习惯或用词不地道的地方，"
    "直接给出修订后的完整译文。只输出译文，不要解释。"
)

# v2.4.0（N1 双向）：英 → 中。术语一致性同样是第一位的，
# 反向翻译最容易出的错是把 Counterbore 译成"反沉孔"一类生造词。
SYSTEM_TRANSLATE_BACK = (
    "你是资深工程翻译，服务中国的工程设计、制造与展示行业从业者。"
    "把用户给出的英文片段译成可直接写进图纸技术要求、BOM、工艺卡、技术协议的中文。"
    "要求：\n"
    "1. 术语必须使用国内行业标准译法（Counterbore=沉孔、Chamfer=倒角、"
    "Tolerance=公差、Counterbore depth=沉孔深度），与用户提供的术语表严格一致；\n"
    "2. 保持工程语气，该省略的主语就省略，不要译成口语；\n"
    "3. 数字与单位之间保留空格（8 mm、M6），单位用符号；\n"
    "4. 只输出中文译文本身，不要解释、不要引号、不要 markdown；\n"
    "5. 多句时每句一行，保留原顺序。"
)


def build_glossary(terms: list[tuple[str, str]]) -> str:
    """把术语对渲染成提示词里的术语表。"""
    if not terms:
        return ""
    lines = [f"  {zh} = {en}" for zh, en in terms[:40]]
    return "必须遵守的术语表（左中文＝右英文）：\n" + "\n".join(lines) + "\n"


def split_lines(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[\n；;]+", text or "") if p.strip()]


if __name__ == "__main__":
    import os
    cli = LLMClient({
        "enabled": True,
        "provider": os.environ.get("ZHIYU_PROVIDER", "glm"),
        "base_url": os.environ.get("ZHIYU_BASE_URL",
                                   "https://open.bigmodel.cn/api/paas/v4"),
        "model": os.environ.get("ZHIYU_MODEL", "glm-4-flash"),
        "api_key": os.environ.get("ZHIYU_KEY", ""),
    })
    print("available:", cli.available)
    print("presets:", list(PRESETS))
    if cli.available and len(sys.argv) > 1:
        t0 = time.perf_counter()
        try:
            out = cli.chat(SYSTEM_TRANSLATE, sys.argv[1], max_tokens=256)
            print(f"({time.perf_counter() - t0:.2f}s)\n{out}")
        except LLMError as e:
            print("失败:", e)
