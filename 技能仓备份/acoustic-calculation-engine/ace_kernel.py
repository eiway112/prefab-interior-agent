# -*- coding: utf-8 -*-
"""ACE 程序内核 — 装配式装修通用隔声计算引擎的可执行面。

版本：1.0.0（2026-10-17，CG-20261017-007 立项并实施；发起人裁定逐字＝「按甲走，需具有前瞻性视角，不仅是当下」）
      CG-20261017-008 起按 PL-229 暂停工程定量出口（版本面单源＝SKILL.md frontmatter）
依赖：Python 3.8+（仅标准库）
判据陈述面：本目录 reference.md。**事实源口径（2026-10-17 独立复核后订正）**＝凡承载
            dB／h／mm 性能量、材料参数或判定阈值的常数与表档，一律以该件为唯一事实源，由
            `维护脚本/ace_regression_test.py` 的 TestDocAnchor 组在测试运行时回读对撞；纯计算网格类
            参数（1/1 倍频中心频、吻合邻接带宽比、敏感性步长）不属在册数值，逐枚在行内注明系本件
            自设，不得顶替在册口径、不得挂文档锚。
接口面：shared/interface-contracts.md 的 IC-03／IC-08／IC-09

【本内核解决的不是「缺一个 .py」】
    M1—M7 的可执行形态此前住在开发仓 `维护脚本/ace_regression_test.py:127—220`，
    而运行时技能目录不加载 `维护脚本/`——实现存在，但不在生效路径上。本内核把它迁入
    运行时技能目录（生效路径的**文件层**），并让测试侧改为 import 本件，实现自此只有一份。

【工程输出边界（CG-20261017-008，PL-229 丙修正版）】
    判据单源＝本目录 reference.md「现行工程输出边界（PL-229）」。`compute_request` 对合法
    IC-03／IC-08／IC-09 请求一律返回 PL-229 降级 payload（降级＝超出模型适用范围、原因代码＝PL-229），
    只带输入校验、风险识别与物理诊断（质量、有效拓扑下的 MSM f₀／耦合模态、单板 fc、浮筑 f₀），
    **不带** 计算结果／频段／精度承诺。经入口可达的模型面因此只剩 M2（fc）／M3（f₀）／M5 的 f₀ 式；
    M1 基线、M4 增益、M5 ΔLw 与裸板基准、M6、M7 仍作为纯函数供研究复算，不经契约组装成工程 dB。
    恢复某契约的定量出口＝把该 ic_id 登记进 VALIDATED_QUANT_ROUTES，且须同批具备匹配可溯源实测
    或经独立验证（含误差／区间覆盖评估）的模型；未登记即出现 计算结果 由红线段判红。
    数据段中 dB 常数保留为 reference.md 历史研究口径的锚定副本（TestKernelDataDocAnchor），不是输出许可。

【分层与不变式】
    数据段（§一）→ 模型段（§二）→ 接口段（§三）→ 红线段（§四）
    不变式 1：数据段是唯一的数值字面来源，模型段不得内联材料参数或表档数值。
    不变式 2：接口段的字段名以 IC_FIELD_SPECS 声明，须与契约件字段约束表逐字相等
              （由 TestInterfaceFieldConservation 三方对撞：内核 ↔ 字段约束表 ↔ JSON Schema）。
    不变式 3：红线段的断言不可被调用方参数关闭。

【行为层挂载点（本批不实装）】
    台账 PL-031（技能间调用四类行为测试的宿主实跑）此前无可跑宿主；本内核给出
    确定性可跑面，PL-031 的行为测试应挂在 `compute_request()` 上。本批只留此指针。

【非目标】
    不含标准限值、不含「是否满足」判定分支、不含建筑类型→限值映射（ACE-R-P1-3）。
    不做 ISO 12354 逐频带合成（M6 缝隙式按 reference.md「单值代入口径（近似声明）」使用）。
    不自造宿舍档数值；取档口径归 PL-200 行 ②（GB 55025-2022 管归属、GB 50118-2010 管旅馆档出处）。
"""

import math

# 版本面单源＝本技能 SKILL.md frontmatter 的 version（内核内不再持有第二枚版本号字面，
# 否则两处会漂移；PL-054 的实现在册身份由 SKILL.md §三 入口段承载）。

# ============================================================
# 〇、物理常数（判据＝reference.md M2 参数表／M3 常数说明）
# ============================================================

SOUND_SPEED_M_S = 343.0      # 空气中声速，20℃ 标准条件（reference.md M2）
AIR_DENSITY_KG_M3 = 1.2      # 空气密度（reference.md M3 常数导出式）

# ============================================================
# 一、数据段（唯一数值字面来源；每档带来源与 S 级信息性标注）
# ============================================================

# --- M1 质量定律 ---
M1_SPECTRAL_CONST_DB = -47.2     # R(f)=20·lg(m·f)+const（reference.md M1 公式）
M1_SIMPLE_K = 0.0                # 仓内统一口径，2026-08-07 CG-20260807-011 仲裁；S2 级统计校准，非标准公式
M1_LIGHTWEIGHT_MASS_LIMIT = 30.0  # 轻质构造区阈值（面密度 <30 kg/㎡，reference.md M1 特别声明）
M1_LIGHTWEIGHT_EMPIRICAL_REF_DB = (38.0, 42.0)   # 双叶轻质构造的经验参考范围（reference.md :50 逐字「该构造按 M3 基准+增益法估算约 38-42 dB」）
M1_LIGHTWEIGHT_EMPIRICAL_PROVENANCE = ("数据源等级＝S3 级、验证状态＝待检测报告验证（该条自载）；同条另转述隔墙技能 §9.2 的 40-45 dB 交叉读，"
                                        "两者与本轮 M3 复算互不接＝台账 PL-229，本件不代裁、只并列披露")
# 在册承诺面为「缺失时 ACE 使用典型值并标注假设」的可选参数：本模型未纳入计算者，给了即须披露
UNCONSUMED_OPTION_FIELDS = ("龙骨间距_mm", "吊杆间距_mm", "频率范围_Hz")
M1_VALID_FREQ_HZ = (100.0, 5000.0)   # 极低频/极高频不适用（reference.md M1「不适用」条）
M1_MIDBAND_FREQ_HZ = (200.0, 2000.0)  # 中频段估算精度最佳

# --- M2 吻合效应 ---
M2_POISSON_DOMAIN = (0.0, 0.5)
M2_RISK_BANDS = (            # fc 与评价频率带 → 风险等级（reference.md M2 判定表）
    (4000.0, float("inf"), "无"),
    (2500.0, 4000.0, "低"),
    (800.0, 2500.0, "中"),
    (float("-inf"), 800.0, "高"),
)
M2_SEPARATION_DELTA_FC_HZ = 300.0   # 异质复合有效错开判据（reference.md M2；对 E 高度敏感且非单调）
M2_FC_PRECISION = 0.10              # ±10%（reference.md M2 精度声明）
OCTAVE_CENTER_FREQS_HZ = (125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0)  # 1/1 倍频中心频（SKILL.md §六 频段分析形制）
M1_SINGLE_LEAF_LOW_BIAS_DB = (5.0, 7.0)  # 单叶密实板材绝对值可能偏低 5-7 dB（reference.md M1 特别声明）
COINCIDENCE_BAND_WIDTH_RATIO = 0.2  # 频段分析与吻合中心的邻接判据（本件自载形制参数，非文档数值）
SENSITIVITY_STEP_RATIO = 1.1        # 本件自设的敏感性步长（10%）：SKILL.md §六 现读只声明敏感性分析的**输出形制**、
                                  #   未规定步长（2026-10-17 复核命中假锚后订正），故本枚不挂文档锚、属计算网格选择

# --- M3 质量-弹簧-质量 ---
M3_CONST = 600.0      # 由 1/(2π)·√(ρ_air·c²·100) 导出（reference.md M3 常数说明）
M3_GAIN_EFFECTIVE_F0_HZ = 200.0     # f₀ ≥ 200 Hz 时增益显著削弱（reference.md M3 增益路径表／5 步法 Step 3）
M3_APPROX_TOTAL_MASS_CONST = 1200.0  # 近对称近似式常数，仅 m₁≈m₂ 适用（reference.md M3 非对称构造第 2 条）

# --- M4 空腔增益叠加（区间 [下界, 上界]，单位 dB） ---
M4_CAVITY_DEPTH_GAIN = (             # 空腔深度 → 增益（reference.md M4 深度增益表）
    (0.0, 50.0, (0.0, 2.0)),
    (50.0, 75.0, (2.0, 4.0)),
    (75.0, 100.0, (4.0, 6.0)),
    (100.0, 150.0, (5.0, 8.0)),
    (150.0, float("inf"), (6.0, 10.0)),
)
M4_FILLING_GAIN = {                  # 填充材料 + 填充率 → 增益（reference.md M4 填充增益表）
    ("岩棉", "满填"): (3.0, 5.0),
    ("岩棉", "部分填充"): (1.0, 3.0),
    ("玻璃棉", "满填"): (2.0, 4.0),
    ("玻璃棉", "部分填充"): (1.0, 2.0),
    ("无", "无"): (0.0, 0.0),
}
M4_DECOUPLING_GAIN = {               # 连接方式 → 增益（reference.md M4 解耦增益表）
    "刚性连接": (0.0, 0.0),
    "弹性连接": (3.0, 5.0),
    "解耦连接": (5.0, 10.0),
}
M4_GAIN_PRECISION = (2.0, 3.0)       # 增益 ±2-3 dB；系经验判断、待独立验证（reference.md M4 精度声明）

# --- M5 撞击声 ---
M5_IMPROVEMENT_SLOPE_DB = 40.0       # ΔL ≈ 40·lg(f/f₀)，f > f₀（reference.md M5 公式）
M5_DELTALW_MASS_COEF = 18.0          # ΔLw ≈ 18·lg(m') − 10·lg(s) + C
M5_DELTALW_STIFF_COEF = 10.0
M5_DELTALW_C_DEFAULT = 10.0          # 待独立验证的经验定标取值；PL-055 未决 ⇒ 本值不得对外作可核验精度证据
M5_DELTALW_C_TYPICAL = (8.0, 12.0)
M5_MN_M3_TO_N_M3 = 1e6               # s 单位绑定：f₀ 式须以 N/m³ 代入（reference.md M5「s 的单位绑定」注）
M5_EXCLUDED_FROM_EFFECTIVE_MASS = ("结构楼板", "弹性垫自身", "活荷载与可变堆载")  # m' 口径排除三项
# 判「哪一层是垫层」的在册材料名（＝IC-08 板材枚举中的垫层支 ＋ reference.md §二 弹性垫层表）；
# 用名单而非 "垫" 字子串，避免「垫层找平」一类同音材料被误排除。
M5_PAD_MATERIALS = ("橡胶垫", "弹性垫层", "隔声垫", "复合隔声垫", "软木垫", "聚氨酯垫")
M5_BARE_SLAB_LNW = (                 # 裸板 Ln,w 基准（reference.md M5 基准表；来源＝检测统计）
    (240.0, (78.0, 82.0)),   # 100mm 钢筋混凝土 ~240 kg/㎡
    (288.0, (76.0, 80.0)),   # 120mm 钢筋混凝土 ~288 kg/㎡
    (360.0, (74.0, 78.0)),   # 150mm 钢筋混凝土 ~360 kg/㎡
)
# （旧形制在此设过 `M5_BARE_SLAB_TOLERANCE_KG_M2 = 30.0`「查表容差」：reference.md 现读无该值，
#   2026-10-17 独立复核命中「自造数值触本仓红线」后删除。声明域一律取表首末档 240—360 kg/㎡。）

