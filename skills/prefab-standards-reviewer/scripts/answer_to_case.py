#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""答复 → 交付件草稿：把技能的一次 Markdown 答复解析成 prefab-deliverable/v3。

硬约束（不编造）：
  1. 只收「带出处」的断言：标准编号或显式「行业通用做法/默认推荐」；
  2. 带条款号 → D（条款候选：条号答复自报、原文与页锚未取证）；仅标准编号、无条款 → D；
     通用做法 → C；三者皆无 → 不收录（列进待审）；A 档不自动产生，仅经人工复核取证进入；
  3. 每条结论/缺件带 _source_line，可回查原文行；
  4. 不推断项目条件；无变量答复默认出对外件，不出工作面。

用法（cwd＝本工具所在目录）：
  python -B answer_to_case.py --answer a.md --title "…" --project "…" --out draft.json
  python -B answer_to_case.py --answer a.md --conclusion-conditions cond.json …（按结论 id 注入 conditions）
  python -B answer_to_case.py --exit-gate a.md --receipt 回执.json --out 交付件.json
  python -B answer_to_case.py --review-report --standards-ledger 台账.json --findings 问题.json \
      --standards-sources 被评审文件… --out 评审件.html --receipt 回执.json
  python -B answer_to_case.py --selftest
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deliverable_render import GATE_VERDICTS  # noqa: E402
from deliverable_render import GRADES  # noqa: E402
from deliverable_render import render as render_case  # noqa: E402
from deliverable_render import validate as validate_case  # noqa: E402

import delivery_layout as L  # noqa: E402

DEV_ROOT = L.ROOT_DEFAULT
INDEX_PATH = DEV_ROOT / L.INDEX_REL
CARD_ROOT = DEV_ROOT  # 证据卡根（测试可 monkeypatch；生产与 DEV_ROOT 同）

STANDARD_RE = re.compile(
    r"(GB\s*/?\s*T?\s*\d{3,5}(?:[-—]\d{4})?|JGJ\s*/?\s*T?\s*\d{2,4}(?:[-—]\d{4})?"
    r"|DB\s*\d{2}\s*/?\s*T?\s*\d{2,4}(?:[-—]\d{4})?|ISO\s*\d+(?:[-:]\d+)?|EN\s*\d+(?:[-:]\d+)?)"
)
CLAUSE_RE = re.compile(r"第\s*[\d.]+\s*条|表\s*[\d.]+|附录\s*[A-Z]")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")
BOLD_IN_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\*\*(.+?)\*\*\s*[：:]\s*(.+?)\s*$")
BOLD_LINE = re.compile(r"^\s*\*\*(.+?)\*\*\s*[：:]\s*(.+?)\s*$")
BOLD_HEAD_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*[：:]?\s*$")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
H3_RE = re.compile(r"^###\s+(.+?)\s*$")
GAP_HEAD_RE = re.compile(r"还需要确认|请补|需要补充|需补充|最小输入集|待确认|请提供")
OVERTURN_HEAD_RE = re.compile(r"翻选条件|失效条件|推翻|不再适用")
CANNOT_RE = re.compile(r"不给|不输出|不能判|不归|不作结果引用|暂停期")
GENERIC_RE = re.compile(r"通用做法|行业通用|行业主流|默认推荐|经验值|通常|惯例")
CONCLUSION_LABEL_RE = re.compile(r"结论|推荐|判定|要点")
PROC_HEAD_RE = re.compile(r"做法|方案|路线|工法|构造")
CANNOT_VALUE_RE = re.compile(r"不给|不能作为|不得作为|不能凭|不作|不能判")
TABLE_ROW_RE = re.compile(r"^\s*\|")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "")).strip().strip("`").strip()


def _sentence(line: str) -> str:
    line = _clean(line)
    m = re.match(r"(.+?。)", line)
    return m.group(1) if m else line


def parse(text: str) -> tuple[dict, list[str]]:
    """返回 (case, review)。case 未填 meta，由调用方补齐。"""
    conclusions: list[dict] = []
    gaps: list[dict] = []
    overturn: list[str] = []
    cannot: list[str] = []
    headings: list[str] = []
    unparsed: list[str] = []
    table_rows = 0
    cur_h2 = cur_h3 = cur_proc = ""
    in_gaps = in_overturn = False

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith(">"):
            continue
        if TABLE_ROW_RE.match(line):
            table_rows += 1
            continue

        m = H2_RE.match(line)
        if m:
            cur_h2, cur_h3 = m.group(1).strip(), ""
            cur_proc = ""
            in_gaps = bool(GAP_HEAD_RE.search(cur_h2))
            in_overturn = bool(OVERTURN_HEAD_RE.search(cur_h2))
            if not in_gaps and not in_overturn:
                headings.append(cur_h2)
            continue
        m = H3_RE.match(line)
        if m:
            title = m.group(1).strip()
            cur_h3 = title
            cur_proc = re.split(r"[：:]", title)[0].strip() if PROC_HEAD_RE.search(title) else ""
            in_gaps = in_overturn = False
            continue
        m = BOLD_HEAD_RE.match(line)
        if m:
            label = _clean(m.group(1))
            in_gaps = bool(GAP_HEAD_RE.search(label))
            in_overturn = bool(OVERTURN_HEAD_RE.search(label))
            continue

        if in_gaps:
            m = BULLET_RE.match(line)
            if m:
                item = _clean(m.group(1))
                if len(item) >= 4:
                    gaps.append({"_source_line": lineno, "what": item, "why": "答复要求补充此项输入", "owner": "待指定", "external": True})
            continue
        if in_overturn:
            m = BULLET_RE.match(line)
            if m:
                item = _clean(m.group(1))
                if len(item) >= 4:
                    overturn.append(item)
            continue

        if CANNOT_RE.search(line):
            s = _sentence(line.lstrip("-* ").strip())
            if s and s not in cannot:
                cannot.append(s)

        m = BOLD_IN_BULLET.match(line) or BOLD_LINE.match(line)
        if not m:
            continue
        label, value = _clean(m.group(1)), _clean(m.group(2))
        if len(label) < 2 or len(value) < 4:
            continue
        if GAP_HEAD_RE.search(label):
            gaps.append({"_source_line": lineno, "what": value, "why": "答复要求补充此项输入", "owner": "待指定", "external": True})
            continue
        if OVERTURN_HEAD_RE.search(label):
            overturn.append(value)
            continue
        if "？" in value or "?" in value:
            gaps.append({"_source_line": lineno, "what": value, "why": "答复以问句形式索取输入", "owner": "待指定", "external": True})
            continue

        standards = STANDARD_RE.findall(value)
        has_clause = bool(CLAUSE_RE.search(value))
        if standards and has_clause:
            # F02（CG-20261117-003）：语法提取只是引用候选——条号来自答复自报，
            # 条文原文、载体身份与页锚均未取证，不得自动升 A；A 档仅经人工复核取证进入。
            grade, doc = "D", "；".join(standards)
            clause = CLAUSE_RE.search(value).group(0) + "（答复自报，原文与页锚未取证）"
            note = "自动定档：条款号取自答复自报，条文原文与页锚未取证，条款核前不得作规范引用，待人工确认。"
        elif standards:
            grade, doc, clause = "D", "；".join(standards), "条款未核（答复仅给标准编号）"
            note = "自动定档：仅有标准编号、未给条款，条款核前不得作规范引用。"
        elif GENERIC_RE.search(value) or GENERIC_RE.search(label):
            grade, doc, clause = "C", "答复中的行业通用做法", "非规范规定值"
            note = ""
        elif CONCLUSION_LABEL_RE.search(label) and CANNOT_VALUE_RE.search(value):
            grade, doc, clause = "N", "不适用（本答复声明不作该判断）", "—"
            note = "自动定档：本条为「不判」声明。"
        elif CONCLUSION_LABEL_RE.search(label) or PROC_HEAD_RE.search(cur_h3 or cur_h2):
            grade, doc, clause = "C", "行业通用做法（答复整理）", "非规范规定值"
            note = "自动定档：答复未给规范出处，按行业通用做法／设计取值列 C，待人工确认。"
        else:
            unparsed.append(f"L{lineno} {label}：{value}")
            continue

        conclusions.append({
            "_source_line": lineno,
            "id": f"C{len(conclusions) + 1}",
            "item": f"{cur_proc}·{label}" if cur_proc else label,
            "conclusion": value,
            "grade": grade,
            "source": {"doc": doc, "clause": clause},
            "conditions": f"按答复「{cur_h3 or cur_h2 or '全文'}」节所述前提。",
            "note": note,
        })

    ids = [c["id"] for c in conclusions]
    for g in gaps:
        g["affects"] = ids

    if not overturn:
        overturn = ["若上述任何前提条件变化，本结论需重判。"]

    review: list[str] = []
    if not conclusions:
        review.append("未提取到任何带出处的结论——该答复可能全部为无条件断言")
    d_count = sum(1 for c in conclusions if c["grade"] == "D")
    if d_count:
        review.append(f"D 档 {d_count} 条：条款未取证（仅标准编号或条号答复自报），须人工确认")
    c_count = sum(1 for c in conclusions if c["grade"] == "C")
    if c_count:
        review.append(f"C 档 {c_count} 条：自动定档（行业通用做法／设计取值），须人工确认")
    if unparsed:
        review.append(f"未收录断言 {len(unparsed)} 条（无标准编号、无通用做法标记）")
    if table_rows:
        review.append(f"表格 {table_rows} 行未自动转结论（内容可能已由正文条目覆盖）")
    if gaps:
        review.append(f"缺件 {len(gaps)} 条：责任方均填「待指定」，须人工指派")
    if not cannot:
        review.append("未识别到「不可判」声明——已用默认边界句，须人工确认")

    case = {
        "schema": "prefab-deliverable/v3",
        "meta": {},
        "conditions": [],
        "scope": {
            "can_judge": [f"关于：{h}" for h in headings[:4]] or ["本次答复列明的结论"],
            "cannot_judge": cannot[:4] or ["本答复仅覆盖上述已列结论；同类但条件不同的情形不在本答复范围内。"],
        },
        "conclusions": conclusions,
        "warnings": [],
        "gaps": gaps or [{
            "_source_line": 0,
            "what": "答复未列出待补输入",
            "why": "需人工确认是否确无缺口",
            "owner": "待指定",
            "external": False,
            "affects": ids,
        }],
        "overturn": overturn,
        "next_steps": [{
            "action": "按缺件清单补齐输入后重判",
            "owner": "待指定",
            "trigger": "输入到位时",
        }],
    }
    return case, review + unparsed


