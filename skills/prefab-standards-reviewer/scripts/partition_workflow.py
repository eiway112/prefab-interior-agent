#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""办公隔墙 W5 统一规则核心与业务工作面。"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import html
import itertools
import json
import os
import stat
import sys
from pathlib import Path

import deliverable_render as D

ROOT = Path(__file__).resolve().parents[1]
RULE_ID = "office-partition-w5"
RULE_VERSION = "1.0.0"
ASSESSMENT_SCHEMA = "prefab-workflow-assessment/v1"
CONDITION_IDS = ("corridor_class", "report_state")


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _case_digest(case: dict) -> str:
    return _sha(_canonical(case))


def _technical_digest(case: dict) -> str:
    value = copy.deepcopy(case)
    value.get("meta", {}).pop("revision", None)
    workflow = value.get("workflow", {})
    workflow.pop("review", None)
    workflow.pop("adoption", None)
    return _case_digest(value)


def _bump_revision(case: dict, note: str) -> None:
    rev = case["meta"]["revision"]
    parts = rev["version"].lstrip("v").split(".")
    if len(parts) != 2 or not all(x.isdigit() for x in parts):
        raise ValueError("revision.version 须为 v0.N")
    rev["version"] = f"v0.{int(parts[1]) + 1}"
    rev["date"] = dt.date.today().isoformat()
    rev["note"] = note


def _write_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"拒绝覆盖既有文件：{path}")
    path.write_bytes(data)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _state_key(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_fingerprint(records: list[dict]) -> str:
    return _sha(_canonical(records))


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    flags = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flags and os.lstat(path).st_file_attributes & flags)


def _safe_source(root: Path, rel: str) -> Path:
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("来源 path 不得为空")
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"来源 path 不得越界：{rel}")
    base = root.resolve(strict=True)
    candidate = base / p
    current = base
    for part in p.parts:
        current = current / part
        if _is_reparse_point(current):
            raise ValueError(f"来源路径含符号链接或重解析点：{rel}")
    target = candidate.resolve(strict=True)
    if base not in target.parents:
        raise ValueError(f"来源 path 逃逸：{rel}")
    if not target.is_file():
        raise ValueError(f"来源不是普通文件：{rel}")
    return target


def _record_source(root: Path, path: Path, role: str) -> dict:
    target = path.resolve(strict=True)
    rel = target.relative_to(root.resolve(strict=True)).as_posix()
    raw = target.read_bytes()
    return {"path": rel, "role": role, "bytes": len(raw), "sha256": _sha(raw)}


def source_status(case: dict, root: Path, state: dict | None = None) -> dict:
    workflow = case.get("workflow")
    if not isinstance(workflow, dict) or not isinstance(workflow.get("source"), dict):
        raise ValueError("workflow.source 缺失")
    records = workflow["source"].get("files")
    if not isinstance(records, list) or not records:
        raise ValueError("workflow.source.files 至少一条")
    actual = []
    status = "valid"
    details = []
    for item in records:
        rel = item.get("path")
        try:
            target = _safe_source(root, rel)
            raw = target.read_bytes()
            current = {"path": rel, "role": item.get("role", ""),
                       "bytes": len(raw), "sha256": _sha(raw)}
            if current["sha256"] != item.get("sha256") or current["bytes"] != item.get("bytes"):
                status = "changed"
            actual.append(current)
            details.append({"path": rel, "status": "valid" if current == item else "changed"})
        except (FileNotFoundError, OSError, ValueError):
            status = "missing" if status == "valid" else status
            actual.append(item)
            details.append({"path": rel, "status": "missing"})
    report_source = workflow.get("report_source", "")
    if state and state.get("report_state") == "provided":
        if not report_source:
            status = "missing_report" if status == "valid" else status
            details.append({"path": "", "role": "匹配构造检测报告", "status": "missing"})
        elif not any(x.get("path") == report_source and x.get("role") == "匹配构造检测报告"
                     for x in records):
            status = "invalid" if status == "valid" else status
            details.append({"path": report_source, "role": "匹配构造检测报告", "status": "invalid"})
    recorded_fp = workflow["source"].get("fingerprint")
    calculated = _source_fingerprint(records)
    if recorded_fp != calculated:
        status = "invalid"
    return {"status": status, "recorded_fingerprint": recorded_fp,
            "current_fingerprint": _source_fingerprint(actual), "files": details,
            "report_source": report_source}


