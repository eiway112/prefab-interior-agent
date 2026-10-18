<div align="center">
  <img src="assets/logo.png" width="150" alt="装配式装修智能体技能合集">
  <h1>装配式装修智能体技能合集</h1>
  <p>面向装配式装修领域的智能体技能（Agent Skills）合集 —— 选型咨询 · 性能估算 · 机电协同 · 验收与合规</p>
  <p>
    <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
    <img src="https://img.shields.io/badge/skills-15-blue" alt="skills">
    <img src="https://img.shields.io/badge/spec-SKILL.md-blueviolet" alt="spec">
    <img src="https://img.shields.io/badge/governance-8%20files-orange" alt="governance">
  </p>
</div>

技能遵循通用的 `SKILL.md` 约定（YAML frontmatter ＋ Markdown 正文），可在支持该约定的智能体平台中加载复用。覆盖**隔墙、墙面、吊顶、楼地面、厨卫、收纳**六大部品系统的选型与施工咨询，配套**隔声计算引擎、机电管线协同、验收清单生成、规范标准复核**等专项能力，并内置完整治理文件体系（标准索引、接口契约、红线注册表、术语表、数据分级与变更治理规则）——每个性能数值尽量带**来源分级与核验状态**，估算输出强制携带**区间、风险与适用边界**。

## 目录

