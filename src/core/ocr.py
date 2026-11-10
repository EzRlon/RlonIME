"""
RlonIME — 离线 OCR（Windows.Media.Ocr，v2.8）

为什么用系统内置引擎而不是 Tesseract/PaddleOCR：
  · **零模型体积**：不打包 .traineddata / onnx，整个 winrt 运行时只有 2.8 MB；
  · **离线**：不出网，符合隐私红线（选中即译那套是显式动作才联网，OCR 完全不联网）；
  · 系统语言包即模型，用户装中文/英文包就多一种识别能力。

现实约束（实测）：
  · 中文包 `zh-Hans-CN` 能识别汉字与拉丁字母，但**拉丁字母偏弱**
    （Counterbore→Counterbore 尚可，Tolerance→T01erance、1804→1894）；
  · 英文包 `en-US` 默认不装。装了之后本模块会**自动挑更合适的引擎**：
    两块引擎各跑一次，按结果里 CJK 占比决定用谁（<15% 视为英文件）。

后处理是必须的：WinRT 的中文结果会在**每个汉字之间插空格**
（"技 术 要 求"），不去掉直接送翻译会毁掉分词。
"""
from __future__ import annotations

import asyncio
import re
import sys
import threading
from dataclasses import dataclass, field

_CJK = r"\u4e00-\u9fff"
_RE_CJK_GAP = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")
_RE_CJK_PUNCT_GAP = re.compile(rf"(?<=[{_CJK}])\s+(?=[，。；：！？、（）【】“”])")
_RE_PUNCT_CJK_GAP = re.compile(rf"(?<=[，。；：！？、（）【】“”])\s+(?=[{_CJK}])")
_RE_MULTI_SPACE = re.compile(r"[ \t]{2,}")

_LANG_ZH = "zh-Hans-CN"
_LANG_EN = "en-US"


class OcrError(Exception):
    pass


@dataclass
class OcrResult:
    text: str = ""
    lang: str = ""
    lines: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    tried: list[str] = field(default_factory=list)   # 实际跑过的引擎

    def __bool__(self) -> bool:
        return bool(self.text.strip())


# ---------------------------------------------------------------- 后处理
def tidy_text(text: str) -> str:
    """清理 WinRT 中文 OCR 的"逐字空格"，并压掉多余空白/空行。"""
    if not text:
        return ""
    out = text.replace("\u3000", " ")
    for _ in range(3):                      # 连续处理，防止交替出现
        prev = out
        out = _RE_CJK_GAP.sub("", out)
        out = _RE_CJK_PUNCT_GAP.sub("", out)
        out = _RE_PUNCT_CJK_GAP.sub("", out)
        if out == prev:
            break
    out = _RE_MULTI_SPACE.sub(" ", out)
    lines = [ln.strip() for ln in out.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def cjk_ratio(text: str) -> float:
    """汉字占比（只统计非空白字符），用于判断这份内容该用哪个引擎。"""
    chars = [c for c in (text or "") if not c.isspace()]
    if not chars:
        return 0.0
    cjk = sum(1 for c in chars if "\u4e00" <= c <= "\u9fff")
    return cjk / len(chars)


# ---------------------------------------------------------------- 引擎
def available_languages() -> list[str]:
    """系统已装 OCR 语言（失败返回空表，调用方按"不可用"处理）。"""
    try:
        from winrt.windows.media.ocr import OcrEngine
    except Exception:
        return []
    try:
        return [str(l.language_tag) for l in
                OcrEngine.available_recognizer_languages]
    except Exception:
        return []


def is_available() -> bool:
    return bool(available_languages())


def _pick_languages() -> list[str]:
    """要试的引擎顺序：中文优先，英文其次（装了才试）。"""
    have = available_languages()
    out: list[str] = []
    for tag in (_LANG_ZH, _LANG_EN):
        if tag in have:
            out.append(tag)
    if not out:
        # 语言标签可能是 zh-Hans / en-GB 之类，宽松匹配
        for tag in have:
            low = tag.lower()
            if low.startswith("zh"):
                out.insert(0, tag)
            elif low.startswith("en"):
                out.append(tag)
    return out


async def _recognize_bytes(data: bytes, tag: str) -> list[str]:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(data)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_language(Language(tag))
    if engine is None:
        raise OcrError(f"无法创建 {tag} OCR 引擎")
    result = await engine.recognize_async(bitmap)
    return [str(ln.text) for ln in result.lines]


def recognize(png_bytes: bytes, prefer: str = "auto") -> OcrResult:
    """识别一张 PNG（bytes）。返回最合适引擎的结果；全失败抛 OcrError。

    线程安全：内部 asyncio.run，可以在工作线程里直接调（UI 不要在主线程调）。
    """
    import time
    t0 = time.perf_counter()
    if not png_bytes:
        raise OcrError("空图像")
    langs = _pick_languages()
    if not langs:
        raise OcrError("系统未安装 OCR 语言包（设置→时间和语言→语言→可选功能 OCR）")

    if prefer != "auto" and prefer in langs:
        langs = [prefer] + [x for x in langs if x != prefer]

    results: list[tuple[str, list[str]]] = []
    errors: list[str] = []
    for tag in langs:
        try:
            lines = asyncio.run(_recognize_bytes(png_bytes, tag))
        except Exception as e:                      # 某个引擎坏了不影响另一个
            errors.append(f"{tag}: {e}")
            continue
        results.append((tag, lines))
        # 中文引擎已经识别出足够多的汉字 → 没必要再跑英文
        joined = "\n".join(lines)
        if tag == _LANG_ZH and cjk_ratio(joined) >= 0.15:
            break

    if not results:
        raise OcrError("；".join(errors) or "OCR 无结果")

    # 选优：默认中文结果；若中文结果几乎没汉字且英文引擎有输出 → 用英文
    chosen = results[0]
    if len(results) > 1:
        zh_text = "\n".join(results[0][1])
        en_text = "\n".join(results[-1][1])
        if cjk_ratio(zh_text) < 0.15 and len(en_text) >= len(zh_text) * 0.8:
            chosen = results[-1]

    tag, lines = chosen
    text = tidy_text("\n".join(lines))
    if not text.strip():
        raise OcrError("没识别到文字（试试放大区域或调低缩放）")
    return OcrResult(text=text, lang=tag, lines=[tidy_text(x) for x in lines],
                     elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                     tried=[t for t, _ in results])


def recognize_async(png_bytes: bytes, callback, prefer: str = "auto") -> None:
    """工作线程里识别，callback(result | None, error)。"""

    def _work() -> None:
        try:
            callback(recognize(png_bytes, prefer), "")
        except Exception as e:
            callback(None, str(e))

    threading.Thread(target=_work, name="RlonOCR", daemon=True).start()


# ---------------------------------------------------------------- 自测
if __name__ == "__main__":
    print("languages:", available_languages())
    print("tidy:", repr(tidy_text("技 术 要 求 ： 未 注 圆 角 R2-R3, 沉 孔 深 度 8 m m")))
    # S13：识别出的文本可能含图纸/合同内容，默认不打到 stdout，只报是否成功
    # 与字符数；显式加 --show-text 才打印原文前 60 字。识别逻辑不变。
    args = sys.argv[1:]
    show_text = "--show-text" in args
    paths = [a for a in args if not a.startswith("--")]
    if paths:
        data = open(paths[0], "rb").read()
        r = recognize(data)
        print(f"success={bool(r.text.strip())} chars={len(r.text)}")
        if show_text:
            print(f"[detail] lang={r.lang} {r.elapsed_ms:.0f}ms tried={r.tried}")
            print("text[:60]=", repr(r.text[:60]))