def validate_workflow(case: dict) -> list[str]:
    if not isinstance(case, dict):
        return ["根节点必须是对象"]
    errs = D.validate(case)
    workflow = case.get("workflow")
    if not isinstance(workflow, dict):
        return errs + ["workflow 缺失"]
    if workflow.get("rule_id") != RULE_ID or workflow.get("rule_version") != RULE_VERSION:
        errs.append("workflow 规则身份或版本不匹配")
    conds = case.get("conditions", [])
    if tuple(c.get("id") for c in conds) != CONDITION_IDS:
        errs.append("W5 条件集合必须为 corridor_class/report_state")
    ids = {c.get("id") for c in case.get("conclusions", [])}
    if ids != {"C1", "C1-alt", "C2", "C2-report", "C3"}:
        errs.append("W5 结论分支集合不完整")
    if not isinstance(workflow.get("review", {}).get("states"), dict):
        errs.append("workflow.review.states 缺失")
    report_source = workflow.get("report_source", "")
    if report_source:
        matches = [x for x in workflow.get("source", {}).get("files", [])
                   if x.get("role") == "匹配构造检测报告" and x.get("path") == report_source]
        if len(matches) != 1:
            errs.append("workflow.report_source 必须唯一指向已登记检测报告")
    adoption = workflow.get("adoption")
    if not isinstance(adoption, dict) or adoption.get("status") not in (
            "pending", "adopted", "modified", "not_adopted"):
        errs.append("workflow.adoption.status 非法")
    return errs


def _parse_state(case: dict, values: list[str] | None) -> dict:
    allowed = {c["id"]: {o["value"] for o in c["options"]} for c in case["conditions"]}
    state = {c["id"]: c["default"] for c in case["conditions"]}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"--set 须为 key=value：{raw}")
        key, value = raw.split("=", 1)
        if key not in allowed:
            raise ValueError(f"未知条件：{key}")
        if value not in allowed[key]:
            raise ValueError(f"条件取值非法：{key}={value}")
        state[key] = value
    return state


def all_states(case: dict) -> list[dict]:
    conds = case["conditions"]
    total = 1
    for c in conds:
        total *= len(c["options"])
    if total > 256:
        raise ValueError("条件组合超过 256，拒绝展开工作面")
    return [dict(zip((c["id"] for c in conds), values))
            for values in itertools.product(*[[o["value"] for o in c["options"]] for c in conds])]


def _applicable(node: dict, state: dict) -> bool:
    when = node.get("when")
    return not when or all(state.get(k) in values for k, values in when.items())


def project(case: dict, state: dict, root: Path, generated_at: str | None = None) -> dict:
    errs = validate_workflow(case)
    if errs:
        raise ValueError("；".join(errs))
    allowed = {c["id"]: {o["value"] for o in c["options"]} for c in case["conditions"]}
    if set(state) != set(allowed) or any(state[k] not in allowed[k] for k in state):
        raise ValueError("条件状态不完整或含非法值")
    src = source_status(case, root, state)
    key = _state_key(state)
    review = case["workflow"]["review"]["states"].get(key)
    tech = _technical_digest(case)
    reason = "该条件组合尚未复核"
    if src["status"] != "valid":
        review_status = "stale" if review else "pending"
        reason = f"来源状态为 {src['status']}，旧复核不得沿用"
    elif review and review.get("source_fingerprint") == src["current_fingerprint"] \
            and review.get("rule_version") == RULE_VERSION \
            and review.get("technical_digest") == tech:
        review_status = "reviewed"
        reason = review.get("note", "已复核")
    elif review:
        review_status = "stale"
        reason = "来源、规则或技术内容已变化，旧复核不得沿用"
    else:
        review_status = "pending"
    recommendations = []
    for c in case["conclusions"]:
        if _applicable(c, state):
            item = copy.deepcopy(c)
            item["review_status"] = review_status
            item["review_reason"] = reason
            recommendations.append(item)
    gaps = []
    for gap in case.get("gaps", []):
        item = copy.deepcopy(gap)
        if gap.get("id") == "G1":
            if state["corridor_class"] == "noise_source":
                item["what"] = ("匹配该全钢直拼隔断构造的 CMA/CNAS 检测报告；"
                                "若采用 Rw+Ctr 分支，须人工确认报告载明对应评价量，"
                                "且 Rw、Rw+C、Rw+Ctr 不可直接比较。")
            item["status"] = "已标记提供，待人工核匹配性" if state["report_state"] == "provided" else "待提供"
        else:
            item["status"] = "待处理"
        gaps.append(item)
    manifest = {
        "schema": ASSESSMENT_SCHEMA,
        "rule_id": RULE_ID,
        "rule_version": RULE_VERSION,
        "case_sha256": _case_digest(case),
        "technical_sha256": tech,
        "source_fingerprint": src["current_fingerprint"],
        "source_status": src["status"],
        "report_source": src["report_source"],
        "state_key": key,
        "review_status": review_status,
        "generated_at": generated_at or _now(),
    }
    return {
        "state": state,
        "recommendations": recommendations,
        "gaps": gaps,
        "overturn": list(case.get("overturn", [])),
        "next_steps": copy.deepcopy(case.get("next_steps", [])),
        "review": {"status": review_status, "reason": reason,
                   "note": (review or {}).get("note", ""),
                   "reviewer_role": (review or {}).get("reviewer_role", ""),
                   "reviewed_at": (review or {}).get("reviewed_at", "")},
        "adoption": copy.deepcopy(case["workflow"]["adoption"]),
        "manifest": manifest,
    }


