"""
SRE Reasoner 回归测试集
========================
版本：v1.13（2026-10-25：Windows 控制台/管道下 run_and_report 先重配置 stdout/stderr 为 UTF-8；84 例与判据不变）
      v1.12（2026-10-17：CG-20261017-006 台账 PL-200 裁定①「公寓按住宿期限分流（甲）」实施批——
守卫组 TestProjectTypeAnalogy 由九例扩为十三例（79→84 例）。三支按「公寓→住宅」写死期望值的例
同批改判（其中两支改名，旧名→新名映射见该组 docstring，映射不留第二处）：正向例改为
「三枚高频场景非未命中＋族含 GB 55025-2022 且不含 GB 55038-2025」；相等例改为「公寓 body 相对
住宅的差集只允许全文强制枚」；类比表牙例改为「撤两轴专用面回类比态／两支全撤才回未命中」。
新增取数侧三例＝取序后缀（专用行优先）＋覆盖面（公寓行逐枚齐于住宅场景且域集相等）＋
标注三件事逐件在场；跨载体逐字对撞例保留原判据（命中数须恰 1）。
承接台账 PL-198 ②／PL-200 ③④——「公寓」自本版起走专用行，PROJECT_TYPE_ANALOGIES 退役为兜底）
      v1.11（2026-10-12：CG-20261012-006 项目类型类比归一守卫组 TestProjectTypeAnalogy 九例，70→79 例；
承接台账 PL-198 ②——「公寓」经 PROJECT_TYPE_ANALOGIES 按住宅档进 M3 场景推理面；九例＝四正向
＋两支控制例（目标档不带标注／类比档未命中态仍判未命中）＋一支牙例（内存内撤类比表须回到未命中，
tearDown 回滚）＋一支跨载体字面对撞（代码常量 == standards-reasoning-rules.md §3.2 注内逐字，恰一枚））
      v1.10（2026-09-26：CG-20260926-010 名称 SOT 优先守卫组 TestNameSotPriority 四例，66→70 例；
承接 PL-002／设计方案 §9.2 共通动作——优先序主表 > 登记表 > 硬编码预置：主表名称覆盖预置、
登记表不覆盖主表、登记表同样覆盖预置（不留同族遮蔽面）、预置仅兜两表均未收录编号。
上一版 v1.9：2026-09-17 复查到期与标准效力／依据确定性解耦，59→66 例）
运行：python sre_regression_test.py

覆盖：M1 分类协议、M3 场景推理（含项目类型分流与类比兜底）与 Step 1a 裁定、M4 适用性裁判、M6 降级、
      IC-10 Response v1.9.0 Schema、SR 引擎化红线，
      以及 SRE 确定性体检行为组 T-B1—T-B7 + A5-B 锚定升档
      （判据源《文档目录/SRE确定性体检设计方案_v1.0.md》§3.2 / §4.3 / §5.2 / §6.2）。
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import random
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import sre_reasoner
from sre_reasoner import reason, classify_standard, _evidence_counter

# ---------------------------------------------------------------------------
# 确定性体检基础设施（设计方案 §3.2 判据 / §4.3 挂载位 / §5.2 十二字段契约）
# 期望值一律取自活体模块与 standards-index.md 现读结果：并行批次改写 STANDARD_NAMES /
# DOMAINS 字面量时，本文件只随事实变化，不随文本漂移。
# ---------------------------------------------------------------------------

SRE_SOURCE = Path(sre_reasoner.__file__).resolve()
SRE_SRC = SRE_SOURCE.read_text(encoding="utf-8")
_SRC_TREE = ast.parse(SRE_SRC)
_REPORT_PATH = Path(__file__).resolve().parent / "sre_regression_report.json"
# 报告路径相对化锚（私密路径防扩散）：skills 根（运行时 ~/.qoder/skills/ 或仓内镜像
# 技能仓备份/，取实际跑侧）。报告 measurements 原记绝对路径（含本机用户名），该 json
# 随技能目录整目录复制即外泄；相对化后承载位置语义不变（prefab-standards-reviewer/…、
# shared/…），跨机器可读。锚外路径退化为 basename（必不含用户名）。
_SKILLS_ROOT = SRE_SOURCE.parent.parent

# 与 sre_reasoner._load_standards_index() 的候选顺序一致
_INDEX_CANDIDATES = [
    SRE_SOURCE.parent.parent / "shared" / "standards-index.md",
    SRE_SOURCE.parent.parent / "standards-index.md",
]

# IC-10 契约候选，与索引同序（运行时 shared 镜像为 L3、技能目录兜底）
_CONTRACT_CANDIDATES = [
    SRE_SOURCE.parent.parent / "shared" / "interface-contracts.md",
    SRE_SOURCE.parent.parent / "interface-contracts.md",
]

# T-B1 的测量点：本模块 import 完毕、任何用例尚未执行之前
IMPORT_TIME_STATUS_COUNT = len(sre_reasoner.STANDARD_STATUS)

GAP_SIGNALS: list[dict] = []
_SIGNAL_TOUCHED: dict[str, object] = {}

CATEGORY_ENUM = ("DEGRADE", "GAP", "DRIFT")
ESCALATION_ENUM = ("S", "A", "B", "C", "—")
ANCHOR_ENUM = ("true", "false", "—", "unknown")
ACTION_ENUM = ("待补录", "待修代码", "待核原文", "已裁定", "不适用")
STATUS_ENUM = ("义务已生效未落实", "可即刻执行", "待用户确认", "待核原文",
               "待实施", "随批次上线", "已闭环", "有意延后")
SIGNAL_KEYS = ("signal_id", "类别", "判据", "实测值", "期望值", "证据定位",
               "裁定动作", "承担者", "状态", "escalation", "anchor", "verify_channel")

# 索引 §1.1A 中代表"已确认"的那一态；其余三态即 §3.2 T-B2 所指降级侧。
# 运行期核对它仍在现读的表内，SOT 改名时用例转红而非静默放行。
CONFIRMED_VERIFY_STATE = "已官方核验"

# §3.2 T-B6：全国层级 = 编号不以 DB/DBJ/SJG 开头且非 T/ 团标
LOCAL_ID_PREFIXES = ("DB", "DBJ", "SJG", "T/")

# §3.2 场景矩阵（取自 sre_reasoner.ACTIVATION_TABLE 既有行，不新造场景）
SCENARIOS = [
    ("住宅", "分户墙", None), ("住宅", "分户楼板", None),
    ("住宅", "户内吊顶", ["隔声"]), ("住宅", "户内吊顶", ["吸声"]),
    ("住宅", "楼地面", None), ("酒店", "客房吊顶", None),
    ("学校", "教室吊顶", None), ("办公", "办公吊顶", None),
    ("医院", "病房地面", None),
]
LOCATIONS = ["全国", "北京", "浙江", "广东", "深圳", "福建", None]


def _index_text() -> str | None:
    for path in _INDEX_CANDIDATES:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None


def _section(text: str, start: str, stops: tuple[str, ...]) -> str:
    """按标题正则切出小节；起点未命中返回空串。"""
    lines = text.splitlines()
    begin = next((i for i, ln in enumerate(lines) if re.match(start, ln)), None)
    if begin is None:
        return ""
    end = next((i for i, ln in enumerate(lines[begin + 1:], begin + 1)
                if any(re.match(s, ln) for s in stops)), len(lines))
    return "\n".join(lines[begin:end])


def _table(block: str) -> tuple[list[str], list[list[str]]]:
    """表头感知取块内首张表：返回 (表头单元, 数据行单元)。

    数据行在首个非表格行处截断——否则同一小节内的第二张表（如索引 §二 末尾的
    「官方核验记录」）会被并入首表行集，其表头单元被当成编号读出。
    """
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        sep = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not sep or set(sep.replace("|", "").strip()) - set("-: "):
            continue
        header = [c.strip() for c in line.strip().strip("|").split("|")]
        rows: list[list[str]] = []
        for ln in lines[i + 2:]:
            if not ln.startswith("|"):
                break
            rows.append([c.strip() for c in ln.strip().strip("|").split("|")])
        return header, rows
    return [], []


def _cell(row: list[str], header: list[str], key: str) -> str:
    return row[header.index(key)] if key in header and header.index(key) < len(row) else ""


def _status_enum() -> list[str]:
    """索引 §1.1「标准状态」字段的合法取值集（现读，到「官方核验日期」行为止）。

    §1.1 表首列是状态字段名（且续行首列为空），故停表判据取行首单元，不取列名。
    """
    block = _section(_index_text() or "", r"^### 1\.1\s", (r"^### 1\.1A",))
    header, rows = _table(block)
    values: list[str] = []
    for row in rows:
        if "官方核验日期" in (row[0] if row else ""):
            break
        values += re.findall(r"`([^`]+)`", _cell(row, header, "取值"))
    return list(dict.fromkeys(values))


def _verify_state_enum() -> list[str]:
    """索引 §1.1A 核验状态四态（现读）。"""
    block = _section(_index_text() or "", r"^### 1\.1A", (r"^### 1\.2",))
    header, rows = _table(block)
    key = header[0] if header else ""
    return list(dict.fromkeys(r[0] for r in rows if key and r))


def _contract_text() -> str:
    for path in _CONTRACT_CANDIDATES:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _contract_ic10_status_enum() -> list[str]:
    """IC-10 `适用标准集[].时间状态` 合法集（从契约 Schema 现读，不写死）。

    契约缺失或正则脱靶时返回空表——调用方按"全表越界"失败收口，不静默放行。
    """
    match = re.search(r'"时间状态":\s*\{[^{}]*?"enum":\s*\[([^\]]*)\]', _contract_text(), re.S)
    if not match:
        return []
    return [s.strip().strip('"') for s in match.group(1).split(",") if s.strip()]


def _contract_ic10_role_enum() -> list[str]:
    """IC-10 `适用标准集[].角色` 合法集（从契约 Schema 现读，本文件不留字面量副本）。

    契约缺失或正则脱靶时返回空表——调用方按"全表越界"失败收口，不静默放行。
    """
    match = re.search(r'"角色":\s*\{[^{}]*?"enum":\s*\[([^\]]*)\]', _contract_text(), re.S)
    if not match:
        return []
    return [s.strip().strip('"') for s in match.group(1).split(",") if s.strip()]


def _contract_declares(field: str) -> bool:
    """字段是否已在 IC-10 契约 Schema 里声明（additionalProperties: false 的前置条件）。"""
    return f'"{field}":' in _contract_text()


def _index_l1_ids() -> set[str]:
    """索引 §二「第一层级：强制性国标（红线标准）」主表编号（从索引现读）。

    不取 `sre_reasoner.INDEX_L1_IDS`——运行时自证自指无交叉核对力，两侧同错即静默放行。
    """
    block = _section(_index_text() or "", r"^## 二、", (r"^## 三、",))
    header, rows = _table(block)
    return {_cell(r, header, "标准编号") for r in rows} - {""}


def _deprecated_rows() -> list[tuple[str, str]]:
    """索引 §7.2 的 (标准编号, 状态) 行集（现读，条数不写死）。"""
    block = _section(_index_text() or "", r"^### 7\.2", (r"^### 7\.3",))
    header, rows = _table(block)
    return [(_cell(r, header, "标准编号"), _cell(r, header, "状态"))
            for r in rows if _cell(r, header, "标准编号")]


def _anchor_ids() -> tuple[set[str] | None, str | None]:
    """索引 §10.2 锚定集。不可达时返回 (None, 降级说明)——§5.2 禁静默填 false。"""
    text = _index_text()
    if text is None:
        return None, "standards-index.md 不可达（候选：{}），A5-B 全部信号 anchor=unknown".format(
            "; ".join(str(p) for p in _INDEX_CANDIDATES))
    block = _section(text, r"^### 10\.2", (r"^### ", r"^## "))
    header, rows = _table(block)
    ids = {_cell(r, header, "标准编号") for r in rows} - {""}
    if not ids:
        return None, "索引 §10.2 锚定表解析为空，A5-B 全部信号 anchor=unknown"
    return ids, None


def _assign_line(name: str) -> int | None:
    for node in ast.walk(_SRC_TREE):
        targets = getattr(node, "targets", [getattr(node, "target", None)])
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
                isinstance(t, ast.Name) and t.id == name for t in targets):
            return node.lineno
    return None


def _def_line(name: str) -> int | None:
    obj = getattr(sre_reasoner, name, None)
    try:
        return inspect.getsourcelines(obj)[1]
    except (TypeError, OSError):
        return _assign_line(name)


def _loc(*pairs: tuple[str, int | None]) -> str:
    return "；".join(f"{SRE_SOURCE.name}:{line}（{what}）" for what, line in pairs if line)


def _main_guard_calls() -> tuple[list[int], list[int]]:
    """_load_standards_index() 的调用点按「是否位于 __main__ 守卫子树内」分组。"""
    calls = [n.lineno for n in ast.walk(_SRC_TREE)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_load_standards_index"]
    guards = [n for n in ast.walk(_SRC_TREE)
              if isinstance(n, ast.If) and "__name__" in ast.dump(n.test)
              and "'__main__'" in ast.dump(n.test)]
    inside = [c for c in calls if any(g.lineno <= c <= (g.end_lineno or g.lineno) for g in guards)]
    return inside, [c for c in calls if c not in inside]


def _probe_id() -> str:
    """每次运行随机的、索引与硬编码表内都不存在的标准编号形态串。"""
    while True:
        candidate = "GB/T 9%06d-19%02d" % (random.randrange(1_000_000), random.randrange(100))
        if (candidate not in sre_reasoner.STANDARD_STATUS
                and candidate not in sre_reasoner.STANDARD_NAMES):
            return candidate


def _reachable_domains() -> set[str]:
    """激活可达域 = 激活表值域 ∪ 声学子域集 ∪ 需求驱动增补域（增补逻辑经活体函数求得）。"""
    reachable = {d for v in sre_reasoner.ACTIVATION_TABLE.values() for d in v}
    reachable |= set(sre_reasoner.ACOUSTIC_SUBDOMAINS)
    demand_matrix = [None, ["隔声"], ["吸声"], ["撞击声"], ["防火", "验收"],
                     ["隔声", "吸声", "撞击声"]]
    for project_type, space_type, _ in SCENARIOS:
        for demands in demand_matrix:
            activated, _ = sre_reasoner._activate_domains(
                project_type, space_type, demands, [])
            reachable |= set(activated)
    return reachable


def _iter_tables(text: str):
    """遍历全文所有 Markdown 表，产出 (表头单元, 数据行单元)。"""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if (line.startswith("|") and i + 1 < len(lines)
                and not set(lines[i + 1].replace("|", "").strip()) - set("-: ")):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            yield header, rows
            i = j
        else:
            i += 1


def _main_table_truth() -> dict[str, str]:
    """索引主表（首列「序号」且含「状态」列）的 编号→状态 真值，按表头列名取值。

    刻意不复用被测代码的固定列号口径：本函数即 T-B4 的对照基准。
    """
    truth: dict[str, str] = {}
    for header, rows in _iter_tables(_index_text() or ""):
        if not header or header[0] != "序号" or "状态" not in header:
            continue
        id_col = next((c for c in ("标准编号", "图集编号") if c in header), None)
        if not id_col:
            continue
        for row in rows:
            std_no = _cell(row, header, id_col)
            if std_no:
                truth[std_no] = _cell(row, header, "状态")
    return truth


def _verify_table_truth(require_both: bool = False) -> dict[str, dict[str, str | None]]:
    """索引核验记录表的 编号→{核验日期, 核验状态} 真值，按列名取值（item 9/10 对照基准）。

    刻意不复用 `sre_reasoner._classify_verify_table`／`_VERIFY_ID_SHAPE`：两侧同错即静默放行，
    交叉核对才有力（同 CG-20260916-009 `INDEX_L1_IDS` 口径）。判据：
      · 含编号列且含「核验日期」**或**「核验状态」列者即核验台账——状态主表两列皆无，天然互斥；
        §1.1A 的核验状态枚举定义表无编号列，亦被排除（否则「已官方核验」会被当成编号读入）；
      · `require_both=True` 只用于反证「或」语义是活代码（索引 §二 的核验状态分层表只有
        「核验状态」无「核验日期」，收紧为"两列都要"会整表丢失状态）；
      · 并列编号单元只按全角 `／` 切分，半角 `/` 属 `GB/T`、`DBJ/T` 编号本体；
      · 首字符非 ASCII 或不含数字者剔除（「其余强制性国标」等散文兜底单元）。
    """
    truth: dict[str, dict[str, str | None]] = {}
    for header, rows in _iter_tables(_index_text() or ""):
        id_col = next((c for c in ("标准编号", "图集编号", "编号") if c in header), None)
        if not id_col:
            continue
        has_date, has_state = "核验日期" in header, "核验状态" in header
        if not ((has_date and has_state) if require_both else (has_date or has_state)):
            continue
        for row in rows:
            for one in _cell(row, header, id_col).split("／"):
                std_no = one.strip().replace("**", "").strip()
                if not std_no or not std_no[0].isascii() or not any(c.isdigit() for c in std_no):
                    continue
                date = _cell(row, header, "核验日期").strip() or None
                state = _cell(row, header, "核验状态").strip() or None
                rec = truth.setdefault(std_no, {"核验日期": None, "核验状态": None})
                if date and (rec["核验日期"] is None or date > rec["核验日期"]):
                    rec["核验日期"] = date
                if state and not rec["核验状态"]:
                    rec["核验状态"] = state
    return truth


def _verify_tier(level: str) -> int:
    """该层级的核验周期天数（取自运行时常量，其真值源为索引 §1.1A，本文件不复制散文数值）。"""
    return sre_reasoner.REVIEW_DAYS_L1 if level == "L1" else sre_reasoner.REVIEW_DAYS_OTHER


def _applicability_evidence(response: dict, std_no: str) -> list[dict]:
    return [ev for ev in response["证据对象"]
            if ev["证据类型"] == "applicability"
            and ev["输入事实"].startswith(f"标准={std_no},")]


def _verify_degradation_evidence(response: dict, std_no: str) -> list[dict]:
    """核验时效专属降级证据。判据须同时锁 `规则来源` 含「§1.1A」与 `输入事实` 编号前缀，
    否则会被「地方标准不适用」「时间状态未知」等无关 degradation 证据顶替而恒真
    （CG-20260917-002 反向注入4 的同类教训）。"""
    return [ev for ev in response["证据对象"]
            if ev["证据类型"] == "degradation" and "§1.1A" in ev["规则来源"]
            and ev["输入事实"].startswith(f"标准={std_no},")]


def _applicable_ids(project_type: str, space_type: str, demands, location) -> set[str]:
    response = sre_reasoner.reason(project_type, space_type, location=location,
                                   demands=demands, system="轻钢龙骨")
    return {item["标准编号"] for item in response["适用标准集"]}


def _record_signal(signal_id: str, category: str, observed: str, expected: str,
                   location: str, escalation: str, violated: bool, touched=set()) -> None:
    """按 §5.2 十二字段记账。裁定动作/状态首轮留空键（§10 条 4），anchor 由 A5-B 覆写。

    `violated=False`（该项已修复）时不落信号——信号集须随修复收敛，否则摘掉
    `@unittest.expectedFailure` 后仍会留下一条已消失的缺口，与 §6.2 自退役语义相悖。
    """
    if not violated:
        return
    GAP_SIGNALS.append({
        "signal_id": signal_id,
        "类别": category,
        "判据": f"《SRE确定性体检设计方案_v1.0.md》§3.2 {signal_id} 行（按指针取值，不在运行期重抄）",
        "实测值": observed,
        "期望值": expected,
        "证据定位": location,
        "裁定动作": None,
        "承担者": "用户（授权运行时写入）→ 维护者（实施）",
        "状态": None,
        "escalation": escalation,
        "anchor": "false",
        "verify_channel": "—",
    })
    _SIGNAL_TOUCHED[signal_id] = touched


def _validate_signal(sig: dict) -> None:
    """落盘前拒绝枚举外的值（§10 条 4）。"""
    missing = [k for k in SIGNAL_KEYS if k not in sig]
    if missing:
        raise ValueError(f"信号 {sig.get('signal_id')} 缺字段：{missing}")
    for key, allowed in (("类别", CATEGORY_ENUM), ("escalation", ESCALATION_ENUM),
                         ("anchor", ANCHOR_ENUM)):
        if sig[key] not in allowed:
            raise ValueError(f"信号 {sig['signal_id']} 的 {key}={sig[key]!r} 越出闭合枚举 {allowed}")
    for key, allowed in (("裁定动作", ACTION_ENUM), ("状态", STATUS_ENUM)):
        value = sig[key]
        if value is None:
            continue
        # §5.2：一格可并列多值（`待修代码①：…；待补录②：…`），逐段校验而非只查首词
        for seg in re.split(r"[；;]", str(value)):
            seg = seg.strip()
            if not seg:
                continue
            head = re.sub(r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]", "",
                          re.split(r"[：:（(]", seg)[0]).strip()
            if head not in allowed:
                raise ValueError(
                    f"信号 {sig['signal_id']} 的 {key} 段首词 {head!r} 越出闭合枚举 {allowed}")


def _aggregate_anchors() -> list[str]:
    """A5-B：本组其余检查跑完后运行，对每条信号按其触及编号叠加 anchor（§4.1/§4.3）。"""
    anchors, degraded = _anchor_ids()
    warnings = []
    if degraded:
        warnings.append(f"[SRE-GAP] A5-B 降级：{degraded}")
    for sig in GAP_SIGNALS:
        touched = _SIGNAL_TOUCHED.get(sig["signal_id"])
        touched = touched() if callable(touched) else set(touched or ())
        if anchors is None:
            sig["anchor"] = "unknown"
        else:
            sig["anchor"] = "true" if anchors & touched else "false"
    return warnings


def _signal_state() -> tuple[str, int]:
    """§6.3.3 ① 的跨轮指纹：绑到信号内容，实测值规模漂移即视为抖动。"""
    rows = sorted([sig["signal_id"], sig["类别"], sig["实测值"], sig["期望值"]]
                  for sig in GAP_SIGNALS)
    digest = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()
    return digest[:16], len(rows)


def _previous_report() -> dict:
    try:
        loaded = json.loads(_REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


class TestM1Classification(unittest.TestCase):
    """M1 编号前缀分类协议。"""

    def _classify(self, std_no: str) -> dict:
        ev = []
        return classify_standard(std_no, ev)

    def test_gb_55xxx_is_l1_redline(self):
        r = self._classify("GB 55038-2025")
        self.assertEqual(r["标准类型"], "全文强制性国标")
        self.assertEqual(r["层级"], "L1")
        self.assertEqual(r["权限"], "red_line")

    def test_gb_t_is_l2_binding(self):
        r = self._classify("GB/T 50121-2005")
        self.assertEqual(r["层级"], "L2")
        self.assertEqual(r["权限"], "binding_support")

    def test_db_is_local(self):
        # 断点7（CG-20260917-002）：层级真值源是 rules.md §1.2，DB/DBJ 地方标准=L2
        # （binding_support 设计/验收），非 L3。此前代码给 L3 与 rules.md 实质分歧。
        r = self._classify("DB33/T 1168-2019")
        self.assertEqual(r["层级"], "L2")
        self.assertEqual(r["权限"], "binding_support")
        self.assertEqual(r["地域范围"], "浙江")

    def test_dbj_is_local_l2(self):
        """断点7（CG-20260917-002）：DBJ/T 序号型（15=广东、13=福建）亦为 L2。"""
        for no in ("DBJ/T 15-208-2020", "DBJ/T 13-428-2023"):
            r = self._classify(no)
            self.assertEqual(r["层级"], "L2", f"{no} 应为 L2")
            self.assertEqual(r["权限"], "binding_support")

    def test_dbj_province_code_form_matches(self):
        """遗留2（CG-20260917-003）：DBJ+省码数字形态（DBJ33/T 1327-2024）须命中前缀，
        不再落 unknown_type；省份从索引 §5.3「适用地区」现读为浙江。
        负向半条：DBJX 一类非法形态仍须降级（守卫修净后仍能失败）。"""
        r = self._classify("DBJ33/T 1327-2024")
        self.assertEqual(r["标准类型"], "地方建设标准")
        self.assertEqual(r["层级"], "L2")
        self.assertEqual(r["权限"], "binding_support")
        self.assertEqual(r["地域范围"], "浙江")
        self.assertEqual(r["分类置信度"], "deterministic")
        bad = self._classify("DBJX 123-2024")
        self.assertEqual(bad["标准类型"], "unknown_type")
        self.assertEqual(bad["分类置信度"], "inferred")

    def test_t_group_is_l2_reference(self):
        # 遗留1（CG-20260917-003）：团体标准层级真值源 rules.md §1.2 定 L2（原代码 L4 分歧）；
        # 权限仍 reference（GS-1 团标不优先于 GB/T）。
        r = self._classify("T/CSUS 40-2022")
        self.assertEqual(r["层级"], "L2")
        self.assertEqual(r["权限"], "reference")

    def test_unknown_prefix_degrades(self):
        r = self._classify("XYZ 123-2024")
        self.assertEqual(r["分类置信度"], "inferred")
        self.assertEqual(r["权限"], "reference")

    def test_atlas_j_series_matches_prefix(self):
        """§1.2「数字+`J`」行须真命中：`\\b` 版式在 J 与数字间不成立，图集号会静默落 §1.4 降级支。"""
        for atlas in ("08J931", "07J905-1"):
            r = self._classify(atlas)
            self.assertEqual(r["标准类型"], "图集", f"{atlas} 未命中 §1.2 图集行")
            self.assertEqual(r["层级"], "L3")
            self.assertEqual(r["权限"], "reference")
            self.assertEqual(r["分类置信度"], "deterministic")

    def test_index_section2_id_is_l1_not_demoted(self):
        """前缀不可识别但索引 §二「第一层级：强制性国标（红线标准）」收录 → L1/red_line。

        降级为 reference 会把红线标准降为参考级。期望集从索引 §二 现读，不写死编号清单。
        """
        l1_ids = _index_l1_ids()
        self.assertGreater(len(l1_ids), 0, "索引 §二 现读为空，判据不可用")
        demoted = {i for i in l1_ids if self._classify(i)["层级"] != "L1"}
        self.assertEqual(demoted, set(), f"索引 §二 收录的强制性国标被降为参考级：{sorted(demoted)}")
        # 负向半条：索引外编号仍须走 §1.4 降级，本判据不得把任意编号升成 L1
        fake = "ZZ 9999-1999"
        self.assertNotIn(fake, l1_ids)
        self.assertEqual(
            (self._classify(fake)["层级"], self._classify(fake)["权限"],
             self._classify(fake)["分类置信度"]),
            ("L4", "reference", "inferred"), "索引外编号未走 §1.4 降级支")


class TestM3ScenarioReasoning(unittest.TestCase):
    """M3 场景→领域→标准族推理链。"""

    def test_residential_partition_wall_default(self):
        r = reason("住宅", "分户墙")
        stds = [s["标准编号"] for s in r["适用标准集"]]
        self.assertIn("GB 55038-2025", stds)
        self.assertIn("GB 55037-2022", stds)
        self.assertIn("GB 50210-2018", stds)

    def _extract_domains(self, reasoning_path: str) -> list[str]:
        start = reasoning_path.find("激活域(") + len("激活域(")
        depth = 1
        end = start
        while end < len(reasoning_path) and depth > 0:
            if reasoning_path[end] == "(":
                depth += 1
            elif reasoning_path[end] == ")":
                depth -= 1
            end += 1
        return reasoning_path[start:end - 1].split(", ")

    def test_hotel_guest_room_ceiling_filter_acoustic(self):
        # 显式不含隔声：acoustic(贡献) 应被过滤
        r = reason("酒店", "客房吊顶", demands=["防火", "验收"])
        domains = self._extract_domains(r["推理路径"])
        self.assertNotIn("acoustic(贡献)", domains)

    def test_residential_ceiling_augment_acoustic(self):
        # 显式隔声：户内吊顶应增补 acoustic(贡献)
        r = reason("住宅", "户内吊顶", demands=["隔声"])
        domains = self._extract_domains(r["推理路径"])
        self.assertIn("acoustic(贡献)", domains)

    def test_commercial_no_acoustic_default(self):
        r = reason("商业", "商铺")
        stds = [s["标准编号"] for s in r["适用标准集"]]
        self.assertNotIn("GB 50118-2010", stds)

    def test_unknown_space_falls_back_minimum(self):
        r = reason("其他", "特殊空间")
        stds = [s["标准编号"] for s in r["适用标准集"]]
        self.assertTrue(any("55037" in s for s in stds))
        self.assertTrue(any("50210" in s for s in stds))


class TestM4Applicability(unittest.TestCase):
    """M4 适用性裁判规则。"""

    def test_local_standard_applicable_when_location_matches(self):
        r = reason("住宅", "分户墙", location="浙江")
        local_stds = [s for s in r["适用标准集"] if s["标准编号"].startswith("DB33")]
        self.assertTrue(local_stds)
        for s in local_stds:
            self.assertEqual(s["地域适用性"], "项目所在地适用")

    def test_local_standard_not_applicable_when_location_differs(self):
        r = reason("住宅", "分户墙", location="广东")
        db33 = [s for s in r["适用标准集"] if s["标准编号"].startswith("DB33")]
        for s in db33:
            self.assertEqual(s["地域适用性"], "不适用仅作对比")

    def test_l1_has_mandatory_check_role(self):
        r = reason("住宅", "分户墙")
        gb55038 = next(s for s in r["适用标准集"] if s["标准编号"] == "GB 55038-2025")
        self.assertEqual(gb55038["角色"], "mandatory_check")

    def _matrix_items(self, std_no: str):
        """全 34 组激活对 × LOCATIONS 里指定编号的产出条目，随附其场景键。"""
        for project_type, space_type in sre_reasoner.ACTIVATION_TABLE:
            for location in LOCATIONS:
                for s in reason(project_type, space_type, location=location)["适用标准集"]:
                    if s["标准编号"] == std_no:
                        yield (project_type, space_type, location), s

    def test_index_section2_standard_gets_mandatory_check(self):
        """GB 18580-2025 裁定值（出处＝索引 §二 红线标准）：全矩阵恒为 L1/red_line/mandatory_check。"""
        bad = [(k, s["层级"], s["权限"], s["角色"])
               for k, s in self._matrix_items("GB 18580-2025")
               if (s["层级"], s["权限"], s["角色"]) != ("L1", "red_line", "mandatory_check")]
        self.assertGreater(len(list(self._matrix_items("GB 18580-2025"))), 0,
                           "GB 18580-2025 未进入任何场景输出，判据空跑")
        self.assertEqual(bad, [], f"甲醛环保红线在部分场景仍被降为参考级：{bad[:4]}")

    def test_atlas_standard_gets_construction_guide(self):
        """08J931 裁定值（出处＝§1.2「数字+`J`」图集行 + Step 4「L3 图集」）：恒为 L3/construction_guide。"""
        hits = list(self._matrix_items("08J931"))
        self.assertGreater(len(hits), 0, "08J931 未进入任何场景输出，判据空跑")
        bad = [(k, s["层级"], s["角色"]) for k, s in hits
               if (s["层级"], s["角色"]) != ("L3", "construction_guide")]
        self.assertEqual(bad, [], f"图集未落 Step 4 的 L3 分支：{bad[:4]}")

    def test_M4_fallback_role_stays_in_contract_enum(self):
        """负向注入：前缀不可识别、索引未收录的编号经 M4 `else` 兜底，角色仍须属契约五值。

        全矩阵正例在数据面修净后不再含兜底支编号（恒真空跑），故合成注入绑定该支——
        兜底值回退为 `reference` 时本用例转红（记忆「修净后的守卫须仍能失败」）。
        """
        fake = "ZZ 9999-1999"
        self.assertNotIn(fake, sre_reasoner.INDEX_L1_IDS, "合成编号已在索引 §二，负向取证失效")
        self.assertFalse(any(re.match(pat, fake) for pat, *_ in sre_reasoner.PREFIX_PATTERNS),
                         "合成编号撞上真实前缀模式，未走 §1.4 降级支")
        domain = next(d for d, m in sre_reasoner.DOMAINS.items()
                      if "默认" in m and "全部" not in m and "住宅" not in m)
        scenario = next(k for k, v in sre_reasoner.ACTIVATION_TABLE.items() if domain in v)
        saved = {d: dict(m) for d, m in sre_reasoner.DOMAINS.items()}
        try:
            mapping = dict(sre_reasoner.DOMAINS[domain])
            mapping["默认"] = list(mapping["默认"]) + [fake]
            sre_reasoner.DOMAINS[domain] = mapping
            hit = next((s for s in reason(*scenario)["适用标准集"] if s["标准编号"] == fake), None)
            self.assertIsNotNone(hit, "合成编号未进入输出，负向路径根本没走到")
            role_enum = _contract_ic10_role_enum()
            self.assertIn(hit["角色"], role_enum,
                          f"M4 兜底支产出 {hit['角色']!r}，越出 IC-10 契约枚举 {role_enum}")
            self.assertLess(sre_reasoner._role_priority(hit["角色"]), 99,
                            "兜底角色落不进 Step 5 优先级表，排序按未知处理")
        finally:
            for d, m in saved.items():
                sre_reasoner.DOMAINS[d] = m


class TestM6Degradation(unittest.TestCase):
    """M6 降级与兜底协议。"""

    def test_missing_location_prompts_question(self):
        r = reason("住宅", "分户墙")
        self.assertIn("未覆盖领域", r)
        self.assertTrue(any("地方标准" in u for u in r["未覆盖领域"]))

    def test_unknown_standard_degrades(self):
        # 通过未知构造体系触发外部协同占位，不应导致崩溃
        r = reason("住宅", "分户墙", system="未知构造")
        self.assertIsInstance(r["适用标准集"], list)


class TestIC10SchemaCompliance(unittest.TestCase):
    """IC-10 Response v1.9.0 Schema 合规（v1.9.0：新增响应字段「外部协同」，CG-20260917-002）。"""

    def test_required_fields_present(self):
        r = reason("住宅", "分户墙")
        self.assertIn("适用标准集", r)
        self.assertIn("推理路径", r)
        self.assertIn("证据对象", r)
        self.assertIsInstance(r["证据对象"], list)
        self.assertGreaterEqual(len(r["证据对象"]), 1)

    def test_evidence_object_schema(self):
        r = reason("住宅", "分户墙")
        for ev in r["证据对象"]:
            self.assertIn("证据ID", ev)
            self.assertIn("证据类型", ev)
            self.assertIn(ev["证据类型"], ["classification", "activation", "mapping", "arbitration", "applicability", "degradation"])
            self.assertIn("规则来源", ev)
            self.assertIn("输入事实", ev)
            self.assertIn("输出结论", ev)
            self.assertIn("置信度", ev)
            self.assertIn(ev["置信度"], ["deterministic", "inferred", "unknown"])

    def test_standard_item_schema(self):
        """IC-10 条目逐字段合规：全 34 组激活对 × LOCATIONS 矩阵遍历。

        判据原为 `reason("住宅", "分户墙")` 单场景（CG-20260916-005 复核 F-03 已登记该
        覆盖面缺口）：该场景不含任何走 M1 降级支的编号，`角色` 断言在 M4 `else` 兜底越枚举
        真实存在时仍恒真。扩面后凡落进兜底支的编号都进入判据面。
        """
        contract_enum = _contract_ic10_status_enum()
        role_enum = _contract_ic10_role_enum()
        self.assertGreater(len(role_enum), 0, "IC-10 契约 角色 枚举现读为空，降级判据不可用")
        bad: list[str] = []
        visited: set[tuple[str, str]] = set()
        checked = 0
        for project_type, space_type in sre_reasoner.ACTIVATION_TABLE:
            for location in LOCATIONS:
                visited.add((project_type, space_type))
                for s in reason(project_type, space_type, location=location)["适用标准集"]:
                    checked += 1
                    tag = f"{project_type}/{space_type}/{location} {s.get('标准编号')}"
                    for field in ("标准编号", "标准名称", "层级", "权限", "角色",
                                  "地域适用性", "时间状态"):
                        if field not in s:
                            bad.append(f"{tag} 缺字段 {field}")
                    if s.get("层级") not in ("L1", "L2", "L3", "L4"):
                        bad.append(f"{tag} 层级 {s.get('层级')!r} 越界")
                    if s.get("权限") not in ("red_line", "binding_support", "reference"):
                        bad.append(f"{tag} 权限 {s.get('权限')!r} 越界")
                    if s.get("角色") not in role_enum:
                        bad.append(f"{tag} 角色 {s.get('角色')!r} 越出 IC-10 契约枚举 {role_enum}")
                    if s.get("时间状态") not in contract_enum:
                        bad.append(f"{tag} 时间状态 {s.get('时间状态')!r} 越出 IC-10 契约枚举")
                    if s.get("状态注记") == "":
                        bad.append(f"{tag} 状态注记 为空串，应省略该字段")
        self.assertEqual(visited, set(sre_reasoner.ACTIVATION_TABLE), "矩阵遍历漏组")
        self.assertGreater(checked, len(visited) * len(LOCATIONS),
                           "矩阵未产出条目（空跑），字段断言无绑定力")
        self.assertEqual(bad, [], f"IC-10 条目字段越界 {len(bad)} 处：{bad[:6]}")

    def test_decision_trace_when_requested(self):
        r = reason("住宅", "分户墙", return_trace=True)
        self.assertIn("决策轨迹", r)
        self.assertIn("Step 0", r["决策轨迹"])
        self.assertIn("Step 6", r["决策轨迹"])

    def test_decision_trace_not_default(self):
        r = reason("住宅", "分户墙")
        self.assertNotIn("决策轨迹", r)


class TestSRRedlines(unittest.TestCase):
    """SR 引擎化红线 SR-R-P1-5 / P1-6 / P2-3。"""

    def test_evidence_objects_cover_required_types(self):
        r = reason("住宅", "分户墙", demands=["隔声"])
        types = {ev["证据类型"] for ev in r["证据对象"]}
        self.assertTrue(
            types >= {"classification", "activation", "mapping"},
            f"证据类型覆盖不足: {types}"
        )

    def test_evidence_rule_source_is_locatable(self):
        r = reason("住宅", "分户墙")
        for ev in r["证据对象"]:
            self.assertTrue(
                ev["规则来源"].startswith("standards-reasoning-rules.md §"),
                f"规则来源不可定位: {ev['规则来源']}"
            )

    def test_degradation_evidence_on_missing_location(self):
        r = reason("住宅", "分户墙")
        types = {ev["证据类型"] for ev in r["证据对象"]}
        self.assertIn("degradation", types)


class TestP0SceneExpectations(unittest.TestCase):
    """P0 断点 1-6 + P1 断点8 修复的场景级期望值断言（第三方审阅报告 §三 / §6.3）。

    每条均为「给定输入 → 断言输出标准集/字段」，这是报告指出当前完全缺失的一类机检；
    并各带负向半条防过度修正。回退对应修复后本组用例须复红（反向注入自证）。
    """

    # ---- 断点1：环保分支不可达（医院/学校键被「默认」拦截）----
    def test_BP1_hospital_school_environmental_branch_reachable(self):
        """医院/学校 environmental 须拿到 rules.md §3.2 该行全部三条标准。"""
        for pt, sp in (("医院", "病房"), ("医院", "诊疗室"), ("学校", "教室墙面")):
            stds = {s["标准编号"] for s in reason(pt, sp)["适用标准集"]}
            for need in ("GB 18580-2025", "GB 30981.1-2025", "T/CSUS 03-2019"):
                self.assertIn(need, stds, f"{pt}|{sp} 环保分支丢 {need}")

    def test_BP1_non_hospital_environmental_stays_default(self):
        """负向半条：住宅户内墙面 environmental 仍只走「默认」族，不得误得医院/学校专属三条。

        若把「医院/学校」分支提前时漏掉 project_type 限定，本用例转红。
        """
        stds = {s["标准编号"] for s in reason("住宅", "户内墙面")["适用标准集"]}
        self.assertIn("GB 18580-2025", stds)
        for forbidden in ("T/CSUS 03-2019", "GB 30981.1-2025"):
            self.assertNotIn(forbidden, stds, f"住宅场景误命中医院/学校专属族 {forbidden}")

    # ---- 断点2：地点入参归一化（北京市→北京）----
    def test_BP2_location_full_name_normalized(self):
        """「北京市/浙江省/广东省」全称须与 stripped 形态命中同一适用标准集。"""
        for full, stripped in (("北京市", "北京"), ("浙江省", "浙江"), ("广东省", "广东")):
            a = {s["标准编号"] for s in reason("住宅", "分户墙", location=full)["适用标准集"]}
            b = {s["标准编号"] for s in reason("住宅", "分户墙", location=stripped)["适用标准集"]}
            self.assertEqual(a, b, f"{full} 与 {stripped} 适用标准集不一致（归一化失效）")
        r = reason("住宅", "分户墙", location="北京市")
        db11 = [s for s in r["适用标准集"] if s["标准编号"] == "DB11/T 1553-2025"]
        self.assertTrue(db11, "北京市未命中 DB11/T 1553-2025")
        self.assertEqual(db11[0]["地域适用性"], "项目所在地适用")

    # ---- 断点3：DBJ 省码索引现读 ----
    @staticmethod
    def _index_region_truth() -> dict[str, str]:
        """索引 §5.3「适用地区」现读（编号→地区），独立于被测代码的解析路径。"""
        block = _section(_index_text() or "", r"^### 5\.3", (r"^### ", r"^## "))
        header, rows = _table(block)
        out: dict[str, str] = {}
        for row in rows:
            no = _cell(row, header, "标准编号")
            reg = _cell(row, header, "适用地区")
            if no and reg and not no.startswith("—"):
                out[no] = reg
        return out

    def test_BP3_dbj_province_resolved_from_index(self):
        """DB/DBJ 地方标准省份须从索引 §5.3「适用地区」现读，不落「待确认省份」。

        对引擎能分类为地方标准者断言。DBJ+省码数字形态（DBJ33/T 1327-2024）此前因
        `^DBJ\\b` 漏命中而落 unknown_type、被本用例跳过；该缺口已由遗留2（CG-20260917-003）
        闭合，DBJ33/T 现可分类，其省份（浙江）由本用例一并现读校验。
        """
        region_truth = self._index_region_truth()
        self.assertTrue(region_truth, "索引 §5.3 适用地区现读为空，判据不可用")
        checked = 0
        for no, reg in region_truth.items():
            if not no.startswith(("DB", "DBJ")):
                continue
            c = classify_standard(no, [])
            if c["标准类型"] == "unknown_type":
                continue
            self.assertEqual(c["地域范围"], reg, f"{no} 地域范围应为 {reg}（索引现读）")
            checked += 1
        self.assertGreaterEqual(checked, 2, "断点3 判据空跑：可分类的 DB/DBJ 条目不足")

    def test_BP3_dbj_applicable_in_own_province(self):
        """DBJ/T 15-208-2020 在广东场景须判「项目所在地适用」，不再误判「不适用仅作对比」。"""
        r = reason("住宅", "分户墙", location="广东")
        dbj15 = [s for s in r["适用标准集"] if s["标准编号"] == "DBJ/T 15-208-2020"]
        self.assertTrue(dbj15, "广东场景未命中 DBJ/T 15-208-2020")
        self.assertEqual(dbj15[0]["地域适用性"], "项目所在地适用")

    # ---- 断点4：场景未命中降级提示写入「未覆盖领域」----
    def test_BP4_unmatched_scene_surfaced_in_uncovered(self):
        # 断点6（CG-20260917-002）：原用例含 ("住宅","卫生间")，自 ACTIVATION_TABLE
        # 改由空间类型维度承载厨卫湿区后，卫生间经 SPACE_ALIASES→厨卫湿区 已命中，不再是
        # 未命中场景。换成真正无映射的 ("商业","地下车库")，保持本判据不空跑。
        for pt, sp in (("养老机构", "失智照护区"), ("商业", "地下车库"), ("其他", "特殊空间")):
            unc = reason(pt, sp).get("未覆盖领域")
            self.assertTrue(unc, f"{pt}|{sp} 未命中场景但「未覆盖领域」为空")
            self.assertTrue(any("场景未命中" in u for u in unc),
                            f"{pt}|{sp}「未覆盖领域」缺场景未命中降级提示: {unc}")

    def test_BP4_matched_scene_no_false_uncovered(self):
        """负向半条：命中激活表的场景不得误报「场景未命中」。"""
        unc = reason("住宅", "分户墙").get("未覆盖领域", [])
        self.assertFalse(any("场景未命中" in u for u in unc),
                         f"命中场景误报场景未命中: {unc}")

    # ---- 断点6：厨卫湿区归入空间类型维度（别名路径复活）----
    def test_BP6_wet_area_matches_via_space_dimension(self):
        """("住宅","卫生间")/("住宅","厨房") 经 SPACE_ALIASES→厨卫湿区 须命中激活表，不再降级。"""
        for sp in ("卫生间", "厨房", "厨卫湿区"):
            r = reason("住宅", sp)
            self.assertTrue(r["适用标准集"], f"住宅|{sp} 命中厨卫湿区行但适用标准集为空")
            unc = r.get("未覆盖领域", [])
            self.assertFalse(any("场景未命中" in u for u in unc),
                             f"住宅|{sp} 应经别名命中厨卫湿区，却报场景未命中: {unc}")

    def test_BP6_acoustic_demands_accepted(self):
        """吸声/撞击声 是 IC-10 性能需求枚举成员，须能激活且不崩溃（遗漏A：契约补枚举）。"""
        for d in ("吸声", "撞击声", "隔声"):
            r = reason("住宅", "楼板", demands=[d])
            self.assertIsInstance(r["适用标准集"], list, f"性能需求 {d} 激活异常")

    # ---- 断点5：外协占位改结构化「外部协同」字段对外可见 ----
    def test_BP5_external_collab_structured_visible(self):
        """住宅+厨卫湿区触发 waterproof(外部协同)，须出结构化「外部协同」条目而非被静默去重。"""
        r = reason("住宅", "卫生间")
        self.assertIn("外部协同", r, "厨卫湿区未产出结构化「外部协同」字段")
        collab = r["外部协同"]
        self.assertTrue(collab, "「外部协同」为空")
        item = collab[0]
        self.assertEqual(item["协同技能"], "waterproofing-expert")
        # 话术须与 interface-contracts.md 头部记录一致：已迁出至独立项目，非「待建」
        self.assertIn("已迁出", item["技能状态"],
                      f"外协话术与迁出记录矛盾（不得为「待建」）: {item['技能状态']}")
        self.assertNotIn("待建", item["技能状态"])
        # 占位「（…」不得再泄漏进对外标准编号集
        for s in r["适用标准集"]:
            self.assertFalse(s["标准编号"].startswith("（"),
                             f"外协占位泄漏进适用标准集: {s['标准编号']}")

    def test_BP5_external_collab_carries_degradation_evidence(self):
        """每条「外部协同」须附一条 degradation 证据（M6：外协不得静默）。

        判据须锁定外协专属证据（规则来源含「外部协同」），否则会被「地方标准不适用」
        那条无关 degradation 证据顶替而恒真（反向注入4 实测：松判据下回退仍绿＝空跑）。
        """
        r = reason("住宅", "卫生间")
        collab_ev = [ev for ev in r["证据对象"]
                     if ev["证据类型"] == "degradation" and "外部协同" in ev["规则来源"]]
        self.assertTrue(collab_ev, "外部协同缺专属 degradation 降级证据（规则来源未含「外部协同」）")
        self.assertIn("waterproofing-expert", collab_ev[0]["输出结论"])

    # ---- 断点8（P1·item11）：名称关键词不改 binding_support 角色 ----
    def test_BP8_name_keyword_does_not_override_binding_support_role(self):
        """断点8：L2/L3+binding_support 标准角色恒 design_basis，名称含「测量/评价」不改角色。

        第三方审阅 T-A1 曾推定「名称差分会改角色」，反事实实测推翻（CG-20260917-004）：名称关键词
        仅在 sre_reasoner.py:697（非 L1 且非 L2/L3-binding_support 支）参与 verification_reference 判定，
        而 DB/DBJ 设计验收类属 L2/binding_support，在 :695-696 即定 design_basis、永不到达 :697。
        正向对照：名称同样含「测量/评价」但非 L1、非 binding_support 者仍落 verification_reference，
        证明名称分支为活代码、只是被 binding_support 支遮蔽——本守卫因此非恒真空跑（回退分支序即转红）。
        """
        # 遮蔽组：L2/binding_support + 名称含「评价」→ 角色恒 design_basis（不被名称改写）
        # 第二成员由 DBJ/T 15-208-2020 换为 DBJ/T 13-428-2023（CG-20260917-008）：前者名称修正为
        # 「建筑室内装配式轻质隔墙技术规程」已不含「评价」，不再满足遮蔽组前置；后者名称含「评价」且 L2/binding_support。
        # 第一成员由 DB33/T 1168-2019 换为 DB33/T 1259-2021（CG-20260918-001）：前者名称修正为
        # 「装配式内装工程施工质量验收规范（浙江）」已不含「评价」；后者实测 L2/binding_support、名含「评价」。
        for no in ("DB33/T 1259-2021", "DBJ/T 13-428-2023"):
            c = classify_standard(no, [])
            self.assertEqual(c["层级"], "L2", f"{no} 应为 L2（断点7 裁定）")
            self.assertEqual(c["权限"], "binding_support", f"{no} 应为 binding_support")
            self.assertIn("评价", sre_reasoner._standard_name(no),
                          f"{no} 前置条件不成立：名称须含「评价」才能检验是否被改写")
            role = sre_reasoner._apply_applicability([c], None, [])[0]["角色"]
            self.assertEqual(role, "design_basis",
                             f"{no} 名称含「评价」但角色应恒 design_basis（binding_support 支遮蔽 :697 名称支）")
        # 正向对照：名称含测量/评价但未被 L1/binding_support 遮蔽 → verification_reference（名称支为活代码）
        control = []
        for k, v in sre_reasoner.STANDARD_NAMES.items():
            if "测量" not in v and "评价" not in v:
                continue
            c = classify_standard(k, [])
            if c["层级"] != "L1" and c["权限"] != "binding_support":
                control.append((k, c))
            if len(control) >= 3:
                break
        self.assertTrue(control, "断点8 对照空跑：无「名称含测量/评价且未被遮蔽」的标准，守卫失去证伪力")
        for no, c in control:
            role = sre_reasoner._apply_applicability([c], None, [])[0]["角色"]
            self.assertEqual(role, "verification_reference",
                             f"{no} 对照：名称含测量/评价且未被遮蔽 → 应 verification_reference（:697 活代码）")


class _SREDeterminismBase(unittest.TestCase):
    """体检用例基类：进入前对齐加载器，退出后就地回滚模块级字典。

    unittest 按类名字母序排，本组类名排在 `TestSRRedlines` 之前；加载器同时改写
    `STANDARD_STATUS` 与 `STANDARD_NAMES`（`setdefault` 支路），若留在原地会污染既有
    23 条用例——T-B1 的「导入态计数」尤其依赖未加载的初值。故 `tearDown` 用
    clear+update 原地回滚而不换引用，被测代码持有的仍是同一对象。
    """

    def setUp(self) -> None:
        self._saved_status = dict(sre_reasoner.STANDARD_STATUS)
        self._saved_names = dict(sre_reasoner.STANDARD_NAMES)
        self._saved_activation = dict(sre_reasoner.ACTIVATION_TABLE)
        sre_reasoner._load_standards_index()

    def tearDown(self) -> None:
        for target, saved in ((sre_reasoner.STANDARD_STATUS, self._saved_status),
                              (sre_reasoner.STANDARD_NAMES, self._saved_names),
                              (sre_reasoner.ACTIVATION_TABLE, self._saved_activation)):
            target.clear()
            target.update(saved)

    def _empty_families(self, response: dict) -> list[str]:
        """从证据对象里取输出空标准族的领域名。"""
        out = []
        for ev in response.get("证据对象", []):
            if ev.get("证据类型") != "mapping":
                continue
            match = re.match(r"^领域=([^,]+),", str(ev.get("输入事实", "")))
            if match and "标准族=[]" in str(ev.get("输出结论", "")):
                out.append(match.group(1))
        return out


class TestSREDeterminismStatic(_SREDeterminismBase):
    """SRE 确定性体检·行为组（静态可测子集）T-B1 / T-B3 / T-B4 / T-B7。

    判据源《文档目录/SRE确定性体检设计方案_v1.0.md》§3.2。**自退役规则（§6.2）**：用例转成
    unexpected success 即说明对应缺陷已由 `sre_reasoner.py` 真实修复——此时必须摘掉装饰器、
    把断言转为正向常态断言，**禁止回退或注释掉被测代码的修复来让用例重新"预期失败"**。
    unexpected success 会让 `wasSuccessful()` 返回 False、门禁 exit 1，这是设计意图而非故障。

    **挂档历史**：T-B1、T-B7 随 CG-20260916-003 修复摘档；T-B4 随 CG-20260916-004 修复摘档，
    判据同步收紧为「拆分主态精确属 IC-10 契约枚举 + 回串无损」（旧前缀口径作废）；T-B3 随
    CG-20260916-005 补录 `acoustic(贡献)`／`acoustic(吸声)`／`acoustic(边界提示)` 三键后摘档。
    本组四项现均为正向常态断言，**已无 `expectedFailure` 挂档项**。
    """

    def test_TB1_index_loaded_at_import(self):
        inside, outside = _main_guard_calls()
        loaded = len(sre_reasoner.STANDARD_STATUS)
        truth = _main_table_truth()
        _record_signal(
            "T-B1", "DEGRADE",
            f"库导入态 STANDARD_STATUS = {IMPORT_TIME_STATUS_COUNT} 条；显式调用加载器后 = {loaded} 条；"
            f"主表应载 {len(truth)} 条",
            "导入态 > 0 条，且与显式加载同输入同输出（两入口可达同一加载器）",
            _loc(("STANDARD_STATUS 定义", _assign_line("STANDARD_STATUS")),
                 ("_load_standards_index 定义", _def_line("_load_standards_index")),
                 ("__main__ 守卫内调用", inside[0] if inside else None),
                 ("导入期调用", outside[0] if outside else None)),
            "S", touched=set(truth),
            violated=IMPORT_TIME_STATUS_COUNT <= 0 or IMPORT_TIME_STATUS_COUNT != loaded,
        )
        self.assertGreater(IMPORT_TIME_STATUS_COUNT, 0, "库导入态标准状态表为空")
        self.assertEqual(IMPORT_TIME_STATUS_COUNT, loaded,
                         "导入态与显式加载态不同：两入口口径已分叉")

    def test_TB3_activated_domains_have_mapping(self):
        dead = sorted(_reachable_domains() - set(sre_reasoner.DOMAINS))
        redundant = sorted(set(sre_reasoner.DOMAINS) - _reachable_domains())
        _record_signal(
            "T-B3", "GAP",
            f"死链域 {len(dead)} 个：{dead}；冗余域 {len(redundant)} 个：{redundant}",
            "激活可达域 ⊆ DOMAINS 键集，且无不可达冗余键",
            _loc(("DOMAINS 定义", _assign_line("DOMAINS")),
                 ("ACTIVATION_TABLE 定义", _assign_line("ACTIVATION_TABLE")),
                 ("ACOUSTIC_SUBDOMAINS 定义", _assign_line("ACOUSTIC_SUBDOMAINS")),
                 ("_map_domain_to_standards 定义", _def_line("_map_domain_to_standards"))),
            "B", violated=bool(dead or redundant),
        )
        self.assertEqual(dead, [], "存在被激活却无映射的死链域")
        self.assertEqual(redundant, [], "存在不可达的冗余映射键")

    def test_TB4_status_read_by_header(self):
        truth = _main_table_truth()
        enum = _status_enum()
        contract_enum = _contract_ic10_status_enum()
        mismatch = {k: (sre_reasoner.STANDARD_STATUS.get(k), v)
                    for k, v in truth.items() if sre_reasoner.STANDARD_STATUS.get(k) != v}
        # CG-20260916-004 起收紧：状态单元按 §4 契约口径拆为（主态, 括注），主态须**精确**属
        # IC-10 枚举成员（前缀口径作废——它曾把「现行有效（…）」整串放行到输出侧）
        out_of_enum = {k: v for k, v in sre_reasoner.STANDARD_STATUS.items()
                       if sre_reasoner._split_status(v)[0] not in contract_enum}
        # 拆分必须无损：主态 + 括注要能原样回串为索引状态单元，否则「已废止（无替代）」类限定丢失
        lossy = {k: v for k, v in sre_reasoner.STANDARD_STATUS.items()
                 if (lambda h, t: h + (f"（{t}）" if t else ""))(*sre_reasoner._split_status(v)) != v}
        # 两状态独立表达：被部分替代不得被改写为过渡期（真值注入法，不依赖列位缺陷是否已修）
        conversion = ""
        if "被部分替代" in enum and "过渡期" in enum:
            ids = sorted(_applicable_ids("住宅", "分户墙", None, "浙江"))
            if ids:
                sre_reasoner.STANDARD_STATUS[ids[0]] = "被部分替代"
                after = next((i for i in reason("住宅", "分户墙", location="浙江")["适用标准集"]
                              if i["标准编号"] == ids[0]), None)
                conversion = f"{ids[0]}：注入 被部分替代 → 输出 {after and after['时间状态']!r}"
        conversion_bad = bool(conversion) and "→ 输出 '被部分替代'" not in conversion
        _record_signal(
            "T-B4", "DRIFT",
            f"主表 {len(truth)} 项中 {len(mismatch)} 项加载态≠表头真值；加载表 {len(sre_reasoner.STANDARD_STATUS)} 项中"
            f"拆分主态越出契约枚举 {len(out_of_enum)} 项、有损拆分 {len(lossy)} 项；"
            f"{conversion or '状态转换项未测（枚举已改名）'}",
            "状态一律按表头列名读取（与表头感知真值同值）、拆分主态精确属 IC-10 契约枚举且回串无损，"
            "且 被部分替代 与 过渡期 不得互转",
            _loc(("固定列号取值", _assign_line("STANDARD_STATUS")),
                 ("_load_standards_index 定义", _def_line("_load_standards_index")),
                 ("时间状态拆分", _def_line("_split_status")),
                 ("时间状态映射", _def_line("_apply_applicability"))),
            "A", touched=set(mismatch) | set(out_of_enum) | set(lossy),
            violated=bool(mismatch or out_of_enum or lossy or conversion_bad),
        )
        self.assertEqual(mismatch, {}, "加载状态与表头真值不一致")
        self.assertGreater(len(contract_enum), 0, "IC-10 契约枚举现读为空，越界判据不可用")
        self.assertTrue(_contract_declares("状态注记"),
                        "引擎可输出 状态注记，但 IC-10 Schema 未声明该字段")
        self.assertEqual(out_of_enum, {}, "拆分主态越出 IC-10 契约枚举")
        self.assertEqual(lossy, {}, "状态单元拆分有损，括注限定被丢弃")
        if conversion:
            self.assertFalse(conversion_bad, f"{conversion}｜被部分替代 被方向性改写为 过渡期")

    def test_TB7_withdrawn_standards_reachable(self):
        registered = _deprecated_rows()
        missing = sorted(i for i, _ in registered if i and i not in sre_reasoner.STANDARD_STATUS)
        _record_signal(
            "T-B7", "GAP",
            f"§7.2 登记 {len(registered)} 行，其中 {len(missing)} 行未被加载器载入：{missing}",
            "§7.2 全部登记行可达（其已废止/被部分替代状态可被推理链读到）",
            _loc(("表类型守卫", _def_line("_load_standards_index")),
                 ("STANDARD_STATUS 定义", _assign_line("STANDARD_STATUS"))),
            "S", touched=set(missing), violated=bool(missing),
        )
        self.assertEqual(missing, [], "§7.2 已废止/被部分替代登记对推理链不可达")


class TestSREDeterminismBehavior(_SREDeterminismBase):
    """SRE 确定性体检·行为组（推理运行子集）T-B2 / T-B5 / T-B6。

    自退役规则同 `TestSREDeterminismStatic`（设计方案 §6.2）：unexpected success =
    修复已落地，须摘装饰器转正向断言，**禁止回退 `sre_reasoner.py` 的修复**。

    **挂档历史**：T-B2 随 CG-20260916-004 摘档，并加正向断言「降级值须属 IC-10 契约取值集且
    全表唯一」；T-B5、T-B6 随 CG-20260916-005 摘档（空族上报 / 全国基线可叠加），各加一条收紧
    用例——T-B5 负向路径（合成无映射域）、T-B6 逐地点正向期望集。**行为组 T-B1—T-B7 七项至此
    全部转正，本文件不再挂 `expectedFailure`。**
    """

    def test_TB2_unregistered_id_degrades(self):
        enum = _status_enum()
        contract_enum = _contract_ic10_status_enum()
        probes = [_probe_id() for _ in range(5)]
        got = {p: sre_reasoner._standard_status(p) for p in probes}
        # 导入态（setUp 快照、未经显式加载）下锚定编号走同一兜底分支——§9.1 判该行为 anchor=true 的来源
        anchors, _ = _anchor_ids()
        sre_reasoner.STANDARD_STATUS.clear()
        sre_reasoner.STANDARD_STATUS.update(self._saved_status)
        anchor_fallback = sorted(a for a in (anchors or set()) if a not in self._saved_status)
        got |= {a: sre_reasoner._standard_status(a) for a in anchor_fallback}
        deterministic = {k: v for k, v in got.items() if v in enum}
        degraded = {k: v for k, v in got.items() if v not in enum}
        off_contract = {k: v for k, v in degraded.items() if v not in contract_enum}
        _record_signal(
            "T-B2", "DEGRADE",
            f"{len(probes)} 个随机不存在编号 + 导入态未登记的锚定编号 {len(anchor_fallback)} 条，"
            f"经 _standard_status() 得确定性状态 {len(deterministic)} 项："
            f"{sorted(set(deterministic.values()))}（触 SR-R-P0-2）；"
            f"降级侧 {len(degraded)} 项取值 {sorted(set(degraded.values()))}，越出契约取值集 {len(off_contract)} 项",
            "未登记编号不得返回任何 §1.1 确定性状态，须落 IC-10 契约取值集内的显式降级态且全表同值",
            _loc(("_standard_status 定义（含兜底默认值）", _def_line("_standard_status"))),
            "S", touched=set(deterministic) | set(off_contract),
            violated=bool(deterministic or off_contract),
        )
        self.assertEqual(deterministic, {}, "对输入域无界的未登记编号给出了确定性状态")
        self.assertGreater(len(contract_enum), 0, "IC-10 契约枚举现读为空，降级判据不可用")
        self.assertEqual(off_contract, {}, "降级输出越出 IC-10 契约取值集（臆造词）")
        self.assertEqual(len(set(degraded.values())), 1, "兜底返回值不唯一，降级口径不确定")

    def test_TB5_empty_family_reported_uncovered(self):
        silent: list[tuple[str, str, str | None, str]] = []
        for project_type, space_type, demands in SCENARIOS:
            for location in LOCATIONS:
                response = reason(project_type, space_type, location=location, demands=demands)
                uncovered = response.get("未覆盖领域", [])
                for domain in self._empty_families(response):
                    if not any(domain in u or domain.split("(")[0] in u for u in uncovered):
                        silent.append((project_type, space_type, location, domain))
        _record_signal(
            "T-B5", "DEGRADE",
            f"空标准族输出 {len(silent)} 处未上报未覆盖领域（静默）：{silent[:6]}"
            + ("…" if len(silent) > 6 else ""),
            "凡 标准族=[] 的映射证据，其领域必须出现在 未覆盖领域 中（LA-4/M 系列降级可见）",
            _loc(("_map_domain_to_standards 定义", _def_line("_map_domain_to_standards")),
                 ("reason 定义", _def_line("reason"))),
            "B", violated=bool(silent),
        )
        self.assertEqual(silent, [], "空标准族被静默吞掉")

    def test_TB5_empty_family_negative_path(self):
        """摘档收紧：矩阵内已无空族，故合成注入无映射域，验上报机制而非数据碰巧非空。"""
        fake = "synthetic(无映射域)"
        self.assertNotIn(fake, sre_reasoner.DOMAINS, "合成域撞上真实映射键，负向取证失效")
        project_type, space_type = next(iter(sre_reasoner.ACTIVATION_TABLE))
        sre_reasoner.ACTIVATION_TABLE[project_type, space_type] = list(
            sre_reasoner.ACTIVATION_TABLE[project_type, space_type]) + [fake]
        response = reason(project_type, space_type, location="全国")
        empty = self._empty_families(response)
        uncovered = response.get("未覆盖领域", [])
        self.assertIn(fake, empty, "合成域未产出空标准族，负向路径根本没走到")
        self.assertTrue(any(fake in u for u in uncovered),
                        f"空标准族未上报 未覆盖领域：{uncovered}")

    def test_TB6_national_baseline_additive(self):
        lost: list[tuple[str | None, str]] = []
        for project_type, space_type, demands in SCENARIOS:
            per_location = {loc: _applicable_ids(project_type, space_type, demands, loc)
                            for loc in LOCATIONS}
            union = set().union(*per_location.values())
            national = {i for i in union if not i.startswith(LOCAL_ID_PREFIXES)}
            for loc, ids in per_location.items():
                lost += [(loc, i) for i in sorted(national - ids)]
        _record_signal(
            "T-B6", "DEGRADE",
            f"全国层级标准随地点丢失 {len(lost)} 处：{lost[:8]}" + ("…" if len(lost) > 8 else ""),
            "全国基线对全部地点（含未提供地点）可叠加，地方标准作增量而非互斥分支",
            _loc(("_map_domain_to_standards 定义", _def_line("_map_domain_to_standards"))),
            "A", touched={i for _, i in lost}, violated=bool(lost),
        )
        self.assertEqual(lost, [], "地点分支抑制了全国层级标准的输出")

    def test_TB6_national_and_local_coexist(self):
        """摘档收紧：从「跨地点差分」升级为「逐地点正向期望集」，期望值取自 DOMAINS 现读。"""
        missing: list[tuple[str, str, str]] = []
        for project_type, space_type, demands in SCENARIOS:
            activated, _ = sre_reasoner._activate_domains(
                project_type, space_type, demands, [])
            for location in [x for x in LOCATIONS if x]:
                got = _applicable_ids(project_type, space_type, demands, location)
                for domain in sorted(activated):
                    mapping = sre_reasoner.DOMAINS.get(domain, {})
                    expect = set(mapping.get("全国", [])) | set(mapping.get(location, []))
                    missing += [(domain, location, i) for i in sorted(
                        {e for e in expect - got if not e.startswith("（")})]
        self.assertEqual(missing, [],
                         f"全国基线或地方增量在该地点缺失：{missing[:6]}")


class TestVerificationLedger(unittest.TestCase):
    """独立读取台账并以测试注入验证边界，不替代内容核验或业务裁定。"""

    def _at_date(self, when):
        return mock.patch.object(sre_reasoner, "datetime", wraps=datetime,
                                 **{"now.return_value": when})

    def _assert_review_reminder(self, response, std_no, confidence="deterministic"):
        apps = _applicability_evidence(response, std_no)
        self.assertEqual(len(apps), 1, f"{std_no} 必须复用唯一的 applicability 证据")
        self.assertEqual(apps[0]["置信度"], confidence)
        self.assertIn("复查提醒", apps[0]["输出结论"])
        self.assertIn("§1.1A", apps[0]["规则来源"])
        self.assertEqual(_verify_degradation_evidence(response, std_no), [],
                         "到期提醒不得冒充 §1.1A 未确认降级")
        return apps[0]

    def _matrix(self):
        for project_type, space_type, demands in SCENARIOS:
            for location in LOCATIONS:
                response = reason(project_type, space_type, location=location,
                                  demands=demands, system="轻钢龙骨")
                yield (project_type, space_type, location), response

    def test_verify_ledger_matches_index_by_column_name(self):
        """机检层：运行时台账与测试侧独立现读同集同值，且无散文假键。"""
        truth = _verify_table_truth()
        ledger = sre_reasoner.STANDARD_VERIFY
        self.assertGreaterEqual(len(truth), 1, "索引核验记录表现读为空，交叉核对失去基准")
        self.assertEqual(set(ledger), set(truth),
                         f"台账键集与索引现读不一致：多 {sorted(set(ledger) - set(truth))[:5]}／"
                         f"少 {sorted(set(truth) - set(ledger))[:5]}")
        diff = [(k, ledger[k], truth[k]) for k in truth if ledger[k] != truth[k]]
        self.assertEqual(diff, [], f"台账值与索引现读不等 {len(diff)} 处：{diff[:3]}")
        prose = [k for k in ledger if re.search(r"[\u4e00-\u9fff]", k)]
        self.assertEqual(prose, [],
                         f"散文单元被当成编号读入台账（应按列名取值并剔除兜底单元）：{prose[:5]}")
        self.assertNotIn("已官方核验", ledger,
                         "§1.1A 核验状态枚举定义表被当成台账读入（该表无编号列，应被排除）")

    def test_verify_state_column_only_table_is_load_bearing(self):
        """仅有状态列的表仍须入账；状态否决不因日期越过复查阈值而空跑。"""
        truth = _verify_table_truth()
        strict = _verify_table_truth(require_both=True)
        lost = sorted(k for k in truth
                      if truth[k]["核验状态"] and not strict.get(k, {}).get("核验状态"))
        self.assertTrue(lost, "或语义空跑：无「仅靠核验状态列入账」的编号，收紧判据不会丢数据")
        veto = []
        for std_no, rec in sre_reasoner.STANDARD_VERIFY.items():
            state, date = rec.get("核验状态"), rec.get("核验日期")
            if (not state or not date or state == "到期需复核"
                    or state.startswith(sre_reasoner.CONFIRMED_VERIFY_PREFIX)):
                continue
            veto.append(std_no)
        self.assertTrue(veto, "状态否决支空跑：无「有日期但核验状态须否决」的编号")
        for std_no in veto:
            ok, why = sre_reasoner._verification_confirmed(
                std_no, classify_standard(std_no, [])["层级"])
            self.assertFalse(ok, f"{std_no} 核验状态须否决，日期到期也不得放行")
            self.assertIn("核验状态", why, f"{std_no} 未确认事由应指向核验状态，实测：{why}")

    def test_missing_verify_date_is_unconfirmed_not_review_due(self):
        """缺日期意味着依据未确认，不等同已有核验记录到期复查。"""
        main_ids = set(_main_table_truth())
        no_record = sorted(main_ids - set(sre_reasoner.STANDARD_VERIFY))
        self.assertTrue(no_record, "空跑：主表编号已全部有核验记录，无日期支不可达")
        for std_no in no_record[:6]:
            ok, why = sre_reasoner._verification_confirmed(
                std_no, classify_standard(std_no, [])["层级"])
            self.assertFalse(ok, f"{std_no} 索引无核验记录却按已确认放行")
            self.assertIn("无「核验日期」", why, f"{std_no} 应明记缺少依据日期，实测：{why}")
        for std_no, rec in sre_reasoner.STANDARD_VERIFY.items():
            if rec.get("核验状态") and not rec.get("核验日期"):
                ok, why = sre_reasoner._verification_confirmed(
                    std_no, classify_standard(std_no, [])["层级"])
                self.assertFalse(ok, f"{std_no} 有状态无日期，仍应未确认：{why}")

    def test_verify_threshold_is_tiered_and_strict(self):
        """六组合成日期：分档阈值只决定复查提醒，不否定既有核验依据。"""
        fake = "GB/T 99999-2020"
        self.assertNotIn(fake, sre_reasoner.STANDARD_VERIFY, "合成编号撞上真实台账，注入失效")
        l1, other = sre_reasoner.REVIEW_DAYS_L1, sre_reasoner.REVIEW_DAYS_OTHER
        self.assertLess(l1, other, "强制性国标复查周期应短于其余标准")
        today = datetime(2026, 9, 18)
        cases = [
            ("L1", l1, False), ("L1", l1 + 1, True),
            ("L2", other, False), ("L2", other + 1, True),
            ("L1", other, True), ("L2", l1 + 1, False),
        ]
        with self._at_date(today):
            for level, age, expect_reminder in cases:
                date = (today - timedelta(days=age)).strftime("%Y-%m-%d")
                # 合成台账仅用于测试；无状态列的既有日期记录也应受同一阈值约束。
                record = {"核验日期": date, "核验状态": None}
                with self.subTest(level=level, age=age), mock.patch.dict(
                        sre_reasoner.STANDARD_VERIFY, {fake: record}):
                    ok, why = sre_reasoner._verification_confirmed(fake, level)
                    self.assertTrue(ok, f"{level} 档 age={age} 天不得仅因到期否定依据：{why}")
                    if expect_reminder:
                        self.assertIn("复查提醒", why)
                    else:
                        self.assertEqual(why, "", "阈值当日及此前不得出现复查提醒")

    def test_real_gb55037_review_boundary_preserves_items_and_certainty(self):
        """真实台账日期加阈值定位边界（本批 2026-09-17/18），不改记录、不读今天。"""
        std_no = "GB 55037-2022"
        record = _verify_table_truth()[std_no]
        self.assertEqual(sre_reasoner.STANDARD_VERIFY[std_no], record)
        self.assertTrue(record["核验状态"].startswith(sre_reasoner.CONFIRMED_VERIFY_PREFIX))
        verified_at = datetime.strptime(record["核验日期"], "%Y-%m-%d")
        level = classify_standard(std_no, [])["层级"]
        boundary = verified_at + timedelta(days=_verify_tier(level))
        responses = []
        for days_after in (0, 1):
            with self._at_date(boundary + timedelta(days=days_after)):
                ok, why = sre_reasoner._verification_confirmed(std_no, level)
                self.assertTrue(ok, why)
                response = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
                responses.append(response)
                apps = _applicability_evidence(response, std_no)
                self.assertEqual(len(apps), 1)
                self.assertEqual(apps[0]["置信度"], "deterministic")
                self.assertEqual(_verify_degradation_evidence(response, std_no), [])
                if days_after:
                    self.assertIn("复查提醒", why)
                    self._assert_review_reminder(response, std_no)
                else:
                    self.assertEqual(why, "")
                    self.assertNotIn("复查提醒", apps[0]["输出结论"])
        self.assertEqual(responses[0]["适用标准集"], responses[1]["适用标准集"],
                         "跨复查边界不得改变任何 item 字段、集合成员或排序")
        before = _applicability_evidence(responses[0], std_no)[0]
        after = _applicability_evidence(responses[1], std_no)[0]
        self.assertIn(before["输出结论"], after["输出结论"], "提醒须追加而非覆盖原适用性结论")
        self.assertEqual(sre_reasoner.STANDARD_VERIFY[std_no], record, "推理不得刷新核验日期")

    def test_explicit_review_due_requires_existing_valid_date_and_reminds(self):
        """仅合成「到期需复核」状态，日期沿用真实台账；未过阈值也须提醒。"""
        std_no = "GB 55037-2022"
        record = _verify_table_truth()[std_no]
        verified_at = datetime.strptime(record["核验日期"], "%Y-%m-%d")
        level = classify_standard(std_no, [])["层级"]
        due_record = dict(record, 核验状态="到期需复核")
        for age in (1, _verify_tier(level) + 1):
            with self.subTest(age=age), self._at_date(verified_at + timedelta(days=age)):
                baseline = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
                with mock.patch.dict(sre_reasoner.STANDARD_VERIFY, {std_no: due_record}):
                    ok, why = sre_reasoner._verification_confirmed(std_no, level)
                    self.assertTrue(ok, why)
                    self.assertIn("复查提醒", why)
                    response = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
                    self._assert_review_reminder(response, std_no)
                    self.assertEqual(response["适用标准集"], baseline["适用标准集"])
                    self.assertEqual(sre_reasoner.STANDARD_VERIFY[std_no], due_record)
        self.assertEqual(sre_reasoner.STANDARD_VERIFY[std_no], record)

    def test_synthetic_unconfirmed_states_and_invalid_dates_stay_false(self):
        """合成输入只供测试：状态否决、缺失／无效／未来日期不能被提醒通道放行。"""
        fake = "GB/T 99999-2020"
        self.assertNotIn(fake, sre_reasoner.STANDARD_VERIFY)
        today = datetime(2026, 9, 18)
        veto_states = ("待核验", "无法核验", "条文级核验（S1）", "条文级核验（S1-）",
                       "到期需复核（测试非精确状态）")
        cases = [(state, date) for state in veto_states
                 for date in ("2026-09-17", "2000-01-01")]
        cases += [(state, date)
                  for state in (sre_reasoner.CONFIRMED_VERIFY_PREFIX, "到期需复核")
                  for date in (None, "", "测试无效日期", "2026-02-30", "2026-09-19")]
        with self._at_date(today):
            for state, date in cases:
                record = {"核验日期": date, "核验状态": state}
                with self.subTest(state=state, date=date), mock.patch.dict(
                        sre_reasoner.STANDARD_VERIFY, {fake: record}):
                    ok, why = sre_reasoner._verification_confirmed(fake, "L2")
                    self.assertFalse(ok, f"合成未确认记录被放行：{record}，{why}")
                    self.assertTrue(why, "未确认必须说明事由")
                    self.assertNotIn("复查提醒", why, "未确认不能仅报复查提醒")
                    if state in veto_states:
                        self.assertIn("核验状态", why)

    def test_synthetic_state_veto_and_missing_record_degrade_end_to_end(self):
        """合成台账端到端反证：状态否决及无记录仍 inferred，且有专属降级证据。"""
        std_no = "GB 55037-2022"
        record = _verify_table_truth()[std_no]
        cases = [(state, dict(record, 核验状态=state))
                 for state in ("待核验", "无法核验", "条文级核验（S1）", "条文级核验（S1-）")]
        cases += [("无记录", None), ("缺日期", dict(record, 核验日期=None))]
        with self._at_date(datetime(2026, 9, 18)):
            baseline = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
            for label, injected in cases:
                ledger = {k: v for k, v in sre_reasoner.STANDARD_VERIFY.items() if k != std_no}
                if injected is not None:
                    ledger[std_no] = injected
                with self.subTest(case=label), mock.patch.dict(
                        sre_reasoner.STANDARD_VERIFY, ledger, clear=True):
                    response = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
                    self.assertEqual(response["适用标准集"], baseline["适用标准集"])
                    apps = _applicability_evidence(response, std_no)
                    self.assertEqual(len(apps), 1)
                    self.assertEqual(apps[0]["置信度"], "inferred")
                    self.assertNotIn("复查提醒", apps[0]["输出结论"])
                    degs = _verify_degradation_evidence(response, std_no)
                    self.assertEqual(len(degs), 1, "缺少 §1.1A 专属降级，其他降级不能顶替")
                    self.assertEqual(degs[0]["置信度"], "inferred")
                    self.assertIn("出口二选一", degs[0]["输出结论"])
                    cause = "无「核验日期」" if label in ("无记录", "缺日期") else "核验状态"
                    self.assertIn(cause, degs[0]["输入事实"])

    def test_old_verification_does_not_promote_unknown_status(self):
        """测试合成未知状态与很旧日期：提醒不抹除 SR-R-P0-2 未知状态降级。"""
        std_no = "GB 55037-2022"
        old_record = dict(_verify_table_truth()[std_no], 核验日期="2000-01-01")
        with self._at_date(datetime(2026, 9, 18)), mock.patch.dict(
                sre_reasoner.STANDARD_STATUS, {std_no: sre_reasoner.STATUS_UNKNOWN}):
            baseline = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
            with mock.patch.dict(sre_reasoner.STANDARD_VERIFY, {std_no: old_record}):
                response = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
                self.assertEqual(response["适用标准集"], baseline["适用标准集"])
                items = [i for i in response["适用标准集"] if i["标准编号"] == std_no]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["时间状态"], sre_reasoner.STATUS_UNKNOWN)
                self._assert_review_reminder(response, std_no, confidence="inferred")
                degs = [ev for ev in response["证据对象"]
                        if ev["证据类型"] == "degradation"
                        and "SR-R-P0-2" in ev["规则来源"]
                        and ev["输入事实"].startswith(f"标准={std_no} ")]
                self.assertEqual(len(degs), 1, "复查提醒不能抹除未知状态专属降级")
                self.assertEqual(degs[0]["置信度"], "inferred")
                self.assertIn("时间状态=未知", degs[0]["输出结论"])

    def test_old_verification_does_not_revive_withdrawn_status(self):
        """测试合成「已废止（无替代）」：确认依据不是确认现行，也不得丢括注。"""
        std_no = "GB 55037-2022"
        old_record = dict(_verify_table_truth()[std_no], 核验日期="2000-01-01")
        with self._at_date(datetime(2026, 9, 18)), mock.patch.dict(
                sre_reasoner.STANDARD_STATUS, {std_no: "已废止（无替代）"}):
            baseline = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
            with mock.patch.dict(sre_reasoner.STANDARD_VERIFY, {std_no: old_record}):
                response = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
                self.assertEqual(response["适用标准集"], baseline["适用标准集"])
                items = [i for i in response["适用标准集"] if i["标准编号"] == std_no]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["时间状态"], "已废止")
                self.assertEqual(items[0]["状态注记"], "无替代")
                app = self._assert_review_reminder(response, std_no, confidence="inferred")
                self.assertIn("时间状态=已废止", app["输出结论"])
                for result in (baseline, response):
                    self.assertEqual(_applicability_evidence(result, std_no)[0]["置信度"], "inferred")
                    degs = [ev for ev in result["证据对象"]
                            if ev["证据类型"] == "degradation" and "TA-3" in ev["规则来源"]
                            and ev["输入事实"].startswith(f"标准={std_no} ")]
                    self.assertEqual(len(degs), 1, "废止保护不能依赖日期超期的通用降级")
                    self.assertEqual(degs[0]["置信度"], "inferred")
                    self.assertIn("不得作为现行合规判据", degs[0]["输出结论"])
                    self.assertIn("咨询主管部门", degs[0]["输出结论"])

    def test_old_verification_keeps_partial_replacement_warning_and_degradation(self):
        """GB 50118 真实部分替代状态＋测试合成旧日期：替代警告与 TA-2 降级仍在。"""
        std_no = "GB 50118-2010"
        self.assertEqual(_main_table_truth()[std_no], "被部分替代")
        old_record = dict(_verify_table_truth()[std_no], 核验日期="2000-01-01")
        with self._at_date(datetime(2026, 9, 18)):
            baseline = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
            with mock.patch.dict(sre_reasoner.STANDARD_VERIFY, {std_no: old_record}):
                response = reason("住宅", "分户墙", location="全国", system="轻钢龙骨")
                self.assertEqual(response["适用标准集"], baseline["适用标准集"])
                items = [i for i in response["适用标准集"] if i["标准编号"] == std_no]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["时间状态"], "被部分替代")
                self.assertTrue(items[0].get("替代警告"))
                self.assertEqual(items[0]["替代警告"],
                                 sre_reasoner.PARTIAL_REPLACEMENT_NOTICES[std_no])
                self._assert_review_reminder(response, std_no)
                notices = []
                for result in (baseline, response):
                    degs = [ev for ev in result["证据对象"]
                            if ev["证据类型"] == "degradation" and "TA-2" in ev["规则来源"]
                            and ev["输入事实"].startswith(f"标准={std_no} ")]
                    self.assertEqual(len(degs), 1, "到期不应移除部分替代的 TA-2 降级证据")
                    notices.append([(ev["输入事实"], ev["输出结论"], ev["规则来源"], ev["置信度"])
                                    for ev in degs])
                self.assertEqual(notices[0], notices[1])

    def test_unconfirmed_verification_degrades_to_inferred_with_m6_evidence(self):
        """端到端：未确认者 applicability 证据降 inferred ＋ 一条 §1.1A 专属 degradation 证据。"""
        seen, bad = 0, []
        for (project_type, space_type, location), response in self._matrix():
            for item in response["适用标准集"]:
                std_no = item["标准编号"]
                if sre_reasoner._verification_confirmed(std_no, item["层级"])[0]:
                    continue
                seen += 1
                tag = f"{project_type}/{space_type}/{location} {std_no}"
                apps = _applicability_evidence(response, std_no)
                if not apps:
                    bad.append(f"{tag} 无 applicability 证据")
                elif apps[0]["置信度"] != "inferred":
                    bad.append(f"{tag} 置信度仍为 {apps[0]['置信度']!r}，未随核验时效降级")
                degs = _verify_degradation_evidence(response, std_no)
                if not degs:
                    bad.append(f"{tag} 缺 §1.1A 核验时效专属 degradation 证据")
                elif "出口二选一" not in degs[0]["输出结论"]:
                    bad.append(f"{tag} 降级证据未明文记账等价出口：{degs[0]['输出结论'][:40]}")
        self.assertGreater(seen, 0, "空跑：矩阵内无未确认编号，降级断言无绑定力")
        self.assertEqual(bad, [], f"核验时效降级未生效 {len(bad)} 处：{bad[:6]}")

    def test_confirmed_verification_keeps_deterministic(self):
        """对称面：已确认且无其他降级因由者仍为 deterministic——防"无条件降级"式假修。"""
        seen, bad = 0, []
        for (project_type, space_type, location), response in self._matrix():
            for item in response["适用标准集"]:
                std_no = item["标准编号"]
                if not sre_reasoner._verification_confirmed(std_no, item["层级"])[0]:
                    continue
                if (item["地域适用性"] == "待确认"
                        or item["时间状态"] in (sre_reasoner.STATUS_UNKNOWN, "已废止")):
                    continue  # 另有降级因由，不在本守卫判据面内
                seen += 1
                tag = f"{project_type}/{space_type}/{location} {std_no}"
                apps = _applicability_evidence(response, std_no)
                if not apps:
                    bad.append(f"{tag} 无 applicability 证据")
                elif apps[0]["置信度"] != "deterministic":
                    bad.append(f"{tag} 依据已确认，置信度应 deterministic，实测 {apps[0]['置信度']!r}")
                if _verify_degradation_evidence(response, std_no):
                    bad.append(f"{tag} 已确认却仍带核验专属降级证据")
        self.assertGreater(seen, 0, "空跑：矩阵内无已确认编号，对称面断言无绑定力")
        self.assertEqual(bad, [], f"已确认标准被误降 {len(bad)} 处：{bad[:6]}")

    def test_verification_degradation_does_not_change_membership(self):
        """全矩阵：核验降级只动确定性与证据，**不得改变适用标准集成员**（预审最小成功标准）。"""
        baseline: dict[tuple, set[str]] = {}
        for project_type, space_type in sre_reasoner.ACTIVATION_TABLE:
            for location in LOCATIONS:
                baseline[(project_type, space_type, location)] = _applicable_ids(
                    project_type, space_type, [], location)
        diffs, total = [], 0
        with mock.patch.object(sre_reasoner, "_verification_confirmed", return_value=(True, "")):
            for key, ids in baseline.items():
                total += len(ids)
                now = _applicable_ids(key[0], key[1], [], key[2])
                if now != ids:
                    diffs.append((key, sorted(ids ^ now)))
        self.assertGreater(total, 0, "空跑：全矩阵未产出条目")
        self.assertEqual(diffs, [], f"核验降级改变了适用标准集成员 {len(diffs)} 处：{diffs[:3]}")

    def test_no_new_field_or_enum_for_verification(self):
        """提醒及降级仅走既有证据字段，IC-10 item 与置信度枚举不扩展。"""
        for field in ("核验状态", "核验日期", "核验时效", "核验确认"):
            self.assertFalse(_contract_declares(field),
                             f"IC-10 契约出现核验专属字段 {field}：属契约不兼容变更，须升版另批")
        checked, bad = 0, []
        for _, response in self._matrix():
            for item in response["适用标准集"]:
                checked += 1
                bad += [f"{item['标准编号']} item 含核验专属键 {k}" for k in item if "核验" in k]
            for ev in response["证据对象"]:
                if ev["证据类型"] in ("applicability", "degradation") \
                        and ev["置信度"] not in ("deterministic", "inferred"):
                    bad.append(f"{ev['证据ID']} 置信度 {ev['置信度']!r} 越既有两值域（新造枚举）")
        self.assertGreater(checked, 0, "空跑：矩阵未产出条目")
        self.assertEqual(bad, [], f"核验降级新造字段或枚举 {len(bad)} 处：{bad[:6]}")


class TestNameSotPriority(unittest.TestCase):
    """PL-002／设计方案 §9.2 共通动作：名称取数优先序＝索引主表 > 登记表 > 硬编码预置。

    旧缺陷（B8 族）：`_load_standards_index` 名称支路原为 `STANDARD_NAMES.setdefault`，
    模块级硬编码预置（sre_reasoner :231 起）对同键主表名称形成遮蔽——索引改名后运行时
    静默沿用旧名，T-A1 比对面只能事后转红。注入取合成索引：临时替换 `sre_reasoner.__file__`
    使 `_load_standards_index` 读临时副本，不落盘治理件、不改运行时索引；
    tearDown 回滚 `__file__` 与模块级字典。临时面用 tempfile 自清，不依赖任何工作区路径
    （本件属运行时技能，须宿主无关）。
    """

    _KEY = "GB 50118-2010"
    _KEY_NAME = "民用建筑隔声设计规范"
    _MAIN_ROW_ANCHOR = "| GB 50118-2010 | 民用建筑隔声设计规范 | 2010 |"
    _FALLBACK_KEY = "GB 55031-2022"
    _FALLBACK_ROW_ANCHOR = "| GB 55031-2022 | 民用建筑通用规范 | 2022 |"
    _REGISTER_HEADER = "| 标准编号 | 标准名称 | 状态 | 替代标准 | 过渡期说明 |"
    _SYNTHETIC_PRESEED_KEY = "ZZ 0000-0000"
    _SYNTHETIC_PRESEED_NAME = "合成预置名（探针）"

    def setUp(self) -> None:
        self._saved_file = sre_reasoner.__file__
        self._saved_dicts = {name: dict(getattr(sre_reasoner, name)) for name in
                             ("STANDARD_NAMES", "STANDARD_STATUS",
                              "STANDARD_PROVINCE", "STANDARD_VERIFY")}
        self._saved_l1 = set(sre_reasoner.INDEX_L1_IDS)

    def tearDown(self) -> None:
        sre_reasoner.__file__ = self._saved_file
        for name, saved in self._saved_dicts.items():
            target = getattr(sre_reasoner, name)
            target.clear()
            target.update(saved)
        sre_reasoner.INDEX_L1_IDS.clear()
        sre_reasoner.INDEX_L1_IDS.update(self._saved_l1)

    @staticmethod
    def _real_index_text() -> str:
        for path in _INDEX_CANDIDATES:
            if path.exists():
                return path.read_text(encoding="utf-8")
        raise unittest.SkipTest("索引不可达")

    def _load_from_text(self, text: str) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="sre_name_sot_")
        self.addCleanup(tmp.cleanup)
        skills = Path(tmp.name) / "skills"
        (skills / "shared").mkdir(parents=True)
        (skills / "shared" / "standards-index.md").write_text(text, encoding="utf-8")
        sre_reasoner.__file__ = str(skills / "prefab-standards-reviewer" / "sre_reasoner.py")
        sre_reasoner._load_standards_index()

    @staticmethod
    def _preseed_names() -> dict:
        for node in ast.walk(_SRC_TREE):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
            elif isinstance(node, ast.AnnAssign):
                target = node.target
            else:
                continue
            if isinstance(target, ast.Name) and target.id == "STANDARD_NAMES":
                return ast.literal_eval(node.value)
        raise AssertionError("STANDARD_NAMES 模块级预置字面量未在源码树中找到")

    @staticmethod
    def _kind_names(text: str, kind: str) -> dict:
        out: dict = {}
        parts = re.split(r"(?m)^(##.*)$", text)
        sections = [("", parts[0])] + [(parts[i], parts[i + 1])
                                       for i in range(1, len(parts) - 1, 2)]
        for _heading, chunk in sections:
            for header, rows in sre_reasoner._iter_md_tables(chunk):
                if sre_reasoner._classify_verify_table(header):
                    continue
                if sre_reasoner._classify_status_table(header) != kind:
                    continue
                i_id = next(header.index(c) for c in sre_reasoner._ID_COLS if c in header)
                i_name = next((header.index(c) for c in sre_reasoner._NAME_COLS
                               if c in header), None)
                if i_name is None:
                    continue
                for row in rows:
                    if i_id < len(row) and row[i_id] and i_name < len(row) and row[i_name]:
                        out.setdefault(row[i_id], row[i_name])
        return out

    def test_main_table_name_overrides_hardcoded_preseed(self):
        text = self._real_index_text()
        self.assertEqual(text.count(self._MAIN_ROW_ANCHOR), 1, "主表行锚不唯一，注入不可信")
        sentinel = self._KEY_NAME + "（SOT探针）"
        injected = text.replace(self._MAIN_ROW_ANCHOR,
                                f"| {self._KEY} | {sentinel} | 2010 |", 1)
        self._load_from_text(injected)
        self.assertEqual(sre_reasoner.STANDARD_NAMES.get(self._KEY), sentinel,
                         "索引主表名称应覆盖硬编码预置（SOT 优先）")
        for no, name in self._kind_names(text, "main").items():
            if no != self._KEY:
                self.assertEqual(sre_reasoner.STANDARD_NAMES.get(no), name,
                                 f"{no} 名称被本次注入波及")

    def test_register_row_does_not_override_main(self):
        text = self._real_index_text()
        self.assertEqual(text.count(self._REGISTER_HEADER), 1, "登记表头锚不唯一，注入不可信")
        lines = text.split("\n")
        i = lines.index(self._REGISTER_HEADER)
        lines.insert(i + 2, f"| {self._KEY} | 登记表探针名 | 已废止 | 探针 | 探针 |")
        self._load_from_text("\n".join(lines))
        self.assertEqual(sre_reasoner.STANDARD_NAMES.get(self._KEY), self._KEY_NAME,
                         "§7.2 登记表名称不得覆盖主表（SOT）名称")

    def test_register_row_overrides_hardcoded_preseed(self):
        """登记表对预置键也须 SOT 优先，否则「仅登记表收录」的键留下同族遮蔽面。

        实测预置 39 键当前全在主表内，真实数据取不出「仅登记表收录的预置键」，故以合成
        预置键模拟该面（`_load_standards_index` 不清空 STANDARD_NAMES，注入键会存活到
        合并阶段）；setUp 已存字典副本，tearDown 回滚，不污染其余用例。
        """
        text = self._real_index_text()
        main_names = self._kind_names(text, "main")
        key = self._SYNTHETIC_PRESEED_KEY
        self.assertNotIn(key, main_names, "合成键撞上主表真实编号，注入不可信")
        self.assertNotIn(key, self._kind_names(text, "register"), "合成键撞上登记表真实编号")
        sre_reasoner.STANDARD_NAMES[key] = self._SYNTHETIC_PRESEED_NAME
        sentinel = self._SYNTHETIC_PRESEED_NAME + "（登记表探针）"
        self.assertEqual(text.count(self._REGISTER_HEADER), 1, "登记表头锚不唯一，注入不可信")
        lines = text.split("\n")
        i = lines.index(self._REGISTER_HEADER)
        lines.insert(i + 2, f"| {key} | {sentinel} | 现行 | 探针 | 探针 |")
        self._load_from_text("\n".join(lines))
        self.assertEqual(sre_reasoner.STANDARD_NAMES.get(key), sentinel,
                         "登记表名称应覆盖硬编码预置（预置不得遮蔽索引任一表）")
        for no, name in main_names.items():
            self.assertEqual(sre_reasoner.STANDARD_NAMES.get(no), name,
                             f"主表键 {no} 被本次登记表注入波及")

    def test_hardcoded_preseed_remains_fallback_for_unindexed_key(self):
        text = self._real_index_text()
        preseed = self._preseed_names()
        self.assertIn(self._FALLBACK_KEY, preseed, "兜底断言的预置键不存在")
        self.assertNotIn(self._FALLBACK_KEY, self._kind_names(text, "register"),
                         "兜底键同时存在于登记表，断言将被补录支路混淆")
        self.assertEqual(text.count(self._FALLBACK_ROW_ANCHOR), 1, "主表行锚不唯一，删除不可信")
        injected = text.replace(self._FALLBACK_ROW_ANCHOR, "", 1)
        self._load_from_text(injected)
        self.assertEqual(sre_reasoner.STANDARD_NAMES.get(self._FALLBACK_KEY),
                         preseed[self._FALLBACK_KEY],
                         "索引未收录编号应回落硬编码预置（兜底层须保留）")


class TestProjectTypeAnalogy(unittest.TestCase):
    """「公寓」分流守卫组（CG-20261012-006 上线项目类型类比归一；CG-20261017-006 按台账 PL-200
    裁定①「甲」的实施批，改为「专用行优先＋类比兜底」，本组由九例扩为十三例）。

    判据源＝`standards-reasoning-rules.md` §3.2 Step 1「项目类型分流与类比兜底」注＋Step 2 的 `公寓`
    族行；末例把代码常量与该注内所引标注**跨载体逐字对撞**（判据与实现分居两载体而不互撞，即《条文
    存在≠机制生效》与「手抄副本漂移」两族的复合形态）。

    三支**改名例**的对应关系（旧名系 §11.2 PL-200 行 ③ 与 CG-20261017-005 行 ①—③ 的引用锚，故把
    映射写在本 docstring 里而不另立第二份映射表；改名理由＝旧断言与分流后判据正相反，留旧名即用例名
    与它实际断言的事互相打脸，属《载体自述字面》族）：
      test_analog_type_hits_table_without_miss_degradation → test_dedicated_rows_hit_without_miss
      test_analog_type_gets_target_family                  → test_apartment_gets_split_family
      test_value_body_identical_to_target_type             → test_value_body_changed_only_by_the_mandatory_standard
                                                           ＋ test_split_family_is_hotel_analogy_not_new_dorm
                                                           （旧断言＝公寓与住宅逐枚相等，本批改判为
                                                            「差集只允许全文强制枚」并另加一枚口径例）
      test_clearing_analogy_table_restores_miss            → test_clearing_dedicated_rows_falls_back_to_analogy
                                                           ＋ test_clearing_both_branches_restores_miss（换判据轴）
    本组沿 CG-20261012-006 的形制同时含正向例、控制例与牙例，并新增两支**取数侧**断言（取序后缀／
    覆盖面）：只留正向例时，「专用行根本没被消费」这一空跑形态抓不到——类比兜底会把「命中」与「带标注」
    两向都顶绿（教训＝CG-20261017-002 ②：注入类用例挡不住空跑，须取数侧断言）。
    """

    ANALOG = "公寓"
    TARGET = "住宅"
    # (一) 的三枚高频场景＝PL-200 行 ③ 逐字点名者
    SPLIT_SCENES = ("分户墙", "分户楼板", "户内吊顶")
    # 与 sre_reasoner._load_standards_index() 的候选顺序同形制（运行时 shared 镜像为判据载体）
    _RULES_CANDIDATES = [
        SRE_SOURCE.parent.parent / "shared" / "standards-reasoning-rules.md",
    ]

    def _notice(self) -> str:
        entry = sre_reasoner.PROJECT_TYPE_ANALOGIES.get(self.ANALOG)
        self.assertIsNotNone(entry, f"{self.ANALOG} 未登记在 PROJECT_TYPE_ANALOGIES，本组失主语")
        return entry[1]

    @staticmethod
    def _conf(resp: dict, etype: str) -> list[str]:
        return [e["置信度"] for e in resp["证据对象"] if e["证据类型"] == etype]

    @staticmethod
    def _source(resp: dict, etype: str) -> list[str]:
        return [e["规则来源"] for e in resp["证据对象"] if e["证据类型"] == etype]

    @staticmethod
    def _ids(resp: dict) -> list[str]:
        return [s["标准编号"] for s in resp["适用标准集"]]

    @staticmethod
    def _body(resp: dict) -> list[tuple]:
        return [(s["标准编号"], s["层级"], s["权限"], s["角色"],
                 s["地域适用性"], s["时间状态"]) for s in resp["适用标准集"]]

    def _drop_dedicated(self):
        """内存内撤掉「公寓」的**两轴**专用面（Step 1 激活行＋Step 2 专用族），返回回滚闭包。

        两轴必须一起撤：只撤激活行时，取族侧仍命中 `DOMAINS[*]["公寓"]`（两表各自独立），
        此时"回到类比态"只在 Step 1 成立而 Step 2 不成立——那是一格混合态，不是本例要断的形态。"""
        saved_table = dict(sre_reasoner.ACTIVATION_TABLE)
        saved_domains = {d: dict(v) for d, v in sre_reasoner.DOMAINS.items()}
        for k in [k for k in sre_reasoner.ACTIVATION_TABLE if k[0] == self.ANALOG]:
            del sre_reasoner.ACTIVATION_TABLE[k]
        for v in sre_reasoner.DOMAINS.values():
            v.pop(self.ANALOG, None)

        def restore():
            sre_reasoner.ACTIVATION_TABLE.clear()
            sre_reasoner.ACTIVATION_TABLE.update(saved_table)
            for d, v in saved_domains.items():
                sre_reasoner.DOMAINS[d].clear()
                sre_reasoner.DOMAINS[d].update(v)
        return restore

    # ── 正向例（验收判据 (一)(二)(三)(四)(六)）────────────────────────────
    def test_dedicated_rows_hit_without_miss(self):
        """(一) 三枚高频场景输出**非**「场景未命中」最小集。

        这一条即 CG-20261017-004 现读二所防的功能回退，须作**正向**断言钉住：只写「不得回退」
        在无守卫时不可机算。"""
        for space in self.SPLIT_SCENES:
            unc = reason(self.ANALOG, space, location="全国").get("未覆盖领域", [])
            self.assertFalse(any("场景未命中" in u for u in unc),
                             f"{self.ANALOG}|{space} 仍判未命中：{unc}")

    def test_apartment_gets_split_family(self):
        """(二) 标准族含 GB 55025-2022 且**不含** GB 55038-2025。

        标准族成员是「按哪一档取值」的指纹：含 GB 55038-2025（该标准第1.0.2条限城镇住宅项目）
        即说明实现仍走住宅类比，等于本枚判定没落地——比「检查是否分流」更能发现空跑。"""
        for space in self.SPLIT_SCENES:
            ids = self._ids(reason(self.ANALOG, space, location="全国"))
            self.assertNotIn("GB 55038-2025", ids,
                             f"{self.ANALOG}|{space} 仍取住宅专用限值标准（分流未落地）")
        for space in ("分户墙", "分户楼板"):
            ids = self._ids(reason(self.ANALOG, space, location="全国"))
            self.assertIn("GB 55025-2022", ids,
                          f"{self.ANALOG}|{space} 未取到全文强制归属枚 GB 55025-2022")

    def test_split_family_is_hotel_analogy_not_new_dorm(self):
        """分流两档在数值面**同取**旅馆档类比（台账 PL-089 终局口径），故公寓族须恰等于
        「非住宅族 ∪ 全文强制归属枚」——既不得等于住宅族，也不得另立第三枚数。

        本例同时是 PL-200 行 ⑤ 末句对 PL-054（隔声计算内核）那条硬约束的文档侧对撞面：
        内核若另造一套宿舍档 dB 数，本例的「非住宅族＋一枚」等式即被打破。"""
        for domain in ("acoustic", "acoustic(impact)"):
            fam = sre_reasoner.DOMAINS[domain][self.ANALOG]
            base = sre_reasoner.DOMAINS[domain]["默认"]
            self.assertEqual(set(fam) - set(base), {"GB 55025-2022"},
                             f"{domain} 的公寓族多出/少了非「旅馆档＋全文强制枚」的编号：{fam}")
            self.assertEqual(set(base) - set(fam), set(),
                             f"{domain} 的公寓族丢了旅馆档承载枚：缺 {sorted(set(base) - set(fam))}")
            self.assertEqual(set(fam) & set(sre_reasoner.DOMAINS[domain][self.TARGET]), set(base),
                             f"{domain} 的公寓族与住宅族的重叠面不等于旅馆档（住宅专用枚混入）")
        self.assertEqual(sre_reasoner.DOMAINS["acoustic(贡献)"][self.ANALOG],
                         sre_reasoner.DOMAINS["acoustic(贡献)"]["默认"],
                         "贡献量归属未走旅馆档（吊顶场景经专用行取族时会另生一档）")

    def test_dedicated_row_precedes_analogy_branch(self):
        """(六) 前一支＝专用行须优先于类比支生效。键解析取序现读＝精确 → 空间别名 → 项目类比 →
        两轴同归一，类比恒居后。两态同判 inferred，故置信度不可作判据，须取 规则来源 后缀。"""
        src = self._source(reason(self.ANALOG, "分户墙", location="全国"), "activation")
        self.assertEqual(len(src), 1, f"activation 证据 {len(src)} 枚（须恰 1，多枚即两支混判）")
        self.assertIn("专用行", src[0], f"本次未走专用行：{src[0]}")
        self.assertNotIn("类比兜底", src[0], f"专用行在场却报类比兜底＝取序判据失真：{src[0]}")

    def test_apartment_degrades_activation_and_mapping_confidence(self):
        """(四) 两支降级须同批在场：activation 与经专用行取族的 mapping 证据均不得留 deterministic。

        判据轴自 CG-20261017-006 起＝「该类型的适用关系是否系参考级」，与本次走专用行还是类比兜底
        无关；若仍钉在类比支上，则专用行落地当天该降级支即被挤成空跑。"""
        resp = reason(self.ANALOG, "分户墙", location="全国")
        act = self._conf(resp, "activation")
        self.assertEqual(act, ["inferred"], f"activation 证据置信度未随分流改判：{act}")
        self.assertIn("inferred", self._conf(resp, "mapping"),
                      "经专用行取族的 mapping 证据仍全 deterministic")
        self.assertTrue(any(e["证据类型"] == "degradation"
                            and self._notice() in e["输出结论"] for e in resp["证据对象"]),
                        "缺与可见面同字的 degradation 证据（内部痕迹与对外输出不同向）")

    def test_split_notice_visible_in_uncovered(self):
        """(三) 之一：标注须落「未覆盖领域」可见面（断点4 口径：证据里的 inferred 使用者读不到）。"""
        resp = reason(self.ANALOG, "分户墙", location="全国")
        self.assertIn(self._notice(), resp.get("未覆盖领域", []),
                      "分流标注未落可见面")

    def test_split_notice_carries_three_facts(self):
        """(三) 之二：标注须同写明三件事，**缺一即属失实**（判据＝PL-200 行 ②）。

        本例防的不是标注缺席（那由上一例挡），而是标注在场却被改写得失实——例如把「数值系旅馆档
        类比」省掉，读者即会把分流档读成宿舍专用限值档。"""
        notice = self._notice()
        for fact, literal in (
            ("(i) 依据系参考级", "参考级"),
            ("(i) 依据载体", "起草说明"),
            ("(ii) 宿舍档无专用数值档", "宿舍档在本合集无专用数值档"),
            ("(ii) 数值系旅馆档类比", "GB 50118-2010 旅馆档类比"),
            ("(iii) 归属须人工确认", "须由规划许可／设计文件人工确认"),
            ("(iii) 不新增建筑定性入参", "不新增建筑定性入参"),
        ):
            self.assertIn(literal, notice, f"标注缺 {fact}，缺项字面＝{literal}")

    def test_apartment_rows_cover_every_residential_scene(self):
        """公寓行须逐枚覆盖住宅侧全部场景，且域集与住宅同场景同值。

        取数侧断言（非注入例）：类比兜底只救「两档同场景」那一格，缺枚即在该格静默退回住宅档——
        门禁全绿而取值本体已漂移，是本仓最难发现的一型。域集相等则钉住「分流只改分类归一与称谓、
        不改激活域集」这条本批声明。"""
        res = {k[1] for k in sre_reasoner.ACTIVATION_TABLE if k[0] == self.TARGET}
        apt = {k[1] for k in sre_reasoner.ACTIVATION_TABLE if k[0] == self.ANALOG}
        self.assertTrue(res, "住宅侧场景集现读为空，本例失主语")
        self.assertEqual(res - apt, set(), f"「公寓」缺这些住宅同名场景行：{sorted(res - apt)}")
        for space in sorted(res & apt):
            self.assertEqual(sre_reasoner.ACTIVATION_TABLE[(self.ANALOG, space)],
                             sre_reasoner.ACTIVATION_TABLE[(self.TARGET, space)],
                             f"公寓|{space} 的激活域集与住宅不等")

    # ── 控制例（防标注面恒真／防两支混判）────────────────────────────────
    def test_target_type_carries_no_analogy_notice(self):
        """控制例：目标档自身不得带分流标注、证据不得被降级（否则标注面恒真）。"""
        resp = reason(self.TARGET, "分户墙", location="全国")
        self.assertNotIn(self._notice(), resp.get("未覆盖领域", []))
        self.assertEqual(self._conf(resp, "activation"), ["deterministic"])
        self.assertIn("GB 55038-2025", self._ids(resp),
                      "住宅档自身丢了 GB 55038-2025（分流批误改住宅面）")

    def test_unmatched_analog_scene_stays_miss(self):
        """控制例：专用行与类比档均无行的空间（公寓｜地下车库）仍判未命中，且不得挂分流标注。"""
        resp = reason(self.ANALOG, "地下车库", location="全国")
        unc = resp.get("未覆盖领域", [])
        self.assertTrue(any("场景未命中" in u for u in unc),
                        f"公寓|地下车库 应走未命中最小集：{unc}")
        self.assertNotIn(self._notice(), unc, "未命中态同时挂分流标注（两支混判即失去可失败性）")

    def test_value_body_changed_only_by_the_mandatory_standard(self):
        """分流只改分类归一与全文强制归属枚，**不改取值本体**：公寓 body 相对住宅 body 的差集，
        多出的只可能是 GB 55025-2022、少掉的只可能是住宅专用枚 GB 55038-2025，其余成员六字段逐枚相等。

        旧例 `test_value_body_identical_to_target_type` 断言的是「公寓与住宅逐枚相等」，那正是本批
        要否掉的形态（相等即专用行未参与取族），故改判为「差集只允许这两枚」的正向读法。
        户内吊顶一类不含声学域的差集为空属预期，其「非恒真」由 (二) 与上一支覆盖面例分别钉住。"""
        for space in self.SPLIT_SCENES:
            a = set(self._body(reason(self.ANALOG, space, location="全国")))
            b = set(self._body(reason(self.TARGET, space, location="全国")))
            only_a = sorted(x for x in a - b)
            only_b = sorted(x for x in b - a)
            self.assertTrue(all(x[0] == "GB 55025-2022" for x in only_a),
                            f"公寓|{space} 相对住宅多出全文强制枚以外的成员：{only_a}")
            self.assertTrue(all(x[0] == "GB 55038-2025" for x in only_b),
                            f"公寓|{space} 相对住宅少掉的并非住宅专用枚：{only_b}")
            self.assertNotIn("GB 55038-2025", [x[0] for x in a],
                             f"公寓|{space} 仍含住宅专用限值枚")

    # ── 牙例（本组非恒真自证）──────────────────────────────────────────
    def test_clearing_dedicated_rows_falls_back_to_analogy(self):
        """(六) 后一支＝撤专用行后须回到**类比态**而非未命中（防「专用行根本没被消费」的空跑）。

        撤行后三向同断：仍命中（兜底支活着）＋标注仍在（两支同批）＋取族回到住宅档（兜底支确是
        取族的承担者，而不只是命中的承担者）。"""
        restore = self._drop_dedicated()
        try:
            resp = reason(self.ANALOG, "分户墙", location="全国")
            unc = resp.get("未覆盖领域", [])
            self.assertFalse(any("场景未命中" in u for u in unc),
                             "撤专用行后公寓退回未命中 ⇒ 类比兜底并非命中的承担者")
            self.assertIn(self._notice(), unc, "撤专用行后分流标注缺席（兜底与标注两支不同批）")
            self.assertIn("类比兜底", self._source(resp, "activation")[0],
                          f"撤专用行后仍报专用行：{self._source(resp, 'activation')[0]}")
            self.assertIn("GB 55038-2025", self._ids(resp),
                          "撤专用行后未回到住宅档 ⇒ 兜底支并非取族的承担者")
        finally:
            restore()
        apt = {k for k in sre_reasoner.ACTIVATION_TABLE if k[0] == self.ANALOG}
        self.assertTrue(apt, "回滚未生效，将污染后续用例")

    def test_clearing_both_branches_restores_miss(self):
        """牙例（换轴）：撤类比表后专用行仍须命中，而**同时**撤掉专用行与类比表才回到未命中——
        两向并判，才既钉住「类比表已退役为兜底、标注支已改由分流承担」，又钉住本组正向例
        不靠类比表顶绿（旧名 `test_clearing_analogy_table_restores_miss` 的对应例）。"""
        saved_analog = sre_reasoner.PROJECT_TYPE_ANALOGIES
        notice = self._notice()  # 须在改表前取，撤类比表后 _notice() 即失主语
        saved_table = dict(sre_reasoner.ACTIVATION_TABLE)
        try:
            sre_reasoner.PROJECT_TYPE_ANALOGIES = {}
            unc = reason(self.ANALOG, "分户墙", location="全国").get("未覆盖领域", [])
            self.assertFalse(any("场景未命中" in u for u in unc),
                             "撤类比表后公寓退回未命中 ⇒ 专用行并非命中的承担者")
            self.assertNotIn(notice, unc,
                             "撤类比表后仍挂分流标注 ⇒ 标注支仍由类比表承担，未随分流改判")
            for k in [k for k in sre_reasoner.ACTIVATION_TABLE if k[0] == self.ANALOG]:
                del sre_reasoner.ACTIVATION_TABLE[k]
            unc2 = reason(self.ANALOG, "分户墙", location="全国").get("未覆盖领域", [])
            self.assertTrue(any("场景未命中" in u for u in unc2),
                            "两支全撤后仍命中 ⇒ 本组正向例恒真")
        finally:
            sre_reasoner.PROJECT_TYPE_ANALOGIES = saved_analog
            sre_reasoner.ACTIVATION_TABLE.clear()
            sre_reasoner.ACTIVATION_TABLE.update(saved_table)
        self.assertEqual(sre_reasoner.PROJECT_TYPE_ANALOGIES, saved_analog, "回滚未生效")
        self.assertTrue({k for k in sre_reasoner.ACTIVATION_TABLE if k[0] == self.ANALOG},
                        "回滚未生效，将污染后续用例")

    def test_notice_literal_matches_rules_carrier(self):
        """跨载体对撞：代码常量标注逐字须在其判据载体内**在场恰一枚**。"""
        present = [p for p in self._RULES_CANDIDATES if p.exists()]
        self.assertTrue(present, "判据载体不可达，本例失主语（不得静默放行）")
        literal = self._notice()
        for p in present:
            text = p.read_text(encoding="utf-8")
            self.assertEqual(text.count(literal), 1,
                             f"{p} 内该标注字面命中 {text.count(literal)} 枚（0＝注未落逐字，"
                             f">1＝另有引用处，两向都使「单源」这句判据失实）")


def _rel(path: Path) -> str:
    # 见 _SKILLS_ROOT 注释：measurements 的 path 值与 standards_index 分组键一律走本函数。
    try:
        return path.relative_to(_SKILLS_ROOT).as_posix()
    except ValueError:
        return path.name


def _fingerprint(path: Path) -> dict:
    try:
        data = path.read_bytes()
    except OSError as exc:
        # error 只记异常类型与 errno：OSError 的 str() 会携带系统路径串（含用户名），不落报告。
        return {"path": _rel(path), "readable": False,
                "error": f"{type(exc).__name__}({exc.errno})"}
    return {"path": _rel(path), "readable": True, "bytes": len(data),
            "sha256_12": hashlib.sha256(data).hexdigest()[:12]}


def run_and_report() -> int:
    # Windows 控制台/管道默认 cp1252：本函数收尾会打印中文与路径，未重配置即
    # UnicodeEncodeError（2026-10-25 GitHub windows-latest 实测：84 例全过后退出码 1）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # A5-B 锚定升档：必须在本组其余检查全部执行完之后运行（§4.1 / §4.3）
    warnings = _aggregate_anchors()
    for text in warnings:
        print(f"[SRE-GAP] {text}")
    for sig in GAP_SIGNALS:
        _validate_signal(sig)

    fingerprint, count = _signal_state()
    previous = _previous_report()
    previous_fingerprint = previous.get("signal_fingerprint")
    stable_streak = (previous.get("signal_stable_streak", 0) + 1
                     if previous_fingerprint == fingerprint else 1)

    report = {
        "total": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "success": result.wasSuccessful(),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "signal_count": count,
        "signal_fingerprint": fingerprint,
        "signal_previous_fingerprint": previous_fingerprint,
        "signal_stable_streak": stable_streak,
        "gap_signals": GAP_SIGNALS,
        "measurements": {
            "sre_reasoner": _fingerprint(SRE_SOURCE),
            "standards_index": {_rel(p): _fingerprint(p) for p in _INDEX_CANDIDATES if p.exists()},
        },
    }
    _REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入: {_REPORT_PATH}")
    print(f"缺口信号 {count} 条｜本轮指纹 {fingerprint}｜上轮指纹 "
          f"{previous_fingerprint or '（首轮，无历史）'}｜连续一致 {stable_streak} 轮"
          f"（§6.3.3 ① 需 ≥2）")
    print("范围说明：本 JSON 只承载载体 B 的行为组信号 T-B1—T-B7；静态组 T-A1—T-A5 由 "
          "维护脚本/validate_governance.py 检查 5 独立产出，两载体互不读取（设计方案 §4.1）。")
    # 定位声明（CG-20261019-002，审计 20261018 P1-6 ③支裁定）：本 JSON 及其连击计数
    # （signal_stable_streak）系**本机运行史凭证**，刻意 gitignored、不入仓——连击语义＝
    # 「本机连续运行且指纹一致」，入仓即把单机语义伪造成跨机器语义，且多机并发会互相覆盖。
    # 换机或删档即重置属**设计行为而非缺陷**；跨机器可证性由确定性面承担＝仓内容 clone
    # 后复跑本门禁与主门禁即得同一指纹与信号读数（该支不依赖本机历史）。
    # 翻转前置（登记于台账 PL-244）：出现真实的跨机器汇总消费方一次，即重开「连击入仓」裁量。
    print("定位说明：本报告与连击计数系本机运行史凭证（gitignored，换机即重置属设计行为）；"
          "跨机器可证性由确定性信号承担，见台账 PL-244。")

    if result.unexpectedSuccesses:
        print("⚠ 存在 unexpected success：对应缺陷已被修复。按设计方案 §6.2 须摘除该用例的 "
              "@unittest.expectedFailure 装饰器并转为正向断言——禁止回退 sre_reasoner.py 的修复来消除本提示。")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_and_report())
