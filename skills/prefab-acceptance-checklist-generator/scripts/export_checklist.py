#!/usr/bin/env python3
"""把 §5 输出模板的验收清单 Markdown 导出为可编辑交付件。

产出同名 .xlsx（清单镜像，含筛选/冻结/判定下拉）与 .docx（方案摘要）。
只用标准库：不联网、不依赖第三方包。缺省拒绝导出缺「依据/适用条件/假设/版本/未决项」的输入。

用法：
    python export_checklist.py <清单.md> [--out <目录>] [--stem <文件名>] [--force]
退出码：0 成功；2 输入缺项或格式不符（不产出文件）；1 内部错误。
默认不覆盖已存在的同名 .xlsx/.docx；确认覆盖须显式加 --force。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

TOOL_VERSION = "1.0.0"
TOOL_NAME = "export_checklist.py"

META_REQUIRED = [
    "项目名称",
    "验收阶段",
    "空间范围",
    "部品系统",
    "编制日期",
    "编制依据",
    "编制版本",
    "假设与限制",
]
META_PLACEHOLDERS = {"", "（待填）", "待填", "TBD", "tbd", "—", "-"}

SEC_BASIC = "基本信息"
SEC_CHECK = "检查表"
SEC_SPECIAL = "无法判定与带条件合格专项表"
SEC_MANDATORY = "强制性条文检查项（必检）"
SEC_RECTIFY = "整改闭环表"
SEC_STATS = "判定统计"
SEC_PENDING = "未决事项"
SEC_ARCHIVE = "资料归档清单"
SEC_SOURCES = "数据来源说明"
SEC_DISCLAIMER = "标准免责提示"
SECTION_REQUIRED = [
    SEC_BASIC, SEC_CHECK, SEC_SPECIAL, SEC_MANDATORY, SEC_RECTIFY,
    SEC_STATS, SEC_PENDING, SEC_ARCHIVE, SEC_SOURCES, SEC_DISCLAIMER,
]

HEADERS = {
    SEC_CHECK: ["序号", "检查项目", "合格标准", "检验方法", "依据标准", "检查记录", "判定", "备注"],
    SEC_SPECIAL: ["序号", "对应检查项", "判定", "证据状态", "缺失证据/整改项",
                  "责任方（补证/整改）", "复验条件", "计划复验时间"],
    SEC_MANDATORY: ["序号", "检查项目", "合格标准", "检验方法", "依据标准", "检查记录", "判定"],
    SEC_RECTIFY: ["序号", "原检查项", "事项描述（首标类型）", "整改要求", "整改期限",
                  "整改结果", "复验判定", "复验人"],
    SEC_STATS: ["合格", "带条件合格", "不合格", "无法判定", "不适用", "未检查", "清单项判定合格率"],
    SEC_PENDING: ["序号", "对应检查项", "事项", "需谁确认", "影响"],
}

JUDGE_FIVE = ["合格", "带条件合格", "不合格", "无法判定", "不适用"]
JUDGE_FOUR = ["合格", "不合格", "无法判定", "不适用"]
EVIDENCE_STATES = ["已具备", "部分具备", "缺失", "待现场复核"]

# 章节别名：容忍标题里多打的空格或全半角括号差异
SECTION_ALIASES = {
    SEC_MANDATORY: [SEC_MANDATORY, "强制性条文检查项", "强制性条文检查项(必检)"],
    SEC_SPECIAL: [SEC_SPECIAL, "无法判定与带条件合格专项表"],
}


def norm(text: str) -> str:
    return re.sub(r"[\s\u3000]+", "", text).replace("(", "（").replace(")", "）")


def clean_cell(text: str) -> str:
    text = text.strip()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        text = text[1:-1].strip()
    return text


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    parts, cur, esc = [], "", False
    for ch in s:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == "|":
            parts.append(clean_cell(cur))
            cur = ""
        else:
            cur += ch
    parts.append(clean_cell(cur))
    return parts


def is_separator(line: str) -> bool:
    if "-" not in line:
        return False
    return re.fullmatch(r"\|?[\s:|-]+\|?", line.strip()) is not None


class Section:
    def __init__(self, name: str):
        self.name = name
        self.meta: dict[str, str] = {}
        self.table: list[list[str]] | None = None
        self.checkboxes: list[tuple[bool, str]] = []
        self.quotes: list[str] = []
        self.paragraphs: list[str] = []
        self.notes: list[str] = []
        self._table_buf: list[str] = []

    def flush_table(self) -> None:
        buf = self._table_buf
        self._table_buf = []
        if not buf:
            return
        rows = [split_row(line) for line in buf if not is_separator(line)]
        if rows and self.table is None:
            self.table = rows
        elif rows:
            raise ValueError(f"章节「{self.name}」存在多张表格，无法确定导出对象")

    def feed(self, line: str) -> None:
        if line.strip().startswith("|"):
            self._table_buf.append(line)
            return
        self.flush_table()
        stripped = line.strip()
        if not stripped:
            return
        m = re.match(r"^>\s?(.*)$", stripped)
        if m:
            if m.group(1).strip():
                self.quotes.append(m.group(1).strip())
            return
        m = re.match(r"^-\s*\[([ xX])\]\s*(.+)$", stripped)
        if m:
            self.checkboxes.append((m.group(1).lower() == "x", m.group(2).strip()))
            return
        m = re.match(r"^[-*]\s*([^：:]{1,40})[：:]\s*(.*)$", stripped)
        if m and norm(self.name) == norm(SEC_BASIC):
            key = m.group(1).strip()
            value = m.group(2).strip()
            self.meta[key] = value
            return
        if stripped.startswith(">") or stripped.startswith("```"):
            return
        if re.match(r"^[-*]\s+", stripped):
            self.paragraphs.append(re.sub(r"^[-*]\s+", "", stripped))
            return
        self.paragraphs.append(stripped)

    def finish(self) -> None:
        self.flush_table()


def parse_sections(text: str) -> dict[str, Section]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sections: dict[str, Section] = {}
    current: Section | None = None
    for line in text.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m and not line.startswith("###"):
            name = m.group(1).strip()
            if name in sections:
                raise ValueError(
                    f"章节「{name}」重复出现；续写内容请合并到同一章节，避免导出时丢行"
                )
            current = Section(name)
            sections[name] = current
            continue
        if current is not None:
            current.feed(line)
    for sec in sections.values():
        sec.finish()
    return sections


def resolve_section(sections: dict[str, Section], canonical: str) -> Section | None:
    for alias in SECTION_ALIASES.get(canonical, [canonical]):
        for name, sec in sections.items():
            if norm(name) == norm(alias):
                return sec
    return None


def resolve_all(sections: dict[str, Section], canonical: str) -> list[Section]:
    names = {norm(alias) for alias in SECTION_ALIASES.get(canonical, [canonical])}
    return [sec for name, sec in sections.items() if norm(name) in names]


KNOWN_SECTION_NORMS = {norm(name) for name in SECTION_REQUIRED} | {
    norm(alias) for aliases in SECTION_ALIASES.values() for alias in aliases
}
SECTION_BASES = {
    norm(re.sub(r"[（(][^）)]*[）)]", "", name)) for name in SECTION_REQUIRED
}


def normalize_judge(value: str, allowed: list[str]) -> str:
    value = value.strip()
    if "□" in value:
        picked = [token for token in allowed if token in value and "□" + token not in value]
        return picked[0] if len(picked) == 1 else ""
    return value if value in allowed else ""


CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
EXCEL_CELL_LIMIT = 32767


def cell_text_problems(blocks: dict) -> list[str]:
    problems = []

    def walk(where: str, value) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                walk(f"{where}「{key}」", item)
            return
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value, start=1):
                walk(f"{where} 第{index}项", item)
            return
        if not isinstance(value, str) or not value:
            return
        if CONTROL_CHARS.search(value):
            problems.append(f"{where} 含不可见控制字符（Excel/Word 无法保存）")
        if len(value) > EXCEL_CELL_LIMIT:
            problems.append(f"{where} 超过 Excel 单元格 {EXCEL_CELL_LIMIT} 字符上限")

    for name, block in blocks.items():
        walk(name, block)
    return problems


def build_model(text: str) -> tuple[dict, list[str]]:
    problems: list[str] = []
    try:
        sections = parse_sections(text)
    except ValueError as exc:
        return {}, [str(exc)]

    for name in sections:
        h = norm(name)
        if h in KNOWN_SECTION_NORMS:
            continue
        for base in SECTION_BASES:
            if base and base in h:
                problems.append(
                    f"章节名「{name}」疑似「{base}」的续写或变体，无法确定导出对象；"
                    "请合并到同名章节或改用精确章节名"
                )
                break
    for name in SECTION_REQUIRED:
        matches = resolve_all(sections, name)
        if not matches:
            problems.append(f"缺少必需章节「{name}」")
        elif len(matches) > 1:
            problems.append(
                f"章节「{name}」存在多个变体：{[m.name for m in matches]}；请合并为一个章节"
            )
    if problems:
        return {}, problems
    ignored_sections = [
        name for name in sections
        if norm(name) not in KNOWN_SECTION_NORMS
        and not any(base and base in norm(name) for base in SECTION_BASES)
    ]

    table_rows: dict[str, list[list[str]]] = {}
    for name in HEADERS:
        sec = resolve_section(sections, name)
        assert sec is not None
        if sec.table is None:
            if name == SEC_SPECIAL and any(p.strip() == "无" for p in sec.paragraphs):
                table_rows[name] = [HEADERS[name]]
                continue
            problems.append(f"章节「{sec.name}」缺少表格")
            continue
        header = sec.table[0]
        expected = HEADERS[name]
        if [norm(c) for c in header] != [norm(c) for c in expected]:
            problems.append(
                f"章节「{sec.name}」表头不符：期望 {expected}，实际 {header}"
            )
            continue
        data = []
        for idx, row in enumerate(sec.table[1:], start=2):
            if len(row) < len(expected):
                row = row + [""] * (len(expected) - len(row))
            if len(row) > len(expected):
                problems.append(f"章节「{sec.name}」第 {idx} 行多出 {len(row) - len(expected)} 列")
                continue
            if not any(cell.strip() for cell in row):
                continue
            data.append(row)
        table_rows[name] = [expected] + data

    basic = resolve_section(sections, SEC_BASIC)
    assert basic is not None
    meta = {}
    for key in META_REQUIRED:
        value = basic.meta.get(key, "").strip()
        if value in META_PLACEHOLDERS:
            problems.append(f"基本信息缺「{key}」（导出件必须保留依据/适用条件/假设/版本，不得留空）")
        meta[key] = value
    for key, value in basic.meta.items():
        meta.setdefault(key, value)

    check = table_rows.get(SEC_CHECK, [HEADERS[SEC_CHECK]])
    if len(check) == 1:
        problems.append("检查表没有任何检查项")
    else:
        judge_col = HEADERS[SEC_CHECK].index("判定")
        for row in check[1:]:
            raw = row[judge_col]
            if not raw or "□" in raw:
                continue
            if normalize_judge(raw, JUDGE_FIVE) == "":
                problems.append(f"检查表判定列取值越界：{raw}（允许 {JUDGE_FIVE} 或留空）")

    mandatory = table_rows.get(SEC_MANDATORY, [HEADERS[SEC_MANDATORY]])
    if len(mandatory) == 1:
        problems.append("强制性条文检查项（必检）表为空——必检项不得整体缺失")
    else:
        judge_col = HEADERS[SEC_MANDATORY].index("判定")
        for row in mandatory[1:]:
            raw = row[judge_col]
            if not raw or "□" in raw:
                continue
            if normalize_judge(raw, JUDGE_FOUR) == "":
                problems.append(f"必检项判定列只能取 {JUDGE_FOUR} 或留空，实际：{raw}")
        for row in mandatory[1:]:
            nm = norm(row[1])
            candidates = [item for item in check[1:] if norm(item[1]) == nm]
            if not candidates:
                candidates = [item for item in check[1:]
                              if norm(item[1]) and (norm(item[1]) in nm or nm in norm(item[1]))]
            if not candidates:
                problems.append(
                    f"必检项未同时列入检查表：{row[1]}（必检项须同时列入检查表并计入统计）"
                )
                continue
            if len(candidates) > 1:
                problems.append(
                    f"必检项「{row[1]}」在检查表中匹配到多行，无法确定对应项："
                    f"{[item[1] for item in candidates]}"
                )
                continue
            if norm(candidates[0][2]) != norm(row[2]):                problems.append(
                    f"必检项「{row[1]}」的合格标准与检查表同名行不一致："
                    f"必检表＝{row[2]}；检查表＝{candidates[0][2]}"
                )

    special = table_rows.get(SEC_SPECIAL, [HEADERS[SEC_SPECIAL]])
    if len(special) > 1:
        for row in special[1:]:
            judge, evidence = row[2].strip(), row[3].strip()
            if judge and judge not in ("无法判定", "带条件合格"):
                problems.append(f"专项表判定列只能取「无法判定」或「带条件合格」，实际：{judge}")
                continue
            if judge and not evidence:
                problems.append(f"专项表「{row[1]}」判定为「{judge}」但未填证据状态")
                continue
            if evidence and evidence not in EVIDENCE_STATES:
                problems.append(f"专项表证据状态列取值越界：{evidence}")
                continue
            if evidence and not judge:
                problems.append(f"专项表「{row[1]}」填了证据状态但未填判定")
                continue
            if judge == "带条件合格" and evidence not in ("已具备", "部分具备"):
                problems.append(
                    f"专项表「{row[1]}」带条件合格只能配「已具备/部分具备」，实际：{evidence}"
                )
            if judge == "无法判定" and evidence not in ("缺失", "待现场复核"):
                problems.append(
                    f"专项表「{row[1]}」无法判定只能配「缺失/待现场复核」，实际：{evidence}"
                )

    rectify = table_rows.get(SEC_RECTIFY, [HEADERS[SEC_RECTIFY]])
    if len(rectify) > 1:
        recheck_col = HEADERS[SEC_RECTIFY].index("复验判定")
        for row in rectify[1:]:
            raw = row[recheck_col]
            if not raw or "□" in raw:
                continue
            if normalize_judge(raw, JUDGE_FIVE) == "":
                problems.append(f"整改闭环表复验判定取值越界：{raw}（允许 {JUDGE_FIVE} 或留空）")

    stats = table_rows.get(SEC_STATS, [HEADERS[SEC_STATS]])
    if len(stats) > 2:
        problems.append("判定统计只允许一行计数（无数据时留空行）")
    if len(stats) == 2:
        counts = [int(cell) if cell.isdigit() else None for cell in stats[1][:6]]
        if any(value is not None for value in counts):
            if any(value is None for value in counts):
                problems.append("判定统计六个计数要么全部填写数字，要么整行留空；不得部分填写")
            else:
                if sum(counts) != len(check) - 1:
                    problems.append(
                        f"判定统计计数合计 {sum(counts)} 与检查表行数 {len(check) - 1} 不一致"
                    )
                actual = {state: 0 for state in JUDGE_FIVE}
                actual["未检查"] = 0
                judge_col = HEADERS[SEC_CHECK].index("判定")
                for row in check[1:]:
                    state = normalize_judge(row[judge_col], JUDGE_FIVE) or "未检查"
                    actual[state] += 1
                for index, state in enumerate(JUDGE_FIVE + ["未检查"]):
                    if counts[index] != actual[state]:
                        problems.append(
                            f"判定统计「{state}」填 {counts[index]}，检查表实际 {actual[state]} 行"
                        )

    pending = table_rows.get(SEC_PENDING, [HEADERS[SEC_PENDING]])

    archive_sec = resolve_section(sections, SEC_ARCHIVE)
    assert archive_sec is not None
    archive = archive_sec.checkboxes
    if not archive:
        problems.append("资料归档清单没有任何条目")

    sources = resolve_section(sections, SEC_SOURCES)
    disclaimer = resolve_section(sections, SEC_DISCLAIMER)
    assert sources is not None and disclaimer is not None
    sources_items = sources.paragraphs + sources.quotes
    disclaimer_items = disclaimer.quotes + disclaimer.paragraphs
    if not sources_items:
        problems.append("数据来源说明为空")
    if not disclaimer_items:
        problems.append("标准免责提示为空")

    problems.extend(cell_text_problems({
        "基本信息": meta,
        "检查表": check,
        "专项表": special,
        "强制性条文表": mandatory,
        "整改闭环表": rectify,
        "判定统计": stats,
        "未决事项": pending,
        "资料归档清单": [["", name] for _done, name in archive],
        "数据来源说明": sources_items,
        "标准免责提示": disclaimer_items,
    }))

    if problems:
        return {}, problems

    stats_row = stats[1] if len(stats) == 2 else [""] * len(HEADERS[SEC_STATS])
    stats_sec = resolve_section(sections, SEC_STATS)
    special_sec = resolve_section(sections, SEC_SPECIAL)
    assert stats_sec is not None and special_sec is not None
    return {
        "meta": meta,
        "check": check,
        "special": special,
        "mandatory": mandatory,
        "rectify": rectify,
        "stats": [HEADERS[SEC_STATS], stats_row],
        "stats_notes": stats_sec.paragraphs + stats_sec.quotes,
        "pending": pending,
        "archive": archive,
        "sources": sources.paragraphs + sources.quotes,
        "disclaimer": disclaimer.quotes + disclaimer.paragraphs,
        "special_notes": special_sec.paragraphs + special_sec.quotes,
        "ignored_sections": ignored_sections,
    }, []


# ---------------------------------------------------------------- XML helpers

def esc(text: str) -> str:
    return escape(text)


def attr(text: str) -> str:
    return quoteattr(text)


def col_letter(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA ..."""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


