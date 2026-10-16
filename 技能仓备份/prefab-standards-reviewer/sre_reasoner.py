"""
SRE Reasoner —— 标准推理引擎参考实现
=====================================
版本：v1.9（2026-10-12，CG-20261012-006：项目类型类比归一上线——「公寓」按住宅档进 M3 场景推理面，
      命中即 activation／mapping 证据置信度降 inferred ＋「未覆盖领域」载显式标注，闭台账 PL-198 ②）
      v1.8（2026-09-26，CG-20260926-010：名称取数改 SOT 优先——主表 > 登记表 > 硬编码预置，闭 PL-002）
      v1.7（2026-09-17，CG-20260917-009：复查到期仅提醒，既有依据未确认仍降级）
依据：standards-reasoning-rules.md v1.2.7 + interface-contracts.md IC-10 v1.9.0

最小接口：
    reason(project_type, space_type, location=None, system=None,
           demands=None, return_trace=False) -> dict

输出符合 IC-10-Response Schema v1.9.0，包含：
- 适用标准集（条目含 时间状态 与可选 状态注记）
- 推理路径
- 证据对象（至少一条；依据未确认者降级，单纯复查到期仅在 applicability 证据中提醒）
- 未覆盖领域（可选）
- 外部协同（可选；断点5 新增，外协占位结构化对外可见）
- 决策轨迹（return_trace=True 时）
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# M1 编号前缀分类协议（standards-reasoning-rules.md §1.2 / §1.4）
# ---------------------------------------------------------------------------

PREFIX_PATTERNS: list[tuple[str, str, str, str, str]] = [
    # (正则, 标准类型, 默认层级, 默认权限, 地域范围)
    (r"^GB\s+55\d{3}", "全文强制性国标", "L1", "red_line", "全国"),
    (r"^GB\s+50\d{3}", "含强制性条文的国标", "L1", "red_line", "全国"),
    (r"^GB\s+5\d{4}", "含强制性条文的国标", "L1", "red_line", "全国"),
    (r"^GB/T", "推荐性国标", "L2", "binding_support", "全国"),
    (r"^JG\b", "行业标准（建设）", "L2", "binding_support", "全国"),
    (r"^JGJ\b", "行业标准（建设）", "L2", "binding_support", "全国"),
    (r"^JG/T", "推荐性行业标准（建设）", "L2", "binding_support", "全国"),
    (r"^JGJ/T", "推荐性行业标准（建设）", "L2", "binding_support", "全国"),
    (r"^JC\b", "行业标准（建材）", "L2", "binding_support", "全国"),
    (r"^JC/T", "推荐性行业标准（建材）", "L2", "binding_support", "全国"),
    (r"^HG\b", "行业标准（化工）", "L2", "binding_support", "全国"),
    (r"^HG/T", "推荐性行业标准（化工）", "L2", "binding_support", "全国"),
    # 断点7（CG-20260917-002）：层级真值源是 rules.md §1.2，定 DB/DBJ 地方标准=L2
    # （binding_support 设计/验收）。此前代码给 L3 与 rules.md 实质分歧（报告§二「机制层
    # 唯一实质分歧」）。DBJ 在 §1.2 为「L2 或 L4」——仅 reference 型产品应用 DBJ 才是 L4，
    # 当前数据面无此类，静态元组取 binding_support 对应的 L2。图集仍为 L3（见下）。
    (r"^DB\d{2}/", "地方标准", "L2", "binding_support", "对应省份"),
    # 「DBJ」后紧跟省码数字（DBJ33/T 1327-2024）：`\b` 在 J 与数字间不成立，旧 `^DBJ\b`
    # 会漏过该形态致其落 unknown_type（遗留2，CG-20260917-003）；`(?=\d|\b)` 同时兼容
    # DBJ/T 15（J↔/ 边界）与 DBJ33/T（J↔数字），仍拒 DBJX 一类非法形态。
    (r"^DBJ(?=\d|\b)", "地方建设标准", "L2", "binding_support", "对应省份"),
    # 团体标准 T/=L2 而非 L4：层级真值源 rules.md §1.2（团标定 L2），M2 锚定 A-L2-04
    # (T/CSUS 40-2022) 亦列 L2，IC-10 的 L4 释义专指企业标准及 DBJ 产品应用型、不含团标。
    # 权限 reference 不变（GS-1 团标不优先于 GB/T）；此前代码 L4 与判据分歧（遗留1，CG-20260917-003）。
    (r"^T/", "团体标准", "L2", "reference", "全国（管辖地采纳后适用）"),
    (r"^\d+CJ", "图集", "L3", "reference", "全国或区域"),
    # 「J」后紧跟数字：`\b` 在 J 与数字之间不成立，08J931 一类图集号会漏过本行
    (r"^\d+J(?=\d)", "图集", "L3", "reference", "全国或区域"),
    (r"^\d+ZJ", "图集", "L3", "reference", "全国或区域"),
    (r"^RISN-TG", "技术导则", "L2", "reference", "全国"),
]

PROVINCE_MAP: dict[str, str] = {
    "11": "北京", "31": "上海", "44": "广东",
    "33": "浙江", "35": "福建", "46": "海南",
    "32": "江苏", "61": "陕西", "41": "河南",
}

ACOUSTIC_SUBDOMAINS = {"acoustic", "acoustic(impact)", "acoustic(贡献)", "acoustic(吸声)", "acoustic(边界提示)"}


# ---------------------------------------------------------------------------
# M2 锚定集（standards-reasoning-rules.md §2.3）
# ---------------------------------------------------------------------------

ANCHORS: set[str] = {
    "GB 55031-2022", "GB 55037-2022", "GB 55038-2025", "GB 50210-2018", "GB 50118-2010",
    "GB/T 50121-2005", "JGJ/T 491-2021", "DB11/T 1553-2025", "T/CSUS 40-2022",
    "07CJ03-1", "08J931",
    "GB/T 23451-2023", "GB 8624-2012",
}


# ---------------------------------------------------------------------------
# M3 场景→领域激活表（standards-reasoning-rules.md §3.2 Step 1）
# 键：(项目类型, 空间类型)；值：候选域集列表
# ---------------------------------------------------------------------------

ACTIVATION_TABLE: dict[tuple[str, str], list[str]] = {
    ("住宅", "分户墙"): ["acoustic", "fire", "acceptance", "prefab"],
    ("住宅", "户内隔墙"): ["acoustic", "fire", "acceptance"],
    ("酒店", "客房隔墙"): ["acoustic", "fire", "acceptance", "prefab"],
    ("医院", "病房"): ["acoustic", "fire", "acceptance", "environmental"],
    ("医院", "诊疗室"): ["acoustic", "fire", "acceptance", "environmental"],
    ("医院", "病房/诊疗室"): ["acoustic", "fire", "acceptance", "environmental"],
    ("学校", "教室"): ["acoustic", "fire", "acceptance"],
    ("学校", "实验室"): ["acoustic", "fire", "acceptance"],
    ("学校", "教室/实验室"): ["acoustic", "fire", "acceptance"],
    ("办公", "办公室"): ["acoustic", "fire", "acceptance"],
    ("办公", "会议室"): ["acoustic", "fire", "acceptance"],
    ("办公", "办公室/会议室"): ["acoustic", "fire", "acceptance"],
    ("商业", "商铺"): ["fire", "acceptance"],
    ("商业", "展厅"): ["fire", "acceptance"],
    ("商业", "商铺/展厅"): ["fire", "acceptance"],
    ("医院", "病房/手术部墙面"): ["fire", "environmental", "acceptance", "prefab"],
    ("酒店", "客房墙面"): ["acceptance", "prefab", "acoustic(边界提示)"],
    ("住宅", "户内墙面"): ["prefab(认定)", "acceptance", "environmental"],
    ("学校", "教室墙面"): ["fire", "environmental", "acceptance"],
    ("办公", "办公墙面"): ["acceptance", "environmental", "prefab"],
    # 断点6（CG-20260917-002）：厨卫湿区 是空间类型（与 SPACE_ALIASES 卫生间/厨房→厨卫湿区
    # 一致），非项目类型。原 ("厨卫湿区","墙板") 把湿区放项目槽，IC-10 项目类型枚举不含它、
    # 按契约调用永不命中，且别名路径（空间槽）此前是死的。改由空间类型维度承载后，
    # ("住宅","卫生间")→别名→("住宅","厨卫湿区") 命中本行。空间类型系自由文本，无需扩枚举。
    ("住宅", "厨卫湿区"): ["prefab", "waterproof(外部协同)", "acceptance"],
    ("住宅", "分户楼板"): ["acoustic(impact)", "fire", "acceptance", "prefab"],
    ("住宅", "楼地面"): ["acoustic(impact)", "acceptance", "prefab"],
    ("酒店", "客房地面"): ["acoustic(impact)", "acceptance", "prefab"],
    ("办公", "办公地面"): ["acceptance", "prefab"],
    ("医院", "病房地面"): ["acoustic(impact)", "acceptance", "environmental"],
    ("学校", "教室地面"): ["acoustic(impact)", "acceptance"],
    ("医院", "手术部/洁净吊顶"): ["fire", "environmental", "acceptance", "prefab"],
    ("医院", "病房吊顶"): ["fire", "acceptance", "prefab"],
    ("酒店", "客房吊顶"): ["acoustic(贡献)", "fire", "acceptance", "prefab"],
    ("住宅", "厨卫集成吊顶"): ["acceptance", "prefab", "environmental", "waterproof(边界)"],
    ("住宅", "户内吊顶"): ["acceptance", "prefab"],
    ("学校", "教室吊顶"): ["acoustic(吸声)", "fire", "acceptance"],
    ("办公", "办公吊顶"): ["acoustic(吸声)", "fire", "acceptance"],
}

# 空间类型归一化别名
SPACE_ALIASES: dict[str, str] = {
    "卫生间": "厨卫湿区",
    "厨房": "厨卫湿区",
}

# 项目类型类比归一表（CG-20261012-006，台账 PL-198 ② 落地）
# 键＝契约「项目类型」取值域内、§3.2 激活表无专用行的建筑类型；值＝(类比目标档, 对外标注逐字)。
# 判据载体单源＝standards-reasoning-rules.md §3.2 Step 1「项目类型类比归一」注；该注所引标注
# 与本常量字面由 sre_regression_test 的类比守卫逐字对撞，任一侧改而另一侧未改即复红（防成第二事实源）。
# 机制形制沿上方 SPACE_ALIASES（键解析取序＝精确 → 空间别名 → 项目类比 → 两轴同归一，
# 类比支恒居后 ⇒ 日后本表若新增公寓专用行，专用行自动优先，无须回改本函数）。
# 判断先例沿台账 PL-089（类比取值可以，但输出必须显式标注、不得写成 S1 逐字引用）——
# **两案不同因，措辞不得照抄**：PL-089 系官方限值缺位，本案系公寓的适用关系未经本合集索引
# 核验取得，故标注如实写「非已确证适用」而非「非专用官方限值」。
PROJECT_TYPE_ANALOGIES: dict[str, tuple[str, str]] = {
    "公寓": (
        "住宅",
        "项目类型「公寓」的适用面未在本合集标准索引取得记载，本条按住宅档类比参考、"
        "非已确证适用；须先确认本项目建筑定性（住宅定性者 GB 55038-2025 即真值源；"
        "商办／旅馆式公寓另按 GB 50118-2010 旅馆档复核），确认前不得把本条读作「经官方核验适用于公寓」",
    ),
}


def _resolve_project_type(project_type: str) -> str:
    """项目类型类比归一：表内者取其类比档，其余原样返回（M3 Step 2 取族同口径用）。"""
    entry = PROJECT_TYPE_ANALOGIES.get(project_type)
    return entry[0] if entry else project_type


# ---------------------------------------------------------------------------
# M3 Step 2 领域→标准族映射（简化核心族）
# ---------------------------------------------------------------------------

DOMAINS: dict[str, Any] = {
    "acoustic": {
        "住宅": ["GB 55038-2025", "GB 50118-2010", "GB/T 50121-2005"],
        "默认": ["GB 50118-2010", "GB/T 50121-2005"],
        "轻钢龙骨": ["GB/T 19889.1-2026", "JG/T 544-2018", "07CJ03-1", "08J931"],
        "条板": ["GB/T 23451-2023"],
        "吊顶": ["GB/T 11981-2024", "GB/T 9775-2025", "JC/T 564.1-2018", "GB/T 25998-2020", "07CJ03-1", "08J931"],
    },
    "acoustic(impact)": {
        "住宅": ["GB 55038-2025", "GB 50118-2010", "GB/T 50121-2005"],
        "默认": ["GB 50118-2010", "GB/T 50121-2005"],
        "浮筑地面": ["GB/T 19889.7-2022", "GB/T 19889.8-2006", "GB/T 45305.3-2026", "08J931"],
        "架空地面": ["08J931"],
    },
    # 以下三键补 T-B3 死链域（CG-20260916-005）。每条编号均取自仓内既有出处，零新造：
    "acoustic(贡献)": {
        # 吊顶对空气声隔声为贡献量而非独立判据，达标归属宿主构件标准
        # ——rules.md:248 注记 + 吊顶技能 reference.md:507「acoustic(贡献) → IC-09 估算 + 宿主构件标准归属」
        "住宅": ["GB 55038-2025"],
        "默认": ["GB 50118-2010"],
        # 面板/龙骨产品标准与图集，同 rules.md:248 构造=吊顶行的六项列举（与 acoustic/吊顶 同族）
        "吊顶": ["GB/T 11981-2024", "GB/T 9775-2025", "JC/T 564.1-2018",
                "GB/T 25998-2020", "07CJ03-1", "08J931"],
    },
    "acoustic(吸声)": {
        # 吸声为主角场景（教室/办公吊顶）→ GB 50118 非住宅条文（混响/允许噪声级），
        # 见吊顶技能 reference.md:507 与 examples.md:230；GB/T 25998-2020 为吸声板产品标准，
        # 08J931 图集名《建筑隔声与吸声构造》且系 rules.md:150 锚定 A-L3-02 三技能共同引用
        "默认": ["GB 50118-2010", "GB/T 25998-2020", "08J931"],
    },
    "acoustic(边界提示)": {
        # rules.md:227 定其为「提示性标注」，故不承载判据编号；墙体整体隔声/耐火认定属隔墙技能
        # 职能（红线 WS-R-P0-2），见墙面 combined_reference_v2.md:271。占位串形态沿 waterproof(边界)
        "默认": ["（外部 prefab-partition-wall-solution 技能墙体整体隔声/耐火认定协同）"],
    },
    "fire": {
        "全部": ["GB 55037-2022", "GB 50016-2014", "GB 50222-2017"],
        "板材": ["GB 8624-2012", "07CJ03-1"],
        "吊顶": ["GB/T 11981-2024", "GB/T 9775-2025", "JC/T 564.1-2018", "GB/T 25998-2020", "07CJ03-1"],
    },
    "acceptance": {
        "全部": ["GB 50210-2018", "GB 55032-2022", "07CJ03-1"],
        "浙江": ["DB33/T 1168-2019"],
        "北京": ["DB11/T 1553-2025"],
        "广东": ["DBJ/T 15-208-2020"],
        "吊顶": ["GB/T 11981-2024", "GB/T 9775-2025", "JC/T 564.1-2018", "GB/T 25998-2020", "07CJ03-1"],
    },
    "prefab": {
        "全国": ["GB/T 51129-2017", "JGJ/T 491-2021", "RISN-TG 055-2025"],
        "浙江": ["DB33/T 1259-2021"],
        "北京": ["DB11/T 1553-2025"],
        "福建": ["DBJ/T 13-428-2023"],
        "深圳": ["SJG 159-2024"],
        "墙面饰面系统": ["T/CECS 1018-2022", "JG/T 579-2021", "JG/T 578-2021"],
    },
    "environmental": {
        "医院/学校": ["GB 18580-2025", "GB 30981.1-2025", "T/CSUS 03-2019"],
        "默认": ["GB 18580-2025"],
        "吊顶板材": ["GB 18580-2025", "GB 6566-2010"],
    },
    "waterproof(外部协同)": {
        "默认": ["（防水专项协同）"],
    },
    "waterproof(边界)": {
        "默认": ["（防水边界咨询）"],
    },
    "prefab(认定)": {
        "默认": ["GB/T 51129-2017", "JGJ/T 491-2021"],
    },
}


# 断点5（CG-20260917-002）：外协技能状态话术。waterproofing-expert 已于 2026-08-14
# 迁出至独立项目「建筑专业辅材技能合集」（见 interface-contracts.md 头部既有记录），
# 本运行时不可直接调用。原「待建」话术与该记录矛盾，按证据取「已迁出」。
EXTERNAL_COLLAB_SKILL = "waterproofing-expert"
EXTERNAL_COLLAB_STATUS = (
    "已迁出至独立项目「建筑专业辅材技能合集」，本运行时不可直接调用，需跨项目协同"
)


# ---------------------------------------------------------------------------
# 标准元数据缓存（优先从 standards-index.md 加载，失败时 fallback）
# ---------------------------------------------------------------------------

STANDARD_NAMES: dict[str, str] = {
    "GB 55031-2022": "民用建筑通用规范",
    "GB 55037-2022": "建筑防火通用规范",
    "GB 55038-2025": "住宅项目规范",
    "GB 50210-2018": "建筑装饰装修工程质量验收标准",
    "GB 50118-2010": "民用建筑隔声设计规范",
    "GB 50016-2014": "建筑设计防火规范（2018 年版）",
    "GB 50222-2017": "建筑内部装修设计防火规范",
    "GB 55032-2022": "建筑与市政工程施工质量控制通用规范",
    "GB/T 50121-2005": "建筑隔声评价标准",
    # 值须与索引主表序号 14 逐字一致；键须带年份，否则与 DOMAINS 引用失配并静默落兜底（CG-20260916-001）
    "GB/T 19889.1-2026": "声学 建筑和建筑构件隔声的现场测量 第1部分：房间之间空气声隔声",
    "GB/T 19889.7-2022": "声学 建筑和建筑构件隔声测量 第7部分：撞击声隔声的现场测量",
    "GB/T 19889.8-2006": "声学 建筑和建筑构件隔声测量 第8部分：重质标准楼板覆面层撞击声改善量的实验室测量",
    "GB/T 45305.3-2026": "声学 建筑构件隔声的实验室测量 第3部分：撞击声隔声测量",
    "GB/T 23451-2023": "建筑用轻质隔墙条板",
    "GB 8624-2012": "建筑材料及制品燃烧性能分级",
    "JGJ/T 491-2021": "装配式内装修技术标准",
    "JG/T 544-2018": "轻钢龙骨式复合墙体",
    "GB/T 11981-2024": "建筑用轻钢龙骨",
    "GB/T 9775-2025": "纸面石膏板",
    "JC/T 564.1-2018": "纤维增强硅酸钙板 第1部分：无石棉硅酸钙板",
    "GB/T 25998-2020": "矿物棉装饰吸声板",
    "GB/T 51129-2017": "装配式建筑评价标准",
    "RISN-TG 055-2025": "装配式装修技术导则",
    "DB11/T 1553-2025": "建筑室内装配式装修技术规程",
    "DB33/T 1168-2019": "装配式内装工程施工质量验收规范（浙江）",
    "DBJ/T 15-208-2020": "建筑室内装配式轻质隔墙技术规程",
    "DB33/T 1259-2021": "装配式内装评价标准",
    "DBJ/T 13-428-2023": "福建省装配式内装修评价标准",
    "SJG 159-2024": "装配式装修评价标准",
    "T/CECS 1018-2022": "装配式室内墙面系统应用技术规程",
    "JG/T 579-2021": "建筑装配式集成墙面",
    "JG/T 578-2021": "装配式建筑用墙板技术要求",
    "GB 18580-2025": "室内装饰装修材料 人造板及其制品中甲醛释放限量",
    "GB 30981.1-2025": "涂料中有害物质限量 第1部分：建筑涂料",
    "GB 6566-2010": "建筑材料放射性核素限量",
    "T/CSUS 03-2019": "医疗建筑集成化装配式内装修技术标准",
    "T/CSUS 40-2022": "装配式建筑室内声环境技术标准",
    "07CJ03-1": "轻钢龙骨石膏板隔墙、吊顶",
    "08J931": "建筑隔声与吸声构造",
}

# 修复前降级标注数据源（CG-20260915-002，T-A4①）。
# change-governance.md §4.5 明文：锚定标准废止/替代 → S 级，且"修复前 SRE 对该领域
# 的推理结果须降级标注"。GB 50118-2010 为锚定 A-L1-05，其 DOMAINS 命中路径共 6 个
# 映射键（acoustic/住宅、acoustic/默认、acoustic(impact)/住宅、acoustic(impact)/默认、
# acoustic(贡献)/默认、acoustic(吸声)/默认；后两条随 CG-20260916-005 补录死链域时增加），
# 该标准不整体失效，故以"编号 → 替代警告"而非剔除编号的方式承载。
# 复算配方：`[f"{d}/{k}" for d, v in DOMAINS.items() for k, ids in v.items()
# if "GB 50118-2010" in ids]` —— 任何向 DOMAINS 增删该编号的改动都须回扫上一行计数。
PARTIAL_REPLACEMENT_NOTICES: dict[str, str] = {
    "GB 50118-2010": (
        "被部分替代：住宅隔声第 4.2.1、4.2.2、4.2.5 条已由 GB 55038-2025 接管"
        "（住建部公告 2025 年第 39 号），引用这三条须切换至 GB 55038-2025；"
        "公共建筑隔声及未替代条文仍有效。本域结论为替代范围闭合前的降级输出，"
        "须经条文级证据复核（TA-2）。"
    ),
}

STANDARD_STATUS: dict[str, str] = {}

# 编号→适用地区真值，由 _load_standards_index() 从索引 §5.3「适用地区」列现读填充。
# DBJ 系序号（DBJ/T 15=广东、DBJ/T 13=福建）是地方建设标准序号、非 GB 行政区划码，
# PROVINCE_MAP 与 `DB\s*(\d{2})` 正则均无法解析（断点3），故地方标准省份以索引现读为准。
STANDARD_PROVINCE: dict[str, str] = {}

# 索引 §二 小节标题「## 二、第一层级：强制性国标（红线标准）」的层级标记词
_INDEX_L1_HEADING = "第一层级"
# 该小节主表收录的编号集合——前缀不可识别的强制性国标（GB 18580 等）的层级真值源，
# 由 _load_standards_index() 现读填充，代码内不维护编号清单副本。
INDEX_L1_IDS: set[str] = set()

# IC-10 v1.9.0 `时间状态` 六值中的显式降级值：索引无该编号记录时的唯一输出（T-B2）。
# 本文件只承载这一个降级值，不复制整个枚举集——合法集由回归测试/门禁从
# interface-contracts.md 与 standards-index.md §1.1 现读校验，代码内不留第二份。
STATUS_UNKNOWN = "未知"

_ID_COLS = ("标准编号", "图集编号")
_NAME_COLS = ("标准名称", "图集名称")
# 核验记录表的列位与主表不同（其第 7 列是"影响条文说明"之类的自由文本），
# 一旦按列号放行就会把说明整句写成标准状态（T-B4）。
_VERIFY_HEADER_KEYS = ("核验日期", "核验状态", "核验结论")

# §1.1A 的维护周期派生天数，只生成复查提醒，不裁定标准效力。
REVIEW_DAYS_L1 = 92
REVIEW_DAYS_OTHER = 183
CONFIRMED_VERIFY_PREFIX = "已官方核验"
# 编号→{核验日期, 核验状态}，由 _load_standards_index() 从核验记录/分层表按列名现读填充，
# 代码内不维护编号清单副本，也不维护任何核验日期的硬编码值。
STANDARD_VERIFY: dict[str, dict[str, str | None]] = {}
# 核验状态分层表末行是「其余强制性国标」这类散文兜底单元、§三 抽查表末行是「多项推荐性/…（除…者除外）」，
# 二者都不是编号却会被误当键载入（越枚举同源的口径污染）。以 ASCII 起首且含数字为编号形态判据；
# 并列单元（`GB 50016-2014／GB 50210-2018／…`）只按全角／切，半角 / 属编号本体（GB/T、T/CECS）。
_VERIFY_ID_SHAPE = re.compile(r"^[0-9A-Za-z].*\d")


def _iter_md_tables(text: str):
    """表头感知地切出 Markdown 表：表头 = 其后紧跟分隔行的那一行。"""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("|") and i + 1 < len(lines) \
                and not set(lines[i + 1].replace("|", "").strip()) - set("-: "):
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            yield header, rows
            i = j
        else:
            i += 1


def _classify_status_table(header: list[str]) -> str | None:
    """main = 索引 §二～§六 主表；register = §7.2 废止/替代登记表；None = 不参与状态载入。"""
    joined = "|".join(header)
    if any(k in joined for k in _VERIFY_HEADER_KEYS) or "锚定ID" in joined:
        return None
    if "状态" not in header:
        return None
    if not any(c in header for c in _ID_COLS):
        return None
    return "main" if header[0] == "序号" else "register"


def _classify_verify_table(header: list[str]) -> bool:
    """核验记录表（§二 官方核验记录、§三 抽查核验记录、§5.3 产品标准核验记录）
    与核验状态分层表：只供核验时效台账现读，**绝不参与状态载入**。

    与 _classify_status_table 互斥是刻意的：核验记录表的自由文本列（「核验结论」
    「影响条文说明」）一旦按固定列号被当状态读入，就会把整句说明写成标准状态（T-B4/T-B7）。
    这里只按列名取 `核验日期`／`核验状态` 两列，不取其余任何列。
    """
    if not any(c in header for c in _ID_COLS):
        return False
    return "核验日期" in header or "核验状态" in header


def _load_standards_index() -> None:
    """从 ../shared/standards-index.md 按表头列名加载标准状态、§二 L1 成员集与核验时效台账。

    主表为状态真值源，§7.2 登记表只补主表未收录的编号（旧版标准在主表单列外），
    核验记录表**不参与状态载入**——三者共用固定列号是 T-B4/T-B7 的根因；
    核验记录表只按列名供 `STANDARD_VERIFY`（核验日期／核验状态）现读，供 M4 时效判定。
    名称取数优先序＝主表 > 登记表 > 模块级硬编码预置（预置只兜两表均未收录的编号）。
    """
    text = None
    for path in [Path(__file__).parent.parent / "shared" / "standards-index.md",
                 Path(__file__).parent.parent / "standards-index.md"]:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            break
    if text is None:
        return

    buckets: dict[str, dict[str, str]] = {"main": {}, "register": {}}
    name_buckets: dict[str, dict[str, str]] = {"main": {}, "register": {}}
    INDEX_L1_IDS.clear()
    STANDARD_PROVINCE.clear()
    STANDARD_VERIFY.clear()
    # 层级由小节标题承载（「## 二、第一层级：强制性国标（红线标准）」），故按 ## 切块
    parts = re.split(r"(?m)^(##.*)$", text)
    sections = [("", parts[0])] + [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    for heading, chunk in sections:
        in_l1_section = _INDEX_L1_HEADING in heading
        for header, rows in _iter_md_tables(chunk):
            if _classify_verify_table(header):
                i_vid = next(header.index(c) for c in _ID_COLS if c in header)
                i_vdate = header.index("核验日期") if "核验日期" in header else None
                i_vstate = header.index("核验状态") if "核验状态" in header else None
                for row in rows:
                    if i_vid >= len(row) or not row[i_vid]:
                        continue
                    for one in row[i_vid].split("／"):
                        std_no = one.strip().replace("**", "").strip()
                        if not _VERIFY_ID_SHAPE.match(std_no):
                            continue
                        rec = STANDARD_VERIFY.setdefault(
                            std_no, {"核验日期": None, "核验状态": None})
                        if i_vdate is not None and i_vdate < len(row):
                            m = re.search(r"\d{4}-\d{2}-\d{2}", row[i_vdate])
                            if m and (rec["核验日期"] is None
                                      or m.group(0) > rec["核验日期"]):
                                rec["核验日期"] = m.group(0)
                        if (i_vstate is not None and i_vstate < len(row)
                                and row[i_vstate] and rec["核验状态"] is None):
                            rec["核验状态"] = row[i_vstate].strip()
                continue
            kind = _classify_status_table(header)
            if kind is None:
                continue
            i_id = next(header.index(c) for c in _ID_COLS if c in header)
            i_name = next((header.index(c) for c in _NAME_COLS if c in header), None)
            i_status = header.index("状态")
            i_region = header.index("适用地区") if "适用地区" in header else None
            for row in rows:
                if i_id >= len(row) or not row[i_id]:
                    continue
                std_no = row[i_id]
                if kind == "main" and in_l1_section:
                    INDEX_L1_IDS.add(std_no)
                if i_status < len(row) and row[i_status]:
                    buckets[kind].setdefault(std_no, row[i_status])
                if i_name is not None and i_name < len(row) and row[i_name]:
                    name_buckets[kind].setdefault(std_no, row[i_name])
                if i_region is not None and i_region < len(row) and row[i_region]:
                    STANDARD_PROVINCE.setdefault(std_no, row[i_region])

    STANDARD_STATUS.clear()
    STANDARD_STATUS.update(buckets["main"])
    for std_no, status in buckets["register"].items():
        STANDARD_STATUS.setdefault(std_no, status)

    # 名称以索引为 SOT：主表 > 登记表 > 硬编码预置，预置仅作两表均未收录编号的兜底。
    # 旧 setdefault 支路会让预置遮蔽索引改名，索引修订后运行时静默沿用旧名（PL-002）；
    # 登记表同走 update 是为不在「仅登记表收录」的键上留下同族遮蔽面。
    STANDARD_NAMES.update(name_buckets["register"])
    STANDARD_NAMES.update(name_buckets["main"])


def _standard_name(std_no: str) -> str:
    return STANDARD_NAMES.get(std_no, std_no)


def _standard_status(std_no: str) -> str:
    """索引登记的原始状态单元；未登记编号返回 STATUS_UNKNOWN（T-B2）。

    兜底不得给确定性状态：SR-R-P0-2 禁止对无依据的输入输出「现行有效」。
    """
    return STANDARD_STATUS.get(std_no, STATUS_UNKNOWN)


def _split_status(status: str) -> tuple[str, str]:
    """按 IC-10 v1.9.0 口径把状态单元拆为（时间状态, 状态注记）。

    索引 §1.1 的状态列可自带全角括注（如「现行有效（代替 GB 18580-2017）」）。以首个
    全角「（」为界切分，前段为契约主态、后段去尾「）」为注记，故为无损拆分——
    「已废止（无替代）」的限定条件由注记位承载，不因截断丢失。本函数不校验主态是否
    属枚举：枚举合法集的判据在回归测试与门禁侧现读契约，代码内不留第二份枚举副本。
    """
    head, sep, tail = status.partition("（")
    if not sep:
        return status, ""
    return head, tail.rstrip("）")


def _verification_confirmed(std_no: str, level: str) -> tuple[bool, str]:
    """返回既有核验记录可否复用，以及未确认事由或复查提醒。"""
    rec = STANDARD_VERIFY.get(std_no)
    tier = REVIEW_DAYS_L1 if level == "L1" else REVIEW_DAYS_OTHER
    if rec is None or not rec.get("核验日期"):
        return False, "索引无「核验日期」记录，既有核验依据未确认（不等于标准已失效）"
    state = rec.get("核验状态")
    if state and not state.startswith(CONFIRMED_VERIFY_PREFIX) and state != "到期需复核":
        return False, f"核验状态＝{state}，既有核验依据未确认（条文级核验不构成全标准状态确认）"
    date_str = rec["核验日期"]
    try:
        verified_at = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return False, f"核验日期 {date_str} 不可解析"
    age = (datetime.now() - verified_at).days
    if age < 0:
        return False, f"核验日期 {date_str} 晚于当前日期，记录须核实"
    if age > tier or state == "到期需复核":
        return True, (
            f"复查提醒：核验日期 {date_str}，距今 {age} 天，维护周期 {tier} 天"
            f"（核验状态={state or '未单列'}）；保留既有核验结论，不代表本次已核实最新状态，"
            "不因复查到期判为失效；项目引用前仍须核对适用条文及废止、替代、修订或勘误依据"
        )
    return True, ""


# 导入期即载入：本模块既被 CLI 直跑，也被技能侧 import，两入口必须看到同一份状态（T-B1）。
_load_standards_index()


# ---------------------------------------------------------------------------
# M1 分类
# ---------------------------------------------------------------------------

def classify_standard(std_no: str, evidence_list: list[dict]) -> dict[str, Any]:
    """对单个标准编号执行 M1 五步分类。"""
    std_no_norm = std_no.strip()
    matched = None
    for pat, std_type, level, authority, scope in PREFIX_PATTERNS:
        if re.match(pat, std_no_norm):
            matched = (std_type, level, authority, scope)
            break

    if matched is None and std_no_norm in INDEX_L1_IDS:
        # §1.2 前缀表只枚举 GB 5xxxx 段；无 /T 的四、五位强制性国标（GB 18580-2025 等）
        # 前缀不可识别，但索引 §二「第一层级：强制性国标（红线标准）」持有其层级真值。
        # 走 §1.4 降级会把红线标准降为参考级，故按 §1.2 的 L1↔red_line 耦合确定性分类。
        matched = ("强制性国标（索引 §二 收录）", "L1", "red_line", "全国")
        evidence_list.append(_evidence(
            "classification", "standards-index.md §二 / standards-reasoning-rules.md §1.4",
            f"标准编号={std_no_norm}",
            "前缀不在 §1.2 表内、索引 §二 收录 → 层级=L1, 权限=red_line",
            "deterministic"
        ))

    if matched is None:
        evidence_list.append(_evidence(
            "degradation", "standards-reasoning-rules.md §1.4 / §五 M6",
            f"标准编号={std_no_norm}",
            f"前缀不可识别，降级为 unknown_type / reference / inferred",
            "inferred"
        ))
        return {
            "标准编号": std_no_norm,
            "标准类型": "unknown_type",
            "层级": "L4",
            "权限": "reference",
            "地域范围": "未知",
            "性能领域": [],
            "分类置信度": "inferred",
        }

    std_type, level, authority, scope = matched

    # 地域范围细化
    actual_scope = scope
    if std_no_norm.startswith(("DB", "DBJ")):
        # 断点3：优先取索引 §5.3「适用地区」现读值。DBJ 系序号（15=广东、13=福建）非 GB
        # 行政区划码，PROVINCE_MAP 与下方正则均无法解析，故地方标准省份以索引现读为准。
        if std_no_norm in STANDARD_PROVINCE:
            actual_scope = STANDARD_PROVINCE[std_no_norm]
        else:
            m = re.search(r"DB\s*(\d{2})", std_no_norm)
            if m:
                province_code = m.group(1)
                province = PROVINCE_MAP.get(province_code, province_code)
                actual_scope = province
            else:
                actual_scope = "待确认省份"

    # 性能领域推断（简化）
    domains: list[str] = []
    if any(k in std_no_norm for k in ["55037", "50016", "50222", "防火", "耐火", "燃烧"]):
        domains.append("fire")
    if any(k in std_no_norm for k in ["50118", "55038", "50121", "19889", "45305", "隔声", "声学"]):
        domains.append("acoustic")
    if any(k in std_no_norm for k in ["55031", "50210", "55032", "验收", "质量"]):
        domains.append("acceptance")
    if any(k in std_no_norm for k in ["491", "1553", "装配式", "装配化"]):
        domains.append("prefab")
    if any(k in std_no_norm for k in ["8624", "23451", "544", "产品"]):
        domains.append("product")
    if any(k in std_no_norm for k in ["18580", "30981", "6566", "室内", "空气", "甲醛"]):
        domains.append("environmental")

    evidence_list.append(_evidence(
        "classification", "standards-reasoning-rules.md §1.2 / §1.4",
        f"标准编号={std_no_norm}",
        f"类型={std_type}, 层级={level}, 权限={authority}, 地域={actual_scope}, 领域={domains}",
        "deterministic" if std_type != "unknown_type" else "inferred"
    ))

    return {
        "标准编号": std_no_norm,
        "标准类型": std_type,
        "层级": level,
        "权限": authority,
        "地域范围": actual_scope,
        "性能领域": domains,
        "分类置信度": "deterministic",
    }


# ---------------------------------------------------------------------------
# M3 场景推理
# ---------------------------------------------------------------------------

def _activate_domains(
    project_type: str,
    space_type: str,
    demands: list[str] | None,
    evidence_list: list[dict],
    uncovered: list[str] | None = None,
) -> tuple[set[str], str]:
    """M3 Step 1 / Step 1a：返回激活域集与裁定说明。"""
    # 键解析取序（CG-20261012-006 起四支）＝精确 → 空间别名 → 项目类比 → 两轴同归一。
    # 类比支恒排在精确与空间别名之后：本表若日后新增公寓专用行，专用行自动优先于类比，
    # 本函数无须回改（沿 SPACE_ALIASES 的同一排布理由）。
    analog = PROJECT_TYPE_ANALOGIES.get(project_type)
    analog_space = SPACE_ALIASES.get(space_type)
    tried: list[tuple[str, str]] = [(project_type, space_type)]
    if analog_space:
        tried.append((project_type, analog_space))
    if analog:
        tried.append((analog[0], space_type))
        if analog_space:
            tried.append((analog[0], analog_space))
    key = tried[0]
    candidates = None
    for k in tried:
        if k in ACTIVATION_TABLE:
            key, candidates = k, ACTIVATION_TABLE[k]
            break

    if candidates is None:
        # 任意 | 任意 最小集
        candidates = ["fire", "acceptance"]
        evidence_list.append(_evidence(
            "activation", "standards-reasoning-rules.md §3.2 Step 1（最小集）",
            f"项目类型={project_type}, 空间类型={space_type}",
            "未命中场景表，使用最小集 fire + acceptance",
            "inferred"
        ))
        # 断点4：证据对象里的 inferred 属内部痕迹，使用者只读「未覆盖领域」。
        # 场景未命中须同时把降级提示落到用户可见输出，对齐 M6「建议可见」（rules.md §五）。
        if uncovered is not None:
            uncovered.append(
                f"场景未命中激活表（项目类型={project_type}, 空间类型={space_type}），"
                f"已降级为最小集 fire+acceptance；建议确认场景表述或转专项技能咨询"
            )
    else:
        # 类比档命中（CG-20261012-006）：置信度不得留 deterministic。理由＝标注若只落
        # 「未覆盖领域」而证据仍判确定，下游只抄「适用标准集」即把未经核验的适用关系读成
        # 已确证（独立复核 ④ 的一手判据）。故 activation 证据同批降 inferred ＋追加一条
        # degradation 证据，复用既有降级语义、不给 IC-10 item 加字段（items 为
        # additionalProperties: false，加字段即契约不兼容变更）。
        by_analogy = analog is not None and key[0] == analog[0]
        evidence_list.append(_evidence(
            "activation",
            "standards-reasoning-rules.md §3.2 Step 1"
            + ("（项目类型类比归一）" if by_analogy else ""),
            f"项目类型={project_type}, 空间类型={space_type}",
            f"命中场景行，候选域={candidates}",
            "inferred" if by_analogy else "deterministic"
        ))
        if by_analogy and uncovered is not None:
            evidence_list.append(_evidence(
                "degradation",
                "standards-reasoning-rules.md §3.2 Step 1（项目类型类比归一）/ §五 M6",
                f"项目类型={project_type} 在本表无专用行，按 {analog[0]} 档类比命中",
                analog[1],
                "inferred",
            ))
            # 沿断点4 口径：证据对象里的 inferred 属内部痕迹，使用者只读「未覆盖领域」，
            # 故类比标注必须同时落到该可见面，不得只留在证据链内。
            uncovered.append(analog[1])

    # 最小集始终激活
    activated = set(candidates) | {"fire", "acceptance"}

    # Step 1a 性能需求裁定（仅 acoustic 子域族）
    arbitration_note = ""
    if demands:
        acoustic_demands = {"隔声", "撞击声", "吸声"}
        has_acoustic = bool(set(demands) & acoustic_demands)
        if has_acoustic:
            # 增补对应子域
            if "楼板" in space_type or "地面" in space_type or "分户楼板" in space_type:
                activated.add("acoustic(impact)")
                arbitration_note = "性能需求含声学类，增补 acoustic(impact)"
            elif "吊顶" in space_type:
                activated.add("acoustic(贡献)")
                arbitration_note = "性能需求含声学类，增补 acoustic(贡献)"
            elif "教室" in space_type or "办公" in space_type:
                activated.add("acoustic(吸声)")
                arbitration_note = "性能需求含声学类，增补 acoustic(吸声)"
            else:
                activated.add("acoustic")
                arbitration_note = "性能需求含声学类，增补 acoustic"
        else:
            # 过滤候选集中的 acoustic 子域（边界提示除外）
            filtered = {d for d in activated if d not in ACOUSTIC_SUBDOMAINS or d == "acoustic(边界提示)"}
            removed = activated - filtered
            activated = filtered
            if removed:
                arbitration_note = f"性能需求不含声学类，过滤 {sorted(removed)}"

        if arbitration_note:
            evidence_list.append(_evidence(
                "arbitration", "standards-reasoning-rules.md §3.2 Step 1a",
                f"性能需求={demands}",
                arbitration_note,
                "deterministic"
            ))

    return activated, arbitration_note


def _map_domain_to_standards(
    domain: str,
    project_type: str,
    location: str | None,
    system: str | None,
    evidence_list: list[dict],
) -> list[str]:
    """M3 Step 2：将单个领域映射到标准族。"""
    mapping = DOMAINS.get(domain, {})
    standards: list[str] = []
    # 项目类型类比归一（CG-20261012-006）：取族用类比档，对外标注与证据的 输入事实 仍载原值
    ptype_key = _resolve_project_type(project_type)
    branch_via_analogy = False

    # 基础族：项目类型专属键须先于「默认」兜底命中。
    # 「医院/学校」若排在「默认」之后，environmental 域对医院/学校将永远先命中「默认」
    # （仅 GB 18580-2025），拿不到 rules.md §3.2 environmental 表规定的 GB 30981.1-2025
    # 与 T/CSUS 03-2019（断点1）。
    if ptype_key == "住宅" and "住宅" in mapping:
        standards.extend(mapping["住宅"])
        # 该支经类比归一取得（如 公寓→住宅）时，本域标准族属类比参考而非确证适用，
        # 证据置信度须与 Step 1 同批改判，不得一侧降一侧留（判据见 §3.2 类比归一注）。
        branch_via_analogy = ptype_key != project_type
    elif "全部" in mapping:
        standards.extend(mapping["全部"])
    elif "医院/学校" in mapping and project_type in ("医院", "学校"):
        standards.extend(mapping["医院/学校"])
    elif "默认" in mapping:
        standards.extend(mapping["默认"])

    # 全国基线可叠加：不得因走地点分支而被抑制（设计方案 §3.2 T-B6，CG-20260916-005）
    standards.extend(mapping.get("全国", []))

    # 地点附加
    if location and location in mapping:
        standards.extend(mapping[location])

    # 构造体系附加
    if system and system in mapping:
        standards.extend(mapping[system])

    # 特殊：墙面饰面系统
    if domain == "prefab" and system and "墙面" in system:
        standards.extend(mapping.get("墙面饰面系统", []))

    # 去重保序
    seen = set()
    unique: list[str] = []
    for s in standards:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    evidence_list.append(_evidence(
        "mapping", "standards-reasoning-rules.md §3.2 Step 2"
        + ("（项目类型类比归一）" if branch_via_analogy else ""),
        f"领域={domain}, 项目类型={project_type}, 所在地={location}, 构造体系={system}",
        f"标准族={unique}",
        "inferred" if (not unique or branch_via_analogy) else "deterministic"
    ))
    return unique


# ---------------------------------------------------------------------------
# M4 适用性裁判
# ---------------------------------------------------------------------------

def _apply_applicability(
    standards: list[dict],
    location: str | None,
    evidence_list: list[dict],
) -> list[dict]:
    """M4 LA/GS/TA/MD：分配角色、地域适用性、时间状态。"""
    result: list[dict] = []
    for std in standards:
        std_no = std["标准编号"]
        level = std["层级"]
        authority = std["权限"]

        # 地域适用性（LA-1 / LA-3）
        if std_no.startswith(("DB", "DBJ")):
            province = std.get("地域范围", "未知")
            if location and province in (location, location.rstrip("省市") + "省", location.rstrip("省市") + "市"):
               地域适用性 = "项目所在地适用"
            elif location:
                地域适用性 = "不适用仅作对比"
            else:
                地域适用性 = "待确认"
        elif std_no.startswith("T/"):
            地域适用性 = "全国（合同/地方采纳后适用）"
        else:
            地域适用性 = "全国"

        # 时间状态（TA 规则组）：主态原样输出，括注走 状态注记，两者不得互转也不得前缀截断
        时间状态, 状态注记 = _split_status(_standard_status(std_no))

        # 角色分配（Step 4）
        if level == "L1":
            role = "mandatory_check"
        elif level in ("L2", "L3") and authority == "binding_support":
            role = "design_basis"
        elif "测量" in _standard_name(std_no) or "评价" in _standard_name(std_no):
            role = "verification_reference"
        elif level == "L3" or "图集" in std["标准类型"]:
            role = "construction_guide"
        elif "评价" in std["标准类型"] or "51129" in std_no or "SJG" in std_no or "RISN" in std_no:
            # RISN-TG 055-2025 为 L2/reference，不匹配前四支而落 else 的 "reference"，
            # 该值不在 IC-10 `角色` 五值枚举内；随其 prefab 域同族语义归入（CG-20260916-005）
            role = "prefab_evaluation"
        else:
            # 兜底不得写 "reference"：那是 IC-10 `权限` 三值枚举的成员，不在 `角色` 五值内，
            # 串槽后既越契约、又落不进 Step 5 优先级表（_role_priority 返回 99）。
            # M1 降级支已把层级定为 L4，Step 4 对 L4 的分配条件即 construction_guide；
            # 分类不确定性由 M1 的 inferred 降级证据承载，不靠越枚举表达。
            role = "construction_guide"

        核验确认, 核验事由 = _verification_confirmed(std_no, level)

        evidence_list.append(_evidence(
            "applicability", "standards-reasoning-rules.md §四 M4 / §3.2 Step 4 / standards-index.md §1.1A",
            f"标准={std_no}, 层级={level}, 权限={authority}, 所在地={location}",
            f"地域适用性={地域适用性}, 时间状态={时间状态}, 角色={role}"
            + (f"；{核验事由}" if 核验确认 and 核验事由 else ""),
            "deterministic" if 地域适用性 != "待确认" and 时间状态 not in (STATUS_UNKNOWN, "已废止")
            and 核验确认 else "inferred"
        ))

        item = {
            "标准编号": std_no,
            "标准名称": _standard_name(std_no),
            "层级": level,
            "权限": authority,
            "角色": role,
            "地域适用性": 地域适用性,
            "时间状态": 时间状态,
        }
        if 状态注记:
            item["状态注记"] = 状态注记
        if 时间状态 == STATUS_UNKNOWN:
            evidence_list.append(_evidence(
                "degradation", "standards-reasoning-rules.md §五 M6 / SR-R-P0-2",
                f"标准={std_no} 索引 §1.1 无状态记录",
                "时间状态=未知，为显式降级态，不得作为合规判据，须走官方核验路径确认现行状态",
                "inferred",
            ))
        if 时间状态 == "已废止":
            evidence_list.append(_evidence(
                "degradation", "standards-reasoning-rules.md §4.3 TA-3 / SR-R-P0-1",
                f"标准={std_no} 时间状态={时间状态}, 状态注记={状态注记 or '未登记'}",
                "已废止标准不得作为现行合规判据；须通过 IC-07 核实替代关系，"
                "无直接替代时咨询主管部门。保留条目用于风险披露，角色不代表可引用；"
                "锚定标准废止须由维护者执行 S 级变更并停止相关结论使用。",
                "inferred",
            ))
        if not 核验确认:
            # 复用 STATUS_UNKNOWN 那条降级支的同一语义（置信度降 inferred ＋ degradation 证据），
            # 不新造枚举、不给 item 加字段：IC-10 items 为 additionalProperties: false，
            # 加字段即契约不兼容变更（整改方案 P1 item 10 明令复用既有降级语义）。
            evidence_list.append(_evidence(
                "degradation",
                "standards-reasoning-rules.md §五 M6 / standards-index.md §1.1A 核验状态执行规则",
                f"标准={std_no}, 层级={level}, {核验事由}",
                "既有核验依据未确认，本条为降低确定性的降级输出，不等于标准已失效："
                "不得作为唯一合规判据；涉及安全/消防/隔声强制指标时须走 IC-07，"
                "依据可追溯的正式文本或权威公告核实，不以获取渠道定级"
                "（出口二选一：补足与结论范围相符的真实核验记录，或接受本降级态）",
                "inferred",
            ))
        # IC-10 items 为 additionalProperties: false，降级标注只能走既有的可选字段 `替代警告`
        notice = PARTIAL_REPLACEMENT_NOTICES.get(std_no)
        if notice:
            item["替代警告"] = notice
            evidence_list.append(_evidence(
                "degradation", "standards-reasoning-rules.md §4.3 TA-2",
                f"标准={std_no} 时间状态={时间状态}",
                "被替代条文切换至替代标准、未替代条文附注引用，本条为替代范围闭合前的降级输出",
                "deterministic",
            ))
        result.append(item)

    return result


def _role_priority(role: str) -> int:
    order = ["mandatory_check", "design_basis", "verification_reference", "construction_guide", "prefab_evaluation"]
    return order.index(role) if role in order else 99


# ---------------------------------------------------------------------------
# 证据对象与决策轨迹
# ---------------------------------------------------------------------------

_evidence_counter = 0


def _evidence(
    evidence_type: str,
    rule_source: str,
    input_fact: str,
    output_conclusion: str,
    confidence: str,
) -> dict:
    global _evidence_counter
    _evidence_counter += 1
    return {
        "证据ID": f"EVID-{_evidence_counter:03d}",
        "证据类型": evidence_type,
        "规则来源": rule_source,
        "输入事实": input_fact,
        "输出结论": output_conclusion,
        "置信度": confidence,
    }


def _build_trace(
    project_type: str,
    space_type: str,
    location: str | None,
    system: str | None,
    demands: list[str] | None,
    activated: set[str],
    standards: list[dict],
    arbitration_note: str,
) -> str:
    lines = [
        f"Step 0 输入: 项目类型={project_type}, 空间类型={space_type}, 所在地={location or '未提供'}, 构造体系={system or '未提供'}, 性能需求={demands or '按规范'}",
        f"Step 1 激活域: {', '.join(sorted(activated))}",
        f"Step 1a 裁定: {arbitration_note or '性能需求未提供或不含声学类，按候选域原样保留'}",
        f"Step 2 族映射: 共涉及 {len(standards)} 条标准",
        "Step 3 权限裁决: L1 优先于 L2/L3/L4；地方标准严于国标时优先",
        "Step 4 角色分配: 按 mandatory_check → design_basis → verification_reference → construction_guide → prefab_evaluation 分配",
        "Step 5 排序输出: 按角色优先级排序",
        "Step 6 标注: 地域适用性与时间状态已标注",
    ]
    return "\n".join(lines)


def _normalize_location(location: str | None) -> str | None:
    """地点入参归一化：剥离尾部「省/市」行政后缀（断点2）。

    M3 Step 2 的地点附加（`location in mapping`）按精确键匹配，而各域地点键与 M4
    `_apply_applicability` 的 `location.rstrip("省市")` 均为 stripped 形态（北京/浙江/
    广东…）。IC-10 契约示例却用「北京市/浙江省」全称，不归一化会导致地方标准族整族
    漏命中。与 M4 同口径，使「北京市」→「北京」后再匹配。空值/退化值原样返回。
    """
    if not location:
        return location
    return location.rstrip("省市") or location


def _external_collab_entry(
    domain: str, placeholder: str, evidence_list: list[dict]
) -> dict[str, Any]:
    """断点5（CG-20260917-002）：外协占位 → 结构化「外部协同」条目 ＋ 降级证据。

    原实现把 （ 前缀占位串在去重时以 `not s.startswith("（")` 静默过滤，而占位串又使
    family 非空、不触发空族提示——用户既看不到标准也看不到外协提示（报告断点5）。改为
    对外可见的结构化字段；技能状态话术按 interface-contracts.md 既有记录取「已迁出」。
    """
    evidence_list.append(_evidence(
        "degradation", "standards-reasoning-rules.md §五 M6 / IC-10 外部协同",
        f"触发域={domain}",
        f"外部协同：{EXTERNAL_COLLAB_SKILL}（{EXTERNAL_COLLAB_STATUS}）",
        "deterministic",
    ))
    return {
        "协同技能": EXTERNAL_COLLAB_SKILL,
        "技能状态": EXTERNAL_COLLAB_STATUS,
        "触发域": domain,
        "协同事项": placeholder.strip("（）"),
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def reason(
    project_type: str,
    space_type: str,
    location: str | None = None,
    system: str | None = None,
    demands: list[str] | None = None,
    return_trace: bool = False,
) -> dict:
    """
    执行标准推理引擎（SRE）。

    参数与 IC-10-Request v1.9.0 对齐。
    """
    global _evidence_counter
    _evidence_counter = 0
    evidence_list: list[dict] = []

    # M6 降级：项目地点未提供时 LA-4 追问
    uncovered: list[str] = []
    # 断点2：地点入参先归一化（北京市→北京），使 M3 Step2 地点附加与 M4 同口径
    location = _normalize_location(location)
    if not location:
        evidence_list.append(_evidence(
            "degradation", "standards-reasoning-rules.md §4.1 LA-4 / §五 M6",
            "项目所在地未提供",
            "跳过地方标准适用性判定，提示追问项目所在地",
            "deterministic"
        ))
        uncovered.append("地方标准适用性（需确认项目所在地）")

    # Step 1 / 1a
    activated, arbitration_note = _activate_domains(
        project_type, space_type, demands, evidence_list, uncovered)

    # Step 2
    raw_standards: list[str] = []
    external_collab: list[dict[str, Any]] = []
    for domain in sorted(activated):
        family = _map_domain_to_standards(domain, project_type, location, system, evidence_list)
        if not family:
            # M6 空族不得静默（设计方案 §3.2 T-B5，CG-20260916-005）：证据对象里的
            # 标准族=[] 属内部痕迹，使用者只读 未覆盖领域，故信号须同时落到输出。
            uncovered.append(f"{domain}（该域标准族为空，须补充 Step 2 映射或转专项技能咨询）")
            continue
        # 断点5（CG-20260917-002）：（ 前缀为外协占位，分流到结构化「外部协同」对外可见，
        # 不再被去重静默过滤；其余为真实标准编号，进 raw_standards。
        for s in family:
            if s.startswith("（"):
                external_collab.append(_external_collab_entry(domain, s, evidence_list))
            else:
                raw_standards.append(s)

    # 去重（外协占位已在 Step 2 分流，此处只处理真实标准编号）
    seen = set()
    unique_raw: list[str] = []
    for s in raw_standards:
        if s not in seen:
            seen.add(s)
            unique_raw.append(s)

    # M1 分类
    classified = [classify_standard(s, evidence_list) for s in unique_raw]

    # M4 适用性裁判
    applied = _apply_applicability(classified, location, evidence_list)

    # Step 5 排序
    applied.sort(key=lambda x: (_role_priority(x["角色"]), x["层级"], x["标准编号"]))

    # 推理路径
    reasoning_path = (
        f"场景({project_type}|{space_type}) → "
        f"激活域({', '.join(sorted(activated))}) → "
        f"标准族({len(applied)}条) → "
        f"角色排序({', '.join(dict.fromkeys(s['角色'] for s in applied))})"
    )

    response: dict[str, Any] = {
        "适用标准集": applied,
        "推理路径": reasoning_path,
        "证据对象": evidence_list,
    }

    if uncovered:
        response["未覆盖领域"] = uncovered

    if external_collab:
        # 断点5（CG-20260917-002）：外协动作对外可见，IC-10 v1.9.0 新增响应字段。
        response["外部协同"] = external_collab

    if return_trace:
        response["决策轨迹"] = _build_trace(
            project_type, space_type, location, system, demands,
            activated, applied, arbitration_note,
        )

    return response


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if len(args) < 2:
        print("用法: python sre_reasoner.py <项目类型> <空间类型> [项目所在地] [构造体系] [性能需求,逗号分隔] [--trace]")
        sys.exit(1)

    project_type, space_type = args[0], args[1]
    location = args[2] if len(args) > 2 and not args[2].startswith("-") else None
    system = args[3] if len(args) > 3 and not args[3].startswith("-") else None
    demands = args[4].split(",") if len(args) > 4 and not args[4].startswith("-") else None
    return_trace = "--trace" in args

    result = reason(project_type, space_type, location, system, demands, return_trace)
    print(json.dumps(result, ensure_ascii=False, indent=2))
