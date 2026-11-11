#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付件渲染器 v2.1：prefab-deliverable/v3 → 形态族单文件 HTML（内容不增不减）。

三种呈现形态，共用一份数据与同一契约：
  conclusion_sheet  工作面——条件可改，结论随条件重算（默认）
  reply_letter      对外件——条件锁定快照，无内部编号，带签位
  brief_card        决策面——只给当前结论、关键缺件、下一步

真实感三件落在数据里而非排版里：签位（编制/复核/接收）、版本与修订、可推翻条件。

用法（cwd＝本工具所在目录）：
  python -B deliverable_render.py --check  <case.json>
  python -B deliverable_render.py --render <case.json> <out.html> [--template X]
  python -B deliverable_render.py --selftest <case.json>
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import re
import sys
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
D_FORBIDDEN = ("规范要求", "规范规定", "标准要求", "应符合")
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
    t = re.sub(r"(?:PL|IC|CG)-\d+\s*", "", t)
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
    numerals = "一二三四五六七八九十"
    numbered = []
    for i, (title, body) in enumerate(sections):
        numbered.append(f"<h2>{numerals[i]}、{title}</h2>")
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
        "<footer>",
        f"<p>{e(meta['project'])} ｜ {e(meta['issued_at'])} ｜ {e(rev['version'])} ｜ 编制：{e(_s(meta['issuer'].get('prepared_by')).strip() or '______')}"
        f" ｜ 复核：{e(_s(meta['issuer'].get('reviewed_by')).strip() or '______')}</p>",
        f'<p class="gen">来源：{e(name)} ｜ sha256：{e(digest)}</p>',
        "</footer>",
        "</main></body></html>",
    ]
    return "\n".join(parts) + "\n"


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


def verify(case_path: Path, out_path: Path) -> int:
    """陈旧件拒绝：件内指纹须等于当前 JSON 指纹，否则判为过期件。"""
    digest = hashlib.sha256(case_path.read_bytes()).hexdigest()
    text = out_path.read_text(encoding="utf-8")
    got = _extract_digest(text)
    if not got:
        print(f"未在件中找到来源指纹：{out_path}", file=sys.stderr)
        return 1
    if got != digest:
        print(f"陈旧件：{out_path.name} 件内指纹 {got[:16]}… ≠ 当前 JSON {digest[:16]}…，须重新渲染", file=sys.stderr)
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
    out.write_bytes(render(case, hashlib.sha256(raw).hexdigest(), target.name, args.template).encode("utf-8"))
    print(f"已渲染：{out}（形态 {args.template or case['meta'].get('default_template', 'conclusion_sheet')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