XLSX_SHEET = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">{dimension}{views}{cols}<sheetData>{data}</sheetData>{autofilter}{validations}<pageMargins left="0.4" right="0.4" top="0.5" bottom="0.5" header="0.3" footer="0.3"/></worksheet>"""


def cell_xml(ref: str, value, style_id: int, sst: dict[str, int]) -> str:
    if value is None or value == "":
        return f'<c r="{ref}" s="{style_id}"/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style_id}"><v>{value}</v></c>'
    text = str(value)
    index = sst.setdefault(text, len(sst))
    return f'<c r="{ref}" s="{style_id}" t="s"><v>{index}</v></c>'


def build_styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4">
<font><sz val="11"/><color theme="1"/><name val="等线"/><family val="2"/><charset val="134"/></font>
<font><b/><sz val="11"/><color theme="1"/><name val="等线"/><family val="2"/><charset val="134"/></font>
<font><b/><sz val="15"/><color theme="1"/><name val="等线"/><family val="2"/><charset val="134"/></font>
<font><i/><sz val="10"/><color rgb="FF808080"/><name val="等线"/><family val="2"/><charset val="134"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"><color rgb="FFBFBFBF"/></left><right style="thin"><color rgb="FFBFBFBF"/></right><top style="thin"><color rgb="FFBFBFBF"/></top><bottom style="thin"><color rgb="FFBFBFBF"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="7">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="常规" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


