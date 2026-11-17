#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付件渲染器 v2.2：prefab-deliverable/v3 → 形态族交付件（HTML 单文件／Word 件，内容不增不减）。

用法（cwd＝本工具所在目录）：
  python -B deliverable_render.py --check    <case.json>
  python -B deliverable_render.py --render   <case.json> <out.html|out.docx> [--template X]
  python -B deliverable_render.py --selftest <case.json>
  python -B deliverable_render.py --verify   <case.json> <out.html|out.docx>

输出形态由 OUT 后缀决定（.docx＝Word 件，余＝单文件 HTML）；三形态两种输出面都出，
docx 面用 stdlib zipfile 直写 OOXML（不引第三方依赖）。

三种呈现形态，共用一份数据与同一契约：
  conclusion_sheet  工作面——条件可改，结论随条件重算（默认）
  reply_letter      对外件——条件锁定快照，无内部编号，带签位
  brief_card        决策面——只给当前结论、关键缺件、下一步

真实感三件落在数据里而非排版里：签位（编制/复核/接收）、版本与修订、可推翻条件。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import io
import json
import re
import sys
import zipfile
from pathlib import Path

SCHEMA = "prefab-deliverable/v3"
TEMPLATES = ("conclusion_sheet", "reply_letter", "brief_card")
GRADES = ("A", "B", "C", "D", "N")
GRADE_LABEL = {
    "A": "规范明文",
    "B": "可溯源报告",
    "C": "通用做法/设计取值",
    "D": "待核",
    "N": "不适用",
}
READ_STATUS = ("已读", "部分读取", "未读", "识别失败")
NUMERALS = "一二三四五六七八九十"           # 章节序号（HTML 面与 docx 面共用）
D_FORBIDDEN = ("规范要求", "规范规定", "标准要求", "应符合")
LEDGER_SCHEMA = "citation-ledger/v1"
STANDARDS_MODES = ("review", "qa")
CARD_STATES = ("valid", "stale", "missing", "declared")
GATE_VERDICTS = ("PASS", "FAIL")
CARD_COUNT_KEYS = ("indexed", "cached", "stale", "missing", "declared",
                   "indexed_cached", "indexed_stale", "indexed_missing")
# 发起人 2026-11-12 裁定：对外文件评审＝日常业务辅助性支持，仅作业务参考、不作为正式文件。
REVIEW_QUALIFICATION = ("本件属日常业务辅助性支持，仅作业务参考，为非正式文件；"
                        "不得用作主管部门审批、发布或验收依据。")
# 自称正式评审的字面（否定句里不出现这些形，故可与上面的定性声明并存）
FORMAL_CLAIMS = ("供编制组与主管部门使用", "供主管部门使用", "正式发布前专家评审", "发布前专家评审")
# 判定口径属本件按场景该定的事，问回去等于把决策成本转给用户（PW 技能作答协议 L0：不得抛回选档）
# 「口径/评价量」作为被问对象即判转嫁；修正量/Rw 等只在与选择问句结合时才判（索取载 C 值的报告是数据请求，不拦）
JUDGMENT_TERMS = ("口径", "评价量")
# 对外件不出现档位码与技能路由码（PW 作答协议 ④ 对外话术层）
EXT_CODE_RE = re.compile(
    r"S[123]\s*：|（\s*(?:AC|ACE|BK|CL|FL|GS|ME|MI|OR|PW|QA|SR|SSCV|ST|WS)\s*）"
    r"|[A-Z]{2,3}-R-[A-Z0-9]+-\d+"
)


def _ext(text) -> str:
    t = _s(text)
    t = t.replace("PL-229 定量暂停期", "隔声定量暂停期").replace("PL-229 暂停期", "隔声定量暂停期")
    t = re.sub(r"(?<![A-Za-z0-9])(?:PL|IC|CG)-\d+(?:-\d+)*\s*", "", t)
    t = EXT_CODE_RE.sub("", t)
    return re.sub(r"[ \t]{2,}", " ", t)

CSS_SHEET = """
:root{--ink:#1a1a1a;--muted:#5b6470;--line:#d8dde3;--soft:#f6f8fa;--accent:#1f4e79;--warn:#b3261e;--warnbg:#fdf1ef}
*{box-sizing:border-box}
body{margin:0;background:#eef1f4;color:var(--ink);font:14px/1.75 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}
.page{max-width:920px;margin:0 auto;background:#fff;padding:40px 52px 32px}
.kicker{font-size:12px;letter-spacing:2px;color:var(--muted)}
h1{font-size:22px;line-height:1.45;margin:6px 0 16px;font-weight:600}
h2{font-size:15px;margin:26px 0 10px;padding-left:9px;border-left:4px solid var(--accent);font-weight:600}
h3{font-size:13.5px;margin:18px 0 6px;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0}
th,td{border:1px solid var(--line);padding:8px 10px;vertical-align:top;text-align:left}
th{background:var(--soft);font-weight:600;white-space:nowrap}
.meta th{width:84px}
.meta td{border-left:0;border-right:0}
.meta tr:last-child td{border-bottom:1px solid var(--line)}
.strip{margin:12px 0 0;padding:9px 12px;background:var(--soft);border-left:3px solid var(--accent);font-size:12.5px;color:var(--muted)}
.strip b{color:var(--ink)}
.controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;background:var(--soft);border:1px solid var(--line);border-radius:6px;padding:14px 16px}
.field{display:flex;flex-direction:column;gap:5px;min-width:0}
.field label{font-size:12px;color:var(--muted)}
.field select{height:38px;padding:4px 8px;border:1px solid #9aa4b0;border-radius:5px;background:#fff;font:inherit;color:var(--ink);min-width:0;width:100%}
.hl{display:grid;grid-template-columns:156px 1fr 124px;gap:12px;align-items:start;padding:11px 0;border-bottom:1px dashed var(--line);font-size:13.5px}
.hl:last-child{border-bottom:0}
.hl-item{color:var(--muted);font-size:12.5px}
.hl-g{font-size:12px;color:var(--muted);text-align:right}
.hl-g .badge{margin-right:5px}
.badge{display:inline-block;min-width:22px;text-align:center;padding:1px 7px;border-radius:3px;font-size:12px;font-weight:600;color:#fff}
.g-A{background:#2e7d32}.g-B{background:#1565c0}.g-C{background:#6d4c41}.g-D{background:#b3261e}.g-N{background:#6b7280}
.cid{white-space:nowrap;color:var(--muted);font-size:12px}
.cond{margin-top:5px;font-size:12px;color:var(--muted)}
.note{font-size:12px;color:var(--muted);margin-top:3px}
.warn{margin-top:10px;padding:10px 14px;border:1px solid #e6b3ad;background:var(--warnbg);color:var(--warn);font-size:13px;border-radius:5px}
.warn strong{display:block;margin-bottom:2px}
.legend{font-size:11.5px;color:var(--muted);margin:4px 0 0}
.gapbox{width:16px;height:16px;margin:0;vertical-align:-3px}
details{margin-top:22px;border-top:1px solid var(--line);padding-top:10px}
summary{cursor:pointer;font-size:13px;font-weight:600;padding:6px 0}
details ul{margin:6px 0;padding-left:20px;font-size:12.5px;color:var(--muted)}
footer{margin-top:26px;padding-top:14px;border-top:1px solid var(--line);font-size:11.5px;color:var(--muted)}
.gen{margin-top:8px;font-family:ui-monospace,Consolas,monospace;font-size:11px;word-break:break-all}
.actions{margin-top:18px;display:flex;gap:10px;flex-wrap:wrap}
.actions button{min-height:40px;padding:8px 16px;border:1px solid #9aa4b0;background:#fff;border-radius:5px;font:inherit;font-size:13px;cursor:pointer}
.actions button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
#status{font-size:12.5px;color:var(--muted);align-self:center}
@media(max-width:760px){
 .page{padding:24px 18px}
 .controls{grid-template-columns:1fr}
 .hl{grid-template-columns:1fr;gap:2px}
 .hl-g{text-align:left}
 table{font-size:12px}
 th,td{padding:6px 7px}
}
@media print{
 @page{size:A4;margin:15mm 13mm}
 body{background:#fff;font-size:10pt}
 .page{padding:0;max-width:none}
 .actions{display:none}
 .controls{background:#fff;border-style:dashed}
 .controls select{border:0;border-bottom:1px solid #999;background:#fff;appearance:none;height:auto;padding:0}
 tr{break-inside:avoid}
 details{border-top:0}
 details[open] summary{margin-bottom:4px}
}
"""

CSS_LETTER = """
:root{--ink:#111;--muted:#555;--line:#cfd6dd}
*{box-sizing:border-box}
body{margin:0;background:#eef1f4;color:var(--ink);font:14px/1.9 "Songti SC","SimSun","Microsoft YaHei",serif}
.page{max-width:800px;margin:0 auto;background:#fff;padding:52px 64px 40px}
h1{font-size:20px;text-align:center;margin:0 0 6px;font-weight:600;letter-spacing:1px}
.sub{text-align:center;font-size:12px;color:var(--muted);margin-bottom:26px}
.to{margin:0 0 18px;font-size:14px}
p{margin:0 0 12px;text-align:justify}
h2{font-size:14px;margin:24px 0 8px;font-weight:600}
.item{margin:0 0 16px;padding-left:12px;border-left:3px solid #b9c3cc}
.item .k{font-weight:600}
.src{font-size:12px;color:var(--muted);margin-top:4px}
.orig{color:var(--muted)}
.note{font-size:12px;color:var(--muted);margin:0 0 10px}
ul{margin:6px 0 12px;padding-left:22px}
li{margin:4px 0}
.sign{width:100%;border-collapse:collapse;margin-top:34px;font-size:13px}
.sign th,.sign td{border:1px solid var(--line);padding:10px 12px;text-align:left}
.sign th{width:70px;background:#f7f9fa;font-weight:600;white-space:nowrap}
.blank{display:inline-block;min-width:120px;border-bottom:1px solid #999;height:1.4em}
footer{margin-top:30px;padding-top:12px;border-top:1px solid var(--line);font-size:11px;color:var(--muted)}
.gen{margin-top:6px;font-family:ui-monospace,Consolas,monospace;word-break:break-all}
@media print{@page{size:A4;margin:18mm 16mm}body{background:#fff;font-size:10.5pt}.page{padding:0;max-width:none}}
"""

CSS_BRIEF = """
:root{--ink:#111;--muted:#5b6470;--line:#d8dde3;--accent:#1f4e79}
*{box-sizing:border-box}
body{margin:0;background:#eef1f4;color:var(--ink);font:14px/1.7 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}
.page{max-width:760px;margin:0 auto;background:#fff;padding:38px 44px 30px}
.kicker{font-size:12px;letter-spacing:2px;color:var(--muted)}
h1{font-size:20px;margin:6px 0 18px;font-weight:600}
.concl{border-top:3px solid var(--accent);padding-top:14px;margin-bottom:22px}
.crow{display:grid;grid-template-columns:150px 1fr;gap:12px;padding:10px 0;border-bottom:1px dashed var(--line)}
.crow:last-child{border-bottom:0}
.ck{font-size:12.5px;color:var(--muted)}
.cv{font-size:15px}
h2{font-size:13px;margin:20px 0 8px;color:var(--muted);letter-spacing:1px;font-weight:600}
ul{margin:4px 0;padding-left:20px}
li{margin:5px 0}
.no{background:#f7f9fa;border-left:3px solid #b9c3cc;padding:8px 12px;font-size:12.5px;color:var(--muted);margin-top:18px}
footer{margin-top:22px;padding-top:12px;border-top:1px solid var(--line);font-size:11px;color:var(--muted)}
.gen{font-family:ui-monospace,Consolas,monospace;word-break:break-all}
@media print{@page{size:A4;margin:18mm 16mm}body{background:#fff;font-size:10.5pt}.page{padding:0;max-width:none}}
"""