# --- M6 声桥／缝隙 ---
M6_BRIDGE_LOSS = {                   # 声桥类型 → 隔声量下降（reference.md M6 声桥表）
    "龙骨刚性连接": (3.0, 5.0),
    "贯通构件": (3.0, 8.0),
    "节点搭接不连续": (2.0, 5.0),
    "严重声桥": (8.0, 15.0),
}
M6_GAP_TABLE = (                     # 简化估算表（reference.md M6；适用前提 R_wall≈30 dB、R_gap=0）
    (0.0001, (0.0, 0.4)),
    (0.001, (2.0, 4.0)),
    (0.01, (8.0, 12.0)),
    (0.015, (12.0, float("inf"))),   # 在册 :313 末档逐字「> 1.5% → > 12 dB」，开区间、无上限
)
M6_GAP_TABLE_PREMISE = "R_wall ≈ 30 dB、R_gap = 0 dB"
M6_GAP_TABLE_PREMISE_R_WALL_DB = 30.0    # 在册特例点：本表由 R_wall≈30 dB、R_gap=0 复算而得（reference.md :315）；
#   偏离该点即不得套用表档。旧形制在此设过 40.0 dB 的自造门限，2026-10-17 复核命中后取消。
M6_R_GAP_DEFAULT = 0.0                    # 缝隙隔声量通常取 0-5 dB，保守取 0

# --- M7 基准校准 ---
M7_VALID_R_RANGE = (0.5, 2.0)           # r 超出即大幅外推，降级为仅趋势判断
M7_SINGLE_LEAF_COEF = 20.0
M7_DOUBLE_LEAF_UPPER_COEF = 40.0
M7_CROSSCHECK_THRESHOLD_DB = 5.0        # 差 > 5 dB 不构成自动择优（reference.md M7 交叉核对要求）
M7_PRECISION = (2.0, 3.0)               # ±2-3 dB；待独立验证的经验区间（reference.md M7 证据状态条）
M7_CORRECTION_TABLE = {                 # 非面密度参数差异修正（reference.md M7 Step 4）
    "空腔填充厚度增加": (1.0, 2.0),
    "空腔深度增加": (1.0, 1.0),
    "连接方式增强": (1.0, 2.0),
    "填充材料更换": (-2.0, 2.0),
}
# 注：Step 4 表的「连接方式机理级变化（共用龙骨→独立双排龙骨 +5~7 dB）」一档**不入本表**——
# 该档自载属机理级变化、须回退 M1—M6 全模型（reference.md M7 适用前提 3），
# 把它做成可查用的修正项即等于允许绕过失效条件 3。

# --- 现场量族相对实验室量的位移档（判据＝reference.md §评价量对照表「DnT,w+C 通常比实验室 Rw+C 低 5-10 dB，经验归纳」） ---
FIELD_METRIC_DROP_DB = (5.0, 10.0)   # (下界位移, 上界位移)，单位 dB；仅用于 DnT,w+C 请求

# --- 精度声明（判据＝SKILL.md §五 输出规范；PL-055 未决，本处为经验值登记） ---
PRECISION_DB = {
    "Rw+C": (3.0, 5.0),
    "Rw+Ctr": (3.0, 5.0),
    "DnT,w+C": (5.0, 10.0),
    "Ln,w": (4.0, 6.0),
    "ΔL": (3.0, 5.0),
    "ΔLw": (3.0, 5.0),
}

# --- 材料参数库（判据＝reference.md §二；区间为 B 类材料参数，S 级仅信息性渠道标注） ---
PANEL_MATERIALS = {
    # 材料类别: {子类: {"rho": kg/m³ 区间, "E": GPa 区间, "sigma": 区间, "channel": 渠道标注}}
    "石膏板": {
        "标准": {"rho": (700.0, 800.0), "E": (2.0, 2.5), "sigma": (0.22, 0.25), "channel": "S2"},
        "高密度": {"rho": (800.0, 900.0), "E": (2.2, 2.8), "sigma": (0.22, 0.25), "channel": "S2"},
    },
    "耐火石膏板": {
        "默认": {"rho": (800.0, 900.0), "E": (2.2, 2.8), "sigma": (0.22, 0.25), "channel": "S2"},
    },
    "硅酸钙板": {
        "标准": {"rho": (1000.0, 1200.0), "E": (6.0, 8.0), "sigma": (0.20, 0.25), "channel": "S2"},
        "高密度": {"rho": (1200.0, 1400.0), "E": (7.0, 10.0), "sigma": (0.20, 0.25), "channel": "S2"},
        "轻质": {"rho": (800.0, 1000.0), "E": (4.0, 6.0), "sigma": (0.20, 0.25), "channel": "S2"},
    },
    "纤维水泥板": {
        "标准": {"rho": (1300.0, 1600.0), "E": (5.0, 10.0), "sigma": (0.18, 0.22), "channel": "S2"},
        "高密度": {"rho": (1600.0, 1800.0), "E": (8.0, 12.0), "sigma": (0.18, 0.22), "channel": "S2"},
    },
    "ALC": {
        "默认": {"rho": (500.0, 700.0), "E": (1.5, 3.0), "sigma": (0.18, 0.22), "channel": "S2"},
    },
}
# 参数库未收录的 IC 枚举值：不得由本内核代拟参数（走「材料参数缺失」降级并要求传入实测值）
PANEL_MATERIALS_UNDOCUMENTED = ("混凝土空心条板", "GRC条板", "发泡陶瓷条板", "金属板", "木饰面板", "其他")
RESILIENT_PAD_STIFFNESS = {          # 动态刚度 s，MN/m³（reference.md §二 弹性垫层；B 类，渠道 S2 信息性）
    "橡胶垫": (10.0, 30.0),
    "聚氨酯垫": (5.0, 20.0),
    "软木垫": (15.0, 50.0),
    "复合隔声垫": (5.0, 15.0),
}
FILLING_MATERIALS = {                # 容重 kg/m³ 与流阻率 kPa·s/m²（reference.md §二 填充材料）
    "岩棉": {"bulk": (40.0, 120.0), "flow_resistivity": (5.0, 30.0)},
    "玻璃棉": {"bulk": (24.0, 48.0), "flow_resistivity": (3.0, 15.0)},
}
SUBSTRATE_MATERIALS = {              # 基层材料（reference.md §二 基层材料表，IC-08 楼板基层）
    "混凝土楼板": {"rho": (2300.0, 2500.0), "E": (28.0, 32.0), "sigma": (0.20, 0.20),
                   "channel": "S1", "note": "普通混凝土 C25-C40，渠道标注仅信息性"},
    "细石混凝土": {"rho": (2000.0, 2200.0), "E": (15.0, 25.0), "sigma": (0.20, 0.20),
                   "channel": "S4", "note": "找平层，行业典型值"},
    "水泥砂浆": {"rho": (1800.0, 2000.0), "E": (10.0, 20.0), "sigma": (0.20, 0.20),
                 "channel": "S4", "note": "找平层，行业典型值"},
}

# --- 统一不可处理范围（判据＝reference.md §四 八项表；U 编号与输出文本逐字取该表） ---
UNPROCESSABLE_FACTORS = {
    "U-01": "现场侧向传声未纳入模型，实际隔声量可能低于估算值 5-15 dB",
    "U-02": "开口和薄弱构件显著降低整体隔声，需专项设计",
    "U-03": "楼板/梁柱绕射传声未纳入模型，需结构声学专项分析",
    "U-04": "复杂节点泄漏未纳入模型，需专项密封设计",
    "U-05": "计算基于理想构造模型，实际施工质量可能导致 ±3-8 dB 离散",
    "U-06": "房间混响时间修正超出 ACE 模型范围，现场测量时须由专业机构执行",
    "U-07": "ACE 输出为专业估算，不替代已安装现场的实测",
    "U-08": "结构振动噪声超出 ACE 空气声隔声模型范围，建议结构声学顾问复核",
}
DEFAULT_OUT_OF_RANGE_HINTS = ("U-01", "U-02", "U-05", "U-07")

# --- 四频段（判据＝reference.md §三 四频段估算方法论） ---
FREQUENCY_BANDS_HZ = (
    ("低频段", 100.0, 315.0, "MSM 共振、刚度控制区"),
    ("中低频段", 315.0, 1000.0, "质量控制区起始"),
    ("中高频段", 1000.0, 3150.0, "质量控制区、吻合效应起始"),
    ("高频段", 3150.0, float("inf"), "吻合效应、阻尼控制区"),  # 在册逐字「3150+ Hz」，无上限
)

# ============================================================
# 二、异常与红线类型（红线段的行为面，此处仅声明形态）
# ============================================================


class ACEError(Exception):
    """内核全部降形态的基类；携带 IC 降级规则的逐字输出串。"""

    kind = ""
    zh = ""

    def as_payload(self):
        return {"降级": self.kind, "说明": self.zh}


class ParameterInsufficient(ACEError):
    """降级规则「参数不足」：返回该串并列出缺失字段（SKILL.md §五）。"""

    kind = "参数不足"
    zh = "参数不足，无法执行有效计算"

    def __init__(self, missing_fields):
        super().__init__(self.zh + "：" + ", ".join(missing_fields))
        self.missing_fields = list(missing_fields)

    def as_payload(self):
        return {"降级": self.kind, "说明": self.zh, "缺失字段": self.missing_fields}


class OutOfModelRange(ACEError):
    """降级规则「构造超出适用边界」＋红线 ACE-R-P0-2：禁止继续给出确定数值。"""

    kind = "超出模型适用范围"
    zh = "超出模型适用范围"

    def __init__(self, factors, advice):
        super().__init__(self.zh)
        self.factors = list(factors)
        self.advice = advice

    def as_payload(self):
        return {"降级": self.kind, "说明": self.zh, "不可建模因素": self.factors,
                "人工评估建议": self.advice}


class UnsupportedCalcType(ACEError):
    """降级规则「计算类型不支持」。"""

    kind = "计算类型不支持"

    def __init__(self, calc_type):
        self.calc_type = calc_type
        super().__init__("当前计算类型 [%s] 不在 ACE 支持范围内" % calc_type)

    def as_payload(self):
        return {"降级": self.kind, "说明": str(self)}


