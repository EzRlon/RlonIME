"""
智语输入法 — 矢量图标系统

不重复造轮子：复用 Qt 的 QPainter / QPainterPath / QIcon 原生矢量绘制能力，
不引入任何外部图标库或图片资源，全部按功能语义用代码路径绘制。

设计原则：
1. **语义化** — 每个图标表达它按钮/标识的真实功能，不用装饰性字符
2. **统一规范** — 统一 16×16 视口、1.5px 圆头描边、按需着色
3. **主题适配** — 同一图标可按不同强调色渲染
4. **高 DPI** — QPixmap.setDevicePixelRatio 保证高分屏清晰
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
)

# 统一绘制视口：所有图标在此坐标系中定义，渲染时等比缩放
VB = 16.0
STROKE = 1.5


# ============================================================
# 基础几何工具
# ============================================================

def _star_path(cx: float, cy: float, r: float, inner: float) -> QPainterPath:
    """标准五角星（尖角朝上）。"""
    p = QPainterPath()
    for i in range(10):
        ang = -math.pi / 2 + math.pi * i / 5
        rr = r if i % 2 == 0 else inner
        pt = QPointF(cx + rr * math.cos(ang), cy + rr * math.sin(ang))
        if i == 0:
            p.moveTo(pt)
        else:
            p.lineTo(pt)
    p.closeSubpath()
    return p


def _spark_path(cx: float, cy: float, r: float) -> QPainterPath:
    """四角星光（内凹菱形），表达"智能生成"。"""
    k = r * 0.22
    p = QPainterPath()
    p.moveTo(cx, cy - r)
    p.quadTo(cx + k, cy - k, cx + r, cy)
    p.quadTo(cx + k, cy + k, cx, cy + r)
    p.quadTo(cx - k, cy + k, cx - r, cy)
    p.quadTo(cx - k, cy - k, cx, cy - r)
    p.closeSubpath()
    return p


def _gear_path(cx: float, cy: float, r_out: float, r_in: float,
               teeth: int = 8) -> QPainterPath:
    """齿轮轮廓：由交替内外半径的顶点构成。"""
    p = QPainterPath()
    seg = teeth * 2
    for i in range(seg):
        ang = -math.pi / 2 + 2 * math.pi * i / seg
        r = r_out if i % 2 == 0 else r_in
        pt = QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang))
        if i == 0:
            p.moveTo(pt)
        else:
            p.lineTo(pt)
    p.closeSubpath()
    return p


def _round_rect(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(QRectF(x, y, w, h), r, r)
    return p


# ============================================================
# 图标定义：每个函数返回 (描边路径列表, 填充路径列表)
# ============================================================

def _p_suggest():
    """智能推荐 — 主星 + 伴星，表示自动生成候选。"""
    return [], [_spark_path(6.0, 6.6, 4.6), _spark_path(12.2, 11.6, 2.6)]


def _p_terms():
    """词库候选 — 三行条目清单，左侧色点强调。"""
    stroke = QPainterPath()
    for y, w in ((3.6, 9.6), (8.0, 11.4), (12.4, 7.6)):
        stroke.moveTo(2.2, y)
        stroke.lineTo(2.2 + w, y)
    return [stroke], []


def _p_identity():
    """身份细化 — 人像剪影，表示按职业身份配置。"""
    stroke = QPainterPath()
    # 头
    stroke.addEllipse(QRectF(5.6, 1.6, 4.8, 4.8))
    # 肩
    stroke.moveTo(2.4, 14.0)
    stroke.cubicTo(2.4, 10.2, 4.8, 8.8, 8.0, 8.8)
    stroke.cubicTo(11.2, 8.8, 13.6, 10.2, 13.6, 14.0)
    return [stroke], []


def _p_settings():
    """设置 — 齿轮 + 中心轴孔。"""
    gear = _gear_path(8.0, 8.0, 7.0, 5.0, 8)
    hole = QPainterPath()
    hole.addEllipse(QRectF(6.2, 6.2, 3.6, 3.6))
    return [gear, hole], []


def _p_keyboard():
    """文字输入 — 键盘轮廓 + 按键 + 空格键。"""
    frame = _round_rect(1.2, 3.6, 13.6, 8.8, 1.8)
    keys = QPainterPath()
    for x in (3.2, 5.8, 8.4, 11.0):
        keys.moveTo(x, 6.4)
        keys.lineTo(x + 1.6, 6.4)
    for x in (3.2, 5.8, 8.4, 11.0):
        keys.moveTo(x, 8.8)
        keys.lineTo(x + 1.6, 8.8)
    return [frame, keys], []


def _p_scene():
    """场景识别 — 准星，表示自动探测工作环境。"""
    ring = QPainterPath()
    ring.addEllipse(QRectF(3.8, 3.8, 8.4, 8.4))
    cross = QPainterPath()
    cross.moveTo(8.0, 0.9);  cross.lineTo(8.0, 2.9)
    cross.moveTo(8.0, 13.1); cross.lineTo(8.0, 15.1)
    cross.moveTo(0.9, 8.0);  cross.lineTo(2.9, 8.0)
    cross.moveTo(13.1, 8.0); cross.lineTo(15.1, 8.0)
    return [ring, cross], []


def _p_drawing():
    """工程图 — 图框 + 尺寸标注，表示图纸标注场景。"""
    sheet = _round_rect(1.4, 2.2, 13.2, 11.6, 1.4)
    mark = QPainterPath()
    # 内部零件轮廓
    mark.moveTo(4.6, 5.2)
    mark.lineTo(11.4, 5.2)
    mark.lineTo(11.4, 8.4)
    mark.lineTo(4.6, 8.4)
    mark.closeSubpath()
    # 尺寸线
    mark.moveTo(4.6, 11.6)
    mark.lineTo(11.4, 11.6)
    mark.moveTo(4.6, 10.4); mark.lineTo(4.6, 12.8)
    mark.moveTo(11.4, 10.4); mark.lineTo(11.4, 12.8)
    return [sheet, mark], []


def _p_english():
    """英文上屏 — 字母 A，表示输出英文术语。"""
    a = QPainterPath()
    a.moveTo(2.6, 13.6)
    a.lineTo(6.4, 2.4)
    a.lineTo(10.2, 13.6)
    cross = QPainterPath()
    cross.moveTo(4.2, 9.6)
    cross.lineTo(8.6, 9.6)
    return [a, cross], []


def _p_close():
    """关闭 — 叉形。"""
    p = QPainterPath()
    p.moveTo(4.2, 4.2); p.lineTo(11.8, 11.8)
    p.moveTo(11.8, 4.2); p.lineTo(4.2, 11.8)
    return [p], []


def _p_back():
    """返回 — 左向箭头。"""
    p = QPainterPath()
    p.moveTo(13.0, 8.0); p.lineTo(3.2, 8.0)
    p.moveTo(7.0, 4.0);  p.lineTo(3.0, 8.0); p.lineTo(7.0, 12.0)
    return [p], []


def _p_forward():
    """切换 — 右向箭头。"""
    p = QPainterPath()
    p.moveTo(3.0, 8.0); p.lineTo(12.8, 8.0)
    p.moveTo(9.0, 4.0); p.lineTo(13.0, 8.0); p.lineTo(9.0, 12.0)
    return [p], []


def _p_check():
    """审核/校核 — 圆环 + 对勾。"""
    ring = QPainterPath()
    ring.addEllipse(QRectF(1.8, 1.8, 12.4, 12.4))
    tick = QPainterPath()
    tick.moveTo(4.8, 8.2)
    tick.lineTo(7.0, 10.4)
    tick.lineTo(11.4, 5.6)
    return [ring, tick], []


def _p_ai():
    """AI 增强 — 芯片 + 星，表示 AI 辅助推荐。"""
    chip = _round_rect(2.6, 2.6, 8.8, 8.8, 1.6)
    core = QPainterPath()
    core.addEllipse(QRectF(5.6, 5.6, 2.8, 2.8))
    return [chip, core], [_spark_path(12.6, 12.0, 2.8)]


def _p_star():
    """常用/高分 — 实心五角星。"""
    return [], [_star_path(8.0, 8.4, 6.6, 2.8)]


def _p_language():
    """中英对照 — 地球经纬网格，表示跨语言映射。"""
    circle = QPainterPath()
    circle.addEllipse(QRectF(2.2, 2.2, 11.6, 11.6))
    grid = QPainterPath()
    grid.moveTo(2.2, 8.0); grid.lineTo(13.8, 8.0)
    grid.moveTo(8.0, 2.2); grid.lineTo(8.0, 13.8)
    grid.moveTo(8.0, 2.2)
    grid.cubicTo(4.6, 5.0, 4.6, 11.0, 8.0, 13.8)
    grid.moveTo(8.0, 2.2)
    grid.cubicTo(11.4, 5.0, 11.4, 11.0, 8.0, 13.8)
    return [circle, grid], []


def _p_layers():
    """图层/分类 — 三层堆叠，表示领域分组。"""
    p = QPainterPath()
    # 顶层菱形
    p.moveTo(8.0, 1.6)
    p.lineTo(14.2, 5.4)
    p.lineTo(8.0, 9.2)
    p.lineTo(1.8, 5.4)
    p.closeSubpath()
    # 下方两层
    p.moveTo(1.8, 8.6)
    p.lineTo(8.0, 12.4)
    p.lineTo(14.2, 8.6)
    p.moveTo(1.8, 11.4)
    p.lineTo(8.0, 14.6)
    p.lineTo(14.2, 11.4)
    return [p], []


def _p_plus():
    """加词条 — 细十字（顶行 ＋）。"""
    p = QPainterPath()
    p.moveTo(8.0, 3.4); p.lineTo(8.0, 12.6)
    p.moveTo(3.4, 8.0); p.lineTo(12.6, 8.0)
    return [p], []


def _p_grid():
    """行业/分类 — 四宫格（顶行 🏷 位）。"""
    p = QPainterPath()
    for x, y in ((2.2, 2.2), (8.8, 2.2), (2.2, 8.8), (8.8, 8.8)):
        p.addRoundedRect(QRectF(x, y, 5.0, 5.0), 1.4, 1.4)
    return [p], []


def _p_pin():
    """图钉 — 候选条固定/跟随切换（v2.7 U09）。"""
    body = QPainterPath()
    # 钉帽（横向圆条）+ 钉身（细杆）+ 针尖
    body.moveTo(3.4, 4.2)
    body.lineTo(12.6, 4.2)
    body.moveTo(5.2, 4.2)
    body.lineTo(5.8, 9.0)
    body.lineTo(10.2, 9.0)
    body.lineTo(10.8, 4.2)
    body.moveTo(8.0, 9.0)
    body.lineTo(8.0, 13.2)
    return [body], []


def _p_pause():
    """暂停接管 — 双竖条。"""
    p = QPainterPath()
    p.moveTo(5.6, 3.8); p.lineTo(5.6, 12.2)
    p.moveTo(10.4, 3.8); p.lineTo(10.4, 12.2)
    return [p], []


def _p_play():
    """恢复接管 — 空心三角。"""
    p = QPainterPath()
    p.moveTo(5.4, 3.6)
    p.lineTo(12.4, 8.0)
    p.lineTo(5.4, 12.4)
    p.closeSubpath()
    return [p], []


def _p_info():
    """关于 — 圆环 + 信息点。"""
    ring = QPainterPath()
    ring.addEllipse(QRectF(2.0, 2.0, 12.0, 12.0))
    stem = QPainterPath()
    stem.moveTo(8.0, 7.4); stem.lineTo(8.0, 11.4)
    dot = QPainterPath()
    dot.addEllipse(QRectF(7.35, 4.4, 1.3, 1.3))
    return [ring, stem], [dot]


# 图标名 -> 构建函数
_BUILDERS: dict[str, object] = {
    "suggest": _p_suggest,
    "terms": _p_terms,
    "identity": _p_identity,
    "settings": _p_settings,
    "keyboard": _p_keyboard,
    "scene": _p_scene,
    "drawing": _p_drawing,
    "english": _p_english,
    "close": _p_close,
    "back": _p_back,
    "forward": _p_forward,
    "check": _p_check,
    "ai": _p_ai,
    "star": _p_star,
    "language": _p_language,
    "layers": _p_layers,
    "plus": _p_plus,
    "grid": _p_grid,
    "pin": _p_pin,
    "pause": _p_pause,
    "play": _p_play,
    "info": _p_info,
}


# ============================================================
# 渲染 API
# ============================================================

def paint_icon(painter: QPainter, name: str, size: int, color: QColor,
               stroke_scale: float = 1.0) -> None:
    """在当前 painter 的 (0,0,size,size) 区域绘制矢量图标。"""
    fn = _BUILDERS.get(name)
    if fn is None:
        return
    strokes, fills = fn()          # type: ignore[operator]
    k = size / VB

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(size / 2.0, size / 2.0)
    painter.scale(k, k)
    painter.translate(-VB / 2.0, -VB / 2.0)
    # 描边宽度需抵消整体缩放，才能得到恒定像素宽度
    delta = stroke_scale - 1.0
    pen_w = max(STROKE + delta, 1.0) / max(k, 0.01)

    for path in fills:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)

    if strokes:
        pen = QPen(color)
        pen.setWidthF(pen_w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for path in strokes:
            painter.drawPath(path)

    painter.restore()


def make_pixmap(name: str, size: int = 16, color: str | QColor = "#4C6EF5",
                dpr: float = 2.0) -> QPixmap:
    """生成带高 DPI 支持的图标 QPixmap。"""
    if isinstance(color, str):
        color = QColor(color)
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_icon(p, name, int(size * dpr), color)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def make_icon(name: str, size: int = 16, color: str | QColor = "#4C6EF5") -> QIcon:
    """生成多尺寸 QIcon，供按钮直接使用。"""
    if isinstance(color, str):
        color = QColor(color)
    icon = QIcon()
    for s in (size, int(size * 1.5), size * 2):
        icon.addPixmap(make_pixmap(name, s, color))
    return icon


# ============================================================
# 自绘图标控件（用于替代字符图标）
# ============================================================

try:
    from PySide6.QtWidgets import QWidget

    class IconWidget(QWidget):
        """只画矢量图标的轻量控件，可动态改色/换图标/悬停变色。"""

        def __init__(self, name: str, size: int = 14, color: str = "#34C759",
                     parent=None):
            super().__init__(parent)
            self._name = name
            self._color = QColor(color)
            self._hover_color: QColor | None = None
            self._hover = False
            self.setFixedSize(size, size)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        def set_clickable(self, hover_color: str | QColor | None = None) -> None:
            """转为可点击按钮：接收鼠标事件 + 手型光标 + 悬停变色。"""
            self.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if hover_color is not None:
                self._hover_color = QColor(hover_color)

        def set_hover_color(self, color: str | QColor) -> None:
            self._hover_color = QColor(color)
            self.update()

        def set_color(self, color: str | QColor) -> None:
            self._color = QColor(color)
            self.update()

        def set_icon(self, name: str) -> None:
            self._name = name
            self.update()

        def enterEvent(self, event) -> None:  # noqa: N802
            if self._hover_color is not None:
                self._hover = True
                self.update()

        def leaveEvent(self, event) -> None:  # noqa: N802
            if self._hover:
                self._hover = False
                self.update()

        def paintEvent(self, event) -> None:  # noqa: N802
            p = QPainter(self)
            c = (self._hover_color
                 if (self._hover and self._hover_color is not None)
                 else self._color)
            paint_icon(p, self._name, self.width(), c)
            p.end()

except ImportError:      # 无 GUI 环境（如纯逻辑测试）
    IconWidget = None    # type: ignore[assignment,misc]


ICON_NAMES = list(_BUILDERS)
