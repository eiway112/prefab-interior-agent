#!/usr/bin/env python3
"""安装、核对与恢复技能包；只依赖 Python 标准库。"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import uuid

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "skills"
ROOT_FILES = ["standards-index.md", "platform-adapter-reference.md"]
SHARED_EXPECTED = [
    "change-governance.md", "data-classification.md", "glossary.md",
    "interface-contracts.md", "platform-adapter-reference.md", "redlines-registry.md",
    "standards-index.md", "standards-reasoning-rules.md",
]
STATE = ".prefab-interior-install.json"
LOCK = ".prefab-interior-install.lock"
BACKUPS = ".prefab-interior-backups"


class InstallError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def safe_path(root: Path, relative: str = "") -> Path:
    if relative:
        parts = PurePosixPath(relative).parts
        if (not parts or PurePosixPath(relative).is_absolute()
                or "/".join(parts) != relative
                or any(p in {".", ".."} or any(c in p for c in '\\:*?"<>|')
                       or p.endswith((".", " ")) for p in parts)):
            raise InstallError(f"非法相对路径：{relative}")
    result = root / relative
    for path in reversed([result, *result.parents]):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise InstallError(f"不处理符号链接或目录联接：{path}")
        if path != result and not stat.S_ISDIR(info.st_mode):
            raise InstallError(f"父路径不是目录：{path}")
    return result


def file_bytes(path: Path) -> bytes | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise InstallError(f"应为普通文件：{path}")
    return path.read_bytes()


def skills(src: Path) -> list[Path]:
    safe_path(src)
    return sorted(p for p in src.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def package(src: Path) -> dict[str, bytes]:
    if not src.is_dir():
        raise InstallError(f"找不到技能包：{src}")
    roots = skills(src)
    if not roots or not (src / "shared").is_dir() or not (src / ROOT_FILES[0]).is_file():
        raise InstallError("技能包不完整：须有技能、shared 与标准索引")
    for p in src.iterdir():
        safe_path(src, p.name)
        if p.is_dir() and p.name != "shared" and p not in roots:
            raise InstallError(f"技能目录缺少 SKILL.md：{p.name}")
    for name in SHARED_EXPECTED:
        p = safe_path(src, "shared/" + name)
        if not p.is_file() or not p.read_bytes().strip():
            raise InstallError(f"缺少或为空的治理文件：{name}")
    if not (src / ROOT_FILES[0]).read_bytes().strip():
        raise InstallError("标准索引不得为空")
    files = {}
    for root in [*roots, src / "shared"]:
        safe_path(root)
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(src).as_posix()
            safe_path(src, rel)
            if p.is_file():
                files[rel] = p.read_bytes()
            elif not p.is_dir():
                raise InstallError(f"技能包含非普通文件：{rel}")
    for name in ROOT_FILES:
        p = safe_path(src, name)
        if p.exists():
            files[name] = p.read_bytes()
    if not any(name.startswith("shared/") for name in files):
        raise InstallError("shared 不得为空")
    return files


def manifest(files: dict[str, bytes]) -> dict:
    hashes = {name: digest(data) for name, data in sorted(files.items())}
    return {"schema": 1, "package_id": digest(encode(hashes)), "files": hashes}


def managed_path(root: Path, name: str) -> Path:
    path = safe_path(root, name)
    top = PurePosixPath(name).parts[0]
    if name not in ROOT_FILES and name != STATE:
        if "/" not in name or not (top.startswith("prefab-") or top in {
                "shared", "acoustic-calculation-engine", "scanned-standard-clause-verify",
                "skill-qa-tester"}):
            raise InstallError(f"路径不属于本技能包：{name}")
    return path


def read_state(target: Path) -> dict | None:
    raw = file_bytes(safe_path(target, STATE))
    if raw is None:
        return None
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {"schema", "package_id", "files"}
            or value["schema"] != 1 or not isinstance(value["files"], dict)):
        raise InstallError("安装清单格式无效")
    for name, sha in value["files"].items():
        managed_path(target, name)
        if name == STATE or not isinstance(sha, str) or len(sha) != 64:
            raise InstallError("安装清单文件指纹无效")
    if digest(encode(value["files"])) != value["package_id"]:
        raise InstallError("安装清单指纹不一致")
    return value


def changes(target: Path, files: dict[str, bytes]):
    old = read_state(target)
    old_hashes = old["files"] if old else {}
    desired = dict(files)
    desired[STATE] = encode(manifest(files))
    result, conflicts = {}, []
    for name in sorted(set(desired) | set(old_hashes), key=lambda n: (n == STATE, n)):
        before = file_bytes(managed_path(target, name))
        after = desired.get(name)
        if before == after:
            continue
        result[name] = (before, after)
        if name == STATE:
            continue
        expected = old_hashes.get(name)
        if before is not None and (expected is None or digest(before) != expected):
            conflicts.append(name)
        elif before is None and expected is not None and after is not None:
            conflicts.append(name)
    return result, conflicts


def check(target: Path, src: Path) -> list[str]:
    try:
        files = package(src)
        problems = []
        for name, data in files.items():
            if file_bytes(managed_path(target, name)) != data:
                problems.append(f"缺失或内容不一致：{name}")
        old = read_state(target)
        if old is not None and old != manifest(files):
            problems.append("安装清单与当前技能包不一致")
        return problems
    except (OSError, ValueError, TypeError) as exc:
        return [str(exc)]


def verify_contents(target: Path, expected: dict[str, bytes | None]):
    drift = [name for name, data in expected.items()
             if file_bytes(managed_path(target, name)) != data]
    if drift:
        raise InstallError("操作期间出现外部改动；内容不一致：" + "、".join(drift))


def atomic_write(path: Path, data: bytes | None):
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".prefab-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def installation_lock(target: Path):
    safe_path(target)
    target.mkdir(parents=True, exist_ok=True)
    path = safe_path(target, LOCK)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InstallError("该目录有另一安装或恢复操作，稍后重试") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def transaction(target: Path, edits: dict, backup_root: Path) -> Path:
    safe_path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / f"update-{uuid.uuid4().hex}.json"
    journal = {"schema": 1, "target": str(target), "files": {name: {
        "before": None if before is None else base64.b64encode(before).decode("ascii"),
        "after_sha256": None if after is None else digest(after),
    } for name, (before, after) in edits.items()}}
    journal["sha256"] = digest(encode(journal))
    with backup.open("xb") as stream:
        stream.write(encode(journal))
        stream.flush()
        os.fsync(stream.fileno())
    print(f"[install] 恢复备份：{backup}")
    applied = []
    try:
        for name in sorted(edits, key=lambda n: (n == STATE, n)):
            before, after = edits[name]
            path = managed_path(target, name)
            if file_bytes(path) != before:
                raise InstallError(f"操作期间文件发生变化：{name}")
            atomic_write(path, after)
            applied.append(name)
        for name, (_, after) in edits.items():
            if file_bytes(managed_path(target, name)) != after:
                raise InstallError(f"写入后内容核对失败：{name}")
    except BaseException:
        for name in reversed(applied):
            before, after = edits[name]
            path = managed_path(target, name)
            if file_bytes(path) != after:
                raise InstallError(f"恢复前发现外部改动，已保留备份：{backup}")
            atomic_write(path, before)
        raise
    return backup


def install(src: Path, target: Path, *, force=False, dry=False,
            backup_root: Path | None = None) -> Path | None:
    safe_path(src)
    safe_path(target)
    src = Path(os.path.abspath(src))
    target = Path(os.path.abspath(target))
    if target == src or target in src.parents or src in target.parents:
        raise InstallError("安装目录与技能包源目录不得重叠")
    files = package(src)
    backup_root = backup_root or target.parent / (target.name + BACKUPS)
    safe_path(backup_root)
    backup_root = Path(os.path.abspath(backup_root))
    if backup_root == src or src in backup_root.parents or backup_root in src.parents:
        raise InstallError("备份目录不得与技能包源目录重叠")
    if backup_root == target or target in backup_root.parents:
        raise InstallError("备份目录必须位于技能安装目录之外")
    with installation_lock(target) if not dry else nullcontext():
        edits, conflicts = changes(target, files)
        for name, (before, after) in edits.items():
            action = "移除旧包文件" if after is None else "新增" if before is None else "替换"
            print(f"[install] {action} {name}")
        if conflicts:
            print("[install] 已有未受管或本地修改文件：" + "、".join(conflicts))
            if not force:
                raise InstallError("未改写内容；请先核对 --dry-run，确认后以 --force 备份并替换")
        if dry:
            print("[install] 仅预览，未写入")
            return None
        expected = {**files, STATE: encode(manifest(files))}
        if not edits:
            verify_contents(target, expected)
            print("[install] 内容已一致，无需更新")
            return None
        backup = transaction(target, edits, backup_root)
        verify_contents(target, expected)
        print(f"[install] 内容逐字节一致，包标识：{manifest(files)['package_id']}")
        return backup


def restore(backup: Path, target: Path, *, dry=False) -> Path | None:
    safe_path(target)
    target = Path(os.path.abspath(target))
    safe_path(backup)
    journal = json.loads(backup.read_bytes())
    if (not isinstance(journal, dict) or set(journal) != {"schema", "target", "files", "sha256"}
            or journal["schema"] != 1 or journal["target"] != str(target)
            or not isinstance(journal["files"], dict) or not journal["files"]):
        raise InstallError("恢复备份格式无效或目标目录不匹配")
    checksum = journal.pop("sha256")
    if digest(encode(journal)) != checksum:
        raise InstallError("恢复备份已损坏或被修改")
    with installation_lock(target) if not dry else nullcontext():
        edits, expected = {}, {}
        for name, entry in journal["files"].items():
            path = managed_path(target, name)
            if not isinstance(entry, dict) or set(entry) != {"before", "after_sha256"}:
                raise InstallError("恢复条目格式无效")
            before = None if entry["before"] is None else base64.b64decode(entry["before"], validate=True)
            expected[name] = before
            current = file_bytes(path)
            if current == before:
                continue
            sha = None if current is None else digest(current)
            if sha != entry["after_sha256"]:
                raise InstallError(f"安装后已有新改动，拒绝覆盖：{name}")
            edits[name] = (current, before)
        if dry or not edits:
            if not dry:
                verify_contents(target, expected)
            print(f"[restore] 待恢复 {len(edits)} 件；未写入")
            return None
        result = transaction(target, edits, target.parent / (target.name + BACKUPS))
        verify_contents(target, expected)
        print(f"[restore] 已恢复 {len(edits)} 件；恢复前内容也已备份")
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description="安装、核对与恢复装配式装修技能包")
    ap.add_argument("--target", type=Path, default=Path.home() / ".qoder" / "skills")
    ap.add_argument("--force", action="store_true", help="备份后替换本地修改或旧版未受管文件")
    ap.add_argument("--backup-dir", type=Path, help="备份目录（默认安装目录旁的同名 .prefab-interior-backups 目录）")
    ap.add_argument("--dry-run", action="store_true", help="只读预览")
    ap.add_argument("--list", action="store_true", help="列出包内技能")
    ap.add_argument("--restore", type=Path, help="从本工具生成的备份恢复；须显式指定原目标目录")
    args = ap.parse_args()
    try:
        if args.restore:
            if args.force or args.list or args.backup_dir:
                ap.error("--restore 不能与 --force、--list、--backup-dir 合用")
            restore(args.restore.expanduser().absolute(), args.target.expanduser(), dry=args.dry_run)
        elif args.list:
            for p in skills(SRC):
                print(p.name)
        else:
            install(SRC, args.target.expanduser(), force=args.force, dry=args.dry_run,
                    backup_root=args.backup_dir.expanduser().absolute() if args.backup_dir else None)
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"[install] FAIL：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