class Sheet:
    def __init__(self, name: str, widths: list[int]):
        self.name = name
        self.widths = widths
        self.rows: list[list[tuple]] = []
        self.freeze = False
        self.autofilter = False
        self.validations: list[tuple[str, list[str]]] = []

    def add(self, cells: list[tuple]) -> None:
        self.rows.append(cells)


def render_sheet(sheet: Sheet, sst: dict[str, int]) -> str:
    parts = []
    for r_index, row in enumerate(sheet.rows, start=1):
        cells = []
        for c_index, (value, style_id) in enumerate(row):
            cells.append(cell_xml(f"{col_letter(c_index)}{r_index}", value, style_id, sst))
        parts.append(f'<row r="{r_index}">{"".join(cells)}</row>')
    data = "".join(parts)
    rows_count = len(sheet.rows)
    cols_count = max((len(r) for r in sheet.rows), default=1)
    dimension = f'<dimension ref="A1:{col_letter(cols_count - 1)}{max(rows_count, 1)}"/>' if rows_count else ""
    views = ""
    if sheet.freeze and rows_count > 1:
        views = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
                 'activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" '
                 'activeCell="A2" sqref="A2"/></sheetView></sheetViews>')
    else:
        views = '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
    cols = ""
    if sheet.widths:
        col_items = "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(sheet.widths)
        )
        cols = f"<cols>{col_items}</cols>"
    autofilter = ""
    if sheet.autofilter and rows_count > 1:
        autofilter = f'<autoFilter ref="A1:{col_letter(cols_count - 1)}{rows_count}"/>'
    validations = ""
    if sheet.validations:
        items = []
        for sqref, values in sheet.validations:
            formula = ",".join(values)
            items.append(
                f'<dataValidation type="list" allowBlank="1" showInputMessage="1" '
                f'showErrorMessage="1" promptTitle="请选择" prompt="从下拉列表选择" '
                f'errorTitle="取值不符" error="请从下拉列表选择" sqref="{sqref}">'
                f"<formula1>\"{esc(formula)}\"</formula1></dataValidation>"
            )
        validations = f'<dataValidations count="{len(items)}">{"".join(items)}</dataValidations>'
    return XLSX_SHEET.format(
        dimension=dimension, views=views, cols=cols, data=data,
        autofilter=autofilter, validations=validations,
    )


