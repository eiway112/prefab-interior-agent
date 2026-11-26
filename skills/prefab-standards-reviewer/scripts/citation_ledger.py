#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准引用台账：核验身份、版本、前缀、用途与来源覆盖，不核验条文内容；Markdown 需 markdown-it-py。"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from bisect import bisect_right
from datetime import datetime
from pathlib import Path, PurePosixPath

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import delivery_layout as L  # noqa: E402

ROOT = L.ROOT_DEFAULT
DEFAULT_INDEX = ROOT / L.INDEX_REL
SCHEMA = "citation-ledger/v1"

# 标准号族。前缀分族（GB／GB/T 是两族，由 /T 决定），编号保留分部号（10801.2），年号可缺。
# 省代码只在后随斜杠时吸收（DB4401/T、DB 33/T），裸 DBJ|DB 须带 (?!\d) 防回溯把散文「DB21 发布文本」裂成「DB2 1」。
# 复合编号（省码-序号）限省码恰 2 位且后随年号或边界，否则 T/CSUS 03-2019 被切成 03-201、GBJ2－86 被切成 2-86。
# 第二成分本身即两位年号且落边界时不得读作序号：JGJ24-86 是编号 24＋年号 1986，不是复合编号「24-86」。
# 连字符变体覆盖集（P10/P15/P25）＝U+002D/U+2014/U+2013/U+FF0D/U+2011/U+2010/U+2012/U+2015/U+FE63/U+2212/U+2043/U+FE58，
# 年号分隔符同轨消费；集外四枚在标准号语境展示为分隔横杠（U+2212 减号字形同横杠，发起人第 28 轮裁定纳入），
# 漏消费则三入口漏年＋复合键截断（成员失真）。第 39 轮裁定（P25 读法A·两枚同批）纳入 U+2043 HYPHEN BULLET
# 与 U+FE58 SMALL EM DASH：仓内真实 PDF 真排版层记录载 U+2043 作连字符（2024⁃10⁃26、wood⁃based panel），
# 非纯项目符号；不纳入则图集/复合键截断且出口引真实索引键 rc1 双判词假红。U+2E3A/U+2E3B 实测同形但本轮
# 裁定留观察（钉现状保护布局：图集/复合键仍截断，出口引完整键仍 rc1 双判词假红＝明示残余）。图集族 ATLAS_RE 独立字符类不消费 _SEP，但与 _SEP 同轨扩集（第 34 轮 P21
# 五码位、第 39 轮 P25 两枚；保持独立硬编码结构）：集外横杠作图集分册号分隔符同样致键截断
# （15J939‒1 → 图集 15J939），出口键级对撞 rc 真实翻转。
# P18：前缀与 /T 大小写不敏感全族（小写 gb/t、混合 Gb/T 等一律识别），键经 norm_prefix 归一大写；ATLAS_RE 不在此列。
_SEP = r"[-—–－‑‐‒―﹣−⁃﹘]"
_YEAR = r"(?:19|20)\d{2}|[5-9]\d"
_COMPOUND = (r"\d{2}(?:\.\d{1,3})?" + _SEP
             + r"(?![5-9]\d\s*(?![\d.]))"
             + r"\d{1,3}(?=\s*" + _SEP + r"\s*(?:" + _YEAR + r")|\s*(?![\d.]))")
STD_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(T\s*/\s*(?:CECS|CSUS|CBDA)|RISN\s*" + _SEP + r"\s*TG"
    r"|DBJ\s*\d{2,4}(?=\s*/)|DB\s*\d{2,4}(?=\s*/)|DBJ(?=\d{2}\s*" + _SEP + r")|DBJ(?!\d)|DB(?!\d)"
    r"|GBJ|GB|JGJ|JG|JC|CJJ|CJ|CECS|SJG|QB|HG|HJ)"
    r"(?:\s*/\s*(T)?)?\s*"
    r"((?:" + _COMPOUND + r")|(?:\d{1,6}(?:\.\d{1,3})?))"
    r"(?:\s*" + _SEP + r"\s*(" + _YEAR + r"))?",
    re.I,
)

ATLAS_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(\d{2}(?!T\d)[A-Z]{1,3}\d{2,3}(?:[-—–－‑‐‒―﹣−⁃﹘]\d{1,2})?)"
    r"(?![A-Za-z0-9])"
)
ATLAS_PREFIX = "图集"

STATUS_PREFIXES = ("现行有效", "即将实施", "过渡期", "已废止", "被部分替代", "被部分废止", "被替代")
DEAD_PREFIXES = ("已废止", "被替代")
PARTIAL_PREFIXES = ("被部分替代", "被部分废止")
MENTION_WORDS = ("替代", "废止", "作废", "旧版", "原引用")
EXTERNAL_WORDS = ("源文件外", "评审依据", "外部依据")
_USAGE_PENDING = re.compile(r"是否|尚未确定|尚未明确|待确认|待核|用途未定|仅作资料|参考资料|需人工确认")
_USAGE_NOT_USE = re.compile(r"(?:不再|不|未|不得|不能|禁止)(?:采用|使用|执行|按|依照|作为|作依据|作设计依据)")
_USAGE_USE = re.compile(r"采用|选用|执行|依照|参照|遵循|根据|依据(?=\s*(?:[A-Z\d]|该|此))|按|取值|取\s*\d|不得低于|须达|应达")
_USAGE_CLAUSE = re.compile(r"第\s*[\d.]+\s*条|表\s*[\d.]+")
_SENTENCE_END = re.compile(r"[。；;！？|\t]")
_FRAGMENT_END = re.compile(r"[，,]|(?:但是|然而|但|而本)")


def citation_usage(line: str, start: int, end: int, tail: str = "") -> str:
    """引用点用途分级（use／mention／unknown）：按管辖本点的就近谓语判定，不按整行取或。

    判据＝主线 §4.1 步骤 4／5（发起人 2026-11-17 审计）：「作为依据」与「讨论其废止、替代关系」
    分开，粒度落到引用点；无法确定时返回 unknown 交人工确认，不得自动读作依据。
    词表属启发式而非语义理解——unknown 与 mention 一律降 WARN、不产 OK，即为该定位的承载。
    `tail`＝结构边界之后、同一展示块内被切掉的余文（仅作回指环视，见下），不入成员／年号窗。
    """
    boundaries = list(_SENTENCE_END.finditer(line))
    left = max((m.end() for m in boundaries if m.end() <= start), default=0)
    right = min((m.start() for m in boundaries if m.start() >= end), default=len(line))
    sentence = line[left:right]
    local_start, local_end = start - left, end - left
    fragments = list(_FRAGMENT_END.finditer(sentence))
    a = max((m.end() for m in fragments if m.end() <= local_start), default=0)
    b = min((m.start() for m in fragments if m.start() >= local_end), default=len(sentence))
    scope = sentence[a:b]
    following = sentence[b:]
    if not following:
        # 结构边界把同一展示块内的句子切成两段时（第 37 轮回件 O3 的在册后果，本轮按第 47 轮
        # 追加同族口径「把实际采用判成提及而豁免＝造向」改判开修），边界后的回指式谓语仍约束
        # 本点；只接边界后首个非空段（块内无后续边界时即余文全体），且仍须满足下条的
        # 「不带新编号＋显式回指」双判据——无回指不接管（否则把另一展示行的谓语误归本点，
        # r47_probe6 的表格式 OCR 实证）。跨展示块（.html／.docx 的源行腿）不接管＝P43 在册。
        following = next((seg for seg in tail.split("\x00") if seg.strip()), "")
    # 只把不带新编号的后续谓语归给当前点，避免旧版讨论污染另一采用点。
    if (not STD_RE.search(following) and not ATLAS_RE.search(following)
            and re.search(r"该(?:标准|规范|图集|版本)|同编号|是否.*(?:采用|依据|执行)|尚未确定|不得.*依据", following)):
        scope += following
    if _USAGE_PENDING.search(scope):
        return "unknown"
    negs = list(_USAGE_NOT_USE.finditer(scope))
    poss = [m for m in _USAGE_USE.finditer(scope)
            if not any(n.start() <= m.start() and m.end() <= n.end() for n in negs)]
    # 否定词只约束它自己引导的谓语，不得把同行其余谓语或整个编号一并豁免（主线 §4.1 步骤 4）。
    # 取管辖本引用点的就近谓语：中文谓语在宾语前，故先看引用点之前最近的一个，前侧无谓语时
    # 取后侧最近的一个；被否定词包住的采用谓语不算未否定的采用（「不得按 X 取值」仍属否定）。
    anchor = local_start - a
    pre = [m for m in negs + poss if m.end() <= anchor]
    post = [m for m in negs + poss if m.end() > anchor]
    near = max(pre, key=lambda m: m.end()) if pre else \
        (min(post, key=lambda m: m.start()) if post else None)
    if near is not None and not any(near is n for n in negs):
        return "use"
    if negs:
        # 转折后的明确采用仍须核验，不能由前面的“不采用”豁免。
        parts = re.split(r"但是|然而|但|而本", scope)
        if len(parts) > 1 and _USAGE_USE.search(parts[-1]) and not _USAGE_NOT_USE.search(parts[-1]):
            return "use"
        return "mention"
    if any(word in scope for word in MENTION_WORDS):
        return "mention"
    if _USAGE_CLAUSE.search(scope):
        return "use"
    return "unknown"


class LedgerError(ValueError):
    pass


def norm_prefix(raw: str, t_flag: str | None) -> str:
    p = re.sub(r"\s+", "", raw).upper().replace("—", "-")
    if p.startswith("DBJ") or p.startswith("DB"):
        return p + ("/T" if t_flag else "")
    if p in ("T/CECS", "RISN-TG", "GBJ", "CECS"):
        return p
    return p + ("/T" if t_flag else "")


def norm_year(y: str | None) -> str | None:
    """两位年号归一 19xx（中国标准两位年号只见于 1950s—1990s）；不归一则废止旧版被判「现行有效」。"""
    return f"19{y}" if y and len(y) == 2 else y