JS = r"""
(function(){
  var D = JSON.parse(document.getElementById('data').textContent);
  var state = {};
  D.conditions.forEach(function(c){ state[c.id] = c['default']; });

  var savedEl = document.getElementById('saved-state');
  var saved = null;
  try { if (savedEl && savedEl.textContent.trim()) saved = JSON.parse(savedEl.textContent); } catch(e){}
  if (saved && saved.conditions) { Object.keys(saved.conditions).forEach(function(k){ if (k in state) state[k]=saved.conditions[k]; }); }

  function matches(c){
    if (!c.when) return true;
    return Object.keys(c.when).every(function(k){ return (c.when[k]||[]).indexOf(state[k]) >= 0; });
  }
  function el(tag, cls, txt){ var e=document.createElement(tag); if(cls) e.className=cls; if(txt!=null) e.textContent=txt; return e; }
  function renderHeadline(){
    var seen={}, groups=[];
    D.conclusions.forEach(function(c){ if(c.group && !seen[c.group]){ seen[c.group]=1; groups.push(c.group); } });
    var box=document.getElementById('headline'); box.innerHTML='';
    var any=false;
    groups.forEach(function(g){
      var c=D.conclusions.filter(function(x){ return x.group===g && matches(x); })[0];
      if(!c) return; any=true;
      var row=el('div','hl');
      row.appendChild(el('div','hl-item',c.item));
      row.appendChild(el('div','hl-text',c.conclusion));
      var gcell=el('div','hl-g');
      gcell.appendChild(el('span','badge g-'+c.grade,c.grade));
      gcell.appendChild(document.createTextNode(c.grade_label||''));
      row.appendChild(gcell);
      box.appendChild(row);
    });
    if(!any) box.appendChild(el('p','note','当前条件组合下没有可判结论——请补充条件或转人工复核。'));
  }
  function renderTable(){
    var tb=document.getElementById('ctbody'); tb.innerHTML='';
    var list=D.conclusions.filter(matches);
    list.forEach(function(c){
      var tr=document.createElement('tr');
      tr.appendChild(el('td','cid',c.id));
      tr.appendChild(el('td',null,c.item));
      var d3=el('td',null,c.conclusion);
      d3.appendChild(el('div','cond','适用条件：'+c.conditions));
      if(c.note) d3.appendChild(el('div','note',c.note));
      tr.appendChild(d3);
      var d4=el('td');
      d4.appendChild(el('span','badge g-'+c.grade,c.grade));
      d4.appendChild(el('div','note',c.grade_label||''));
      tr.appendChild(d4);
      var d5=el('td',null,c.source.doc);
      d5.appendChild(el('div','note',c.source.clause));
      tr.appendChild(d5);
      tb.appendChild(tr);
    });
    document.getElementById('ccount').textContent = String(list.length);
  }
  function renderWarnings(){
    var box=document.getElementById('warnings'); box.innerHTML='';
    D.warnings.forEach(function(w){
      if(!matches(w)) return;
      var d=el('div','warn');
      d.appendChild(el('strong',null,'条件冲突'));
      d.appendChild(document.createTextNode(w.text));
      box.appendChild(d);
    });
  }
  function renderStatus(){
    var boxes=document.querySelectorAll('[data-gap]');
    var done=0; boxes.forEach(function(b){ if(b.checked) done++; });
    document.getElementById('status').textContent = '状态：草稿 ｜ 缺件已补 ' + done + ' / ' + boxes.length + (done===boxes.length ? '（可转专业审核）' : '');
  }
  function renderAll(){ renderHeadline(); renderTable(); renderWarnings(); renderStatus(); }

  D.conditions.forEach(function(c){
    var sel=document.getElementById('cond-'+c.id);
    if(!sel) return;
    if(saved && saved.conditions && (c.id in saved.conditions)) sel.value=state[c.id];
    sel.addEventListener('change', function(){ state[c.id]=sel.value; renderAll(); });
  });
  document.querySelectorAll('[data-gap]').forEach(function(b){ b.addEventListener('change', renderStatus); });

  var ex=document.getElementById('exportBtn');
  if(ex) ex.addEventListener('click', function(){
    var gaps={}; document.querySelectorAll('[data-gap]').forEach(function(b){ gaps[b.getAttribute('data-gap')]=b.checked; });
    var st=document.getElementById('saved-state');
    if(st) st.textContent=JSON.stringify({conditions:state, gaps:gaps});
    var doc='<!DOCTYPE html>\n'+document.documentElement.outerHTML;
    var blob=new Blob([doc],{type:'text/html;charset=utf-8'});
    var a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=document.title+'_已填.html';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function(){ URL.revokeObjectURL(a.href); }, 1500);
  });
  var pr=document.getElementById('printBtn');
  if(pr) pr.addEventListener('click', function(){ window.print(); });

  renderAll();
})();
"""


