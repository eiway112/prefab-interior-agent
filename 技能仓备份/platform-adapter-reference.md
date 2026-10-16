# 双平台动作适配说明

> **文件定位**：本文件为双平台动作适配说明的单一事实源（SOT）。优先使用本文件作为平台无关动作到 Qoder/Codex 工具的映射参考。
>
> **镜像说明**：`shared/platform-adapter-reference.md` 是本文件的只读镜像，用于兼容 `prefab-partition-wall-solution` 等历史引用。修改请以本文件为准，并同步更新镜像。
本文件用于让 Qoder 与 Codex 共用同一套技能正文。技能正文应优先描述“动作”，避免写死平台工具名。

## 动作映射

| 平台无关动作 | Qoder 常见工具 | Codex 常见工具/做法 |
|---|---|---|
| 列出技能文件 | Glob | PowerShell `Get-ChildItem` 或 `rg --files` |
| 读取技能文件 | Read | `Get-Content`、文件读取工具 |
| 搜索技能内容 | Grep | `rg`、`Select-String` |
| 搜索官方标准信息 | WebSearch | web search，优先官方标准平台和主管部门网站 |
| 精确修改文件 | Edit | `apply_patch` |
| 新建文件 | Write | `apply_patch` 新增文件 |
| 并行测试用例 | Task | 子代理工具或多工具并行执行 |
| 生成测试报告 | Write | `apply_patch` 或输出到约定目录 |

## 编写规则

1. `SKILL.md` 中使用“读取文件”“搜索内容”“生成报告”等动作名。
2. 只有在平台专属参考文件中说明具体工具名。
3. 当平台缺少某个工具时，采用等价动作，不改变技能目标。
4. 不把文件修改作为标准复核技能的默认行为；复核技能默认输出建议，是否修改由用户或执行代理确认。
