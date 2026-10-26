"""
智语输入法 — 三级细分体系定义

体系：行业(Industry) → 职业(Occupation) → 职位(Position)

- 行业：机械 / 电气 / 建筑 / 电子（对应四套基础词库）
- 职业：行业下的细分技术方向（如机械下的结构设计、模具、工艺）
- 职位：同一职业下按职责侧重区分（如设计员 / 主管 / 审图）

设计意图：职位影响的是**词库权重取向**，而非另一套独立词库。
例如「机械-结构设计-设计员」侧重建模术语，「机械-结构设计-审图」侧重
公差与标注术语。这样既保证词库可维护，又能精准贴合实际工作。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Position:
    """职位：在职业基础上调整词库权重取向。"""

    key: str
    name: str
    desc: str = ""
    # 该职位重点关注的场景标签（命中则加权）
    focus_tags: list[str] = field(default_factory=list)
    # 权重系数：1.0 为基准，>1 表示该场景词更需要
    scene_boost: float = 1.0


@dataclass
class Occupation:
    """职业：行业下的细分技术方向。"""

    key: str
    name: str
    desc: str = ""
    positions: list[Position] = field(default_factory=list)
    # 该职业偏重的场景标签
    focus_tags: list[str] = field(default_factory=list)


@dataclass
class Industry:
    """行业：顶层分类，对应一套基础词库。"""

    key: str              # 对应并复用 config.professions 的 key
    name: str
    desc: str = ""
    group: str = ""       # 所属大类：工程 / 设计 / 职能 / 医疗 / 商务
    occupations: list[Occupation] = field(default_factory=list)


# ============================================================
# 三级细分体系完整定义
# ============================================================

INDUSTRIES: list[Industry] = [
    # ==================== 工程类（原有四套完整词库） ====================

    # ---------------- 机械 ----------------
    Industry(
        key="mechanical", name="机械工程",
        desc="机械产品设计、制造与工艺",
        group="工程",
        occupations=[
            Occupation(
                key="structure", name="结构/产品设计",
                desc="零部件结构设计与三维建模",
                focus_tags=["3d_modeling", "assembly"],
                positions=[
                    Position("designer", "设计员", "日常建模与出图",
                             ["3d_modeling", "drawing_2d"], 1.0),
                    Position("senior_designer", "设计主管", "方案把控与评审",
                             ["assembly", "analysis"], 1.15),
                    Position("checker", "审图/校核", "图纸审核与技术把关",
                             ["drawing_2d", "annotation"], 1.3),
                    Position("director", "设计总监", "技术路线与团队管理",
                             ["assembly", "analysis", "drawing_2d"], 1.1),
                ],
            ),
            Occupation(
                key="mold", name="模具设计",
                desc="注塑模、冲压模等模具设计",
                focus_tags=["3d_modeling", "surface"],
                positions=[
                    Position("designer", "模具设计员", "分型与型腔设计",
                             ["3d_modeling", "surface"], 1.2),
                    Position("leader", "模具主管", "模具方案与试模",
                             ["assembly", "analysis"], 1.15),
                ],
            ),
            Occupation(
                key="sheetmetal", name="钣金设计",
                desc="钣金件展开与成型设计",
                focus_tags=["sheet_metal", "drawing_2d"],
                positions=[
                    Position("designer", "钣金设计员", "展开与折弯设计",
                             ["sheet_metal", "3d_modeling"], 1.25),
                    Position("process", "钣金工艺员", "下料与折弯工艺",
                             ["sheet_metal", "cam"], 1.2),
                ],
            ),
            Occupation(
                key="manufacturing", name="工艺/制造",
                desc="加工工艺规划与数控编程",
                focus_tags=["cam", "drawing_2d"],
                positions=[
                    Position("process", "工艺工程师", "工艺路线与工装",
                             ["cam", "drawing_2d"], 1.2),
                    Position("cnc", "CNC 编程员", "数控程序编制",
                             ["cam"], 1.3),
                ],
            ),
            Occupation(
                key="analysis", name="仿真分析",
                desc="结构强度与运动仿真",
                focus_tags=["analysis", "3d_modeling"],
                positions=[
                    Position("fea", "仿真工程师", "有限元分析",
                             ["analysis"], 1.35),
                ],
            ),
            Occupation(
                key="plan_design", name="方案设计",
                desc="非标设备整体方案与选型",
                focus_tags=["assembly", "3d_modeling"],
                positions=[
                    Position("planner", "方案工程师", "方案构思与可行性",
                             ["assembly", "3d_modeling"], 1.2),
                    Position("leader", "方案主管", "方案评审与报价支持",
                             ["assembly"], 1.15),
                ],
            ),
        ],
    ),

    # ---------------- 电气 ----------------
    Industry(
        key="electrical", name="电气工程",
        desc="电气系统设计、成套与调试",
        group="工程",
        occupations=[
            Occupation(
                key="schematic", name="电气设计",
                desc="电气原理图与接线设计",
                focus_tags=["schematic", "panel_layout"],
                positions=[
                    Position("designer", "电气设计员", "原理图绘制",
                             ["schematic"], 1.2),
                    Position("leader", "电气主管", "方案与选型把关",
                             ["schematic", "panel_layout"], 1.15),
                    Position("checker", "图纸审核", "电气图纸校核",
                             ["schematic"], 1.3),
                    Position("director", "电气总监", "技术标准与团队管理",
                             ["schematic", "panel_layout"], 1.1),
                ],
            ),
            Occupation(
                key="panel", name="成套/盘柜",
                desc="配电柜与控制柜成套设计",
                focus_tags=["panel_layout", "schematic"],
                positions=[
                    Position("designer", "成套设计员", "柜内布置与接线",
                             ["panel_layout"], 1.3),
                    Position("assembler", "成套装配", "柜体装配与配线",
                             ["panel_layout"], 1.25),
                ],
            ),
            Occupation(
                key="automation", name="自动化控制",
                desc="PLC 与运动控制系统",
                focus_tags=["schematic", "panel_layout"],
                positions=[
                    Position("plc", "PLC 工程师", "程序编写与调试",
                             ["schematic"], 1.25),
                    Position("motion", "运动控制工程师", "伺服与变频调试",
                             ["schematic"], 1.25),
                ],
            ),
            Occupation(
                key="power", name="供配电",
                desc="负荷计算与配电系统设计",
                focus_tags=["schematic", "panel_layout"],
                positions=[
                    Position("designer", "供配电设计员", "配电系统设计",
                             ["schematic"], 1.25),
                    Position("commissioning", "调试工程师", "现场调试验收",
                             ["panel_layout"], 1.2),
                ],
            ),
        ],
    ),

    # ---------------- 建筑 ----------------
    Industry(
        key="architecture", name="建筑与结构",
        desc="建筑设计、结构设计与 BIM",
        group="工程",
        occupations=[
            Occupation(
                key="arch_design", name="建筑设计",
                desc="方案与施工图建筑设计",
                focus_tags=["bim_modeling", "drawing_2d"],
                positions=[
                    Position("architect", "建筑设计师", "方案与施工图",
                             ["bim_modeling", "drawing_2d"], 1.15),
                    Position("drafter", "建筑绘图员", "施工图绘制",
                             ["drawing_2d"], 1.25),
                    Position("chief", "专业负责人", "方案把控与协调",
                             ["bim_modeling"], 1.1),
                ],
            ),
            Occupation(
                key="plan_design", name="方案设计",
                desc="概念方案与总图规划",
                focus_tags=["bim_modeling", "drawing_2d"],
                positions=[
                    Position("planner", "方案设计师", "概念方案与效果表达",
                             ["bim_modeling"], 1.15),
                    Position("chief", "方案主创", "方案创意与汇报",
                             ["bim_modeling", "drawing_2d"], 1.1),
                ],
            ),
            Occupation(
                key="structure", name="结构设计",
                desc="混凝土与钢结构设计",
                focus_tags=["bim_modeling", "analysis"],
                positions=[
                    Position("designer", "结构设计员", "配筋与构件设计",
                             ["bim_modeling", "drawing_2d"], 1.2),
                    Position("checker", "结构审核", "计算书与图纸校核",
                             ["analysis", "drawing_2d"], 1.3),
                ],
            ),
            Occupation(
                key="bim", name="BIM 建模",
                desc="BIM 模型搭建与协同",
                focus_tags=["bim_modeling", "schedule"],
                positions=[
                    Position("modeler", "BIM 建模员", "模型搭建与出图",
                             ["bim_modeling"], 1.3),
                    Position("coordinator", "BIM 协调员", "管线综合与碰撞检查",
                             ["bim_modeling", "analysis"], 1.25),
                ],
            ),
            Occupation(
                key="mep", name="机电/暖通",
                desc="建筑机电与暖通设计",
                focus_tags=["bim_modeling", "drawing_2d"],
                positions=[
                    Position("designer", "机电设计师", "机电管线设计",
                             ["bim_modeling", "drawing_2d"], 1.2),
                ],
            ),
        ],
    ),

    # ---------------- 电子 ----------------
    Industry(
        key="electronics", name="电子工程",
        desc="电路设计、PCB 设计与生产",
        group="工程",
        occupations=[
            Occupation(
                key="hardware", name="硬件设计",
                desc="原理图与电路设计",
                focus_tags=["schematic"],
                positions=[
                    Position("designer", "硬件设计员", "原理图设计",
                             ["schematic"], 1.25),
                    Position("leader", "硬件主管", "方案与器件选型",
                             ["schematic"], 1.15),
                ],
            ),
            Occupation(
                key="pcb", name="PCB 设计",
                desc="PCB 布局布线设计",
                focus_tags=["pcb_layout"],
                positions=[
                    Position("layout", "Layout 工程师", "布局与布线",
                             ["pcb_layout"], 1.35),
                    Position("si", "SI/PI 工程师", "信号与电源完整性",
                             ["pcb_layout", "analysis"], 1.3),
                ],
            ),
            Occupation(
                key="test", name="测试与认证",
                desc="硬件测试与 EMC 认证",
                focus_tags=["analysis", "pcb_layout"],
                positions=[
                    Position("test", "测试工程师", "功能与可靠性测试",
                             ["analysis"], 1.25),
                    Position("emc", "EMC 工程师", "电磁兼容整改",
                             ["analysis", "pcb_layout"], 1.35),
                ],
            ),
            Occupation(
                key="production", name="生产工艺",
                desc="SMT 与生产制造",
                focus_tags=["pcb_layout"],
                positions=[
                    Position("smt", "SMT 工程师", "贴片工艺与良率",
                             ["pcb_layout"], 1.25),
                ],
            ),
        ],
    ),

    # ==================== 设计类 ====================

    # ---------------- 平面 / 视觉设计 ----------------
    Industry(
        key="graphic", name="平面与视觉设计",
        desc="品牌视觉、平面物料与 UI 设计",
        group="设计",
        occupations=[
            Occupation(
                key="brand", name="品牌视觉",
                desc="LOGO、VI 与品牌物料设计",
                focus_tags=["vector", "print"],
                positions=[
                    Position("designer", "平面设计师", "海报 / 画册 / 物料",
                             ["vector", "print"], 1.2),
                    Position("senior", "资深设计师", "全案视觉与创意",
                             ["vector", "print"], 1.15),
                    Position("director", "设计总监", "品牌策略与视觉把关",
                             ["vector", "print"], 1.1),
                ],
            ),
            Occupation(
                key="ui", name="UI/UX 设计",
                desc="界面、交互与设计系统",
                focus_tags=["ui_design", "vector"],
                positions=[
                    Position("ui", "UI 设计师", "界面视觉与切图交付",
                             ["ui_design", "vector"], 1.25),
                    Position("ux", "交互设计师", "交互流程与原型",
                             ["ui_design"], 1.2),
                ],
            ),
            Occupation(
                key="ecommerce", name="电商设计",
                desc="详情页、主图与活动视觉",
                focus_tags=["vector", "print"],
                positions=[
                    Position("designer", "电商设计师", "主图 / 详情页",
                             ["vector", "print"], 1.2),
                ],
            ),
            Occupation(
                key="illustration", name="插画/动效",
                desc="插画绘制与动效包装",
                focus_tags=["vector"],
                positions=[
                    Position("illustrator", "插画师", "商业插画与角色",
                             ["vector"], 1.25),
                    Position("motion", "动效设计师", "动画与转场包装",
                             ["vector"], 1.2),
                ],
            ),
        ],
    ),

    # ---------------- 家装 / 室内设计 ----------------
    Industry(
        key="interior", name="室内与家装设计",
        desc="住宅、商业空间与软装设计",
        group="设计",
        occupations=[
            Occupation(
                key="interior", name="室内设计",
                desc="空间方案与施工图",
                focus_tags=["space_plan", "drawing_2d"],
                positions=[
                    Position("designer", "室内设计师", "空间方案与效果图",
                             ["space_plan"], 1.2),
                    Position("drafter", "深化设计员", "施工图深化",
                             ["drawing_2d"], 1.25),
                ],
            ),
            Occupation(
                key="home", name="家装方案",
                desc="全屋定制与家装方案",
                focus_tags=["space_plan", "material"],
                positions=[
                    Position("planner", "方案设计师", "户型优化与方案编排",
                             ["space_plan"], 1.2),
                    Position("sales", "全屋定制设计", "定制柜体方案",
                             ["space_plan", "material"], 1.2),
                ],
            ),
            Occupation(
                key="soft", name="软装陈设",
                desc="家具、灯具与软装搭配",
                focus_tags=["material", "space_plan"],
                positions=[
                    Position("designer", "软装设计师", "陈设方案与选品",
                             ["material"], 1.25),
                ],
            ),
            Occupation(
                key="landscape", name="景观设计",
                desc="园林景观与室外空间",
                focus_tags=["space_plan", "drawing_2d"],
                positions=[
                    Position("designer", "景观设计师", "方案与种植设计",
                             ["space_plan"], 1.2),
                ],
            ),
        ],
    ),

    # ---------------- 工程制图 / 深化设计 ----------------
    Industry(
        key="drafting", name="工程制图与深化",
        desc="施工图、深化图与竣工资料",
        group="设计",
        occupations=[
            Occupation(
                key="detail", name="深化设计",
                desc="图纸深化与节点设计",
                focus_tags=["drawing_2d", "annotation"],
                positions=[
                    Position("designer", "深化设计师", "节点深化与出图",
                             ["drawing_2d", "annotation"], 1.25),
                    Position("drafter", "制图员", "平立剖与详图绘制",
                             ["drawing_2d"], 1.3),
                ],
            ),
            Occupation(
                key="coordination", name="图纸协调",
                desc="多专业图纸核对与综合",
                focus_tags=["drawing_2d", "bim_modeling"],
                positions=[
                    Position("coordinator", "图纸协调员", "专业间核对与合图",
                             ["drawing_2d"], 1.25),
                    Position("checker", "图纸审核", "出图前审查",
                             ["drawing_2d", "annotation"], 1.3),
                ],
            ),
            Occupation(
                key="asbuilt", name="竣工与资料",
                desc="竣工图与工程资料整理",
                focus_tags=["drawing_2d", "schedule"],
                positions=[
                    Position("compiler", "资料员", "竣工图与档案整理",
                             ["drawing_2d", "schedule"], 1.2),
                ],
            ),
        ],
    ),

    # ==================== 职能类 ====================

    # ---------------- 财务 / 会计 ----------------
    Industry(
        key="finance", name="财务与会计",
        desc="会计核算、税务与资金管理",
        group="职能",
        occupations=[
            Occupation(
                key="accounting", name="会计核算",
                desc="记账、结账与报表编制",
                focus_tags=["accounting", "report"],
                positions=[
                    Position("accountant", "会计", "日常核算与月结",
                             ["accounting"], 1.2),
                    Position("chief", "会计主管", "核算复核与结账",
                             ["accounting", "report"], 1.15),
                ],
            ),
            Occupation(
                key="cost", name="成本管理",
                desc="成本核算与分析",
                focus_tags=["accounting", "report"],
                positions=[
                    Position("cost", "成本会计", "成本归集与差异分析",
                             ["accounting"], 1.3),
                ],
            ),
            Occupation(
                key="tax", name="税务",
                desc="税务申报与筹划",
                focus_tags=["tax", "accounting"],
                positions=[
                    Position("tax", "税务会计", "申报与发票管理",
                             ["tax"], 1.3),
                    Position("manager", "税务经理", "税务筹划与风险",
                             ["tax"], 1.2),
                ],
            ),
            Occupation(
                key="treasury", name="资金/出纳",
                desc="资金收付与银行结算",
                focus_tags=["accounting"],
                positions=[
                    Position("cashier", "出纳", "收付款与银行对账",
                             ["accounting"], 1.25),
                    Position("treasury", "资金主管", "资金计划与融资",
                             ["accounting", "report"], 1.15),
                ],
            ),
            Occupation(
                key="fpa", name="财务分析",
                desc="经营分析与预算管理",
                focus_tags=["report", "budget"],
                positions=[
                    Position("analyst", "财务分析师", "经营分析与预测",
                             ["report", "budget"], 1.25),
                    Position("budget", "预算专员", "预算编制与管控",
                             ["budget"], 1.25),
                ],
            ),
            Occupation(
                key="fina_admin", name="财务管理",
                desc="财务负责人与统筹",
                focus_tags=["report", "budget", "tax"],
                positions=[
                    Position("manager", "财务经理", "财务统筹与内控",
                             ["report", "budget"], 1.15),
                    Position("cfo", "财务总监", "财务战略与资本运作",
                             ["report", "budget", "tax"], 1.1),
                    Position("boss", "老板/总经理", "经营决策与看报表",
                             ["report", "budget"], 1.0),
                ],
            ),
        ],
    ),

    # ---------------- 人力 / 行政 ----------------
    Industry(
        key="hr", name="人力资源与行政",
        desc="招聘、薪酬、绩效与行政事务",
        group="职能",
        occupations=[
            Occupation(
                key="recruit", name="招聘",
                desc="人才寻访与面试组织",
                focus_tags=["hr", "communication"],
                positions=[
                    Position("recruiter", "招聘专员", "渠道与面试安排",
                             ["hr"], 1.25),
                    Position("manager", "招聘主管", "招聘计划与用人评估",
                             ["hr"], 1.15),
                ],
            ),
            Occupation(
                key="comp", name="薪酬绩效",
                desc="薪酬核算与绩效管理",
                focus_tags=["hr", "report"],
                positions=[
                    Position("cbm", "薪酬绩效专员", "薪酬核算与考核",
                             ["hr", "report"], 1.25),
                    Position("manager", "人力资源经理", "体系搭建与统筹",
                             ["hr"], 1.15),
                ],
            ),
            Occupation(
                key="employee", name="员工关系",
                desc="入离职、社保与劳动关系",
                focus_tags=["hr", "law"],
                positions=[
                    Position("er", "员工关系专员", "社保与劳动关系",
                             ["hr", "law"], 1.2),
                    Position("admin", "人事专员", "入离职与档案",
                             ["hr"], 1.25),
                ],
            ),
            Occupation(
                key="admin", name="行政/总务",
                desc="办公行政与后勤保障",
                focus_tags=["admin"],
                positions=[
                    Position("admin", "行政专员", "办公事务与采购",
                             ["admin"], 1.25),
                    Position("manager", "行政主管", "行政统筹与制度",
                             ["admin"], 1.15),
                ],
            ),
            Occupation(
                key="hrd", name="人力统筹",
                desc="人力资源负责人",
                focus_tags=["hr", "report"],
                positions=[
                    Position("hrd", "人力资源总监", "组织与人才战略",
                             ["hr"], 1.1),
                    Position("boss", "老板/总经理", "用人决策",
                             ["hr"], 1.0),
                ],
            ),
        ],
    ),

    # ---------------- 法务 / 合规 ----------------
    Industry(
        key="legal", name="法务与合规",
        desc="合同审查、诉讼与知识产权",
        group="职能",
        occupations=[
            Occupation(
                key="contract", name="合同管理",
                desc="合同起草、审查与履约",
                focus_tags=["legal", "contract"],
                positions=[
                    Position("legal", "法务专员", "合同审查与用印",
                             ["legal", "contract"], 1.25),
                    Position("manager", "法务经理", "合同体系与风险",
                             ["legal", "contract"], 1.15),
                ],
            ),
            Occupation(
                key="litigation", name="诉讼仲裁",
                desc="争议解决与案件办理",
                focus_tags=["legal", "litigation"],
                positions=[
                    Position("lawyer", "律师", "代理诉讼与出庭",
                             ["legal", "litigation"], 1.3),
                    Position("counsel", "法务顾问", "案件评估与应对",
                             ["legal", "litigation"], 1.2),
                ],
            ),
            Occupation(
                key="ip", name="知识产权",
                desc="商标、专利与著作权",
                focus_tags=["legal", "ip"],
                positions=[
                    Position("ip", "知识产权专员", "商标专利申报与维护",
                             ["ip"], 1.3),
                ],
            ),
            Occupation(
                key="compliance", name="合规内控",
                desc="合规审查与内控体系",
                focus_tags=["legal", "compliance"],
                positions=[
                    Position("compliance", "合规专员", "制度审查与培训",
                             ["compliance"], 1.25),
                    Position("director", "法务总监", "法务战略与重大谈判",
                             ["legal", "contract"], 1.1),
                ],
            ),
        ],
    ),

    # ==================== 医疗类 ====================

    # ---------------- 临床医疗 ----------------
    Industry(
        key="medical", name="临床医疗",
        desc="门诊、病房与手术诊疗",
        group="医疗",
        occupations=[
            Occupation(
                key="physician", name="临床医师",
                desc="接诊、诊断与治疗",
                focus_tags=["diagnosis", "treatment"],
                positions=[
                    Position("doctor", "医生", "门诊接诊与病历书写",
                             ["diagnosis", "treatment"], 1.25),
                    Position("resident", "住院医师", "病房管理与医嘱",
                             ["treatment", "nursing"], 1.2),
                    Position("attending", "主治/主任医师", "疑难病例与手术",
                             ["diagnosis", "surgery"], 1.15),
                ],
            ),
            Occupation(
                key="surgery", name="外科",
                desc="手术与围术期管理",
                focus_tags=["surgery", "treatment"],
                positions=[
                    Position("surgeon", "外科医生", "手术方案与操作",
                             ["surgery"], 1.3),
                    Position("anesthesia", "麻醉医师", "麻醉评估与实施",
                             ["surgery"], 1.25),
                ],
            ),
            Occupation(
                key="imaging", name="影像/检验",
                desc="医学影像与实验室检查",
                focus_tags=["examination", "diagnosis"],
                positions=[
                    Position("radiology", "影像科医师", "阅片与报告",
                             ["examination", "diagnosis"], 1.25),
                    Position("lab", "检验技师", "标本检测与结果",
                             ["examination"], 1.25),
                ],
            ),
            Occupation(
                key="pharmacy", name="药学",
                desc="处方审核与临床用药",
                focus_tags=["medication", "treatment"],
                positions=[
                    Position("pharmacist", "药师", "处方审核与调配",
                             ["medication"], 1.25),
                    Position("clinical", "临床药师", "用药方案与监护",
                             ["medication", "treatment"], 1.2),
                ],
            ),
            Occupation(
                key="preventive", name="公共卫生",
                desc="预防保健与健康管理",
                focus_tags=["prevention", "diagnosis"],
                positions=[
                    Position("doctor", "公卫医师", "流行病学与防控",
                             ["prevention"], 1.2),
                ],
            ),
        ],
    ),

    # ---------------- 护理 ----------------
    Industry(
        key="nursing", name="护理",
        desc="临床护理与患者照护",
        group="医疗",
        occupations=[
            Occupation(
                key="ward", name="病房护理",
                desc="住院患者日常照护",
                focus_tags=["nursing", "treatment"],
                positions=[
                    Position("nurse", "护士", "执行医嘱与基础护理",
                             ["nursing"], 1.3),
                    Position("head_nurse", "护士长", "排班与护理质量",
                             ["nursing"], 1.15),
                ],
            ),
            Occupation(
                key="or", name="手术室/ICU",
                desc="手术配合与重症监护",
                focus_tags=["nursing", "surgery"],
                positions=[
                    Position("or_nurse", "手术室护士", "器械配合与巡回",
                             ["surgery", "nursing"], 1.3),
                    Position("icu", "ICU 护士", "生命体征与重症监护",
                             ["nursing", "treatment"], 1.3),
                ],
            ),
            Occupation(
                key="outpatient", name="门诊/社区",
                desc="门诊分诊与社区护理",
                focus_tags=["nursing", "prevention"],
                positions=[
                    Position("nurse", "门诊护士", "分诊与注射输液",
                             ["nursing"], 1.25),
                ],
            ),
        ],
    ),

    # ==================== 商务 / 贸易类 ====================

    # ---------------- 船务 / 外贸 ----------------
    Industry(
        key="shipping", name="船务与外贸",
        desc="国际物流、报关与外贸业务",
        group="商务",
        occupations=[
            Occupation(
                key="shipping", name="船务单证",
                desc="订舱、报关与提单处理",
                focus_tags=["shipping", "document"],
                positions=[
                    Position("shipping", "船务专员", "订舱与出运跟进",
                             ["shipping", "document"], 1.3),
                    Position("docs", "单证员", "报关与单据制作",
                             ["document", "shipping"], 1.3),
                    Position("manager", "船务主管", "物流方案与成本",
                             ["shipping"], 1.15),
                ],
            ),
            Occupation(
                key="trade", name="外贸业务",
                desc="客户开发、报价与跟单",
                focus_tags=["trade", "document"],
                positions=[
                    Position("sales", "外贸业务员", "询盘报价与跟单",
                             ["trade", "document"], 1.25),
                    Position("merchandiser", "跟单员", "订单跟进与协调",
                             ["trade", "document"], 1.25),
                    Position("manager", "外贸经理", "客户与业绩管理",
                             ["trade"], 1.15),
                ],
            ),
            Occupation(
                key="customs", name="关务",
                desc="进出口报关与合规",
                focus_tags=["shipping", "document", "law"],
                positions=[
                    Position("customs", "报关员", "报关报检与查验",
                             ["shipping", "document"], 1.3),
                ],
            ),
            Occupation(
                key="purchase", name="采购/供应链",
                desc="询价、下单与供应商管理",
                focus_tags=["purchase", "document"],
                positions=[
                    Position("buyer", "采购员", "询价比价与下单",
                             ["purchase"], 1.25),
                    Position("sqe", "供应商管理", "供应商评估与稽核",
                             ["purchase"], 1.2),
                ],
            ),
        ],
    ),

    # ---------------- 经营 / 管理 ----------------
    Industry(
        key="management", name="经营与管理",
        desc="企业经营管理与综合决策",
        group="商务",
        occupations=[
            Occupation(
                key="gm", name="总经理/老板",
                desc="经营决策与资源统筹",
                focus_tags=["management", "report"],
                positions=[
                    Position("boss", "老板/总经理", "经营决策与看报表",
                             ["management", "report"], 1.15),
                    Position("vp", "副总/合伙人", "分管业务与执行",
                             ["management"], 1.1),
                ],
            ),
            Occupation(
                key="sales", name="销售",
                desc="客户开发与商务谈判",
                focus_tags=["sales", "management"],
                positions=[
                    Position("rep", "销售代表", "客户拜访与成交",
                             ["sales"], 1.25),
                    Position("manager", "销售经理", "团队与业绩管理",
                             ["sales", "management"], 1.15),
                ],
            ),
            Occupation(
                key="pm", name="项目管理",
                desc="项目计划、进度与交付",
                focus_tags=["management", "schedule"],
                positions=[
                    Position("pm", "项目经理", "进度与资源协调",
                             ["management", "schedule"], 1.2),
                    Position("pmo", "PMO", "流程与项目治理",
                             ["management"], 1.15),
                ],
            ),
            Occupation(
                key="marketing", name="市场/品牌",
                desc="市场推广与品牌运营",
                focus_tags=["marketing", "management"],
                positions=[
                    Position("marketing", "市场专员", "推广活动与渠道",
                             ["marketing"], 1.25),
                    Position("brand", "品牌经理", "品牌策略与传播",
                             ["marketing"], 1.2),
                ],
            ),
            Occupation(
                key="ops", name="运营",
                desc="业务运营与数据分析",
                focus_tags=["management", "report"],
                positions=[
                    Position("ops", "运营专员", "日常运营与数据跟进",
                             ["management", "report"], 1.25),
                    Position("director", "运营总监", "运营策略与增长",
                             ["management"], 1.15),
                ],
            ),
        ],
    ),
    # ---------------- 包装与展示道具 ----------------
    Industry(
        key="packaging", name="包装与展示道具",
        desc="奢侈品包装盒、展示道具的外协加工与打样",
        group="设计",
        occupations=[
            Occupation(
                key="pkg_design", name="包装结构设计",
                desc="盒型结构与内衬设计",
                focus_tags=["component", "material"],
                positions=[
                    Position("designer", "包装设计师", "盒型与内托设计",
                             ["component", "material"], 1.25),
                    Position("structure", "结构工程师", "结构强度与装配",
                             ["component", "assembly"], 1.2),
                ],
            ),
            Occupation(
                key="pkg_graphic", name="包装视觉设计",
                desc="面纸图文与工艺效果设计",
                focus_tags=["print", "finish"],
                positions=[
                    Position("graphic", "包装平面设计师", "面纸图文与印刷稿",
                             ["print", "finish"], 1.25),
                    Position("finish", "工艺设计师", "表面工艺效果设定",
                             ["finish"], 1.25),
                ],
            ),
            Occupation(
                key="pkg_display", name="展示道具设计",
                desc="陈列座、展示架设计",
                focus_tags=["display", "component"],
                positions=[
                    Position("designer", "道具设计师", "展示结构与陈列方案",
                             ["display", "component"], 1.25),
                ],
            ),
            Occupation(
                key="pkg_engineer", name="包装工程与工艺",
                desc="打样、工艺与品质跟进",
                focus_tags=["assembly", "compliance"],
                positions=[
                    Position("engineer", "包装工艺工程师", "工艺路线与打样",
                             ["assembly", "finish"], 1.25),
                    Position("qc", "品质工程师", "检验标准与验收",
                             ["compliance", "examination"], 1.3),
                    Position("pm", "包材项目经理", "交期与供应商协调",
                             ["schedule", "compliance"], 1.2),
                ],
            ),
            Occupation(
                key="pkg_director", name="设计管理",
                desc="设计统筹与客户确认",
                focus_tags=["component", "finish", "print"],
                positions=[
                    Position("director", "设计总监", "方案把控与工艺定板",
                             ["component", "finish"], 1.15),
                    Position("boss", "老板/负责人", "项目决策与成本把控",
                             ["component", "compliance"], 1.0),
                ],
            ),
        ],
    ),
]


# ---------------- 查询辅助 ----------------
def get_industry(key: str) -> Industry | None:
    return next((i for i in INDUSTRIES if i.key == key), None)


def get_occupation(industry_key: str, occ_key: str) -> Occupation | None:
    ind = get_industry(industry_key)
    if not ind:
        return None
    return next((o for o in ind.occupations if o.key == occ_key), None)


def get_position(industry_key: str, occ_key: str, pos_key: str) -> Position | None:
    occ = get_occupation(industry_key, occ_key)
    if not occ:
        return None
    return next((p for p in occ.positions if p.key == pos_key), None)


def resolve_focus_tags(industry_key: str, occ_key: str, pos_key: str) -> tuple[list[str], float]:
    """
    解析当前三级选择对应的场景标签与权重系数。

    返回 (focus_tags, scene_boost)。
    职位提供最具体的标签；职业提供补充；两者合并去重。
    """
    tags: list[str] = []
    boost = 1.0

    occ = get_occupation(industry_key, occ_key)
    if occ:
        tags.extend(occ.focus_tags)

    pos = get_position(industry_key, occ_key, pos_key)
    if pos:
        # 职位标签优先（放前面）
        tags = list(pos.focus_tags) + [t for t in tags if t not in pos.focus_tags]
        boost = pos.scene_boost

    # 去重保序
    seen: set[str] = set()
    uniq = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq, boost


def describe_chain(industry_key: str, occ_key: str, pos_key: str) -> str:
    """生成人类可读的三级链描述，如 '机械工程 / 结构设计 / 审图校核'。"""
    parts = []
    ind = get_industry(industry_key)
    if ind:
        parts.append(ind.name)
    occ = get_occupation(industry_key, occ_key)
    if occ:
        parts.append(occ.name)
    pos = get_position(industry_key, occ_key, pos_key)
    if pos:
        parts.append(pos.name)
    return " / ".join(parts) if parts else "未设置"