def norm_num(n: str) -> str:
    """编号内全角／长连字符归一 ASCII，使 `DBJ 46－061` 与 `DBJ 46-061` 同键（索引侧用全角）。
    归一集＝U+2014/U+2013/U+FF0D/U+2011/U+2010/U+2012/U+2015/U+FE63/U+2212/U+2043/U+FE58，与 _SEP 变体集同轨（P10/P15/P25）：
    不归一则复合编号键留原码位，与 ASCII 形覆盖/归因对撞失配。"""
    return re.sub(r"[—–－‑‐‒―﹣−⁃﹘]", "-", n)


# OCR/排版常见异写：`GB T33544`／`GBT33544`／`GB / T 33544` → `GB/T 33544`。
# 只归一「前缀＋T＋数字」三连，不碰正文里的字母 T。P18：大小写不敏感全族，小写 gbt/gb t 同归一。
_T_GLUE = re.compile(r"(?<![A-Za-z])(GBJ|GB|JGJ|JG|JC|CJJ|CJ|DBJ?\d{0,6})(\s*/\s*|\s*)T(?=\s*\d)", re.I)


def prettify(line: str) -> str:
    return _T_GLUE.sub(lambda m: f"{m.group(1)}/T", line)


from html.parser import HTMLParser


class _VisibleHTML(HTMLParser):
    """静态显式结构；不模拟 CSS 级联与继承（内联 style 的显式 display/visibility 除外，见下）、
    隐式树修复、控件状态或xmp/plaintext等特殊内容模式。
    noscript 按无脚本静态口径视为可见（load_text 不执行脚本或 CSS）。
    hidden 布尔属性按显式隐藏消费（子树不计入可见面、位置留结构边界防跨隐藏拼接）；
    内联 style 属性的显式 display:none 与 visibility:hidden／collapse 按 hidden 轨消费
    （P32，发起人 2026-11-21 第 45 轮当日面板回裁逐字「只吃内联 style 子集，同批修」）：
    子树不计入可见面、元素位置留结构边界、同标签嵌套按深度、未闭仍 fail-closed；重复 style
    属性按 UA 规则首值生效（第 45 枚回件 N1：void 族两支分判——display:none 与 hidden 属性
    ＝自布局移除故不留边界（沿 P17），visibility:hidden·collapse＝占位不显墨，br／hr／img
    的结构边界照可见轨保留、input 的 value 不入面）。类选择器与 <style> 规则级联仍属 CSS 模拟排除（不模拟口径的
    射程自本批起收窄，a4 读数留在册面）；子元素以 visibility:visible 撤销父级隐藏未建模＝
    保守残余（只漏不造）；控件状态仍属排除。
    rt 注音层（rendering.html 默认 display:ruby-text）非空时自成结构边界、不拼入基字流，
    注音文本仍计入可见面；空 rt／纯空白注音不拆基字。基字跨 rt 的编号年号保守不拼＝
    最小修残余（只漏不造，ruby 感知双流未做）；未闭 rt 沿未闭合非隐藏结构标签既有口径。
    替换元素（iframe/canvas/video/audio/object）的 fallback 子内容仅在 UA 不支持该元素时
    渲染，不计入可见面；元素位置留结构边界（沿 img alt 先例）防两侧文本跨替换元素拼接。
    progress/meter 的 fallback 与 canvas 完全同构，同轨纳入（P23，发起人 2026-11-19
    第 37 轮面板裁定读法 A）。
    object 取「资源可渲染」默认展示态（发起人 2026-11-18 裁定读法 A）；资源不可渲染时
    fallback 真会上屏的引用漏检＝保守残余（只漏不造）。同标签嵌套按深度消费；REPLACED
    族异标签交错闭合也按静态嵌套入栈、endtag 只弹顶帧、不匹配则忽略（P30，发起人
    2026-11-21 第 44 轮面板裁定读法 A／仅 REPLACED 族入栈），非 REPLACED 内层不入栈
    （射程外）；未闭替换元素子树吞并至 EOF（只漏不造，不新增拒判面）；子树内隐藏容器
    仍沿既有隐藏栈轨。
    SVG desc 是元数据、从不视觉渲染，归固有隐藏容器（与 title 同类，不占位故不留边界）；
    SVG metadata 元素不在本轮登记家族（P13 登记字面＝desc），行为未改。
    显示型 input 的 value 按显示内容消费（发起人 2026-11-19 裁定读法 A，非交互态）：
    type∈text/search/url/tel/password/submit/button/reset（缺省/空=text）时 value 经
    handle_data 计入可见面，input 仍 void 不留边界（P17）；非显示型（hidden/checkbox/
    radio/file/image/range/color/date 等）与集外/未知 type（email/number 等）钉现状不消费
    ＝保守残余（只漏不造）。控件交互态（checked/disabled 等）仍属排除 1 不模拟。
    select 闭合态属默认渲染态（发起人 2026-11-19 裁定读法 A，非交互态）：仅选中项
    （selected 属性；无 selected 取首项，多 selected 末项为准＝HTML 单选重置语义）入
    可见面，select 位置留结构边界（沿 img/REPLACED 先例）防跨控件拼年；multiple 全项
    上屏、option 间留结构边界。option 不可嵌套、</option> 可省略，兄弟 option 隐式闭合
    按新选项起算（仅 option 族如此，其余标签隐式树修复仍不模拟）。size>1 无
    multiple 的列表框形态钉现状按闭合态处理＝保守残余（只漏不造）；未闭 select 沿未闭
    合非隐藏结构标签既有口径（照常抽出，不新增拒判面）；同标签嵌套按深度消费（沿
    hidden/fallback 轨）。
    select/optgroup 直属非 option 文本（含 pretty-print 空白）同属非渲染内容，不入
    可见面（发起人 2026-11-21 第 42 轮面板裁定读法 A＋直属+optgroup 文本）；option
    内文本与 multiple 全项不变；嵌套 select 在 option 内属未建模畸形标记，沿同标签
    嵌套按深度消费口径不动＝载录残余。
    `<![CDATA[` 在非外来内容中不是 CDATA 段：WHATWG 解析为 bogus comment、止于首个 `>`，
    其后内容按正常标记与文本上屏（发起人 2026-11-21 第 40 轮面板裁定「修·按 WHATWG」）。
    无内部 `>` 的 `<![CDATA[内容]]>` 整个落在注释内、与浏览器一致不上屏；SVG/MathML
    外来内容中的真 CDATA 段未建模＝只漏不造残余；未闭（无 `>`）仍拒判，不新增 fail-open。
    markdown fence／缩进代码块在 md 层独立成块，但其渲染结果仍嵌套在当前未闭合的
    REPLACED fallback 或 select 抑制区内（浏览器不上屏）；_visible_blocks 的
    fence/code_block 分支与 hidden 同口径消费 fallback／select 静默态（P29，发起人
    2026-11-21 第 43 轮面板裁定读法 A）；option 内 fence 按 option 文本消费不动。"""
    # 默认结构取 https://html.spec.whatwg.org/multipage/rendering.html；CommonMark块名不等于显示边界。
    BLOCKS = {
        "html", "body",
        "address", "blockquote", "center", "dialog", "div", "figure", "figcaption", "footer", "form",
        "header", "legend", "listing", "main", "p", "pre", "search",
        "article", "aside", "h1", "h2", "h3", "h4", "h5", "h6", "hgroup", "nav", "section",
        "dir", "dd", "dl", "dt", "menu", "ol", "ul", "li",
        "table", "caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr", "td", "th",
        "fieldset", "details", "summary",
    }

    HIDDEN_CONTAINERS = {"datalist", "desc", "head", "noembed", "noframes", "rp", "script", "style", "template", "title"}
    HIDDEN_VOID = {"area", "base", "basefont", "link", "meta", "param"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}
    # 替换元素：fallback 子内容仅在 UA 不支持该元素时渲染，支持浏览器（含无脚本静态态）不上屏。
    # P23：progress/meter 与 canvas 完全同构，同批纳入（发起人 2026-11-19 第 37 轮面板裁定读法 A）。
    REPLACED = {"audio", "canvas", "iframe", "meter", "object", "progress", "video"}
    # 隐藏的 RAWTEXT/RCDATA 只找自身结束标签，内容里的伪标签不能改变隐藏栈。
    CDATA_CONTENT_ELEMENTS = ("script", "style", "title", "noembed", "noframes")
    # P19：显示型 input 的 value 是上屏文本（发起人 2026-11-19 裁定读法 A＋type 感知）。
    # 仅此族消费 value；非显示型（hidden/checkbox/radio/file/image/range/color/date 等）
    # 与集外/未知 type（email/number 等）钉现状不消费＝保守残余（只漏不造）。
    INPUT_VALUE_TYPES = {"text", "search", "url", "tel", "password", "submit", "button", "reset"}
    # P32：只消费内联 style 属性里的显式 display:none／visibility:hidden·collapse
    # （声明须以分号或串尾收口，允许 !important；display 取其余值、visibility:visible 不隐）。
    # 第 45 枚回件 N1：两支须分判——display:none 把元素自布局移除（不占位、无框），
    # visibility 只隐自身墨迹而保留占位框（br 仍换行、img／hr 仍留框）。
    STYLE_DISPLAY_NONE = re.compile(
        r"(?:^|;)\s*display\s*:\s*none\s*(?:!\s*important)?\s*(?:;|$)", re.I)
    STYLE_VISIBILITY_HIDDEN = re.compile(
        r"(?:^|;)\s*visibility\s*:\s*(?:hidden|collapse)\s*(?:!\s*important)?\s*(?:;|$)", re.I)

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []  # 隐藏帧栈 [tag, depth]：depth 消费同标签嵌套，内层闭合不提前解除外层隐藏
        self.rt_frames = []  # rt 注音帧 [parts长度, 进入时边界态, 有非空白内容]：非空注音自成边界，空注音不拆基字
        self.fallback = []  # 替换元素帧栈 [tag, depth]：子树不计入可见面，同标签嵌套按深度消费
        self.selects = []  # P22 select 帧栈：闭合态仅选中项上屏，同标签嵌套按深度消费
        self.select_retract = None  # P22 selected 重traction 事件（parts 截断点），md 行内路径同步截断 chars/spans/breaks
        self.pre_depth = 0
        self.at_boundary = True

    def reset(self):
        super().reset()
        # P26：新版解析器（本机 3.14 有该开关）把 `<![CDATA[` 按 CDATA 段消费至 `]]>`；
        # 关掉即走 WHATWG bogus comment（止于首个 `>`）。无此开关的版本由
        # parse_marked_section 覆写同轨。
        if hasattr(self, "_set_support_cdata"):
            self._set_support_cdata(False)

    def parse_marked_section(self, i, report=1):
        # P26（≤3.12 路径）：只改 cdata 标记的止法，其余标记段沿基类。
        raw = self.rawdata
        if raw[i:i + 9].lower() == "<![cdata[":
            end = raw.find(">", i + 2)
            return -1 if end < 0 else end + 1
        return super().parse_marked_section(i, report)

    def _select_silent(self):
        # P28：select 帧活跃且当前不在 option 内（select/optgroup 直属文本不渲染）或
        # 非选中 option 被抑制，均不入可见面；option 内文本与 multiple 全项照旧。
        return bool(self.selects) and (self.selects[-1]["suppress"]
                                       or not self.selects[-1]["opt"])

    def _style_hidden(self, attrs):
        # P32：重复 style 属性按 UA 规则首值生效（与 input/select 的 setdefault 同口径）；
        # 只读内联声明，不解析 <style> 规则与类选择器级联。返回 (display:none?, visibility 隐?)。
        for name, value in attrs:
            if name == "style":
                decl = value or ""
                return (bool(self.STYLE_DISPLAY_NONE.search(decl)),
                        bool(self.STYLE_VISIBILITY_HIDDEN.search(decl)))
        return (False, False)

    def boundary(self):
        if self._select_silent():
            return
        if not self.at_boundary:
            self.parts.append("\n")
        self.at_boundary = True

    def check_complete(self):
        if self.hidden:
            raise LedgerError(f"隐藏容器未闭合 {[f[0] for f in self.hidden]}，无法可靠判定可见文本，须修正输入后重试")
        if self.rawdata and not self.rawdata.startswith("<!--"):
            raise LedgerError("HTML 语法未闭合，无法可靠提取可见文本，须修正输入后重试")

    def parse_comment(self, i, report=1):
        # Python 3.10 的注释终止规则不含 WHATWG 突闭形态；仅在解析器识别到注释后补齐。
        raw = self.rawdata
        for opening in ("<!-->", "<!--->"):
            if raw.startswith(opening, i):
                return i + len(opening)
        end = re.search(r"-->|--!>", raw[i + 4:])
        return i + 4 + end.end() if end else -1

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN_VOID:
            return
        hidden_attr = any(k == "hidden" for k, _ in attrs)
        # P32：内联 style 的显式 display:none／visibility:hidden·collapse 与 hidden 属性同轨。
        disp_none, vis_hidden = self._style_hidden(attrs)
        styled_hidden = disp_none or vis_hidden
        if (hidden_attr or styled_hidden) and tag in self.VOID:
            # P17：hidden void 不占位、无隐藏子树，留边界反切断连续文本致漏年（全 void 族统一）；
            # display:none 的 void 同判。第 45 枚回件 N1：visibility 只隐墨不除框，br／hr／img
            # 的结构边界须照可见轨保留，否则屏上不同行的两段被拼成源侧不存在的完整成员＝造向。
            if (not (hidden_attr or disp_none) and not self.hidden
                    and tag in ("br", "hr", "img")):
                self.boundary()
            return
        causes_hidden = (tag in self.HIDDEN_CONTAINERS
                         or ((hidden_attr or styled_hidden) and tag not in self.VOID))
        if self.hidden:
            if tag == self.hidden[-1][0]:
                self.hidden[-1][1] += 1
            elif causes_hidden:
                self.hidden.append([tag, 1])
            return
        if causes_hidden:
            self.hidden.append([tag, 1])
            if tag not in self.HIDDEN_CONTAINERS:
                # hidden 属性与内联 style 显式隐藏元素的位置留结构边界，防两侧可见文本跨
                # 隐藏拼接出新编号（H6）；固有隐藏容器沿既有口径不留边界（行内容器仍拼合）。
                self.boundary()
            return
        if self.fallback:
            if tag == self.fallback[-1][0]:
                self.fallback[-1][1] += 1
            elif tag in self.REPLACED:
                # P30：异标签 REPLACED 交错也入栈；endtag 只弹顶帧，不匹配则忽略
                #（严格静态嵌套），防外层 endtag 提前解除内层抑制。
                self.fallback.append([tag, 1])
            return
        if tag in self.REPLACED:
            # fallback 不渲染；元素位置留结构边界，防两侧可见文本跨替换元素拼接出新编号。
            self.fallback.append([tag, 1])
            self.boundary()
            return
        if tag == "input":
            # P19：显示型 input 的 value 经 handle_data 入可见面；input 仍 void 不留边界（P17）。
            ad = {}
            for k, v in attrs:
                ad.setdefault(k, v)
            itype = (ad.get("type") or "text").lower()
            value = ad.get("value")
            if itype in self.INPUT_VALUE_TYPES and value:
                self.handle_data(value)
            return
        if tag == "select":
            # P22：闭合 select 仅选中项上屏；multiple 全项上屏。元素位置留结构边界。
            if self.selects:
                self.selects[-1]["depth"] += 1
                return
            ad = {}
            for k, v in attrs:
                ad.setdefault(k, v)
            self.boundary()
            self.selects.append({"depth": 1, "multiple": "multiple" in ad,
                                 "mark": len(self.parts), "ab": self.at_boundary,
                                 "chosen": False, "opt": False,
                                 "suppress": False, "seen": False})
            return
        if tag == "option" and self.selects:
            f = self.selects[-1]
            if f["opt"]:
                # </option> 可省略：新 option 起算即隐式闭合前一 option（option 不可嵌套）。
                f["opt"] = False
                f["suppress"] = False
            f["opt"] = True
            if f["multiple"]:
                f["suppress"] = False
                self.boundary()
            else:
                if any(k == "selected" for k, _ in attrs):
                    # 单选重置语义：末个 selected 为准，撤回此前暂显的首项/前选中项文本。
                    del self.parts[f["mark"]:]
                    self.at_boundary = f["ab"]
                    self.select_retract = f["mark"]
                    f["chosen"] = True
                    f["suppress"] = False
                else:
                    f["suppress"] = f["chosen"] or f["seen"]
                f["seen"] = True
            return
        if tag in self.BLOCKS or tag in ("br", "hr"):
            self.boundary()
        if tag == "rt":
            self.rt_frames.append([len(self.parts), self.at_boundary, False])
        if tag in ("pre", "listing"):
            self.pre_depth += 1
        elif tag == "img":
            # alt 仅在加载失败时显示，不计入可见面；图像位置作结构边界，防两侧文本跨图拼接。
            self.boundary()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in self.CDATA_CONTENT_ELEMENTS:
            self.set_cdata_mode(tag)
        if tag == "rt" and not self.hidden:
            self.handle_endtag(tag)
        if tag in self.REPLACED and not self.hidden:
            self.handle_endtag(tag)
        if tag in ("select", "option") and not self.hidden:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1][0]:
                self.hidden[-1][1] -= 1
                if self.hidden[-1][1] == 0:
                    self.hidden.pop()
            return
        if self.fallback:
            if tag == self.fallback[-1][0]:
                self.fallback[-1][1] -= 1
                if self.fallback[-1][1] == 0:
                    self.fallback.pop()
            return
        if tag == "option":
            if self.selects and self.selects[-1]["opt"]:
                self.selects[-1]["opt"] = False
                self.selects[-1]["suppress"] = False
            return
        if tag == "select":
            if self.selects:
                self.selects[-1]["depth"] -= 1
                if self.selects[-1]["depth"] == 0:
                    self.selects.pop()
                    self.boundary()
            return
        if tag in self.BLOCKS:
            self.boundary()
        if tag == "rt" and self.rt_frames:
            mark, was_boundary, has_content = self.rt_frames.pop()
            if has_content:
                if not was_boundary:
                    self.parts.insert(mark, "\n")
                self.boundary()
        if tag in ("pre", "listing"):
            self.pre_depth = max(0, self.pre_depth - 1)

    def handle_data(self, data):
        if (not self.hidden and not self.fallback
                and not self._select_silent() and data):
            value = (data.replace("\r\n", "\n").replace("\r", "\n") if self.pre_depth
                     else re.sub(r"[\t\n\f\r ]+", " ", data))
            self.parts.append(value)
            self.at_boundary = value.endswith("\n")
            if self.rt_frames and value.strip():
                self.rt_frames[-1][2] = True