class ComplianceJudgementRefused(ACEError):
    """红线 ACE-R-P1-3：内核不判断是否满足标准限值，也不接受限值入参。"""

    kind = "拒绝合规判断"
    zh = "合规判断由发起方基于 A1/B2 完成，ACE 不含标准限值、不做达标判定"

    def __init__(self, hits=()):
        super().__init__(self.zh)
        self.hits = list(hits)

    def as_payload(self):
        out = {"降级": self.kind, "说明": self.zh}
        if self.hits:
            out["命中字段"] = self.hits
        return out


# ============================================================
# 三、模型段 M1—M7（数据段之外不得出现数值字面）
# ============================================================


def mass_law(m, f, const=M1_SPECTRAL_CONST_DB):
    """M1：R(f) = 20·lg(m·f) + const。m 面密度 kg/㎡，f 频率 Hz。"""
    if m <= 0 or f <= 0:
        raise ValueError("m and f must be positive")
    return 20 * math.log10(m * f) + const


def mass_law_single_value(m, k=M1_SIMPLE_K):
    """M1 简化式：Rw ≈ 20·lg(m) + K。仅用于单板概念性相对比较，不得作确定性 Rw 输出。"""
    if m <= 0:
        raise ValueError("m must be positive")
    return 20 * math.log10(m) + k


def coincidence_freq(h_m, rho, E, sigma):
    """M2：Cremer 吻合频率 fc = c²/(2π·h)·√(12(1−σ²)·ρ/E)。h 单位 m、E 单位 Pa。"""
    if h_m <= 0 or rho <= 0 or E <= 0:
        raise ValueError("h, rho, E must be positive")
    if not (M2_POISSON_DOMAIN[0] < sigma < M2_POISSON_DOMAIN[1]):
        raise ValueError("Poisson ratio must be in (0, 0.5)")
    return (SOUND_SPEED_M_S ** 2) / (2 * math.pi * h_m) * math.sqrt(
        12 * (1 - sigma ** 2) * rho / E)


def coincidence_risk_level(fc_hz):
    """M2：按 fc 落带给出风险等级（判据＝M2_RISK_BANDS，即 reference.md M2 判定表；表为唯一数值源）。"""
    for lo, hi, level in M2_RISK_BANDS:
        if lo < fc_hz <= hi:
            return level
    raise ValueError("unreachable fc: %r" % fc_hz)


def coincidence_risk_band_text(fc_hz):
    """风险频率范围串：fc ±10%（M2 精度声明），形如 "2000-2500"。"""
    lo = fc_hz * (1 - M2_FC_PRECISION)
    hi = fc_hz * (1 + M2_FC_PRECISION)
    return "%d-%d" % (round(lo), round(hi))


def reduced_mass(m1, m2):
    """M3：约化面密度 m_red = m₁·m₂/(m₁+m₂)，对任意正质量比精确成立。"""
    if m1 <= 0 or m2 <= 0:
        raise ValueError("m1 and m2 must be positive")
    return m1 * m2 / (m1 + m2)


def msm_resonance(d_cm, m1, m2):
    """M3：f₀ = 600/√(d_cm·m_red)，d 单位 cm。"""
    if d_cm <= 0:
        raise ValueError("d must be positive")
    return M3_CONST / math.sqrt(d_cm * reduced_mass(m1, m2))


def msm_resonance_iso_exact(d_cm, m1, m2):
    """M3 精确式（交叉核对用）：f₀ = (1/2π)·√(ρ_air·c²/(d_m·m_red))。"""
    d_m = d_cm / 100.0
    return (1 / (2 * math.pi)) * math.sqrt(
        AIR_DENSITY_KG_M3 * SOUND_SPEED_M_S ** 2 / (d_m * reduced_mass(m1, m2)))


def msm_resonance_approx_total(d_cm, m1, m2):
    """近对称近似式 f₀ ≈ 1200/√(d·(m₁+m₂))：仅 m₁≈m₂ 适用，非对称构造须回精确式。"""
    if m1 <= 0 or m2 <= 0 or d_cm <= 0:
        raise ValueError("d, m1, m2 must be positive")
    return M3_APPROX_TOTAL_MASS_CONST / math.sqrt(d_cm * (m1 + m2))


def double_cavity_coupled_modes(d1_cm, d2_cm, m1, m2, m3):
    """M4 双空腔条：三质量-两弹簧耦合系统的两个非零模态（Hz），按升序返回。

    两腔分别按单腔 MSM 计算不还原耦合模态逐值，dB 增益无可加性
    （reference.md M4「双空腔系统」条）⇒ 本函数只给模态，不合成增益。
    """
    k1 = AIR_DENSITY_KG_M3 * SOUND_SPEED_M_S ** 2 / (d1_cm / 100.0)
    k2 = AIR_DENSITY_KG_M3 * SOUND_SPEED_M_S ** 2 / (d2_cm / 100.0)
    inv = [1.0 / m1, 1.0 / m2, 1.0 / m3]
    # 特征方程 ω⁴ − b·ω² + c = 0（3×3 对称质量归一矩阵的非零模态）
    b = (k1 + k2) * inv[1] + (k1 * inv[0] + k2 * inv[2])
    c = k1 * k2 * (inv[0] * inv[1] + inv[0] * inv[2] + inv[1] * inv[2])
    disc = b * b - 4 * c
    if disc < 0:
        raise ValueError("negative discriminant; check inputs")
    w2 = [(b + math.sqrt(disc)) / 2.0, (b - math.sqrt(disc)) / 2.0]
    return sorted(math.sqrt(v) / (2 * math.pi) for v in w2)


def frequency_band_of(f_hz):
    """把频率落到 reference.md §三 四频段的频段名（M1 有效域外的频点返回「域外频点」）。"""
    for name, lo, hi in ((n, lo, hi) for n, lo, hi, _note in FREQUENCY_BANDS_HZ):
        if lo <= f_hz < hi:
            return name
    return "域外频点"


def deepest_depth_for_note(depths):
    """假设说明里引用的空腔深度＝最深腔（与取档口径一致）；无空腔时返回 0。"""
    vals = [d for d in depths if d]
    return max(vals) if vals else 0.0


def cavity_depth_gain(d_mm):
    """M4 空腔深度增益区间 (下界, 上界)。"""
    for lo, hi, gain in M4_CAVITY_DEPTH_GAIN:
        if lo <= d_mm < hi or (hi == float("inf") and d_mm >= lo):
            return gain
    raise ValueError("cavity depth out of table: %r" % d_mm)


def filling_gain(material, ratio, assumptions=None):
    """M4 填充增益区间；表档未载的组合**按无填充计**并披露，不借用他档（CG-20261017-007 整改）。

    旧形制缺陷＝未载组合回落到「玻璃棉/部分填充」＝代拟参数，与件内不变式
    「本内核不代拟参数」及契约表两枚默认档（填充材料默认"无"、填充率默认"无"）互斥；
    一手读数＝聚酯纤维棉/满填 与 岩棉/填充率缺失 均被凭空记 +1~2 dB 且假设列零披露。
    """
    key = (material or "无", ratio or "无")
    if key in M4_FILLING_GAIN:
        return M4_FILLING_GAIN[key]
    if assumptions is not None:
        assumptions.append("填充组合「%s／%s」不在 M4 填充增益表档内，按无填充计（不借用他档、"
                           "不代拟参数）" % (key[0], key[1]))
    return M4_FILLING_GAIN[("无", "无")]


def decoupling_gain(connection):
    """M4 解耦增益区间。"""
    if connection not in M4_DECOUPLING_GAIN:
        raise ValueError("unknown connection: %r" % connection)
    return M4_DECOUPLING_GAIN[connection]


def impact_sound_delta_L(f, f0):
    """M5：ΔL = 40·lg(f/f₀)（f > f₀），f ≤ f₀ 时改善量为 0。"""
    if f <= f0:
        return 0.0
    return M5_IMPROVEMENT_SLOPE_DB * math.log10(f / f0)


def floating_floor_f0(s_MNm3, m_prime):
    """M5 浮筑地面共振频率：s 由 MN/m³ 换算 N/m³ 后代入 SI 式（单位绑定条）。"""
    if s_MNm3 <= 0 or m_prime <= 0:
        raise ValueError("s and m_prime must be positive")
    return (1 / (2 * math.pi)) * math.sqrt(s_MNm3 * M5_MN_M3_TO_N_M3 / m_prime)


def impact_sound_deltaLw(m_prime, s_MNm3, C=M5_DELTALW_C_DEFAULT):
    """M5 单值评价 ΔLw = 18·lg(m') − 10·lg(s) + C；s 按 MN/m³ 数值代入（与 C 同定标）。"""
    if m_prime <= 0 or s_MNm3 <= 0:
        raise ValueError("m_prime and s must be positive")
    return (M5_DELTALW_MASS_COEF * math.log10(m_prime)
            - M5_DELTALW_STIFF_COEF * math.log10(s_MNm3) + C)


def bare_slab_lnw(m_slab):
    """查 M5 裸板 Ln,w 基准表：**表声明域内取最近档**；域外返回 None（调用方须降级，不得外推）。

    域界取表首末档 240—360 kg/㎡。旧形制在此设过 ±30 kg/㎡「查表容差带」（reference.md 现读无该值，
    2026-10-17 独立复核命中「自造数值」后取消）；该容差带还会让 240／288 两档在 258—270 kg/㎡ 重叠、
    按表序恒返回首档（偏低档），故同时改为逐档取最小距离（并列时取低质量档，保守）。
    """
    if not (M5_BARE_SLAB_LNW[0][0] <= m_slab <= M5_BARE_SLAB_LNW[-1][0]):
        return None
    best = min(M5_BARE_SLAB_LNW, key=lambda x: (abs(x[0] - m_slab), x[0]))
    return best[1]


def gap_effective_R(R_wall, gap_fraction, R_gap=M6_R_GAP_DEFAULT):
    """M6 缝隙合成：R = −10·lg[(1−p)·10^(−R_wall/10) + p·10^(−R_gap/10)]。

    单值代入口径属近似（reference.md M6「单值代入口径（近似声明）」条）。
    """
    if not (0 < gap_fraction < 1):
        raise ValueError("gap_fraction must be in (0, 1)")
    tau = ((1 - gap_fraction) * 10 ** (-R_wall / 10)
           + gap_fraction * 10 ** (-R_gap / 10))
    return -10 * math.log10(tau)