def load_case(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _s(value) -> str:
    return value if isinstance(value, str) else ""


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_standards(st) -> list[str]:
    """引用台账随件块（`standards`）的契约：每个计数都必须能由件内数据复算，不接受手写字面。

    动因＝2026-11-10 一次真实文件评审的附录把编号数与引用点数写死在生成脚本里，与被评审文件
    机算值计数偶合而成员不同（漏登一枚团标）。故本块判据取**成员级**与**复算级**两层：
    键集逐枚对撞、计数由 results／rows／states 现算，任一处手写即红。

    射程边界（据实声明，独立复核第一轮 B1 后收窄、第二轮 N12/N13 再收窄）：本函数覆盖**聚合计数**
    （red/yellow/green/unverified/unindexed_undeclared/ledger.count/cards 八档）与**成员集**
    （rows 键集、states 键集与 indexed 逐行对随件 index_keys、lines 键集由 states 派生）。
    **行级 `rows[].count`、`hits[].line`、`cited_years` 与判词字面不在本函数射程**：
    评审档由 `answer_to_case._ledger_closure_errors` 与被评审文件实抽逐枚对撞承担
    （该函数另对撞 `inputs[].bytes`／`sha256` 与 ack 标记），答疑档由台账抽取器在构建时承担；
    不得声称「每个字段手写即红」。
    **`index_keys` 是自声明面而非锚定面**：本函数为纯函数、不读索引文件，故只校它与 `states[].indexed`
    互校自洽（并校去重升序形制）；把 index_keys 整块替换、逐行 indexed 与聚合同步改写的**协同篡改**
    不在本函数射程。真实索引锚定由产出端承担（`audit_standards` 从 `INDEX_PATH` 现算），
    交付后重锚须另设入口，本批不新增常驻机制。
    """
    errs: list[str] = []
    if not isinstance(st, dict):
        return ["standards 必须是对象"]

    led = st.get("ledger")
    rows: list = []
    if not isinstance(led, dict):
        errs.append("standards.ledger 缺失或不是对象（台账须随件，不得只留指针）")
    else:
        if led.get("schema") != LEDGER_SCHEMA:
            errs.append(f"standards.ledger.schema 必须为 {LEDGER_SCHEMA}")
        raw_rows = led.get("rows")
        if not isinstance(raw_rows, list) or not raw_rows:
            errs.append("standards.ledger.rows 为空：零引用是合法态，但不得冒充已核验")
        else:
            rows = raw_rows
        if not _is_int(led.get("count")) or led.get("count") != len(rows):
            errs.append(f"standards.ledger.count {led.get('count')!r} ≠ rows 实数 {len(rows)}")

    keys: list[str] = []
    years: dict[str, list] = {}
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or not _s(r.get("key")).strip():
            errs.append(f"standards.ledger.rows[{i}].key 不得为空")
            continue
        keys.append(r["key"])
        years[r["key"]] = r.get("cited_years") or []
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        errs.append(f"standards.ledger.rows 键重复：{'、'.join(dup)}")

    ver = st.get("verification")
    if not isinstance(ver, dict):
        errs.append("standards.verification 缺失或不是对象（出口闸回执须随件，不只在 stdout）")
        ver = {}
    mode = ver.get("mode")
    if mode not in STANDARDS_MODES:
        errs.append(f"standards.verification.mode 必须是 {STANDARDS_MODES}")
    ikeys = ver.get("index_keys")
    if not isinstance(ikeys, list) or not ikeys or not all(_s(x).strip() for x in ikeys):
        errs.append("standards.verification.index_keys 必须是非空字符串数组（索引成员集随件，供逐行对撞）")
        ikeys = []
    elif ikeys != sorted(set(ikeys)):
        errs.append("standards.verification.index_keys 须为去重后升序（形制由 audit_standards 单源产出）")
    iset = set(ikeys)
    results = ver.get("results")
    if not isinstance(results, list) or not results:
        errs.append("standards.verification.results 为空：无判定明细不得报计数")
        results = []
    tally = {"FAIL": 0, "WARN": 0, "OK": 0}
    for i, it in enumerate(results):
        if (not isinstance(it, (list, tuple)) or len(it) != 2
                or it[0] not in tally or not _s(it[1]).strip()):
            errs.append(f"standards.verification.results[{i}] 须为 [级别, 判词]，级别 ∈ FAIL/WARN/OK")
            continue
        tally[it[0]] += 1
    for name, lv in (("red", "FAIL"), ("yellow", "WARN"), ("green", "OK")):
        got = ver.get(name)
        if not _is_int(got):
            errs.append(f"standards.verification.{name} 必须是整数")
        elif got != tally[lv]:
            errs.append(f"standards.verification.{name} {got} ≠ results 复算 {tally[lv]}（计数不得手写）")

    cards = st.get("cards")
    if not isinstance(cards, dict):
        errs.append("standards.cards 缺失或不是对象（卡消费读数须随件）")
        cards = {}
    for k in CARD_COUNT_KEYS:
        if not _is_int(cards.get(k)) or cards.get(k) < 0:
            errs.append(f"standards.cards.{k} 必须是非负整数")
    states = cards.get("states")
    if not isinstance(states, list):
        errs.append("standards.cards.states 必须是数组（逐行卡状态，索引内行也须报）")
        states = []
    indexed_of: dict[str, bool] = {}
    state_of: dict[str, str] = {}
    for i, s in enumerate(states):
        if not isinstance(s, dict) or not _s(s.get("key")).strip():
            errs.append(f"standards.cards.states[{i}].key 不得为空")
            continue
        if not isinstance(s.get("indexed"), bool):
            errs.append(f"standards.cards.states[{i}].indexed 必须是布尔值")
        if s.get("state") not in CARD_STATES:
            errs.append(f"standards.cards.states[{i}].state 必须是 {CARD_STATES}")
            continue
        if s["state"] == "declared" and s.get("indexed"):
            errs.append(f"standards.cards.states[{i}] 索引内行不得记为已声明外源：{s['key']}")
        if iset and bool(s.get("indexed")) != (s["key"] in iset):
            errs.append(f"standards.cards.states[{i}].indexed 与随件索引成员集不符：{s['key']}")
        indexed_of[s["key"]] = bool(s.get("indexed"))
        state_of[s["key"]] = s["state"]
    if sorted(indexed_of) != sorted(set(keys)):
        miss = sorted(set(keys) - set(indexed_of))
        extra = sorted(set(indexed_of) - set(keys))
        errs.append(f"standards.cards.states 与台账键集不符（成员级）：缺 {miss}｜多 {extra}")
    if len(states) != len(keys):
        errs.append(f"standards.cards.states {len(states)} 条 ≠ 台账 {len(keys)} 行")
    lines = cards.get("lines")
    if not isinstance(lines, list):
        errs.append("standards.cards.lines 必须是数组（逐行卡提示，由 states 派生）")
        lines = []
    want_lines = {k for k in keys if k in state_of
                  and (state_of[k] in ("valid", "stale")
                       or (state_of[k] == "missing" and not indexed_of.get(k)))}
    got_lines = [x[0] for x in lines if isinstance(x, (list, tuple)) and len(x) == 2 and _s(x[0]).strip()]
    if len(got_lines) != len(lines):
        errs.append("standards.cards.lines 每项须为 [键, 提示] 二元组")
    if sorted(got_lines) != sorted(want_lines):
        errs.append(f"standards.cards.lines 键集与 states 派生不符：缺 {sorted(want_lines - set(got_lines))}"
                    f"｜多 {sorted(set(got_lines) - want_lines)}")

    def _n(pred) -> int:
        return sum(1 for k in keys if k in state_of and pred(k))

    pop = [
        ("indexed", lambda k: indexed_of.get(k, False)),
        ("declared", lambda k: not indexed_of.get(k, False) and state_of[k] == "declared"),
        ("cached", lambda k: not indexed_of.get(k, False) and state_of[k] == "valid"),
        ("stale", lambda k: not indexed_of.get(k, False) and state_of[k] == "stale"),
        ("missing", lambda k: not indexed_of.get(k, False) and state_of[k] == "missing"),
        ("indexed_cached", lambda k: indexed_of.get(k, False) and state_of[k] == "valid"),
        ("indexed_stale", lambda k: indexed_of.get(k, False) and state_of[k] == "stale"),
        ("indexed_missing", lambda k: indexed_of.get(k, False) and state_of[k] == "missing"),
    ]
    for name, pred in pop:
        if _is_int(cards.get(name)) and cards[name] != _n(pred):
            errs.append(f"standards.cards.{name} {cards[name]} ≠ states 复算 {_n(pred)}")
    if all(_is_int(cards.get(k)) for k in ("indexed", "cached", "stale", "missing", "declared")):
        total = sum(cards[k] for k in ("indexed", "cached", "stale", "missing", "declared"))
        if total != len(keys):
            errs.append(f"standards.cards 五档合计 {total} ≠ 台账 {len(keys)} 行（有行未被查卡）")

    for name, pred in (
        ("unindexed_undeclared", lambda k: not indexed_of.get(k, False) and state_of[k] != "declared"),
        ("unverified", lambda k: not years.get(k) or not indexed_of.get(k, False)),
    ):
        got = ver.get(name)
        if not _is_int(got) or got < 0:
            errs.append(f"standards.verification.{name} 必须是非负整数")
        elif got != _n(pred):
            errs.append(f"standards.verification.{name} {got} ≠ 台账＋卡状态复算 {_n(pred)}")

    gate = st.get("gate")
    if not isinstance(gate, dict):
        errs.append("standards.gate 缺失或不是对象（须记判定与出具时点）")
    else:
        if gate.get("verdict") not in GATE_VERDICTS:
            errs.append(f"standards.gate.verdict 必须是 {GATE_VERDICTS}")
        elif gate["verdict"] == "PASS" and tally["FAIL"]:
            errs.append(f"standards.gate 报 PASS 而红 {tally['FAIL']} 项：红件不得自称放行")
        for k in ("tool", "ran_at"):
            if not _s(gate.get(k)).strip():
                errs.append(f"standards.gate.{k} 不得为空")

    # 契约判定字段（评审档 review_contract／答疑档 standards_contract）可选，但在场即校形制（第三轮 N30④）
    for cname in ("review_contract", "standards_contract"):
        c = st.get(cname)
        if c is None:
            continue
        if not isinstance(c, dict):
            errs.append(f"standards.{cname} 必须是对象")
            continue
        if c.get("verdict") not in GATE_VERDICTS:
            errs.append(f"standards.{cname}.verdict 必须是 {GATE_VERDICTS}")
        ce = c.get("errors")
        if not isinstance(ce, list) or not all(_s(x).strip() for x in ce):
            errs.append(f"standards.{cname}.errors 必须是字符串数组")
        elif (c.get("verdict") == "PASS") != (not ce):
            errs.append(f"standards.{cname} 的 verdict 与 errors 不自洽")

    qual = _s(st.get("qualification")).strip()
    if not qual:
        errs.append("standards.qualification 不得为空（定性声明须随件）")
    else:
        if mode == "review":
            for w in ("业务参考", "非正式文件"):
                if w not in qual:
                    errs.append(f"standards.qualification 缺「{w}」：对外文件评审已裁为业务参考、非正式文件")
        for w in FORMAL_CLAIMS:
            if w in qual:
                errs.append(f"standards.qualification 自称正式评审（{w}），与发起人 2026-11-12 裁定不符")
    return errs


def validate(case) -> list[str]:
    errs: list[str] = []
    if not isinstance(case, dict):
        return ["根节点必须是对象"]
    if case.get("schema") != SCHEMA:
        errs.append(f"schema 必须为 {SCHEMA}")

    meta = case.get("meta") if isinstance(case.get("meta"), dict) else {}
    if not case.get("meta"):
        errs.append("meta 缺失或不是对象")
    for key in ("title", "project", "stage", "issued_at", "compiled_by"):
        if not _s(meta.get(key)).strip():
            errs.append(f"meta.{key} 不得为空")

    tpl = meta.get("default_template")
    if tpl is not None and tpl not in TEMPLATES:
        errs.append(f"meta.default_template 必须是 {TEMPLATES}")

    issuer = meta.get("issuer")
    if not isinstance(issuer, dict):
        errs.append("meta.issuer 缺失（需 prepared_by / reviewed_by / received_by 三个签位）")
    else:
        for key in ("prepared_by", "reviewed_by", "received_by"):
            if key not in issuer:
                errs.append(f"meta.issuer.{key} 缺失")

    rev = meta.get("revision")
    if not isinstance(rev, dict):
        errs.append("meta.revision 缺失（需 version / date / note）")
    else:
        for key in ("version", "date", "note"):
            if not _s(rev.get(key)).strip():
                errs.append(f"meta.revision.{key} 不得为空")

    inputs = meta.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        errs.append("meta.inputs 至少一条")
    else:
        for i, item in enumerate(inputs):
            if not isinstance(item, dict):
                errs.append(f"meta.inputs[{i}] 不是对象")
                continue
            if not _s(item.get("name")).strip():
                errs.append(f"meta.inputs[{i}].name 不得为空")
            if item.get("status") not in READ_STATUS:
                errs.append(f"meta.inputs[{i}].status 必须是 {READ_STATUS}")

    conds = case.get("conditions")
    cids: list[str] = []
    opts: dict[str, list[str]] = {}
    tpl_name = meta.get("default_template") or "conclusion_sheet"
    if not isinstance(conds, list) or not conds:
        if tpl_name == "conclusion_sheet":
            errs.append("conditions 至少一条（工作面必须声明可改条件；对外件/决策面可为空）")
        conds = []
    for i, c in enumerate(conds):
        at = f"conditions[{i}]"
        if not isinstance(c, dict):
            errs.append(f"{at} 不是对象")
            continue
        cid = _s(c.get("id")).strip()
        if not cid:
            errs.append(f"{at}.id 不得为空")
            continue
        if cid in cids:
            errs.append(f"{at}.id 重复：{cid}")
        cids.append(cid)
        if not _s(c.get("label")).strip():
            errs.append(f"{at}.label 不得为空")
        options = c.get("options")
        if not isinstance(options, list) or len(options) < 2:
            errs.append(f"{at}.options 至少两个")
            options = []
        vals = []
        for j, o in enumerate(options):
            if not isinstance(o, dict) or not _s(o.get("value")).strip() or not _s(o.get("label")).strip():
                errs.append(f"{at}.options[{j}] 必须有 value 与 label")
            else:
                vals.append(o["value"])
        opts[cid] = vals
        dflt = _s(c.get("default"))
        if dflt not in vals:
            errs.append(f"{at}.default 必须命中某个 option")

    scope = case.get("scope") if isinstance(case.get("scope"), dict) else {}
    if not case.get("scope"):
        errs.append("scope 缺失或不是对象")
    for key in ("can_judge", "cannot_judge"):
        val = scope.get(key)
        if not isinstance(val, list) or not [x for x in val if _s(x).strip()]:
            errs.append(f"scope.{key} 至少一条（不能判什么必须写）")

    cons = case.get("conclusions")
    ids: list[str] = []
    if not isinstance(cons, list) or not cons:
        errs.append("conclusions 至少一条")
        cons = []
    for i, c in enumerate(cons):
        at = f"conclusions[{i}]"
        if not isinstance(c, dict):
            errs.append(f"{at} 不是对象")
            continue
        cid = _s(c.get("id")).strip()
        if not cid:
            errs.append(f"{at}.id 不得为空")
        elif cid in ids:
            errs.append(f"{at}.id 重复：{cid}")
        else:
            ids.append(cid)
        for key in ("item", "conclusion", "conditions"):
            if not _s(c.get(key)).strip():
                errs.append(f"{at}.{key} 不得为空")
        grade = c.get("grade")
        if grade not in GRADES:
            errs.append(f"{at}.grade 必须是 {GRADES}")
        src = c.get("source") if isinstance(c.get("source"), dict) else {}
        if not c.get("source"):
            errs.append(f"{at}.source 缺失或不是对象")
        for key in ("doc", "clause"):
            if not _s(src.get(key)).strip():
                errs.append(f"{at}.source.{key} 不得为空")
        when = c.get("when")
        if when is not None:
            if not isinstance(when, dict) or not when:
                errs.append(f"{at}.when 必须是非空对象或省略")
            else:
                for k, vs in when.items():
                    if k not in opts:
                        errs.append(f"{at}.when 引用未声明条件：{k}")
                    elif not isinstance(vs, list) or not vs:
                        errs.append(f"{at}.when.{k} 必须是数组")
                    else:
                        for v in vs:
                            if v not in opts[k]:
                                errs.append(f"{at}.when.{k} 含非法取值：{v}")
        if grade == "D":
            body = _s(c.get("conclusion"))
            for word in D_FORBIDDEN:
                if word in body:
                    errs.append(f"{at} 为 D 档却出现规范强制语「{word}」")

    warnings = case.get("warnings", [])
    if not isinstance(warnings, list):
        errs.append("warnings 必须是数组（可为空）")
        warnings = []
    for i, w in enumerate(warnings):
        at = f"warnings[{i}]"
        if not isinstance(w, dict):
            errs.append(f"{at} 不是对象")
            continue
        if not _s(w.get("text")).strip():
            errs.append(f"{at}.text 不得为空")
        when = w.get("when")
        if not isinstance(when, dict) or not when:
            errs.append(f"{at}.when 必须是非空对象")
            continue
        for k, vs in when.items():
            if k not in opts:
                errs.append(f"{at}.when 引用未声明条件：{k}")
            elif not isinstance(vs, list) or not vs:
                errs.append(f"{at}.when.{k} 必须是数组")
            else:
                for v in vs:
                    if v not in opts[k]:
                        errs.append(f"{at}.when.{k} 含非法取值：{v}")

    gaps = case.get("gaps")
    ask_ext = 0
    if not isinstance(gaps, list) or not gaps:
        errs.append("gaps 至少一条（缺什么必须显式写出）")
        gaps = []
    for i, g in enumerate(gaps):
        at = f"gaps[{i}]"
        if not isinstance(g, dict):
            errs.append(f"{at} 不是对象")
            continue
        for key in ("what", "why", "owner"):
            if not _s(g.get(key)).strip():
                errs.append(f"{at}.{key} 不得为空")
        if not isinstance(g.get("external"), bool):
            errs.append(f"{at}.external 必须是布尔值（对外件只列外部缺口）")
        kind = g.get("kind", "ask")
        if kind not in ("ask", "assume"):
            errs.append(f"{at}.kind 只能是 ask（须贵方提供）或 assume（按默认推进）")
        affects = g.get("affects")
        if not isinstance(affects, list):
            errs.append(f"{at}.affects 必须是数组")
            affects = []
        elif kind == "ask" and not affects:
            errs.append(f"{at}.affects 至少一条：须贵方提供的事项必须写明它改变哪条结论")
        for ref in affects:
            if ref not in ids:
                errs.append(f"{at}.affects 指向不存在的结论：{ref}")
        if kind == "assume":
            for key in ("default", "verify_at"):
                if not _s(g.get(key)).strip():
                    errs.append(f"{at}.{key} 不得为空（按默认推进须写明默认做法与验证节点）")
        elif g.get("external"):
            ask_ext += 1
            what = _s(g.get("what"))
            hit = next((t for t in JUDGMENT_TERMS if t in what), None)
            if not hit and re.search(r"还是|哪种|哪一|选哪", what) and re.search(r"Rw|Ctr|DnT|修正", what):
                hit = "评价量选择问句"
            if hit:
                errs.append(f"{at} 问的是判定口径（{hit}）：口径由本件按场景定，不向贵方询问")

    if ask_ext > 3:
        errs.append(f"对外必答项 {ask_ext} 条，超过 3 条：其余应改为按默认推进并写明验证节点")

    steps = case.get("next_steps")
    if not isinstance(steps, list) or not steps:
        errs.append("next_steps 至少一条")
    else:
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                errs.append(f"next_steps[{i}] 不是对象")
                continue
            for key in ("action", "owner", "trigger"):
                if not _s(s.get(key)).strip():
                    errs.append(f"next_steps[{i}].{key} 不得为空")

    overturn = case.get("overturn")
    if not isinstance(overturn, list) or not [x for x in overturn if _s(x).strip()]:
        errs.append("overturn 至少一条（什么情况下本件结论会改变）")

    withdrawn = case.get("withdrawn")
    if withdrawn is not None:
        if not isinstance(withdrawn, list):
            errs.append("withdrawn 必须是数组")
        else:
            for i, w in enumerate(withdrawn):
                if not isinstance(w, dict):
                    errs.append(f"withdrawn[{i}] 不是对象")
                    continue
                for key in ("id", "item", "conclusion", "reason"):
                    if not _s(w.get(key)).strip():
                        errs.append(f"withdrawn[{i}].{key} 不得为空")

    terms = case.get("terminology")
    if terms is not None and (not isinstance(terms, list) or not all(_s(x).strip() for x in terms)):
        errs.append("terminology 必须是非空字符串数组")

    for i, c in enumerate(case.get("conclusions") or []):
        if not isinstance(c, dict):
            continue
        at = f"conclusions[{i}]"
        doubt = c.get("doubt")
        if doubt is None:
            continue
        if c.get("review_status") != "存疑":
            errs.append(f"{at}.doubt 只在 review_status 为『存疑』时成立")
        if not isinstance(doubt, dict):
            errs.append(f"{at}.doubt 必须是对象")
            continue
        for key in ("scope", "resolve"):
            if not _s(doubt.get(key)).strip():
                errs.append(f"{at}.doubt.{key} 不得为空（存疑须写清范围与定案条件）")

    if case.get("standards") is not None:
        errs.extend(validate_standards(case["standards"]))

    return errs


def _applicable(node: dict, state: dict) -> bool:
    when = node.get("when")
    if not when:
        return True
    return all(state.get(k) in vs for k, vs in when.items())


def _default_state(case: dict) -> dict:
    return {c["id"]: c["default"] for c in case["conditions"]}


def _ul(items) -> str:
    return "<ul>" + "".join(f"<li>{html.escape(str(x))}</li>" for x in items if str(x).strip()) + "</ul>"


def _groups(case: dict) -> list[str]:
    seen, groups = set(), []
    for c in case["conclusions"]:
        g = c.get("group")
        if g and g not in seen:
            seen.add(g)
            groups.append(g)
    return groups


def _headline_pairs(case: dict, state: dict) -> list[tuple[str, dict]]:
    out = []
    for g in _groups(case):
        hit = next((c for c in case["conclusions"] if c.get("group") == g and _applicable(c, state)), None)
        if hit:
            out.append((g, hit))
    return out


def _sign_table(case: dict) -> str:
    meta = case["meta"]
    issuer = meta["issuer"]
    e = html.escape

    def cell(key: str) -> str:
        val = _s(issuer.get(key)).strip()
        return e(val) if val else '<span class="blank"></span>'

    return (
        '<table class="sign">'
        f'<tr><th>编制</th><td>{cell("prepared_by")}</td><th>复核</th><td>{cell("reviewed_by")}</td></tr>'
        f'<tr><th>接收</th><td>{cell("received_by")}</td><th>日期</th><td>{e(meta["issued_at"])}</td></tr>'
        "</table>"
    )


def _withdrawn_table(case: dict) -> str:
    if not case.get("withdrawn"):
        return ""
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(w['id'])}</td><td>{e(w['item'])}</td><td>{e(w['conclusion'])}</td><td>{e(w['reason'])}</td></tr>"
        for w in case["withdrawn"]
    )
    return (
        "<h2>已剔除的答复条目（不构成本件结论）</h2>"
        "<table><thead><tr><th>原编号</th><th>结论项</th><th>原答复表述</th><th>剔除理由</th></tr></thead><tbody>"
        + rows + "</tbody></table>"
    )


def _card_label(state: str) -> str:
    return {"valid": "有效", "stale": "已过期", "missing": "无卡", "declared": "已声明外源"}.get(state, "无卡")


STANDARDS_HEADERS = ("标准编号", "引用年号", "引用点", "索引", "证据卡")


def standards_rows(st: dict) -> list[list[str]]:
    """台账逐行的取数单源（HTML 表与 docx 表共用，杜绝第二份手写台账）。"""
    states = {s["key"]: s for s in st["cards"]["states"]}
    return [
        [
            r["key"],
            "、".join(str(y) for y in (r.get("cited_years") or [])) or "未注",
            str(r.get("count") or 0),
            "索引内" if states.get(r["key"], {}).get("indexed") else "索引外",
            _card_label(states.get(r["key"], {}).get("state", "missing")),
        ]
        for r in st["ledger"]["rows"]
    ]


def standards_table(st: dict) -> str:
    """台账逐行表（编号／年号／引用点／索引／卡）。工作面与评审件共用，杜绝第二份手写台账。"""
    e = html.escape
    rows = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in row) + "</tr>"
                   for row in standards_rows(st))
    return ("<table><thead><tr>" + "".join(f"<th>{e(h)}</th>" for h in STANDARDS_HEADERS)
            + "</tr></thead><tbody>" + rows + "</tbody></table>")


