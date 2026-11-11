#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""citation_ledger.py — 标准引用台账：从任意文本抽取引用、与索引对撞、与源文件双向覆盖。

动因（2026-11-10 长沙四份 PDF 评审事故的机算承担者）：既有判据对「引用」只做锚点触发式
配对（validate_governance 检查 8：文中出现「序号 N」才判），而编造的形态恰恰是**不命中锚点**
——写错前缀（GB／GB-T）、漏年号、引已废止旧版、整条编号不存在，全部落「无锚不判」判绿。
本工具把判据反转为**闭集枚举**：先枚举出全部引用，再逐条对撞，未解析者 fail-closed。

三轴判据（每条引用都判，不依赖是否出现「序号」）：
  L1 身份   —— 编号必须在 `standards-index.md` 主表内（或显式标 external／unindexed-ack）
  L2 版本   —— 引用年号须等于索引现版年号；索引状态为已废止即红、被部分替代/被部分废止即黄
  L3 前缀   —— 编号解析不到时，若同号在另一前缀下存在，判「前缀/族不符」而非「未收录」
  L4 覆盖   —— 给了 --sources 时：源文件有而交付物无＝漏引（红）；交付物有而源文件无＝
               归因错误（红），除非该行被标 external（评审依据，源文件外）

「提及 vs 依据」豁免（结构判据、不建白名单）：同一行含 `替代`／`废止`／`旧版`／`不得`／
`待核`／`原引用` 之一者判为「提及」，只黄不红——正确答案常以否定句出现（实例＝
`成果/交付件/真实试点/自贸三层_办公隔墙隔声.json` 的 D 档结论逐字「图面引用的 GB/T 11981-2008
已被 GB/T 11981-2024 替代…不得以图面旧版号直接作依据」，一刀切判红会把人指向错误的修法）。

子命令：
  extract  <文件...> --out <ledger.json>        抽取引用，产出台账（不含判定）
  verify   <ledger.json> --index <索引> [--sources <文件...>]   对台账施加 L1—L4，红即 exit 1
  --selftest                                   内存内负向注入自证（不读写仓内文件）

退出码：0 全过（可含 WARN）；1 存在 FAIL；2 无法产出（输入缺失/不可解析，fail-closed）。

口径边界（据实披露）：
  - 只抽取「标准号族」字面，不判条文号是否存在、不判条文内容、不判数值是否正确；
  - 索引解析只认主表「序号|编号|名称|…|状态」形态（与检查 3 同源形制），核验记录表不入面；
  - OCR 造成的同号异写（`GB T33544`／`GB/T 33544`）按「空格与斜杠归一」处理，仍不合并者
    按 L3 前缀族报出，由人裁定；本工具不猜。
依赖：Python 3.10+（仅标准库）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import delivery_layout as L  # noqa: E402

ROOT = L.ROOT_DEFAULT
DEFAULT_INDEX = ROOT / L.INDEX_REL
SCHEMA = "citation-ledger/v1"

# 标准号族。前缀分族（GB／GB/T 是两族，由 /T 决定），编号保留分部号（10801.2），年号可缺。
STD_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(T\s*/\s*CECS|RISN\s*[-—]\s*TG|DBJ?\s*\d{2,6}|GBJ|GB|JGJ|JG|JC|CJJ|CJ|CECS)"
    r"\s*(/\s*T)?\s*"
    r"(\d{1,6}(?:\.\d{1,3})?)"
    r"(?:\s*[-—–]\s*((?:19|20)\d{2}))?"
)

STATUS_PREFIXES = ("现行有效", "即将实施", "过渡期", "已废止", "被部分替代", "被部分废止", "被替代")
DEAD_PREFIXES = ("已废止", "被替代")
PARTIAL_PREFIXES = ("被部分替代", "被部分废止")
MENTION_WORDS = ("替代", "废止", "旧版", "不得", "待核", "原引用")
EXTERNAL_WORDS = ("源文件外", "评审依据", "外部依据")


class LedgerError(ValueError):
    pass


def norm_prefix(raw: str, t_flag: str | None) -> str:
    p = re.sub(r"\s+", "", raw).upper().replace("—", "-")
    if p.startswith("DBJ") or p.startswith("DB"):
        return p + ("/T" if t_flag else "")
    if p in ("T/CECS", "RISN-TG", "GBJ", "CECS"):
        return p
    return p + ("/T" if t_flag else "")


# OCR/排版常见异写：`GB T33544`／`GBT33544`／`GB / T 33544` → `GB/T 33544`。
# 只归一「前缀＋T＋数字」三连，不碰正文里的字母 T。
_T_GLUE = re.compile(r"(?<![A-Za-z])(GBJ|GB|JGJ|JG|JC|CJJ|CJ|DBJ?\d{0,6})(\s*/\s*|\s*)T(?=\s*\d)")


def prettify(line: str) -> str:
    return _T_GLUE.sub(lambda m: f"{m.group(1)}/T", line)


