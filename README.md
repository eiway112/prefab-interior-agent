# 装配式装修智能体技能合集

面向装配式装修领域的 Qoder 智能体技能库。覆盖隔墙、墙面、吊顶、楼地面、厨卫、收纳六大部品系统的选型与施工咨询，配套声学计算、机电管线协同、验收清单、规范标准复核等专项能力，并内置完整的治理文件体系（标准索引、接口契约、红线注册表、术语表、变更治理规则）。

技能合集以"总入口路由 + 专项技能协同"为架构：综合问题由编排技能识别意图并分派，专项问题直达对应技能，跨部品问题走机电集成协调与集成校验。

## 快速开始

1. 下载本仓库：在仓库页面点击 **Code → Download ZIP**，或执行
   - GitHub：`git clone https://github.com/eiway112/prefab-interior-agent.git`
   - Gitee：`git clone https://gitee.com/eiway112/prefab-interior-agent.git`
2. 将 `技能仓备份/` 下所需的技能目录复制到 Qoder 技能目录：
   - Windows：`C:\Users\<你的用户名>\.qoder\skills\`
   - macOS / Linux：`~/.qoder/skills/`
3. 如需完整合集能力（技能间相互调用与治理约束），请将全部技能目录连同 `技能仓备份/standards-index.md` 与 `技能仓备份/shared/` 一并复制。
4. 重启 Qoder，直接向智能体提问即可，例如："分户隔墙选什么方案能达到 Rw 45 dB？"

## 目录结构

| 目录 / 文件 | 说明 |
|---|---|
| `技能仓备份/` | **对外引用入口**：15 个技能本体 + 治理文件完整镜像，可直接复制使用 |
| `技能仓备份/standards-index.md` | 规范标准索引（现行标准登记与核验记录；条数随批次浮动，以该件头部计数现读为准） |
| `技能仓备份/shared/` | 治理文件：变更治理、术语表、接口契约、红线注册表、平台适配参考 |
| `README.md` / `LICENSE` / `CHANGELOG.md` | 说明、许可（MIT）与变更记录 |

> 本仓是**技能分发面**：只含技能本体与治理文件镜像。开发仓的过程记录、维护脚本与校验门禁（治理登记、批凭据、各道验证脚本）不随本仓分发。

## 行尾前提（最容易踩的一条）

仓根 `.gitattributes` 声明 `* text eol=lf`，请保留。你的机器若设了 `core.autocrlf=true`（Windows 上常见，且可能是全局而非仓库级），删掉或绕过该文件会让清洁克隆的工作树整体转成 CRLF，从而与文件内部记录的字节形态不一致。自验法：`git ls-files --eol | awk '{print $1}' | sort | uniq -c`——文本件应全部落 `i/lf`。

## 运行环境

技能为纯文本资产，直接复制即可用，无运行依赖。若后续要自建校验，Python 3.10+ 可跑（部分维护脚本有 PEP 604 签名）。

## 技能清单（15 个）

**协调与综合咨询**

- `prefab-interior-systems-orchestrator` — 合集总入口与路由协调器
- `prefab-interior-materials-expert` — 材料选型与综合技术咨询

**部品系统（6 个）**

- `prefab-partition-wall-solution` — 装配式隔墙方案决策与验证
- `prefab-wall-surface-system` — 装配式墙面系统选型与施工指导
- `prefab-ceiling-system` — 装配式吊顶系统
- `prefab-floor-system` — 装配式楼地面系统（浮筑/架空/干法调平）
- `prefab-bathroom-kitchen-system` — 整体卫浴与集成厨房
- `prefab-storage-system` — 模块化收纳系统

**专项计算与协同**

- `acoustic-calculation-engine` — 隔声计算引擎（空气声/撞击声、MSM 模型、吻合效应）
- `prefab-mep-integration-system` — 机电管线集成与跨部品协同

**质量与合规**

- `prefab-acceptance-checklist-generator` — 验收清单生成器
- `prefab-standards-reviewer` — 技能技术指标的规范标准复核
- `scanned-standard-clause-verify` — 标准条文原文核验流水线
- `skill-qa-tester` — 技能质量测试与验证

**治理**

- `prefab-governance-sync` — 治理文件三层同步与发布流程

`waterproofing-expert`（建筑防水工程技术顾问）已于 2026-08-14 迁出本合集，独立维护于建筑专业辅材技能合集项目；本合集各技能保留对其的跨合集外部协同引用。

## 版本与迭代策略

- 合集整体采用语义化版本 `MAJOR.MINOR.PATCH`，每个发布基线打 git 标签（如 `v1.0.0`），历史变更见 [CHANGELOG.md](CHANGELOG.md)。
- 规范标准修订、技能内容勘误以 PATCH/MINOR 发布；架构或契约级调整以 MAJOR 发布。
- 引用时建议锁定到具体标签（下载 tag 对应的源码包），避免跟随主干拿到中间状态。
- 后续演进方向：单技能独立版本对外发布（各技能 frontmatter 已内置 version 字段）、通过 Releases 分发打包产物、Issues 接收使用反馈与数据纠错。

## 引用与署名

本仓库采用 [MIT License](LICENSE)。复制、引用或二次开发请保留版权声明；建议引用格式：

> 装配式装修智能体技能合集 vX.Y.Z，艾为智造（长沙）科技有限公司，https://github.com/eiway112/prefab-interior-agent

## 边界与免责声明

- 技能输出为工程技术参考意见，不替代注册执业人员的正式设计文件。
- 隔声、耐火等确定性性能数据须以 CMA/CNAS 检测报告和现行国家/行业标准为准；技能内置估算模型仅用于方案比较。
- 项目落地前请结合工程所在地法规、设计文件与产品实际参数核实。