def standards_receipt(st: dict) -> str:
    """出口闸回执字面（计数＋卡消费＋判定）。三形态与评审件共用同一份，不另写常量。"""
    ver, cards, led = st["verification"], st["cards"], st["ledger"]
    rows = led["rows"]
    pts = sum(r.get("count") or 0 for r in rows)
    return (f"红 {ver['red']}｜黄 {ver['yellow']}｜绿 {ver['green']}｜"
            f"未核 {ver['unverified']} 枚｜索引外未声明 {ver['unindexed_undeclared']} 枚｜"
            f"台账 {len(rows)} 枚／{pts} 引用点｜"
            f"证据卡：索引内 有效 {cards['indexed_cached']}／过期 {cards['indexed_stale']}／无卡 {cards['indexed_missing']}，"
            f"索引外未声明 有效 {cards['cached']}／过期 {cards['stale']}／无卡 {cards['missing']}，"
            f"已声明外源 {cards['declared']}｜出口闸 {st['gate']['verdict']}")


# 对外面判词清洗：verify 判词内嵌索引 status 全文，而 status 是内部治理散文
# （带仓内路径／收录批号／平台查询串／内部节号／档位码 S1-S3／路由码 WS 域）。
# 第二轮复核 B6 实测：逐类剥字面会留下编号残尾与病句（20/87 键），故改**结构性收口**——
# 凡含状态前缀的配平括号段整段收为「（前缀）」，对外只出分类不出内部凭据散文；
# 其后再剥反引号 span／仓内路径／URL／查询串并收口空括号与叠标点。内部代号由 _ext 承担。
_REPO_PATH_RE = re.compile(r"(?:成果|记录|文档|程序文件|技能仓备份|_专题_[^\s/，。；）)]*)/[^\s，。；）)」』】`]+")
_QUERY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\?[^\s，。；）)」』】`]+")
# 第四轮 N35／第五轮 N41／第六轮 N44：内部字面类（§／档位码／本仓／WS 域）的剥离单源＝
# `_ext_msg` 内继 `_ext(_collapse_status(...))` 之后、其余剥支之前对**全文**生效（一处覆盖裸文本／半角括号／
# 全角括号三形；该前置位置载重——须先剥字面再清括号/标点，否则残段清不净；_ext_msg 只服务对外面，
# 工作面仍留原文，不影响内部判定证据）。`_collapse_status` 只负责括号段结构：含前缀配平段收为「（前缀）」、
# 配平但无前缀且**含内部字面**的段整段删除（配平故不吞尾句编号年号，第六轮 N45）、其余原样保留。
# 编号与年号不在该针内，不吞尾。已知取舍（第五轮 N42，据实披露）：「本仓」「WS 域」无词边界，
# 会误吃含该子串的合法中文词（基本仓储／成本仓位／WS 域外）；现批实物 0 命中，不加白名单式边界
# （脆弱且漏内部指称形如「本仓现读」）；翻转前置＝对外面出现含该子串的合法词时改独立指称形。
_INTERNAL_LITERAL_RE = re.compile(r"§|（S\d）|\(S\d\)|S[0-3]|WS\s*域|本仓")


def _collapse_status(t: str) -> str:
    from citation_ledger import STATUS_PREFIXES

    out, i = [], 0
    while i < len(t):
        if t[i] != "（":
            out.append(t[i])
            i += 1
            continue
        depth, j = 0, i
        while j < len(t):
            if t[j] == "（":
                depth += 1
            elif t[j] == "）":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            # 不配平（调用方可写数据面）：不收口不删段，避免吞掉尾句编号与年号（第三轮 N30②）；
            # 内部字面由 _ext_msg 全文层剥（第六轮 N44 单源）
            out.append(t[i:])
            break
        seg = t[i:j + 1]
        # 取段内**最早位置**的前缀，不按元组序——否则「已废止（…原记现行有效…）」会被误标（第三轮 N23）
        hit = min((p for p in STATUS_PREFIXES if p in seg), key=seg.index, default=None)
        if hit:
            out.append(f"（{hit}）")
        elif _INTERNAL_LITERAL_RE.search(seg):
            # 配平且含内部字面：整段删除（配平故不吞尾；剥字面会留病句残段，第六轮 N45）
            out.append("")
        else:
            out.append(seg)
        i = j + 1
    return "".join(out)


def _ext_msg(msg: str) -> str:
    t = _ext(_collapse_status(msg))
    # 第六轮 N44：内部字面类全文层剥（单源），覆盖裸文本／半角括号／全角括号三形
    t = _INTERNAL_LITERAL_RE.sub("", t)
    t = re.sub(r"`[^`]*`", "", t)
    t = _REPO_PATH_RE.sub("", t)
    t = re.sub(r"https?://\S+", "", t)
    t = _QUERY_RE.sub("", t)
    t = re.sub(r"（[，、；\s]*）", "", t)
    t = re.sub(r"\([，、；\s]*\)", "", t)
    t = re.sub(r"[，、；]{2,}", "，", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip().rstrip("，、；")


def standards_issues(st: dict, levels=("FAIL", "WARN"), external: bool = False) -> str:
    e = html.escape
    items = "".join(f"<li>[{e(lv)}] {e(_ext_msg(msg) if external else msg)}</li>"
                    for lv, msg in st["verification"]["results"] if lv in levels)
    return f"<ul>{items}</ul>" if items else ("<p>无红项。</p>" if levels == ("FAIL",) else "<p>无红黄项。</p>")


def _standards_section(case: dict) -> str:
    st = case.get("standards")
    if not st:
        return ""
    e = html.escape
    return (
        "<h2>标准引用核验（台账随件）</h2>"
        f'<div class="strip"><b>出口闸回执：</b>{e(standards_receipt(st))}</div>'
        f'<p class="note">{e(st["qualification"])}</p>'
        + standards_table(st)
        + '<p class="legend">未核＝身份未核（索引外）或版本未核（引用未注年号）的台账行枚数；'
        "计数由台账与判定明细现算，不是手写值。证据卡只补证据信息，不改判定。</p>"
        + standards_issues(st)
    )


def render_sheet(case: dict, digest: str, name: str) -> str:
    if not case["conditions"]:
        raise ValueError("工作面需要至少一个可改条件；无变量答复请用 reply_letter 或 brief_card")
    meta, scop = case["meta"], case["scope"]
    e = html.escape
    state = _default_state(case)
    rev = meta["revision"]

    meta_rows = "".join(
        f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>"
        for k, v in (("项目", meta["project"]), ("阶段", meta["stage"]),
                     ("出具日期", meta["issued_at"]), ("版本", f'{rev["version"]}（{rev["note"]}）'),
                     ("编制", meta["compiled_by"]))
    )
    controls = "".join(
        '<div class="field">'
        f'<label for="cond-{e(c["id"])}">{e(c["label"])}</label>'
        f'<select id="cond-{e(c["id"])}" data-cond="{e(c["id"])}">'
        + "".join(
            f'<option value="{e(o["value"])}"{" selected" if o["value"] == c["default"] else ""}>{e(o["label"])}</option>'
            for o in c["options"]
        )
        + "</select></div>"
        for c in case["conditions"]
    )
    def _gap_how(g: dict) -> str:
        if g.get("kind", "ask") == "assume":
            return f"按默认推进：{g.get('default', '')}（验证节点：{g.get('verify_at', '')}）"
        return f"须贵方提供（影响：{'、'.join(g['affects'])}）"

    gap_rows = "".join(
        "<tr>"
        f'<td><input class="gapbox" type="checkbox" data-gap="{e(g["what"])}" aria-label="已补齐"></td>'
        f'<td>{e(g["what"])}</td><td>{e(g["why"])}</td><td>{e(g["owner"])}</td>'
        f"<td>{e(_gap_how(g))}</td>"
        "</tr>"
        for g in case["gaps"]
    )
    step_rows = "".join(
        f"<tr><td>{i}</td><td>{e(s['action'])}</td><td>{e(s['owner'])}</td><td>{e(s['trigger'])}</td></tr>"
        for i, s in enumerate(case["next_steps"], 1)
    )
    input_rows = "".join(
        f'<tr><td>{e(it["name"])}</td><td>{e(it["status"])}</td><td>{e(it.get("note") or "")}</td></tr>'
        for it in meta["inputs"]
    )
    headline = "".join(
        '<div class="hl">'
        f'<div class="hl-item">{e(h["item"])}</div>'
        f'<div class="hl-text">{e(h["conclusion"])}</div>'
        f'<div class="hl-g"><span class="badge g-{e(h["grade"])}">{e(h["grade"])}</span>{e(GRADE_LABEL[h["grade"]])}</div>'
        "</div>"
        for _, h in _headline_pairs(case, state)
    )
    rows = "".join(
        "<tr>"
        f'<td class="cid">{e(c["id"])}</td>'
        f'<td>{e(c["item"])}{"〔存疑〕" if c.get("review_status") == "存疑" else ""}</td>'
        f'<td>{e(c["conclusion"])}<div class="cond">适用条件：{e(c["conditions"])}</div>'
        + (f'<div class="note">存疑范围：{e(c["doubt"]["scope"])}｜定案条件：{e(c["doubt"]["resolve"])}</div>'
           if isinstance(c.get("doubt"), dict) else "")
        + (f'<div class="note">{e(c["note"])}</div>' if _s(c.get("note")).strip() else "")
        + "</td>"
        f'<td><span class="badge g-{e(c["grade"])}">{e(c["grade"])}</span>'
        f'<div class="note">{e(GRADE_LABEL[c["grade"]])}</div></td>'
        f'<td>{e(c["source"]["doc"])}<div class="note">{e(c["source"]["clause"])}</div></td>'
        "</tr>"
        for c in case["conclusions"] if _applicable(c, state)
    )
    warns = "".join(
        f'<div class="warn"><strong>条件冲突</strong>{e(w["text"])}</div>'
        for w in case.get("warnings", []) if _applicable(w, state)
    )

    payload = {
        "conditions": case["conditions"],
        "conclusions": [dict(c, grade_label=GRADE_LABEL[c["grade"]]) for c in case["conclusions"]],
        "warnings": case.get("warnings", []),
    }
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    default_headline = '<p class="note">当前条件组合下没有可判结论——请补充条件或转人工复核。</p>'

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{e(meta['title'])}</title>",
        f"<style>{CSS_SHEET}</style>",
        "</head><body><main class=\"page\">",
        '<div class="kicker">工作面 · 条件可改 · 结论随条件重算</div>',
        f"<h1>{e(meta['title'])}</h1>",
        f'<table class="meta">{meta_rows}</table>',
        f'<div class="strip"><b>本件不判：</b>{e("；".join(scop["cannot_judge"][:3]))}</div>',
        "<h2>条件</h2>",
        f'<div class="controls">{controls}</div>',
        f'<div id="warnings">{warns}</div>',
        "<h2>当前结论</h2>",
        f'<div id="headline">{headline or default_headline}</div>',
        f'<h2>结论与依据（<span id="ccount">{len([c for c in case["conclusions"] if _applicable(c, state)])}</span> 条适用）</h2>',
        "<table><thead><tr><th>#</th><th>结论项</th><th>结论与适用条件</th><th>档位</th><th>依据</th></tr></thead>",
        f'<tbody id="ctbody">{rows}</tbody></table>',
        '<p class="legend">档位：A 规范明文（可核到条款）· B 可溯源报告 · C 行业通用做法或设计取值 · D 待核（不得作规范要求）· N 不适用。'
        "档位口径单源＝各技能 §7；本件只转写，不改判。</p>",
        (f'<p class="legend">术语提示：{e("；".join(case["terminology"]))}</p>' if case.get("terminology") else ""),
        _withdrawn_table(case),
        "<h2>缺什么、谁给、影响哪条</h2>",
        "<table><thead><tr><th>已补</th><th>缺什么</th><th>为什么要</th><th>谁提供</th><th>处理方式／影响哪条</th></tr></thead><tbody>",
        gap_rows,
        "</tbody></table>",
        "<h2>下一步</h2>",
        "<table><thead><tr><th>#</th><th>动作</th><th>责任方</th><th>触发时点</th></tr></thead><tbody>",
        step_rows,
        "</tbody></table>",
        "<h2>什么情况下本件结论会改变</h2>",
        _ul(case["overturn"]),
        '<details><summary>本件边界（能判 / 不能判）与输入溯源</summary>',
        '<p class="note" style="margin-top:8px"><b>能判：</b></p>',
        _ul(scop["can_judge"]),
        '<p class="note"><b>不能判：</b></p>',
        _ul(scop["cannot_judge"]),
        '<p class="note"><b>输入与读取状态：</b></p>',
        "<table><thead><tr><th>输入</th><th>读取状态</th><th>说明</th></tr></thead><tbody>",
        input_rows,
        "</tbody></table>",
        "</details>",
        _standards_section(case),
        "<h2>签署</h2>",
        _sign_table(case),
        '<div class="actions">',
        '<button class="primary" id="exportBtn" type="button">导出已填件（存档）</button>',
        '<button id="printBtn" type="button">打印 / 导出 PDF</button>',
        '<span id="status"></span>',
        "</div>",
        "<footer>",
        "<p>本单由装配式装修技能合集生成，供技术参考。标准规范以官方发布的现行有效版本为准。"
        "具体设计、施工及验收由项目设计单位与具备资质的专业人员负责。</p>",
        f'<p class="gen">来源：{e(name)} ｜ sha256：{e(digest)} ｜ {e(rev["version"])} ｜ '
        f'结论 {len(case["conclusions"])} 条（按条件显示） ｜ 缺件 {len(case["gaps"])} 条</p>',
        "</footer>",
        "</main>",
        f'<script id="data" type="application/json">{data_json}</script>',
        '<script id="saved-state" type="application/json">null</script>',
        f"<script>{JS}</script>",
        "</body></html>",
    ]
    return "\n".join(parts) + "\n"


def _condition_line(case: dict, state: dict) -> str:
    bits = []
    for c in case["conditions"]:
        label = next((o["label"] for o in c["options"] if o["value"] == state.get(c["id"])), "")
        bits.append(f'{c["label"]}＝{label}')
    return "；".join(bits)


def render_letter(case: dict, digest: str, name: str) -> str:
    meta, scop = case["meta"], case["scope"]
    e = html.escape
    state = _default_state(case)
    rev = meta["revision"]

    items = []
    for c in case["conclusions"]:
        if not _applicable(c, state):
            continue
        doubt = c.get("doubt") or {}
        tail = ""
        if c.get("review_status") == "存疑":
            scope = _s(doubt.get("scope")).strip() or "整条"
            where = "本条整体尚未确认" if scope == "整条" else f"其中{scope}尚待核实"
            tail = f"（{where}，不作为本件结论；定案条件：{doubt.get('resolve', '')}）"
        items.append(
            '<div class="item">'
            f'<div class="k">{e(c["item"])}{"（依据待核）" if c["grade"] == "D" else ""}</div>'
            f"<p>{e(_ext(c['conclusion'])) + e(tail)}</p>"
            f'<div class="src">适用条件：{e(_ext(c["conditions"]))} ｜ 依据：{e(_ext(c["source"]["doc"]))} {e(_ext(c["source"]["clause"]))}</div>'
            "</div>"
        )

    item_of = {c["id"]: c["item"] for c in case["conclusions"]}
    ask = "".join(
        f"<li><b>{e(_ext(g['what']))}</b>——{e(_ext(g['why']))}（提供方：{e(g['owner'])}；"
        f"影响：{e('、'.join(item_of.get(a, a) for a in g['affects']))}）</li>"
        for g in case["gaps"] if g.get("external") and g.get("kind", "ask") == "ask"
    )
    assumed = "".join(
        f"<li><b>{e(_ext(g['what']))}</b>——按默认推进：{e(_ext(g.get('default', '')))}；验证节点：{e(_ext(g.get('verify_at', '')))}</li>"
        for g in case["gaps"] if g.get("external") and g.get("kind") == "assume"
    )

    steps = "".join(f"<li>{e(s['action'])}（{e(s['owner'])}，{e(s['trigger'])}）</li>" for s in case["next_steps"])

    cond_text = _condition_line(case, state)
    cond_sentence = (
        f"本答复基于下列已锁定条件：{cond_text}；条件变化时结论需重新判定。"
        if cond_text else
        "本答复未绑定可变条件；若项目条件与本答复所述不一致，结论需重新判定。"
    )
    fixes = []
    for w in case.get("withdrawn", []):
        fixes.append(
            '<div class="item">'
            f'<p class="orig">原答复：「{e(_ext(w["conclusion"]))}」</p>'
            f"<p>本件不予采用。{e(_ext(w['reason']))}</p>"
            "</div>"
        )

    sections = [("结论与依据", items or ["<p>当前条件下没有可答复的结论。</p>"])]
    if fixes:
        sections.append(("已更正的表述", ["<p>原答复中的下列表述经复核不成立，本件不予采用，特此更正。</p>", *fixes]))
    ask_block = [f"<ul>{ask}</ul>"] if ask else ["<p>无需贵方另行提供事项。</p>"]
    if assumed:
        ask_block.append("<p>下列事项不影响本件结论，本件已按默认推进，请在标注节点验证；如与贵方要求不符，请回函更正。</p>")
        ask_block.append(f"<ul>{assumed}</ul>")
    sections += [
        ("需贵方提供或确认的事项", ask_block),
        ("下一步", [f"<ul>{steps}</ul>"]),
        ("本答复的边界", [f"<p>{e('；'.join(_ext(x).rstrip('。') for x in scop['cannot_judge']))}。</p>"]),
        ("答复失效条件", [_ul([_ext(x) for x in case["overturn"]])]),
    ]
    if case.get("standards"):
        st = case["standards"]
        sections.append(("标准引用核验", [
            f'<p class="note">{e(standards_receipt(st))}</p>',
            f'<p class="note">{e(st["qualification"])}</p>',
            standards_issues(st, levels=("FAIL",), external=True),
        ]))
    numbered = []
    for i, (title, body) in enumerate(sections):
        numbered.append(f"<h2>{NUMERALS[i]}、{title}</h2>")
        numbered += body

    term_block = [f'<p class="note">术语：{e(t)}</p>' for t in case.get("terminology", [])]

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{e(meta['title'])}（对外答复）</title>",
        f"<style>{CSS_LETTER}</style>",
        "</head><body><main class=\"page\">",
        f"<h1>{e(meta['title'])}</h1>",
        f'<div class="sub">{e(meta["project"])} ｜ {e(meta["stage"])} ｜ {e(rev["version"])} ｜ {e(meta["issued_at"])}</div>',
        f'<p class="to">致：{e(_s(meta["issuer"].get("received_by")).strip() or "（接收方）")}</p>',
        f'<p>根据贵方提供的项目条件，就本项目{ e(meta["stage"]) }事项，现将结论与依据答复如下。{e(cond_sentence)}</p>',
        *term_block,
        *numbered,
        _sign_table(case),
        "<footer>",
        "<p>本答复由装配式装修技能合集生成，供技术参考。标准规范以官方发布的现行有效版本为准。"
        "具体设计、施工及验收由项目设计单位与具备资质的专业人员负责。</p>",
        f'<p class="gen">来源：{e(name)} ｜ sha256：{e(digest)} ｜ {e(rev["version"])}（{e(rev["note"])}）</p>',
        "</footer>",
        "</main></body></html>",
    ]
    return "\n".join(parts) + "\n"