def gap_loss_from_table(gap_fraction, r_wall_db=None):
    """M6 简化估算表档；表自带适用前提＝R_wall ≈ 30 dB，明显高于该量级者不得套用本表。

    前提判据取 reference.md M6 适用前提注（:315 逐字：上表由上式在 R_wall ≈ 30 dB、R_gap = 0 dB
    的特例算得，不得当普适值使用）⇒ 给了 r_wall_db 且不等于该在册特例点即不得套表，须走
    gap_effective_R 现算（旧形制把前提只写在注释里无机检面；再前一版又自设过 40 dB 门限，同批取消）。
    """
    if r_wall_db is not None and abs(r_wall_db - M6_GAP_TABLE_PREMISE_R_WALL_DB) > 1e-9:
        raise OutOfModelRange(
            ["缝隙表档的在册适用前提为「%s」特例，本例 R_wall=%.1f dB 偏离该点"
             % (M6_GAP_TABLE_PREMISE, r_wall_db)],
            "须按 gap_effective_R 以实际 R_wall 现算缝隙合成隔声量，不得套用简化估算表")
    for pt, band in M6_GAP_TABLE:
        if gap_fraction <= pt:
            return band
    return M6_GAP_TABLE[-1][1]


def m7_delta_R_bounds(r):
    """M7 Step 2 两界：返回 (20·lg r, 40·lg r)。位名不表大小，r<1 时第二值更负。"""
    if r <= 0:
        raise ValueError("r must be positive")
    return (M7_SINGLE_LEAF_COEF * math.log10(r),
            M7_DOUBLE_LEAF_UPPER_COEF * math.log10(r))


def m7_delta_R_interval(r):
    """M7 输出区间：端点一律按数值升序（Step 2「恒升序」判据）。"""
    lo, hi = m7_delta_R_bounds(r)
    return (min(lo, hi), max(lo, hi))


def m7_is_valid_extrapolation(r):
    """M7 Step 3：r 超出 [0.5, 2] 属大幅外推，只给趋势、不给确定区间。"""
    return M7_VALID_R_RANGE[0] <= r <= M7_VALID_R_RANGE[1]


def m7_crosscheck_disclosure(Rw_base, mass_law_ref_db=None):
    """M7 交叉核对：基准值与 M1 现算值之差越过阈值时给披露串，否则返回 None。

    判据＝reference.md M7 交叉核对条「差 > 5 dB 不构成自动择优」（阈值取常量
    `M7_CROSSCHECK_THRESHOLD_DB`）⇒ 本函数只给披露与回退判据，不代替人工选值
    （无状态、不改入参，调用方据此决定是否回退 M1—M6）。

    形制注：本 docstring 不得写成格式化串（三引号串后接 `% 变量` 会使它不再是字符串字面量语句，
    `__doc__` 变 None＝2026-10-17 独立复核命中，已复原）。
    """
    if mass_law_ref_db is None:
        return None
    d = abs(Rw_base - mass_law_ref_db)
    if d <= M7_CROSSCHECK_THRESHOLD_DB:
        return None
    return ("交叉核对：基准值 %.1f dB 与 M1 质量定律现算 %.1f dB 差 %.1f dB > %.0f dB，"
            "不构成自动择优，须回查基准来源与构造同族性（reference.md M7 交叉核对条）"
            % (Rw_base, mass_law_ref_db, d, M7_CROSSCHECK_THRESHOLD_DB))


def m7_calibrate(Rw_base, r, corrections=(), mechanism_level_change=False,
                 source_traceable=True, same_family=True, mass_law_ref_db=None):
    """M7 基准校准：返回 (下界, 上界, 中值) 或按失效条件回退。

    corrections 为 M7_CORRECTION_TABLE 的键序列；端点配对法＝下界端加各修正项下界之和、
    上界端加上界之和（reference.md M7 Step 5，本内核不重复承载该判据）。
    失效条件 1—3 任一触发 → 返回 None，调用方须标注「基准校准不适用」并回退 M1—M6。
    """
    if not source_traceable or not same_family or mechanism_level_change:
        return None
    if not m7_is_valid_extrapolation(r):
        return None
    dlo, dhi = m7_delta_R_interval(r)
    if m7_crosscheck_disclosure(Rw_base, mass_law_ref_db) is not None:
        # 交叉核对差越过阈值 ⇒ 不自动择优，按失效条件回退（返回 None，由调用方带出披露串）
        return None
    slo = sum(M7_CORRECTION_TABLE[k][0] for k in corrections)
    shi = sum(M7_CORRECTION_TABLE[k][1] for k in corrections)
    lo = Rw_base + dlo + slo
    hi = Rw_base + dhi + shi
    return (lo, hi, (lo + hi) / 2.0)


# ============================================================
# 四、接口段：字段声明与请求校验
# ============================================================

# 字段声明：{IC: {方向: {字段路径: (类型, 必填档)}}}
# 路径写法与契约件字段约束表逐字同形（含 "[]" 数组元素位）。
IC_FIELD_SPECS = {
    "IC-03": {
        "请求": {
            "调用方标识": ("object", "必填"),
            "调用方标识.技能标识": ("string", "必填"),
            "调用方标识.计算类型": ("string", "必填"),
            "调用方标识.评价量": ("string", "必填"),
            "构造描述": ("object", "必填"),
            "构造描述.构造类型": ("string", "必填"),
            "构造描述.板材层": ("数组[object]", "必填"),
            "构造描述.板材层[].材料类别": ("string", "必填"),
            "构造描述.板材层[].厚度_mm": ("number", "必填"),
            "构造描述.板材层[].面密度_kg_m2": ("number", "可选"),
            "构造描述.板材层[].层数": ("number", "可选"),
            "构造描述.空腔层": ("数组[object]", "必填（双层/多层构造时）"),
            "构造描述.空腔层[].深度_mm": ("number", "必填"),
            "构造描述.空腔层[].填充材料": ("string", "可选"),
            "构造描述.空腔层[].填充率": ("string", "可选"),
            "构造描述.支撑/连接系统": ("object", "必填"),
            "构造描述.支撑/连接系统.支撑类型": ("string", "必填"),
            "构造描述.支撑/连接系统.连接方式": ("string", "必填"),
            "构造描述.支撑/连接系统.龙骨间距_mm": ("number", "可选"),
            "构造描述.密封方式": ("string", "可选"),
            "计算选项": ("object", "可选"),
            "计算选项.输出详细度": ("string", "可选"),
            "计算选项.频率范围_Hz": ("object", "可选"),
        },
        "响应": {
            "计算结果": ("object", "是"),
            "计算结果.隔声估算区间": ("object", "是"),
            "计算结果.隔声估算区间.下限_dB": ("number", "是"),
            "计算结果.隔声估算区间.上限_dB": ("number", "是"),
            "计算结果.隔声估算区间.评价量": ("string", "是"),
            "计算结果.隔声估算区间.置信度": ("string", "是"),
            "风险识别": ("object", "是"),
            "风险识别.吻合效应风险": ("object", "是"),
            "风险识别.吻合效应风险.等级": ("string", "是"),
            "风险识别.吻合效应风险.风险频率范围_Hz": ("string", "是"),
            "风险识别.吻合效应风险.说明": ("string", "是"),
            "风险识别.声桥风险等级": ("object", "是"),
            "风险识别.声桥风险等级.等级": ("string", "是"),
            "风险识别.声桥风险等级.风险点": ("数组[string]", "是"),
            "风险识别.声桥风险等级.说明": ("string", "是"),
            "风险识别.密封评估": ("object", "是"),
            "风险识别.密封评估.结论": ("string", "是"),
            "风险识别.密封评估.依据": ("string", "是"),
            "风险识别.密封评估.改进建议": ("数组[string]", "否"),
            "风险识别.构造合理性意见": ("object", "是"),
            "风险识别.构造合理性意见.结论": ("string", "是"),
            "风险识别.构造合理性意见.风险点": ("数组[string]", "是"),
            "风险识别.构造合理性意见.优化方向": ("数组[string]", "否"),
            "关键假设说明": ("数组[string]", "是"),
            "适用边界声明": ("string", "是"),
            "超出范围提示": ("数组[string]", "是"),
        },
    },
    "IC-08": {
        "请求": {
            "调用方标识.技能标识": ("string", "必填"),
            "调用方标识.计算类型": ("string", "必填"),
            "调用方标识.评价量": ("string", "必填"),
            "构造描述.构造类型": ("string", "必填"),
            "构造描述.板材层": ("数组[object]", "必填"),
            "构造描述.板材层[].材料类别": ("string", "必填"),
            "构造描述.板材层[].厚度_mm": ("number", "必填"),
            "构造描述.板材层[].面密度_kg_m2": ("number", "可选"),
            "构造描述.支撑/连接系统.支撑类型": ("string", "必填"),
            "构造描述.支撑/连接系统.弹性垫动态刚度_MN_m3": ("number", "可选"),
            "构造描述.密封方式": ("string", "可选"),
        },
        "响应": {
            "计算结果.隔声估算区间.下限_dB": ("number", "是"),
            "计算结果.隔声估算区间.上限_dB": ("number", "是"),
            "计算结果.隔声估算区间.评价量": ("string", "是"),
            "计算结果.隔声估算区间.置信度": ("string", "是"),
            "风险识别.声桥风险等级.等级": ("string", "是"),
            "风险识别.声桥风险等级.风险点": ("数组[string]", "是"),
            "风险识别.声桥风险等级.说明": ("string", "是"),
            "风险识别.密封评估.结论": ("string", "是"),
            "风险识别.密封评估.依据": ("string", "是"),
            "风险识别.密封评估.改进建议": ("数组[string]", "否"),
            "关键假设说明": ("数组[string]", "是"),
            "适用边界声明": ("string", "是"),
            "超出范围提示": ("数组[string]", "是"),
        },
    },
    "IC-09": {
        "请求": {
            "调用方标识.技能标识": ("string", "必填"),
            "调用方标识.计算类型": ("string", "必填"),
            "调用方标识.评价量": ("string", "必填"),
            "构造描述.构造类型": ("string", "必填"),
            "构造描述.板材层": ("数组[object]", "必填"),
            "构造描述.板材层[].材料类别": ("string", "必填"),
            "构造描述.板材层[].厚度_mm": ("number", "必填"),
            "构造描述.板材层[].面密度_kg_m2": ("number", "可选"),
            "构造描述.板材层[].层数": ("number", "可选"),
            "构造描述.空腔层": ("数组[object]", "条件必填"),
            "构造描述.空腔层[].深度_mm": ("number", "必填"),
            "构造描述.空腔层[].填充材料": ("string", "可选"),
            "构造描述.空腔层[].填充率": ("string", "可选"),
            "构造描述.支撑/连接系统.支撑类型": ("string", "必填"),
            "构造描述.支撑/连接系统.连接方式": ("string", "可选"),
            "构造描述.支撑/连接系统.吊杆间距_mm": ("number", "可选"),
            "构造描述.密封方式": ("string", "可选"),
        },
        "响应": {
            "计算结果.隔声估算区间.下限_dB": ("number", "是"),
            "计算结果.隔声估算区间.上限_dB": ("number", "是"),
            "计算结果.隔声估算区间.评价量": ("string", "是"),
            "计算结果.隔声估算区间.置信度": ("string", "是"),
            "风险识别.吻合效应风险.等级": ("string", "是"),
            "风险识别.吻合效应风险.风险频率范围_Hz": ("string", "否"),
            "风险识别.吻合效应风险.说明": ("string", "是"),
            "风险识别.声桥风险等级.等级": ("string", "是"),
            "风险识别.声桥风险等级.风险点": ("数组[string]", "是"),
            "风险识别.声桥风险等级.说明": ("string", "是"),
            "风险识别.密封评估.结论": ("string", "是"),
            "风险识别.密封评估.依据": ("string", "是"),
            "风险识别.密封评估.改进建议": ("数组[string]", "否"),
            "关键假设说明": ("数组[string]", "是"),
            "适用边界声明": ("string", "是"),
            "超出范围提示": ("数组[string]", "是"),
        },
    },
}