def build(text: str, *, title: str, project: str, stage: str, date: str,
          source_name: str, template: str, conditions: list[dict] | None,
          cond_map: dict[str, str] | None = None) -> tuple[dict, list[str]]:
    case, review = parse(text)
    if cond_map:
        known = {c["id"] for c in case["conclusions"]}
        unknown = set(cond_map) - known
        if unknown:
            raise SystemExit(f"--conclusion-conditions 含未知结论 id：{sorted(unknown)}")
        for c in case["conclusions"]:
            if c["id"] in cond_map:
                c["conditions"] = cond_map[c["id"]]
    case["meta"] = {
        "title": title,
        "project": project,
        "stage": stage,
        "issued_at": date,
        "compiled_by": f"answer_to_case.py ← {source_name}",
        "default_template": template,
        "issuer": {"prepared_by": "", "reviewed_by": "", "received_by": ""},
        "revision": {"version": "v0.1", "date": date, "note": "自动提取草稿"},
        "inputs": [{"name": source_name, "status": "已读", "note": f"解析自答复全文（{len(text.splitlines())} 行）"}],
    }
    case["conditions"] = conditions or []
    if not case["conditions"] and template == "conclusion_sheet":
        review.append("工作面需要可改条件：本次未提供 --conditions，建议改用 reply_letter 或 brief_card")
    errs = validate_case(case)
    if errs:
        review.append("契约未通过：" + "；".join(errs[:5]))
    return case, review


SHEET_CSS = """
:root{--ink:#1a1a1a;--muted:#5b6470;--line:#d8dde3;--soft:#f6f8fa;--accent:#1f4e79}
*{box-sizing:border-box}
body{margin:0;background:#eef1f4;color:var(--ink);font:13px/1.6 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}
.page{max-width:1400px;margin:0 auto;background:#fff;padding:28px 32px 40px}
h1{font-size:19px;margin:0 0 6px}
.sub{color:var(--muted);font-size:12.5px;margin:0 0 16px}
.bar{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:10px 0;display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:2}
.bar b{color:var(--accent)}
button{min-height:36px;padding:6px 14px;border:1px solid #9aa4b0;background:#fff;border-radius:5px;font:inherit;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
table{width:100%;border-collapse:collapse;margin-top:12px}
th,td{border:1px solid var(--line);padding:7px 9px;vertical-align:top;text-align:left}
th{background:var(--soft);position:sticky;top:52px;font-weight:600;white-space:nowrap;z-index:1}
td.q{white-space:nowrap;color:var(--muted)}
td.txt{max-width:520px}
.tags{font-size:11px;color:var(--muted);margin-top:3px}
.badge{display:inline-block;min-width:20px;text-align:center;padding:0 6px;border-radius:3px;color:#fff;font-weight:600}
.g-A{background:#2e7d32}.g-B{background:#1565c0}.g-C{background:#6d4c41}.g-D{background:#b3261e}.g-N{background:#6b7280}
select,input,textarea{font:inherit;border:1px solid #9aa4b0;border-radius:4px;padding:4px 6px;background:#fff;min-width:0}
select{width:78px}
input,textarea{width:100%}
textarea{min-height:64px;resize:vertical}
tr.done{background:#f2f8f3}
@media print{.bar{position:static}.page{padding:0}button{display:none}}
"""

SHEET_JS = r"""
(function(){
  function scriptJson(value){
    return JSON.stringify(value).replace(/</g,'\\u003c').replace(/>/g,'\\u003e').replace(/&/g,'\\u0026')
      .replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029');
  }
  var sels=[].slice.call(document.querySelectorAll('select[data-grade]'));
  function sync(){
    var done=sels.filter(function(s){return s.value;}).length;
    document.getElementById('done').textContent=String(done);
    sels.forEach(function(s){s.closest('tr').classList.toggle('done', !!s.value);});
  }
  sels.forEach(function(s){s.addEventListener('change',sync);});
  document.querySelectorAll('[data-note]').forEach(function(i){i.addEventListener('input',sync);});
  function collect(includeEmpty){
    var items=sels.map(function(s){
      var tr=s.closest('tr');
      return {q:tr.getAttribute('data-q'), id:tr.getAttribute('data-id'), grade:s.value,
              note:(tr.querySelector('[data-note]')||{}).value||'',
              status:(tr.querySelector('select[data-status]')||{}).value||'',
              dscope:(tr.querySelector('input[data-dscope]')||{}).value||'',
              dresolve:(tr.querySelector('input[data-dresolve]')||{}).value||''};
    });
    if(!includeEmpty) items=items.filter(function(x){return x.grade||x.note||x.status||x.dscope||x.dresolve;});
    var gaps=[].slice.call(document.querySelectorAll('tr[data-gi]')).map(function(tr){
      return {q:tr.getAttribute('data-q'), gi:parseInt(tr.getAttribute('data-gi'),10),
              kind:(tr.querySelector('select[data-kind]')||{}).value||'ask',
              default:(tr.querySelector('input[data-default]')||{}).value||'',
              verify_at:(tr.querySelector('input[data-verify]')||{}).value||'',
              owner:(tr.querySelector('input[data-owner]')||{}).value||''};
    });
    return {items:items, gaps:gaps};
  }
  function own(o,k){ return Object.prototype.hasOwnProperty.call(o,k); }
  function restore(control,item,key){ if(control&&own(item,key)) control.value=item[key]==null?'':item[key]; }
  document.getElementById('expJson').addEventListener('click',function(){
    var blob=new Blob([JSON.stringify(collect(false),null,2)],{type:'application/json'});
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);
    a.download='grade_review.json';document.body.appendChild(a);a.click();a.remove();
    setTimeout(function(){URL.revokeObjectURL(a.href);},1500);
  });
  document.getElementById('expHtml').addEventListener('click',function(){
    var st=document.getElementById('saved-state');
    st.textContent=scriptJson(collect(true));
    var doc='<!DOCTYPE html>\n'+document.documentElement.outerHTML;
    var blob=new Blob([doc],{type:'text/html;charset=utf-8'});
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);
    a.download='档位复核表_已填.html';document.body.appendChild(a);a.click();a.remove();
    setTimeout(function(){URL.revokeObjectURL(a.href);},1500);
  });
  var saved=document.getElementById('saved-state');
  if(saved && saved.textContent.trim() && saved.textContent.trim()!=='null'){
    try{
      var st=JSON.parse(saved.textContent);
      var itemRows=[].slice.call(document.querySelectorAll('tr[data-q][data-id]'));
      (st.items||[]).forEach(function(it){
        var tr=itemRows.filter(function(r){return r.getAttribute('data-q')===it.q&&r.getAttribute('data-id')===it.id;})[0];
        if(!tr) return;
        restore(tr.querySelector('select[data-grade]'),it,'grade');
        restore(tr.querySelector('[data-note]'),it,'note');
        restore(tr.querySelector('select[data-status]'),it,'status');
        restore(tr.querySelector('input[data-dscope]'),it,'dscope');
        restore(tr.querySelector('input[data-dresolve]'),it,'dresolve');
      });
      var gapRows=[].slice.call(document.querySelectorAll('tr[data-q][data-gi]'));
      (st.gaps||[]).forEach(function(g){
        var tr=gapRows.filter(function(r){return r.getAttribute('data-q')===g.q&&String(r.getAttribute('data-gi'))===String(g.gi);})[0];
        if(!tr) return;
        restore(tr.querySelector('select[data-kind]'),g,'kind');
        restore(tr.querySelector('input[data-default]'),g,'default');
        restore(tr.querySelector('input[data-verify]'),g,'verify_at');
        restore(tr.querySelector('input[data-owner]'),g,'owner');
      });
    }catch(e){}
  }
  sync();
})();
"""


def render_review_sheet(pairs: list[tuple[str, dict]]) -> str:
    e = html.escape
    rows = []
    gap_rows = []
    for q, case in pairs:
        for c in case["conclusions"]:
            opts = "".join(f'<option value="{g}">{g}</option>' for g in ("A", "B", "C", "D", "N"))
            d = c.get("doubt") or {}
            st = c.get("review_status", "")
            rows.append(
                f'<tr data-q="{e(q)}" data-id="{e(c["id"])}">'
                f'<td class="q">{e(q)}</td><td class="q">{e(c["id"])}</td>'
                f'<td>{e(c["item"])}</td>'
                f'<td class="txt">{e(c["conclusion"])}'
                f'<div class="tags">依据：{e(c["source"]["doc"])}｜{e(c["source"]["clause"])}'
                f'　原文 L{e(str(c.get("_source_line", 0)))}</div></td>'
                f'<td><span class="badge g-{e(c["grade"])}">{e(c["grade"])}</span></td>'
                f'<td><select data-grade><option value="">—</option>{opts}</select></td>'
                f'<td><textarea data-note placeholder="理由（可选）"></textarea></td>'
                f'<td><select data-status><option value="">不改</option>'
                f'<option value="存疑"{" selected" if st == "存疑" else ""}>存疑</option>'
                f'<option value="清除"{" selected" if st and st != "存疑" else ""}>清除</option></select></td>'
                f'<td><input data-dscope placeholder="存疑范围" value="{e(d.get("scope", ""))}"></td>'
                f'<td><input data-dresolve placeholder="定案条件" value="{e(d.get("resolve", ""))}"></td>'
                "</tr>"
            )
        for gi, g in enumerate(case["gaps"]):
            gap_rows.append(
                f'<tr data-q="{e(q)}" data-gi="{gi}">'
                f'<td class="q">{e(q)}</td><td class="txt">{e(g["what"])}</td>'
                f'<td><select data-kind><option value="ask"{" selected" if g.get("kind", "ask") == "ask" else ""}>必答</option>'
                f'<option value="assume"{" selected" if g.get("kind") == "assume" else ""}>默认推进</option></select></td>'
                f'<td><input data-default placeholder="默认做法" value="{e(g.get("default", ""))}"></td>'
                f'<td><input data-verify placeholder="验证节点" value="{e(g.get("verify_at", ""))}"></td>'
                f'<td><input data-owner placeholder="提供方" value="{e(g.get("owner", ""))}"></td>'
                "</tr>"
            )
    total = len(rows)
    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>档位复核表</title>",
        f"<style>{SHEET_CSS}</style>",
        "</head><body><main class=\"page\">",
        "<h1>档位复核表</h1>",
        f'<p class="sub">共 {total} 条结论。确认档位：A 规范明文（须能核到条款）／B 可溯源报告／C 通用做法或设计取值／D 待核（不得作规范要求）／N 不适用。'
        "存疑须填范围与定案条件。下方缺口表分档：必答（≤3，须挂结论）／默认推进（须填默认做法与验证节点）。现值仅作起点。</p>",
        '<div class="bar"><span>已复核 <b id="done">0</b> / ' + str(total) + '</span>'
        '<button class="primary" id="expJson" type="button">导出复核结果 JSON</button>'
        '<button id="expHtml" type="button">存为已填 HTML</button></div>',
        "<table><thead><tr><th>题</th><th>#</th><th>结论项</th><th>结论与依据</th><th>现</th><th>确认</th><th>备注</th>"
        "<th>存疑</th><th>存疑范围</th><th>定案条件</th></tr></thead><tbody>",
        "".join(rows),
        "</tbody></table>",
        "<h2>缺口分档（必答≤3，其余默认推进）</h2>",
        "<table><thead><tr><th>题</th><th>缺什么</th><th>方式</th><th>默认做法</th><th>验证节点</th><th>提供方</th></tr></thead><tbody>",
        "".join(gap_rows),
        "</tbody></table>",
        "</main>",
        '<script id="saved-state" type="application/json">null</script>',
        f"<script>{SHEET_JS}</script>",
        "</body></html>",
    ]) + "\n"