def _html_visible_text(text: str) -> str:
    parser = _VisibleHTML()
    parser.feed(text)
    parser.check_complete()
    return "".join(parser.parts)


def _markdown_parser():
    try:
        from markdown_it import MarkdownIt
        from markdown_it.rules_inline.state_inline import StateInline
        from markdown_it.rules_block.state_block import StateBlock
    except ImportError as exc:
        raise LedgerError("Markdown 解析依赖缺失：请安装 markdown-it-py==4.0.0；不得按零引用通过") from exc

    class LocatedInline(StateInline):
        def pushPending(self):
            content = self.pending
            start = self.src.rfind(content, 0, self.pos)
            if start < 0:
                raise LedgerError("Markdown 文本无法对应原文位置")
            token = super().pushPending()
            token.meta["source_span"] = (start, start + len(content))
            return token

    class LocatedBlock(StateBlock):
        def getLines(self, begin, end, indent, keepLastLF):
            value = super().getLines(begin, end, indent, keepLastLF)
            spans = []
            for row in range(begin, end):
                first = line_start = self.bMarks[row]
                last = self.eMarks[row] + (row + 1 < end or keepLastLF)
                columns = 0
                while first < last and columns < indent:
                    char = self.src[first]
                    if char == "\t":
                        columns += 4 - (columns + self.bsCount[row]) % 4
                    elif char == " " or first - line_start < self.tShift[row]:
                        columns += 1
                    else:
                        break
                    first += 1
                spans.extend([(first - 1, first)] * max(0, columns - indent))
                spans.extend((i, i + 1) for i in range(first, min(last, len(self.src))))
            self.extracted_lines = (value, spans)
            return value

    def located_code(rule):
        def run(state, start, end, silent):
            count = len(state.tokens)
            matched = rule(state, start, end, silent)
            if matched and not silent:
                token = state.tokens[count]
                value, spans = state.extracted_lines
                if value.rstrip("\n") != token.content.rstrip("\n"):
                    raise LedgerError("Markdown 代码内容与解析范围不符")
                token.meta["block_spans"] = spans[:len(token.content.rstrip("\n"))]
            return matched
        return run

    def located_table(rule):
        def run(state, start, end, silent):
            count = len(state.tokens)
            matched = rule(state, start, end, silent)
            if not matched or silent:
                return matched
            row_cells, cell_indices = {}, {}
            for token in state.tokens[count:]:
                if token.type != "inline":
                    continue
                row = token.map[0]
                if row not in row_cells:
                    a, b = state.bMarks[row] + state.tShift[row], state.eMarks[row]
                    raw = state.src[a:b]
                    a += len(raw) - len(raw.lstrip())
                    raw = raw.strip()
                    cells = [("", [])]
                    for i, char in enumerate(raw):
                        value, spans = cells[-1]
                        if char == "|" and (i == 0 or raw[i - 1] != "\\"):
                            cells.append(("", []))
                        elif char == "|":
                            cells[-1] = (value[:-1] + char, spans[:-1] + [(a + i - 1, a + i + 1)])
                        else:
                            cells[-1] = (value + char, spans + [(a + i, a + i + 1)])
                    if cells and not cells[0][0]:
                        cells.pop(0)
                    if cells and not cells[-1][0]:
                        cells.pop()
                    row_cells[row] = cells
                    cell_indices[row] = 0
                index = cell_indices[row]
                cells = row_cells[row]
                value, spans = cells[index] if index < len(cells) else ("", [])
                if value.strip() != token.content:
                    raise LedgerError("Markdown 单元格内容与解析范围不符")
                left = len(value) - len(value.lstrip())
                token.meta["block_spans"] = spans[left:left + len(token.content)]
                cell_indices[row] += 1
            return matched
        return run

    def parse_block(src, md, env, tokens):
        state = LocatedBlock(src, md, env, tokens)
        md.block.tokenize(state, state.line, state.lineMax)
        return state.tokens

    def located_rule(rule):
        def run(state, silent):
            start, count = state.pos, len(state.tokens)
            matched = rule(state, silent)
            if matched and not silent:
                for token in state.tokens[count:]:
                    token.meta.setdefault("source_span", (start, state.pos))
            return matched
        return run

    def parse_inline(src, md, env, tokens):
        state = LocatedInline(src, md, env, tokens)
        md.inline.tokenize(state)
        for rule in md.inline.ruler2.getRules(""):
            rule(state)
        return state.tokens

    parser = MarkdownIt("commonmark").enable("table")
    # 合并文本节点会丢失实体、转义和强调各自的原始跨度。
    parser.core.ruler.disable("text_join")
    parser.inline.ruler2.disable("fragments_join")
    names = parser.inline.ruler.get_active_rules()
    for name, rule in zip(names, parser.inline.ruler.getRules("")):
        parser.inline.ruler.at(name, located_rule(rule))
    parser.inline.parse = parse_inline
    for name, rule in zip(parser.block.ruler.get_active_rules(), parser.block.ruler.getRules("")):
        if name in ("fence", "code"):
            parser.block.ruler.at(name, located_code(rule))
        elif name == "table":
            parser.block.ruler.at(name, located_table(rule))
    parser.block.parse = parse_block
    return parser


