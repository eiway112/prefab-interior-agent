#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能包自检：不装宿主、不联网，验证包本身是否完整可加载。

用法：
    python tools/smoke_test.py
    python tools/smoke_test.py --target ~/.qoder/skills   # 连安装结果一起验

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
import re
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "skills"
SHARED_EXPECTED = [
    "change-governance.md",
    "data-classification.md",
    "glossary.md",
    "interface-contracts.md",
    "platform-adapter-reference.md",
    "redlines-registry.md",
    "standards-index.md",
    "standards-reasoning-rules.md",
]
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


def main() -> int:
    ap = argparse.ArgumentParser(description="技能包自检")
    ap.add_argument("--target", help="额外检查一个已安装目录")
    args = ap.parse_args()

    if not SRC.is_dir():
        print(f"[smoke] FAIL：找不到 {SRC}", file=sys.stderr)
        return 2

    failures, notes = [], []
    skill_dirs = sorted(p for p in SRC.iterdir() if p.is_dir() and p.name != "shared")
    with_skill = [p for p in skill_dirs if (p / "SKILL.md").is_file()]
    notes.append(f"技能目录 {len(skill_dirs)} 个，其中带 SKILL.md {len(with_skill)} 个")

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
            for d in with_skill:
                if not (target / d.name / "SKILL.md").is_file():
                    failures.append(f"安装目录缺 {d.name}/SKILL.md")
            for name in SHARED_EXPECTED:
                if not (target / "shared" / name).is_file():
                    failures.append(f"安装目录缺 shared/{name}")
            notes.append(f"安装目录检查：{target}")

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