def _selftest() -> int:
    sample = """## 可选做法

### 做法一：龙骨调平

- **燃烧性能**：饰面板须达 A 级（GB 50222-2017 第 4.0.4 条）。
- **基材等级**：硅酸钙板本身为 A 级（GB 8624-2012）。
- **做法定位**：酒店走道默认推荐做法，属行业通用做法。
- **总占用厚度**：约 60–140mm（含饰面板）。

## 使用体验

- **使用手感**：这个做法更好用。

## 结论与边界

**先给结论**：本答复不给 dB 数值。

**翻选条件**：

- 走道净宽紧张时改干挂。
- 有频繁检修需求时改干挂。

## 还需要确认的信息

1. 项目所在地。
2. 基层实测平整度。
"""
    failures = []
    case, review = build(
        sample, title="测试", project="测试项目", stage="技术咨询",
        date="2026-11-09", source_name="selftest.md",
        template="reply_letter", conditions=None,
    )

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail and not ok else ''}")
        if not ok:
            failures.append(name)

    grades = [c["grade"] for c in case["conclusions"]]
    check("五条断言入结论", len(case["conclusions"]) == 5, str(grades))
    check("档位规则：条款→A／仅标准号→D／通用做法与构造项→C／不判声明→N", grades == ["D", "D", "C", "C", "N"], str(grades))
    # ── F02（审计 2026-11-17，E05）：编号＋条号不得无证据自动升 A ──
    c1 = case["conclusions"][0]
    check("T06 编号＋条号不自动升 A（条款候选须待核）", c1["grade"] == "D", c1["grade"])
    check("T06 注记不自报已取证", "已取证" not in c1.get("note", ""), c1.get("note", ""))
    quoted, _ = build("- **燃烧性能**：按 GB 99999-2020 第 999.9 条，「测试条文逐字。」\n",
                      title="F02", project="隔离测试", stage="咨询", date="2026-11-17",
                      source_name="f02.md", template="reply_letter", conditions=None)
    q1 = quoted["conclusions"][0]
    check("T07 答复含逐字但缺载体身份与页锚仍不升 A", q1["grade"] == "D", q1["grade"])
    hand = json.loads(json.dumps(case))
    hand["conclusions"][0]["grade"] = "A"
    hand["conclusions"][0]["note"] = "自动定档：条款承载已取证（条款号＋逐字＋页锚），待人工确认。"
    check("T08 契约拒绝自动定档的 A 档", bool(validate_case(hand)))
    hand["conclusions"][0]["note"] = ""
    check("T08 人工复核确认的 A 档契约仍放行", not validate_case(hand),
          "；".join(validate_case(hand)[:3]))
    kept_f02 = merge_review_note("自动定档：条款号取自答复自报，条文原文与页锚未取证，待人工确认。", False, "已核条款")
    check("T08 复核不改档保留待核判据", kept_f02.startswith("定档依据：") and "未取证" in kept_f02, kept_f02)
    check("无条件断言不入结论", all("更好用" not in c["conclusion"] for c in case["conclusions"]))
    check("未收录断言进待审", any("未收录断言 1 条" in r for r in review if isinstance(r, str)))

    src_lines = {c["_source_line"] for c in case["conclusions"]}
    lines = sample.splitlines()
    check("每条结论可回查原文行", all(1 <= n <= len(lines) and case["conclusions"][i]["conclusion"] in _clean(lines[n - 1])
                                  for i, n in enumerate(sorted(src_lines))))

    body = "\n".join(lines)
    check("不编造：结论文本均为原文子串", all(c["conclusion"] in body for c in case["conclusions"]))
    check("缺件取自『还需要确认的信息』", len(case["gaps"]) == 2, str(len(case["gaps"])))
    check("缺件指向存在的结论", all(ref in {c["id"] for c in case["conclusions"]} for g in case["gaps"] for ref in g["affects"]))
    check("失效条件取自『翻选条件』", len(case["overturn"]) == 2)
    check("契约校验通过", not validate_case(case), "；".join(validate_case(case)[:3]))
    check("对外件可渲染", "sha256" in render_case(case, "0" * 64, "selftest.json", "reply_letter"))
    try:
        render_case(case, "0" * 64, "selftest.json", "conclusion_sheet")
        check("无变量时工作面拒绝渲染", False)
    except ValueError:
        check("无变量时工作面拒绝渲染", True)

    kept = merge_review_note("自动定档：仅有标准编号、未给条款，条款核前不得作规范引用。", False, "我不确定")
    check("复核不改档时保留自动判据", kept == "定档依据：仅有标准编号、未给条款，条款核前不得作规范引用。 ｜ 复核：我不确定", kept)
    moved = merge_review_note("自动定档：答复未给规范出处，按行业通用做法／设计取值列 C，待人工确认。", True, "")
    check("复核改档时作废原档位理由", moved == "", moved)

    print(f"\n结果：{'全部通过' if not failures else '失败 ' + str(len(failures)) + ' 项'}")
    return 0 if not failures else 1


def merge_review_note(prev: str, grade_changed: bool, review_note: str) -> str:
    """复核后只摘掉「待人工确认」这层时态，不连带删掉自动定档给出的判据。"""
    note = prev or ""
    if note.startswith("自动定档："):
        parts = [x.strip() for x in note[len("自动定档："):].split("。") if x.strip()]
        if grade_changed:
            parts = [x for x in parts if "待人工确认" not in x and "列 " not in x]
        else:
            parts = [x.replace("，待人工确认", "") for x in parts]
        note = ("定档依据：" + "。".join(parts) + "。") if parts else ""
    if review_note:
        note = f"{note} ｜ 复核：{review_note}".strip(" ｜")
    return note


def _card_desc(r: dict) -> str:
    c = r.get("card") or {}
    level = c.get("citable_level") or "未标级"
    cache = c.get("cache") or {}
    nc = c.get("not_covered") or []
    tail = f"；未覆盖：{'；'.join(nc[:2])}" if nc else ""
    return f"{level}；verified_on {cache.get('verified_on')}；到期 {cache.get('expires_at')}{tail}"


def _standards_enrichment(rows: list[dict], index_text: str, *, fetch_missing: bool = False,
                          fetch_budget: float = 30.0, fetch_total: float = 120.0):
    """对台账**逐行**查证据卡（索引内行也查）；只读缓存，判定不变。

    返回 `(stats, lines, states)`。`states` 逐行给 `{key, indexed, state}`，供回执随件落盘。
    索引内行只报状态、不进 `--fetch-missing` 取真面（取真射程仍是索引外未声明行）。

    动因（CG-20261112-002 §4.2 实测）：原实现对索引内行 `continue` 不查卡，而全仓唯一那枚卡
    恰在索引内 ⇒ 卡消费次数结构性为 0。本函数只补提示层读数，不新增判定轴。
    """
    from citation_ledger import parse_index
    import on_demand_retrieval as O

    index = parse_index(index_text)
    stats = {"indexed": 0, "cached": 0, "stale": 0, "missing": 0, "declared": 0,
             "indexed_cached": 0, "indexed_stale": 0, "indexed_missing": 0}
    lines: list[tuple[str, str]] = []
    states: list[dict] = []
    state_of: dict[str, dict] = {}
    fetchable: list[int] = []

    def _expired(r: dict) -> str:
        exp = ((r.get("card") or {}).get("cache") or {}).get("expires_at")
        return f"证据卡已过期（到期 {exp}），可 retrieve --refresh"

    for row in rows:
        key = row.get("key")
        if not key:
            continue
        indexed = key in index
        if not indexed and (row.get("mention") or row.get("external") or row.get("ack")):
            stats["declared"] += 1
            st = {"key": key, "indexed": False, "state": "declared"}
            states.append(st)
            state_of[key] = st
            continue
        r = O.lookup_card(CARD_ROOT, key)
        state = r["state"]
        st = {"key": key, "indexed": indexed, "state": state}
        states.append(st)
        state_of[key] = st
        bucket = {"valid": "cached", "stale": "stale"}.get(state, "missing")
        if indexed:
            stats["indexed"] += 1
            stats["indexed_" + bucket] += 1
            if state == "valid":
                lines.append((key, f"证据卡 {_card_desc(r)}"))
            elif state == "stale":
                lines.append((key, _expired(r)))
            continue
        stats[bucket] += 1
        if state == "valid":
            lines.append((key, f"证据卡 {_card_desc(r)}"))
        elif state == "stale":
            fetchable.append(len(lines))
            lines.append((key, _expired(r)))
        else:
            fetchable.append(len(lines))
            lines.append((key, "无有效证据卡（可加 --fetch-missing 取真）"))

    if fetch_missing:
        import subprocess
        import time
        started = time.monotonic()
        engine = Path(__file__).with_name("on_demand_retrieval.py")
        for i in fetchable:
            key, desc = lines[i]
            left = fetch_total - (time.monotonic() - started)
            if left <= 5:
                lines[i] = (key, f"{desc}（总预算耗尽，未取真）")
                continue
            cmd = [sys.executable, "-B", str(engine), "--root", str(CARD_ROOT),
                   "retrieve", key, "--budget", str(fetch_budget), "--total-budget", str(left)]
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            r2 = O.lookup_card(CARD_ROOT, key)
            if r2["state"] == "valid":
                lines[i] = (key, f"取真成功；证据卡 {_card_desc(r2)}")
                prev = state_of[key]["state"]
                stats[{"valid": "cached", "stale": "stale"}.get(prev, "missing")] -= 1
                stats["cached"] += 1
                state_of[key]["state"] = "valid"
            else:
                lines[i] = (key, f"取真未达有效证据卡（exit {p.returncode}）")
    return stats, lines, states


