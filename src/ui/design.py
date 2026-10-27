"""
RlonIME — 统一设计系统（Design Tokens，v2.2）

全应用唯一视觉来源：候选条、悬停卡、弹窗全部引用这里，改一处即全局一致。

两套配色（v2.2 定稿，弃用蓝白）：
  LIGHT — 翠青 · 白（与 SolidWorks / AutoCAD / Office 深色标题栏共存的默认）
  DARK  — 黑 · 金（夜间出图、深色主题 IDE 场景）

红色仅作错误态。
"""
from __future__ import annotations


class _Base:
    """两套主题共享的几何与字号（不随主题变化）。v2.2 更小巧。"""

    # 尺寸
    BAR_H = 30                  # 单行模式参考高（结果态/提示态）
    CHIP_H = 24                 # 单个候选胶囊高
    TOP_ROW_H = 20              # 顶行（拼音 + 图标列）高
    EN_ROW_H = 18               # 译法列单行高
    SUG_H = 26                  # 联想态行高
    BAR_MAX_W = 560             # 宽度上限（v2.2：640→560）
    BAR_MIN_W = 160
    RADIUS = 10                 # 卡片圆角
    CHIP_RADIUS = 5             # 胶囊圆角
    PAD_X = 8
    GAP = 4

    # 字号阶梯（v2.2 新装默认 中文11 / 英文9，可在 config.json 微调）
    FS_ZH = 11
    FS_EN = 9
    FS_IDX = 9
    FS_TIP = 10

    # ---- 旧版大面板沿用的几何名（保持向后兼容，勿删）----
    WIDTH = 560
    ROW_H = 38
    TOP_H = 28
    INPUT_H = 36
    FOOT_H = 28
    VISIBLE_ROWS = 5

    # ---- 旧版语义色名（指向新令牌；悬停底色仍被候选条引用）----
    BG_BAR = "#FAFBFD"
    PRIMARY_SOFT_2 = "#D8F0DC"
    ACCENT_EN = "#0E7490"
    ACCENT_SUG = "#0E9F6E"
    ACCENT_OK = "#1E9E5A"
    OK_SOFT = "#E7F6ED"

    # ---- MD3 几何（弹窗卡片化界面）----
    MD3_CARD_RADIUS = 10
    MD3_SWITCH_W = 36
    MD3_SWITCH_H = 20
    MD3_SWITCH_KNOB = 15


class LIGHT(_Base):
    """青苹果绿 · 白（v4.0.2：主色 #34C759）。"""
    BG = "#FFFFFF"
    BG_TOP = "#FFFFFF"
    BG_BOTTOM = "#F6FAF8"
    BG_HOVER = "#F3F8F6"
    LINE = "#DCE8E4"
    LINE_SOFT = "#EFF6F3"

    TEXT = "#17201D"
    TEXT_SUB = "#4A5A55"
    TEXT_MUTED = "#708078"
    TEXT_FAINT = "#97A69F"

    # v4.0.2：主色 #4ADE80 → #34C759。上一个亮绿饱和度高、渐变顶又接近白，
    # 长时间盯着候选条会"晃眼"（用户反馈）。现在：
    #   · 主色换成苹果绿 #34C759（低一档亮度）；
    #   · 渐变顶只比主色亮 1.11:1（原来 #86EFAC 与主色差 1.6:1），
    #     立体感靠色相微差，不靠高光；
    #   · 选中态文字 #052E16（6.7:1），副行深绿 #0B4A21（4.7:1，原 #166534 只有 3.2:1）；
    #   · 悬停/浅底也同步降饱和，避免大片荧光绿。
    PRIMARY = "#34C759"
    # 小字号主色文字必须更深：纯主色在白底上只有 2.2:1，9~11px 会糊成一片。
    # #15803D 是 4.6:1，够清楚又不发黑。
    PRIMARY_TEXT = "#15803D"
    PRIMARY_HI = "#45D06B"
    PRIMARY_SOFT = "#DFF6E3"
    SEL_TEXT = "#052E16"
    SEL_SUB = "#0B4A21"

    EN = "#0E7490"
    OK = "#1E9E5A"
    OK_SOFT = "#E7F6ED"
    WARN = "#B4600A"
    WARN_SOFT = "#FFF4E5"
    ERR = "#D64545"
    ERR_SOFT = "#FCEAEA"
    AI = "#0E9F6E"
    AI_HI = "#14B47F"
    AI_SOFT = "#E4F6EE"

    SHADOW = (0, 0, 0, 52)      # RGBA
    OPACITY = 0.99

    # ---- MD3 tonal 令牌（弹窗；映射上方现有色值，不另造色）----
    MD3_SURFACE = BG
    MD3_SURFACE_DIM = "#F4F9F7"
    MD3_SURFACE_CONTAINER = "#EFF6F3"
    MD3_SURFACE_CONTAINER_HIGH = "#E6F0EC"
    MD3_ON_SURFACE = TEXT
    MD3_ON_SURFACE_VARIANT = TEXT_SUB
    MD3_OUTLINE = "#A8BDB6"
    MD3_OUTLINE_VARIANT = LINE_SOFT
    MD3_PRIMARY = PRIMARY
    MD3_ON_PRIMARY = "#052E16"
    MD3_PRIMARY_CONTAINER = "#DFF6E3"
    MD3_ON_PRIMARY_CONTAINER = "#052E16"
    MD3_ERROR = ERR
    MD3_ERROR_CONTAINER = ERR_SOFT
    MD3_SWITCH_TRACK_OFF = "#DCE8E4"
    MD3_SWITCH_THUMB = "#FFFFFF"


