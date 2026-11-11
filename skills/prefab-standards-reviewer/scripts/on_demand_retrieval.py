#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""on_demand_retrieval.py — L-RKL 按需取真引擎：证据卡缓存 + 通道四态 + 预算 + 缺口冒泡。

子命令：
  query    <标准编号>                 只读证据卡缓存；命中 exit 0，未命中/过期 exit 1；不联网
  retrieve <标准编号> [--name ...]     缓存优先；未命中/--refresh 时按路由跑通道，写证据卡；命中 exit 0，未取到 exit 1
  gaps     --ledger <台账.json>        从 citation_ledger 台账冒泡未取真缺口；不联网
  channels                            打印通道注册表与路由
  layout                              打印现读交付布局（开发仓／随包）
  --selftest                          离线负向注入自证（不联网、不写仓内证据卡）

判据边界：
  - 只做身份面（编号/名称/年号/状态/发布实施日期）；不取条文原文、不写知识条目；
  - 证据卡 review_status=unreviewed，机器生成，不替代独立复核，不自动入库；
  - 通道四态：hit / filtered_no_record / not_covered / indeterminate；未命中不写「不存在」；
    墙钟耗尽与大载体一律落 indeterminate（reason=wall_clock_budget／carrier_too_large），
    不得落 not_covered——「没取到」不等于「该库不收」；
  - 预算分两层：`--budget`＝每通道**墙钟**秒（缺省按注册表分档），`--total-budget`＝整轮墙钟秒；
    socket 单次操作超时另由分档给，二者不可混用（urllib 的 timeout 不约束整段墙钟）；
  - 证据卡 TTL：命中 90 天、未命中 7 天；过期由 query 报 stale，不得当现行证据用。
    miss-TTL 7 天即「改日再取」回路，不是终局否证。

依赖：Python 3.10+（标准库）；政府体系件 PDF 抽行需要 PyMuPDF，缺库时该通道记 indeterminate。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import delivery_layout as L  # noqa: E402

ROOT_DEFAULT = L.ROOT_DEFAULT
REGISTRY_REL = L.REGISTRY_REL
CARD_DIR_REL = L.CARD_DIR_REL
INDEX_REL = L.INDEX_REL
SCHEMA = "standard-evidence-card/v1"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

STATE_HIT = "hit"
STATE_FILTERED = "filtered_no_record"
STATE_NOT_COVERED = "not_covered"
STATE_INDETERMINATE = "indeterminate"
STATES = (STATE_HIT, STATE_FILTERED, STATE_NOT_COVERED, STATE_INDETERMINATE)
LEVEL_RANK = {"S1": 3, "S2": 2, "S2-": 1, "S0": 0}
MACHINE_REVIEW = "unreviewed（机器生成；未走独立复核，不入知识条目）"


def _grade_token(grade) -> str:
    """从 `S1（转引）`／`S2-` 这类带注记的等级串取等级本体；取不到返回空串。"""
    m = re.match(r"\s*(S[012]-?)", str(grade or ""))
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 基础工具

def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_str() -> str:
    return date.today().isoformat()


def norm_id(s: str) -> str:
    s = (s or "").strip().upper()
    for a, b in (("—", "-"), ("–", "-"), ("－", "-"), ("／", "/")):
        s = s.replace(a, b)
    return re.sub(r"\s+", "", s)


def id_key(s: str) -> tuple[str, str]:
    s = norm_id(s)
    m = re.match(r"^(.*?)-((?:19|20)\d{2})$", s)
    return (m.group(1), m.group(2)) if m else (s, "")


def same_standard(a: str, b: str) -> bool:
    ka, kb = id_key(a), id_key(b)
    return ka[0] == kb[0] and (not ka[1] or not kb[1] or ka[1] == kb[1])


def prefix_of(s: str) -> str:
    m = re.match(r"^([A-Z]+\d*(?:/[A-Z]+)?)", norm_id(s))
    return m.group(1) if m else ""


def family_of(s: str) -> str:
    p = prefix_of(s).split("/")[0]
    if p.startswith("DBJ"):
        return "DBJ"
    if p.startswith("DB"):
        return "DB"
    return p


def core_of(s: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)(?:-(?:19|20)\d{2})?$", norm_id(s))
    return m.group(1) if m else ""


def year_of(s: str) -> str | None:
    m = re.search(r"-((?:19|20)\d{2})$", norm_id(s))
    return m.group(1) if m else None


def safe_id(s: str) -> str:
    s = re.sub(r"([A-Z])(\d)", r"\1-\2", norm_id(s))
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")


def norm_date(s):
    if not s:
        return s
    m = re.match(r"^(\d{4})/(\d{2})/(\d{2})$", str(s).strip())
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else s


def read_json(p) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write_json(p, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def strip_tags(s: str) -> str:
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"[\u2000-\u200b\u3000\u00a0]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(T\s*/\s*CECS|RISN\s*[-—]\s*TG|DBJ?\s*\d{2,6}|GBJ|GB|JGJ|JG|JC|CJJ|CJ|CECS)"
    r"\s*(/\s*T)?\s*(\d{1,6}(?:\.\d{1,3})?)(?:\s*[-—–]\s*((?:19|20)\d{2}))?"
)


def extract_codes(text: str) -> list[str]:
    out = []
    for m in CODE_RE.finditer(text):
        p = m.group(1).replace(" ", "").upper()
        if m.group(2):
            p += "/T"
        out.append(f"{p}{m.group(3)}-{m.group(4)}" if m.group(4) else f"{p}{m.group(3)}")
    return out


def code_core(code: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)", norm_id(code))
    return m.group(1) if m else ""


def core_match(code: str, token: str) -> bool:
    return code_core(code) == code_core(token)


class _WallClockExpired(Exception):
    pass