def render_brief(case: dict, digest: str, name: str) -> str:
    meta = case["meta"]
    e = html.escape
    state = _default_state(case)
    rev = meta["revision"]

    rows = "".join(
        f'<div class="crow"><div class="ck">{e(h["item"])}</div><div class="cv">{e(h["conclusion"])}</div></div>'
        for _, h in _headline_pairs(case, state)
    )
    if not rows:
        rows = "".join(
            f'<div class="crow"><div class="ck">{e(c["item"])}</div><div class="cv">{e(c["conclusion"])}</div></div>'
            for c in case["conclusions"][:4]
        )
    rows = rows or '<p class="note">当前条件下没有可判结论。</p>'

    gaps = "".join(f"<li>{e(g['what'])}（{e(g['owner'])}）</li>" for g in case["gaps"] if g.get("external"))
    steps = "".join(f"<li>{e(s['action'])}（{e(s['owner'])}，{e(s['trigger'])}）</li>" for s in case["next_steps"])

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{e(meta['title'])}（速览）</title>",
        f"<style>{CSS_BRIEF}</style>",
        "</head><body><main class=\"page\">",
        '<div class="kicker">决策速览 · 只给结论、缺口与动作</div>',
        f"<h1>{e(meta['title'])}</h1>",
        f'<div class="concl">{rows}</div>',
        "<h2>必须先补齐</h2>",
        f"<ul>{gaps}</ul>",
        "<h2>下一步</h2>",
        f"<ul>{steps}</ul>",
        f'<div class="no"><b>本件不判：</b>{e("；".join(case["scope"]["cannot_judge"][:3]))}</div>',
        f'<div class="no"><b>会推翻结论的情况：</b>{e("；".join(x.rstrip("。") for x in case["overturn"]))}。</div>',
        (f'<div class="no"><b>已更正：</b>{len(case.get("withdrawn", []))} 条原答复表述经复核不成立，本件不予采用。</div>'
         if case.get("withdrawn") else ""),
        (f'<div class="no"><b>尚待核实：</b>{sum(1 for c in case["conclusions"] if c.get("review_status") == "存疑")} 条结论未定案，'
         f'按各项所列定案条件补齐后再引用。</div>'
         if any(c.get("review_status") == "存疑" for c in case["conclusions"]) else ""),
        (f'<div class="no"><b>标准引用核验：</b>{e(standards_receipt(case["standards"]))}<br>'
         f'{e(case["standards"]["qualification"])}</div>' if case.get("standards") else ""),
        "<footer>",
        f"<p>{e(meta['project'])} ｜ {e(meta['issued_at'])} ｜ {e(rev['version'])} ｜ 编制：{e(_s(meta['issuer'].get('prepared_by')).strip() or '______')}"
        f" ｜ 复核：{e(_s(meta['issuer'].get('reviewed_by')).strip() or '______')}</p>",
        f'<p class="gen">来源：{e(name)} ｜ sha256：{e(digest)}</p>',
        "</footer>",
        "</main></body></html>",
    ]
    return "\n".join(parts) + "\n"


REVIEW_SCHEMA = "prefab-review/v1"
SEVERITIES = ("A", "B", "C")
SEVERITY_LABEL = {"A": "阻断交付", "B": "交付前应改", "C": "可交付后修订"}


def validate_review(rep) -> list[str]:
    """文件评审件契约：问题条目由调用方数据给，台账与回执由机算块给，两者都不得写死统计。"""
    errs: list[str] = []
    if not isinstance(rep, dict):
        return ["根节点必须是对象"]
    if rep.get("schema") != REVIEW_SCHEMA:
        errs.append(f"schema 必须为 {REVIEW_SCHEMA}")
    meta = rep.get("meta") if isinstance(rep.get("meta"), dict) else {}
    if not meta:
        errs.append("meta 缺失或不是对象")
    for key in ("title", "subject", "issued_at", "compiled_by", "scope_note"):
        if not _s(meta.get(key)).strip():
            errs.append(f"meta.{key} 不得为空")
    for key in ("title", "subject"):
        for w in FORMAL_CLAIMS:
            if w in _s(meta.get(key)):
                errs.append(f"meta.{key} 自称正式评审（{w}）：对外文件评审已裁为业务参考、非正式文件")
    inputs = meta.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        errs.append("meta.inputs 至少一条（被评审文件须逐件列名与读取状态）")
    else:
        for i, it in enumerate(inputs):
            if not isinstance(it, dict) or not _s(it.get("name")).strip():
                errs.append(f"meta.inputs[{i}].name 不得为空")
            elif it.get("status") not in READ_STATUS:
                errs.append(f"meta.inputs[{i}].status 必须是 {READ_STATUS}")
    findings = rep.get("findings")
    if not isinstance(findings, list):
        errs.append("findings 必须是数组（可为空：零问题也是合法评审结论）")
        findings = []
    seen: list[str] = []
    for i, f in enumerate(findings):
        at = f"findings[{i}]"
        if not isinstance(f, dict):
            errs.append(f"{at} 不是对象")
            continue
        for key in ("id", "dim", "loc", "issue", "fix"):
            if not _s(f.get(key)).strip():
                errs.append(f"{at}.{key} 不得为空")
        if f.get("sev") not in SEVERITIES:
            errs.append(f"{at}.sev 必须是 {SEVERITIES}")
        fid = _s(f.get("id")).strip()
        if fid in seen:
            errs.append(f"{at}.id 重复：{fid}")
        seen.append(fid)
    st = rep.get("standards")
    if st is None:
        errs.append("standards 缺失：评审件必须随机算台账与出口闸回执，不得只给问题清单")
    else:
        errs.extend(validate_standards(st))
        if isinstance(st, dict) and isinstance(st.get("verification"), dict):
            if st["verification"].get("mode") != "review":
                errs.append("评审件的 standards.verification.mode 必须是 review")
    return errs


def render_review(rep: dict, digest: str, name: str) -> str:
    """文件评审件 → 单文件 HTML。台账附录全部由 standards 块渲染，本函数不含任何统计常量。

    脱敏射程（第二轮 N14 据实声明）：只有 verify 判词经 `_ext_msg`（内嵌索引状态散文属内部治理面）；
    `meta` 与 `findings` 是调用方数据，本函数只转写不脱敏——被评审对象自己的分级（如 S0～S3）
    属评审内容，一律剥掉会毁掉结论。内部编号／仓内路径／URL 须由调用方在数据里自行清除。
    """
    e = html.escape
    meta, st = rep["meta"], rep["standards"]
    findings = rep["findings"]
    sev_n = {s: sum(1 for f in findings if f["sev"] == s) for s in SEVERITIES}
    dims: list[str] = []
    for f in findings:
        if f["dim"] not in dims:
            dims.append(f["dim"])

    meta_rows = "".join(
        f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>"
        for k, v in (("评审对象", meta["subject"]), ("出具日期", meta["issued_at"]),
                     ("编制", meta["compiled_by"]), ("问题条目", f"{len(findings)} 项"))
    )
    input_rows = "".join(
        f'<tr><td>{e(it["name"])}</td><td>{e(it["status"])}</td><td>{e(it.get("note") or "")}</td></tr>'
        for it in meta["inputs"]
    )
    blocks = []
    for dim in dims:
        sub = [f for f in findings if f["dim"] == dim]
        rows = "".join(
            "<tr>"
            f'<td>{e(f["id"])}</td><td>{e(f["loc"])}</td><td>{e(f["issue"])}</td>'
            f'<td>{e(f["fix"])}</td><td>{e(f["sev"])} {e(SEVERITY_LABEL[f["sev"]])}</td>'
            "</tr>"
            for f in sub
        )
        blocks.append(
            f"<h3>{e(dim)}（{len(sub)} 项）</h3>"
            "<table><thead><tr><th>编号</th><th>位置</th><th>问题</th><th>建议</th><th>等级</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{e(meta['title'])}</title>",
        f"<style>{CSS_SHEET}</style>",
        "</head><body><main class=\"page\">",
        '<div class="kicker">文件评审 · 业务参考 · 非正式文件</div>',
        f"<h1>{e(meta['title'])}</h1>",
        f'<div class="warn"><strong>本件定性</strong>{e(st["qualification"])}</div>',
        f'<table class="meta">{meta_rows}</table>',
        f'<div class="strip"><b>出口闸回执：</b>{e(standards_receipt(st))}</div>',
        "<h2>评审范围与边界</h2>",
        f"<p>{e(meta['scope_note'])}</p>",
        "<h2>被评审文件与读取状态</h2>",
        "<table><thead><tr><th>文件</th><th>读取状态</th><th>说明</th></tr></thead>"
        f"<tbody>{input_rows}</tbody></table>",
        "<h2>问题清单</h2>",
        f'<p class="legend">共 {len(findings)} 项：'
        + "；".join(f"{s} {SEVERITY_LABEL[s]} {sev_n[s]} 项" for s in SEVERITIES)
        + "。等级与建议由评审数据给出，本件只转写。</p>",
        *blocks,
        "<h2>附录　标准引用核验台账</h2>",
        f'<p class="legend">本附录由机算台账渲染：编号 {len(st["ledger"]["rows"])} 枚、引用点 '
        f'{sum(r.get("count") or 0 for r in st["ledger"]["rows"])} 个，均由 citation_ledger 抽取后现算，'
        "不是手写值。逐枚状态见判定明细。</p>",
        standards_table(st),
        '<p class="legend">未核＝身份未核（索引外）或版本未核（引用未注年号）的台账行枚数。'
        "证据卡只补证据信息，不改判定；索引内行同样报卡状态。</p>",
        "<h3>判定明细（红／黄逐条）</h3>",
        standards_issues(st, external=True),
        "<footer>",
        f"<p>{e(st['qualification'])}</p>",
        f'<p class="gen">来源：{e(name)} ｜ sha256：{e(digest)} ｜ 出口闸 {e(st["gate"]["verdict"])}'
        f'（{e(st["gate"]["tool"])}，{e(st["gate"]["ran_at"])}）</p>',
        "</footer>",
        "</main></body></html>",
    ]
    return "\n".join(parts) + "\n"