def _inline_chars(tokens, source, parser=None):
    chars, spans, breaks = [], [], []
    parser = parser if parser is not None else _VisibleHTML()
    select_marks = [0] * len(parser.selects)
    for token in tokens:
        start, end = token.meta["source_span"]
        kind = token.type
        if kind == "html_inline":
            before = len(parser.parts)
            parser.feed(token.content)
            if parser.select_retract is not None:
                # P22 selected 重traction：同步截断本块已产出的 chars/spans/breaks。
                m = select_marks[-1] if select_marks else 0
                del chars[m:]
                del spans[m:]
                breaks[:] = [b for b in breaks if b < m]
                parser.select_retract = None
            value = "".join(parser.parts[before:]).replace("\n", "\x00")
            origins = [(start, end)] * len(value)
            while len(select_marks) < len(parser.selects):
                select_marks.append(len(chars) + len(value))
            while len(select_marks) > len(parser.selects):
                select_marks.pop()
        elif (parser.hidden or parser.fallback or parser._select_silent()):
            continue
        elif kind in ("softbreak", "hardbreak"):
            breaks.append(len(chars))
            value = " " if kind == "softbreak" and not parser.pre_depth else "\x00"
            origins = [(start, end)]
        elif kind == "code_inline":
            a, b = start + len(token.markup), end - len(token.markup)
            value = source[a:b].replace("\n", " ")
            if value.startswith(" ") and value.endswith(" ") and value.strip():
                a, b, value = a + 1, b - 1, value[1:-1]
            origins = [(i, i + 1) for i in range(a, b)]
        elif kind == "image":
            # alt 仅在加载失败时显示，不计入可见面；图像位置按结构边界消费（同硬换行轨），
            # 防两侧文本跨图拼接成新编号。
            breaks.append(len(chars))
            value = "\x00"
            origins = [(start, end)]
        elif kind in ("text", "text_special"):
            value = token.content
            if source[start:end] == value:
                origins = [(i, i + 1) for i in range(start, end)]
            elif source[start:end] == f"<{value}>":
                # 自动链接 text 节点的跨度含尖括号；源片恰为 <value> 时剥壳逐字映射。
                origins = [(i, i + 1) for i in range(start + 1, end - 1)]
            else:
                origins = [(start, end)] * len(value)
        else:
            continue
        if value:
            parser.at_boundary = value.endswith("\x00")
        chars.extend(value)
        spans.extend(origins)
    return "".join(chars), spans, breaks


def _source_chars(content, lines, offsets, start, end, cursors):
    spans = []
    row = start
    for part_no, part in enumerate(content.split("\n")):
        found = False
        while row < min(end, len(lines)):
            raw = lines[row].rstrip("\r\n")
            positions = [(offsets[row] + i, offsets[row] + i + 1) for i in range(len(raw))]
            pos = raw.find(part, cursors.get(row, 0))
            if pos >= 0:
                if part_no:
                    previous_end = spans[-1][1] if spans else offsets[row]
                    spans.append((previous_end, offsets[row] + pos))
                spans.extend(positions[pos:pos + len(part)])
                cursors[row] = pos + len(part)
                found = True
                row += 1
                break
            row += 1
        if not found:
            raise LedgerError("Markdown 内容无法对应原文范围，不生成虚假位置")
    return spans


def _source_line_offsets(text):
    return [0] + [m.end() for m in re.finditer(r"\r\n|\r|\n", text)]


# P33 首块 frontmatter（发起人 2026-11-21 第 45 轮当日面板回裁逐字「判不上屏，同批修」）。
# 口径取渲染器惯例（GFM／Obsidian／站点生成器／本仓 SKILL.md 均不把 frontmatter 渲染上屏），
# 该口径属发起人裁定而非规范推导——CommonMark 字面读法（首块 --- 是 thematic break，其后
# key: value 行是段落或被 --- 作 setext 下划线）自本批起作废，不作正确性依据。
_FM_FENCE = re.compile(r"\ufeff?---[ \t]*$")
# 第 45 枚回件 N3：键字符类取 Unicode（`标题: x` 与 `"title": x` 同属顶层 key: value 形，
# 原 ASCII-only 类把中文键 frontmatter 判成普通段落＝成员残留上屏）；全角冒号 `：` 不作键，
# 冒号后无空格（`title:x`，YAML 里是标量而非映射）与纯序列块仍按「无 key: value」上屏＝钉现状。
_FM_KEY = re.compile(r"""^(?:"[^"\n]*"|'[^'\n]*'|[^\W\d][\w.\- ]*):(?:\s|$)""")


def _frontmatter_lines(text: str) -> int:
    """md 首块 YAML frontmatter 占用的源行数（含闭合围栏），非 frontmatter 返回 0。
    判定＝首行恰为 ---（允许尾随空白与 BOM）＋其后存在恰为 --- 的闭合行＋块内至少一行
    顶层 key: value；闭合行之前出现的内容仍在面（无闭合围栏不作 frontmatter）。
    分行规则与 `_source_line_offsets` 同集（\r\n｜\r｜\n），不用 str.splitlines——后者另认
    \x0b\x0c\x1c-\x1e\x85\u2028\u2029，会使行界与 token.map 错位而多吞正文行。"""
    lines = re.split(r"\r\n|\r|\n", text)
    if len(lines) < 3 or not _FM_FENCE.match(lines[0]):
        return 0
    for i in range(1, len(lines)):
        if _FM_FENCE.match(lines[i]):
            return i + 1 if any(_FM_KEY.match(line) for line in lines[1:i]) else 0
    return 0