- [能看到什么](#能看到什么)
- [架构总览](#架构总览)
- [技能矩阵（15 个）](#技能矩阵15-个)
- [隔声计算引擎（ACE）](#隔声计算引擎ace)
- [治理与证据体系](#治理与证据体系)
- [快速开始](#快速开始)
- [目录结构](#目录结构)
- [行尾前提（最容易踩的一条）](#行尾前提最容易踩的一条)
- [运行环境](#运行环境)
- [版本与迭代策略](#版本与迭代策略)
- [引用与署名](#引用与署名)
- [边界与免责声明](#边界与免责声明)

## 能看到什么

把技能装进你的智能体平台后，直接用自然语言提问即可。合集以"总入口路由 + 专项技能协同"工作：综合问题由编排技能识别意图并分派，专项问题直达对应技能，跨部品问题走机电集成协调与集成校验。

| 你问 | 合集做什么 |
|---|---|
| "分户隔墙选什么方案能达到 Rw 45 dB？" | 隔墙技能给出候选构造对比（轻钢龙骨双空腔 / ALC 条板等），调用隔声计算引擎出**估算区间**并明示适用边界；超范围时按红线拒给确定数值 |
| "酒店卫生间用整体卫浴，防水底盘和排水坡度怎么控？" | 厨卫技能给出湿区防水接口、坡度与**两次闭水试验**等工序控制点及验收要点 |
| "地暖＋浮筑地面做完，面层标高会抬高多少？" | 楼地面技能按构造层次**逐层实算**标高增量，并提示不同口径不得混用 |
| "这个隔墙方案里的数值有依据吗？" | 标准复核技能对指标表逐项核规范版本、条文定位与数据档位（S1 国标条文 / S2 检测统计 / S3 经验常数 / S4 行业估算） |
| "帮我把这套方案的过程验收清单拉出来" | 验收清单技能按空间 × 部品系统 × 施工阶段生成可检查、可记录、可整改闭环的清单 |
| "管线在吊顶和隔墙空腔里怎么走才不冲突？" | 机电集成技能做跨部品管线协调、检修口布置与安装顺序编排 |

> 输出的确定性分级是设计出来的：**专业估算 / 粗略估算 / 风险审查优先 / 仅供方案比较**四档显式标注，估算一律带区间与关键假设；确定性性能数据以 CMA/CNAS 检测报告和现行标准为准。

## 架构总览

```mermaid
flowchart TD
    U(用户提问) --> OR[总入口·路由协调<br/>prefab-interior-systems-orchestrator]
    OR --> EXP[材料选型·综合咨询<br/>prefab-interior-materials-expert]
    subgraph PARTS [六大部品系统]
        PW[隔墙<br/>partition-wall-solution]
        WS[墙面<br/>wall-surface-system]
        CL[吊顶<br/>ceiling-system]
        FL[楼地面<br/>floor-system]
        BK[厨卫<br/>bathroom-kitchen-system]
        ST[收纳<br/>storage-system]
    end
    OR --> PARTS
    subgraph CALC [专项计算与协同]
        ACE[隔声计算引擎<br/>acoustic-calculation-engine]
        MEP[机电集成协调<br/>mep-integration-system]
    end
    PW -. "IC-03/08/09" .-> ACE
    FL -. "IC-03/08/09" .-> ACE
    CL -. "IC-03/08/09" .-> ACE
    PARTS -. "跨部品管线协同" .-> MEP
    subgraph QA [质量与合规]
        AC[验收清单生成器]
        SR[规范标准复核]
        SV[标准条文核验流水线]
        QT[技能质量测试]
    end
    GOV[(治理文件 shared/<br/>标准索引 · 接口契约 · 红线注册表<br/>术语表 · 数据分级 · 变更治理)]
    PARTS --- GOV
    QA --- GOV
    CALC --- GOV
```

三层协作：**部品技能**给方案与构造，**专项引擎**给可复算的性能估算，**质量合规技能**在出口把关（指标复核、红线拦截、验收闭环）；全部技能共同消费同一套治理文件，保证口径单源。技能间接口按编号契约（IC-xx）注册，请求/响应字段有单一事实源。

## 技能矩阵（15 个）

| 域 | 技能 | 解决什么 |
|---|---|---|
| 协调 | `prefab-interior-systems-orchestrator` | 合集总入口：意图识别、路由分派、跨专业冲突协调 |
| 协调 | `prefab-interior-materials-expert` | 材料选型、规范解读、质量问题诊断与综合咨询 |
| 部品 | `prefab-partition-wall-solution` | 分户墙/室内隔墙选型：双空腔轻钢龙骨、双排龙骨、ALC 条板等路线的隔声/耐火/成本对比与施工控制 |
| 部品 | `prefab-wall-surface-system` | 墙面系统：无龙骨点式调平、龙骨调平（GL/UL/LGS）、干法直挂与饰面板工艺 |
| 部品 | `prefab-ceiling-system` | 集成/干法/模块化吊顶：隔声贡献估算、管线协同、检修口与标高控制 |
| 部品 | `prefab-floor-system` | 浮筑/架空/干法调平/快装地面：撞击声改善评估、标高推算、管线分离 |
| 部品 | `prefab-bathroom-kitchen-system` | 整体卫浴与集成厨房：防水底盘、湿区接口、排水坡度、闭水试验、洁具安装 |
| 部品 | `prefab-storage-system` | 模块化收纳：玄关柜/衣柜/橱柜/卫浴柜的尺寸模数、墙地顶接口与安装顺序 |
| 专项 | `acoustic-calculation-engine` | 通用隔声计算引擎（M1—M7 物理模型，见下节） |
| 专项 | `prefab-mep-integration-system` | 机电管线集成：管线分离策略、检修路径、设备点位、安装顺序编排 |
| 质量 | `prefab-acceptance-checklist-generator` | 过程检查/隐蔽验收/分项验收/竣工交付清单生成与整改闭环 |
| 质量 | `prefab-standards-reviewer` | 技术指标表的规范标准核验：版本有效性、条文定位、数据档位 |
| 质量 | `scanned-standard-clause-verify` | 扫描版/文本版标准 PDF 的身份核验与条文原文核验流水线 |
| 质量 | `skill-qa-tester` | 技能质量测试：功能完整性、数据准确性、内部一致性、红线合规 |
| 治理 | `prefab-governance-sync` | 治理文件三层同步与发布流程 |

> `waterproofing-expert`（建筑防水工程技术顾问）已于 2026-08-14 迁出本合集，独立维护于建筑专业辅材技能合集项目；本合集各技能保留对其的跨合集外部协同引用。

## 隔声计算引擎（ACE）

隔声是本合集估算密度最高的专业面，单独抽成通用引擎，由隔墙/楼地面/吊顶技能经编号接口（IC-03 空气声 / IC-08 撞击声 / IC-09 侧向传声）调用：

| 层 | 内容 |
|---|---|
| 物理模型 M1—M7 | 质量定律 · Cremer 吻合效应 · 双叶墙 MSM 共振 · 空腔增益 · 撞击声改善量 · 声桥/缝隙修正 · 检测报告基准校准 |
| 可执行内核 | `acoustic-calculation-engine/ace_kernel.py`（Python 3.8+，仅标准库；唯一入口 `compute_request(ic_id, request)`，参数不足/超范围一律以降级结构返回而非抛错） |
| 输出纪律 | 恒标"专业估算"性质；强制携带估算区间、风险四件套、关键假设与适用边界声明；**超出模型适用范围时拒给确定数值**（红线） |
| 数据纪律 | 材料参数与基准表带来源分级（S1 国标条文 / S2 产品检测统计 / S3 经验常数 / S4 行业估算）；S2 及以下输出附数据源说明 |

## 治理与证据体系

本合集不只是一组提示词——每个技能背后是同一套可核查的治理文件（随仓分发，位于 `技能仓备份/shared/` 与 `技能仓备份/standards-index.md`）：

| 文件 | 承担什么 |
|---|---|
| `standards-index.md` | 规范标准索引：收录标准的编号、名称、效力状态（现行/废止/被替代）与**核验状态标注**；条数以该件头部计数现读为准 |
| `shared/redlines-registry.md` | 红线注册表：逐技能登记"不得违反"的输出约束（如超范围禁给确定数值、评价量不得混用），条数以该件现读为准 |
| `shared/interface-contracts.md` | 技能间接口契约（IC-xx 编号化）：请求/响应字段约束的单一事实源 |
| `shared/standards-reasoning-rules.md` | 标准适用性推理规则：废止标准降级、替代切换、地域适用性等判则 |
| `shared/data-classification.md` | 数据分类与取证判据：四类量（限值/派生量/物理参数/叙述）的来源与分级要求 |
| `shared/glossary.md` | 跨技能共用术语表（触发词与专业名词的统一口径） |
| `shared/change-governance.md` | 变更治理规则：S/A/B/C 定级、审批流程与变更日志 |
| `shared/platform-adapter-reference.md` | 平台适配参考：跨智能体平台加载的适配说明 |

配套机制（写进技能正文、可被提问直接触发）：**已废止标准自动降级并提示替代关系**；**实验室评价量（Rw+C）与现场评价量（DnT,w+C）不得混用**；**估算与检测值不得互替**；数值缺依据时显式降级而不是编造。

## 快速开始

1. 下载本仓库：在仓库页面点击 **Code → Download ZIP**，或执行
   - GitHub：`git clone https://github.com/eiway112/prefab-interior-agent.git`
   - Gitee：`git clone https://gitee.com/eiway112/prefab-interior-agent.git`
2. 将 `技能仓备份/` 下所需的技能目录复制到你所用平台的**技能加载目录**（例如 Qoder：`~/.qoder/skills/`；Windows 下为 `C:\Users\<你的用户名>\.qoder\skills\`。其他平台请以其文档为准）。
3. 如需完整合集能力（技能间相互调用与治理约束），请将全部技能目录连同 `技能仓备份/standards-index.md` 与 `技能仓备份/shared/` 一并复制。
4. 重启你的智能体平台，直接提问即可，例如："分户隔墙选什么方案能达到 Rw 45 dB？"

## 目录结构

```
prefab-interior-agent/
├── README.md / LICENSE / CHANGELOG.md   ← 说明 · MIT 许可 · 变更记录
├── assets/                              ← 品牌资源
└── 技能仓备份/                           ← 对外引用入口：复制这里
    ├── prefab-*/ · acoustic-*/ · scanned-*/ · skill-qa-tester/
    │                                    ← 15 个技能目录（SKILL.md ＋ reference/examples 等）
    ├── standards-index.md               ← 规范标准索引（含核验状态标注）
    ├── platform-adapter-reference.md    ← 平台适配参考
    └── shared/                          ← 治理文件 8 件（契约·红线·术语·数据分级·变更治理等）
```

> 本仓是**技能分发面**：只含技能本体与治理文件镜像。开发仓的过程记录、维护脚本与校验门禁（治理登记、批凭据、各道验证脚本）不随本仓分发。

## 行尾前提（最容易踩的一条）

仓根 `.gitattributes` 声明 `* text eol=lf`，请保留。你的机器若设了 `core.autocrlf=true`（Windows 上常见，且可能是全局而非仓库级），删掉或绕过该文件会让清洁克隆的工作树整体转成 CRLF，从而与文件内部记录的字节形态不一致。自验法：`git ls-files --eol | awk '{print $1}' | sort | uniq -c`——文本件应全部落 `i/lf`。

## 运行环境

技能为纯文本资产，直接复制即可用，无运行依赖。ACE 可执行内核 `ace_kernel.py` 仅用 Python 3.8+ 标准库。若后续要自建校验，Python 3.10+ 可跑（部分维护脚本有 PEP 604 签名）。

## 版本与迭代策略

- 合集整体采用语义化版本 `MAJOR.MINOR.PATCH`，每个发布基线打 git 标签（如 `v1.0.0`），历史变更见 [CHANGELOG.md](CHANGELOG.md)。
- 规范标准修订、技能内容勘误以 PATCH/MINOR 发布；架构或契约级调整以 MAJOR 发布。
- 引用时建议锁定到具体标签（下载 tag 对应的源码包），避免跟随主干拿到中间状态。
- 反馈与纠错：欢迎开 Issue（使用问题、数据纠错、标准失效线索均可）；后续演进方向为单技能独立版本发布（各技能 frontmatter 已内置 version 字段）与 Releases 分发打包产物。

## 引用与署名

本仓库采用 [MIT License](LICENSE)。复制、引用或二次开发请保留版权声明；建议引用格式：

> 装配式装修智能体技能合集 vX.Y.Z，艾为智造（长沙）科技有限公司，https://github.com/eiway112/prefab-interior-agent

## 边界与免责声明

- 技能输出为工程技术参考意见，不替代注册执业人员的正式设计文件。
- 隔声、耐火等确定性性能数据须以 CMA/CNAS 检测报告和现行国家/行业标准为准；技能内置估算模型仅用于方案比较。
- 项目落地前请结合工程所在地法规、设计文件与产品实际参数核实。