def build_workflow(source_path: Path, answer_path: Path, root: Path) -> dict:
    source = _load(source_path)
    if source.get("schema") != D.SCHEMA:
        raise ValueError("源案卷不是 prefab-deliverable/v3")
    by_id = {c.get("id"): c for c in source.get("conclusions", [])}
    if not all(k in by_id for k in ("C1", "C2", "C3")):
        raise ValueError("源案卷缺少 C1/C2/C3")
    answer = answer_path.read_text(encoding="utf-8")
    c1, c2, c3 = by_id["C1"], by_id["C2"], by_id["C3"]
    ordinary_base = c1["conclusion"].split("；若走廊侧", 1)[0]
    ordinary = ordinary_base + "。"
    noise = "若走廊侧贴邻设备机房等产生噪声源，改按 Rw+Ctr 复核。Rw、Rw+C、Rw+Ctr 为不同评价量，不可直接比较。"
    if ordinary_base not in c1["conclusion"] or noise not in c1["conclusion"]:
        raise ValueError("源 C1 条件分支已变化，拒绝按旧规则重建")
    case = copy.deepcopy(source)
    case["conditions"] = [
        {"id": "corridor_class", "label": "走廊侧空间性质", "default": "ordinary",
         "options": [{"value": "ordinary", "label": "普通房间（非噪声源）"},
                     {"value": "noise_source", "label": "贴邻设备机房等噪声源"}]},
        {"id": "report_state", "label": "匹配构造的检测报告", "default": "missing",
         "options": [{"value": "missing", "label": "尚未提供"},
                     {"value": "provided", "label": "已提供，待人工核匹配性"}]},
    ]
    c1_source = {"doc": "GB 50118-2010；GB/T 50121-2005",
                 "clause": "第 8.2.1 条表 8.2.1 行 2；第 8.2.2 条表 8.2.2 行 2；GB/T 50121-2005 附录 A 表 A.0.1"}
    for needle in ("第 8.2.1 条表 8.2.1 行2", "第 8.2.2 条表 8.2.2 行2", "GB/T 50121-2005 附录 A 表 A.0.1"):
        if needle not in c1["conclusion"]:
            raise ValueError(f"源 C1 依据锚已变化：{needle}")
    c1_ordinary = copy.deepcopy(c1)
    c1_ordinary.update({"item": "评价量推荐（普通走廊）", "conclusion": ordinary,
                        "source": c1_source,
                        "when": {"corridor_class": ["ordinary"]},
                        "conditions": "走廊按普通房间（非产生噪声的房间）归类。"})
    c1_noise = copy.deepcopy(c1)
    c1_noise.update({"id": "C1-alt", "item": "评价量推荐（噪声源邻接）", "conclusion": noise,
                     "source": c1_source,
                     "when": {"corridor_class": ["noise_source"]},
                     "conditions": "走廊侧贴邻设备机房等产生噪声源。"})
    c2_missing = copy.deepcopy(c2)
    c2_missing.update({"item": "声明值效力（报告未提供）",
                       "when": {"report_state": ["missing"]},
                       "conditions": "匹配构造的 CMA/CNAS 检测报告尚未提供。"})
    c2_report = copy.deepcopy(c2)
    c2_report.update({"id": "C2-report", "item": "声明值效力（报告已提供）",
                      "conclusion": "匹配该构造的 CMA/CNAS 检测报告已标记为提供；报告是否匹配构造、是否载明所需评价量，仍须人工复核，工具不自动把图面声明值转为验收值。",
                      "source": {"doc": "流程控制（基于原 C2 缺件要求）", "clause": "非规范规定值"},
                      "when": {"report_state": ["provided"]},
                      "conditions": "报告已提供，但构造匹配性与评价量仍须人工复核。",
                      "note": "状态分支，不新增工程取值。"})
    c3_keep = copy.deepcopy(c3)
    c3_keep["item"] = "标准版本时效"
    case["conclusions"] = [c1_ordinary, c1_noise, c2_missing, c2_report, c3_keep]
    case["scope"] = {
        "can_judge": ["办公走廊—房间隔墙的评价量口径", "图面声明值是否可直接作为验收依据", "图面引用标准版本的时效核对"],
        "cannot_judge": ["不替代设计单位的专业审查与工程责任判断", "不输出未经匹配数据验证的定量隔声预测", "具体产品性能以匹配构造的检测报告为准"],
    }
    case["gaps"] = [
        {"id": "G1", "what": "匹配该全钢直拼隔断构造的 CMA/CNAS 检测报告（须载计权隔声量及粉红噪声修正量 C 值）。",
         "why": "答复要求补充此项输入", "owner": "供应商 / 采购", "external": True,
         "affects": ["C1", "C1-alt", "C2", "C2-report", "C3"], "kind": "ask"},
        {"id": "G2", "what": "按现行版本复核 GB/T 11981 与 GB/T 50121 的适用条款。",
         "why": "原 C3 的标准时效结论仍含待核项", "owner": "技能维护", "external": False,
         "affects": ["C3"], "kind": "ask"},
        {"id": "G3", "what": "项目负责人对本工作面的专业内容进行复核。",
         "why": "签署栏可留空，但专业内容须由用户复核", "owner": "项目负责人", "external": False,
         "affects": ["C1", "C1-alt", "C2", "C2-report", "C3"], "kind": "ask"},
    ]
    case["overturn"] = [
        "走廊侧贴邻设备机房等产生噪声源时，评价量推荐由 Rw+C 改为 Rw+Ctr 复核。",
        "匹配构造检测报告从未提供变为已提供时，声明值效力结论进入新条件分支并需重核。",
        "来源文件、规则版本或技术内容变化时，既有复核状态失效并显式标为需重核。",
        "任何前提条件变化，本结论需重判。",
    ]
    case["next_steps"] = [
        {"action": "补齐匹配构造检测报告并核构造匹配性", "owner": "供应商 / 采购", "trigger": "报告到位后"},
        {"action": "复核现行标准版本与适用条款", "owner": "技能维护", "trigger": "引用前"},
        {"action": "复核本工作面并记录采用或修改事实", "owner": "项目负责人", "trigger": "业务使用时"},
    ]
    records = [_record_source(root, source_path, "源案卷"), _record_source(root, answer_path, "原始答复")]
    case["meta"]["issued_at"] = dt.date.today().isoformat()
    case["meta"]["compiled_by"] = "partition_workflow.py ← 既有真实案卷"
    case["meta"]["default_template"] = "conclusion_sheet"
    case["meta"]["issuer"] = {"prepared_by": "", "reviewed_by": "", "received_by": ""}
    case["meta"]["revision"] = {"version": "v0.2", "date": dt.date.today().isoformat(),
                                "note": "W5 结构化工作面；签署栏保留，签名非闭环前置"}
    case["meta"]["inputs"] = [
        {"name": source_path.name, "status": "已读", "bytes": records[0]["bytes"],
         "sha256": records[0]["sha256"], "note": "既有真实案卷"},
        {"name": answer_path.name, "status": "已读", "bytes": records[1]["bytes"],
         "sha256": records[1]["sha256"], "note": "真实试点答复"},
    ]
    case["workflow"] = {
        "rule_id": RULE_ID,
        "rule_version": RULE_VERSION,
        "source": {"files": records, "fingerprint": _source_fingerprint(records)},
        "report_source": "",
        "review": {"states": {}},
        "adoption": {"status": "pending", "reason": "", "modified_conclusions": [],
                     "materials_supplemented": [], "changed_recommendation": False,
                     "recorded_at": "", "note": "待实际使用后填写"},
    }
    errs = validate_workflow(case)
    if errs:
        raise ValueError("；".join(errs))
    return case