def _visible_blocks(text: str, normalized: bool = False):
    offsets = _source_line_offsets(text)
    lines = [text[a:b] for a, b in zip(offsets, offsets[1:] + [len(text)])]
    if normalized:
        for offset, line in zip(offsets, lines):
            value = line.rstrip("\r\n")
            yield value, [(offset + i, offset + i + 1) for i in range(len(value))], [], "visible_text"
        return
    parser = _markdown_parser()
    html = _VisibleHTML()
    # P33：首块 frontmatter 整段不入可见面——按 token.map 行界跳过产出，不改源文本，
    # 故后续行的可见行序号与跨度映射逐字不变（跨度维硬约束）。
    fm_end = _frontmatter_lines(text)
    block_start, block_end = 0, len(lines)
    normalized_spans = [m.span() for m in re.finditer(r"\r\n|[\s\S]", text)]
    cursors = {}
    for token in parser.parse(text):
        if fm_end and token.map and token.map[0] < fm_end:
            continue
        if token.map:
            block_start, block_end = token.map
        block_spans = token.meta.get("block_spans")
        if block_spans is not None:
            block_spans = [(normalized_spans[a][0], normalized_spans[b - 1][1])
                           for a, b in block_spans]
        if token.type == "inline":
            source_spans = block_spans if block_spans is not None else _source_chars(
                token.content, lines, offsets, block_start, block_end, cursors)
            plain, local_spans, breaks = _inline_chars(token.children or [], token.content, html)
            mapped = [(source_spans[a][0], source_spans[b - 1][1]) for a, b in local_spans]
            yield plain, mapped, breaks, "source_span"
        elif token.type in ("fence", "code_block"):
            # P29：代码块虽独立成块，渲染结果仍在未闭 fallback／select 抑制区内，不上屏。
            if html.hidden or html.fallback or html._select_silent():
                continue
            value = token.content.rstrip("\n")
            if value:
                if block_spans is None or len(block_spans) != len(value):
                    raise LedgerError("Markdown 代码原文范围缺失")
                yield value.replace("\n", "\x00"), block_spans, [], "source_span"
        elif token.type == "html_block":
            before = len(html.parts)
            html.feed(token.content)
            html.select_retract = None
            plain = "".join(html.parts[before:])
            a = offsets[block_start]
            b = offsets[block_end] if block_end < len(offsets) else len(text)
            for part in plain.split("\n"):
                yield part, [(a, b)] * len(part), [], "source_block"
    html.check_complete()


def visible_text(line: str) -> str:
    return "\n".join(plain.replace("\x00", "\n") for plain, _, _, _ in _visible_blocks(line))


# 零宽不可见字符五码位集（P16；发起人第 29 轮裁定：提取层整体剔除、可见面保留原码位；
# U+FEFF 统一按零宽剔除、不改解码层；同批扩及 U+200C/200D/2060）。
# 编号内部插入＝裂号（成员失真）、前缀内/前缀后插入＝整成员漏、年号侧插入＝漏年；
# 只在 iter_citations 匹配层剔除 plain 并搬运 spans/breaks，load_text/visible_text 不动。
_ZERO_WIDTH = re.compile(r"[​‌‍⁠﻿]")


_P41_TAIL_YEAR = re.compile(r"(?:19|20)\d{2}")


def _p41_unit_db(plain: str, m, prefix: str) -> bool:
    """P41 支B（CG-20261123-002）＝OCR 表列里的单位分贝被读成地标前缀。

    判据四支同时成立才不收＝前缀为裸 `DB`＋前一非空字符是 `/` 且再前为汉字（`空气声计权隔声量/Db 41`
    这类「量名/单位」形）＋匹配体自身不带年号＋其后既无 `/T`·`/J` 也无 14 字内年号
    （真地标的三种写法 `DB 41/T 123-2020`／`DB 63/001-2019`／`按/DB 41-2020` 一律放行）。
    射程止于「汉字量名 + `/` + 无年号裸 DB」＝不主张拦下全部斜杠写法（`见附录/DB 41` 同形者一并
    不收，语料实测该形 0 处真成员；改前改后逐件差分见 CG-20261123-002 执行记录）。

    同批的**支A（`pl200_gb55025_render` 目录名内嵌）判不在形状层收口**＝按左 `_`＋右词字符的同形
    守卫在 994 件语料上连带打掉 `_GB50121官方承载面…`、`…_JGJT175-2018_第1-8章…` 等 26 处真引用面
    （一手读数＝`〈过程目录〉/2026-11-23/r53_p41_g7.out`，另正则会回退到更短数字、造出
    `GB 5502` 这类更坏的假成员，见 `r53_p41_diff2.out`）⇒ 判别子在语义层而非形状层，维持登记。
    """
    if prefix.strip().upper() != "DB":
        return False
    if m.group(4):
        return False
    if not re.search(r"[\u4e00-\u9fff]\s*/\s*$", plain[:m.start()]):
        return False
    tail = plain[m.end():m.end() + 14]
    return not (_P41_TAIL_YEAR.search(tail) or re.match(r"\s*/\s*[TJ]\b", tail))


def iter_citations(text: str, normalized: bool = False):
    line_offsets = _source_line_offsets(text)
    for plain, spans, breaks, basis in _visible_blocks(text, normalized):
        # 零宽剔除先于前缀归一：G{ZW}B/T、GB{ZW}/T 复原后 _T_GLUE/STD_RE 方可消费；
        # 反向遍历避免位移失效，spans 逐字符同步删除，breaks 按删除宽度左移。
        for m in reversed(list(_ZERO_WIDTH.finditer(plain))):
            a, b = m.span()
            plain = plain[:a] + plain[b:]
            del spans[a:b]
            breaks = [p if p < a else p - (b - a) for p in breaks]
        # 前缀归一同步搬运跨度，避免 GBT / GB T 等写法改变后续位置。
        for match in reversed(list(_T_GLUE.finditer(plain))):
            replacement = f"{match.group(1)}/T"
            a, b = match.span()
            origin = (spans[a][0], spans[b - 1][1])
            plain = plain[:a] + replacement + plain[b:]
            spans[a:b] = [origin] * len(replacement)
            breaks = [p if p < a else p + len(replacement) - (b - a) for p in breaks]
        matches = [(m, False) for m in STD_RE.finditer(plain)] + [(m, True) for m in ATLAS_RE.finditer(plain)]
        for m, atlas in sorted(matches, key=lambda item: item[0].start()):
            a, b = spans[m.start()][0], spans[m.end() - 1][1]
            ln = bisect_right(line_offsets, a)
            end_ln = bisect_right(line_offsets, max(a, b - 1))
            start_line = line_offsets[ln - 1]
            end_line = line_offsets[end_ln] if end_ln < len(line_offsets) else len(text)
            excerpt = text[start_line:end_line].rstrip("\r\n")
            left = max((p + 1 for p in breaks if p < m.start()), default=0)
            right = min((p for p in breaks if p >= m.end()), default=len(plain))
            # 用途窗与成员窗同取 breaks 切段，另把边界后余文交 `citation_usage` 作回指环视
            # （第 47 轮 O3 处置：仅显式回指才接管，见该函数注释）；成员／年号／回指仍
            # 只看切段内的文本＝提取判据（可见面与键集）不改。
            usage = citation_usage(plain[left:right], m.start() - left, m.end() - left,
                                   plain[right:])
            position = {"end_line": end_ln, "position_basis": basis}
            if basis != "visible_text":
                position.update(source_start=a, source_end=b)
            prefix = ATLAS_PREFIX if atlas else norm_prefix(m.group(1), m.group(2))
            num = norm_num(m.group(1)) if atlas else norm_num(m.group(3))
            year = None if atlas else norm_year(m.group(4))
            if not atlas and _p41_unit_db(plain, m, prefix):
                continue
            yield prefix, num, year, ln, excerpt, usage, plain[left:right], position


def key_of(prefix: str, num: str) -> str:
    return f"{prefix} {num}"


def parse_index(text: str) -> dict:
    """索引主表 → {key: {"seq","name","year","status","line"}}；同名多行取首行（核验记录表不入面）。"""
    out: dict[str, dict] = {}
    for i, line in enumerate(text.splitlines(), 1):
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("*").strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        m = STD_RE.search(cells[1])
        if m:
            status = next((c for c in cells[2:] if c.startswith(STATUS_PREFIXES)), None)
            if status is None:
                continue
            k = key_of(norm_prefix(m.group(1), m.group(2)), norm_num(m.group(3)))
            out.setdefault(k, {
                "seq": cells[0], "name": cells[2], "year": norm_year(m.group(4)),
                "status": status, "line": i,
            })
            continue
        am = ATLAS_RE.search(cells[1])
        if am:
            status = next((c for c in cells[2:] if c.startswith(STATUS_PREFIXES)), None)
            if status is None:
                continue
            year = cells[3] if len(cells) > 3 and re.fullmatch(r"(?:19|20)\d{2}", cells[3]) else None
            k = key_of(ATLAS_PREFIX, norm_num(am.group(1)))
            out.setdefault(k, {
                "seq": cells[0], "name": cells[2], "year": year,
                "status": status, "line": i,
            })
    return out