# 取值域（判据＝契约件字段约束表「取值范围 / 枚举」列）
IC_DOMAINS = {
    "IC-03": {
        "技能标识": ("PW",),
        "计算类型": ("空气声隔声",),
        "评价量": ("Rw+C", "Rw+Ctr", "DnT,w+C"),
        "构造类型": ("单层均质", "双层板+空腔", "多层复合", "条板"),
        "板材材料类别": ("石膏板", "耐火石膏板", "硅酸钙板", "纤维水泥板", "ALC",
                         "混凝土空心条板", "GRC条板", "发泡陶瓷条板", "金属板", "木饰面板", "其他"),
        "空腔填充材料": ("岩棉", "玻璃棉", "聚酯纤维棉", "软木", "橡胶", "无", "其他"),
        "填充率": ("满填", "部分填充", "无"),
        "支撑类型": ("轻钢龙骨", "铝龙骨", "木龙骨", "无(条板直接安装)", "其他"),
        "连接方式": ("刚性连接", "弹性连接", "解耦连接"),
        "密封方式": ("MS密封胶", "隔声密封条", "普通密封", "未指定"),
        "输出详细度": ("简略", "标准", "详细"),
        "置信度": ("高", "中", "低"),
        "风险等级": ("无", "低", "中", "高"),
        "密封结论": ("充分", "不足", "无法判断"),
        "构造合理性结论": ("合理", "存在风险", "需改进"),
    },
    "IC-08": {
        "技能标识": ("FL",),
        "计算类型": ("撞击声隔声",),
        "评价量": ("Ln,w", "ΔL"),
        "构造类型": ("浮筑", "架空", "干法调平", "快装"),
        "板材材料类别": ("混凝土楼板", "细石混凝土", "橡胶垫", "弹性垫层", "水泥砂浆",
                         "OSB板", "硅酸钙板", "隔声垫", "其他"),
        "支撑类型": ("浮筑垫层", "架空支撑", "调平螺栓", "直铺", "其他"),
        "密封方式": ("MS密封胶", "隔声密封条", "普通密封", "未指定"),
        "置信度": ("高", "中", "低"),
        "风险等级": ("无", "低", "中", "高"),
        "密封结论": ("充分", "不足", "无法判断"),
    },
    "IC-09": {
        "技能标识": ("CL",),
        "计算类型": ("空气声隔声",),
        "评价量": ("Rw+C", "Rw+Ctr"),
        "构造类型": ("单层板", "双层板+空腔", "隔声吊顶", "其他"),
        "板材材料类别": ("石膏板", "耐火石膏板", "硅酸钙板", "纤维水泥板", "金属板", "其他"),
        "空腔填充材料": ("岩棉", "玻璃棉", "聚酯纤维棉", "软木", "橡胶", "无", "其他"),
        "填充率": ("满填", "部分填充", "无"),
        "支撑类型": ("吊杆", "弹性吊钩", "减振吊杆", "龙骨吊挂", "其他"),
        "连接方式": ("刚性连接", "弹性连接"),
        "密封方式": ("MS密封胶", "隔声密封条", "普通密封", "未指定"),
        "置信度": ("高", "中", "低"),
        "风险等级": ("无", "低", "中", "高"),
        "密封结论": ("充分", "不足", "无法判断"),
    },
}

# 红线 ACE-R-P1-3：合规判断类入参一律拒收（键名与取值面双扫；形态归一后比对，不可由参数关闭）
FORBIDDEN_REQUEST_TOKENS = ("限值", "标准限值", "是否满足", "达标", "合规判定", "requirement", "limit_db")

# 契约件字段约束表「取值范围 / 枚举」列的数值合理域逐字落成断言（判据＝shared/interface-contracts.md
# 的 IC-03 :695/:696/:699/:705、IC-08 :1141/:1142/:1144、IC-09 :1468/:1469/:1477 各行；
# 越域即「参数不足」，不得带入数值——旧形制只检枚举四枚，面密度 900／深度 900 照给确定区间）。
IC_NUMERIC_RANGES = {
    "IC-03": {"厚度_mm": (6.0, 300.0), "面密度_kg_m2": (5.0, 200.0), "深度_mm": (0.0, 300.0),
              "龙骨间距_mm": (300.0, 1200.0), "层数": (1.0, float("inf"))},  # 层数在册只 declare 正整数（Schema minimum:1，无 maximum）
    "IC-08": {"厚度_mm": (1.0, 300.0), "面密度_kg_m2": (1.0, 500.0),
              "弹性垫动态刚度_MN_m3": (5.0, 100.0)},
    "IC-09": {"厚度_mm": (4.0, 100.0), "面密度_kg_m2": (3.0, 100.0), "深度_mm": (0.0, 600.0),
              "吊杆间距_mm": (300.0, 2400.0), "层数": (1.0, float("inf"))},  # 同上：不替契约件发明上限
}

# 枚举字段路径 → IC_DOMAINS 键名（全表遍历用；旧形制只检四枚调用方标识/构造类型字段）
IC_ENUM_FIELD_PATHS = {
    "调用方标识.技能标识": "技能标识",
    "调用方标识.计算类型": "计算类型",
    "调用方标识.评价量": "评价量",
    "构造描述.构造类型": "构造类型",
    "构造描述.板材层[].材料类别": "板材材料类别",
    "构造描述.空腔层[].填充材料": "空腔填充材料",
    "构造描述.空腔层[].填充率": "填充率",
    "构造描述.支撑/连接系统.支撑类型": "支撑类型",
    "构造描述.支撑/连接系统.连接方式": "连接方式",
    "构造描述.密封方式": "密封方式",
    "计算选项.输出详细度": "输出详细度",
}


def _norm_token(text):
    """键名／取值的比对形态归一＝小写＋全角转半角＋去空白（防 Limit_dB／limit_DB／Ｒw 类改写绕过）。"""
    out = []
    for ch in str(text):
        code = ord(ch)
        if code == 0x3000:
            code = 32
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        out.append(chr(code))
    return "".join(out).casefold().replace(" ", "").replace("\t", "")

MODEL_COMBINATION = {   # 模型选择路由（判据＝reference.md §三 模型选择路由表）
    ("空气声隔声", "单层均质"): ("M1", "M2", "M6"),
    ("空气声隔声", "双层板+空腔"): ("M1", "M2", "M3", "M4", "M6"),
    ("空气声隔声", "条板"): ("M1", "M2", "M6"),
    ("空气声隔声", "多层复合"): ("M1", "M2", "M3", "M4", "M6"),
    ("撞击声隔声", "浮筑"): ("M5", "M6"),
    ("撞击声隔声", "架空"): ("M3", "M5", "M6"),
}

# ACE 支持的计算类型全集（判据＝SKILL.md §二 能力表 C1—C7 与 §三 三组契约的计算类型字段）
ACE_SUPPORTED_CALC_TYPES = tuple(sorted({ct for ct, _ in MODEL_COMBINATION}))

# 路由在册的构造类型全集。除 §三 路由表外，另收录 IC-09 侧「单层板／隔声吊顶」两档——
# 该表无吊顶专属行，吊顶档位见 reference.md §四「吊顶（IC-09）」边界表，
# 「单层板」与路由表「单层均质」同指（本处并列收录并注明，不改契约枚举字面）。
ROUTED_CONSTRUCT_TYPES = {
    "空气声隔声": ("单层均质", "双层板+空腔", "多层复合", "条板", "单层板", "隔声吊顶"),
    "撞击声隔声": ("浮筑", "架空"),
}


def _iter_paths(obj, prefix=""):
    """把请求对象铺成 IC_FIELD_SPECS 同形的路径串集合。"""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (prefix, k) if prefix else k
            out.add(p)
            out |= _iter_paths(v, p)
    elif isinstance(obj, list):
        for item in obj:
            out |= _iter_paths(item, prefix + "[]")
    return out


def _dig(obj, path):
    """按 IC_FIELD_SPECS 同形的路径取值；"[]" 段视为跳过（数组本体级取值）。"""
    node = obj
    for seg in path.replace("[]", "").split("."):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


def _walk_values(obj, prefix=""):
    """铺成 (路径, 取值) 序列，路径形制与 IC_FIELD_SPECS／`_iter_paths` 同（数组元素位记 "[]"）。"""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (prefix, k) if prefix else k
            out.append((p, v))
            out.extend(_walk_values(v, p))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_walk_values(item, prefix + "[]"))
    return out


def _collect_at(request, spec_path):
    """取 IC_FIELD_SPECS 形制路径上的全部取值（数组逐元素各一枚），返回 (显示路径, 值)。"""
    return [(p, v) for p, v in _walk_values(request) if p == spec_path]


def _collect_at_key(request, field_name):
    """按末段字段名收集全树取值（数值域校验用；同名字段在多层出现时逐一检）。"""
    return [(p, v) for p, v in _walk_values(request) if p.rsplit(".", 1)[-1] == field_name]