def record_review(case: dict, state: dict, note: str, root: Path) -> dict:
    src = source_status(case, root, state)
    if src["status"] != "valid":
        raise ValueError(f"来源状态为 {src['status']}，拒绝写入复核")
    value = copy.deepcopy(case)
    key = _state_key(state)
    value["workflow"]["review"]["states"][key] = {
        "status": "reviewed", "reviewer_role": "项目负责人", "note": note.strip() or "已复核",
        "reviewed_at": _now(), "source_fingerprint": src["current_fingerprint"],
        "rule_version": RULE_VERSION, "technical_digest": _technical_digest(case),
    }
    _bump_revision(value, "写入项目负责人复核；签署栏可留空")
    errs = validate_workflow(value)
    if errs:
        raise ValueError("；".join(errs))
    return value


def record_adoption(case: dict, *, status: str, reason: str, modified: list[str],
                    materials: list[str], changed: bool, root: Path) -> dict:
    if status not in ("pending", "adopted", "modified", "not_adopted"):
        raise ValueError("采用状态非法")
    if status in ("modified", "not_adopted") and not reason.strip():
        raise ValueError("修改或不采用须填写原因")
    known = {c["id"] for c in case["conclusions"]}
    if any(item not in known for item in modified):
        raise ValueError("人工修改引用了未知结论")
    src = source_status(case, root)
    if src["status"] != "valid":
        raise ValueError(f"来源状态为 {src['status']}，拒绝写入采用事实")
    value = copy.deepcopy(case)
    value["workflow"]["adoption"] = {
        "status": status, "reason": reason.strip(), "modified_conclusions": modified,
        "materials_supplemented": materials, "changed_recommendation": bool(changed),
        "recorded_at": _now(), "note": "由实际使用方填写，非 AI 代填",
    }
    _bump_revision(value, "写入实际采用与人工修改事实")
    errs = validate_workflow(value)
    if errs:
        raise ValueError("；".join(errs))
    return value


