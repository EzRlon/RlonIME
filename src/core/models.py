"""
智语输入法 — 核心数据模型
术语条目 / 候选结果 / 场景上下文
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Term:
    """一条专业术语条目。

    词库 JSON 中每个对象映射为一个 Term。
    """

    zh: str                              # 中文术语，如 "沉孔"
    en: str                              # 英文术语，如 "Counterbore"
    pinyin: str = ""                     # 全拼，如 "chenkong"（构建期自动生成）
    abbr: str = ""                       # 首字母简拼，如 "ck"
    brief: str = ""                      # 简述
    scene: str = ""                      # 使用场景描述，如 "工程图孔标注"
    software: list[str] = field(default_factory=list)   # 适用软件 key
    scene_tags: list[str] = field(default_factory=list) # 场景标签，用于加权
    standard: str = ""                   # 标准表述 / 国际标准编号
    var_en: list[str] = field(default_factory=list)     # 英文别名/其他译法
    freq: int = 100                      # 基础词频（0-1000）
    profession: str = ""                 # 所属领域 key（加载时注入）
    custom: bool = False                 # 是否用户自定义

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Term":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Candidate:
    """检索结果候选项，携带完整打分明细便于调试与展示。"""

    term: Term
    score: float = 0.0
    match_type: str = ""      # full_pinyin / abbr / prefix / en
    detail: dict[str, float] = field(default_factory=dict)
    # v3.0.3 候选分区：pro = 专业/智能词库，common = 通用输入法词库。
    # 候选条据此把两组视觉分开，用户一眼能看出"哪几个是工程词、
    # 哪几个是日常字"，而不是被混排挤在一起。
    group: str = ""

    @property
    def zh(self) -> str:
        return self.term.zh

    @property
    def en(self) -> str:
        return self.term.en

    def why(self) -> str:
        """人类可读的推荐理由，用于 UI 提示。只保留最高信号的两项。"""
        parts = []
        if self.detail.get("custom", 0) > 0 or self.term.custom:
            parts.append("自定义")
        if self.detail.get("history", 0) > 0:
            parts.append("常用")
        if self.detail.get("scene", 0) > 0:
            parts.append("场景")
        if self.detail.get("profession", 0) > 0:
            parts.append("领域")
        if not parts:
            parts.append("词库")
        return "·".join(parts[:2])


@dataclass
class SceneContext:
    """当前场景上下文。"""

    process_name: str = ""
    window_title: str = ""
    software_key: str = ""
    software_name: str = ""
    profession_key: str = ""
    scene_tags: list[str] = field(default_factory=list)
    detected_by: str = "none"    # process / title / manual / default
    confidence: float = 0.0

    # ---- 三级细分：行业 / 职业 / 职位 ----
    industry_key: str = ""       # 同 profession_key，用于语义清晰
    occupation_key: str = ""
    position_key: str = ""
    focus_tags: list[str] = field(default_factory=list)   # 由职位+职业解析而来
    position_boost: float = 1.0                           # 职位权重系数
    hierarchy: str = ""                                   # 人类可读三级链

    def describe(self) -> str:
        if self.hierarchy:
            return self.hierarchy
        if not self.software_key:
            return "未识别软件环境 · 通用模式"
        prof_name = {
            "mechanical": "机械", "electrical": "电气",
            "architecture": "建筑", "electronics": "电子",
        }.get(self.profession_key, "通用")
        return f"{self.software_name} · {prof_name}"


# ---------- 场景标签中英对照（供 UI 展示） ----------
SCENE_LABELS: dict[str, str] = {
    # 工程
    "3d_modeling": "三维建模",
    "drawing_2d": "工程图",
    "assembly": "装配体",
    "sheet_metal": "钣金",
    "surface": "曲面",
    "cam": "加工",
    "annotation": "标注",
    "schematic": "原理图",
    "panel_layout": "盘柜布置",
    "pcb_layout": "PCB 布局",
    "bim_modeling": "BIM 建模",
    "schedule": "明细表",
    "general": "通用文本",
    # 设计
    "vector": "矢量图形",
    "print": "印刷输出",
    "ui_design": "界面设计",
    "space_plan": "空间方案",
    "material": "材料构造",
    # 职能
    "accounting": "会计核算",
    "tax": "税务",
    "budget": "预算管理",
    "report": "报表分析",
    "hr": "人力管理",
    "admin": "行政事务",
    "legal": "法务文书",
    "contract": "合同审查",
    "litigation": "争议解决",
    "ip": "知识产权",
    "compliance": "合规内控",
    "document": "文档处理",
    "communication": "沟通协作",
    # 医疗
    "diagnosis": "诊断",
    "treatment": "治疗",
    "nursing": "护理",
    "surgery": "手术",
    "medication": "用药",
    "examination": "检查检验",
    "prevention": "预防保健",
    # 商务
    "shipping": "船务物流",
    "trade": "外贸业务",
    "purchase": "采购供应",
    "management": "经营管理",
    "sales": "销售商务",
    "marketing": "市场品牌",
    # 包装与展示道具
    "component": "结构件",
    "finish": "表面工艺",
    "view": "图纸视图",
    "display": "展示道具",
    "tolerance": "公差配合",
    "edge": "边缘工艺",
    "side": "侧面视图",
    "base": "底部视图",
}


# ---------- 图纸标注意图关键词 ----------
# 窗口标题含这些词 -> 判定为"工程图/标注"场景，加大英文术语权重
DRAWING_TITLE_HINTS: list[str] = [
    "drawing", "drawings", "工程图", "图纸", "draft", "drafting",
    "detail", "details", "详图", "sheet", "layout", "blueprint",
]

# ---------- 文档类型后缀 -> 场景标签 ----------
# 工程软件窗口标题通常带文档名+后缀，这是判定场景最可靠的信号。
# 例如 SolidWorks 装配体窗口标题: "... - [0917.SLDASM]"
DOC_EXTENSION_SCENES: dict[str, list[str]] = {
    # SolidWorks
    ".slddrw": ["drawing_2d"],
    ".sldasm": ["assembly"],
    ".sldprt": ["3d_modeling"],
    ".sldsm": ["sheet_metal"],
    # AutoCAD
    ".dwg": ["drawing_2d"],
    ".dxf": ["drawing_2d"],
    # UG / NX 与 Creo
    ".prt": ["3d_modeling"],
    ".asm": ["assembly"],
    ".drw": ["drawing_2d"],
    ".frm": ["drawing_2d"],
    # Revit
    ".rvt": ["bim_modeling"],
    ".rfa": ["bim_modeling"],
    ".rte": ["bim_modeling"],
    # EPLAN / 电气
    ".elkx": ["schematic"],
    ".elk": ["schematic"],
    ".zw1": ["schematic"],
    # Altium / 电子
    ".schdoc": ["schematic"],
    ".pcbdoc": ["pcb_layout"],
    ".prjpcb": ["pcb_layout"],
    ".schlib": ["schematic"],
    ".pcblib": ["pcb_layout"],
    # Cadence
    ".dsn": ["schematic"],
    ".brd": ["pcb_layout"],
    # SketchUp
    ".skp": ["bim_modeling"],
}


def scene_label(tag: str) -> str:
    return SCENE_LABELS.get(tag, tag)