# ================================ docx 面（OOXML 直写，零第三方依赖）================================
# 版式单源＝代码内一组 DX_* 常量（不留第二份），取值逐项可指回 `核验成果/` 两份真实对外评审件的复算：
# 前者＝2026-11-10 发布前专家评审报告件（A4 11906×16838、页边距上 1361／左右 1474／下 1247、
# 待填底色 FFF2CC、单元格上下 40、表头黑体加粗＋正文宋体）；
# 后者＝20261104 技术评审意见件（表格四边与内框单线 sz=4、单元格左右 108、正文 21 半磅）。
# 两份的 pgMar 本就不同（后者上 1361／右 1247／下 1247／左 1361），本批页边距取前者；
# 西文字体走 Times New Roman。两份件名含客户项目名，故此处只留日期形制指称——随包工具正文出现
# 该字面即被 `deliverable_acceptance_test.test_tools_carry_no_customer_constants` 判红；
# 件名全称与逐项复算入口见本批执行记录 §二。
# 依赖选型＝stdlib：本件是随包工具（`sync_skill_backup.py` 的 TOOL_BUNDLE 把它推到
# `<技能>/scripts/`），而公开面 README 的「零第三方依赖」字面正对随包面——引第三方库会当场
# 使该对外承诺不实。判据与否因见本批执行记录。
DOCX_SUFFIX = ".docx"
DOCX_FP_MEMBER = "docProps/core.xml"        # docx 侧指纹承载件（zip 内明文 XML，Word 不重排）
DX_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
DX_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
DX_MIME = {
    "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "styles": "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
    "core": "application/vnd.openxmlformats-package.core-properties+xml",
}
DX_PAGE_W, DX_MAR_TB, DX_MAR_LR = 11906, (1361, 1247), 1474
DX_CONTENT_W = DX_PAGE_W - 2 * DX_MAR_LR    # 版心宽（twips），列宽按它分配
DX_FILL_BLANK = "FFF2CC"                    # 待填格底色
DX_ZIP_STAMP = (1980, 1, 1, 0, 0, 0)        # 时间戳钉死＝两次渲染逐字节全等的前提
DX_HINT = ("金色底格为待填项，在 Word 中点击即可键入；其余文字为本件结论，"
           "件内两处（正文来源行与文档属性）各记一枚来源件指纹，二者与当前来源件不符时判为陈旧件。")


def _dx_pstyle(sid: str, ea: str, sz: int, *, bold: bool = False, before: int = 0,
               after: int = 60, line: int = 276, jc: str | None = None,
               color: str | None = None, keep_next: bool = False) -> str:
    ppr = (("<w:keepNext/>" if keep_next else "")
           + f'<w:spacing w:before="{before}" w:after="{after}" w:line="{line}" w:lineRule="auto"/>'
           + (f'<w:jc w:val="{jc}"/>' if jc else ""))
    rpr = (f'<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="{ea}"/>'
           + ("<w:b/><w:bCs/>" if bold else "")
           + (f'<w:color w:val="{color}"/>' if color else "")
           + f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>')
    return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{sid}"/><w:qFormat/>'
            f"<w:pPr>{ppr}</w:pPr><w:rPr>{rpr}</w:rPr></w:style>")


DX_STYLES = DX_XML + (
    f'<w:styles {DX_NS}>'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体" w:cs="Times New Roman"/>'
    '<w:sz w:val="21"/><w:szCs w:val="21"/>'
    '<w:lang w:val="en-US" w:eastAsia="zh-CN" w:bidi="ar-SA"/>'
    "</w:rPr></w:rPrDefault>"
    '<w:pPrDefault><w:pPr><w:spacing w:after="60" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
    "</w:docDefaults>"
    + _dx_pstyle("Normal", "宋体", 21)
    + _dx_pstyle("dxTitle", "黑体", 32, bold=True, before=240, after=120, jc="center")
    + _dx_pstyle("dxSub", "宋体", 21, after=40, jc="center", color="595959")
    + _dx_pstyle("dxH", "黑体", 24, bold=True, before=240, after=80, keep_next=True)
    + _dx_pstyle("dxItem", "黑体", 21, bold=True, after=20, keep_next=True)
    + _dx_pstyle("dxNote", "宋体", 18, after=40, color="595959")
    + _dx_pstyle("dxSrc", "宋体", 18, after=40, color="595959")
    + _dx_pstyle("dxCell", "宋体", 18, after=20, line=240)
    + _dx_pstyle("dxCellH", "黑体", 18, bold=True, after=20, line=240)
    + _dx_pstyle("dxGap", "宋体", 8, after=0, line=240)
    + "</w:styles>"
)
DX_TABLE_PR = (
    "<w:tblPr>"
    '<w:tblW w:w="0" w:type="auto"/>'
    "<w:tblBorders>"
    + "".join(f'<w:{k} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
              for k in ("top", "left", "bottom", "right", "insideH", "insideV"))
    + "</w:tblBorders>"
    '<w:tblLayout w:type="fixed"/>'        # CT_TblPrBase：tblLayout 须排在 tblCellMar 之前
    "<w:tblCellMar>"
    '<w:top w:w="40" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
    '<w:bottom w:w="40" w:type="dxa"/><w:right w:w="108" w:type="dxa"/>'
    "</w:tblCellMar>"
    "</w:tblPr>"
)
DX_SECTPR = (
    "<w:sectPr>"
    f'<w:pgSz w:w="{DX_PAGE_W}" w:h="16838"/>'
    f'<w:pgMar w:top="{DX_MAR_TB[0]}" w:right="{DX_MAR_LR}" w:bottom="{DX_MAR_TB[1]}" '
    f'w:left="{DX_MAR_LR}" w:header="720" w:footer="720" w:gutter="0"/>'
    "</w:sectPr>"
)
DX_CONTENT_TYPES = DX_XML + (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    f'<Override PartName="/word/document.xml" ContentType="{DX_MIME["document"]}"/>'
    f'<Override PartName="/word/styles.xml" ContentType="{DX_MIME["styles"]}"/>'
    f'<Override PartName="/{DOCX_FP_MEMBER}" ContentType="{DX_MIME["core"]}"/>'
    "</Types>"
)
DX_ROOT_RELS = DX_XML + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
    'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    "</Relationships>"
)
DX_DOC_RELS = DX_XML + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)


def _dx_text(s) -> str:
    return html.escape(str(s), quote=False)


def _dx_p(text, style: str = "Normal", ppr: str = "") -> str:
    run = f'<w:r><w:t xml:space="preserve">{_dx_text(text)}</w:t></w:r>' if text else ""
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{ppr}</w:pPr>{run}</w:p>'


def _dx_tc(val, width: int, style: str) -> str:
    """val=None 出待填空白格（金色底），str 出文本格。"""
    shd = "" if val is not None else f'<w:shd w:val="clear" w:color="auto" w:fill="{DX_FILL_BLANK}"/>'
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shd}</w:tcPr>'
            + _dx_p(val, style) + "</w:tc>")


def _dx_widths(ratios, total: int = DX_CONTENT_W) -> list[int]:
    w = [max(1, int(round(total * float(r) / sum(ratios)))) for r in ratios]
    w[-1] += total - sum(w)                 # 余数吃在末列，保证列宽和恰等于版心
    return w


def _dx_table(header, rows, ratios, bold_cols=()):
    return ("table", header, rows, tuple(ratios), tuple(bold_cols))


def _dx_kv(pairs) -> tuple:
    return _dx_table(None, [[k, v] for k, v in pairs], (16, 84), bold_cols=(0,))


def _dx_sign(case: dict) -> tuple:
    issuer = case["meta"]["issuer"]

    def slot(key: str):
        val = _s(issuer.get(key)).strip()
        return val or None

    return _dx_table(None, [["编制", slot("prepared_by"), "复核", slot("reviewed_by")],
                            ["接收", slot("received_by"), "日期", case["meta"]["issued_at"]]],
                     (12, 38, 12, 38), bold_cols=(0, 2))


def _dx_source(case: dict, digest: str, name: str) -> list:
    rev = case["meta"]["revision"]
    return [("src", f"来源：{name} ｜ sha256：{digest} ｜ {rev['version']}（{rev['note']}）"),
            ("note", "本件由装配式装修技能合集生成，供技术参考。标准规范以官方发布的现行有效版本为准；"
                     "具体设计、施工及验收由项目设计单位与具备资质的专业人员负责。")]


def _dx_standards(case: dict, external: bool) -> list:
    """标准引用核验块（docx 面）。取数与 HTML 面同源：回执＝standards_receipt、
    台账＝standards_rows、判词＝verification.results；本函数不含任何手写计数。"""
    st = case.get("standards")
    if not st:
        return []
    levels = ("FAIL",) if external else ("FAIL", "WARN")
    msgs = [f"[{lv}] {_ext_msg(msg) if external else msg}"
            for lv, msg in st["verification"]["results"] if lv in levels]
    return [("h", "标准引用核验" if external else "标准引用核验（台账随件）"),
            ("p", f"出口闸回执：{standards_receipt(st)}"),
            ("note", st["qualification"]),
            _dx_table(list(STANDARDS_HEADERS), [list(r) for r in standards_rows(st)],
                      (34, 16, 12, 14, 20)),
            ("note", "未核＝身份未核（索引外）或版本未核（引用未注年号）的台账行枚数；"
                     "计数由台账与判定明细现算，不是手写值。证据卡只补证据信息，不改判定。"),
            ("bullets", msgs or (["无红项。"] if levels == ("FAIL",) else ["无红黄项。"]))]


def _dx_body(blocks) -> str:
    out = []
    for blk in blocks:
        kind = blk[0]
        if kind == "table":
            _, header, rows, ratios, bold = blk
            widths = _dx_widths(ratios)
            if any(len(r) != len(widths) for r in rows) or (header and len(header) != len(widths)):
                raise ValueError("docx 表格列数与列宽不配")
            grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
            trs = ""
            if header:
                trs += ('<w:tr><w:trPr><w:tblHeader/></w:trPr>'
                        + "".join(_dx_tc(h, w, "dxCellH") for h, w in zip(header, widths))
                        + "</w:tr>")
            for row in rows:
                trs += ("<w:tr>" + "".join(
                    _dx_tc(v, w, "dxCellH" if i in bold and v is not None else "dxCell")
                    for i, (v, w) in enumerate(zip(row, widths))) + "</w:tr>")
            out.append(f'<w:tbl>{DX_TABLE_PR}<w:tblGrid>{grid}</w:tblGrid>{trs}</w:tbl>'
                       + _dx_p(None, "dxGap"))
        elif kind == "bullets":
            for it in blk[1]:
                out.append(_dx_p(f"— {it}", "Normal",
                                 '<w:ind w:left="340" w:hanging="340"/>'))
        elif kind == "title":
            out.append(_dx_p(blk[1], "dxTitle"))
        elif kind == "sub":
            out.append(_dx_p(blk[1], "dxSub"))
        elif kind == "h":
            out.append(_dx_p(blk[1], "dxH"))
        elif kind == "item":
            out.append(_dx_p(blk[1], "dxItem"))
        elif kind in ("note", "src"):
            out.append(_dx_p(blk[1], "dxNote" if kind == "note" else "dxSrc"))
        elif kind == "p":
            out.append(_dx_p(blk[1]))
        else:
            raise ValueError(f"未知 docx 块类型：{kind}")
    return "".join(out)