def attach_report(case: dict, report_path: Path, root: Path) -> dict:
    value = copy.deepcopy(case)
    record = _record_source(root, report_path, "匹配构造检测报告")
    files = [x for x in value["workflow"]["source"]["files"] if x.get("role") != "匹配构造检测报告"]
    files.append(record)
    value["workflow"]["source"]["files"] = files
    value["workflow"]["source"]["fingerprint"] = _source_fingerprint(files)
    value["workflow"]["report_source"] = record["path"]
    _bump_revision(value, "登记匹配构造检测报告；旧复核须重核")
    errs = validate_workflow(value)
    if errs:
        raise ValueError("；".join(errs))
    return value


def apply_submission(case: dict, submission: dict, root: Path) -> dict:
    if not isinstance(submission, dict) or submission.get("schema") != ASSESSMENT_SCHEMA \
            or not submission.get("snapshot") or submission.get("authoritative") is not False:
        raise ValueError("回灌件不是 W5 待校验快照")
    state = submission.get("state")
    manifest = submission.get("manifest")
    if not isinstance(state, dict) or not isinstance(manifest, dict):
        raise ValueError("回灌件缺状态或版本清单")
    if manifest.get("case_sha256") != _case_digest(case):
        raise ValueError("回灌件基于旧案卷，拒绝覆盖当前版本")
    allowed = {c["id"]: {o["value"] for o in c["options"]} for c in case["conditions"]}
    if set(state) != set(allowed) or any(state[k] not in allowed[k] for k in state):
        raise ValueError("回灌状态不完整或含非法值")
    if manifest.get("state_key") != _state_key(state):
        raise ValueError("回灌状态与版本清单不一致")
    if source_status(case, root, state)["status"] != "valid":
        raise ValueError("回灌状态来源不完整或已变化，拒绝写入")
    review_submit = submission.get("review_submission")
    adoption_submit = submission.get("adoption_submission")
    if not isinstance(review_submit, dict) or not isinstance(adoption_submit, dict):
        raise ValueError("回灌件缺复核或采用提交")
    value = copy.deepcopy(case)
    if review_submit.get("requested"):
        value = record_review(value, state, str(review_submit.get("note", "")), root)
    status = adoption_submit.get("status", "pending")
    if status != "pending":
        value = record_adoption(
            value, status=status, reason=str(adoption_submit.get("reason", "")),
            modified=adoption_submit.get("modified_conclusions", []),
            materials=adoption_submit.get("materials_supplemented", []),
            changed=bool(adoption_submit.get("changed_recommendation")), root=root)
    if not review_submit.get("requested") and status == "pending":
        raise ValueError("回灌件没有可写入的复核或采用事实")
    return value