def validate_request(ic_id, request):
    """按 IC_FIELD_SPECS 校验请求，返回缺失的必填路径列表（空列表＝通过）。

    三条拒收形态（各对应契约件/红线注册表的一条既有判据，本函数不新造判据）：
      · 命中 FORBIDDEN_REQUEST_TOKENS → ComplianceJudgementRefused（红线 ACE-R-P1-3，不可由参数关闭）
      · 计算类型不在 MODEL_COMBINATION 内 → UnsupportedCalcType（SKILL.md §五 降级规则「计算类型不支持」）
      · 取值越出契约枚举域 → ParameterInsufficient 携带越域项（同降级规则「参数不足」形制）

    分工写明以免两处判据：本函数只判**字段是否在场**与**取值是否越域**；
    数组元素级必填（"…[].x"）与条件必填（obligation 含「条件」或括注）由计算段取数时
    抛 ParameterInsufficient，因为那两类要按构造类型/数组内容判定，本函数看不到语义。
    """
    spec = IC_FIELD_SPECS[ic_id]["请求"]
    present = _iter_paths(request)
    domains = IC_DOMAINS[ic_id]

    for token in FORBIDDEN_REQUEST_TOKENS:
        norm = _norm_token(token)
        hit = [p for p in present if norm in _norm_token(p.rsplit(".", 1)[-1])]
        if not hit:  # 键名未命中再扫取值面（防「限值45dB 是否满足」塞进良名键后回显）
            # 取值面一律走 _walk_values：它对数组逐元素下探（旧形制用 _dig，"[]" 段被剥掉后
            # 遇 list 返回 None ⇒ 板材层[].施工说明 一类数组内文本从不入检＝红线只在键名面生效）
            hit = [path for path, val in _walk_values(request)
                   if isinstance(val, str) and norm in _norm_token(val)]
        if hit:
            raise ComplianceJudgementRefused(hit[:3])

    calc = _dig(request, "调用方标识.计算类型")
    if calc and calc not in ACE_SUPPORTED_CALC_TYPES:
        raise UnsupportedCalcType(calc)

    # 枚举全表遍历（旧形制只检四枚，IC-09 送「解耦连接」这类跨 IC 值与「随便写」均直入数值）
    bad_enum = []
    for path, dkey in IC_ENUM_FIELD_PATHS.items():
        if dkey not in domains:
            continue
        for fpath, val in _collect_at(request, path):
            if val is not None and val not in domains[dkey]:
                bad_enum.append("%s 取值越域：%s（本契约域＝%s）"
                                % (fpath, val, "/".join(domains[dkey])))
    if bad_enum:
        raise ParameterInsufficient(bad_enum)

    bad_numeric = []
    for fname, (lo, hi) in IC_NUMERIC_RANGES.get(ic_id, {}).items():
        for fpath, val in _collect_at_key(request, fname):
            if val is None:
                continue
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                bad_numeric.append("%s 类型非数值：%r" % (fpath, val))
            elif not (lo <= val <= hi):
                bad_numeric.append("%s 超出契约合理范围 %.0f-%.0f：%s" % (fpath, lo, hi, val))
    if bad_numeric:
        raise ParameterInsufficient(bad_numeric)

    missing = []
    for path, (_type, obligation) in spec.items():
        if "条件" in obligation or "（" in obligation:
            continue                       # 条件必填/带括注者由计算段按语义判定
        if not obligation.startswith("必填"):
            continue
        if "[]" in path:
            if path.split("[]")[0] in present:
                continue                   # 数组本体在场即视为已交，逐元素由计算段判
        if path not in present:
            missing.append(path)
    return missing


# ============================================================
# 五、计算实现（IC-03／IC-09 空气声共用核，IC-08 撞击声）
# ============================================================


def _panel_face_density(layer, assumptions, ic_id):
    """板材层面密度：给定即用；未给定按参数库区间中值 ρ_typ·h 推算并标注（参数使用规则 3）。"""
    material = layer.get("材料类别")
    thickness_mm = layer.get("厚度_mm")
    if material is None or thickness_mm is None:
        raise ParameterInsufficient(["构造描述.板材层[].材料类别", "构造描述.板材层[].厚度_mm"])
    if material not in PANEL_MATERIALS:
        raise OutOfModelRange(
            ["材料参数缺失且参数库未收录该类别：%s（未收录清单一枚即 %s 等，见 "
             "PANEL_MATERIALS_UNDOCUMENTED）" % (material, "、".join(PANEL_MATERIALS_UNDOCUMENTED))],
            "须由调用方传入实测 ρ/E/σ 或 CMA/CNAS 检测报告数据；本内核不代拟参数")
    m = layer.get("面密度_kg_m2")
    if m is None:
        rho = PANEL_MATERIALS[material]["默认" if "默认" in PANEL_MATERIALS[material]
                                 else next(iter(PANEL_MATERIALS[material]))]["rho"]
        rho_typ = sum(rho) / 2.0
        m = rho_typ * thickness_mm / 1000.0
        assumptions.append("面密度使用典型值推算：%s %.0fmm → %.1f kg/㎡（ρ 取参数库区间中值）"
                           % (material, thickness_mm, m))
    layers = layer.get("层数", 1) or 1
    return m * layers


# --- PL-229 工程输出边界（判据单源＝reference.md「现行工程输出边界（PL-229）」） ---
# 现阶段无经独立验证、并完成误差与区间覆盖评估的系统定量路线 ⇒ 合法请求一律返回本降级形制，
# 不含 计算结果／频段 dB／精度承诺。恢复某契约的定量出口须先把该 ic_id 登记进
# VALIDATED_QUANT_ROUTES（并同批给出验证证据）；未登记即出现 计算结果 ＝ 红线段判红。
VALIDATED_QUANT_ROUTES = frozenset()
PL229_REASON_CODE = "PL-229"
PL229_FACTOR = ("未经独立验证的系统定量模型：在册质量定律基线、空腔／填充／解耦增益、声桥档与"
                "撞击声经验式的组合未完成误差与区间覆盖评估，不能作为工程预测")
PL229_ADVICE = ("需要定量结果时：引用与目标构造、安装边界和评价量均匹配的可溯源检测报告原评价量"
                "（记录报告编号与匹配核对），或转经独立验证的专项声学计算；不得以本内核研究公式、"
                "经验区间或调用方简化公式兜底")
PL229_BOUNDARY = ("当前接口不输出系统隔声定量结果（含单值、区间、频段与精度承诺）；仅返回输入校验、"
                  "风险识别与物理诊断（质量／频率），诊断不构成隔声预测，也不作合规判断依据")
SUSPENDED_HINTS = {
    "IC-03": [UNPROCESSABLE_FACTORS["U-02"], UNPROCESSABLE_FACTORS["U-03"], UNPROCESSABLE_FACTORS["U-04"]],
    "IC-09": [UNPROCESSABLE_FACTORS["U-03"], "吊杆刚性传声与吊顶内机电噪声未纳入模型",
              UNPROCESSABLE_FACTORS["U-04"]],
    "IC-08": [UNPROCESSABLE_FACTORS["U-04"], "垫层老化、施工不平整、地暖运行后隔声退化未纳入模型"],
}


def _prediction_unavailable(ic_id, metric, assumptions, risks, diagnostics):
    """PL-229 降级 payload：保留诊断与风险识别，不带任何工程 dB 值。"""
    return {
        "降级": OutOfModelRange.kind,
        "说明": OutOfModelRange.zh,
        "原因代码": PL229_REASON_CODE,
        "请求评价量": metric,
        "物理诊断": diagnostics,
        "风险识别": risks,
        "关键假设说明": list(assumptions),
        "适用边界声明": PL229_BOUNDARY,
        "超出范围提示": list(SUSPENDED_HINTS[ic_id]) + [PL229_FACTOR],
        "不可建模因素": [PL229_FACTOR],
        "人工评估建议": PL229_ADVICE,
    }


def _leaf_masses(masses, leaf_count):
    """仅接受已逐叶给出的质量；叠层不得等分成空腔两侧的独立叶。"""
    if leaf_count not in (2, 3) or len(masses) != leaf_count:
        raise ParameterInsufficient(["构造描述.板材层（独立叶片归属不足，禁止拆分或猜测分组）"])
    if any(m <= 0 or not math.isfinite(m) for m in masses):
        raise ParameterInsufficient(["独立叶面密度须为有限正数"])
    if leaf_count == 3:
        return masses[0], masses[1], masses[2], sum(masses)
    return masses[0], masses[1], 0.0, sum(masses)