def audit_standards(led: dict, index_text: str, *, sources: dict | None = None,
                    strict: bool = False, fetch_missing: bool = False,
                    fetch_budget: float = 30.0, fetch_total: float = 120.0,
                    tool: str = "answer_to_case.py --exit-gate") -> dict:
    """四轴核验＋逐行卡补证 → 随件回执块（交付件 `standards` 字段）。

    判定全部由 `citation_ledger.verify()` 承担，本函数不改判、不新增判定轴；
    计数一律由判定明细与台账现算，不接受手写字面（契约由 `validate_standards` 承担）。
    `mode`：给了源文件＝review（评审类，须双向覆盖），否则＝qa（技术答疑类）。
    """
    from citation_ledger import parse_index, verify
    from deliverable_render import REVIEW_QUALIFICATION

    results = verify(led, index_text, sources, tolerate_unindexed=not strict)
    stats, lines, states = _standards_enrichment(
        led.get("rows") or [], index_text, fetch_missing=fetch_missing,
        fetch_budget=fetch_budget, fetch_total=fetch_total)
    indexed_of = {s["key"]: s["indexed"] for s in states}
    rows = led.get("rows") or []
    red = sum(1 for lv, _ in results if lv == "FAIL")
    return {
        "ledger": led,
        "verification": {
            "mode": "review" if sources is not None else "qa",
            "strict": bool(strict),
            "index_keys": sorted(parse_index(index_text)),
            "red": red,
            "yellow": sum(1 for lv, _ in results if lv == "WARN"),
            "green": sum(1 for lv, _ in results if lv == "OK"),
            "unverified": sum(1 for r in rows
                              if not (r.get("cited_years") or []) or not indexed_of.get(r["key"])),
            "unindexed_undeclared": sum(1 for s in states if not s["indexed"] and s["state"] != "declared"),
            "results": [[lv, m] for lv, m in results],
        },
        "cards": dict(stats, states=states, lines=[[k, d] for k, d in lines]),
        "qualification": REVIEW_QUALIFICATION,
        "gate": {
            "verdict": "FAIL" if red else "PASS",
            "tool": tool,
            "ran_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


def _print_cards(st: dict) -> None:
    c = st["cards"]
    print(f"标准引用补证：索引内 {c['indexed']}（卡有效 {c['indexed_cached']}／过期 {c['indexed_stale']}"
          f"／无卡 {c['indexed_missing']}）｜索引外未声明 有效 {c['cached']}｜过期 {c['stale']}"
          f"｜无卡 {c['missing']}｜已声明外源 {c['declared']}")
    for key, desc in c["lines"]:
        print(f"  - {key}：{desc}")


def _stamp_contract(st: dict) -> None:
    """回执随件带「随件块契约」判定（第三轮 N26）：gate 只反映引用核验，块自身契约须同件可读。"""
    from deliverable_render import validate_standards

    errs = validate_standards(st)
    st["standards_contract"] = {"verdict": "FAIL" if errs else "PASS", "errors": errs}


def _write_receipt(args, st: dict | None, case: dict | None = None) -> bool:
    """回执随件落盘：`--receipt` 写回执 JSON，`--out` 写带 standards 的交付件本体（不只在 stdout）。

    返回 False＝请求了本体但被拒写（红件或合并后契约不过），调用方须据此判红，不得照印 PASS。
    本体落盘前重跑 `validate_case`：主流程只校了不含 standards 的 case，合并块须另校。
    零引用态（审计 F01/T05）：本体不带随件块（空 rows 不得冒充已核验），以本轮实际状态
    覆盖旧件；回执显式记录零引用态。
    """
    if st is None:
        return True
    _stamp_contract(st)
    if args.receipt:
        p = Path(args.receipt)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((json.dumps(st, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        print(f"出口闸回执：{p}")
    if args.out and case is not None:
        if st["gate"]["verdict"] != "PASS":
            print("交付件本体未落盘：出口闸 FAIL——红件不得以交付件本体形式落盘（回执仍留作判定证据）",
                  file=sys.stderr)
            return False
        if st.get("zero_citation"):
            body, note = dict(case, meta=dict(case["meta"])), "零引用、无随件块"
            body.pop("standards", None)
            revision = dict(body["meta"]["revision"])
            revision["note"] = "；".join(filter(None, (revision.get("note"),
                                                     "本轮零引用，未跑引用核验，不背书旧件")))
            body["meta"]["revision"] = revision
        else:
            body, note = dict(case, standards=st), "台账随件"
        errs = validate_case(body)
        if errs:
            print(f"交付件本体未落盘：契约 FAIL（{len(errs)} 项）——{errs[0]}", file=sys.stderr)
            return False
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((json.dumps(body, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        print(f"交付件本体（{note}）：{p}")
        _revoke_stale_gate_companion(p, "answer_to_case.py --exit-gate", "出口闸",
                                     Path(args.receipt) if args.receipt else None)
    return True


_ANSWER_BINARY_MAGIC = (
    (b"PK\x03\x04", "ZIP 容器件（DOCX／XLSX／PPTX）"),
    (b"%PDF", "PDF 件"),
    (b"\xd0\xcf\x11\xe0", "OLE 复合文档件（旧版 DOC／XLS／PPT）"),
)
# HTML 文档只在**文档根**判定（doctype 或首 element 即 <html>）：Markdown 正文合法内嵌
# HTML 标签，按「含标签」判定会把第 12 轮的 fail-open 守卫样本整批误拒（假红退化）。
_HTML_DOC_RE = re.compile(r"(?:<!doctype\s+html\b|<html[\s>])", re.IGNORECASE)


def _read_answer_text(path: Path) -> tuple[str | None, str | None]:
    """答复件读取与格式闸（P49）：返回 (Markdown 文本, 拒判理由)，两者恰有一枚非 None。

    出口闸的答复面只有 Markdown 一种口径（`build` 按 Markdown 结构解析）；HTML／DOCX 属
    **来源材料**解析面，经 `--standards-sources` 由 `citation_ledger.collect` 消费，本闸不缩其面。
    改前形制＝二进制件经 `read_text(encoding='utf-8')` 抛未捕获 `UnicodeDecodeError` 逸出 `main`
    （rc=1＋traceback、stdout 零声明、旧 PASS 件原样残留），HTML 文档则被当 Markdown 解析后
    只报「conclusions 至少一条」类误导判词（不指真因）。本函数把两支都收成受控拒判，
    **不借机新增答复格式支持**。
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, f"答复件不可读（{type(e).__name__}：{e}）"
    for magic, name in _ANSWER_BINARY_MAGIC:
        if raw.startswith(magic):
            return None, (f"答复件是 {name}，不是 Markdown 答复件"
                          f"（二进制件只可经 --standards-sources 作来源材料）")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, (f"答复件不是 UTF-8 文本（{e.reason}，偏移 {e.start}），"
                      f"不是可解析的 Markdown 答复件")
    if _HTML_DOC_RE.match(text[:8192].lstrip("\ufeff").lstrip()):
        return None, ("答复件是 HTML 文档，不是 Markdown 答复件"
                      "（HTML 只可经 --standards-sources 作来源材料，其编号提取面不受影响）")
    return text, None


def _load_json_object(path: Path, what: str,
                      shape: tuple[type, ...] = (dict,)) -> tuple[object | None, str | None]:
    """输入件（台账／问题条目／条件件／复核表输入件）的「可读＋可解码＋可解析＋顶层形状」合取。

    返回 (对象, 拒判理由)，两者恰有一枚非 None。动因＝改前形制＝`json.loads` 抛
    `JSONDecodeError`、顶层非对象时 `led.get`／`dict(rep, …)` 抛 `AttributeError`／`ValueError`
    逸出 `main`（rc=1＋traceback、stdout 零声明、同路径旧 PASS 件原样残留），
    第四十八枚回件 §四.1／第四十九枚回件 N1 一手实测。此类故障属**输入件**故障而非判定结论，
    须走受控拒判；rc=2 与其余拒判支同形制，rc 语义不因此放宽。

    `shape` 由调用方按**该件的契约形状**给定（P54，CG-20261123-001；默认 `(dict,)`＝P46／P48
    既有三支逐字不变）：`--conditions` 的合法顶层是数组，故该处取 `(dict, list)`＝只延伸
    「可读＋可解码＋可解析」三支而**不加顶层形状闸**（顶层形状不匹配仍静默通过＝PL-304 行 ①
    分流口径「畸形标记独有一支维持登记」的落点，本批不据此声称已拦全部畸形件）。
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, f"{what}不可读（{type(e).__name__}：{e}）"
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return None, f"{what}不可解析（{type(e).__name__}：{str(e)[:200]}）"
    if not isinstance(obj, shape):
        if shape == (dict,):
            return None, f"{what}顶层须为 JSON 对象（现读 {type(obj).__name__}）"
        named = "／".join(t.__name__ for t in shape)
        return None, f"{what}顶层须为 {named} 之一（现读 {type(obj).__name__}）"
    return obj, None


def _is_own_gate_artifact(p: Path, tool: str) -> bool:
    """同路径件是否本工具自产判定件（P47）：顶层仅 gate、且 gate.tool 与本入口一致。

    判不出即不覆盖＝把未经身份确认的文件当作自产件覆写的代价（销毁用户成果）高于
    「本轮持久声明缺一枚件」；判据取归属（tool＋形状）而非取位置，覆盖同名件与覆盖用户
    自有件在形状上不可区分时按不在场处理并如实声明。
    """
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    gate = obj.get("gate") if isinstance(obj, dict) else None
    return (isinstance(obj, dict) and set(obj) == {"gate"} and isinstance(gate, dict)
            and gate.get("tool") == tool and gate.get("verdict") in GATE_VERDICTS)


def _revoke_stale_gate_companion(out_path: Path, tool: str, label: str,
                                 receipt_path: Path | None = None) -> None:
    """成功轮撤销上一轮的自产旁证判定件（P53，CG-20261123-001）。

    改前形制（三支入口同因，一手读数＝`〈过程目录〉/2026-11-23/r52_probe_before.out`）＝
    拒判／契约 FAIL 轮落的 `<out 同名>.gate.json` 在**随后的成功轮既不被追写也不消失**：
    成功轮 stdout 照印 PASS，而该件仍逐字段读作上一轮的 FAIL＝同目录内两枚载体对本轮给出
    相反声明，且本轮输出零提及它。

    取「删除自产件」而非「覆写 PASS 态」＝发起人 2026-11-23 逐字裁定「撤销自产件（删除上一轮
    旁证件），不采用覆写 PASS 态」。覆写会使旁证位长期在场，下游按「该件存在」即读到一枚
    本轮从未请求过的 PASS 声明；删除后该目录回到成功轮本来就不产判定件的形制（既有用例
    `test_r51_answer_entry_non_markdown_refused_without_touching_draft` 的正向对照
    `assertFalse(side.exists())` 即该形制的在册断言）。

    身份对撞先于删除（沿 P47 同一判据）：只删 `_is_own_gate_artifact` 认得的自产件；
    判不出即不删并如实声明——删除不可逆，误删用户件的成本高于一枚过时凭证留在场的成本。

    `receipt_path` 同名豁免（独立复核第一枚回件 N1）：`--receipt` 被显式指到 `<out stem>.gate.json`
    这一派生名时，该件即**本轮自己刚写的回执**（用户指定的输出面），不属「上一轮遗留的旁证件」；
    此时静默跳过＝既不删本轮载体，也不得对它印「非本工具自产件，不删除」（那句判词对
    本轮 PASS 回执为假）。
    """
    side = out_path.with_name(out_path.stem + ".gate.json")
    if not side.exists():
        return
    if receipt_path is not None and Path(receipt_path).resolve() == side.resolve():
        return
    if not _is_own_gate_artifact(side, tool):
        print(f"{label}：同路径判定凭证 {side} 经身份对撞非本工具自产件，不删除"
              f"（该件仍读作其自身轮次的判定，与本轮 PASS 无关）", file=sys.stderr)
        return
    side.unlink()
    print(f"{label}：本轮已产出判定物，上一轮自产判定凭证已撤销（删除，不覆写 PASS 态）：{side}")


def _write_red_receipt(args, reason: str, errors: list[str] | None = None,
                       refused: bool = False, tool: str = "answer_to_case.py --exit-gate",
                       label: str = "出口闸", red_companion: bool = False,
                       verification_ran: bool = False, stale_rendered=()) -> bool:
    """红件轮覆盖回执：请求了 `--receipt` 时，上一轮的 PASS 回执不得留在同一路径冒充本轮判定。

    动因＝独立复核第二十枚回件 N3（契约红早退不触回执，旧 PASS 回执字节前后全等）／
    N4（补证异常时静默不写、stdout 照印 PASS）。主线 §4.1 步骤 6 的「不能以 PASS 留下旧件
    造成误认」延伸到红件轮：rc 虽是权威判据，单独读回执文件的下游仍会把旧件读作本轮结论。
    射程（据实声明）：本件只承载判定态与理由，**不含 verification／ledger 块**——
    据 `gate.verdict == "FAIL"` 即止，不得从中读任何引用结论；引用结论仍由绿件轮的回执承载。
    `verdict` 取闭集 `deliverable_render.GATE_VERDICTS` 的 "FAIL" 而不另立第三值＝拒判与不合格
    的分野由 `gate.refused` 承载，改枚举须同批动契约单源与三面镜像，本批不取该路径。

    两类轮次分开（发起人 2026-11-22 就 P42 裁定，据主线步骤 6）：
    - rc=1 红件轮：一律覆盖（本轮请求了产物即须留下本轮的红件态）。
    - rc=2 拒判轮（`refused=True`）：**同路径已有旧件才中和，无旧件不产新件**——第 12 轮
      「拒判轮不留件」约定（`test_r12_html_unclosed_hidden_container_not_fail_open_at_exit`
      的 `assertFalse(rp.exists())`，其现场从未预置旧件）防的是「拒判冒充已产出判定物」，
      步骤 6 防的是「旧 PASS 件被读作本轮结论」；无旧件时后者不成立，两条约定不相交。
      拒判件另以 `gate.stale_companion` 点名同路径旧本体（本体内容**不改写、不搬移**＝覆盖用户
      交付物属对外不可逆动作，本批不取该路径，持久声明由回执承载）。
    - rc=2 且只请求 `--out`、未请求 `--receipt`（第四十八枚回件唯一阻断项，P45）：判定凭证写
      **本体旁证位** `<out 同名>.gate.json`，条件同样限「本体在场」＝本体不在场时无旧件、
      不产新件。取旁证位而非覆盖本体的理由同上；旁证件与回执同形制，只承载判定态。
    - rc=2 且请求了 `--receipt` 而该路径不在场（P55，CG-20261123-001）：改前双侧不落，
      现按同一条旁证通路回落（条件与上一支一致＝本体在场才留件），使「回执没落到、旁证也没落到」
      这一组合至少留下一枚可读的本轮失败声明；仍落不下时（无 `--out` 可派生名）返回 False，
      由调用方声明未落，不得静默。
    - 旁证位落盘前先经身份对撞（P47，CG-20261122-001）：该名由 `--out` 自动派生，交付目录内
      可恰好已有同名**用户自有件**；`_is_own_gate_artifact` 不认即不覆盖，声明面退回 stderr
      （该窄口的持久声明缺一枚件，是本支刻意取的取舍，翻转前置见执行记录）。

    `tool`／`label` 由调用入口给定（P48，CG-20261122-001）＝评审入口的判定件不得自称
    `--exit-gate`；归属错误会让下游按回执筛入口时把评审的拒判记到答疑面上。

    第三枚形制（P52，CG-20261122-002）＝评审入口 **rc=1 契约 FAIL 轮**：该轮请求了文件输出
    （`--out` 是必填）却没渲染，改前只有一句 stderr，未给 `--receipt` 时持久面零本轮声明（旧 PASS
    回执／旧渲染件原样在场）。`red_companion=True` 使该轮在未给 `--receipt` 时也落旁证位，
    **不受「无旧件不留件」约束**＝那条限的是 rc=2 拒判轮（拒判未形成判定），而契约 FAIL 本身
    就是本轮已形成的判定。`verification_ran=True`＋`stale_rendered` 承载该轮的两条事实：
    引用核验**已跑**（不得冒充「未跑」，也不得据此翻转只反映引用核验的 `gate.verdict`），
    以及旧渲染件与随件数据的路径（字节不改写）。返回 True＝本轮失败状态已落持久件；
    False＝未落（含旁证位被非本工具件占据的窄口），调用方须自行声明未落，不得静默。
    """
    out_path = Path(args.out) if getattr(args, "out", None) else None
    side_path = out_path.with_name(out_path.stem + ".gate.json") if out_path is not None else None
    via_sidecar = False
    if getattr(args, "receipt", None):
        p = Path(args.receipt)
        note = "回执"
        if refused and not p.exists():
            # P55（CG-20261123-001）：改前此支直接 return False＝回执与旁证位双侧不落，本轮零
            # 持久载体、声明只在 stderr（登记现场＝`--receipt` 给定而该件不在场而旧本体在场）。
            # 现回落到同一条旁证通路，条件仍取 rc=2 既有约定「无旧件不产新件」＝有旧本体才留件，
            # 故本支不放宽第 12 轮约定；`--out` 不可派生旁证名时（该入口未给 `--out`）照旧不落。
            if side_path is None or not out_path.exists():
                return False
            p, note, via_sidecar = side_path, "判定凭证（本体旁证位）", True
    else:
        if side_path is None or not ((refused and out_path.exists()) or red_companion):
            return False
        p, note, via_sidecar = side_path, "判定凭证（本体旁证位）", True
    if via_sidecar and p.exists() and not _is_own_gate_artifact(p, tool):
        # P47：旁证位名由 `--out` 派生，交付目录内可恰好已有同名用户件；未经身份确认
        # 即覆写＝销毁用户成果。此处不产持久件，声明面退回 stderr（rc 仍 2＝不冒充放行）。
        print(f"{label}{'拒判' if refused else '红件'}{note}未落盘：同路径件 {p} 经身份对撞"
              f"非本工具自产判定件，不覆盖用户文件——"
              f"{'本轮未形成判定，同路径旧回执／本体不得读作本轮结论' if refused else '本轮未渲染，同路径旧渲染件／随件数据仍属上一轮'}",
              file=sys.stderr)
        return False
    gate = {"verdict": "FAIL", "tool": tool,
            "ran_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason, "errors": list(errors or [])[:20],
            "verification_ran": bool(verification_ran)}
    if refused:
        gate["refused"] = True
        # 本体侧保守＝不改写、不搬移用户交付物（覆盖属对外不可逆动作），但拒判件须**点名**
        # 同路径旧本体，使「不得读作本轮结论」落在持久载体上而非只落在 stderr。
        if out_path is not None and out_path.exists():
            gate["stale_companion"] = str(out_path)
    if stale_rendered:
        gate["stale_rendered"] = [str(x) for x in stale_rendered]
    st = {"gate": gate}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((json.dumps(st, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"{label}{'拒判' if refused else '红件'}{note}"
          f"{'（本件不承载引用结论）' if verification_ran else '（本轮未产引用结论）'}：{p}",
          file=sys.stderr)
    return True


def _ledger_closure_errors(led: dict, src_map: dict, srcs: list) -> list[str]:
    """评审档台账须逐枚等于被评审文件的实抽结果（键集／引用点／年号／提及／点级提及与
    年号明细／点级用途／外源标记／输入身份）。

    动因＝独立复核第一轮 B1/B2：行级 count 与 external 标记原先不受任何约束，手改一个整数
    即可让对外评审件报假引用点，手改一个布尔即可把编造键降为 WARN 放行。`collect()` 的 hits
    未截断（截断只发生在 build_ledger 写 rows 时），故可逐枚复算；不闭合即 fail-closed。
    第二轮 B4 补 `ack`：它与 external 同族、同为 verify 的降级杠杆，而 `collect()` 根本不产它，
    故评审档出现 ack 一律判不闭合（未收录声明须走 findings 并附理由，不得改台账标记）。
    第二轮 N13 补输入身份：`inputs` 逐枚对**实际读到的文件**重算指纹，伪造 sha256/bytes 即不闭合。
    射程外（据实声明）：`rows[].hits` 整块（写盘时已截断至 5 条，无法与实抽全量对撞）。
    本函数只用于评审档（台账登记的对象就是被评审文件）；答疑档台账是交付物自己的引用，
    与源文件的差属漏引／归因判定轴，不得用闭合校验抹掉。
    """
    from citation_ledger import _input_fingerprint

    errs: list[str] = []
    rows = {r["key"]: r for r in led.get("rows") or [] if isinstance(r, dict) and r.get("key")}
    if set(rows) != set(src_map):
        errs.append(f"键集不闭合：台账多 {sorted(set(rows) - set(src_map))}｜缺 {sorted(set(src_map) - set(rows))}")
    for k in sorted(set(rows) & set(src_map)):
        r, s = rows[k], src_map[k]
        if (r.get("count") or 0) != len(s["hits"]):
            errs.append(f"{k} 引用点 {r.get('count')} ≠ 源文件实抽 {len(s['hits'])}")
        if sorted(str(y) for y in (r.get("cited_years") or [])) != sorted(s["years"]):
            errs.append(f"{k} 年号 {r.get('cited_years')} ≠ 源文件实抽 {sorted(s['years'])}")
        if bool(r.get("mention")) != bool(s["mention"]):
            errs.append(f"{k} 提及标记与源文件实抽不符")
        # 点级字段自 CG-20261117-004 起是 verify 的有效语义杠杆（行级仅回落），
        # 须同受闭合对撞，否则手改点级明细可翻转评审判词（第二轮复核 R2-ERROR-1）。
        if list(r.get("hit_mentions") or []) != [h["mention"] for h in s["hits"]]:
            errs.append(f"{k} 点级提及明细与源文件实抽不符")
        if list(r.get("hit_years") or []) != [h["year"] for h in s["hits"]]:
            errs.append(f"{k} 点级年号明细与源文件实抽不符")
        if list(r.get("hit_usages") or []) != [h["usage"] for h in s["hits"]]:
            errs.append(f"{k} 点级用途明细与源文件实抽不符")
        if bool(r.get("external")) != bool(s["external"]):
            errs.append(f"{k} 外源标记与源文件实抽不符")
        if r.get("ack"):
            errs.append(f"{k} 带人工「未收录已声明」标记（ack）：评审档台账须为被评审文件实抽，"
                        f"未收录声明须走 findings 并附理由")
    want = {str(Path(p).resolve()): _input_fingerprint(Path(p)) for p in srcs}
    got = {str(Path(it.get("path", "")).resolve()): it for it in (led.get("inputs") or [])
           if isinstance(it, dict)}
    if set(got) != set(want):
        errs.append(f"输入件集不闭合：台账多 {sorted(set(got) - set(want))}｜缺 {sorted(set(want) - set(got))}")
    for p in sorted(set(got) & set(want)):
        for f in ("bytes", "sha256"):
            if got[p].get(f) != want[p][f]:
                errs.append(f"输入身份不符：{Path(p).name} 的 {f} {got[p].get(f)!r} ≠ 实读 {want[p][f]!r}")
    return errs


def _ledger_reuse_errors(led: dict, fresh: dict, answer_path: Path | None = None) -> list[str]:
    """外置台账复用前与当前答复实抽对撞（审计 F01/T01，E01）：输入身份／键集／年号／引用点数。

    答疑档台账登记的对象＝当前答复自身，故事实集合必须等于当前文本实抽；不一致即拒绝
    复用并给出重新提取路径。语义（mention/hit_mentions/hit_years/hit_usages）不在此对撞——复用时
    一律以实抽覆写（见出口闸段），前像语义旧台账或手改提及标记不得把死标准降 WARN
    （独立复核 ERROR-1）。
    输入身份（第 4 轮终审阻断 2）：给了 answer_path 时，台账 inputs 须含当前答复的
    路径/bytes/sha256 指纹，缺失或不符即拒复用——事实全同的另一文件旧台账不得骑乘放行。
    external/ack 声明通道的现文本复核另在出口闸覆写段承担（声明词不在当前答复正文即剥除）。
    评审档的逐枚闭合（含输入身份）另由 `_ledger_closure_errors` 承担。
    """
    errs: list[str] = []
    if answer_path is not None:
        from citation_ledger import _input_fingerprint
        want = _input_fingerprint(answer_path)
        want_path = str(answer_path.resolve())
        inputs = led.get("inputs")
        matched = isinstance(inputs, list) and any(
            isinstance(it, dict) and isinstance(it.get("path"), str)
            and str(Path(it["path"]).resolve()) == want_path
            and it.get("bytes") == want["bytes"]
            and it.get("sha256") == want["sha256"]
            for it in inputs)
        if not matched:
            errs.append(f"输入身份不符：台账 inputs 缺当前答复 {answer_path.name} 的指纹"
                        f"（路径/bytes/sha256）——事实全同的另一文件台账不得复用")
    a = {r["key"]: r for r in led.get("rows") or [] if isinstance(r, dict) and r.get("key")}
    b = {r["key"]: r for r in fresh.get("rows") or [] if isinstance(r, dict) and r.get("key")}
    if set(a) != set(b):
        errs.append(f"编号成员不闭合：台账多 {sorted(set(a) - set(b))}｜缺 {sorted(set(b) - set(a))}")
    for k in sorted(set(a) & set(b)):
        if sorted(str(y) for y in (a[k].get("cited_years") or [])) != \
                sorted(str(y) for y in (b[k].get("cited_years") or [])):
            errs.append(f"{k} 年号不符：台账 {a[k].get('cited_years')} ≠ 当前答复实抽 {b[k].get('cited_years')}")
        if (a[k].get("count") or 0) != (b[k].get("count") or 0):
            errs.append(f"{k} 引用点数不符：台账 {a[k].get('count')} ≠ 当前答复实抽 {b[k].get('count')}")
    return errs


def _zero_citation_block(led: dict, index_text: str, *, sources_given: bool,
                         strict: bool, tool: str) -> dict:
    """零引用态的随件块（审计 F01/T05）：显式记录「本轮无引用、未跑引用核验」。

    契约对空 rows 必判 FAIL（「零引用是合法态，但不得冒充已核验」）——该判词即诚实声明，
    回执不得被读作对任何旧件的有效性背书。
    """
    from citation_ledger import parse_index
    from deliverable_render import REVIEW_QUALIFICATION

    results = [["WARN", "零引用：本答复不含标准号，本轮未跑引用核验；本回执不构成对任何旧件的有效性背书"]]
    return {
        "ledger": led,
        "zero_citation": True,
        "verification": {
            "mode": "review" if sources_given else "qa",
            "strict": bool(strict),
            "index_keys": sorted(parse_index(index_text)),
            "red": 0, "yellow": 1, "green": 0,
            "unverified": 0, "unindexed_undeclared": 0,
            "results": results,
        },
        "cards": {"indexed": 0, "declared": 0, "cached": 0, "stale": 0, "missing": 0,
                  "indexed_cached": 0, "indexed_stale": 0, "indexed_missing": 0,
                  "states": [], "lines": []},
        "qualification": REVIEW_QUALIFICATION,
        "gate": {"verdict": "PASS", "tool": tool,
                 "ran_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
    }


def _review_refuse(args, reason: str, errors: list[str] | None = None,
                   details=()) -> int:
    """评审入口 rc=2 拒判轮的声明面与中和面（P48，CG-20261122-001）。

    第 47 轮的旧件中和只落在 `--exit-gate` 链上；`--review-report` 的拒判支此前留旧 PASS
    件原样在场、stderr 零声明（第四十八枚回件 §四.3、第四十九枚回件 N3 一手实测）。评审档
    与答疑档共用同一形制＝有旧件即中和、无旧件不留件，只保护一侧即半修。
    `gate.tool` 取真实入口＝评审的拒判不得自称 `--exit-gate`，否则回执归属错误。
    """
    print(f"评审件 FAIL：{reason}——本轮未形成判定，"
          f"同路径旧回执／评审件不得读作本轮结论", file=sys.stderr)
    for x in details:
        print(f"  - {x}", file=sys.stderr)
    _write_red_receipt(args, f"拒判：{reason}——本轮未形成判定", errors, refused=True,
                       tool="answer_to_case.py --review-report", label="评审件")
    return 2


def _review_report(ap, args) -> int:
    """文件评审件：机算台账 → 闭合校验 → 四轴核验 → 渲染 → 回执。问题条目由 `--findings` 数据给。

    闸的两道（独立复核第一轮 B2 后重写，不再用「带源／不带源判词差集」取阻断集——该差集会被
    前缀碰撞与 external 降级吞掉编造键的红）：
      ① **闭合校验**：台账须逐枚等于被评审文件实抽（`_ledger_closure_errors`：键集／引用点／年号／
         提及／外源／ack 标记／输入件身份指纹），不闭合 rc=2 不渲染；
         由此评审件自身不可能带入编造键／假引用点／假外源标记／假「已声明」标记／假输入身份。
      ② **契约校验**：`validate_review`（含 standards 复算级＋成员级判据），红 rc=1 不渲染。
    过闸后，L1—L3 的红（身份／版本／前缀）属**被评审文件**的引用问题＝评审发现，随件报出、不阻断出具；
    此时 L4 覆盖轴因闭合而结构性为空，故不存在「覆盖轴红」这一阻断类。口径句据实印，不再一律归因。
    回执另带 `review_contract`（第二轮 N15）：`gate` 只反映引用核验，契约判定须同件可读，
    否则单独读回执会把「契约 FAIL 未渲染」误读成已放行。
    交付射程（第三轮 N27／第四轮 N38）：**对外交付面＝HTML 单件**；随件 `.json` 与回执含未脱敏
    verify 判词全文（内部编号／仓内路径／检索串／档位码），属内部判定证据，不得随 HTML 一并交付。
    拒判轮（rc=2）的旧件处置（P48，CG-20261122-001）＝经 `_review_refuse` 统一走与答疑入口同一
    形制（有旧件即中和、无旧件不留件），且不再以异常逸出冒充「本轮没跑、旧件仍可用」。
    契约 FAIL 轮（rc=1，P52，CG-20261122-002）＝同一持久面纪律，但取该轮的两条事实：引用核验
    **已跑**（回执带完整 `verification` 与 `review_contract`），本轮**未渲染**（旧渲染件与随件数据
    字节不变并被 `stale_rendered` 点名）；未给 `--receipt` 时落本体旁证位，rc 不因补件而改动。
    """
    from citation_ledger import LedgerError, collect
    from deliverable_render import REVIEW_SCHEMA, render_review, validate_review

    if not args.standards_ledger or not args.findings or not args.out:
        ap.error("--review-report 需要 --standards-ledger、--findings 与 --out")
    if not args.standards_sources:
        ap.error("--review-report 需要 --standards-sources（评审须对被评审文件做双向覆盖与闭合校验）")
    led_path, find_path = Path(args.standards_ledger), Path(args.findings)
    for p in (led_path, find_path):
        if not p.exists():
            return _review_refuse(args, f"输入不存在（{p}）")
    srcs = [Path(p) for p in args.standards_sources]
    absent = [str(p) for p in srcs if not p.exists()]
    if absent:
        return _review_refuse(args, f"被评审文件不在场 {absent}（不得以跳过冒充通过）")
    led, why = _load_json_object(led_path, "台账")
    if why is None:
        rep, why = _load_json_object(find_path, "问题条目")
    if why is not None:
        return _review_refuse(args, why)
    if not (led.get("rows") or []):
        return _review_refuse(args, "台账零引用（不产评审件、不冒充已核验）")
    try:
        src_led = collect(srcs)
    except LedgerError as e:
        # 改前形制＝被评审文件本身无法判定时，LedgerError 逸出到 `main`（traceback＋旧件残留），
        # 而答疑入口同一异常早有 rc=2 支＝两入口不对称。
        return _review_refuse(args, f"被评审文件无法判定（{e}）")
    closure = _ledger_closure_errors(led, src_led, srcs)
    if closure:
        return _review_refuse(args, f"台账与被评审文件不闭合（{len(closure)} 项）——"
                              f"附录须登记被评审文件的实抽引用，不得手改", closure, closure[:10])
    index_text = INDEX_PATH.read_text(encoding="utf-8")
    try:
        st = audit_standards(led, index_text, sources=collect(srcs), strict=args.strict_standards,
                             fetch_missing=args.fetch_missing, fetch_budget=args.fetch_budget,
                             fetch_total=args.fetch_total, tool="answer_to_case.py --review-report")
    except LedgerError as e:
        return _review_refuse(args, f"引用台账无法判定（{e}）")
    rep = dict(rep, schema=REVIEW_SCHEMA, standards=st)
    errs = validate_review(rep)
    st["review_contract"] = {"verdict": "FAIL" if errs else "PASS", "errors": errs}
    _stamp_contract(st)
    out = Path(args.out)
    data_path = out.with_suffix(".json")
    # 本轮尚未写任何产物（渲染在 errs 判定之后），故此处现读到的在场件一律属上一轮
    stale_rendered = [p for p in (out, data_path) if p.exists()]
    if errs and stale_rendered:
        # P52（CG-20261122-002）：契约 FAIL 轮不渲染＝同路径旧渲染件与随件数据字节不变
        # （不删除、不搬移、不冒充本轮），其关联须落在**持久面**上而非只落 stderr。
        # 引用核验已跑的事实保留在 `st` 内（不得改写为「未跑」），`gate.verdict` 仍只反映
        # 引用核验＝被评审文件的引用问题与评审件自身契约失败分属两轴，不得互相顶替。
        st["gate"]["stale_rendered"] = [str(p) for p in stale_rendered]
    if args.receipt:
        p = Path(args.receipt)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((json.dumps(st, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        print(f"出口闸回执：{p}")
    v = st["verification"]
    pts = sum(r.get("count") or 0 for r in led["rows"])
    print(f"引用核验：{'FAIL' if v['red'] else 'PASS'}｜红 {v['red']}｜黄 {v['yellow']}｜绿 {v['green']}"
          f"｜未核 {v['unverified']} 枚｜索引外未声明 {v['unindexed_undeclared']} 枚"
          f"｜台账 {len(led['rows'])} 枚／{pts} 引用点｜闭合校验 PASS")
    _print_cards(st)
    if errs:
        print(f"评审件契约 FAIL（{len(errs)} 项）：不渲染", file=sys.stderr)
        for x in errs:
            print(f"  - {x}", file=sys.stderr)
        if not args.receipt:
            # 未给 `--receipt` ≠ 未请求文件输出（`--out` 对本入口必填）——本轮的失败状态仍须有
            # 可核载体。走出口闸同一条旁证通路（派生名＋身份对撞＋入口归属）；落不下时明示未落，
            # 不伪称已记录成功。rc 仍取 1（契约红），不升 2＝拒判与契约失败不得混为一谈。
            if not _write_red_receipt(args, f"评审件契约 FAIL（{len(errs)} 项）——本轮未渲染，"
                                           f"同路径旧评审件不得读作本轮结论", errs,
                                      tool="answer_to_case.py --review-report", label="评审件",
                                      red_companion=True, verification_ran=True,
                                      stale_rendered=stale_rendered):
                print("评审件契约 FAIL 状态未落持久件（理由见上）——本轮未渲染，"
                      "同路径旧渲染件／随件数据仍属上一轮", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(rep, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    data_path.write_bytes(raw)
    out.write_bytes(render_review(rep, hashlib.sha256(raw).hexdigest(), data_path.name).encode("utf-8"))
    _revoke_stale_gate_companion(out, "answer_to_case.py --review-report", "评审件",
                                 Path(args.receipt) if args.receipt else None)
    print(f"评审件契约 PASS｜问题 {len(rep['findings'])} 项｜数据 {data_path}｜渲染 {out}")
    print("  交付射程：对外交付面＝HTML 单件；随件 .json 与回执含未脱敏判词全文，属内部判定证据，不得随 HTML 一并交付。")
    if v["red"]:
        print(f"  口径：红 {v['red']} 项均属被评审文件的引用问题（台账已与被评审文件逐枚闭合，评审发现随件报出）；"
              f"本件自身诚实性由闭合校验与契约承担。")
    return 0


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="答复 → prefab-deliverable/v3 草稿")
    ap.add_argument("--answer", help="答复 Markdown 路径")
    ap.add_argument("--title")
    ap.add_argument("--project")
    ap.add_argument("--stage", default="技术咨询")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--out")
    ap.add_argument("--template", default="reply_letter", choices=("reply_letter", "brief_card", "conclusion_sheet"))
    ap.add_argument("--conditions", help="可选：条件数组 JSON 路径")
    ap.add_argument("--conclusion-conditions", metavar="JSON",
                    help="可选：按结论 id 注入 conditions 的 JSON 映射（{\"C1\": \"…\"}）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--review-sheet", metavar="OUT")
    ap.add_argument("--sheet-inputs", nargs="+", metavar="JSON")
    ap.add_argument("--apply-review", metavar="REVIEW_JSON")
    ap.add_argument("--exit-gate", metavar="ANSWER_MD",
                    help="交付出口闸：解析答复并跑契约校验，绿才允许交付；红列出违例供修订。"
                         "答复件限 Markdown 文本；HTML 文档／二进制件（DOCX·PDF 等）受控拒判 rc=2")
    ap.add_argument("--standards-ledger", metavar="LEDGER_JSON",
                    help="可选：标准引用台账（citation_ledger.py extract 产物），仅在与当前答复实抽"
                         "身份对撞（输入指纹／键集／年号／引用点数）通过后随出口闸复用，不符即拒绝并提示重新提取。"
                         "两档差异：评审档（--review-report）禁 ack／external 手改（闭合校验逐枚对实抽）；"
                         "答疑/出口闸档 external 声明须在当前答复正文逐字在场（否则剥除），ack 不经复用保留")
    ap.add_argument("--standards-sources", nargs="+", metavar="TXT",
                    help="可选：源文件文本；给出时对台账施加「漏引／归因错误」双向覆盖判据")
    ap.add_argument("--strict-standards", action="store_true",
                    help="未收录于标准索引即红（默认降黄，供开放世界答疑场景）")
    ap.add_argument("--fetch-missing", action="store_true",
                    help="对无有效证据卡的索引外引用联网取真（默认关；证据卡写入用户缓存，"
                         "由 PREFAB_CACHE_DIR 或宿主默认目录解析，不改写随包 evidence/）")
    ap.add_argument("--fetch-budget", type=float, default=30.0, help="每枚取真预算秒（默认 30）")
    ap.add_argument("--fetch-total", type=float, default=120.0, help="取真总预算秒（默认 120）")
    ap.add_argument("--receipt", metavar="OUT_JSON",
                    help="出口闸回执落盘（红/黄/绿＋未核项数＋索引外未声明枚数＋卡消费＋定性声明＋"
                         "standards_contract 随件块契约判定）。回执属内部判定证据：含未脱敏 verify 判词全文，不得对外交付")
    ap.add_argument("--review-report", action="store_true",
                    help="文件评审件：机算台账＋--findings 数据 → 单文件 HTML（业务参考、非正式文件）。"
                         "对外交付面＝HTML 单件；同目录随件 .json 与回执含未脱敏判词全文（内部编号／仓内路径／检索串／档位码），"
                         "属内部判定证据，不得随 HTML 一并交付")
    ap.add_argument("--findings", metavar="JSON",
                    help="评审问题条目数据（prefab-review/v1 的 meta 与 findings；客户专属内容由此给，不入工具）。"
                         "口径：meta/findings 是调用方数据，工具只转写不脱敏（被评审对象自己的分级如 S0～S3 属评审内容，"
                         "一律剥掉会毁掉结论）；内部编号/仓内路径/URL 须由调用方在数据里自行清除。"
                         "工具承担脱敏的只有 verify 判词（内嵌索引状态散文）")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.review_report:
        return _review_report(ap, args)
    if args.exit_gate:
        path = Path(args.exit_gate)
        text, why = _read_answer_text(path)
        if why is not None:
            # P49：不支持的答复格式＝受控拒判，不得以解码异常逸出（旧 PASS 件原样残留且零声明），
            # 也不得让 HTML 文档被当 Markdown 解析后只报误导性的正文契约错误。
            print(f"出口闸 FAIL：{why}——本轮未形成判定，同路径旧回执／本体不得读作本轮结论",
                  file=sys.stderr)
            _write_red_receipt(args, f"拒判：{why}——本轮未形成判定", refused=True)
            return 2
        case, _ = build(text, title=path.stem, project="交付出口", stage=args.stage,
                        date=args.date, source_name=path.name, template="reply_letter", conditions=None)
        errs = validate_case(case)
        if errs:
            print(f"出口闸 FAIL（{len(errs)} 项）：修订答复后重跑，不得交付红件", file=sys.stderr)
            for x in errs:
                print(f"  - {x}", file=sys.stderr)
            _write_red_receipt(args, "答复契约 FAIL：引用核验未跑，本件不承载引用结论", errs)
            return 1
        # 标准引用核验（审计 F01 重写，E01）：一律先对当前答复实抽；外置台账仅在与实抽
        # 身份对撞（输入指纹／键集／年号／引用点数）通过后复用；零引用仍校验显式来源一致性，
        # 请求回执/本体时显式记录本轮实际状态，不留旧件冒充已核验。
        from citation_ledger import LedgerError, build_ledger, collect, verify
        led_path = Path(args.standards_ledger) if args.standards_ledger else None
        if led_path is not None and not led_path.exists():
            print(f"出口闸 FAIL：标准引用台账不存在（{led_path}）——本轮未形成判定，"
                  f"同路径旧回执／本体不得读作本轮结论", file=sys.stderr)
            _write_red_receipt(args, f"拒判：标准引用台账不存在（{led_path}）——本轮未形成判定",
                               refused=True)
            return 2
        src_paths = [Path(p) for p in (args.standards_sources or [])]
        absent = [str(p) for p in src_paths if not p.exists()]
        if absent:
            print(f"出口闸 FAIL：源文件不在场 {absent}——不得以跳过冒充通过；"
                  f"本轮未形成判定，同路径旧回执／本体不得读作本轮结论", file=sys.stderr)
            _write_red_receipt(args, f"拒判：源文件不在场 {absent}——本轮未形成判定",
                               refused=True)
            return 2
        st = None
        try:
            fresh = build_ledger([path])
            led = fresh
            if led_path is not None:
                ext, why = _load_json_object(led_path, "标准引用台账")
                if why is not None:
                    print(f"出口闸 FAIL：{why}——拒绝复用；本轮未形成判定，"
                          f"同路径旧回执／本体不得读作本轮结论", file=sys.stderr)
                    _write_red_receipt(args, f"拒判：{why}——本轮未形成判定", refused=True)
                    return 2
                reuse_errs = _ledger_reuse_errors(ext, fresh, path)
                if reuse_errs:
                    print(f"出口闸 FAIL：外置台账与当前答复实抽不符（{len(reuse_errs)} 项）——拒绝复用；"
                          f"请对当前答复重新提取（citation_ledger.py extract {path.name}）；"
                          f"本轮未形成判定，同路径旧回执／本体不得读作本轮结论", file=sys.stderr)
                    for x in reuse_errs:
                        print(f"  - {x}", file=sys.stderr)
                    _write_red_receipt(args, f"拒判：外置台账与当前答复实抽不符"
                                             f"（{len(reuse_errs)} 项）——本轮未形成判定",
                                       reuse_errs, refused=True)
                    return 2
                # 语义事实一律以当前实抽为准（独立复核 ERROR-1）：前像语义旧台账／手改
                # 提及标记不得把死标准降 WARN 放行。声明通道须以当前答复可见文本为根据
                # （第 4 轮终审阻断 2/T01）：external 仅当当前文本声明词实抽成立才保留，
                # 否则视为过期声明剥除；ack 无正文声明词对应（抽取器不产），复用一律剥除
                # ——未收录声明须写入答复正文（「源文件外依据」等），不得只改台账标记。
                fresh_by_key = {r["key"]: r for r in fresh["rows"]}
                for r in ext.get("rows") or []:
                    if not isinstance(r, dict):
                        continue  # 非法行交 verify 判 LedgerError（rc=2），不在此崩溃
                    f = fresh_by_key.get(r.get("key"))
                    if f is not None:
                        for field in ("mention", "hit_mentions", "hit_years", "hit_usages", "hits"):
                            r[field] = f[field]
                        r["external"] = bool(f.get("external"))
                        r.pop("ack", None)
                ext["inputs"] = fresh["inputs"]
                led = ext
            n_rows = len(led.get("rows") or [])
            index_text = INDEX_PATH.read_text(encoding="utf-8")
            sources = collect(src_paths) if src_paths else None
            if n_rows == 0:
                hard = [f"漏引：源文件有 {k}，答复零引用" for k in sorted(sources)] if sources else []
                st = _zero_citation_block(led, index_text, sources_given=bool(src_paths),
                                          strict=args.strict_standards,
                                          tool="answer_to_case.py --exit-gate")
                if hard:
                    st["verification"]["results"] = [["FAIL", m] for m in hard]
                    st["verification"]["red"] = len(hard)
                    st["verification"]["yellow"] = 0
                    st["gate"]["verdict"] = "FAIL"
                    _write_receipt(args, st, case)
                    print(f"出口闸 FAIL（零引用而来源一致性 {len(hard)} 项）：修订答复后重跑", file=sys.stderr)
                    for m in hard:
                        print(f"  - {m}", file=sys.stderr)
                    return 1
                print("标准引用：0 枚（本件不含标准号，未跑引用核验；回执/本体显式记录零引用态）")
            else:
                try:
                    st = audit_standards(led, index_text, sources=sources, strict=args.strict_standards,
                                         fetch_missing=args.fetch_missing,
                                         fetch_budget=args.fetch_budget, fetch_total=args.fetch_total)
                except LedgerError:
                    raise
                except Exception as e:
                    print(f"标准引用补证：跳过（{e}）", file=sys.stderr)
                if st is None:
                    results = verify(led, index_text, sources, tolerate_unindexed=not args.strict_standards)
                else:
                    results = [tuple(x) for x in st["verification"]["results"]]
                    _print_cards(st)
                hard = [m for lv, m in results if lv == "FAIL"]
                if hard:
                    if st is None:
                        _write_red_receipt(args, "标准引用核验 FAIL 且补证异常：本件不承载引用结论", hard)
                    else:
                        _write_receipt(args, st, case)
                    print(f"出口闸 FAIL（标准引用 {len(hard)} 项）：修订答复或台账后重跑", file=sys.stderr)
                    for m in hard:
                        print(f"  - {m}", file=sys.stderr)
                    return 1
                print(f"标准引用核验 PASS：{n_rows} 枚 ｜ 黄 {sum(1 for lv, _ in results if lv == 'WARN')} 条")
        except LedgerError as e:
            print(f"出口闸 FAIL：引用台账无法判定（{e}）——本轮未形成判定，"
                  f"同路径旧回执／本体不得读作本轮结论", file=sys.stderr)
            _write_red_receipt(args, f"拒判：引用台账无法判定（{e}）——本轮未形成判定",
                               refused=True)
            return 2
        if st is None and (args.receipt or args.out):
            # 第二十枚回件 N4：补证异常时请求的回执／本体静默不写＝旧 PASS 件残留冒充本轮结论；
            # 不请求产物的离线降级（仅台账核验、rc0）保持不变。
            _write_red_receipt(args, "标准引用补证异常：本轮未完成核验，不得以旧件冒充已核验")
            print("出口闸 FAIL：标准引用补证异常而本轮请求了产物／回执——静默不写会留旧件冒充已核验；"
                  "须修复证据卡链，或不带 --receipt/--out 重跑（仅台账核验）", file=sys.stderr)
            return 1
        if not _write_receipt(args, st, case):
            print("出口闸 FAIL：交付件本体未落盘（理由见上）——不得读作已放行", file=sys.stderr)
            return 1
        # 第四轮 N33：只给 --receipt 时块契约 FAIL 不影响返回值，stdout 须同句限定，不得单读误作已放行
        sc = (st or {}).get("standards_contract") or {}
        if st is None:
            qual = "｜补证异常：本轮仅跑台账核验（证据卡未更新），未产回执／本体"
        elif (st or {}).get("zero_citation"):
            qual = "｜零引用：未跑引用核验，回执已显式记录本轮状态（不背书旧件）"
        elif sc.get("verdict") == "FAIL":
            qual = (f"｜随件块契约 FAIL（{len(sc.get('errors') or [])} 项）——见回执 standards_contract；"
                    f"引用核验本身 PASS")
        else:
            qual = ""
        print(f"出口闸 PASS：{path.name} ｜ 结论 {len(case['conclusions'])} 条 ｜ 对外必答 "
              f"{sum(1 for g in case['gaps'] if g.get('external') and g.get('kind', 'ask') == 'ask')} 条{qual}")
        return 0
    if args.apply_review:
        if not args.sheet_inputs:
            ap.error("--apply-review 需要 --sheet-inputs")
        # P54（CG-20261123-001）：复核结果件与逐枚输入件此前都是裸 `read_text` ＋ `json.loads`，
        # 任一枚不可读／非 UTF-8／不可解析即 traceback 逸出（rc=1、零声明），且**输入件按序改写**
        # 会使前几枚已被本轮覆写、第 N 枚才崩＝半程状态。现先全量过闸、再逐个应用。
        review, why = _load_json_object(Path(args.apply_review), "复核结果件")
        if why is not None:
            print(f"复核结果未应用：{why}——本轮未改写任何输入件，同路径旧件不得读作本轮结果",
                  file=sys.stderr)
            return 2
        sheets: list[tuple[Path, str, dict]] = []
        for p in args.sheet_inputs:
            path = Path(p)
            case, why = _load_json_object(path, "复核表输入件")
            if why is not None:
                print(f"复核结果未应用：{why}（{path}）——本轮未改写任何输入件，"
                      f"同路径旧件不得读作本轮结果", file=sys.stderr)
                return 2
            sheets.append((path, path.stem.replace("20261109_", ""), case))
        by_q: dict[str, dict] = {}
        for it in review.get("items", []):
            by_q.setdefault(it["q"], {})[it["id"]] = it
        changed = unknown = incomplete = 0
        gaps_by_q: dict[str, list] = {}
        for g in review.get("gaps", []):
            gaps_by_q.setdefault(g.get("q", ""), []).append(g)
        for path, q, case in sheets:
            for c in case["conclusions"]:
                it = by_q.get(q, {}).get(c["id"])
                if not it:
                    continue
                if it.get("grade"):
                    if it["grade"] not in GRADES:
                        unknown += 1
                    else:
                        grade_changed = c["grade"] != it["grade"]
                        if grade_changed:
                            changed += 1
                        c["note"] = merge_review_note(c.get("note", ""), grade_changed, it.get("note") or "")
                        c["grade"] = it["grade"]
                status = it.get("status") or ""
                dscope = (it.get("dscope") or "").strip()
                dresolve = (it.get("dresolve") or "").strip()
                if status == "清除":
                    c.pop("review_status", None)
                    c.pop("doubt", None)
                elif dscope and dresolve:
                    c["review_status"] = "存疑"
                    c["doubt"] = {"scope": dscope, "resolve": dresolve}
                elif status == "存疑":
                    incomplete += 1
            for g in gaps_by_q.get(q, []):
                gi = g.get("gi")
                if not isinstance(gi, int) or not (0 <= gi < len(case["gaps"])):
                    continue
                t = case["gaps"][gi]
                for src in ("default", "verify_at", "owner"):
                    if g.get(src):
                        t[src] = g[src]
                if g.get("kind") == "assume":
                    if t.get("default") and t.get("verify_at"):
                        t["kind"] = "assume"
                    else:
                        incomplete += 1
                elif g.get("kind") == "ask":
                    t["kind"] = "ask"
            path.write_bytes(json.dumps(case, ensure_ascii=False, indent=2).encode("utf-8"))
        print(f"已应用复核结果：改档 {changed} 条 ｜ 非法档位 {unknown} 条 ｜ 缺必填未写入 {incomplete} 条")
        return 0
    if args.review_sheet:
        if not args.sheet_inputs:
            ap.error("--review-sheet 需要 --sheet-inputs")
        pairs = []
        for p in args.sheet_inputs:
            # P54（CG-20261123-001）：同 `--apply-review` 支，输入件先过闸再渲染，
            # 不得以 traceback 逸出冒充「本轮没跑」（rc=2＋本轮未渲染声明）。
            case, why = _load_json_object(Path(p), "复核表输入件")
            if why is not None:
                print(f"复核表未生成：{why}（{p}）——本轮未渲染，同路径旧复核表不得读作本轮结果",
                      file=sys.stderr)
                return 2
            pairs.append((Path(p).stem.replace("20261109_", ""), case))
        out = Path(args.review_sheet)
        out.write_bytes(render_review_sheet(pairs).encode("utf-8"))
        total = sum(len(c["conclusions"]) for _, c in pairs)
        print(f"已生成复核表：{out} ｜ {len(pairs)} 份 ｜ {total} 条")
        return 0
    for req in ("answer", "title", "project", "out"):
        if not getattr(args, req):
            ap.error(f"--{req} 必填")

    path = Path(args.answer)
    # P50（CG-20261122-002）：本入口此前对答复件走裸 `read_text(encoding="utf-8")`＝同一读取点在
    # 出口闸早有格式闸、在答疑面没有（两支同因不同面）：二进制／非 UTF-8 件抛异常逸出（rc=1＋
    # traceback＋零声明，旧草稿原样留在 `--out`），HTML 文档则被当 Markdown 解析后 rc=0 覆写旧草稿。
    # 复用 `_read_answer_text`（不借机新增 HTML／DOCX 答复支持）＝受控拒判且不触写盘。
    text, why = _read_answer_text(path)
    if why is not None:
        print(f"草稿未生成：{why}——本轮未产出交付件草稿，同路径旧草稿不得读作本轮结果",
              file=sys.stderr)
        _write_red_receipt(args, f"拒判：{why}——本轮未生成草稿", refused=True,
                           tool="answer_to_case.py --answer", label="草稿入口")
        return 2
    conds = cond_map = None
    # P54（CG-20261123-001）：两处条件件此前是裸 `json.loads(Path(...).read_text(encoding="utf-8"))`
    # ＝件不在场／非 UTF-8／不可解析一律 traceback 逸出（rc=1、stdout 零声明、旧草稿不被点名）。
    # 同法延伸到既有 `_load_json_object`（零新增函数；`--conditions` 的合法顶层＝数组，见其 help
    # 与本条正向对照读数，故取 `shape=(dict, list)`＝不加顶层形状闸，畸形形状维持登记）。
    # 两支各用独立因变量（独立复核第一枚回件 阻-1）：共用一枚 `why` 时，后一支读取成功会把它
    # 覆盖成 None＝前一支的坏件被静默丢件出草稿（rc=0），恰与本族要防的「静默丢件」同类。
    why_conds = why_cc = None
    if args.conditions:
        conds, why_conds = _load_json_object(Path(args.conditions), "条件件", shape=(dict, list))
    if args.conclusion_conditions:
        cond_map, why_cc = _load_json_object(Path(args.conclusion_conditions), "结论条件件")
    if why_conds is not None or why_cc is not None:
        why = "；".join(w for w in (why_conds, why_cc) if w is not None)
        print(f"草稿未生成：{why}——本轮未产出交付件草稿，同路径旧草稿不得读作本轮结果",
              file=sys.stderr)
        _write_red_receipt(args, f"拒判：{why}——本轮未生成草稿", refused=True,
                           tool="answer_to_case.py --answer", label="草稿入口")
        return 2
    case, review = build(text, title=args.title, project=args.project, stage=args.stage,
                         date=args.date, source_name=path.name, template=args.template, conditions=conds,
                         cond_map=cond_map)
    out = Path(args.out)
    out.write_bytes(json.dumps(case, ensure_ascii=False, indent=2).encode("utf-8"))
    _revoke_stale_gate_companion(out, "answer_to_case.py --answer", "草稿入口",
                                 Path(args.receipt) if args.receipt else None)

    print(f"解析：{path.name} ｜ {len(text.splitlines())} 行")
    print(f"结论 {len(case['conclusions'])} 条：" + " / ".join(f"{g} {sum(1 for c in case['conclusions'] if c['grade'] == g)}"
                                                        for g in ("A", "C", "D")))
    print(f"缺件 {len(case['gaps'])} 条 ｜ 失效条件 {len(case['overturn'])} 条 ｜ 形态 {args.template}")
    for item in review:
        print(f"  - {item}")
    print(f"输出：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