def build_xlsx(model: dict, source_name: str, sha256: str, generated: str) -> bytes:
    meta = model["meta"]
    sheets: list[Sheet] = []

    cover = Sheet("封面与依据", [22, 100])
    cover.add([("装配式装修验收清单", 4), ("可编辑交付件（XLSX 清单＋DOCX 方案摘要）", 5)])
    cover.add([])
    for key in META_REQUIRED:
        cover.add([(key, 6), (meta.get(key, ""), 2)])
    applicable = (
        f"验收阶段＝{meta.get('验收阶段', '')}；空间范围＝{meta.get('空间范围', '')}；"
        f"部品系统＝{meta.get('部品系统', '')}；数据来源分级见本表下方说明。"
    )
    cover.add([("适用条件", 6), (applicable, 2)])
    pending_count = max(len(model["pending"]) - 1, 0)
    cover.add([("未决事项", 6), (
        f"{pending_count} 项（详见工作表「未决事项」，本文件不替代确认）" if pending_count else "无", 2
    )])
    if model["special_notes"]:
        cover.add([("专项表判定说明", 6), (" ".join(model["special_notes"]), 2)])
    if model["ignored_sections"]:
        cover.add([("未纳入导出的章节", 6), ("、".join(model["ignored_sections"]), 2)])
    cover.add([])
    cover.add([("数据来源说明", 1)])
    for item in model["sources"]:
        cover.add([("", 2), (item, 2)])
    cover.add([("标准免责提示", 1)])
    for item in model["disclaimer"]:
        cover.add([("", 2), (item, 2)])
    cover.add([])
    cover.add([("交付件信息", 1)])
    for key, value in [
        ("生成时间", generated),
        ("输入文件", source_name),
        ("输入文件 SHA-256", sha256),
        ("导出工具", f"{TOOL_NAME} v{TOOL_VERSION}"),
        ("使用说明", "本表为清单镜像；DOCX 为方案摘要。判定栏由具备相应资格人员现场填写，"
                     "本文件不替代验收判定。"),
    ]:
        cover.add([(key, 6), (value, 2)])
    sheets.append(cover)

    def table_sheet(name: str, rows: list[list[str]], widths: list[int],
                    center_cols: set[int] = frozenset({0}),
                    validation: tuple[int, list[str], str] | None = None) -> Sheet:
        sh = Sheet(name, widths)
        header, data = rows[0], rows[1:]
        sh.add([(cell, 1) for cell in header])
        for row in data:
            cells = []
            for i, cell in enumerate(row):
                value = int(cell) if i == 0 and header[0] == "序号" and cell.isdigit() else cell
                cells.append((value, 3 if i in center_cols else 2))
            sh.add(cells)
        sh.freeze = True
        sh.autofilter = True
        if validation:
            col, values, _ = validation
            if data:
                sh.validations.append((f"{col_letter(col)}2:{col_letter(col)}{len(rows)}", values))
        return sh

    check = table_sheet(
        "检查表", model["check"], [6, 24, 34, 22, 26, 22, 16, 22],
        center_cols={0, 6},
        validation=(6, JUDGE_FIVE, "判定"),
    )
    sheets.append(check)

    special_rows = model["special"]
    special = table_sheet(
        "无法判定与带条件合格专项表", special_rows, [6, 22, 14, 14, 30, 20, 22, 16],
        center_cols={0, 2, 3},
    )
    if len(special_rows) > 1:
        last = len(special_rows)
        special.validations.append((f"C2:C{last}", ["无法判定", "带条件合格"]))
        last = len(special_rows)
        special.validations.append((f"D2:D{last}", EVIDENCE_STATES))
    sheets.append(special)

    sheets.append(table_sheet(
        "强制性条文检查项", model["mandatory"], [6, 26, 34, 22, 26, 22, 16],
        center_cols={0, 6},
        validation=(6, JUDGE_FOUR, "判定"),
    ))
    sheets.append(table_sheet(
        "整改闭环表", model["rectify"], [6, 22, 28, 26, 14, 20, 14, 12],
        center_cols={0, 4, 6},
        validation=(6, JUDGE_FIVE, "复验判定"),
    ))

    stats = Sheet("判定统计", [12, 12, 10, 12, 10, 10, 20])
    header, row = model["stats"][0], model["stats"][1]
    stats.add([(cell, 1) for cell in header])
    stats.add([(int(cell) if cell.isdigit() else cell, 3) for cell in row])
    for note in model["stats_notes"]:
        stats.add([(note, 5)])
    sheets.append(stats)

    sheets.append(table_sheet(
        "未决事项", model["pending"], [6, 22, 40, 18, 30], center_cols={0},
    ))

    archive = Sheet("资料归档清单", [6, 62, 12])
    archive.add([("序号", 1), ("资料名称", 1), ("是否具备", 1)])
    for index, (_done, name) in enumerate(model["archive"], start=1):
        archive.add([(index, 3), (name, 2), ("是" if _done else "否", 3)])
    n = len(model["archive"]) + 1
    archive.validations.append((f"C2:C{n}", ["是", "否"]))
    archive.freeze = True
    archive.autofilter = True
    sheets.append(archive)

    sst: dict[str, int] = {}
    sheet_xml = [render_sheet(sheet, sst) for sheet in sheets]

    names = [s.name for s in sheets]
    if len(set(names)) != len(names):
        raise RuntimeError("工作表名称重复")

    rels = "".join(
        f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
        for i in range(len(sheets))
    )
    styles_id = len(sheets) + 1
    sst_id = len(sheets) + 2
    rels += (f'<Relationship Id="rId{styles_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             f'<Relationship Id="rId{sst_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>')

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="9303"/>'
        '<workbookPr defaultThemeVersion="164011"/>'
        '<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="24000" windowHeight="12000"/></bookViews>'
        '<sheets>'
        + "".join(f'<sheet name={attr(s.name)} sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, s in enumerate(sheets))
        + '</sheets><calcPr calcId="191029"/></workbook>'
    )

    sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{sum(1 for s in sheets for r in s.rows for v, _ in r if isinstance(v, str) and v != "")}" uniqueCount="{len(sst)}">'
        + "".join(f"<si><t xml:space=\"preserve\">{esc(text)}</t></si>" for text in sst)
        + "</sst>"
    )

    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(sheets))
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + overrides
        + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>'
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )

    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{esc(meta.get("项目名称", ""))} · 装配式装修验收清单</dc:title>'
        f'<dc:subject>{esc(meta.get("验收阶段", ""))}</dc:subject>'
        f'<dc:creator>装配式装修技能合集 · {TOOL_NAME}</dc:creator>'
        f'<cp:lastModifiedBy>{TOOL_NAME}</cp:lastModifiedBy>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{utc_stamp()}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{utc_stamp()}</dcterms:modified>'
        f'<dc:description>编制版本＝{esc(meta.get("编制版本", ""))}；生成时间＝{esc(generated)}；输入 SHA-256＝{sha256}</dc:description>'
        '</cp:coreProperties>'
    )
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        f'<Application>{TOOL_NAME}</Application><AppVersion>{TOOL_VERSION}</AppVersion>'
        '</Properties>'
    )

    payload = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        "docProps/core.xml": core,
        "docProps/app.xml": app,
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + rels + '</Relationships>'
        ),
        "xl/styles.xml": build_styles_xml(),
        "xl/sharedStrings.xml": sst_xml,
    }
    for i, xml in enumerate(sheet_xml, start=1):
        payload[f"xl/worksheets/sheet{i}.xml"] = xml

    return zip_bytes(payload)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def zip_bytes(payload: dict[str, str]) -> bytes:
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in payload.items():
            zf.writestr(name, content.encode("utf-8"))
    return buffer.getvalue()