def compute_airborne(ic_id, request):
    """IC-03／IC-09 共用空气声核：输入校验＋物理诊断＋风险识别，按 PL-229 返回降级 payload。

    5 步估算法的公式与表档仍住在模型段供研究复算，但不再组装为工程 dB（判据单源＝
    reference.md「现行工程输出边界（PL-229）」）。
    """
    assumptions = []
    desc = request.get("构造描述", {})
    calc_type = request["调用方标识"]["计算类型"]
    metric = request["调用方标识"]["评价量"]
    ctype = desc.get("构造类型")

    panels = desc.get("板材层", [])
    if not panels:
        raise ParameterInsufficient(["构造描述.板材层"])
    masses = [_panel_face_density(p, assumptions, ic_id) for p in panels]

    cavities = desc.get("空腔层", [])
    support = desc.get("支撑/连接系统", {})
    connection = support.get("连接方式", "刚性连接")
    if "连接方式" in IC_FIELD_SPECS[ic_id]["请求"] and not support.get("连接方式"):
        assumptions.append("连接方式未指定，按默认「刚性连接」计并标注假设")
    seal = desc.get("密封方式", "未指定")
    options = request.get("计算选项", {})
    detail = options.get("输出详细度", "标准")

    double_leaf = ctype in ("双层板+空腔", "隔声吊顶", "多层复合")
    heterogeneous = len(set(p.get("材料类别") for p in panels)) > 1
    if heterogeneous or ctype == "多层复合":
        # 适用边界表（reference.md §四）：异质复合墙 → 「趋势判断和敏感性分析／仅供方案比较」。
        # PL-229 暂停期内所有构造都不出工程 dB；此标记只追加「仅定性讨论」的假设说明。
        trend_only = True
    else:
        trend_only = False

    m_total = sum(masses)
    assumptions.append("板材总面密度 %.1f kg/㎡；单层面密度乘层数，仅统计质量，不推定独立叶片数量" % m_total)
    if m_total < M1_LIGHTWEIGHT_MASS_LIMIT:
        assumptions.append("轻质构造区：不以质量定律单值或经验区间替代系统隔声预测")
    f0 = None
    msm_note = "非双叶构造，MSM 诊断不适用"
    modes = None
    if double_leaf:
        if not cavities:
            raise ParameterInsufficient(["构造描述.空腔层"])
        depths = [c.get("深度_mm") for c in cavities]
        if any(d is None or d <= 0 for d in depths):
            raise ParameterInsufficient(["构造描述.空腔层[].深度_mm（须为正数）"])
        for cavity in cavities:
            if cavity.get("填充材料") in FILLING_MATERIALS and not cavity.get("填充率"):
                raise ParameterInsufficient(["构造描述.空腔层[].填充率"])
        if ic_id == "IC-09":
            msm_note = "IC-09 缺宿主构件与叶片归属字段，MSM 诊断不执行"
            assumptions.append("吊顶面板叠层不等于空腔两侧独立叶；缺宿主构件与叶片归属，"
                               "不计算 MSM 共振频率，不输出绝对隔声量或贡献量")
        elif ctype == "多层复合":
            msm_note = "多层复合构造的板材数组含叠层，叶片归属不可由数组推定，MSM 诊断不执行"
            assumptions.append("多层复合：板材数组可能含同侧叠层，不按逐项独立叶计算 MSM 共振频率")
        elif len(cavities) in (1, 2) and len(masses) == len(cavities) + 1:
            m1, m2, m3, _ = _leaf_masses(masses, len(cavities) + 1)
            assumptions.append("MSM 诊断的条件假设：板材数组每项各代表一片独立叶，空腔按相邻叶排列；"
                               "若实际为同侧叠层则本项频率不适用；理想空气弹簧，不含连接刚度、阻尼与边界效应")
            if len(cavities) == 2:
                modes = double_cavity_coupled_modes(depths[0] / 10.0, depths[1] / 10.0, m1, m2, m3)
                f0 = modes[1]
                msm_note = "双空腔耦合模态（逐项独立叶条件下）"
                assumptions.append("双空腔非零耦合模态 %.1f/%.1f Hz；两腔增益不得分别计算后叠加；"
                                   "模态诊断不构成系统隔声预测" % tuple(modes))
            else:
                f0 = msm_resonance(depths[0] / 10.0, m1, m2)
                msm_note = "单空腔 MSM 共振（逐项独立叶条件下）"
                assumptions.append("MSM 共振频率 f₀=%.1f Hz（空腔 %.0f mm、m₁=%.1f、m₂=%.1f kg/㎡）；"
                                   "仅为上述条件下的机理诊断" % (f0, depths[0], m1, m2))
        else:
            msm_note = "独立叶片归属不足，MSM 诊断未执行"
            assumptions.append("独立叶片归属不足，不猜测分组、不等分板材质量，MSM 诊断未执行")

    # Step 4 — 吻合效应检查
    fc_list = []
    for p in panels:
        material = p["材料类别"]
        entry = PANEL_MATERIALS.get(material, {})
        sub = entry.get("默认") or (next(iter(entry.values())) if entry else None)
        if sub is None:
            continue
        rho_typ = sum(sub["rho"]) / 2.0
        e_typ = sum(sub["E"]) / 2.0 * 1e9
        sigma_typ = sum(sub["sigma"]) / 2.0
        fc = coincidence_freq(p["厚度_mm"] / 1000.0, rho_typ, e_typ, sigma_typ)
        fc_list.append((material, p["厚度_mm"], fc))
    # M2 异质错开判据须遍历**全部异质板对**（旧形制只比 panels[0]/[1]，三层 A/A/B 的相邻组合漏检）
    for i in range(len(fc_list)):
        for j in range(i + 1, len(fc_list)):
            if fc_list[i][0] == fc_list[j][0]:
                continue                   # 同种板材两侧 fc 恒等，Δfc＝0 不属该判据对象
            d_fc = abs(fc_list[i][2] - fc_list[j][2])
            if d_fc < M2_SEPARATION_DELTA_FC_HZ:
                assumptions.append("异质板对「%s／%s」Δfc=%.0f Hz < %.0f Hz，吻合谷可能叠加，"
                                   "须按所选产品实测 ρ/E 复核（reference.md M2 异质复合条）"
                                   % (fc_list[i][0], fc_list[j][0], d_fc, M2_SEPARATION_DELTA_FC_HZ))
                break
        else:
            continue
        break

    # PL-229：不把研究用基线/经验增益/声桥档拼成工程预测，也不以固定扣减换算现场量。
    assumptions.append("请求评价量 %s：未执行相应的有效系统频谱预测与单值评价，不以换标签代替计算" % metric)

    # 吻合风险取**最劣板**（reference.md M2 判定表语义＝fc 越低越危险）：
    # 旧形制写 max(fc) ⇒ 与判据表反着取，低频吻合谷被整枚隐去（CG-20261017-007 整改）。
    worst_fc = min((fc for _, _, fc in fc_list), default=None)
    risk_level = coincidence_risk_level(worst_fc) if worst_fc else "无"

    if trend_only:
        assumptions.append("异质复合／多层复合构造仅供方案比较与定性讨论，不给工程定量预测")

    for _path, _val in _walk_values(request):
        _leaf = _path.rsplit(".", 1)[-1]
        if _leaf in UNCONSUMED_OPTION_FIELDS:
            assumptions.append("请求给了「%s」，本模型未将该参数纳入计算（契约字段约束表对在册可选参数只承诺"
                               "「缺失时 ACE 使用典型值并标注假设」，未承诺按入参参与计算）" % _leaf)

    risks = {
        "吻合效应风险": {
            "等级": risk_level,
            "风险频率范围_Hz": coincidence_risk_band_text(worst_fc) if worst_fc else "未评估",
            "说明": ("按参数库区间中值复算 fc=%.0f Hz；吻合谷深度与宽度取决于板材阻尼，"
                     "本内核不做定量估算" % worst_fc) if worst_fc else "板材参数缺失，未评估吻合效应",
        },
        "声桥风险等级": {
            "等级": "中" if connection == "刚性连接" else ("低" if connection == "弹性连接" else "无"),
            "风险点": (["龙骨与主体结构刚性连接形成潜在声桥"] if connection == "刚性连接" else []),
            "说明": ("刚性连接将结构振动传递至龙骨和面板，降低中高频隔声性能"
                     if connection == "刚性连接" else "%s：传声路径已部分切断" % connection),
        },
        "密封评估": {
            "结论": "无法判断" if seal == "未指定" else ("充分" if seal in ("MS密封胶", "隔声密封条") else "不足"),
            "依据": ("隔墙与顶板、楼板、侧墙交界处的密封处理对隔声性能影响显著"
                      if seal == "未指定" else "密封方式取值：%s" % seal),
            "改进建议": ["墙顶与墙底设置隔声密封条", "板缝和接缝处采用 MS 密封胶连续封闭"],
        },
    }
    if ic_id == "IC-03":
        risks["构造合理性意见"] = {
            "结论": "合理" if connection != "刚性连接" or not double_leaf else "存在风险",
            "风险点": ((["两叶经刚性龙骨连接，结构声桥削弱双叶解耦"]
                        if connection == "刚性连接" and double_leaf else [])
                       + (["f₀ 偏高导致低频段隔声受限"] if (f0 or 0) >= M3_GAIN_EFFECTIVE_F0_HZ else [])),
            "优化方向": ["在成本允许时考虑解耦连接以改善中高频隔声"],
        }
    if ic_id == "IC-09":
        risks["密封评估"]["依据"] = "吊顶收口与周边密封对隔声性能影响显著（密封方式：%s）" % seal
        risks["密封评估"]["改进建议"] = ["吊顶周边收口设置隔声密封条", "板缝及检修口周边连续密封"]
        if connection == "刚性连接":
            risks["声桥风险等级"]["风险点"] = ["吊杆／主龙骨与结构楼板刚性连接形成潜在声桥"]
            risks["声桥风险等级"]["说明"] = "刚性吊挂把楼板振动直接传至吊顶面板，削弱吊顶空腔的解耦作用"
        assumptions.append("未考虑侧向传声")
    diagnostics = {
        "板材总面密度_kg_m2": round(m_total, 1),
        "MSM诊断": {"状态": msm_note,
                    "共振频率_Hz": round(f0, 1) if (f0 is not None and modes is None) else None,
                    "耦合模态_Hz": [round(x, 1) for x in modes] if modes else []},
        "吻合临界频率": [{"材料类别": mat, "厚度_mm": h, "fc_Hz": round(fc, 0)}
                         for mat, h, fc in fc_list],
    }
    if detail == "详细":
        assumptions.append("详细模式不输出频段隔声量或单点隔声数值；敏感性仅作定性方向说明")
        diagnostics["定性敏感性"] = [
            {"参数名": "面板总面密度", "当前值": "%.1f kg/㎡" % m_total,
             "影响方向": "增大面密度通常改善质量控制区隔声；未经验证模型不给增量值", "敏感度": "高"},
            {"参数名": "空腔深度", "当前值": "%.0f mm" % (cavities[0]["深度_mm"] if cavities else 0),
             "影响方向": "加深空腔使 MSM 共振频率下移；系统隔声变化须以实测或经验证模型确定",
             "敏感度": "中" if double_leaf else "低"},
        ]
    return _prediction_unavailable(ic_id, metric, assumptions, risks, diagnostics)