def _docx_visible_text(xml: str, annotations: bool = True) -> str:
    """按 Word 默认打印版式消费：w:vanish 开启的 run 不计入可见面（位置留结构边界），
    w:webHidden 仅 Web 版式隐藏、仍计入；styles.xml 样式继承的 vanish 未消费＝在册延期。
    mc:Fallback 子树整体不计（Choice 优先、二者互斥显示）；文本框等嵌套 w:p 的文本只归
    最内层段，外层段不吸收内层 w:t——每份显示文本恰产一行一点。嵌套段继承祖先 run 的
    隐藏态（vanish run 内的 drawing 随 run 不显示），隐藏嵌套段位置在外层段留结构边界
    防跨隐藏框拼号；嵌套段自身仍产一行（全隐藏＝空行）。段标 w:pPr/w:rPr/w:vanish 开启时
    段落标记不显示，与同父元素的下一相邻段并排合并为一行（不跨文本框区域合并；val 缺省
    或∈{1,true,on} 隐藏，∈{0,false,off} 与 webHidden 段标可见不合并）。
    w:ruby 注音层（ECMA-376 CT_Ruby）：rubyBase 是行内基字、rt 是基字上方小字注音，
    文档序 rt 先于 base 是 schema 序非显示序。基字流跨 ruby 连续（不拆编号年号）；rt 独立
    收集为注音层——annotations=True（默认，显示面）把注音行接在本段基字行之后，
    annotations=False（提取基底，load_text 消费）只含基字流：注音不入提取与行级回指
    （发起人第 27 轮裁定读法 B 双流分离；与 HTML P9 跨入口口径差＝HTML rt 入提取、DOCX rt 不入）。
    隐藏态注音随 run 不计；空/纯空白注音不产注音行。残余：行级显示面无法表达「注音在基字
    上方」，注音行取阅读序接基字行后；ruby 感知全保真双流（位置映射）未做＝在册延期。
    w:moveFrom 修订移出侧（ECMA-376 CT_RunTrackChange）：Word 默认打印版式（最终态）不在
    源位显示，与 w:del/delText 同侧不计——子树整体排除、位置留一枚结构边界防两侧可见文本
    拼年（沿 vanish 隐藏 run 轨，段首/段尾/连续不留空行），子树内嵌套段随子树不计不另产行；
    w:moveTo 移入侧仍计入（与 w:ins 同侧）。段标 w:pPr/w:rPr/w:moveFrom＝段落标记被移出，
    与段标 vanish 同轨作同父相邻兄弟段合并（不跨文本框区域）。moveFromRangeStart/End 标记
    无文本不消费（发起人第 38 轮裁定读法A·开修＋留结构边界＋段标同批合并）。
    w:sym 可见符号字符与 w:ptab 绝对定位间隔（ECMA-376 CT_Sym/CT_PTab）：二者均不是编号
    —年号合法分隔（_SEP 十二码位），且 w:sym 的 F0xx 为 ANSI 码位而非横杠字形（F02D↔
    Wingdings 0x2D 在公开字符表中为正字符 pictograph）；位置各留一枚结构边界，编号与
    年号不邻接不得跨其拼年、键保留年号不附（发起人第 41 轮裁定 P27 读法A·两元素同轨）。
    编号内部插入（1198<w:sym/>1）因边界拆键＝裁定后果；残余：符号字形随字体而变、
    仓内无真实样本，字形语义不作外推。
    表格级不显示标记（P31，第 45 轮有阈值收口复审判「普通输入×造向＋出口假绿」，发起人
    停止阈值「普通×造向/假绿→修」已预授权）：w:trPr 的 hidden＝行隐藏（ISO/IEC 29500-1
    「entire table row is hidden and not displayed」）、del／moveFrom＝整行修订删除或移出，
    w:tcPr 的 cellDel＝整格修订删除、vMerge 非 restart＝纵向合并延续格；行/格不上屏而段内
    仍是普通 w:t，故按祖先标记整段按隐藏消费。位置各留一枚结构边界（段产空行）＝隐藏行/格
    两侧可见文本不得跨行拼出新编号，段标隐藏不跨不显示行合并。控制例不退化＝w:hidden 的
    w:val∈{0,false,off} 不隐、w:trPr 的 ins／cantSplit／tblHeader 与 w:tcPr 的 gridSpan／
    cellIns／vMerge restart 照常计入、run 级三轨与段标 w:pPr/w:rPr/w:del 不动。
    残余：CT_TrPr 无 w:vanish 元素（第四十四枚回件纠正，合成件读数只作观测不作判据）；
    延续格是否留正文无 Word 原生样本可证（依据＝元素语义＋发起人裁定）。"""
    from xml.etree import ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    mc = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise LedgerError("DOCX 主文档不接受 DTD 或实体声明")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise LedgerError(f"DOCX 主文档 XML 不可解析：{exc}") from exc

    def vanish_on(run):
        rpr = run.find(ns + "rPr")
        v = rpr.find(ns + "vanish") if rpr is not None else None
        if v is None:
            return False
        val = v.get(ns + "val", v.get("val"))
        return val is None or val.lower() in ("1", "true", "on")

    def mark_moved_on(ppr):
        rpr = ppr.find(ns + "rPr")
        return rpr is not None and rpr.find(ns + "moveFrom") is not None

    def flag_on(el):
        if el is None:
            return False
        val = el.get(ns + "val", el.get("val"))
        return val is None or val.lower() in ("1", "true", "on")

    def row_not_shown(tr):
        # P31：ISO/IEC 29500-1 的 w:trPr 行级标记＝hidden（行隐藏，规范原文「entire table row
        # is hidden and not displayed」）、del／moveFrom（整行修订删除／移出，最终打印版式
        # 不在源位显示）。w:val∈{0,false,off} 为关闭态不隐；w:ins／w:cantSplit／w:tblHeader
        # 不是隐藏标记。
        trpr = tr.find(ns + "trPr")
        if trpr is None:
            return False
        return (flag_on(trpr.find(ns + "hidden"))
                or trpr.find(ns + "del") is not None
                or trpr.find(ns + "moveFrom") is not None)

    def cell_not_shown(tc):
        # P31：w:tcPr 的 cellDel＝整格修订删除；vMerge 缺省或 val=continue＝纵向合并延续格
        # （Word 原生合并把正文留在 restart 格，延续格内容不上屏）。val=restart 与 cellIns／
        # cellMerge／gridSpan 均照常计入。延续格一支的依据＝元素语义＋发起人第 45 轮裁定，
        # 仓内无 Word 原生手工样本可证转换件是否留正文（弱触发，见执行记录 §7.52 验证边界）。
        tcpr = tc.find(ns + "tcPr")
        if tcpr is None:
            return False
        if tcpr.find(ns + "cellDel") is not None:
            return True
        merge = tcpr.find(ns + "vMerge")
        if merge is None:
            return False
        val = merge.get(ns + "val", merge.get("val"))
        return val is None or val.lower() != "restart"

    parents = {c: p for p in root.iter() for c in p}

    def under_fallback(node):
        cur = parents.get(node)
        while cur is not None:
            if cur.tag == mc + "Fallback":
                return True
            cur = parents.get(cur)
        return False

    def under_move_from(node):
        cur = parents.get(node)
        while cur is not None:
            if cur.tag == ns + "moveFrom":
                return True
            cur = parents.get(cur)
        return False

    def under_hidden_run(node):
        cur = parents.get(node)
        while cur is not None:
            if cur.tag == ns + "r" and vanish_on(cur):
                return True
            cur = parents.get(cur)
        return False

    def under_not_shown_cell(node):
        cur = parents.get(node)
        while cur is not None:
            if cur.tag == ns + "tc" and cell_not_shown(cur):
                return True
            cur = parents.get(cur)
        return False

    def table_not_shown(node):
        # 段落所在行或任一祖先格不显示时，段内普通 w:t 也不上屏（整表被删则每行各产空行）。
        cur = parents.get(node)
        while cur is not None:
            if cur.tag == ns + "tr" and row_not_shown(cur):
                return True
            cur = parents.get(cur)
        return under_not_shown_cell(node)

    paragraphs = []
    for para in root.iter(ns + "p"):
        if under_fallback(para) or under_move_from(para):
            continue
        # 表格级不显示＝整段按隐藏消费：段仍产一行（空行），使隐藏行/格两侧各留一枚结构
        # 边界，防两侧可见文本跨行拼出新编号（沿 P24 moveFrom／P7 vanish 轨）；段标隐藏
        # 合并随之失效（不跨不显示行合并）。
        off_table = table_not_shown(para)
        ppr = para.find(ns + "pPr")
        mark_hidden = (not off_table and ppr is not None
                       and (vanish_on(ppr) or mark_moved_on(ppr)))
        parts = []
        ann_parts = []

        def visit(node, hidden, parts):
            if node.tag == mc + "Fallback":
                return
            if node.tag == ns + "moveFrom":
                # 移出侧最终版式不显示：子树整体不计，位置留结构边界防跨移出内容拼年。
                parts.append(None)
                return
            if node.tag in (ns + "sym", ns + "ptab"):
                # w:sym 可见符号字符（F0xx 为 ANSI 码位，如 F02D↔Wingdings 0x2D 正字符
                # pictograph）与 w:ptab 绝对定位间隔均非编号—年号合法分隔，位置留结构
                # 边界防跨其拼年（发起人第 41 轮裁定 P27 读法A·两元素同轨）。
                parts.append(None)
                return
            if node.tag == ns + "rt":
                # 注音层：不拼入基字流，独立收集（隐藏态随 run；显示面才消费）。
                if annotations:
                    for child in node:
                        visit(child, hidden, ann_parts)
                return
            if node.tag == ns + "p" and node is not para:
                # 嵌套段（w:txbxContent 等）的文本归其自身一行，外层段不得重复吸收。
                # 祖先 run 隐藏时框随 run 不显示，位置留结构边界防跨隐藏框拼号。
                if hidden:
                    parts.append(None)
                return
            if node.tag == ns + "r":
                hidden = hidden or vanish_on(node)
            if node.tag in (ns + "t", ns + "tab", ns + "br", ns + "cr", ns + "noBreakHyphen"):
                if hidden:
                    parts.append(None)
                elif node.tag == ns + "t":
                    parts.append(node.text or "")
                elif node.tag == ns + "tab":
                    parts.append("\t")
                elif node.tag in (ns + "br", ns + "cr"):
                    parts.append("\n")
                else:
                    # 可见连字符（U+2011，同 HTML &#8209;）；丢弃会把编号与年号拼成错误编号。
                    parts.append("\u2011")
            for child in node:
                visit(child, hidden, parts)

        visit(para, off_table or under_hidden_run(para), parts)
        rendered = []
        boundary = False
        for part in parts:
            if part is None:
                # 隐藏 run 位置留一枚硬边界，防两侧可见 run 拼出显示中不存在的完整编号；
                # 段首/段尾/连续隐藏不留空行。
                boundary = bool(rendered)
                continue
            if part:
                if boundary:
                    rendered.append("\n")
                    boundary = False
                rendered.append(part)
        ann_text = ""
        if annotations:
            # 注音层只作显示：隐藏贡献（None）与空串不计，不留边界（无拼号风险）。
            ann_text = "".join(p for p in ann_parts if p is not None and p)
            if not ann_text.strip():
                ann_text = ""
        paragraphs.append((parents.get(para), "".join(rendered), mark_hidden, ann_text))
    out = []
    for i, (parent, text, mark_hidden, ann) in enumerate(paragraphs):
        out.append(text)
        if ann:
            out.append("\n" + ann)
        if i + 1 < len(paragraphs):
            # 段标隐藏＝与下一相邻兄弟段并排显示；不同父元素（跨文本框区域）不合并。
            if not (mark_hidden and paragraphs[i + 1][0] is parent):
                out.append("\n")
    return "".join(out)