# ---------------------------------------------------------------- DOCX writer

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def run_xml(text: str, bold: bool = False, size: int | None = None,
            color: str | None = None, italic: bool = False) -> str:
    props = ['<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="宋体"/>']
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    return (f'<w:r><w:rPr>{"".join(props)}</w:rPr>'
            f'<w:t xml:space="preserve">{esc(text)}</w:t></w:r>')


def para(text: str, style: str | None = None, **kwargs) -> str:
    ppr = []
    if style:
        ppr.append(f'<w:pStyle w:val="{style}"/>')
    ppr.append('<w:spacing w:before="0" w:after="120"/>')
    body = "".join(run_xml(line, **kwargs) for line in text.split("\n"))
    return f'<w:p><w:pPr>{"".join(ppr)}</w:pPr>{body}</w:p>'


def heading(level: int, text: str) -> str:
    return f'<w:p><w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr>{run_xml(text)}</w:p>'


def table_xml(rows: list[list[str]], widths: list[int], header: bool = True,
              size: int = 20) -> str:
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    trs = []
    for r_index, row in enumerate(rows):
        cells = []
        for c_index, value in enumerate(row):
            width = widths[c_index] if c_index < len(widths) else widths[-1]
            text = "" if value is None else str(value)
            bold = header and r_index == 0
            paragraph = (
                '<w:p><w:pPr><w:spacing w:before="20" w:after="20"/></w:pPr>'
                + run_xml(text, bold=bold, size=size)
                + '</w:p>'
            )
            shade = '<w:shd w:val="clear" w:color="auto" w:fill="D9E1F2"/>' if bold else ""
            cells.append(
                f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}</w:tcPr>{paragraph}</w:tc>'
            )
        trpr = '<w:trPr><w:tblHeader/></w:trPr>' if header and r_index == 0 else ""
        trs.append(f'<w:tr>{trpr}{"".join(cells)}</w:tr>')
    borders = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
        f'<w:tblBorders>{borders}</w:tblBorders></w:tblPr>'
        f'<w:tblGrid>{grid}</w:tblGrid>{"".join(trs)}</w:tbl>'
        + '<w:p><w:pPr><w:spacing w:before="0" w:after="120"/></w:pPr></w:p>'
    )