def compute_impact(request):
    """IC-08：输入校验＋浮筑层质量／f₀ 诊断＋风险识别，按 PL-229 返回降级 payload。

    裸板基准 − ΔLw 流程与经验式仍住模型段供研究复算，不再组装为工程 Ln,w 区间。
    """
    assumptions = []
    desc = request.get("构造描述", {})
    metric = request["调用方标识"]["评价量"]
    panels = desc.get("板材层", [])
    if not panels:
        raise ParameterInsufficient(["构造描述.板材层"])
    support = desc.get("支撑/连接系统", {})
    seal = desc.get("密封方式", "未指定")

    ctype = desc.get("构造类型")
    if ctype == "架空":
        # §三 路由表把 撞击声隔声／架空 配给 M3+M5+M6，§四 边界表要求「MSM 模型估算架空层衰减」；
        # 而 IC-08 请求面的 构造描述 只声明 构造类型／板材层／支撑连接系统／密封方式 四枚
        # （additionalProperties=false，无 空腔层／架空层高度 字段）⇒ M3 无取数入口。
        # 旧形制静默按浮筑式＋橡胶垫默认刚度算，使 架空 与 浮筑 的响应逐字节相同＝基线误用。
        raise OutOfModelRange(
            ["架空地面须按 M3 估算架空层衰减，但 IC-08 请求面无架空层高度／空腔深度承载字段"],
            "须补架空层几何（净高、面板面密度、支承间距）后转专项核算，或改按浮筑构造口径提交")
    if ctype == "浮筑" or ctype is None:
        pass

    if metric == "ΔL":
        # ΔL 属频带改善量（reference.md M5 ΔL=40·lg(f/f₀)），须有频率入参；
        # IC-08 请求面 additionalProperties=false 且无 频率 字段 ⇒ 该评价量在本契约上不可算。
        # 旧形制把 Ln,w 的数值贴上 ΔL 标签返回＝正面违反 ACE-R-P2-2 的「不可混用」实质。
        raise OutOfModelRange(
            ["评价量 ΔL 属频带量，须频率入参，而 IC-08 请求面无频率承载字段"],
            "改按 Ln,w 单值口径提交，或由发起方以频带计算专项接口传入频率")

    m_slab = None
    m_float = 0.0
    pad_layers = []
    for p in panels:
        material = p.get("材料类别")
        thickness = p.get("厚度_mm")
        if material is None or thickness is None:
            raise ParameterInsufficient(["构造描述.板材层[].材料类别", "构造描述.板材层[].厚度_mm"])
        if material in M5_PAD_MATERIALS:
            # 弹性垫自身质量不计入 m'（M5 m' 口径排除三项），故垫层无须面密度，单独登记供假设说明
            pad_layers.append("%s %.0fmm" % (material, thickness))
            continue
        m = p.get("面密度_kg_m2")
        if m is None:
            entry = SUBSTRATE_MATERIALS.get(material)
            if entry is None:
                raise OutOfModelRange(["材料参数缺失且参数库未收录该类别：%s" % material],
                                      "须传入实测面密度或检测报告数据")
            m = sum(entry["rho"]) / 2.0 * thickness / 1000.0
            assumptions.append("面密度使用典型值推算：%s %.0fmm → %.1f kg/㎡" % (material, thickness, m))
        if material == "混凝土楼板":
            m_slab = m if m_slab is None else m_slab + m
        else:
            m_float += m
    if m_slab is None:
        raise ParameterInsufficient(["构造描述.板材层[].材料类别（缺混凝土楼板基层，无法查裸板 Ln,w 基准）"])
    if m_float <= 0:
        raise ParameterInsufficient(["构造描述.板材层（浮筑层有效振动面密度为 0，须列面层与垫层以上构造）"])
    assumptions.append("m' 为浮筑层有效振动面密度＝垫层以上共同运动各层之和（本例 %.1f kg/㎡）："
                       "不含%s（reference.md M5「m' 口径」三项排除）"
                       % (m_float, "、".join(M5_EXCLUDED_FROM_EFFECTIVE_MASS)
                          + ("；本例垫层 %s 亦已排除" % "、".join(pad_layers) if pad_layers else "")))

    s = support.get("弹性垫动态刚度_MN_m3")
    if s is None:
        f0 = None
        f0_note = "弹性垫动态刚度缺失，不以典型值猜填，浮筑共振频率诊断未执行"
        assumptions.append(f0_note)
    else:
        if not (isinstance(s, (int, float)) and math.isfinite(s) and s > 0):
            raise ParameterInsufficient(["构造描述.支撑/连接系统.弹性垫动态刚度_MN_m3（须为有限正数）"])
        f0 = floating_floor_f0(s, m_float)
        f0_note = "质量—弹簧单自由度诊断（理想刚性基层，不含边缘约束与垫层非线性）"
        assumptions.append("浮筑共振频率 f₀=%.1f Hz（s=%.1f MN/m³、m'=%.1f kg/㎡，SI 式换算后代入）；"
                           "仅为机理诊断，不构成撞击声改善量或 Ln,w 预测" % (f0, s, m_float))
    assumptions.append("请求评价量 %s：未执行经验证的撞击声系统预测；ΔLw 经验式（PL-055 未决）与裸板基准表"
                       "仅供研究复算，不组装为工程区间，亦不得把偏低读数称为保守" % metric)
    risks = {
        "声桥风险等级": {
            "等级": "中" if support.get("支撑类型") == "直铺" else "低",
            "风险点": ["刚性连接处（侧墙、门槛、管线穿透）形成声桥，削弱浮筑层隔声"],
            "说明": "浮筑层与结构层之间的刚性接触是撞击声改善量下降的主因",
        },
        "密封评估": {
            "结论": "无法判断" if seal == "未指定" else "充分",
            "依据": "边缘隔离与楼板交界密封处理（密封方式：%s）" % seal,
            "改进建议": ["设置边缘隔声条，避免浮筑层与结构层刚性连接"],
        },
    }
    diagnostics = {
        "楼板面密度_kg_m2": round(m_slab, 1),
        "浮筑层有效面密度_kg_m2": round(m_float, 1),
        "弹性垫动态刚度_MN_m3": s,
        "浮筑共振诊断": {"状态": f0_note, "共振频率_Hz": round(f0, 1) if f0 is not None else None},
    }
    return _prediction_unavailable("IC-08", metric, assumptions, risks, diagnostics)


def compute_request(ic_id, request):
    """内核唯一入口：请求 → IC-OUT 响应 dict；降级时返回带「降级」键的 payload。

    行为层（PL-031）四类调用测试应挂在本函数上；红线断言在返回前强制执行。

    本函数**不抛出**降级形态：全部降级（参数不足／越域／类型不支持／超出适用边界／拒收合规判断）
    一律以 payload dict 返回，形如 {"降级": <场景名>, "说明": <契约件/技能件在册措辞>, ...}；
    正常路径返回 IC-OUT 响应 dict。调用方据此分支，无需 try/except。
    """
    if ic_id not in IC_FIELD_SPECS:
        return ParameterInsufficient(
            ["接口标识 ic_id 不在册：%r（ACE 仅承担 IC-03／IC-08／IC-09）" % ic_id]).as_payload()
    try:
        missing = validate_request(ic_id, request)
        if missing:
            return ParameterInsufficient(missing).as_payload()
        calc = _dig(request, "调用方标识.计算类型")
        ctype = _dig(request, "构造描述.构造类型")
        if calc and ctype and ctype not in ROUTED_CONSTRUCT_TYPES.get(calc, ()):
            raise OutOfModelRange(
                ["构造类型「%s」在 §三 模型选择路由表内无对应模型组合" % ctype],
                "须由调用方给出路由表在册的构造类型，或转专项声学设计核算；本内核不为该构造另造算法")
        if ic_id == "IC-08":
            resp = compute_impact(request)
        else:
            resp = compute_airborne(ic_id, request)
    except ACEError as err:
        return err.as_payload()
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        # 「不抛出」承诺的行为面（SKILL.md §三 与本体 docstring 双处宣称）：模型段的域外输入
        # 一律折成「参数不足」payload，不得让调用方拿到裸异常（旧形制实测 7 类逃逸＝
        # 面密度≤0／厚度为字符串／深度≤0／层数为字符串／ic_id 越册／连接方式越域／输出面回显触发断言）。
        return ParameterInsufficient(
            ["输入取值落在模型定义域之外：%s: %s" % (type(exc).__name__, exc)]).as_payload()
    enforce_redlines(ic_id, request, resp)
    return resp


# ============================================================
# 六、红线段（断言不可被参数关闭；判据＝SKILL.md §四／redlines-registry.md §八）
# ============================================================

OUTPUT_NATURE_LABEL = "专业估算"
FORBIDDEN_OUTPUT_TOKENS = ("实验室检测值", "现场实测值", "满足标准", "达标", "符合规范要求")
# PL-229 降级 payload 内不得出现的定量出口形态（键名或取值面）：
SUSPENDED_FORBIDDEN_KEYS = ("计算结果", "频段分析", "敏感性分析", "隔声估算区间")
SUSPENDED_REQUIRED_KEYS = ("降级", "说明", "原因代码", "请求评价量", "物理诊断", "风险识别",
                           "关键假设说明", "适用边界声明", "超出范围提示", "不可建模因素", "人工评估建议")


def _iter_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _iter_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_keys(v)


def _enforce_common(ic_id, resp):
    if not resp.get("关键假设说明"):
        raise AssertionError("ACE-R-P1-1/P2-1：关键假设说明不得为空")
    if not resp.get("超出范围提示"):
        raise AssertionError("ACE-R-P0-1：超出范围提示不得为空")
    if ic_id != "IC-08" and "密封评估" not in resp.get("风险识别", {}):
        raise AssertionError("ACE-R-P1-2：空气声场景必须输出密封评估")
    if ic_id == "IC-08" and "声桥风险等级" not in resp.get("风险识别", {}):
        raise AssertionError("ACE-R-P1-1：撞击声场景必须出声桥风险")
    blob = "".join(str(v) for v in _flatten(resp))
    for token in FORBIDDEN_OUTPUT_TOKENS:
        if token in blob:
            raise AssertionError("ACE-R-P0-1：输出含检测／达标表述 token=%r" % token)
    return blob


def enforce_redlines(ic_id, request, resp):
    """返回前红线自检，违反即抛 AssertionError（由调用方视为内核缺陷）。

    PL-229：ic_id 未登记 VALIDATED_QUANT_ROUTES 时，响应必须是 PL-229 降级 payload——
             不得含 计算结果／频段／敏感性定量键，键名与取值面均不得出现 dB；
             请求评价量必须原样回显（ACE-R-P2-2 不可混用的暂停态形制）。
    ACE-R-P0-1：禁表述为检测值／实测值／达标。
    ACE-R-P2-1：关键假设说明必列且非空。
    ACE-R-P1-1／P1-2：声桥（IC-08）／密封评估（空气声）必出。
    """
    requested = (request.get("调用方标识") or {}).get("评价量")
    if ic_id not in VALIDATED_QUANT_ROUTES:
        if resp.get("原因代码") != PL229_REASON_CODE or resp.get("降级") != OutOfModelRange.kind:
            raise AssertionError("PL-229：%s 未登记经验证定量路线，响应须为 PL-229 降级 payload" % ic_id)
        missing = [k for k in SUSPENDED_REQUIRED_KEYS if k not in resp]
        if missing:
            raise AssertionError("PL-229：降级 payload 缺键 %s" % missing)
        keys = list(_iter_keys(resp))
        hit = [k for k in keys if k in SUSPENDED_FORBIDDEN_KEYS or "dB" in k]
        if hit:
            raise AssertionError("PL-229：降级 payload 含定量出口键 %s" % hit)
        if resp.get("请求评价量") not in IC_DOMAINS[ic_id]["评价量"]:
            raise AssertionError("ACE-R-P2-2：请求评价量 %r 不在本契约取值域" % resp.get("请求评价量"))
        if requested is not None and resp.get("请求评价量") != requested:
            raise AssertionError("ACE-R-P2-2：回显评价量 %r ≠ 请求评价量 %r（不可混用）"
                                 % (resp.get("请求评价量"), requested))
        blob = _enforce_common(ic_id, resp)
        if "dB" in blob:
            raise AssertionError("PL-229：降级 payload 取值面出现 dB 字面＝定量出口回潮")
        return True
    out = resp.get("计算结果", {}).get("隔声估算区间", {})
    if not out.get("评价量"):
        raise AssertionError("ACE-R-P2-2：隔声估算区间缺评价量标注")
    if out.get("评价量") not in IC_DOMAINS[ic_id]["评价量"]:
        raise AssertionError("ACE-R-P2-2：评价量 %r 不在本契约取值域" % out.get("评价量"))
    if requested is not None and out.get("评价量") != requested:
        raise AssertionError("ACE-R-P2-2：输出评价量 %r ≠ 请求评价量 %r（不可混用）"
                             % (out.get("评价量"), requested))
    if out.get("下限_dB", 0) > out.get("上限_dB", 0):
        raise AssertionError("区间端点须按数值升序（下限 ≤ 上限）")
    _enforce_common(ic_id, resp)
    return True


def _flatten(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _flatten(v)
    elif isinstance(node, list):
        for v in node:
            yield from _flatten(v)
    else:
        yield node


def output_nature_note():
    """调用方整合时须带出的输出性质声明（ACE-R-P0-1；PL-229 暂停态口径）。"""
    if not VALIDATED_QUANT_ROUTES:
        return ("隔声计算引擎（acoustic-calculation-engine）当前仅提供输入校验、物理诊断与风险识别，"
                "未输出系统隔声定量结果（PL-229），不是检测值或实测值")
    return "隔声计算结果由 acoustic-calculation-engine 技能提供，为%s性质" % OUTPUT_NATURE_LABEL