def load_text(path: Path) -> str:
    """DOCX 只读主文档正文基字流（ruby 注音不入提取），不含页眉、页脚、脚注；
    HTML 不执行脚本或 CSS。"""
    suf = path.suffix.lower()
    if suf == ".docx":
        import zipfile
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        return _docx_visible_text(xml, annotations=False)
    if suf in (".html", ".htm"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        return _html_visible_text(raw)
    return path.read_bytes().decode("utf-8", errors="replace")


def collect(files: list[Path], *, package_root: Path | None = None) -> dict:
    """逐点用途与年号；HTML/DOCX 的位置基准为可见文本行，不是原文件物理行。"""
    rows: dict[str, dict] = {}
    for f in files:
        text = load_text(f)
        identity = _input_fingerprint(f, package_root=package_root)
        portable = {k: identity[k] for k in ("source_id", "relative_path") if k in identity}
        normalized = f.suffix.lower() in (".html", ".htm", ".docx")
        for prefix, num, year, ln, line, usage, vline, position in iter_citations(text, normalized):
            k = key_of(prefix, num)
            r = rows.setdefault(k, {"years": set(), "hits": [], "mention": False, "external": False})
            if year:
                r["years"].add(year)
            r["hits"].append({"file": str(f).replace("\\", "/"), "line": ln,
                              "text": line.strip()[:120], "mention": usage == "mention",
                              "usage": usage, "year": year, **position, **portable})
            if any(w in vline for w in EXTERNAL_WORDS):
                r["external"] = True
    for r in rows.values():
        r["mention"] = bool(r["hits"]) and all(h["mention"] for h in r["hits"])
    return rows


def _input_fingerprint(path: Path, *, package_root: Path | None = None) -> dict:
    import hashlib
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    out = {"path": str(path).replace("\\", "/"), "bytes": len(data), "sha256": sha}
    if package_root is not None:
        root = Path(package_root).expanduser().resolve(strict=True)
        resolved = path.expanduser().resolve(strict=True)
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise LedgerError(f"输入越出交付包根：{path}") from exc
        out.update(source_id=f"sha256:{sha}", relative_path=relative)
    return out


def build_ledger(files: list[Path], *, package_root: Path | None = None) -> dict:
    rows = collect(files, package_root=package_root)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": [_input_fingerprint(p, package_root=package_root) for p in files],
        "count": len(rows),
        "rows": [
            {
                "key": k,
                "cited_years": sorted(v["years"]),
                "count": len(v["hits"]),
                "mention": v["mention"],
                "external": v["external"],
                "hit_mentions": [h["mention"] for h in v["hits"]],
                "hit_years": [h["year"] for h in v["hits"]],
                "hit_usages": [h["usage"] for h in v["hits"]],
                "hits": v["hits"][:5],
            }
            for k, v in sorted(rows.items())
        ],
    }


def ledger_from_texts(texts: dict[str, str]) -> dict:
    """内存内版本（不落盘）：{文件名: 文本} → ledger。供测试与脚本复用。"""
    import hashlib
    rows: dict[str, dict] = {}
    inputs = []
    for name, text in texts.items():
        data = text.encode("utf-8")
        inputs.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        for prefix, num, year, ln, line, usage, vline, position in iter_citations(text):
            k = key_of(prefix, num)
            r = rows.setdefault(k, {"years": set(), "hits": [], "external": False})
            if year:
                r["years"].add(year)
            r["hits"].append({"file": name, "line": ln, "text": line.strip()[:120],
                              "mention": usage == "mention", "usage": usage, "year": year, **position})
            r["external"] |= any(w in vline for w in EXTERNAL_WORDS)
    return {
        "schema": SCHEMA,
        "inputs": inputs,
        "count": len(rows),
        "rows": [
            {"key": k, "cited_years": sorted(v["years"]), "count": len(v["hits"]),
             "mention": all(h["mention"] for h in v["hits"]),
             "external": v["external"], "hit_mentions": [h["mention"] for h in v["hits"]],
             "hit_years": [h["year"] for h in v["hits"]],
             "hit_usages": [h["usage"] for h in v["hits"]], "hits": v["hits"][:5]}
            for k, v in sorted(rows.items())
        ],
    }


def _num_index(index: dict) -> dict:
    out: dict[str, list[str]] = {}
    for k in index:
        prefix, num = k.rsplit(" ", 1)
        out.setdefault(num, []).append(prefix)
    return out


def verify(ledger: dict, index_text: str, sources: dict | None = None,
           tolerate_unindexed: bool = False) -> list[tuple[str, str]]:
    """→ [(level, message)]，level ∈ {FAIL, WARN, OK}。

    tolerate_unindexed=False（默认，评审类）：未收录＝红——评审报告的职责就是逐枚交代状态；
    True（技术答疑类）：未收录＝黄，仍逐枚列出，交人确认。
    """
    if ledger.get("schema") != SCHEMA:
        raise LedgerError(f"ledger schema 非法：{ledger.get('schema')!r}")
    rows = ledger.get("rows")
    if not isinstance(rows, list) or not rows:
        raise LedgerError("ledger.rows 为空（空跑不判绿）")

    index = parse_index(index_text)
    if not index:
        raise LedgerError("索引主表解析为空（fail-closed，不回落零命中即绿）")
    by_num = _num_index(index)
    if sources:
        for k in sources:
            prefix, num = k.rsplit(" ", 1)
            by_num.setdefault(num, [])
            if prefix not in by_num[num]:
                by_num[num].append(prefix)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    for row in rows:
        if not isinstance(row, dict) or not row.get("key"):
            raise LedgerError(f"ledger 行非法：{row!r}")
        key = row["key"]
        if key in seen:
            out.append(("FAIL", f"台账行重复：{key}"))
        seen.add(key)
        mention = bool(row.get("mention"))
        external = bool(row.get("external"))
        years = [str(y) for y in (row.get("cited_years") or [])]

        if key not in index:
            prefix, num = key.rsplit(" ", 1)
            near = [p for p in by_num.get(num, []) if p != prefix]
            if near:
                out.append(("FAIL", f"前缀/族不符：{key}（同号在其它前缀下存在：{'／'.join(sorted(near))} {num}）"))
                continue
            if external:
                out.append(("WARN", f"源文件外依据（须在正文声明理由）：{key}"))
            elif row.get("ack"):
                out.append(("WARN", f"未收录（已声明）：{key}"))
            elif tolerate_unindexed:
                out.append(("WARN", f"未收录于标准索引（须人工确认身份）：{key}"))
            else:
                out.append(("FAIL", f"未收录于标准索引且未声明：{key} —— 补「源文件外依据」或未收录声明"))
            continue

        ent = index[key]
        status = ent["status"]
        cur_year = ent["year"]
        # 豁免粒度落到引用点（审计 F01/T03）：hit_mentions 逐点对应 count；
        # 旧台账无点级明细时回落行级语义（兼容范围＝整行同判，不默认赋予有效性）。
        hm = row.get("hit_mentions")
        if isinstance(hm, list) and hm and all(isinstance(x, bool) for x in hm):
            if len(hm) != (row.get("count") or 0):
                raise LedgerError(f"hit_mentions 长度 {len(hm)} ≠ 引用点数 {row.get('count')}：{key}")
            point_mentions = hm
        else:
            point_mentions = [mention]
        usages = row.get("hit_usages")
        if usages is not None:
            if (not isinstance(usages, list) or len(usages) != len(point_mentions)
                    or any(u not in ("use", "mention", "unknown") for u in usages)
                    or [u == "mention" for u in usages] != point_mentions):
                raise LedgerError(f"hit_usages 与引用点语义不自洽：{key}")
            use_points = [u == "use" for u in usages]
            has_unknown = "unknown" in usages
        else:
            use_points = [not m for m in point_mentions]
            has_unknown = False
        all_mention = all(point_mentions)
        has_use = any(use_points)
        if has_unknown:
            out.append(("WARN", f"引用用途待确认，未认定为本次依据：{key}"))
        # 年号对撞只取非提及点的年号（独立复核 ERROR-3）：讨论旧版＋采用现行版不误红；
        # 无点级年号明细的旧台账回落行级全量（保守，不放宽）。
        hy = row.get("hit_years")
        if isinstance(hy, list) and len(hy) == len(point_mentions):
            # 点级年号与行级年号同在时须自洽（第二轮复核 V5/V6）：手改 hit_years
            # 把旧年号翻成现行属判词造假，fail-closed 拒判。
            if sorted({str(y) for y in hy if y}) != sorted(years):
                raise LedgerError(f"hit_years 与 cited_years 不自洽：{key}")
            use_years = sorted({str(y) for y, use in zip(hy, use_points) if y and use})
        else:
            use_years = years
        before = len([1 for lv, _ in out if lv == "FAIL"])
        dead = any(status.startswith(p) for p in DEAD_PREFIXES)
        if dead and has_use:
            out.append(("FAIL", f"引用已废止标准：{key}（索引 {ent['seq']} 行：{status}）"))
            continue
        if dead and all_mention:
            out.append(("WARN", f"已废止标准属提及而非依据（待人工确认用途）：{key}（{status}）"))
            continue
        if any(status.startswith(p) for p in PARTIAL_PREFIXES) and has_use:
            out.append(("WARN", f"引用被部分替代标准：{key}（{status}）—— 正文须声明适用面"))
        if use_years and cur_year and any(y != cur_year for y in use_years) and has_use:
            out.append(("FAIL", f"年号与索引现版不一致：{key} 引用 {use_years}／索引 {cur_year}（{status}）"))
            continue
        if not use_years and cur_year and has_use:
            out.append(("WARN", f"未注年号：{key}（索引现版 {cur_year}）"))
        if all_mention:
            out.append(("WARN", f"引用点均属提及（待人工确认用途）：{key}（{status}）"))
            continue
        if has_use and len([1 for lv, _ in out if lv == "FAIL"]) == before:
            out.append(("OK", f"{key} → {status}（{cur_year or '—'}）"))

    if sources is not None:
        src_keys = set(sources)
        del_keys = {r["key"] for r in rows}
        for k in sorted(src_keys - del_keys):
            out.append(("FAIL", f"漏引：源文件有 {k}，交付物未覆盖"))
        for k in sorted(del_keys - src_keys):
            row = next(r for r in rows if r["key"] == k)
            if row.get("external"):
                out.append(("WARN", f"源文件外依据（须在正文声明理由）：{k}"))
                continue
            prefix, num = k.rsplit(" ", 1)
            near = [p for p in by_num.get(num, []) if p != prefix]
            if near:
                out.append(("WARN", f"近似条目：{k}（源文件侧为 {'／'.join(sorted(near))} {num}，OCR/写法差异须人工确认）"))
            else:
                out.append(("FAIL", f"归因错误：交付物有 {k}，源文件未见（须标「源文件外依据」或删除）"))
    return out


