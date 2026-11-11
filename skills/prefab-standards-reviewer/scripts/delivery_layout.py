#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付布局解析 —— 同一份工具字节在开发仓／运行时技能目录／公开包三处各取自己的根。

判据＝**结构正向识别**，不靠环境变量、不靠 cwd：工具目录名为 `scripts` 且其上一级
有 `SKILL.md`，即判为随包布局（技能目录的识别口径与 `install.py` 的 `skills()` 一致）；
否则判为开发仓布局（工具住 `维护脚本/`）。

两种布局只差四个常量：根、索引相对路径、证据卡目录相对路径与卡名前缀、通道源相对路径。
常量一律保持**相对**形制，消费侧仍按 `root / REL` 取值——这样 `--root` 覆盖与测试侧
对卡根的注入（`patch.object(answer_to_case, "CARD_ROOT", …)`）在两种布局下语义不变。

随包布局的落点（由 `sync_skill_backup.py` 的 TOOL_BUNDLE 声明清单交付）：
    <技能>/scripts/{citation_ledger,answer_to_case,deliverable_render,
                    on_demand_retrieval,delivery_layout}.py
    <技能>/scripts/retrieval_channels.json      ← 开发仓 维护脚本/配置/标准取真通道源.json
    <技能>/evidence/card_<safe_id>.json         ← 开发仓 核验成果/证据卡_<safe_id>.json
    <技能>/../shared/standards-index.md         ← 随包治理八件之一（install.py 已强制在场）

公开面路径必须 ASCII（`derive_public_face.py` fail-closed），故通道源与证据卡的**交付名**
为 ASCII；开发仓侧仍用中文原名，映射只住在 TOOL_BUNDLE 一处。
"""
from __future__ import annotations

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# 技能目录的识别口径＝有 SKILL.md（与 install.py 的 skills() 同判据）
PACKAGED = SCRIPT_DIR.name == "scripts" and (SCRIPT_DIR.parent / "SKILL.md").is_file()

if PACKAGED:
    ROOT_DEFAULT = SCRIPT_DIR.parent
    INDEX_REL = Path("../shared/standards-index.md")
    CARD_DIR_REL = Path("evidence")
    CARD_PREFIX = "card_"
    REGISTRY_REL = Path("scripts/retrieval_channels.json")
    LEDGER_DEFAULT_REL = Path("evidence/citation_ledger.json")
else:
    ROOT_DEFAULT = SCRIPT_DIR.parent
    INDEX_REL = Path("治理文件/standards-index.md")
    CARD_DIR_REL = Path("成果/标准核验")
    CARD_PREFIX = "证据卡_"
    REGISTRY_REL = Path("维护脚本/配置/标准取真通道源.json")
    LEDGER_DEFAULT_REL = Path("核验成果/标准引用台账.json")


def card_name(safe_id: str) -> str:
    """证据卡文件名（不含目录）。开发仓＝`证据卡_<id>.json`，随包＝`card_<id>.json`。"""
    return f"{CARD_PREFIX}{safe_id}.json"


def card_glob() -> str:
    return f"{CARD_PREFIX}*.json"


def describe() -> dict:
    """现读布局，供 `--layout` 与门禁取数；不写盘、不联网。"""
    return {
        "packaged": PACKAGED,
        "script_dir": str(SCRIPT_DIR).replace("\\", "/"),
        "root_default": str(ROOT_DEFAULT).replace("\\", "/"),
        "index_rel": INDEX_REL.as_posix(),
        "card_dir_rel": CARD_DIR_REL.as_posix(),
        "card_prefix": CARD_PREFIX,
        "registry_rel": REGISTRY_REL.as_posix(),
        "index_exists": (ROOT_DEFAULT / INDEX_REL).exists(),
        "card_dir_exists": (ROOT_DEFAULT / CARD_DIR_REL).exists(),
        "registry_exists": (ROOT_DEFAULT / REGISTRY_REL).exists(),
    }


if __name__ == "__main__":
    import json
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(json.dumps(describe(), ensure_ascii=False, indent=2))