def build_docx(model: dict, source_name: str, sha256: str, generated: str) -> bytes:
    meta = model["meta"]
    body: list[str] = []
    body.append(para("装配式装修验收清单 · 方案摘要", style="Heading1"))
    body.append(para(
        f"编制版本：{meta.get('编制版本', '')} ｜ 编制日期：{meta.get('编制日期', '')} ｜ "
        f"生成时间：{generated}",
        size=18, color="808080",
    ))
    body.append(para(
        f"输入文件：{source_name} ｜ 输入 SHA-256：{sha256}",
        size=18, color="808080",
    ))
    body.append(para(
        "本摘要与同名 XLSX 清单一并交付；逐条检查项、专项表、整改闭环表见 XLSX。"
        "本文件不替代监理工程师或质量检查员的现场判定。",
        size=18, color="808080",
    ))

    body.append(heading(2, "一、项目基本信息"))
    info = [[k, meta.get(k, "")] for k in META_REQUIRED]
    body.append(table_xml(info, [2200, 7000], header=False, size=21))

    body.append(heading(2, "二、编制依据"))
    body.append(para(meta.get("编制依据", "")))
    standards: list[str] = []
    for table in (model["check"], model["mandatory"]):
        header = table[0]
        if "依据标准" not in header:
            continue
        idx = header.index("依据标准")
        for row in table[1:]:
            value = row[idx].strip() if idx < len(row) else ""
            if value and value not in standards:
                standards.append(value)
    if standards:
        body.append(para("清单条目引用的依据标准／来源（去重）：", bold=True))
        for item in standards:
            body.append(para(f"· {item}", size=20))

    body.append(heading(2, "三、适用条件"))
    body.append(para(
        f"验收阶段：{meta.get('验收阶段', '')}；空间范围：{meta.get('空间范围', '')}；"
        f"部品系统：{meta.get('部品系统', '')}。"
    ))
    for item in model["sources"]:
        body.append(para(f"· {item}", size=20))

    body.append(heading(2, "四、假设与限制"))
    body.append(para(meta.get("假设与限制", "")))

    body.append(heading(2, "五、清单构成与判定统计"))
    counts = [
        ["检查表", f"{len(model['check']) - 1} 项"],
        ["无法判定与带条件合格专项表", f"{max(len(model['special']) - 1, 0)} 项"],
        ["强制性条文检查项", f"{len(model['mandatory']) - 1} 项"],
        ["整改闭环表", f"{max(len(model['rectify']) - 1, 0)} 项"],
        ["未决事项", f"{max(len(model['pending']) - 1, 0)} 项"],
    ]
    body.append(table_xml([["构成", "数量"]] + counts, [5000, 4200], size=20))
    stats_rows = [model["stats"][0], model["stats"][1]]
    body.append(table_xml(stats_rows, [1200, 1300, 1200, 1300, 1100, 1100, 1900], size=18))
    for note in model["stats_notes"]:
        body.append(para(note, size=18, color="808080"))

    body.append(heading(2, "六、未决事项"))
    if len(model["pending"]) > 1:
        body.append(table_xml(model["pending"], [800, 2000, 3000, 1800, 1600], size=18))
    else:
        body.append(para("无"))

    body.append(heading(2, "七、资料归档要求"))
    for done, name in model["archive"]:
        body.append(para(f"{'☑' if done else '☐'} {name}", size=20))

    body.append(heading(2, "八、标准免责提示"))
    for item in model["disclaimer"]:
        body.append(para(item, size=18, color="595959"))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + "".join(body)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
          '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
          'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>'
          '</w:body></w:document>'
    )

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:styles xmlns:w="{W_NS}">'
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="宋体"/>'
        '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault>'
        '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
        '</w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="200"/><w:outlineLvl w:val="0"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="34"/><w:szCs w:val="34"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
        '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="1"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>'
        '</w:styles>'
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{esc(meta.get("项目名称", ""))} · 装配式装修验收清单方案摘要</dc:title>'
        f'<dc:creator>装配式装修技能合集 · {TOOL_NAME}</dc:creator>'
        f'<cp:lastModifiedBy>{TOOL_NAME}</cp:lastModifiedBy>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{utc_stamp()}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{utc_stamp()}</dcterms:modified>'
        f'<dc:description>编制版本＝{esc(meta.get("编制版本", ""))}；生成时间＝{esc(generated)}；输入 SHA-256＝{sha256}</dc:description>'
        '</cp:coreProperties>'
    )
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        f'<Application>{TOOL_NAME}</Application><AppVersion>{TOOL_VERSION}</AppVersion>'
        '</Properties>'
    )
    return zip_bytes({
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        "docProps/core.xml": core,
        "docProps/app.xml": app,
        "word/document.xml": document,
        "word/_rels/document.xml.rels": document_rels,
        "word/styles.xml": styles,
    })