def rebind_ledger(ledger: dict, root: Path) -> dict:
    import hashlib

    if ledger.get("schema") != SCHEMA:
        raise LedgerError(f"ledger schema 非法：{ledger.get('schema')!r}")
    inputs = ledger.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise LedgerError("可移植台账 inputs 为空或缺失")
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise LedgerError(f"重绑定根不是目录：{root}")
    rebound = copy.deepcopy(ledger)
    path_map, identity_map, seen, old_paths = {}, {}, set(), set()
    for item in rebound["inputs"]:
        if not isinstance(item, dict):
            raise LedgerError("可移植台账 inputs 含非法条目")
        rel = item.get("relative_path")
        sha = item.get("sha256")
        size = item.get("bytes")
        if (not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel
                or PurePosixPath(rel).is_absolute() or "/".join(PurePosixPath(rel).parts) != rel
                or any(p in {".", ".."} for p in PurePosixPath(rel).parts)):
            raise LedgerError(f"非法包内相对路径：{rel!r}")
        if rel in seen:
            raise LedgerError(f"包内相对路径重复：{rel}")
        seen.add(rel)
        if (not isinstance(sha, str) or len(sha) != 64
                or not isinstance(size, int) or isinstance(size, bool) or size < 0
                or item.get("source_id") != f"sha256:{sha}"):
            raise LedgerError(f"内容身份字段无效：{rel}")
        candidate = root.joinpath(*PurePosixPath(rel).parts).resolve(strict=True)
        if not candidate.is_relative_to(root):
            raise LedgerError(f"重绑定路径越界或链接逃逸：{rel}")
        if not candidate.is_file():
            raise LedgerError(f"重绑定输入不是普通文件：{rel}")
        data = candidate.read_bytes()
        if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
            raise LedgerError(f"重绑定内容身份不符（同名不同内容或缺件）：{rel}")
        old = item.get("path")
        if not isinstance(old, str) or not old:
            raise LedgerError(f"可移植台账缺物理路径：{rel}")
        if old in old_paths:
            raise LedgerError(f"可移植台账物理路径重复：{old}")
        old_paths.add(old)
        new = str(candidate).replace("\\", "/")
        path_map[old] = new
        identity_map[old] = {"source_id": item["source_id"], "relative_path": rel}
        item["path"] = new
    for row in rebound.get("rows") or []:
        if not isinstance(row, dict):
            continue
        for hit in row.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            old = hit.get("file")
            if old not in path_map:
                raise LedgerError(f"引用点未绑定已验证输入：{old!r}")
            identity = identity_map[old]
            for field, value in identity.items():
                if field in hit and hit[field] != value:
                    raise LedgerError(f"引用点身份与输入不一致：{old} 的 {field}")
            hit["file"] = path_map[old]
            hit.update(identity)
    rebound["rebound_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return rebound


def _cmd_extract(args) -> int:
    files = [Path(p) for p in args.files]
    if not files:
        print("extract：至少一个输入文件", file=sys.stderr)
        return 2
    missing = [str(p) for p in files if not p.exists()]
    if missing:
        print(f"extract：输入不存在 {missing}", file=sys.stderr)
        return 2
    package_root = Path(args.package_root) if args.package_root else None
    led = build_ledger(files, package_root=package_root)
    out = Path(args.out) if args.out else ROOT / L.LEDGER_DEFAULT_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(led, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"台账已产出：{out} ｜ 引用 {led['count']} 枚 ｜ 输入 {len(files)} 件")
    return 0


def _cmd_rebind(args) -> int:
    led_path = Path(args.ledger)
    if not led_path.is_file():
        print(f"rebind：台账不存在：{led_path}", file=sys.stderr)
        return 2
    try:
        led = json.loads(led_path.read_text(encoding="utf-8"))
        rebound = rebind_ledger(led, Path(args.root))
    except (LedgerError, OSError, ValueError, TypeError) as exc:
        print(f"rebind FAIL（未写入）：{exc}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else led_path.with_name(led_path.stem + ".rebound.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(rebound, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"台账已重绑定：{out} ｜ 输入 {len(rebound['inputs'])} 件 ｜ 内容身份逐件通过")
    return 0


def _cmd_verify(args) -> int:
    led_path = Path(args.ledger)
    idx_path = Path(args.index) if args.index else DEFAULT_INDEX
    if not led_path.exists() or not idx_path.exists():
        print(f"verify：输入缺失（ledger={led_path}，index={idx_path}）", file=sys.stderr)
        return 2
    try:
        led = json.loads(led_path.read_text(encoding="utf-8"))
        results = verify(led, idx_path.read_text(encoding="utf-8"),
                         collect([Path(p) for p in args.sources]) if args.sources else None,
                         tolerate_unindexed=bool(args.tolerate_unindexed))
    except LedgerError as e:
        print(f"verify FAIL（无法判定）：{e}", file=sys.stderr)
        return 2
    fails = [m for lv, m in results if lv == "FAIL"]
    warns = [m for lv, m in results if lv == "WARN"]
    for lv, m in results:
        if lv != "OK":
            print(f"  [{lv}] {m}")
    print(f"台账核验：{'FAIL' if fails else 'PASS'} ｜ 红 {len(fails)} ｜ 黄 {len(warns)} ｜ "
          f"通过 {sum(1 for lv, _ in results if lv == 'OK')} ｜ 行 {led['count']}")
    return 1 if fails else 0


def _selftest() -> int:
    idx = """## 二、主表
| 序号 | 标准编号 | 标准名称 | 实施日期 | 状态 |
|---|---|---|---|---|
| 1 | GB 50222-2017 | 建筑内部装修设计防火规范 | 2018-04-01 | 现行有效 |
| 2 | GB/T 11981-2024 | 建筑用轻钢龙骨 | 2025-05-01 | 现行有效 |
| 3 | GB/T 19889.14-2010 | 声学 建筑构件隔声 | 2011-04-01 | 已废止 |
## 七、其他
"""
    def led(*texts):
        return ledger_from_texts({"case.md": "\n".join(texts)})

    def build_ledger_text(text):
        return ledger_from_texts({"src.txt": text})

    cases = [
        ("控制例（正确版本）", led("GB 50222-2017 第 4.0.4 条"), None, False),
        ("编造编号", led("GB/T 99999-2099 第 4.1.2 条"), None, True),
        ("旧版年号", led("GB/T 11981-2008 第 3 条"), None, True),
        ("前缀错（GB↔GB/T）", led("GB/T 50222-2017 第 4.0.4 条"), None, True),
        ("已废止", led("GB/T 19889.14-2010 第 3 条"), None, True),
        ("已废止但属提及", led("GB/T 19889.14-2010 已被替代，不得作依据"), None, False),
        ("漏引（源有产物无）", led("GB 50222-2017"), ["GB 50222-2017", "GB/T 11981-2024"], True),
        ("归因错误（产物有源无）", led("GB 50222-2017\nGB 55002-2021"), ["GB 50222-2017"], True),
        ("源文件外已声明", led("GB 50222-2017\n评审依据（源文件外）：GB 55002-2021"), ["GB 50222-2017"], False),
    ]
    bad = 0
    for name, ledger, src, want_fail in cases:
        src_map = ({r["key"]: {} for r in build_ledger_text("\n".join(src))["rows"]}
                   if src is not None else None)
        results = verify(ledger, idx, src_map)
        got = any(lv == "FAIL" for lv, _ in results)
        if got != want_fail:
            bad += 1
            print(f"  SELFTEST FAIL：{name} 期望 {'红' if want_fail else '绿'}，实得 {'红' if got else '绿'}")
            for lv, m in results:
                print(f"    [{lv}] {m}")
        else:
            print(f"  OK {name} → {'红' if got else '绿'}")
    print(f"selftest：{len(cases) - bad}/{len(cases)}")
    return 0 if bad == 0 else 1


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="标准引用台账：抽取／核验")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--tolerate-unindexed", action="store_true",
                    help="未收录降为黄（技术答疑类）；默认红（评审类）")
    sub = ap.add_subparsers(dest="cmd")
    e = sub.add_parser("extract", help="从文本抽取引用并产出台账")
    e.add_argument("files", nargs="+")
    e.add_argument("--out")
    e.add_argument("--package-root", help="生成可搬迁台账：记录包内相对路径与内容身份")
    b = sub.add_parser("rebind", help="按内容身份把可搬迁台账显式重绑到新包根")
    b.add_argument("ledger")
    b.add_argument("--root", required=True)
    b.add_argument("--out")
    v = sub.add_parser("verify", help="对台账施加 L1—L4 判据")
    v.add_argument("ledger")
    v.add_argument("--index")
    v.add_argument("--sources", nargs="+")
    v.add_argument("--tolerate-unindexed", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    try:
        if args.cmd == "extract":
            return _cmd_extract(args)
        if args.cmd == "rebind":
            return _cmd_rebind(args)
        if args.cmd == "verify":
            return _cmd_verify(args)
    except LedgerError as exc:
        print(f"引用检查无法完成：{exc}", file=sys.stderr)
        return 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