def _transient(exc) -> bool:
    """只对连接类瞬时失败重试；4xx 是确定答复，重试无意义。

    先例＝官方出版者电子书首探 25 s 超时、后探 332 ms 即成（站点瞬时不可用，非「无全文」）。
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


def http_get(url, referer=None, timeout=30, data=None, xhr=True, retries=0,
             wall_budget=None) -> dict:
    """timeout＝单次 socket 操作超时；wall_budget＝**整段墙钟**上限（含重试）。

    两者必须分开：urllib 的 timeout 不约束整段墙钟——实测本地慢速服务每 0.4 s 吐 64 B、
    16 s 发完，以 timeout=2 调用仍 ok=True、墙钟 15.75 s。故读循环按 deadline 逐块收口。
    墙钟耗尽时**不返回部分字节**：部分载体的 sha256 不对应完整件，不得作证据。
    """
    headers = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    if referer:
        headers["Referer"] = referer
    if xhr:
        headers["X-Requested-With"] = "XMLHttpRequest"
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    t = time.perf_counter()
    deadline = t + wall_budget if wall_budget else None
    last_exc = None
    partial = 0
    for attempt in range(retries + 1):
        left = (deadline - time.perf_counter()) if deadline is not None else None
        if left is not None and left <= 0:
            last_exc = _WallClockExpired("wall_budget exhausted before attempt")
            break
        sock = min(timeout, left) if left is not None else timeout
        try:
            req = urllib.request.Request(url, headers=headers, data=data)
            with urllib.request.urlopen(req, timeout=sock) as r:
                chunks = []
                # read1＝有多少取多少；read(n) 会阻塞等满 n 字节，deadline 就只在两次 read
                # 之间生效，慢速载体照样超预算（实测 wall_budget=0.6 仍跑到 3.88 s）。
                read_some = getattr(r, "read1", None) or r.read
                while True:
                    if deadline is not None and time.perf_counter() >= deadline:
                        partial = sum(len(c) for c in chunks)
                        raise _WallClockExpired("wall_budget exhausted during read")
                    chunk = read_some(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                b = b"".join(chunks)
                if "gzip" in (r.headers.get("Content-Encoding") or ""):
                    b = gzip.decompress(b)
                return {"ok": True, "status": int(r.status), "body": b,
                        "ms": round((time.perf_counter() - t) * 1000, 1), "error": None,
                        "attempts": attempt + 1}
        except Exception as exc:
            last_exc = exc
            if isinstance(exc, _WallClockExpired):
                break
            if attempt < retries and _transient(exc):
                left = (deadline - time.perf_counter()) if deadline is not None else None
                if left is not None and left <= 0.5:
                    break
                time.sleep(min(0.5 * (attempt + 1), max(0.0, left - 0.2) if left else 0.5))
                continue
            break
    err = ("wall_clock_budget" if isinstance(last_exc, _WallClockExpired)
           else f"{type(last_exc).__name__}: {last_exc}")
    return {"ok": False, "status": -1, "body": b"",
            "ms": round((time.perf_counter() - t) * 1000, 1),
            "error": err, "partial_bytes": partial, "attempts": attempt + 1}


def result(channel, state, reason, elapsed_ms=0, detail=None, evidence=None) -> dict:
    return {"channel": channel, "state": state, "reason": reason,
            "elapsed_ms": round(elapsed_ms, 1), "detail": detail, "evidence": evidence}


# ---------------------------------------------------------------- 上下文与缓存

class Ctx:
    def __init__(self, root: Path, registry: dict):
        self.root = Path(root)
        self.registry = registry

    @property
    def card_dir(self) -> Path:
        return self.root / CARD_DIR_REL

    @property
    def index_path(self) -> Path:
        return self.root / INDEX_REL


def load_registry(root: Path) -> dict:
    return read_json(Path(root) / REGISTRY_REL)


def card_file(ctx: Ctx, sid: str) -> Path:
    return ctx.card_dir / L.card_name(safe_id(sid))


def find_card(ctx: Ctx, sid: str):
    if not ctx.card_dir.exists():
        return None, None
    for p in sorted(ctx.card_dir.glob(L.card_glob())):
        try:
            d = read_json(p)
        except Exception:
            continue
        if same_standard(d.get("standard_id", ""), sid):
            return p, d
    return None, None


def card_state(card: dict) -> str:
    exp = (card.get("cache") or {}).get("expires_at")
    if not exp:
        return "valid"
    try:
        d = date.fromisoformat(exp)
    except Exception:
        return "valid"
    return "valid" if date.today() <= d else "stale"


def lookup_card(root, sid: str) -> dict:
    """只读证据卡查询，供交付出口闸等消费侧调用；不联网、不写盘。

    台账里的编号常不带年号，而证据卡 standard_id 带年号；same_standard 对任一侧缺年号
    按「前缀+编号」匹配，故无需在调用侧另做归一化。
    """
    ctx = Ctx(Path(root), {})
    p, card = find_card(ctx, sid)
    if not card:
        return {"state": "missing", "card": None, "path": None}
    return {"state": card_state(card), "card": card,
            "path": str(p).replace("\\", "/")}


def load_index(ctx: Ctx) -> dict:
    p = ctx.index_path
    if not p.exists():
        return {}
    for base in (Path(__file__).resolve().parent, ctx.root / "程序文件", ROOT_DEFAULT / "程序文件"):
        if (base / "citation_ledger.py").exists():
            try:
                sys.path.insert(0, str(base))
                import citation_ledger  # noqa: F401
                return citation_ledger.parse_index(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


def lookup_in(index: dict, sid: str):
    for k, v in index.items():
        if same_standard(k, sid):
            return {"key": k, **v}
    return None


def index_lookup(ctx: Ctx, sid: str):
    return lookup_in(load_index(ctx), sid)


# ---------------------------------------------------------------- 证据卡

FIELD_ORDER = ["canonical_name", "year", "status", "implemented_date", "issued_date", "announcement_no"]


def _src_rank(s: dict):
    return (LEVEL_RANK.get(s.get("level", ""), -1), s.get("fetched_at", ""))


def field_from_parsed(src: dict, field: str):
    parsed = src.get("parsed") or {}
    if field == "canonical_name":
        return parsed.get("name")
    if field == "year":
        return parsed.get("year") or year_of(parsed.get("code", ""))
    if field == "status":
        st = parsed.get("status")
        if st and parsed.get("doc_date"):
            return f"{st}（源文件编制时点：{parsed['doc_date']}）"
        return st
    if field == "implemented_date":
        return norm_date(parsed.get("implemented_date"))
    if field == "issued_date":
        return norm_date(parsed.get("issued_date"))
    if field == "announcement_no":
        return parsed.get("announcement_no")
    return None


def pick_fields(sources: list[dict]):
    fields, grades = {}, {}
    for f in FIELD_ORDER:
        best = None
        for s in sorted(sources, key=_src_rank, reverse=True):
            v = field_from_parsed(s, f)
            if v:
                best = (v, s)
                break
        fields[f] = best[0] if best else None
        grades[f] = best[1].get("level", "S0") if best else None
    return fields, grades


def merge_sources(old_sources: list[dict], new_sources: list[dict]) -> list[dict]:
    out, seen = [], set()
    for s in list(new_sources) + list(old_sources):
        key = (s.get("carrier"), s.get("sha256"), s.get("page"))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def merge_states(old_states: list[dict], new_states: list[dict]) -> list[dict]:
    """同一通道：证据命中优先于瞬时不可判；旧命中不被本轮取回失败覆盖。"""
    out, by_ch = [], {}
    for s in list(new_states) + list(old_states):
        ch = s.get("channel")
        if ch not in by_ch:
            item = dict(s)
            by_ch[ch] = item
            out.append(item)
            continue
        prev = by_ch[ch]
        if prev.get("state") == STATE_INDETERMINATE and s.get("state") == STATE_HIT:
            merged = dict(s)
            merged["last_attempt"] = {"state": prev.get("state"), "reason": prev.get("reason"),
                                      "elapsed_ms": prev.get("elapsed_ms")}
            by_ch[ch] = merged
            out[out.index(prev)] = merged
        elif prev.get("state") == STATE_HIT and s.get("state") == STATE_INDETERMINATE:
            prev["last_attempt"] = {"state": s.get("state"), "reason": s.get("reason"),
                                    "elapsed_ms": s.get("elapsed_ms")}
    return out


def known_channel_ids(ctx: Ctx):
    ch = (ctx.registry or {}).get("channels") or {}
    if not ch:
        return None
    ids = set(ch.keys())
    gov = ch.get("gov_compilation") or {}
    ids |= {"gov:" + s.get("id", "") for s in (gov.get("sources") or [])}
    return ids


_REFRESH_NOTE_RE = re.compile(r"；机器刷新 \d{4}-\d{2}-\d{2}（新增证据未经独立复核）")


def _carry_review(old_card: dict | None, mode: str) -> str:
    """独立复核标记是证据出处的一部分，机器刷新不得把它抹成 unreviewed。

    保留原标记并追加刷新时点，让读者知道「复核覆盖的是旧证据、本轮新增未经复核」；
    追加项按固定形制先删后加，重复刷新不累积。
    """
    prev = ((old_card or {}).get("review_status") or "").strip()
    if not prev or prev == MACHINE_REVIEW:
        return MACHINE_REVIEW
    base = _REFRESH_NOTE_RE.sub("", prev)
    return f"{base}；机器刷新 {today_str()}（新增证据未经独立复核）"


def build_card(ctx: Ctx, sid: str, name: str | None, results: list[dict],
               old_card: dict | None = None, mode: str = "retrieve",
               started_at: str | None = None, elapsed_ms: float | None = None,
               budget_s: float = 30, corroborate: bool = False) -> dict:
    hits = [r for r in results if r.get("state") == STATE_HIT and r.get("evidence")]
    new_sources = [r["evidence"] for r in hits]
    old_sources = (old_card or {}).get("identity_sources") or []
    sources = merge_sources(old_sources, new_sources) if old_card else new_sources
    new_states = [{k: v for k, v in r.items() if k != "evidence"} for r in results]
    states = merge_states((old_card or {}).get("channel_states") or [], new_states) if old_card else new_states
    known = known_channel_ids(ctx)
    if known is not None:
        states = [s for s in states if s.get("channel") in known]
    fields, grades = pick_fields(sources)

    clause = ((old_card or {}).get("clause_anchor")
              or {"page": None, "quote": None, "carrier_sha256": None})

    # 条文锚也是证据：只看身份源会把已取到 S1 条文的卡在刷新后降级成 S2
    best_level = "S0"
    for lv in [s.get("level", "") for s in sources] + [_grade_token(clause.get("grade"))]:
        if LEVEL_RANK.get(lv, -1) > LEVEL_RANK.get(best_level, -1):
            best_level = lv
    outcome = "hit" if sources else "miss"

    sid_out = (sources[0].get("parsed", {}).get("code") if sources else None) or sid
    if not same_standard(sid_out, sid):
        sid_out = sid

    not_covered = []
    if outcome == "miss":
        not_covered.append("无通道在预算内给出可引用身份证据")
    if not fields.get("issued_date"):
        not_covered.append("发布日期（官方页未取到）")
    elif grades.get("issued_date") == "S0":
        not_covered.append("发布日期官方页（仅 S0 转述）")
    if not fields.get("implemented_date"):
        not_covered.append("实施日期")
    if not fields.get("status"):
        not_covered.append("状态")
    if not clause.get("quote"):
        not_covered.append("条文原文（S1）")
    elif "转引" in (clause.get("limitation") or ""):
        not_covered.append("标准正文原页（条文经转引）")
    if any(s.get("reason") == "record_missing" for s in states):
        not_covered.append("发布机关原始公告页（站内索引未命中）")
    if fields.get("status"):
        not_covered.append("今日仍现行（仅有源文件编制时点状态）")

    low = [f for f, g in grades.items() if g == "S0"]
    ttl_cfg = ctx.registry.get("ttl_days") or {}
    ttl_days = int(ttl_cfg.get("hit" if outcome == "hit" else "miss", 90 if outcome == "hit" else 7))
    created = ((old_card or {}).get("cache") or {}).get("created_at") or now_iso()
    verified = today_str()
    expires = (date.today() + timedelta(days=ttl_days)).isoformat()

    return {
        "schema": SCHEMA,
        "standard_id": sid_out,
        "prefix_family": prefix_of(sid),
        "number_core": core_of(sid),
        "name_query": name,
        "canonical_name": fields.get("canonical_name"),
        "year": fields.get("year") or year_of(sid),
        "status": fields.get("status"),
        "issued_date": fields.get("issued_date"),
        "implemented_date": fields.get("implemented_date"),
        "field_grades": {**{k: grades.get(k) for k in FIELD_ORDER},
                         "standard_id": best_level if sources else None,
                         "clause": (clause.get("grade") or "S1") if clause.get("quote") else "未取得"},
        "identity_sources": sources,
        "clause_anchor": clause,
        "citable_level": best_level if sources else "S0",
        "not_covered": not_covered,
        "low_grade_fields": low,
        "repo_citation": (old_card or {}).get("repo_citation"),
        "outcome": outcome,
        "cache": {
            "created_at": created,
            "updated_at": now_iso(),
            "verified_on": verified,
            "ttl_days": ttl_days,
            "expires_at": expires,
        },
        "last_run": {
            "mode": mode,
            "started_at": started_at or now_iso(),
            "elapsed_ms": elapsed_ms,
            "budget_s": budget_s,
            "corroborate": corroborate,
        },
        "channel_states": states,
        "review_status": _carry_review(old_card, mode),
        "generated_by": "on_demand_retrieval.py",
        "generated_at": now_iso(),
    }


# ---------------------------------------------------------------- 通道分类（纯函数，供 selftest）

def classify_samr(sid: str, health: dict, target: dict, family: dict | None) -> dict:
    if not health.get("ok"):
        return result("samr", STATE_INDETERMINATE, "health_fetch_failed", health.get("ms", 0), health.get("error"))
    if not any(row_code(r) for r in health.get("rows") or []):
        return result("samr", STATE_INDETERMINATE, "health_control_empty", health.get("ms", 0))
    hc = health.get("control")
    if hc and not any(core_match(row_code(r), hc) for r in health.get("rows") or []):
        return result("samr", STATE_INDETERMINATE, "health_control_mismatch", health.get("ms", 0))
    trows = target.get("rows") or []
    hit = [r for r in trows if same_standard(row_code(r), sid)]
    if target.get("ok") and hit:
        r = hit[0]
        ev = {
            "channel": "samr:gbQueryPage",
            "level": "S2",
            "carrier": target.get("url"),
            "bytes": target.get("bytes"),
            "sha256": target.get("sha256"),
            "page": None,
            "row": f"{row_code(r)}｜{row_name(r)}",
            "parsed": {
                "code": row_code(r),
                "name": row_name(r),
                "status": r.get("STATE"),
                "issued_date": r.get("ISSUE_DATE"),
                "implemented_date": r.get("ACT_DATE"),
            },
            "fetched_at": now_iso(),
            "elapsed_ms": target.get("ms", 0),
        }
        return result("samr", STATE_HIT, "target_row", target.get("ms", 0), evidence=ev)
    if not target.get("ok"):
        return result("samr", STATE_INDETERMINATE, "target_fetch_failed", target.get("ms", 0), target.get("error"))
    same_base = [r for r in trows if id_key(row_code(r))[0] == id_key(sid)[0]]
    if same_base:
        return result("samr", STATE_FILTERED, "year_mismatch", target.get("ms", 0),
                      [row_code(r) for r in same_base[:5]])
    if family is not None:
        if not family.get("ok"):
            return result("samr", STATE_INDETERMINATE, "family_fetch_failed", family.get("ms", 0), family.get("error"))
        fc = family.get("control", "")
        fam_prefix = family_of(sid)
        if not any(norm_id(row_code(r)).startswith(fam_prefix) and core_match(row_code(r), fc)
                   for r in family.get("rows") or []):
            return result("samr", STATE_NOT_COVERED, "family_control_empty", family.get("ms", 0))
    return result("samr", STATE_FILTERED, "target_absent", target.get("ms", 0))


def classify_openstd(sid: str, health: dict, target: dict, family: dict | None) -> dict:
    if not health.get("ok"):
        return result("openstd", STATE_INDETERMINATE, "health_fetch_failed", health.get("ms", 0), health.get("error"))
    if not health.get("showinfo"):
        return result("openstd", STATE_INDETERMINATE, "health_control_empty", health.get("ms", 0))
    hc = health.get("control")
    if hc and not any(core_match(c, hc) for c in health.get("codes") or []):
        return result("openstd", STATE_INDETERMINATE, "health_control_mismatch", health.get("ms", 0))
    codes = target.get("codes") or []
    hit = [c for c in codes if same_standard(c, sid)]
    if target.get("ok") and hit:
        ev = {
            "channel": "openstd:std_list",
            "level": "S2",
            "carrier": target.get("url"),
            "bytes": target.get("bytes"),
            "sha256": target.get("sha256"),
            "page": None,
            "row": hit[0],
            "parsed": {"code": hit[0]},
            "fetched_at": now_iso(),
            "elapsed_ms": target.get("ms", 0),
        }
        return result("openstd", STATE_HIT, "target_code", target.get("ms", 0), evidence=ev)
    if not target.get("ok"):
        return result("openstd", STATE_INDETERMINATE, "target_fetch_failed", target.get("ms", 0), target.get("error"))
    if family is not None:
        if not family.get("ok"):
            return result("openstd", STATE_INDETERMINATE, "family_fetch_failed", family.get("ms", 0), family.get("error"))
        fam_prefix = family_of(sid)
        if not any(norm_id(c).startswith(fam_prefix) for c in family.get("codes") or []):
            return result("openstd", STATE_NOT_COVERED, "family_control_empty", family.get("ms", 0))
    return result("openstd", STATE_FILTERED, "target_absent", target.get("ms", 0))


def classify_hbba(sid: str, health: dict, target: dict, family: dict | None, ghost: dict | None) -> dict:
    if not health.get("ok"):
        return result("hbba", STATE_INDETERMINATE, "health_fetch_failed", health.get("ms", 0), health.get("error"))
    if not health.get("records"):
        return result("hbba", STATE_INDETERMINATE, "health_control_empty", health.get("ms", 0))
    hc = health.get("control")
    if hc and not any(core_match(r.get("code", ""), hc) for r in health.get("records") or []):
        return result("hbba", STATE_INDETERMINATE, "health_control_mismatch", health.get("ms", 0))
    if ghost is not None and ghost.get("ok") and ghost.get("records"):
        return result("hbba", STATE_INDETERMINATE, "ghost_not_empty", ghost.get("ms", 0),
                      [r.get("code") for r in ghost.get("records") or []])
    for r in target.get("records") or []:
        if same_standard(r.get("code", ""), sid):
            ev = {
                "channel": "hbba:stdQueryList",
                "level": "S2",
                "carrier": target.get("url"),
                "bytes": target.get("bytes"),
                "sha256": target.get("sha256"),
                "page": None,
                "row": f"{r.get('code')}｜{r.get('chName')}｜{r.get('status')}",
                "parsed": {
                    "code": r.get("code"),
                    "name": r.get("chName"),
                    "status": r.get("status"),
                    "issued_date": ms_to_date(r.get("issueDate")),
                    "implemented_date": ms_to_date(r.get("actDate")),
                },
                "fetched_at": now_iso(),
                "elapsed_ms": target.get("ms", 0),
            }
            return result("hbba", STATE_HIT, "target_record", target.get("ms", 0), evidence=ev)
    if not target.get("ok"):
        return result("hbba", STATE_INDETERMINATE, "target_fetch_failed", target.get("ms", 0), target.get("error"))
    if family is not None:
        if not family.get("ok"):
            return result("hbba", STATE_INDETERMINATE, "family_fetch_failed", family.get("ms", 0), family.get("error"))
        fc = family.get("control", "")
        if not any(same_standard(r.get("code", ""), fc) for r in family.get("records") or []):
            return result("hbba", STATE_NOT_COVERED, "family_control_empty", family.get("ms", 0))
    return result("hbba", STATE_FILTERED, "target_absent", target.get("ms", 0))


def classify_mohurd(sid: str, controls_ok: bool, target_found: bool, detail: dict | None) -> dict:
    if not controls_ok:
        return result("mohurd", STATE_INDETERMINATE, "control_failed")
    if target_found and detail and detail.get("ok"):
        parsed = detail.get("parsed") or {}
        ev = {
            "channel": "mohurd:announcement",
            "level": "S2",
            "carrier": detail.get("url"),
            "bytes": detail.get("bytes"),
            "sha256": detail.get("sha256"),
            "page": None,
            "row": parsed.get("code"),
            "parsed": parsed,
            "fetched_at": now_iso(),
            "elapsed_ms": detail.get("ms", 0),
        }
        return result("mohurd", STATE_HIT, "detail_verified", detail.get("ms", 0), evidence=ev)
    if target_found and detail and not detail.get("ok"):
        return result("mohurd", STATE_INDETERMINATE, "detail_mismatch", detail.get("ms", 0), detail.get("plain_head"))
    return result("mohurd", STATE_INDETERMINATE, "record_missing")


# ---------------------------------------------------------------- 通道探针

def row_code(r: dict) -> str:
    return (r.get("C_STD_CODE") or "").replace("<sacinfo>", "").replace("</sacinfo>", "")


def row_name(r: dict) -> str:
    return r.get("C_C_NAME") or ""


def _body_meta(r: dict) -> dict:
    b = r.get("body") or b""
    return {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}


def fetch_gbquery(cfg: dict, q: str, budget: Budget) -> dict:
    url = cfg["endpoint"].format(q=urllib.parse.quote(q))
    r = budget.call(url, cfg.get("referer"))
    if not r["ok"]:
        return {"ok": False, "rows": [], "ms": r["ms"], "error": r["error"], "url": url}
    try:
        d = json.loads(r["body"].decode("utf-8", "replace"))
    except Exception as exc:
        return {"ok": False, "rows": [], "ms": r["ms"], "error": f"json: {exc}", "url": url}
    return {"ok": True, "rows": d.get("rows") or [], "ms": r["ms"], "error": None, "url": url, **_body_meta(r)}


def probe_samr(ctx: Ctx, sid: str, budget: Budget) -> list[dict]:
    cfg = ctx.registry["channels"]["samr"]
    health = fetch_gbquery(cfg, cfg["health_control"], budget)
    health["control"] = cfg["health_control"]
    target = fetch_gbquery(cfg, core_of(sid), budget)
    fam = None
    fc = (cfg.get("family_controls") or {}).get(family_of(sid))
    if fc:
        fam = fetch_gbquery(cfg, fc, budget)
        fam["control"] = fc
    return [classify_samr(sid, health, target, fam)]


def fetch_openstd(cfg: dict, q: str, budget: Budget) -> dict:
    url = cfg["endpoint"].format(q=urllib.parse.quote(q))
    r = budget.call(url, cfg.get("referer"))
    if not r["ok"]:
        return {"ok": False, "codes": [], "showinfo": 0, "ms": r["ms"], "error": r["error"], "url": url}
    html = r["body"].decode("utf-8", "replace")
    return {"ok": True, "codes": extract_codes(html),
            "showinfo": len(re.findall(r"showInfo\('([0-9A-Fa-f]{16,})'\)", html)),
            "ms": r["ms"], "error": None, "url": url, **_body_meta(r)}


def probe_openstd(ctx: Ctx, sid: str, budget: Budget) -> list[dict]:
    cfg = ctx.registry["channels"]["openstd"]
    health = fetch_openstd(cfg, cfg["health_control"], budget)
    health["control"] = cfg["health_control"]
    target = fetch_openstd(cfg, core_of(sid), budget)
    fam = None
    fc = (cfg.get("family_controls") or {}).get(family_of(sid))
    if fc:
        fam = fetch_openstd(cfg, fc, budget)
        fam["control"] = fc
    return [classify_openstd(sid, health, target, fam)]


def ms_to_date(ms):
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000).date().isoformat()
    except Exception:
        return None


def fetch_hbba(cfg: dict, key: str, budget: Budget) -> dict:
    data = urllib.parse.urlencode({"current": 1, "size": 20, "key": key}).encode()
    r = budget.call(cfg["endpoint"], cfg.get("referer"), data=data)
    if not r["ok"]:
        return {"ok": False, "records": [], "ms": r["ms"], "error": r["error"], "url": cfg["endpoint"]}
    try:
        d = json.loads(r["body"].decode("utf-8", "replace"))
    except Exception as exc:
        return {"ok": False, "records": [], "ms": r["ms"], "error": f"json: {exc}", "url": cfg["endpoint"]}
    return {"ok": True, "records": d.get("records") or [], "ms": r["ms"], "error": None,
            "url": cfg["endpoint"], **_body_meta(r)}


def probe_hbba(ctx: Ctx, sid: str, budget: Budget) -> list[dict]:
    cfg = ctx.registry["channels"]["hbba"]
    health = fetch_hbba(cfg, cfg["health_control"], budget)
    health["control"] = cfg["health_control"]
    target = fetch_hbba(cfg, sid, budget)
    ghost_key = id_key(sid)[0] + cfg.get("ghost_suffix", "-2099")
    ghost = fetch_hbba(cfg, ghost_key, budget)
    fam = None
    fc = (cfg.get("family_controls") or {}).get(family_of(sid))
    if fc:
        fam = fetch_hbba(cfg, fc, budget)
        fam["control"] = fc
    return [classify_hbba(sid, health, target, fam, ghost)]


def mohurd_search(ctx: Ctx, cfg: dict, q: str, budget: Budget) -> dict:
    url = cfg["search"].format(q=urllib.parse.quote(q))
    r = budget.call(url, cfg.get("search_referer"))
    if not r["ok"]:
        return {"ok": False, "items": [], "ms": r["ms"], "error": r["error"], "url": url}
    try:
        d = json.loads(r["body"].decode("utf-8", "replace"))
    except Exception as exc:
        return {"ok": False, "items": [], "ms": r["ms"], "error": f"json: {exc}", "url": url}
    items = ((d.get("data") or {}).get("searchResult") or {}).get("result") or []
    return {"ok": True, "items": items, "ms": r["ms"], "error": None, "url": url, **_body_meta(r)}


def mohurd_form(ctx: Ctx, cfg: dict, title: str, budget: Budget, file_no: str = "") -> dict:
    search = json.dumps({
        "fileNumberName": file_no, "createDate": "", "typeId": "", "gwlx": "", "zyfl": "",
        "publishGroupId": "", "combinationId": "", "title_text": title,
        "searchKeywordRange": "createDateDesc", "order": "createDateDesc",
    }, ensure_ascii=False)
    param = json.dumps({"pageNo": 1, "loadEnabled": True, "search": search, "pageSize": "30"}, ensure_ascii=False)
    url = cfg["form"] + "?" + urllib.parse.urlencode({**cfg["form_params"], "paramJson": param})
    r = budget.call(url, cfg.get("form_referer"))
    if not r["ok"]:
        return {"ok": False, "rows": [], "ms": r["ms"], "error": r["error"], "url": url}
    try:
        html = json.loads(r["body"].decode("utf-8", "replace"))["data"]["html"]
    except Exception as exc:
        return {"ok": False, "rows": [], "ms": r["ms"], "error": f"json: {exc}", "url": url}
    rows = []
    for m in re.finditer(r'<a[^>]*href="(/gongkai/zc/wjk/art/[^"]+)"[^>]*>(.*?)</a>', html, re.S):
        t = strip_tags(m.group(2))
        if t:
            rows.append((t, m.group(1)))
    return {"ok": True, "rows": rows, "ms": r["ms"], "error": None, "url": url, **_body_meta(r)}


def parse_mohurd_detail(body: bytes, sid: str, name: str | None) -> dict:
    plain = strip_tags(body.decode("utf-8", "replace"))
    n = norm_id(plain)
    has_id = norm_id(sid) in n or (bool(name) and norm_id(name) in n)
    fields = {}
    for label in ("文号", "发文日期", "实施日期", "索引号"):
        m = re.search(label + r"\s*[:：]?\s*([^\s]{2,40})", plain)
        if m:
            fields[label] = m.group(1)
    return {"ok": has_id, "fields": fields, "plain_head": plain[:200]}


def probe_mohurd(ctx: Ctx, sid: str, name: str | None, budget: Budget) -> list[dict]:
    t0 = time.perf_counter()
    cfg = ctx.registry["channels"]["mohurd"]
    hc = cfg.get("health_controls") or {}
    h_search = mohurd_search(ctx, cfg, hc.get("search", "建筑环境通用规范"), budget)
    h_form = mohurd_form(ctx, cfg, hc.get("form", "建筑环境通用规范"), budget)
    h_no = mohurd_form(ctx, cfg, "", budget, file_no=hc.get("file_no", "1292")) if hc.get("file_no") else {"ok": True, "rows": [("ok", "")]}
    controls_ok = bool(h_search.get("ok") and h_search.get("items") and h_form.get("ok") and h_form.get("rows") and h_no.get("ok"))

    target_hits = []
    for q in [name, sid]:
        if not q:
            continue
        r = mohurd_search(ctx, cfg, q, budget)
        for it in r.get("items") or []:
            plain = strip_tags(it)
            if norm_id(sid) in norm_id(plain) or (name and norm_id(name) in norm_id(plain)):
                for h in re.findall(r'href="(https://www\.mohurd\.gov\.cn/gongkai/zc/wjk/art/[^"]+)"', it):
                    target_hits.append(h)
    if not target_hits and name:
        r = mohurd_form(ctx, cfg, name, budget)
        for title, href in r.get("rows") or []:
            if norm_id(sid) in norm_id(title) or norm_id(name) in norm_id(title):
                target_hits.append("https://www.mohurd.gov.cn" + href)

    detail = None
    if target_hits:
        url = target_hits[0]
        r = budget.call(url, cfg.get("search_referer"))
        if r["ok"]:
            parsed = parse_mohurd_detail(r["body"], sid, name)
            detail = {"ok": parsed["ok"], "parsed": {
                "code": sid, "name": name,
                "issued_date": parsed["fields"].get("发文日期"),
                "implemented_date": parsed["fields"].get("实施日期"),
                "announcement_no": parsed["fields"].get("文号"),
            }, "plain_head": parsed["plain_head"], "url": url,
                "ms": r["ms"], **_body_meta(r)}
        else:
            detail = {"ok": False, "plain_head": r.get("error"), "url": url, "ms": r["ms"]}
    res = classify_mohurd(sid, controls_ok, bool(target_hits), detail)
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return [res]


SKIP_LINE_RE = re.compile(
    r"^\s*(\d+|\[\d+\][\d.\-]*|行业标准|行标|国家标准|国标|地方标准|地标|团体标准|现行|在编|拟编|√|—|-)\s*$"
)


def parse_compilation_window(lines: list[str], idx: int, sid: str) -> dict:
    name = None
    for j in range(idx - 1, max(-1, idx - 8), -1):
        ln = (lines[j] or "").strip()
        if not ln or CODE_RE.search(ln) or SKIP_LINE_RE.match(ln):
            continue
        if re.search(r"[\u4e00-\u9fff]", ln):
            name = ln
            break
    after = [x.strip() for x in lines[idx + 1:idx + 10] if (x or "").strip()]
    category = next((ln for ln in after if re.search(r"行业标准|行标|国家标准|国标|地方标准|地标|团体标准", ln)), None)
    date_m = re.search(r"\d{4}/\d{2}/\d{2}", " ".join(after))
    status = next((ln for ln in after if re.search(r"现行|废止|在编|拟编", ln)), None)
    if not status and "√" in " ".join(after):
        header = " ".join(lines[:15])
        if "现行" in header:
            status = "现行（√）"
    row = "｜".join([ln.strip() for ln in lines[max(0, idx - 2):idx + 6] if (ln or "").strip()])
    return {"code": lines[idx].strip(), "name": name, "category": category,
            "implemented_date": norm_date(date_m.group(0)) if date_m else None,
            "status": status, "row": row}


def probe_gov_compilation(ctx: Ctx, sid: str, budget: Budget, corroborate: bool = False,
                          allow_large: bool = False) -> list[dict]:
    cfg = ctx.registry["channels"]["gov_compilation"]
    cap = int((ctx.registry.get("budgets") or {}).get("large_carrier_bytes", 8 * 1024 * 1024))
    out, any_fetch = [], False
    for src in cfg.get("sources") or []:
        if int(src.get("bytes") or 0) > cap and not allow_large:
            out.append(result("gov:" + src["id"], STATE_INDETERMINATE, "carrier_too_large", 0,
                              {"bytes": src.get("bytes"), "cap": cap}))
            continue
        r = budget.call(src["url"])
        if not r["ok"]:
            reason = ("wall_clock_budget" if r.get("error") == "wall_clock_budget" else "fetch_failed")
            out.append(result("gov:" + src["id"], STATE_INDETERMINATE, reason, r["ms"], r["error"]))
            continue
        body = r["body"]
        any_fetch = True
        if len(body) != src.get("bytes") or hashlib.sha256(body).hexdigest() != src.get("sha256"):
            out.append(result("gov:" + src["id"], STATE_INDETERMINATE, "bytes_or_sha_mismatch", r["ms"]))
            continue
        try:
            import fitz  # PyMuPDF
        except Exception as exc:
            out.append(result("gov:" + src["id"], STATE_INDETERMINATE, "pymupdf_missing", r["ms"], str(exc)))
            continue
        try:
            doc = fitz.open(stream=body, filetype="pdf")
            hit_ev = None
            for page in doc:
                lines = [ln.strip() for ln in page.get_text().splitlines() if ln.strip()]
                idx = next((i for i, ln in enumerate(lines) if same_standard(ln, sid)), None)
                if idx is None:
                    for i, ln in enumerate(lines):
                        m = CODE_RE.search(ln)
                        if m and same_standard(m.group(0), sid):
                            idx = i
                            break
                if idx is not None:
                    parsed = parse_compilation_window(lines, idx, sid)
                    hit_ev = {
                        "channel": src["name"],
                        "level": src.get("level", "S2-"),
                        "carrier": src["url"],
                        "bytes": len(body),
                        "sha256": src["sha256"],
                        "page": f"p{page.number + 1}",
                        "row": parsed.get("row"),
                        "parsed": {**parsed, "doc_date": src.get("doc_date")},
                        "fetched_at": now_iso(),
                        "elapsed_ms": r["ms"],
                    }
                    break
            doc.close()
            if hit_ev:
                out.append(result("gov:" + src["id"], STATE_HIT, "row_found", r["ms"], evidence=hit_ev))
                if not corroborate:
                    break
            else:
                out.append(result("gov:" + src["id"], STATE_FILTERED, "row_absent", r["ms"]))
        except Exception as exc:
            out.append(result("gov:" + src["id"], STATE_INDETERMINATE, "pdf_parse_failed", r["ms"],
                              f"{type(exc).__name__}: {exc}"))
    return out


# ---------------------------------------------------------------- 路由与检索

class Budget:
    """通道预算：socket＝单次操作超时；墙钟 deadline 由**本通道各请求共享剩余额度**。

    分档依据（实测）＝小接口 0.15—4.91 s／次、CNIS 427 ms；省级体系件 PDF 山东 27.84 s
    ＋抽行≈28.3 s、新疆 2.68 s；北京 16.4 MB 30 s 只下 3.2 MB（大载体不进常态路由）。
    """

    def __init__(self, socket_s: float, wall_s: float | None, retries: int = 1):
        self.socket = float(socket_s)
        self.deadline = time.perf_counter() + wall_s if wall_s else None
        self.retries = int(retries)

    def left(self):
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.perf_counter())

    def call(self, url, referer=None, data=None, retries=None) -> dict:
        return http_get(url, referer, self.socket, data=data,
                        retries=self.retries if retries is None else retries,
                        wall_budget=self.left())


def channel_budget(ctx: Ctx, ch: str, override: float | None = None) -> Budget:
    cfg = (ctx.registry.get("budgets") or {})
    tier = cfg.get(ch) or cfg.get("default") or {}
    socket_s = float(tier.get("socket_s", 8))
    wall_s = float(override) if override else float(tier.get("wall_s", 12))
    return Budget(socket_s, wall_s, int(cfg.get("retry_attempts", 1)))


def route_for(ctx: Ctx, sid: str) -> list[str]:
    fam = family_of(sid)
    routes = ctx.registry.get("routes") or {}
    return routes.get(fam) or ["gov_compilation", "mohurd", "hbba", "samr", "openstd"]


def _has_s2(results: list[dict]) -> bool:
    return any(r.get("state") == STATE_HIT and (r.get("evidence") or {}).get("level") == "S2" for r in results)


def run_channels(ctx: Ctx, sid: str, name: str | None, budget: float | None = None,
                 total_budget: float = 120, corroborate: bool = False,
                 allow_large: bool = False) -> tuple[list[dict], float]:
    """budget＝每通道**墙钟**预算覆盖值（None＝按注册表分档）；total_budget＝整轮墙钟。"""
    start = time.perf_counter()
    results = []
    for ch in route_for(ctx, sid):
        if time.perf_counter() - start >= total_budget:
            results.append(result(ch, STATE_INDETERMINATE, "total_budget", 0))
            break
        b = channel_budget(ctx, ch, budget)
        if b.deadline is not None:
            b.deadline = min(b.deadline, start + total_budget)
        if ch == "gov_compilation":
            rs = probe_gov_compilation(ctx, sid, b, corroborate, allow_large)
            results.extend(rs)
        elif ch == "samr":
            results.extend(probe_samr(ctx, sid, b))
        elif ch == "openstd":
            results.extend(probe_openstd(ctx, sid, b))
        elif ch == "hbba":
            results.extend(probe_hbba(ctx, sid, b))
        elif ch == "mohurd":
            results.extend(probe_mohurd(ctx, sid, name, b))
        if not corroborate and _has_s2(results):
            break
    return results, round((time.perf_counter() - start) * 1000, 1)


# ---------------------------------------------------------------- 缺口冒泡

def compute_gaps(ctx: Ctx, ledger_path, limit: int | None = None) -> dict:
    ledger = read_json(ledger_path)
    index = load_index(ctx)
    rows = ledger.get("rows") or []
    tasks = []
    for r in rows:
        key = r.get("key", "")
        ient = lookup_in(index, key)
        p, card = find_card(ctx, key)
        valid = bool(card) and card_state(card) == "valid"
        if ient and valid:
            state = "indexed_cached"
        elif ient:
            state = "indexed_no_card"
        elif valid:
            state = "cached"
        elif card:
            state = "card_stale"
        else:
            state = "gap"
        first = (r.get("hits") or [{}])[0]
        tasks.append({
            "key": key,
            "cited_years": r.get("cited_years"),
            "count": r.get("count"),
            "anchor": f"{first.get('file','')}:{first.get('line','')}",
            "state": state,
            "index_status": (ient or {}).get("status"),
            "card_level": (card or {}).get("citable_level"),
        })
    tasks.sort(key=lambda x: (-int(x.get("count") or 0), x.get("key", "")))
    summary = {}
    for t in tasks:
        summary[t["state"]] = summary.get(t["state"], 0) + 1
    if limit:
        tasks = tasks[:limit]
    return {"ledger": str(ledger_path), "total": len(rows), "summary": summary, "tasks": tasks}


# ---------------------------------------------------------------- 命令

def cmd_query(ctx: Ctx, sid: str) -> int:
    t = time.perf_counter()
    p, card = find_card(ctx, sid)
    ient = index_lookup(ctx, sid)
    elapsed = round((time.perf_counter() - t) * 1000, 1)
    if not card:
        out = {"standard_id": sid, "cache_hit": False, "stale": False,
               "index_hit": bool(ient), "index": ient, "elapsed_ms": elapsed,
               "within_budget": elapsed <= 2000}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1
    state = card_state(card)
    out = {
        "standard_id": card.get("standard_id"),
        "cache_hit": state == "valid",
        "stale": state == "stale",
        "card": str(p).replace("\\", "/"),
        "citable_level": card.get("citable_level"),
        "canonical_name": card.get("canonical_name"),
        "status": card.get("status"),
        "issued_date": card.get("issued_date"),
        "implemented_date": card.get("implemented_date"),
        "field_grades": card.get("field_grades"),
        "not_covered": card.get("not_covered"),
        "review_status": card.get("review_status"),
        "verified_on": (card.get("cache") or {}).get("verified_on"),
        "expires_at": (card.get("cache") or {}).get("expires_at"),
        "index_hit": bool(ient),
        "elapsed_ms": elapsed,
        "within_budget": elapsed <= 2000,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if state == "valid" else 1


def cmd_retrieve(ctx: Ctx, sid: str, name: str | None, budget: float | None, total_budget: float,
                 refresh: bool, merge: bool, corroborate: bool, write: bool,
                 allow_large: bool = False) -> int:
    started = now_iso()
    t = time.perf_counter()
    p, old = find_card(ctx, sid)
    if not refresh and old and card_state(old) == "valid":
        out = {
            "standard_id": old.get("standard_id"), "cache_hit": True, "stale": False,
            "card": str(p).replace("\\", "/"), "citable_level": old.get("citable_level"),
            "canonical_name": old.get("canonical_name"), "status": old.get("status"),
            "elapsed_ms": round((time.perf_counter() - t) * 1000, 1),
            "within_budget": True, "mode": "cache",
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    results, _ = run_channels(ctx, sid, name, budget, total_budget, corroborate, allow_large)
    card = build_card(ctx, sid, name, results, old_card=old if merge else None,
                      mode="refresh" if refresh else "retrieve", started_at=started,
                      elapsed_ms=round((time.perf_counter() - t) * 1000, 1), budget_s=budget,
                      corroborate=corroborate)
    card["last_run"]["budget_tiers"] = {
        ch: (budget if budget else float(((ctx.registry.get("budgets") or {}).get(ch)
                                          or (ctx.registry.get("budgets") or {}).get("default")
                                          or {}).get("wall_s", 12)))
        for ch in route_for(ctx, sid)}
    card["last_run"]["allow_large"] = allow_large
    out_path = None
    if write:
        out_path = card_file(ctx, sid)
        write_json(out_path, card)
    out = {
        "standard_id": card.get("standard_id"), "outcome": card.get("outcome"),
        "cache_hit": False, "citable_level": card.get("citable_level"),
        "canonical_name": card.get("canonical_name"), "status": card.get("status"),
        "issued_date": card.get("issued_date"), "implemented_date": card.get("implemented_date"),
        "field_grades": card.get("field_grades"), "not_covered": card.get("not_covered"),
        "channel_states": card.get("channel_states"),
        "elapsed_ms": card.get("last_run", {}).get("elapsed_ms"),
        "card": str(out_path).replace("\\", "/") if out_path else None,
        "mode": card.get("last_run", {}).get("mode"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if card.get("outcome") == "hit" else 1


def cmd_channels(ctx: Ctx) -> int:
    channels = {}
    for k, v in (ctx.registry.get("channels") or {}).items():
        channels[k] = {
            "boundary": v.get("boundary"),
            "health": v.get("health_control") or v.get("health_controls"),
            "family_controls": v.get("family_controls"),
            "sources": [s.get("id") for s in (v.get("sources") or [])] or None,
        }
    print(json.dumps({"schema": ctx.registry.get("schema"), "routes": ctx.registry.get("routes"),
                      "channels": channels}, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- 离线自检

def _selftest() -> int:
    import tempfile

    ok = 0
    total = 0

    def check(name, cond, detail=""):
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
            print(f"  OK   {name}")
        else:
            print(f"  FAIL {name} :: {detail}")

    check("same_standard 空格/斜杠", same_standard("JGJ/T398-2017", "JGJ/T 398-2017"))
    check("same_standard 年号缺省", same_standard("JGJ/T398", "JGJ/T 398-2017"))
    check("same_standard 年号不同", not same_standard("JGJ/T398-2018", "JGJ/T 398-2017"))
    check("前缀不同不匹配", not same_standard("JGJ398-2017", "JGJ/T 398-2017"))
    check("safe_id", safe_id("JGJ/T 398-2017") == "JGJ-T-398-2017", safe_id("JGJ/T 398-2017"))
    check("card_state valid", card_state({"cache": {"expires_at": "2099-01-01"}}) == "valid")
    check("card_state stale", card_state({"cache": {"expires_at": "2000-01-01"}}) == "stale")

    health = {"ok": True, "rows": [{"C_STD_CODE": "GB/T 9978-1999"}], "ms": 1}
    target = {"ok": True, "rows": [{"C_STD_CODE": "GB/T 398-2018"}], "ms": 1}
    fam = {"ok": True, "rows": [], "control": "491", "ms": 1}
    r = classify_samr("JGJ/T 398-2017", health, target, fam)
    check("samr JGJ 库不覆盖", r["state"] == STATE_NOT_COVERED, r)
    fam_gb491 = {"ok": True, "rows": [{"C_STD_CODE": "GB/T 491-1987"}], "control": "491", "ms": 1}
    r = classify_samr("JGJ/T 398-2017", health, target, fam_gb491)
    check("samr 同数字异前缀不冒充族控制", r["state"] == STATE_NOT_COVERED, r)
    target_hit = {"ok": True, "ms": 1, "url": "u", "bytes": 1, "sha256": "s",
                  "rows": [{"C_STD_CODE": "JGJ/T 398-2017", "C_C_NAME": "装配式住宅建筑设计标准", "STATE": "现行"}]}
    r = classify_samr("JGJ/T 398-2017", health, target_hit, fam)
    check("samr 命中", r["state"] == STATE_HIT and r["evidence"]["parsed"]["code"] == "JGJ/T 398-2017", r)
    r = classify_samr("JGJ/T 398-2017", {"ok": False, "rows": [], "error": "timeout"}, target, fam)
    check("samr 控制枚失败=不可判", r["state"] == STATE_INDETERMINATE, r)
    r = classify_samr("JGJ/T 398-2017",
                      {"ok": True, "rows": [{"C_STD_CODE": "GB/T 9999-2000"}], "control": "9978", "ms": 1},
                      target, fam)
    check("samr 控制枚不匹配=不可判", r["state"] == STATE_INDETERMINATE and r["reason"] == "health_control_mismatch", r)
    r = classify_samr("GB/T 398-2017", health, target, fam)
    check("samr 年号不同=过滤后无记录", r["state"] == STATE_FILTERED and r["reason"] == "year_mismatch", r)

    oh = {"ok": True, "codes": ["GB/T 9775-2025"], "showinfo": 15, "ms": 1}
    ot = {"ok": True, "codes": ["GB/T 398-2018"], "showinfo": 30, "ms": 1, "url": "u", "bytes": 1, "sha256": "s"}
    of = {"ok": True, "codes": ["GB/T 491-1987"], "ms": 1}
    r = classify_openstd("JGJ/T 398-2017", oh, ot, of)
    check("openstd JGJ 库不覆盖", r["state"] == STATE_NOT_COVERED, r)

    hh = {"ok": True, "records": [{"code": "JC/T 985-2017"}], "ms": 1}
    ht = {"ok": True, "records": [], "ms": 1, "url": "u", "bytes": 1, "sha256": "s"}
    hf = {"ok": True, "records": [], "control": "JGJ/T 491", "ms": 1}
    hg = {"ok": True, "records": [], "ms": 1}
    r = classify_hbba("JGJ/T 398-2017", hh, ht, hf, hg)
    check("hbba JGJ 库不覆盖", r["state"] == STATE_NOT_COVERED, r)
    r = classify_hbba("JGJ/T 398-2017", hh, ht, hf, {"ok": True, "records": [{"code": "x"}], "ms": 1})
    check("hbba ghost 非空=不可判", r["state"] == STATE_INDETERMINATE and r["reason"] == "ghost_not_empty", r)

    r = classify_mohurd("JGJ/T 398-2017", True, False, None)
    check("mohurd 记录缺失", r["state"] == STATE_INDETERMINATE and r["reason"] == "record_missing", r)

    sd_lines = ["24", "续表3.3", "序号", "体系标准", "编号", "标准名称", "标准编号", "标准类别",
                "实施日期", "编制状态", "备注", "67", "0201C-40", "装配式住宅建筑设计标准",
                "JGJ/T 398-2017", "行业标准", "2018/06/01", "现行"]
    p = parse_compilation_window(sd_lines, 14, "JGJ/T 398-2017")
    check("山东行抽取", p["name"] == "装配式住宅建筑设计标准" and p["implemented_date"] == "2018-06-01"
          and p["status"] == "现行", p)

    xj_lines = ["45", "( 3 )", "标准项目：", "体系编码", "标准名称", "标准编号", "编制出版状况",
                "备注", "现行", "在编", "拟编", "[01].6.1-10", "装配式住宅建筑设计标准",
                "JGJ/T 398-2017", "√", "行标"]
    p = parse_compilation_window(xj_lines, 13, "JGJ/T 398-2017")
    check("新疆行抽取", p["name"] == "装配式住宅建筑设计标准" and (p["status"] or "").startswith("现行"), p)

    tmp = Path(tempfile.mkdtemp())
    # 根取 tmp/skill：随包布局的 INDEX_REL 含 `../`，根若直接取 tmp 会把夹具写到 tmp 之外
    ctx = Ctx(tmp / "skill", {"ttl_days": {"hit": 90, "miss": 7}})
    ev = {"channel": "sd", "level": "S2", "carrier": "u", "sha256": "s", "page": "p28",
          "parsed": {"code": "JGJ/T 398-2017", "name": "装配式住宅建筑设计标准", "status": "现行",
                     "implemented_date": "2018-06-01", "doc_date": "2025-10"},
          "fetched_at": now_iso(), "elapsed_ms": 10}
    card = build_card(ctx, "JGJ/T 398-2017", "装配式住宅建筑设计标准",
                      [result("gov:sd", STATE_HIT, "row_found", 10, evidence=ev)])
    check("卡片命中", card["outcome"] == "hit" and card["canonical_name"] == "装配式住宅建筑设计标准"
          and card["citable_level"] == "S2", card)
    check("卡片条文未取得", card["clause_anchor"]["quote"] is None)
    old_with_clause = {"clause_anchor": {"page": "p67", "quote": "Q", "carrier_sha256": "s",
                                        "grade": "S1（转引）", "limitation": "转引"}}
    card3 = build_card(ctx, "JGJ/T 398-2017", "装配式住宅建筑设计标准",
                       [result("gov:sd", STATE_HIT, "row_found", 10, evidence=ev)],
                       old_card=old_with_clause)
    check("卡片条文已取得（转引）", card3["clause_anchor"]["quote"] == "Q"
          and card3["field_grades"]["clause"] == "S1（转引）"
          and not any("条文原文（S1）" in x for x in card3["not_covered"]),
          card3["not_covered"])
    reviewed = {"review_status": "独立复核通过（2026-11-10；reviewer=独立复核代理）",
                "clause_anchor": {"page": "p10", "quote": "Q", "carrier_sha256": "s", "grade": "S1"}}
    card4 = build_card(ctx, "JGJ/T 398-2017", "装配式住宅建筑设计标准",
                       [result("gov:sd", STATE_HIT, "row_found", 10, evidence=ev)],
                       old_card=reviewed, mode="refresh")
    check("刷新不抹独立复核标记", card4["review_status"].startswith("独立复核通过")
          and "机器刷新" in card4["review_status"], card4["review_status"])
    check("刷新不降级已取得 S1 条文的卡", card4["citable_level"] == "S1", card4["citable_level"])
    card5 = build_card(ctx, "JGJ/T 398-2017", None,
                       [result("gov:sd", STATE_HIT, "row_found", 10, evidence=ev)],
                       old_card=card4, mode="refresh")
    check("重复刷新不累积追加项", card5["review_status"].count("机器刷新") == 1, card5["review_status"])
    card6 = build_card(ctx, "JGJ/T 398-2017", None,
                       [result("gov:sd", STATE_HIT, "row_found", 10, evidence=ev)])
    check("无旧卡时仍标 unreviewed", card6["review_status"] == MACHINE_REVIEW, card6["review_status"])

    card2 = build_card(ctx, "GB/T 99999-2099", None,
                       [result("samr", STATE_INDETERMINATE, "health_fetch_failed", 10)])
    check("未取到=miss/S0", card2["outcome"] == "miss" and card2["citable_level"] == "S0")
    merged = merge_sources([{"carrier": "a", "sha256": "1", "level": "S0", "parsed": {"issued_date": "2017-10-30"}}],
                           card["identity_sources"])
    check("合并去重", len(merged) == 2)
    ms = merge_states([{"channel": "samr", "state": "x"}],
                      [{"channel": "samr", "state": "y"}, {"channel": "gov", "state": "hit"}])
    check("状态合并去重", len(ms) == 2 and ms[0]["state"] == "y")
    ms = merge_states([{"channel": "gov:sd", "state": "hit", "reason": "row_found"}],
                      [{"channel": "gov:sd", "state": "indeterminate", "reason": "fetch_failed"}])
    check("状态合并：命中不被失败覆盖", ms[0]["state"] == "hit"
          and (ms[0].get("last_attempt") or {}).get("reason") == "fetch_failed", ms)

    pth = card_file(ctx, "JGJ/T 398-2017")
    write_json(pth, card)
    p2, c2 = find_card(ctx, "JGJ/T 398-2017")
    check("卡片可查", c2 is not None and card_state(c2) == "valid")
    lc = lookup_card(ctx.root, "JGJ/T 398")
    check("lookup_card 无年号匹配", lc["state"] == "valid"
          and (lc.get("card") or {}).get("standard_id") == "JGJ/T 398-2017", lc)
    lc2 = lookup_card(ctx.root, "GB/T 99999-2099")
    check("lookup_card 无卡", lc2["state"] == "missing" and lc2.get("card") is None, lc2)

    (ctx.root / INDEX_REL).parent.mkdir(parents=True, exist_ok=True)
    (ctx.root / INDEX_REL).write_text(
        "| 序号 | 标准编号 | 标准名称 | 实施日期 | 状态 |\n|---|---|---|---|---|\n"
        "| 1 | GB 50222-2017 | 建筑内部装修设计防火规范 | 2018-04-01 | 现行有效 |\n",
        encoding="utf-8")
    ledger = ctx.root / "ledger.json"
    write_json(ledger, {"rows": [
        {"key": "GB 50222", "cited_years": ["2017"], "count": 2, "hits": [{"file": "x", "line": 1}]},
        {"key": "JGJ/T 398", "cited_years": ["2017"], "count": 6, "hits": [{"file": "y", "line": 320}]},
    ]})
    g = compute_gaps(ctx, ledger)
    states = {t["key"]: t["state"] for t in g["tasks"]}
    check("gaps 已收录未验证", states.get("GB 50222") == "indexed_no_card", g)
    check("gaps 已缓存", states.get("JGJ/T 398") == "cached", g)

    # ---- 预算与兜底（本地回环服务，不出网）----
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Drip(BaseHTTPRequestHandler):
        mode = "drip"
        seen = 0

        def do_GET(self):
            type(self).seen += 1
            if self.mode == "404":
                self.send_error(404)
                return
            if self.mode == "flaky" and type(self).seen == 1:
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Length", "1280" if self.mode == "drip" else "2")
            self.end_headers()
            if self.mode == "drip":
                for _ in range(20):
                    self.wfile.write(b"x" * 64)
                    self.wfile.flush()
                    time.sleep(0.2)
            else:
                self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _Drip)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        _Drip.mode, _Drip.seen = "drip", 0
        t0 = time.perf_counter()
        r = http_get(base_url + "slow.pdf", timeout=2, wall_budget=0.6)
        wall = time.perf_counter() - t0
        check("墙钟硬闸：慢速载体被截停", not r["ok"] and r["error"] == "wall_clock_budget"
              and wall < 2.0, {"wall": round(wall, 2), "error": r["error"]})
        check("墙钟耗尽不返回部分字节", r["body"] == b"" and r.get("partial_bytes", 0) > 0, r)

        _Drip.mode, _Drip.seen = "404", 0
        r = http_get(base_url + "gone", timeout=2, retries=2, wall_budget=5)
        check("4xx 是确定答复，不重试", not r["ok"] and r["attempts"] == 1, r)

        _Drip.mode, _Drip.seen = "flaky", 0
        r = http_get(base_url + "flaky", timeout=2, retries=1, wall_budget=8)
        check("瞬时失败重试后命中", r["ok"] and r["attempts"] == 2, r)
    finally:
        srv.shutdown()

    bctx = Ctx(tmp / "skill", {"budgets": {
        "default": {"socket_s": 8, "wall_s": 12},
        "gov_compilation": {"socket_s": 20, "wall_s": 35},
        "large_carrier_bytes": 8388608, "retry_attempts": 1}})
    check("分档取数：小接口墙钟 12 s", channel_budget(bctx, "samr").left() <= 12, "")
    check("分档取数：省级 PDF 墙钟 35 s", 34 < channel_budget(bctx, "gov_compilation").left() <= 35, "")
    check("CLI 覆盖优先于分档", 4 < channel_budget(bctx, "gov_compilation", 5).left() <= 5, "")
    big = Ctx(tmp / "skill", {"channels": {"gov_compilation": {"sources": [
        {"id": "big", "name": "大载体", "url": "http://127.0.0.1:1/none.pdf",
         "bytes": 16409535, "sha256": "0" * 64}]}},
        "budgets": {"large_carrier_bytes": 8388608}})
    rs = probe_gov_compilation(big, "JGJ/T 398-2017", channel_budget(big, "gov_compilation"))
    check("大载体不进常态路由", rs[0]["state"] == STATE_INDETERMINATE
          and rs[0]["reason"] == "carrier_too_large" and rs[0]["elapsed_ms"] == 0, rs)
    rs = probe_gov_compilation(big, "JGJ/T 398-2017", channel_budget(big, "gov_compilation"),
                               allow_large=True)
    check("显式 --allow-large 才取大载体", rs[0]["reason"] != "carrier_too_large", rs)

    class _WallFailBudget:
        def call(self, *a, **k):
            return {"ok": False, "status": -1, "body": b"", "ms": 35000,
                    "error": "wall_clock_budget", "partial_bytes": 3249053}

    small = Ctx(tmp / "skill", {"channels": {"gov_compilation": {"sources": [
        {"id": "sd", "name": "省级体系件", "url": "http://127.0.0.1:1/none.pdf",
         "bytes": 1620134, "sha256": "0" * 64}]}}})
    rs = probe_gov_compilation(small, "JGJ/T 398-2017", _WallFailBudget())
    check("墙钟耗尽落 indeterminate 而非 not_covered",
          rs[0]["state"] == STATE_INDETERMINATE and rs[0]["reason"] == "wall_clock_budget", rs)
    wc = build_card(small, "JGJ/T 398-2017", None, rs)
    check("墙钟耗尽的卡不写「无通道给出证据」以外的否证",
          wc["outcome"] == "miss" and not any("库不覆盖" in x for x in wc["not_covered"]), wc["not_covered"])

    print(f"selftest: {ok}/{total}")
    return 0 if ok == total else 1


# ---------------------------------------------------------------- 入口

def main(argv: list[str]) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="L-RKL 按需取真引擎：证据卡缓存 + 通道四态")
    ap.add_argument("--root", default=str(ROOT_DEFAULT), help="项目根目录（默认本文件上一级）")
    ap.add_argument("--selftest", action="store_true", help="离线负向注入自证，不联网")
    sub = ap.add_subparsers(dest="cmd")

    q = sub.add_parser("query", help="只读证据卡缓存")
    q.add_argument("standard_id")

    r = sub.add_parser("retrieve", help="缓存优先，未命中/--refresh 跑通道并写证据卡")
    r.add_argument("standard_id")
    r.add_argument("--name", default=None)
    r.add_argument("--budget", type=float, default=None,
                   help="每通道**墙钟**预算秒（覆盖注册表分档；缺省按档：小接口 12 s／省级体系件 PDF 35 s）")
    r.add_argument("--total-budget", type=float, default=120, help="整轮墙钟预算秒")
    r.add_argument("--allow-large", action="store_true",
                   help="允许取大载体（>8 MB）；缺省不取，记 indeterminate/carrier_too_large")
    r.add_argument("--refresh", action="store_true")
    r.add_argument("--no-merge", action="store_true")
    r.add_argument("--corroborate", action="store_true")
    r.add_argument("--no-write", action="store_true")

    g = sub.add_parser("gaps", help="从引用台账冒泡未取真缺口")
    g.add_argument("--ledger", required=True)
    g.add_argument("--limit", type=int)

    sub.add_parser("channels", help="打印通道注册表与路由")
    sub.add_parser("layout", help="打印现读交付布局（开发仓／随包）与三处根是否在场")

    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.cmd:
        ap.print_help()
        return 2
    if args.cmd == "layout":
        print(json.dumps(L.describe(), ensure_ascii=False, indent=2))
        return 0
    root = Path(args.root)
    try:
        registry = load_registry(root)
    except Exception as exc:
        print(f"注册表读取失败：{root / REGISTRY_REL} :: {exc}", file=sys.stderr)
        return 2
    ctx = Ctx(root, registry)
    if args.cmd == "query":
        return cmd_query(ctx, args.standard_id)
    if args.cmd == "retrieve":
        return cmd_retrieve(ctx, args.standard_id, args.name, args.budget, args.total_budget,
                            args.refresh, not args.no_merge, args.corroborate, not args.no_write,
                            args.allow_large)
    if args.cmd == "gaps":
        try:
            print(json.dumps(compute_gaps(ctx, Path(args.ledger), args.limit), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"gaps 失败：{exc}", file=sys.stderr)
            return 2
        return 0
    if args.cmd == "channels":
        return cmd_channels(ctx)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