def write_atomic(path: Path, data: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass
    parser = argparse.ArgumentParser(description="验收清单 Markdown → XLSX + DOCX")
    parser.add_argument("source", help="按 SKILL.md §5 模板填写的清单 Markdown")
    parser.add_argument("--out", help="输出目录（缺省＝输入文件所在目录）")
    parser.add_argument("--stem", help="输出文件名（缺省＝输入文件名）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的同名输出")
    args = parser.parse_args(argv)

    src = Path(args.source)
    if not src.is_file():
        print(f"[导出失败] 输入文件不存在：{src}", file=sys.stderr)
        return 2
    raw = src.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        print("[导出失败] 输入必须是 UTF-8 文本", file=sys.stderr)
        return 2

    model, problems = build_model(text)
    if problems:
        print(f"[导出拒绝] 输入有 {len(problems)} 处缺项/格式问题，未产出任何文件：", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 2

    digest = hashlib.sha256(raw).hexdigest()
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out_dir = Path(args.out) if args.out else src.parent
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[导出失败] 无法创建输出目录：{exc}", file=sys.stderr)
        return 1
    stem = args.stem or src.stem
    if not stem or stem in (".", "..") or stem != stem.strip() or re.search(r'[\\/:*?"<>|]', stem):
        print(f"[导出拒绝] 输出文件名非法：{stem!r}（只允许单个文件名，不含路径分隔符）",
              file=sys.stderr)
        return 2

    try:
        xlsx = build_xlsx(model, src.name, digest, generated)
        docx = build_docx(model, src.name, digest, generated)
    except Exception as exc:  # noqa: BLE001
        print(f"[导出失败] 生成过程出错：{exc}", file=sys.stderr)
        return 1

    xlsx_path = out_dir / f"{stem}.xlsx"
    docx_path = out_dir / f"{stem}.docx"
    targets = [xlsx_path, docx_path]
    existing = [str(p) for p in targets if p.exists()]
    if existing and not args.force:
        print("[导出拒绝] 目标文件已存在，未覆盖：", file=sys.stderr)
        for path in existing:
            print(f"  - {path}", file=sys.stderr)
        print("  确认覆盖请加 --force；或改用 --stem 指定新文件名。", file=sys.stderr)
        return 2
    backups = {path: path.read_bytes() for path in targets if path.exists()}
    try:
        write_atomic(xlsx_path, xlsx)
        write_atomic(docx_path, docx)
    except OSError as exc:
        for path in targets:
            if path in backups:
                try:
                    write_atomic(path, backups[path])
                except OSError:
                    pass
            elif path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
        print(f"[导出失败] 写盘失败：{exc}（已回滚本次改动）", file=sys.stderr)
        return 1

    print(f"[导出完成] {xlsx_path}")
    print(f"[导出完成] {docx_path}")
    if model["ignored_sections"]:
        print(f"  [提示] 以下章节未纳入导出：{'、'.join(model['ignored_sections'])}")
    print(f"  检查表 {len(model['check']) - 1} 项｜必检项 {len(model['mandatory']) - 1} 项｜"
          f"专项表 {max(len(model['special']) - 1, 0)} 项｜整改 {max(len(model['rectify']) - 1, 0)} 项｜"
          f"未决 {max(len(model['pending']) - 1, 0)} 项｜归档 {len(model['archive'])} 件")
    print(f"  输入 SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