class DARK(_Base):
    """黑 · 金。"""
    BG = "#1A1B1F"
    BG_TOP = "#1A1B1F"
    BG_BOTTOM = "#141519"
    BG_HOVER = "#24262C"
    LINE = "#34363C"
    LINE_SOFT = "#232529"

    TEXT = "#ECE7DA"
    TEXT_SUB = "#B8B2A2"
    TEXT_MUTED = "#8E8A7B"
    TEXT_FAINT = "#6B675C"

    PRIMARY = "#E3C15E"
    PRIMARY_TEXT = "#E3C15E"
    PRIMARY_HI = "#F0D07A"
    PRIMARY_SOFT = "#3B3420"
    SEL_TEXT = "#1C1A12"
    SEL_SUB = "#574E2A"

    EN = "#D9BC66"
    OK = "#8FCB72"
    OK_SOFT = "#26301F"
    WARN = "#E0A34B"
    WARN_SOFT = "#3A2F1C"
    ERR = "#E06C5C"
    ERR_SOFT = "#3A211F"
    AI = "#A8D5BA"
    AI_HI = "#BCE2CB"
    AI_SOFT = "#25332B"

    SHADOW = (0, 0, 0, 110)
    OPACITY = 0.99

    # ---- MD3 tonal 令牌（映射上方现有色值）----
    MD3_SURFACE = BG
    MD3_SURFACE_DIM = "#141519"
    MD3_SURFACE_CONTAINER = "#232529"
    MD3_SURFACE_CONTAINER_HIGH = "#2A2C32"
    MD3_ON_SURFACE = TEXT
    MD3_ON_SURFACE_VARIANT = TEXT_SUB
    MD3_OUTLINE = "#5A5D66"
    MD3_OUTLINE_VARIANT = LINE_SOFT
    MD3_PRIMARY = PRIMARY
    MD3_ON_PRIMARY = "#1A1B1F"
    MD3_PRIMARY_CONTAINER = "#3B3420"
    MD3_ON_PRIMARY_CONTAINER = "#F2E3B3"
    MD3_ERROR = ERR
    MD3_ERROR_CONTAINER = ERR_SOFT
    MD3_SWITCH_TRACK_OFF = "#3A3D45"
    MD3_SWITCH_THUMB = "#FFFFFF"


def palette(theme: str = "light"):
    """按主题名取配色类。"""
    return DARK if str(theme).lower().startswith("dark") else LIGHT


# 候选条默认使用浅色（CAD / Office 生态的主流选择）
DS = LIGHT


def set_theme(theme: str):
    """全局切换主题（模块级，便于 UI 层统一引用）。"""
    global DS
    DS = palette(theme)
    return DS
