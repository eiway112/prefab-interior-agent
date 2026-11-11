#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""答复 → 交付件草稿：把技能的一次 Markdown 答复解析成 prefab-deliverable/v3。

硬约束（不编造）：
  1. 只收「带出处」的断言：标准编号或显式「行业通用做法/默认推荐」；
  2. 带条款号 → A；仅标准编号、无条款 → D；通用做法 → C；三者皆无 → 不收录（列进待审）；
  3. 每条结论/缺件带 _source_line，可回查原文行；
  4. 不推断项目条件；无变量答复默认出对外件，不出工作面。

用法（cwd＝本工具所在目录）：
  python -B answer_to_case.py --answer a.md --title "…" --project "…" --out draft.json
  python -B answer_to_case.py --answer a.md --conclusion-conditions cond.json …（按结论 id 注入 conditions）
  python -B answer_to_case.py --selftest
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
            grade, doc, clause = "A", "；".join(standards), (CLAUSE_RE.search(value).group(0) if CLAUSE_RE.search(value) else "")
            note = "自动定档：条款承载已取证（条款号＋逐字＋页锚），待人工确认。"
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
        review.append(f"D 档 {d_count} 条：仅标准编号、条款未核，须人工确认")
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
select,input{font:inherit;border:1px solid #9aa4b0;border-radius:4px;padding:4px 6px;background:#fff;min-width:0}
select{width:78px}
input{width:100%}
tr.done{background:#f2f8f3}
@media print{.bar{position:static}.page{padding:0}button{display:none}}
"""

SHEET_JS = r"""
(function(){
  var sels=[].slice.call(document.querySelectorAll('select[data-grade]'));
  function sync(){
    var done=sels.filter(function(s){return s.value;}).length;
    document.getElementById('done').textContent=String(done);
    sels.forEach(function(s){s.closest('tr').classList.toggle('done', !!s.value);});
  }
  sels.forEach(function(s){s.addEventListener('change',sync);});
  document.querySelectorAll('input[data-note]').forEach(function(i){i.addEventListener('input',sync);});
  function collect(){
    var items=sels.map(function(s){
      var tr=s.closest('tr');
      return {q:tr.getAttribute('data-q'), id:tr.getAttribute('data-id'), grade:s.value,
              note:(tr.querySelector('input[data-note]')||{}).value||'',
              status:(tr.querySelector('select[data-status]')||{}).value||'',
              dscope:(tr.querySelector('input[data-dscope]')||{}).value||'',
              dresolve:(tr.querySelector('input[data-dresolve]')||{}).value||''};
    }).filter(function(x){return x.grade||x.status||x.dscope||x.dresolve;});
    var gaps=[].slice.call(document.querySelectorAll('tr[data-gi]')).map(function(tr){
      return {q:tr.getAttribute('data-q'), gi:parseInt(tr.getAttribute('data-gi'),10),
              kind:(tr.querySelector('select[data-kind]')||{}).value||'ask',
              default:(tr.querySelector('input[data-default]')||{}).value||'',
              verify_at:(tr.querySelector('input[data-verify]')||{}).value||'',
              owner:(tr.querySelector('input[data-owner]')||{}).value||''};
    });
    return {items:items, gaps:gaps};
  }
  document.getElementById('expJson').addEventListener('click',function(){
    var blob=new Blob([JSON.stringify(collect(),null,2)],{type:'application/json'});
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);
    a.download='grade_review.json';document.body.appendChild(a);a.click();a.remove();
    setTimeout(function(){URL.revokeObjectURL(a.href);},1500);
  });
  document.getElementById('expHtml').addEventListener('click',function(){
    var st=document.getElementById('saved-state');
    st.textContent=JSON.stringify(collect());
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
      (st.items||[]).forEach(function(it){
        var tr=document.querySelector('tr[data-q="'+it.q+'"][data-id="'+it.id+'"]');
        if(!tr) return;
        var s=tr.querySelector('select[data-grade]'); if(s&&it.grade) s.value=it.grade;
        var n=tr.querySelector('input[data-note]'); if(n&&it.note) n.value=it.note;
        var stt=tr.querySelector('select[data-status]'); if(stt&&it.status) stt.value=it.status;
        var ds=tr.querySelector('input[data-dscope]'); if(ds&&it.dscope) ds.value=it.dscope;
        var dr=tr.querySelector('input[data-dresolve]'); if(dr&&it.dresolve) dr.value=it.dresolve;
      });
      (st.gaps||[]).forEach(function(g){
        var tr=document.querySelector('tr[data-q="'+g.q+'"][data-gi="'+g.gi+'"]');
        if(!tr) return;
        var k=tr.querySelector('select[data-kind]'); if(k&&g.kind) k.value=g.kind;
        var d=tr.querySelector('input[data-default]'); if(d) d.value=g.default||'';
        var v=tr.querySelector('input[data-verify]'); if(v) v.value=g.verify_at||'';
        var o=tr.querySelector('input[data-owner]'); if(o&&g.owner) o.value=g.owner;
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
                f'<td><input data-note placeholder="理由（可选）"></td>'
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
    check("档位规则：条款→A／仅标准号→D／通用做法与构造项→C／不判声明→N", grades == ["A", "D", "C", "C", "N"], str(grades))
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
    """对台账中「索引外且未声明」的行查证据卡；只读缓存，判定不变。"""
    from citation_ledger import parse_index
    import on_demand_retrieval as O

    index = parse_index(index_text)
    stats = {"indexed": 0, "cached": 0, "stale": 0, "missing": 0, "declared": 0}
    lines: list[tuple[str, str]] = []
    for row in rows:
        key = row.get("key")
        if not key:
            continue
        if key in index:
            stats["indexed"] += 1
            continue
        if row.get("mention") or row.get("external") or row.get("ack"):
            stats["declared"] += 1
            continue
        r = O.lookup_card(CARD_ROOT, key)
        if r["state"] == "valid":
            stats["cached"] += 1
            lines.append((key, f"证据卡 {_card_desc(r)}"))
        elif r["state"] == "stale":
            stats["stale"] += 1
            exp = ((r.get("card") or {}).get("cache") or {}).get("expires_at")
            lines.append((key, f"证据卡已过期（到期 {exp}），可 retrieve --refresh"))
        else:
            stats["missing"] += 1
            lines.append((key, "无有效证据卡（可加 --fetch-missing 取真）"))

    if fetch_missing:
        import subprocess
        import time
        started = time.monotonic()
        engine = Path(__file__).with_name("on_demand_retrieval.py")
        for i, (key, desc) in enumerate(lines):
            if "无有效证据卡" not in desc and "已过期" not in desc:
                continue
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
            else:
                lines[i] = (key, f"取真未达有效证据卡（exit {p.returncode}）")
    return stats, lines


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
                    help="交付出口闸：解析答复并跑契约校验，绿才允许交付；红列出违例供修订")
    ap.add_argument("--standards-ledger", metavar="LEDGER_JSON",
                    help="可选：标准引用台账（citation_ledger.py extract 产物），随出口闸跑引用核验")
    ap.add_argument("--standards-sources", nargs="+", metavar="TXT",
                    help="可选：源文件文本；给出时对台账施加「漏引／归因错误」双向覆盖判据")
    ap.add_argument("--strict-standards", action="store_true",
                    help="未收录于标准索引即红（默认降黄，供开放世界答疑场景）")
    ap.add_argument("--fetch-missing", action="store_true",
                    help=f"对无有效证据卡的索引外引用联网取真（默认关；证据卡写入 {L.CARD_DIR_REL.as_posix()}/）")
    ap.add_argument("--fetch-budget", type=float, default=30.0, help="每枚取真预算秒（默认 30）")
    ap.add_argument("--fetch-total", type=float, default=120.0, help="取真总预算秒（默认 120）")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.exit_gate:
        path = Path(args.exit_gate)
        text = path.read_text(encoding="utf-8")
        case, _ = build(text, title=path.stem, project="交付出口", stage=args.stage,
                        date=args.date, source_name=path.name, template="reply_letter", conditions=None)
        errs = validate_case(case)
        if errs:
            print(f"出口闸 FAIL（{len(errs)} 项）：修订答复后重跑，不得交付红件", file=sys.stderr)
            for x in errs:
                print(f"  - {x}", file=sys.stderr)
            return 1
        # 标准引用核验：默认对答复自身建台账（技术答疑／交付件两场景自动生效）。
        # 给出 --standards-ledger 时以该台账为准；零引用不判（合法态），不冒充「已核验」。
        from citation_ledger import LedgerError, collect, ledger_from_texts, verify
        led_path = Path(args.standards_ledger) if args.standards_ledger else None
        if led_path is not None and not led_path.exists():
            print(f"出口闸 FAIL：标准引用台账不存在（{led_path}）", file=sys.stderr)
            return 2
        try:
            led = (json.loads(led_path.read_text(encoding="utf-8")) if led_path is not None
                   else ledger_from_texts({path.name: text}))
            n_rows = len(led.get("rows") or [])
            if n_rows == 0:
                print("标准引用：0 枚（本件不含标准号，跳过引用核验）")
            else:
                index_text = INDEX_PATH.read_text(encoding="utf-8")
                results = verify(
                    led, index_text,
                    collect([Path(p) for p in args.standards_sources]) if args.standards_sources else None,
                    tolerate_unindexed=not args.strict_standards)
                try:
                    stats, card_lines = _standards_enrichment(
                        led.get("rows") or [], index_text,
                        fetch_missing=args.fetch_missing,
                        fetch_budget=args.fetch_budget, fetch_total=args.fetch_total)
                    print(f"标准引用补证：索引内 {stats['indexed']}｜证据卡有效 {stats['cached']}｜"
                          f"过期 {stats['stale']}｜无卡 {stats['missing']}｜已声明外源 {stats['declared']}")
                    for key, desc in card_lines:
                        print(f"  - {key}：{desc}")
                except Exception as e:
                    print(f"标准引用补证：跳过（{e}）", file=sys.stderr)
                hard = [m for lv, m in results if lv == "FAIL"]
                if hard:
                    print(f"出口闸 FAIL（标准引用 {len(hard)} 项）：修订答复或台账后重跑", file=sys.stderr)
                    for m in hard:
                        print(f"  - {m}", file=sys.stderr)
                    return 1
                print(f"标准引用核验 PASS：{n_rows} 枚 ｜ 黄 {sum(1 for lv, _ in results if lv == 'WARN')} 条")
        except LedgerError as e:
            print(f"出口闸 FAIL：引用台账无法判定（{e}）", file=sys.stderr)
            return 2
        print(f"出口闸 PASS：{path.name} ｜ 结论 {len(case['conclusions'])} 条 ｜ 对外必答 "
              f"{sum(1 for g in case['gaps'] if g.get('external') and g.get('kind', 'ask') == 'ask')} 条")
        return 0
    if args.apply_review:
        if not args.sheet_inputs:
            ap.error("--apply-review 需要 --sheet-inputs")
        review = json.loads(Path(args.apply_review).read_text(encoding="utf-8"))
        by_q: dict[str, dict] = {}
        for it in review.get("items", []):
            by_q.setdefault(it["q"], {})[it["id"]] = it
        changed = unknown = incomplete = 0
        gaps_by_q: dict[str, list] = {}
        for g in review.get("gaps", []):
            gaps_by_q.setdefault(g.get("q", ""), []).append(g)
        for p in args.sheet_inputs:
            path = Path(p)
            q = path.stem.replace("20261109_", "")
            case = json.loads(path.read_text(encoding="utf-8"))
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
            case = json.loads(Path(p).read_text(encoding="utf-8"))
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
    text = path.read_text(encoding="utf-8")
    conds = json.loads(Path(args.conditions).read_text(encoding="utf-8")) if args.conditions else None
    cond_map = (json.loads(Path(args.conclusion_conditions).read_text(encoding="utf-8"))
                if args.conclusion_conditions else None)
    case, review = build(text, title=args.title, project=args.project, stage=args.stage,
                         date=args.date, source_name=path.name, template=args.template, conditions=conds,
                         cond_map=cond_map)
    out = Path(args.out)
    out.write_bytes(json.dumps(case, ensure_ascii=False, indent=2).encode("utf-8"))

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
