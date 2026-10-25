# tools/

两个脚本，只用 Python 标准库，不需要装依赖、不联网。

| 命令 | 作用 |
|---|---|
| `python tools/install.py` | 把 15 个技能＋`shared/`＋标准索引装到 `~/.qoder/skills`（可用 `--target` 改目录、`--dry-run` 只看计划） |
| `python tools/smoke_test.py` | 自检：SKILL.md、frontmatter、`../shared/` 引用、8 份治理文件、标准索引 |

装完再跑一次 `smoke_test.py --target <安装目录>`，可验证安装结果完整。

要求：Python 3.10+。
