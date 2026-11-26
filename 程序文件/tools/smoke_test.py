#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能包自检：不装宿主、不联网，验证包本身是否完整可加载。

用法：
    python 程序文件/tools/smoke_test.py
    python 程序文件/tools/smoke_test.py --target ~/.qoder/skills   # 连安装结果一起验

检查项：
  1. 技能目录数与 SKILL.md 是否一一对应
  2. 每个 SKILL.md 是否有 YAML frontmatter，且含 name / description
  3. 技能正文里引用的 ../shared/<文件> 是否都真实存在
  4. shared/ 治理文件是否齐全
  5. standards-index.md 是否存在且非空

退出码：0＝全部通过；1＝有失败项；2＝包结构不可读。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from install import SHARED_EXPECTED, check as check_install

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "skills"
SHARED_REF = re.compile(r"\.\./shared/([A-Za-z0-9._\-]+)")


def parse_frontmatter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return meta
        if ":" in line and not line.startswith((" ", "\t")):
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return {}


def capability_report(base: Path, cache_override: Path | None = None) -> tuple[dict, bool]:
    scripts = base / "prefab-standards-reviewer" / "scripts"
    checks = []

    def add(name, status, detail, hint=None):
        item = {"name": name, "status": status, "detail": detail}
        if hint:
            item["hint"] = hint
        checks.append(item)

    try:
        index_ok = (base / "standards-index.md").is_file() and bool(
            (base / "standards-index.md").read_text(encoding="utf-8").strip())
        shared_ok = all((base / "shared" / name).is_file() for name in SHARED_EXPECTED)
        add("base_read", "ok" if index_ok and shared_ok else "unavailable",
            "标准索引与 shared 治理文件可读" if index_ok and shared_ok else "标准索引或 shared 治理文件缺失")
    except OSError as exc:
        add("base_read", "unavailable", str(exc))

    def run_selftest(script):
        path = scripts / script
        if not path.is_file():
            return "unavailable", f"缺工具：{path.name}"
        try:
            proc = subprocess.run([sys.executable, "-B", "-X", "utf8", str(path), "--selftest"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=60, cwd=str(base))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "unavailable", str(exc)
        return ("ok" if proc.returncode == 0 else "unavailable",
                (proc.stdout + proc.stderr).strip().splitlines()[-1] if (proc.stdout + proc.stderr).strip()
                else f"exit={proc.returncode}")

    status, detail = run_selftest("citation_ledger.py")
    add("citation_verify", status, detail,
        None if status == "ok" else "安装 markdown-it-py==4.0.0 mdurl==0.1.2 后重跑")
    status, detail = run_selftest("answer_to_case.py")
    add("rendering", status, detail)

    try:
        import fitz
        add("pdf_extraction", "ok", f"PyMuPDF {getattr(fitz, '__version__', 'unknown')}")
    except Exception as exc:
        add("pdf_extraction", "degraded", f"政府体系 PDF 通道不可用：{exc}",
            "python -m pip install PyMuPDF")

    try:
        if cache_override is not None:
            cache = cache_override.expanduser().absolute()
        else:
            layout_path = scripts / "delivery_layout.py"
            spec = importlib.util.spec_from_file_location("prefab_delivery_layout", layout_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cache = module.cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        probe = cache / f".prefab-cache-probe-{os.getpid()}"
        probe.write_bytes(b"ok")
        if probe.read_bytes() != b"ok":
            raise OSError("缓存探针回读不一致")
        probe.unlink()
        add("cache_write", "ok", str(cache).replace("\\", "/"))
    except Exception as exc:
        add("cache_write", "unavailable", str(exc), "用 --cache-dir 指定用户可写目录")

    required = {"base_read", "citation_verify", "rendering", "cache_write"}
    can_proceed = all(item["status"] == "ok" for item in checks if item["name"] in required)
    return {"scope": "installed-capabilities", "base": str(base).replace("\\", "/"),
            "can_proceed": can_proceed, "checks": checks}, can_proceed


def main() -> int:
    ap = argparse.ArgumentParser(description="技能包自检")
    ap.add_argument("--target", help="额外检查一个已安装目录")
    ap.add_argument("--capabilities", action="store_true", help="输出安装后的能力级诊断 JSON")
    ap.add_argument("--cache-dir", type=Path, help="能力诊断使用的可写缓存目录")
    args = ap.parse_args()

    if args.capabilities:
        base = Path(args.target).expanduser() if args.target else SRC
        report, can_proceed = capability_report(base, args.cache_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if can_proceed else 1

    if not SRC.is_dir():
        print(f"[smoke] FAIL：找不到 {SRC}", file=sys.stderr)
        return 2

    failures, notes = [], []
    skill_dirs = sorted(p for p in SRC.iterdir() if p.is_dir() and p.name != "shared")
    with_skill = [p for p in skill_dirs if (p / "SKILL.md").is_file()]
    notes.append(f"技能目录 {len(skill_dirs)} 个，其中带 SKILL.md {len(with_skill)} 个")
    if not with_skill:
        failures.append("技能包为空：没有可加载的技能")

    for d in skill_dirs:
        if not (d / "SKILL.md").is_file():
            failures.append(f"{d.name}：缺 SKILL.md")

    for d in with_skill:
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        meta = parse_frontmatter(text)
        if not meta:
            failures.append(f"{d.name}：SKILL.md 缺少 frontmatter")
        else:
            for key in ("name", "description"):
                if not meta.get(key):
                    failures.append(f"{d.name}：frontmatter 缺 {key}")
        for ref in sorted(set(SHARED_REF.findall(text))):
            if not (SRC / "shared" / ref).is_file():
                failures.append(f"{d.name}：引用 shared/{ref} 不存在")

    for name in SHARED_EXPECTED:
        if not (SRC / "shared" / name).is_file():
            failures.append(f"shared/{name} 缺失")

    index = SRC / "standards-index.md"
    if not index.is_file() or not index.read_text(encoding="utf-8").strip():
        failures.append("standards-index.md 缺失或为空")

    if args.target:
        target = Path(args.target).expanduser()
        if not target.is_dir():
            failures.append(f"安装目录不存在：{target}")
        else:
            failures.extend(check_install(target, SRC))
            notes.append(f"安装目录逐字节核对：{target}")

    for n in notes:
        print(f"[smoke] {n}")
    if failures:
        print(f"[smoke] FAIL：{len(failures)} 项", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"[smoke] PASS：{len(with_skill)} 个技能、{len(SHARED_EXPECTED)} 份治理文件、标准索引均通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