def _dx_letter_blocks(case: dict, digest: str, name: str) -> list:
    """对外答复函 → Word 件：散文段沿用 HTML 面的措辞，需贵方回复与下一步改为可填表。"""
    meta, scop = case["meta"], case["scope"]
    rev = meta["revision"]
    state = _default_state(case)
    to = _s(meta["issuer"].get("received_by")).strip()
    b: list = [("title", meta["title"]),
               ("sub", f'{meta["project"]} ｜ {meta["stage"]} ｜ {rev["version"]} ｜ {meta["issued_at"]}'),
               ("p", f"致：{to}" if to else "致：（接收方待填，见文末签署表）"),
               ("note", DX_HINT)]

    items: list = []
    for c in case["conclusions"]:
        if not _applicable(c, state):
            continue
        doubt = c.get("doubt") or {}
        tail = ""
        if c.get("review_status") == "存疑":
            scope = _s(doubt.get("scope")).strip() or "整条"
            where = "本条整体尚未确认" if scope == "整条" else f"其中{scope}尚待核实"
            tail = f"（{where}，不作为本件结论；定案条件：{doubt.get('resolve', '')}）"
        items.append(("item", c["item"] + ("（依据待核）" if c["grade"] == "D" else "")))
        items.append(("p", _ext(c["conclusion"]) + _ext(tail)))
        items.append(("src", f'适用条件：{_ext(c["conditions"])} ｜ 依据：'
                             f'{_ext(c["source"]["doc"])} {_ext(c["source"]["clause"])}'))
    if not items:
        items = [("p", "当前条件下没有可答复的结论。")]

    fixes: list = []
    for w in case.get("withdrawn", []):
        fixes.append(("note", f'原答复：「{_ext(w["conclusion"])}」'))
        fixes.append(("p", f"本件不予采用。{_ext(w['reason'])}"))

    item_of = {c["id"]: c["item"] for c in case["conclusions"]}
    asks = [g for g in case["gaps"] if g.get("external") and g.get("kind", "ask") == "ask"]
    assumed = [g for g in case["gaps"] if g.get("external") and g.get("kind") == "assume"]
    ask_rows = [[_ext(g["what"]), _ext(g["why"]), g["owner"],
                 "、".join(item_of.get(a, a) for a in g["affects"]), None] for g in asks]
    ask_block: list = ([_dx_table(["事项", "为什么要", "提供方", "影响哪条", "贵方回复"],
                                  ask_rows, (20, 24, 10, 18, 28))]
                       if asks else [("p", "无需贵方另行提供事项。")])
    if assumed:
        ask_block.append(("p", "下列事项不影响本件结论，本件已按默认推进，请在标注节点验证；"
                               "如与贵方要求不符，请回函更正。"))
        ask_block.append(("bullets", [f'{_ext(g["what"])}——按默认推进：{_ext(g.get("default", ""))}'
                                      f'；验证节点：{_ext(g.get("verify_at", ""))}' for g in assumed]))

    step_rows = [[s["action"], s["owner"], s["trigger"], None] for s in case["next_steps"]]

    sections = [("结论与依据", items)]
    if fixes:
        sections.append(("已更正的表述",
                         [("p", "原答复中的下列表述经复核不成立，本件不予采用，特此更正。"), *fixes]))
    sections += [
        ("需贵方提供或确认的事项", ask_block),
        ("下一步", [_dx_table(["动作", "责任方", "触发时点", "完成情况"], step_rows, (40, 16, 20, 24))]),
        ("本答复的边界", [("p", "；".join(_ext(x).rstrip("。") for x in scop["cannot_judge"]) + "。")]),
        ("答复失效条件", [("bullets", [_ext(x) for x in case["overturn"]])]),
    ]
    sections += [("标准引用核验", _dx_standards(case, external=True))] if case.get("standards") else []

    cond_text = _condition_line(case, state)
    cond_sentence = (f"本答复基于下列已锁定条件：{cond_text}；条件变化时结论需重新判定。"
                     if cond_text else
                     "本答复未绑定可变条件；若项目条件与本答复所述不一致，结论需重新判定。")
    b.append(("p", f"根据贵方提供的项目条件，就本项目{meta['stage']}事项，现将结论与依据答复如下。{cond_sentence}"))
    b += [("note", f"术语：{t}") for t in case.get("terminology", [])]
    for i, (title, body) in enumerate(sections):
        b.append(("h", f"{NUMERALS[i]}、{title}"))
        b += body
    b.append(("h", "签署"))
    b.append(_dx_sign(case))
    b += _dx_source(case, digest, name)
    return b


def _dx_sheet_blocks(case: dict, digest: str, name: str) -> list:
    """工作面 → Word 件：HTML 面的下拉与重算在 Word 里不存在，本面出默认条件快照＋可改取值列。"""
    if not case["conditions"]:
        raise ValueError("工作面需要至少一个可改条件；无变量答复请用 reply_letter 或 brief_card")
    meta, scop = case["meta"], case["scope"]
    rev = meta["revision"]
    state = _default_state(case)
    shown = [c for c in case["conclusions"] if _applicable(c, state)]
    b: list = [("title", meta["title"]),
               ("sub", f'工作面 · 默认条件快照 ｜ {meta["project"]} ｜ {rev["version"]} ｜ {meta["issued_at"]}'),
               ("note", DX_HINT),
               ("p", f"本件不判：{'；'.join(scop['cannot_judge'][:3])}")]

    b.append(("h", "条件"))
    b.append(_dx_table(["条件", "当前取值", "可选项", "贵方确认取值"],
                       [[c["label"],
                         next((o["label"] for o in c["options"] if o["value"] == state.get(c["id"])), ""),
                         "／".join(o["label"] for o in c["options"]), None]
                        for c in case["conditions"]], (20, 20, 36, 24)))

    b.append(("h", "当前结论"))
    pairs = _headline_pairs(case, state)
    b.append(_dx_table(["结论项", "结论", "档位"],
                       [[h["item"], h["conclusion"], f'{h["grade"]} {GRADE_LABEL[h["grade"]]}']
                        for _, h in pairs] or [["—", "当前条件组合下没有可判结论——请补充条件或转人工复核。", "—"]],
                       (22, 64, 14)))

    b.append(("h", f"结论与依据（{len(shown)} 条适用）"))
    rows = []
    for i, c in enumerate(shown, 1):
        note = ""
        if isinstance(c.get("doubt"), dict):
            note = f'\n存疑范围：{c["doubt"]["scope"]}｜定案条件：{c["doubt"]["resolve"]}'
        if _s(c.get("note")).strip():
            note += f'\n{c["note"]}'
        rows.append([str(i), c["item"] + ("〔存疑〕" if c.get("review_status") == "存疑" else ""),
                     f'适用条件：{c["conditions"]}{note}',
                     f'{c["grade"]} {GRADE_LABEL[c["grade"]]}',
                     f'{c["source"]["doc"]} {c["source"]["clause"]}'])
    b.append(_dx_table(["#", "结论项", "结论与适用条件", "档位", "依据"], rows, (5, 20, 40, 10, 25)))
    b.append(("note", "档位：A 规范明文（可核到条款）· B 可溯源报告 · C 行业通用做法或设计取值 · "
                      "D 待核（不得作规范要求）· N 不适用。档位口径单源＝各技能正文；本件只转写，不改判。"))
    if case.get("terminology"):
        b.append(("note", f"术语提示：{'；'.join(case['terminology'])}"))
    if case.get("warnings"):
        for w in case["warnings"]:
            if _applicable(w, state):
                b.append(("p", f"条件冲突：{w['text']}"))
    if case.get("withdrawn"):
        b.append(("h", "已剔除的答复条目（不构成本件结论）"))
        b.append(_dx_table(["原编号", "结论项", "原答复表述", "剔除理由"],
                           [[w["id"], w["item"], w["conclusion"], w["reason"]]
                            for w in case["withdrawn"]], (10, 22, 40, 28)))

    b.append(("h", "缺什么、谁给、影响哪条"))

    def gap_row(g: dict) -> str:
        if g.get("kind", "ask") == "assume":
            return f"按默认推进：{g.get('default', '')}（验证节点：{g.get('verify_at', '')}）"
        return f"须贵方提供（影响：{'、'.join(g['affects'])}）"

    b.append(_dx_table(["已补", "缺什么", "为什么要", "谁提供", "处理方式／影响哪条"],
                       [[None, g["what"], g["why"], g["owner"], gap_row(g)] for g in case["gaps"]],
                       (7, 25, 24, 12, 32)))
    b.append(("h", "下一步"))
    b.append(_dx_table(["#", "动作", "责任方", "触发时点", "完成情况"],
                       [[str(i), s["action"], s["owner"], s["trigger"], None]
                        for i, s in enumerate(case["next_steps"], 1)], (5, 34, 14, 19, 28)))
    b.append(("h", "什么情况下本件结论会改变"))
    b.append(("bullets", list(case["overturn"])))
    b.append(("h", "本件边界（能判／不能判）与输入溯源"))
    b.append(("p", "能判："))
    b.append(("bullets", list(scop["can_judge"])))
    b.append(("p", "不能判："))
    b.append(("bullets", list(scop["cannot_judge"])))
    b.append(("p", "输入与读取状态："))
    b.append(_dx_table(["输入", "读取状态", "说明"],
                       [[it["name"], it["status"], _s(it.get("note") or "")] for it in meta["inputs"]],
                       (44, 14, 42)))
    b += _dx_standards(case, external=False)
    b.append(("h", "签署"))
    b.append(_dx_sign(case))
    b += _dx_source(case, digest, name)
    return b


def _dx_brief_blocks(case: dict, digest: str, name: str) -> list:
    """决策面 → Word 件：只给当前结论、关键缺件、下一步（与 HTML 面同取舍，判词不上面）。"""
    meta = case["meta"]
    state = _default_state(case)
    pairs = _headline_pairs(case, state)
    rows = [[h["item"], h["conclusion"]] for _, h in pairs] or \
           [[c["item"], c["conclusion"]] for c in case["conclusions"][:4]]
    doubt_n = sum(1 for c in case["conclusions"] if c.get("review_status") == "存疑")
    b: list = [("title", meta["title"]),
               ("sub", f'决策速览 · 只给结论、缺口与动作 ｜ {meta["project"]} ｜ {meta["stage"]}'),
               ("note", DX_HINT),
               _dx_table(["结论项", "当前结论"], rows, (26, 74))]
    if not rows:
        b.append(("p", "当前条件下没有可判结论。"))
    b.append(("h", "必须先补齐"))
    b.append(("bullets", [f'{g["what"]}（{g["owner"]}）' for g in case["gaps"] if g.get("external")]
              or ["无外部缺件。"]))
    b.append(("h", "下一步"))
    b.append(("bullets", [f'{s["action"]}（{s["owner"]}，{s["trigger"]}）' for s in case["next_steps"]]
              or ["无。"]))
    if doubt_n:
        b.append(("note", f"尚待核实：{doubt_n} 条结论未定案，签署前须回工作面件看定案条件。"))
    if case.get("standards"):
        st = case["standards"]
        b.append(("note", f"出口闸回执：{standards_receipt(st)}"))
        b.append(("note", st["qualification"]))
    b.append(("h", "签署"))
    b.append(_dx_sign(case))
    b += _dx_source(case, digest, name)
    return b


