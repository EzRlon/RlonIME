"""
生成应用图标（v2.2 D9 纯代码绘制，无需外部素材）。

两版（均为多尺寸 16/24/32/48/64/128/256，符合 Windows 图标规范）：
  zhiyu.ico      浅色版：翠青 #0D9488 底 + 白"工"字（主图标：exe/安装包）
  zhiyu_dark.ico 深色版：近黑底 #141519 + 金 #E3C15E 描边与字

扁平、无渐变；"工"字用矢量笔画绘制（不用字体），小尺寸下不糊。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
SIZES = [16, 24, 32, 48, 64, 128, 256]

# 两版配色：(底, 字/描边, 是否描边, 输出文件)
VARIANTS = [
    ("#0D9488", "#FFFFFF", False, ROOT / "packaging" / "zhiyu.ico"),
    ("#141519", "#E3C15E", True, ROOT / "packaging" / "zhiyu_dark.ico"),
]


def render(size: int, bg: str, fg: str, rim: bool) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    s = float(size)
    r = s * 0.16
    m = s * 0.05                       # 外边距
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(bg)))
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), r, r)
    if rim:                            # 黑金版：金描边
        pen = QPen(QColor(fg))
        pen.setWidthF(max(s * 0.045, 1.0))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(m * 1.25, m * 1.25,
                                 s - 2.5 * m, s - 2.5 * m), r * 0.9, r * 0.9)

    # "工"字（矢量笔画：上横短 / 竖 / 下横长；横画即拼音格线之意象）
    pen = QPen(QColor(fg))
    pen.setWidthF(max(s * 0.105, 1.4))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(int(s * 0.335), int(s * 0.345), int(s * 0.665), int(s * 0.345))
    p.drawLine(int(s * 0.500), int(s * 0.345), int(s * 0.500), int(s * 0.655))
    p.drawLine(int(s * 0.285), int(s * 0.655), int(s * 0.715), int(s * 0.655))
    p.end()
    return pix


def _png_bytes(pix: QPixmap) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pix.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_ico(out: Path, bg: str, fg: str, rim: bool) -> None:
    pngs = [_png_bytes(render(s, bg, fg, rim)) for s in SIZES]
    n = len(pngs)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + n * 16
    entries, blobs = b"", b""
    for s, data in zip(SIZES, pngs):
        w = h = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
        blobs += data
    out.write_bytes(header + entries + blobs)
    print(f"图标已生成: {out}  ({out.stat().st_size} bytes, {n} 个尺寸)")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    for bg, fg, rim, out in VARIANTS:
        out.parent.mkdir(parents=True, exist_ok=True)
        write_ico(out, bg, fg, rim)
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
