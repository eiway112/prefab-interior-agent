#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把技能包安装到智能体技能目录。

用法：
    python tools/install.py --list
    python tools/install.py                      # 装到 ~/.qoder/skills
    python tools/install.py --target /path/to/skills
    python tools/install.py --dry-run            # 只打印将做什么
    python tools/install.py --force              # 覆盖已存在文件

只依赖标准库。装完会自检（SKILL.md 是否到位、shared 是否齐全），
自检失败返回非零。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "skills"
ROOT_FILES = ["standards-index.md", "platform-adapter-reference.md"]


def skills(src: Path) -> list[Path]:
    return sorted(p for p in src.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def plan(src: Path) -> list[tuple[Path, Path]]:
    """返回 [(源, 目标相对路径)]。shared 目录整体带出，其余按目录/文件逐个带出。"""
    items = [(p, Path(p.name)) for p in skills(src)]
    shared = src / "shared"
    if shared.is_dir():
        items.append((shared, Path("shared")))
    for name in ROOT_FILES:
        f = src / name
        if f.is_file():
            items.append((f, Path(name)))
    return items


def copy_item(src: Path, dst: Path, force: bool, dry: bool) -> str:
    if src.is_dir():
        if dst.exists() and not force:
            return "skip"
        if not dry:
            shutil.copytree(src, dst, dirs_exist_ok=force)
        return "copy"
    if dst.exists() and not force:
        return "skip"
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return "copy"


def check(target: Path, src: Path) -> list[str]:
    problems = []
    for sk in skills(src):
        if not (target / sk.name / "SKILL.md").is_file():
            problems.append(f"缺 SKILL.md：{sk.name}")
    shared = src / "shared"
    if shared.is_dir():
        for f in shared.iterdir():
            if f.is_file() and not (target / "shared" / f.name).is_file():
                problems.append(f"缺治理文件：shared/{f.name}")
    for name in ROOT_FILES:
        if (src / name).is_file() and not (target / name).is_file():
            problems.append(f"缺根级文件：{name}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="安装装配式装修技能包")
    ap.add_argument("--target", help="技能目录（默认 ~/.qoder/skills）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在文件")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    ap.add_argument("--list", action="store_true", help="只列技能")
    args = ap.parse_args()

    if not SRC.is_dir():
        print(f"[install] 找不到技能源目录：{SRC}", file=sys.stderr)
        print("[install] 请从仓库根目录运行本脚本，或确认 skills/ 未被裁剪。", file=sys.stderr)
        return 2

    sk = skills(SRC)
    if args.list:
        print(f"技能 {len(sk)} 个：")
        for p in sk:
            print(f"  - {p.name}")
        return 0

    target = Path(args.target).expanduser() if args.target else Path.home() / ".qoder" / "skills"
    items = plan(SRC)
    print(f"[install] 源：{SRC}")
    print(f"[install] 目标：{target}")
    print(f"[install] 计划：{len(sk)} 个技能 + shared + {len(ROOT_FILES)} 个根级文件"
          f"（{'dry-run' if args.dry_run else '写入'}）")

    stats = {"copy": 0, "skip": 0}
    for src, rel in items:
        action = copy_item(src, target / rel, args.force, args.dry_run)
        stats[action] += 1
        if action == "skip":
            print(f"  skip {rel}（已存在；--force 可覆盖）")
    print(f"[install] 完成：写入 {stats['copy']}｜跳过 {stats['skip']}")

    if args.dry_run:
        return 0

    problems = check(target, SRC)
    if problems:
        print("[install] 自检失败：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"[install] 自检通过：{len(sk)} 个技能与治理文件均已到位")
    print(f"[install] 下一步：重启智能体宿主，然后直接提问。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