def _dx_package(body: str, title: str, digest: str) -> bytes:
    parts = [
        ("[Content_Types].xml", DX_CONTENT_TYPES),
        ("_rels/.rels", DX_ROOT_RELS),
        ("word/document.xml", DX_XML + f'<w:document {DX_NS}><w:body>{body}{DX_SECTPR}</w:body></w:document>'),
        ("word/_rels/document.xml.rels", DX_DOC_RELS),
        ("word/styles.xml", DX_STYLES),
        (DOCX_FP_MEMBER, DX_XML
         + '<cp:coreProperties '
           'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
           'xmlns:dc="http://purl.org/dc/elements/1.1/">'
           + f"<dc:title>{_dx_text(title)}</dc:title>"
             f"<dc:identifier>来源件指纹 sha256：{_dx_text(digest)}</dc:identifier>"
           + "</cp:coreProperties>"),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for member, xml in parts:
            info = zipfile.ZipInfo(member, date_time=DX_ZIP_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            z.writestr(info, xml.encode("utf-8"))
    return buf.getvalue()


def render_docx(case: dict, digest: str, name: str, template: str | None = None) -> bytes:
    """prefab-deliverable/v3 → Word 件（人工可填、零第三方依赖）。三形态与 HTML 面同数据同契约。"""
    tpl = template or case["meta"].get("default_template") or "conclusion_sheet"
    if tpl == "reply_letter":
        blocks = _dx_letter_blocks(case, digest, name)
    elif tpl == "brief_card":
        blocks = _dx_brief_blocks(case, digest, name)
    else:
        blocks = _dx_sheet_blocks(case, digest, name)
    return _dx_package(_dx_body(blocks), case["meta"]["title"], digest)


def docx_member(pkg: bytes, member: str) -> str:
    """取 docx 包内某件的明文（自检与门禁的 docx 侧取数入口）。"""
    with zipfile.ZipFile(io.BytesIO(pkg)) as z:
        return z.read(member).decode("utf-8")


def docx_fingerprint_text(pkg: bytes) -> str:
    """从 docx 字节里取承载指纹的那件明文（Word 保存不重排 docProps/core.xml）。"""
    return docx_member(pkg, DOCX_FP_MEMBER)


def _dx_join_text(xml_frag: str) -> str:
    """从一段 OOXML 里取文字层。定界须排除 `<w:tbl>`／`<w:tc>` 等同前缀元素：
    只认裸 `<w:t>` 与带属性的 `<w:t xml:space=...>`。"""
    return html.unescape("".join(re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", xml_frag, re.S)))


def docx_text(pkg: bytes) -> str:
    """docx 正文文字层（逐 `<w:t>` 拼接后反转义）＝内容取数面，不含标记、也不含命名空间串。"""
    return _dx_join_text(docx_member(pkg, "word/document.xml"))


def docx_paragraphs(pkg: bytes) -> list:
    """逐段取（样式, 文字）。文字层判据需按段落过滤时用，形制与 HTML 面的 `.sub` 剥离同口径。"""
    doc = docx_member(pkg, "word/document.xml")
    out = []
    for para in re.findall(r"<w:p>.*?</w:p>", doc, re.S):
        m = re.search(r'<w:pStyle w:val="([^"]+)"/>', para)
        out.append((m.group(1) if m else "Normal", _dx_join_text(para)))
    return out


def docx_tables(pkg: bytes) -> list:
    """逐表取行、逐行取格的文字层＝docx 侧「逐格对撞」的取数入口（与 HTML 表同口径）。"""
    doc = docx_member(pkg, "word/document.xml")
    tables = []
    for tbl in re.findall(r"<w:tbl>.*?</w:tbl>", doc, re.S):
        rows = []
        for tr in re.findall(r"<w:tr>.*?</w:tr>", tbl, re.S):
            rows.append([_dx_join_text(tc)
                         for tc in re.findall(r"<w:tc>.*?</w:tc>", tr, re.S)])
        tables.append(rows)
    return tables


def render(case: dict, digest: str, name: str, template: str | None = None) -> str:
    tpl = template or case["meta"].get("default_template") or "conclusion_sheet"
    if tpl == "reply_letter":
        return render_letter(case, digest, name)
    if tpl == "brief_card":
        return render_brief(case, digest, name)
    return render_sheet(case, digest, name)


def _embedded(html_text: str) -> dict:
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html_text, re.S)
    if not m:
        raise AssertionError("未找到嵌入数据块")
    return json.loads(m.group(1))


def _extract_digest(html_text: str) -> str:
    m = re.search(r"sha256：([0-9a-f]{64})", html_text)
    return m.group(1) if m else ""


def digest_matches(html_text: str, digest: str) -> bool:
    got = _extract_digest(html_text)
    return bool(got) and got == digest


def is_docx(out_path: Path) -> bool:
    """输出面判定：后缀 .docx 走 Word 件，其余走单文件 HTML（CLI 与 verify 同判据）。"""
    return out_path.suffix.lower() == DOCX_SUFFIX


def _fingerprint_texts(out_path: Path) -> dict:
    """件内指纹读数，按面逐枚取。

    docx 有两枚须同指一源＝`docProps/core.xml` 的承载位（Word 重排不到它）＋正文来源行
    （客户看得见的那枚）。只钉承载位时，人工把正文来源行改成另一枚件的值也不判红，
    而客户对外的凭据恰恰是看得见的那枚——HTML 面全文即文本，取一枚。
    """
    if is_docx(out_path):
        pkg = out_path.read_bytes()
        return {"文档属性": docx_fingerprint_text(pkg), "正文来源行": docx_member(pkg, "word/document.xml")}
    return {"件内": out_path.read_text(encoding="utf-8")}


def verify(case_path: Path, out_path: Path) -> int:
    """陈旧件拒绝：件内指纹须等于当前 JSON 指纹，否则判为过期件。HTML 与 docx 两条面同判据。"""
    digest = hashlib.sha256(case_path.read_bytes()).hexdigest()
    try:
        faces = _fingerprint_texts(out_path)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
        print(f"未在件中找到来源指纹：{out_path}（{type(exc).__name__}：{exc}）", file=sys.stderr)
        return 1
    got = {}
    for face, text in faces.items():
        val = _extract_digest(text)
        if not val:
            print(f"未在件中找到来源指纹：{out_path}（缺「{face}」枚）", file=sys.stderr)
            return 1
        got[face] = val
    if len(set(got.values())) > 1:
        detail = " ｜ ".join(f"{face} {val[:16]}…" for face, val in got.items())
        print(f"件内指纹自相矛盾：{out_path.name} 各枚不同指一源（{detail}），须重新渲染", file=sys.stderr)
        return 1
    if digest not in set(got.values()):
        detail = " ｜ ".join(f"{face} {val[:16]}…" for face, val in got.items())
        print(f"陈旧件：{out_path.name} 件内指纹 {detail} ≠ 当前 JSON {digest[:16]}…，须重新渲染", file=sys.stderr)
        return 1
    print(f"一致：{out_path.name} 对应当前 JSON（{digest[:16]}…）")
    return 0


def _selftest(case_path: Path) -> int:
    raw = case_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    case = json.loads(raw.decode("utf-8"))
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail and not ok else ''}")
        if not ok:
            failures.append(name)

    errs = validate(case)
    check("契约校验通过", not errs, "; ".join(errs))

    outs = {}
    for tpl in TEMPLATES:
        try:
            outs[tpl] = render(case, digest, case_path.name, tpl)
        except Exception as exc:  # noqa: BLE001
            check(f"{tpl} 渲染无异常", False, repr(exc))
            continue
        check(f"{tpl} 渲染确定性（两次一致）", outs[tpl] == render(case, digest, case_path.name, tpl))
        check(f"{tpl} 无外部资源引用", "http://" not in outs[tpl] and "https://" not in outs[tpl])
        check(f"{tpl} 含签署与溯源", ("sign" in outs[tpl] or ("编制" in outs[tpl] and "复核" in outs[tpl])) and "sha256" in outs[tpl])
        try:
            pkg = render_docx(case, digest, case_path.name, tpl)
        except Exception as exc:  # noqa: BLE001
            check(f"{tpl} docx 渲染无异常", False, repr(exc))
            continue
        doc = docx_member(pkg, "word/document.xml")
        text = docx_text(pkg)
        fp = docx_fingerprint_text(pkg)
        check(f"{tpl} docx 渲染确定性（两次逐字节一致）",
              pkg == render_docx(case, digest, case_path.name, tpl))
        check(f"{tpl} docx 两处指纹（文档属性＋正文来源行）同指当前源件",
              digest_matches(fp, digest) and _extract_digest(doc) == digest)
        check(f"{tpl} docx 负向注入：承载枚不符判为陈旧件", not digest_matches(fp.replace(digest, "0" * 64), digest))
        check(f"{tpl} docx 负向注入：可见枚单指他件时两枚不成一致",
              not (digest_matches(fp, digest) and _extract_digest(doc.replace(digest, "0" * 64)) == digest))
        check(f"{tpl} docx 无外部资源引用", "http://" not in text and "https://" not in text)
        check(f"{tpl} docx 含签署与溯源", "编制" in text and "复核" in text and "sha256" in text)
        check(f"{tpl} docx 可填空：待填格带底色", f'w:fill="{DX_FILL_BLANK}"' in doc)

    sheet = outs.get("conclusion_sheet", "")
    if sheet:
        data = _embedded(sheet)
        src = {c["id"]: c for c in case["conclusions"]}
        bad = [cid for cid in src
               if next((d for d in data["conclusions"] if d["id"] == cid), None) is None
               or next(d for d in data["conclusions"] if d["id"] == cid)["conclusion"] != src[cid]["conclusion"]]
        check("工作面：结论文本逐字在件", not bad, ",".join(bad))
        state = _default_state(case)
        visible = [c["id"] for c in case["conclusions"] if _applicable(c, state)]
        check("工作面：默认态行数 = 服务端渲染行数", sheet.count('<td class="cid">') == len(visible))
        check("工作面：含交互入口", all(k in sheet for k in ('data-cond=', 'data-gap=', 'id="exportBtn"')))

    letter = outs.get("reply_letter", "")
    if letter:
        check("对外件：无内部编号", not re.search(r"\bC\d+\b", letter) and "PL-" not in letter and "IC-" not in letter and "CG-" not in letter)
        check("对外件：档位码与技能路由码被清除",
              _ext("（S1：GB 50037）由厨卫技能（BK）出节点，引 MI-R-P0-1").strip()
              == "（GB 50037）由厨卫技能出节点，引")
        check("对外件：不出现档位字母与图例", "档位" not in letter and "规范明文" not in letter)
        check("对外件：只列外部缺口", all(g["what"] in letter for g in case["gaps"] if g.get("external"))
              and all(g["what"] not in letter for g in case["gaps"] if not g.get("external")))
        check("对外件：条件锁定写明", "已锁定条件" in letter)

    brief = outs.get("brief_card", "")
    if brief:
        check("决策面：含每组当前结论", all(h["conclusion"] in brief for _, h in _headline_pairs(case, _default_state(case))))

    for tpl, text in outs.items():
        check(f"{tpl} 指纹可提取且等于源 JSON", digest_matches(text, digest))
    stale = outs.get("conclusion_sheet", "").replace(digest, "0" * 64)
    check("负向注入：指纹不符判为陈旧件", not digest_matches(stale, digest))

    bad1 = copy.deepcopy(case)
    bad1["conclusions"][0]["grade"] = "D"
    bad1["conclusions"][0]["conclusion"] = "按规范要求应采用 A 级"
    check("负向注入：D 档写规范强制语被拒", bool(validate(bad1)))

    bad2 = copy.deepcopy(case)
    bad2["scope"]["cannot_judge"] = []
    check("负向注入：缺『不能判什么』被拒", bool(validate(bad2)))

    bad3 = copy.deepcopy(case)
    bad3["gaps"][0]["affects"] = ["C99"]
    check("负向注入：缺件指向不存在结论被拒", bool(validate(bad3)))

    bad4 = copy.deepcopy(case)
    bad4["conclusions"][0]["when"] = {"nosuch": ["x"]}
    check("负向注入：条件引用未声明维度被拒", bool(validate(bad4)))

    bad5 = copy.deepcopy(case)
    bad5["overturn"] = []
    check("负向注入：缺『可推翻条件』被拒", bool(validate(bad5)))

    bad6 = copy.deepcopy(case)
    bad6["meta"]["issuer"] = {}
    check("负向注入：缺签位被拒", bool(validate(bad6)))

    bad7 = copy.deepcopy(case)
    bad7["conclusions"][0]["review_status"] = "存疑"
    bad7["conclusions"][0]["doubt"] = {"scope": "整条", "resolve": ""}
    check("负向注入：存疑缺定案条件被拒", bool(validate(bad7)))

    bad8 = copy.deepcopy(case)
    bad8["conclusions"][0]["doubt"] = {"scope": "整条", "resolve": "补齐后重判"}
    check("负向注入：非存疑条目带 doubt 被拒", bool(validate(bad8)))

    bad9 = copy.deepcopy(case)
    bad9["gaps"][0]["external"] = True
    bad9["gaps"][0]["what"] = "隔声量口径取 Rw+C 还是 Rw+Ctr"
    check("负向注入：把判定口径问回去被拒", bool(validate(bad9)))

    bad10 = copy.deepcopy(case)
    bad10["gaps"][0]["kind"] = "assume"
    check("负向注入：按默认推进缺默认值与验证节点被拒", bool(validate(bad10)))

    bad11 = copy.deepcopy(case)
    for g in bad11["gaps"]:
        g["external"] = True
        g["kind"] = "ask"
    check("负向注入：对外必答超过 3 条被拒", bool(validate(bad11)))

    rich = copy.deepcopy(case)
    shown = [c for c in rich["conclusions"] if _applicable(c, _default_state(rich))]
    rich["withdrawn"] = [{"id": rich["conclusions"][0]["id"], "item": "原结论",
                          "conclusion": "原答复中经复核不成立的表述", "reason": "复核判定不成立，依据见下。"}]
    shown[0]["review_status"] = "存疑"
    shown[0]["doubt"] = {"scope": "整条", "resolve": "补齐设计文件后重判"}
    shown[1]["review_status"] = "存疑"
    shown[1]["doubt"] = {"scope": "「该句」", "resolve": "给出同口径造价"}
    rl = render(rich, digest, "selftest.json", "reply_letter")
    check("对外件：更正引用原表述并说明理由", "原答复：「原答复中经复核不成立的表述」" in rl and "已更正的表述" in rl)
    check("对外件：整条存疑印原文但标未确认", shown[0]["conclusion"] in rl and "本条整体尚未确认" in rl)
    check("对外件：部分存疑只标该句并给定案条件", shown[1]["conclusion"] in rl
          and "其中「该句」尚待核实" in rl and "定案条件：" in rl)
    check("对外件：章节按序编号", all(f"{n}、" in rl for n in "一二三四五六"))
    sr = copy.deepcopy(case)
    sr["conclusions"][0]["review_status"] = "存疑"
    sr["conclusions"][0]["doubt"] = {"scope": "「该句」", "resolve": "补造价口径"}
    sh = render(sr, digest, "selftest.json", "conclusion_sheet")
    check("工作面：标出存疑范围与定案条件", "存疑范围：「该句」" in sh and "定案条件：补造价口径" in sh)

    asm = copy.deepcopy(case)
    asm["gaps"][0]["kind"] = "assume"
    asm["gaps"][0]["external"] = True
    asm["gaps"][0]["default"] = "按项目所在地现行评价标准推进"
    asm["gaps"][0]["verify_at"] = "施工图评审前由设计确认"
    al = render(asm, digest, "selftest.json", "reply_letter")
    check("对外件：默认推进项单列且不占必答位",
          "按默认推进" in al and asm["gaps"][0]["what"] in al
          and al.split("需贵方提供或确认的事项")[1].count("提供方：") ==
          sum(1 for g in asm["gaps"] if g.get("external") and g.get("kind", "ask") == "ask"))

    print(f"\n结果：{'全部通过' if not failures else '失败 ' + str(len(failures)) + ' 项'} ｜ 形态 {len(TEMPLATES)} 种 ｜ 结论 {len(case['conclusions'])} 条 ｜ 缺件 {len(case['gaps'])} 条")
    return 0 if not failures else 1


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="prefab-deliverable/v3 形态族交付件渲染器")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="CASE")
    group.add_argument("--render", nargs=2, metavar=("CASE", "OUT"))
    group.add_argument("--selftest", metavar="CASE")
    group.add_argument("--verify", nargs=2, metavar=("CASE", "OUT"))
    parser.add_argument("--template", choices=TEMPLATES, help="覆盖 meta.default_template")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest(Path(args.selftest))

    if args.verify:
        return verify(Path(args.verify[0]), Path(args.verify[1]))

    target = Path(args.check or args.render[0])
    raw = target.read_bytes()
    case = json.loads(raw.decode("utf-8"))
    errs = validate(case)
    if errs:
        print(f"契约校验失败（{len(errs)} 项）：", file=sys.stderr)
        for item in errs:
            print(f"  - {item}", file=sys.stderr)
        return 1
    if args.check:
        print(f"契约校验通过：{target} ｜ 形态 {case['meta'].get('default_template', 'conclusion_sheet')}")
        return 0
    out = Path(args.render[1])
    digest = hashlib.sha256(raw).hexdigest()
    face = "docx" if is_docx(out) else "html"
    payload = (render_docx(case, digest, target.name, args.template) if face == "docx"
               else render(case, digest, target.name, args.template).encode("utf-8"))
    out.write_bytes(payload)
    print(f"已渲染：{out}（形态 {args.template or case['meta'].get('default_template', 'conclusion_sheet')}"
          f" ｜ 输出面 {face} ｜ {len(payload)} B）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