def iter_citations(text: str):
    """产出 (prefix, num, year_or_None, line_no, line_text)。"""
    for i, line in enumerate(text.splitlines(), 1):
        for m in STD_RE.finditer(prettify(line)):
            yield norm_prefix(m.group(1), m.group(2)), m.group(3), m.group(4), i, line


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
        if not m:
            continue
        status = next((c for c in cells[2:] if c.startswith(STATUS_PREFIXES)), None)
        if status is None:
            continue
        k = key_of(norm_prefix(m.group(1), m.group(2)), m.group(3))
        out.setdefault(k, {
            "seq": cells[0], "name": cells[2], "year": m.group(4),
            "status": status, "line": i,
        })
    return out


def load_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        import zipfile
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        return re.sub(r"<[^>]+>", "", xml.replace("</w:p>", "\n"))
    return path.read_text(encoding="utf-8", errors="replace")


def collect(files: list[Path]) -> dict:
    """→ {key: {"years":set, "hits":[{file,line,text}], "mention":bool, "external":bool}}"""
    rows: dict[str, dict] = {}
    for f in files:
        text = load_text(f)
        for prefix, num, year, ln, line in iter_citations(text):
            k = key_of(prefix, num)
            r = rows.setdefault(k, {"years": set(), "hits": [], "mention": False, "external": False})
            if year:
                r["years"].add(year)
            r["hits"].append({"file": str(f).replace("\\", "/"), "line": ln, "text": line.strip()[:120]})
            if any(w in line for w in MENTION_WORDS):
                r["mention"] = True
            if any(w in line for w in EXTERNAL_WORDS):
                r["external"] = True
    return rows


def _input_fingerprint(path: Path) -> dict:
    import hashlib
    data = path.read_bytes()
    return {"path": str(path).replace("\\", "/"), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def build_ledger(files: list[Path]) -> dict:
    rows = collect(files)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": [_input_fingerprint(p) for p in files],
        "count": len(rows),
        "rows": [
            {
                "key": k,
                "cited_years": sorted(v["years"]),
                "count": len(v["hits"]),
                "mention": v["mention"],
                "external": v["external"],
                "hits": v["hits"][:5],
            }
            for k, v in sorted(rows.items())
        ],
    }


def ledger_from_texts(texts: dict[str, str]) -> dict:
    """内存内版本（不落盘）：{文件名: 文本} → ledger。供测试与脚本复用。"""
    rows: dict[str, dict] = {}
    for name, text in texts.items():
        for prefix, num, year, ln, line in iter_citations(text):
            k = key_of(prefix, num)
            r = rows.setdefault(k, {"years": set(), "mention": False, "external": False, "n": 0})
            r["n"] += 1
            if year:
                r["years"].add(year)
            r["mention"] |= any(w in line for w in MENTION_WORDS)
            r["external"] |= any(w in line for w in EXTERNAL_WORDS)
    return {
        "schema": SCHEMA,
        "count": len(rows),
        "rows": [
            {"key": k, "cited_years": sorted(v["years"]), "count": v["n"],
             "mention": v["mention"], "external": v["external"]}
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
        before = len([1 for lv, _ in out if lv == "FAIL"])
        if any(status.startswith(p) for p in DEAD_PREFIXES) and not mention:
            out.append(("FAIL", f"引用已废止标准：{key}（索引 {ent['seq']} 行：{status}）"))
            continue
        if any(status.startswith(p) for p in PARTIAL_PREFIXES) and not mention:
            out.append(("WARN", f"引用被部分替代标准：{key}（{status}）—— 正文须声明适用面"))
        if years and cur_year and any(y != cur_year for y in years) and not mention:
            out.append(("FAIL", f"年号与索引现版不一致：{key} 引用 {years}／索引 {cur_year}（{status}）"))
            continue
        if not years and cur_year and not mention:
            out.append(("WARN", f"未注年号：{key}（索引现版 {cur_year}）"))
        if len([1 for lv, _ in out if lv == "FAIL"]) == before:
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


def _cmd_extract(args) -> int:
    files = [Path(p) for p in args.files]
    if not files:
        print("extract：至少一个输入文件", file=sys.stderr)
        return 2
    missing = [str(p) for p in files if not p.exists()]
    if missing:
        print(f"extract：输入不存在 {missing}", file=sys.stderr)
        return 2
    led = build_ledger(files)
    out = Path(args.out) if args.out else ROOT / L.LEDGER_DEFAULT_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(led, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"台账已产出：{out} ｜ 引用 {led['count']} 枚 ｜ 输入 {len(files)} 件")
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
    v = sub.add_parser("verify", help="对台账施加 L1—L4 判据")
    v.add_argument("ledger")
    v.add_argument("--index")
    v.add_argument("--sources", nargs="+")
    v.add_argument("--tolerate-unindexed", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.cmd == "extract":
        return _cmd_extract(args)
    if args.cmd == "verify":
        return _cmd_verify(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