def render_workface(case: dict, root: Path) -> str:
    projections = {_state_key(state): project(case, state, root, case["meta"]["issued_at"])
                   for state in all_states(case)}
    default_state = {c["id"]: c["default"] for c in case["conditions"]}
    default_key = _state_key(default_state)
    all_conclusions = []
    seen = set()
    for p in projections.values():
        for c in p["recommendations"]:
            if c["id"] not in seen:
                seen.add(c["id"])
                all_conclusions.append({"id": c["id"], "item": c["item"]})
    payload = {
        "title": case["meta"]["title"], "project": case["meta"]["project"],
        "stage": case["meta"]["stage"], "revision": case["meta"]["revision"],
        "conditions": case["conditions"], "default_state": default_state,
        "projections": projections, "all_conclusions": all_conclusions,
        "source_names": [Path(x["path"]).name for x in case["workflow"]["source"]["files"]],
        "issuer": case["meta"]["issuer"],
        "assessment_schema": ASSESSMENT_SCHEMA,
    }
    e = html.escape
    controls = "".join(
        f'<div class="field"><label for="cond-{e(c["id"])}">{e(c["label"])}</label>'
        f'<select id="cond-{e(c["id"])}" data-cond="{e(c["id"])}">'
        + "".join(f'<option value="{e(o["value"])}"{" selected" if o["value"] == c["default"] else ""}>'
                  f'{e(o["label"])}</option>' for o in c["options"])
        + "</select></div>" for c in case["conditions"])
    modified = "".join(
        f'<label class="check"><input type="checkbox" data-modified value="{e(c["id"])}"> {e(c["item"])}</label>'
        for c in all_conclusions)
    sign = D._sign_table(case)
    css = D.CSS_SHEET + """
.cards{display:grid;gap:12px}.card{border:1px solid var(--line);border-left:4px solid var(--accent);padding:14px 16px;border-radius:6px}
.card h3{margin:0 0 6px;font-size:16px}.basis{color:var(--muted);font-size:13px}.badge{display:inline-block;padding:1px 8px;border-radius:12px;background:var(--soft);border:1px solid var(--line)}
.status{padding:10px 12px;border-radius:5px;background:var(--soft);border:1px solid var(--line)}.status.stale{background:var(--warnbg);color:var(--warn);border-color:#e7b7b0}
.form{display:grid;gap:10px;border:1px solid var(--line);padding:16px;border-radius:6px}.form textarea{width:100%;min-height:70px;font:inherit;border:1px solid #9aa4b0;border-radius:4px;padding:8px}
.check{display:block;margin:4px 0}.muted{color:var(--muted);font-size:13px}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}
"""
    js = r"""
(function(){
  var D=JSON.parse(document.getElementById('data').textContent);
  var state=Object.create(null);
  D.conditions.forEach(function(c){state[c.id]=c['default'];});
  function key(){var o=Object.create(null);Object.keys(state).sort().forEach(function(k){o[k]=state[k];});return JSON.stringify(o);}
  function el(tag,cls,text){var n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;}
  function text(id){return document.getElementById(id);}
  function render(){
    var p=D.projections[key()];
    var box=text('recommendations');box.textContent='';
    p.recommendations.forEach(function(c){
      var card=el('article','card');
      card.appendChild(el('h3',null,c.item));
      card.appendChild(el('p',null,c.conclusion));
      card.appendChild(el('p','basis','依据：'+c.source.doc+' '+c.source.clause));
      card.appendChild(el('p','muted','适用条件：'+c.conditions+' ｜ '+D.grade_labels[c.grade]));
      box.appendChild(card);
    });
    if(!p.recommendations.length)box.appendChild(el('p','muted','当前条件组合下没有可判结论。'));
    var gaps=text('gaps');gaps.textContent='';
    p.gaps.forEach(function(g){
      var tr=el('tr');[g.status,g.what,g.why,g.owner].forEach(function(v){tr.appendChild(el('td',null,v));});gaps.appendChild(tr);
    });
    var over=text('overturn');over.textContent='';
    p.overturn.forEach(function(v){over.appendChild(el('li',null,v));});
    var st=text('review-status');st.textContent=p.review.status+'：'+p.review.reason+(p.review.note?' ｜ '+p.review.note:'');
    st.className='status'+(p.review.status==='stale'?' stale':'');
    text('manifest').textContent='版本 '+D.revision.version+' ｜ 规则 '+p.manifest.rule_version+' ｜ 来源 '+p.manifest.source_status+' ｜ 源指纹 '+p.manifest.source_fingerprint.slice(0,12)+' ｜ 案卷 '+p.manifest.case_sha256.slice(0,12);
  }
  D.conditions.forEach(function(c){var s=text('cond-'+c.id);s.addEventListener('change',function(){state[c.id]=s.value;render();});});
  function adoption(){
    return {status:text('adopt-status').value,reason:text('adopt-reason').value,
      modified_conclusions:Array.prototype.slice.call(document.querySelectorAll('[data-modified]')).filter(function(x){return x.checked;}).map(function(x){return x.value;}),
      materials_supplemented:text('materials').value.split('\n').map(function(x){return x.trim();}).filter(Boolean),
      changed_recommendation:text('changed').checked,recorded_at:new Date().toISOString()};
  }
  text('export').addEventListener('click',function(){
    var p=JSON.parse(JSON.stringify(D.projections[key()]));
    p.schema=D.assessment_schema;p.snapshot=true;p.authoritative=false;p.revision=D.revision;
    p.review_submission={requested:text('review-confirm').checked,note:text('review-note').value};
    p.adoption_submission=adoption();p.exported_at=new Date().toISOString();
    var blob=new Blob([JSON.stringify(p,null,2)],{type:'application/json'});var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='办公隔墙工作面_待校验回灌件.json';document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(a.href);},1500);
  });
  text('print').addEventListener('click',function(){window.print();});
  render();
})();
"""
    payload["grade_labels"] = D.GRADE_LABEL
    return "\n".join([
        "<!DOCTYPE html>", '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{e(case['meta']['title'])}</title>", f"<style>{css}</style></head><body><main class=\"page\">",
        '<div class="kicker">非承重隔墙 · 业务工作面</div>',
        f"<h1>{e(case['meta']['title'])}</h1>",
        f'<p class="sub">{e(case["meta"]["project"])} ｜ {e(case["meta"]["stage"])} ｜ {e(case["meta"]["revision"]["version"])}</p>',
        '<div class="status">本件为日常业务辅助支持，不是官方文件；签署栏保留，但签名不是闭环条件。</div>',
        '<h2>条件</h2>', f'<div class="controls">{controls}</div>',
        '<h2>当前推荐</h2>', '<div id="recommendations" class="cards"></div>',
        '<h2>判断边界</h2>', '<p><b>能判：</b></p>', D._ul(case["scope"]["can_judge"]),
        '<p><b>不能判：</b></p>', D._ul(case["scope"]["cannot_judge"]),
        '<h2>待证项</h2>', '<table><thead><tr><th>状态</th><th>缺什么</th><th>为什么</th><th>责任方</th></tr></thead><tbody id="gaps"></tbody></table>',
        '<h2>什么情况下结论会改变</h2>', '<ul id="overturn"></ul>',
        '<h2>人工复核</h2>', '<div id="review-status" class="status"></div>',
        '<label>复核意见（可选）<textarea id="review-note"></textarea></label>',
        '<label class="check"><input id="review-confirm" type="checkbox"> 确认由我本人复核当前条件组合</label>',
        '<p class="muted">复核角色由用户本人承担；无需填写姓名或签名。导出件须经统一入口校验写入，页内不直接改写案卷。</p>',
        '<h2>采用与修改事实</h2>', '<div class="form">',
        '<label>采用状态<select id="adopt-status"><option value="pending">待使用</option><option value="adopted">采用</option><option value="modified">人工修改后采用</option><option value="not_adopted">不采用</option></select></label>',
        '<label>采用／修改原因<textarea id="adopt-reason"></textarea></label>',
        '<label>人工修改了哪些结论</label>', modified,
        '<label>已补齐材料（每行一项）<textarea id="materials"></textarea></label>',
        '<label class="check"><input id="changed" type="checkbox"> 修改后改变了原推荐</label>',
        '</div>', '<h2>版本回指</h2>', '<p id="manifest" class="muted"></p>',
        '<p class="muted">本 HTML 是导出时快照；源数据变化后请重新生成，旧件不代表新源。</p>',
        '<p class="gen">sha256：' + e(_case_digest(case)) + '</p>',
        '<p class="muted">来源：' + e("、".join(payload["source_names"])) + '</p>',
        '<h2>签署（格式保留，可留空）</h2>', sign,
        '<div class="actions"><button id="export" class="primary" type="button">导出待校验回灌件 JSON</button><button id="print" type="button">打印 / 导出 PDF</button></div>',
        '<footer><p>具体设计、施工及验收由项目设计单位与具备资质的专业人员负责。</p></footer>',
        '</main>', f'<script id="data" type="application/json">{D.script_json(payload)}</script>',
        f"<script>{js}</script>", "</body></html>",
    ]) + "\n"


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="办公隔墙 W5 统一业务工作面")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--source", required=True)
    p.add_argument("--answer", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=str(ROOT))
    p = sub.add_parser("check")
    p.add_argument("--case", required=True)
    p.add_argument("--root", default=str(ROOT))
    p = sub.add_parser("assess")
    p.add_argument("--case", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--set", action="append", default=[])
    p.add_argument("--root", default=str(ROOT))
    p = sub.add_parser("render")
    p.add_argument("--case", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=str(ROOT))
    p = sub.add_parser("review")
    p.add_argument("--case", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--set", action="append", default=[])
    p.add_argument("--root", default=str(ROOT))
    p = sub.add_parser("adoption")
    p.add_argument("--case", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--status", required=True, choices=("pending", "adopted", "modified", "not_adopted"))
    p.add_argument("--reason", default="")
    p.add_argument("--modified", action="append", default=[])
    p.add_argument("--materials", action="append", default=[])
    p.add_argument("--changed-recommendation", action="store_true")
    p.add_argument("--root", default=str(ROOT))
    p = sub.add_parser("attach-report")
    p.add_argument("--case", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=str(ROOT))
    p = sub.add_parser("apply")
    p.add_argument("--case", required=True)
    p.add_argument("--submission", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).resolve(strict=True)
        if args.cmd == "build":
            value = build_workflow(Path(args.source), Path(args.answer), root)
            _write_new(Path(args.out), _json_bytes(value))
            print(f"已建立工作面：{args.out}")
            return 0
        case = _load(Path(args.case))
        if args.cmd == "check":
            errs = validate_workflow(case)
            if errs:
                print("工作面校验失败：", file=sys.stderr)
                for item in errs:
                    print(f"  - {item}", file=sys.stderr)
                return 1
            print(f"工作面校验通过：{args.case}")
            return 0
        if args.cmd == "assess":
            state = _parse_state(case, args.set)
            value = project(case, state, root)
            _write_new(Path(args.out), _json_bytes(value))
            print(f"已评定：{args.out}（复核态 {value['review']['status']}）")
            return 0
        if args.cmd == "render":
            _write_new(Path(args.out), render_workface(case, root).encode("utf-8"))
            print(f"已渲染工作面：{args.out}")
            return 0
        if args.cmd == "review":
            state = _parse_state(case, args.set)
            value = record_review(case, state, args.note, root)
            _write_new(Path(args.out), _json_bytes(value))
            print(f"已写入复核：{args.out}")
            return 0
        if args.cmd == "adoption":
            value = record_adoption(case, status=args.status, reason=args.reason,
                                    modified=args.modified, materials=args.materials,
                                    changed=args.changed_recommendation, root=root)
            _write_new(Path(args.out), _json_bytes(value))
            print(f"已写入采用事实：{args.out}")
            return 0
        if args.cmd == "attach-report":
            value = attach_report(case, Path(args.report), root)
            _write_new(Path(args.out), _json_bytes(value))
            print(f"已登记检测报告：{args.out}")
            return 0
        if args.cmd == "apply":
            value = apply_submission(case, _load(Path(args.submission)), root)
            _write_new(Path(args.out), _json_bytes(value))
            print(f"已校验写入回灌件：{args.out}")
            return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
