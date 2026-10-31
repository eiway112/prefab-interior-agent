<div align="center">
  <img src="assets/logo.png" width="150" alt="装配式装修智能体技能合集">
  <h1>装配式装修智能体技能合集</h1>
  <p>把一个垂直领域的专家判据，做成智能体可加载、可审计、可复用的知识资产</p>
  <p>
    <img src="https://img.shields.io/badge/version-v1.5.0-orange" alt="version">
    <img src="https://img.shields.io/badge/skills-15-blue" alt="skills">
    <img src="https://img.shields.io/badge/python-3.10%2B-yellow" alt="python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
    <a href="https://github.com/eiway112/prefab-interior-agent/actions/workflows/smoke.yml"><img src="https://github.com/eiway112/prefab-interior-agent/actions/workflows/smoke.yml/badge.svg" alt="CI"></a>
  </p>
</div>

> **一句话**：装配式装修的选型、构造、机电协同、验收与规范复核，做成了 15 个智能体技能。它和「直接问大模型」的区别不在会不会说，而在**说得出依据、也说得出边界**——每条数值带来源档位与核验状态，够不着的地方明确拒答。

## 先看它怎么拒绝人

下面不是设计文档里的假设，是 [`examples/03`](examples/03-mep-redline-check.md) 里技能对一句常见施工提问的**真实应答**（原样节选，未润色）：

> 用户：卫生间给水管直接从防水层穿过去接花洒，可以吗？

```markdown
[MI-R-P0-4 触发] 严禁管线无套管穿越防水层

管线穿越厨卫防水层时必须设置套管，并做密封处理，否则将破坏防水层，造成渗漏隐患。
穿楼板的防水套管应高出装饰层完成面且高度不应小于 20mm
（S1：GB 55030-2022 第4.6.6条第3款，2026-09-25 官方出版物核验），
套管与管道间的密封做法系本合集接口控制要求（S3）。

**因此，该做法不可直接实施。**

**必须完成的事项**：
- 防水施工前完成套管预埋
- 套管与管线间填充密封材料
- 防水附加层覆盖套管根部
- 闭水试验合格后方可封板
```

一个红线编号、一条带核验日期的国标条文出处、一个数值属于哪一档来源、一句明确的「不可直接实施」，以及改对了要做什么。**多数通用大模型在这里也会给你一段读起来很顺的做法——区别是它没有红线编号、没有条文出处，也没有档位。**

隔声维度上它甚至更不讨喜——直接不给数。同一份方案验证输出里，那一行逐字长这样（[`examples/01`](examples/01-partition-wall-verification.md)）：

```markdown
| 隔声性能 | DnT,w+C≥50 dB | 历史演示：Rw+C≈52-57 dB（估算，不可引用） | 🔲（PL-229 暂停期不判估算） | 无经验证定量预测；按 §3.A 衰减 5-10 dB 折算现场下限不足，须现场检测 |
```

旧口径的估算值留着但打上「不可引用」，当前判定给 🔲，再写清为什么不能引用、要补什么才能引用。**代价先说清楚：它有时不给数，而这正是它敢给数的部分可以用的前提。**

## 30 秒跑起来

```sh
git clone https://github.com/eiway112/prefab-interior-agent.git
cd prefab-interior-agent
python tools/install.py                              # 装到 ~/.qoder/skills，--target 可改目录
python tools/smoke_test.py --target ~/.qoder/skills  # 自检：15 技能 + 8 份治理文件
```

重启智能体宿主，直接提问。第一句建议问「酒店客房整体卫浴，SMC、彩钢板、瓷砖饰面三条路线怎么选？」——这条能拿到完整的选型对比与条文级依据；想看它怎么处理隔声类问题，先读 [`examples/01`](examples/01-partition-wall-verification.md)。需要 Python 3.10+，**零第三方依赖**。

不想装？[`examples/`](examples/) 放了四份技能**真实应答样例**，由开发仓从各技能自己的示例件**原样抽取**，不改写、不补数值：

| 示例 | 演示什么 |
|---|---|
| [01 隔墙方案验证](examples/01-partition-wall-verification.md) | 11 维逐项验证，隔声维度标 🔲 与「不可引用」，并给出核实路径 |
| [02 厨卫选型对比](examples/02-bathroom-kitchen-selection.md) | 三条技术路线对比，防水与排水给到条文级依据与来源档位 |
| [03 机电红线拦截](examples/03-mep-redline-check.md) | 触碰红线时直接拒给做法，并给合规替代与依据（本页开头那段） |
| [04 标准边界核验](examples/04-standards-review.md) | 逐条核「≥」与「＞」是否与标准原文表述一致 |

手动安装、更新与恢复见[快速开始](#快速开始)。

## 一眼看完

| 维度 | 现状 | 出处 |
|---|---|---|
| 技能 | 15 个（六大部品系统 + 计算/协同/验收/复核专项） | [技能矩阵](#技能矩阵15-个) |
| 加载形态 | 通用 `SKILL.md` 约定（YAML frontmatter ＋ Markdown 正文），不绑定单一平台 | 各技能目录 |
| 治理文件 | 8 份（标准索引、接口契约、红线注册表、术语表、数据分级、变更治理等） | `skills/shared/` |
| 标准收录 | 93 条（v1.4.0 现读），逐条带效力状态与核验状态 | [`skills/standards-index.md`](skills/standards-index.md) |
| 随包文件 | 99 个 | [公开面边界](#公开面边界随包--不随包) |
| 可跑测试 | 109 例，纯标准库，装完即可复跑 | [质量与测试](#质量与测试) |
| CI | Ubuntu／Windows × Python 3.10／3.12，每次 push 实跑 | `.github/workflows/smoke.yml` |
| 运行依赖 | 0（技能正文是 Markdown，引擎只用标准库） | [运行环境](#运行环境) |
| 未核项 | 公开，不藏缺口 | [`verification/`](verification/) |
| 许可 | MIT，引用请保留版权声明 | [引用与署名](#引用与署名) |

> 标准收录数、核验状态分布等会随版本变化的数字，一律以 [`verification/metrics.json`](verification/metrics.json) 的派生时现读为准；本表只是入口，不是这些数字的第二个事实源。

## 你问一句，它答什么

| 你问 | 合集做什么 |
|---|---|
| “分户隔墙选什么方案能达到 Rw 45 dB？” | 隔墙技能给出候选构造对比（轻钢龙骨双空腔 / ALC 条板等），调用隔声计算引擎给出**诊断、风险识别与适用边界**（系统定量预测现阶段暂停，可引用匹配的检测报告值）；超范围时按红线拒给确定数值 |
| “酒店卫生间用整体卫浴，防水底盘和排水坡度怎么控？” | 厨卫技能给出湿区防水接口、坡度与**两次闭水试验**等工序控制点及验收要点 |
| “厨房地柜、吊柜的深度和台面高度按什么标准？” | 收纳技能按已核验条文给出取值：地柜／水槽柜深度 550～600mm（JG/T 219-2017 5.3.1）、吊柜深度 320／350mm（5.3.2）、台面高 800～850mm（JGJ/T 477-2018 4.3.2）；吊柜深 300mm 与高柜高 2400mm 明确标注为行业做法、非标准系列值 |
| “地暖＋浮筑地面做完，面层标高会抬高多少？” | 楼地面技能按构造层次**逐层实算**标高增量，并提示不同口径不得混用 |
| “这个隔墙方案里的数值有依据吗？” | 标准复核技能对指标表逐项核规范版本、条文定位与数据档位（S1 国标条文 / S2 检测统计 / S3 经验常数 / S4 行业估算） |
| “帮我把这套方案的过程验收清单拉出来” | 验收清单技能按空间 × 部品系统 × 施工阶段生成可检查、可记录、可整改闭环的清单，并可导出**带版本与 SHA-256 的 XLSX 8 表 / DOCX 方案摘要**；缺编制依据即拒绝导出 |
| “这条吊顶换成更耐修的，十年后谁更便宜？” | 吊顶／楼地面技能按「硬约束淘汰 → 非支配集 → 条件排序与翻转点」比较，并给耐久·维修·更换维度（失效原因、是否拆面层、是否需停用）；全生命周期成本缺任一项输入即判「不能判」，不设默认频次、不估寿命 |
| “管线在吊顶和隔墙空腔里怎么走才不冲突？” | 机电集成技能做跨部品管线协调、检修口布置与安装顺序编排 |

## 为什么不是直接问大模型

垂直领域的真实风险不是模型说得不够多，而是**它说错的时候和说对的时候一样自信**。下面是四个具体失效形态，以及本合集各自的机制承担者：

| 通用模型的失效形态 | 本合集的机制 | 在哪一层 |
|---|---|---|
| 编一个看起来专业的数值，语气和真数据一样 | 每条数值标来源档位（S1 国标条文／S2 检测统计／S3 经验常数／S4 行业估算），无档位的数不用 | `shared/data-classification.md` |
| 拿废止标准当现行标准引用 | 已废止标准自动降级并提示替代关系；标准逐条记效力状态 | `standards-index.md` ＋ `shared/standards-reasoning-rules.md` |
| 把实验室测出来的 Rw+C 当成现场交付的 DnT,w+C | 评价量不得混用是注册在案的红线，跨口径时先纠正提问 | `shared/redlines-registry.md` |
| 模型算不准仍然给区间，读者无从判断可信度 | 隔声系统定量预测**整体暂停**（PL-229），只返回诊断、风险与适用边界；恢复须等匹配可溯源的检测证据 | `acoustic-calculation-engine` |

还有一条更根本的：**这些判据不在提示词里，在 8 份可核查的治理文件里。** 15 个技能读同一套口径，所以「A 技能说 20mm、B 技能说 25mm」这类自相矛盾有单一裁决处，而不是靠模型当场圆。

## 能力地图

```mermaid
flowchart TD
    U([用户提问]) --> OR[总入口]
    OR --> EXP[材料咨询]

    subgraph PARTS [六大部品系统]
        direction LR
        PW[隔墙] ~~~ WS[墙面] ~~~ CL[吊顶]
        FL[楼地面] ~~~ BK[厨卫] ~~~ ST[收纳]
    end
    OR --> PARTS

    subgraph CALC [专项计算与协同]
        direction LR
        ACE[隔声引擎] ~~~ MEP[机电协同]
    end
    PW -. "IC-03/08/09" .-> ACE
    FL -. "IC-03/08/09" .-> ACE
    CL -. "IC-03/08/09" .-> ACE
    PARTS -. "跨部品协同" .-> MEP

    subgraph QA [质量与合规出口]
        direction LR
        AC[验收清单] ~~~ SR[标准复核] ~~~ SV[条文核验] ~~~ QT[技能 QA]
    end
    PARTS --> QA
    CALC --> QA

    GOV[(治理文件<br/>shared/ 8 件)]
    PARTS --- GOV
    CALC --- GOV
    QA --- GOV
```

三层协作：**部品技能**给方案与构造，**专项引擎**给诊断、风险识别与适用边界，**质量合规技能**在出口把关（指标复核、红线拦截、验收闭环）；全部技能共同消费同一套治理文件，保证口径单源。技能间接口按编号契约（IC-xx）注册，请求/响应字段有单一事实源。

图中只放短标签，完整目录名以[技能矩阵](#技能矩阵15-个)为单源；维护向的 `prefab-governance-sync` 只用于治理文件同步，不在用户能力图上。

## 一次提问怎么走

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant OR as 总入口
    participant SK as 专项技能
    participant GOV as 治理文件
    participant ACE as 隔声引擎

    U->>OR: 自然语言提问
    OR->>OR: 意图识别 · 路由分派
    OR->>SK: 转交专项技能
    SK->>GOV: 读标准/契约/红线
    GOV-->>SK: 版本 · 字段 · 禁止项
    opt 涉及隔声／撞击声
        SK->>ACE: IC-03／IC-08／IC-09
        ACE-->>SK: 诊断 · 风险 · 边界
    end
    SK-->>OR: 构造 · 要点 · 缺项
    OR-->>U: 分级输出
```

注意第 4—5 步：**技能不是凭记忆作答，是先读治理文件再答**。这是「同一问题问两次给同一个口径」的结构原因。

## 技能矩阵（15 个）

| 域 | 技能 | 解决什么 |
|---|---|---|
| 协调 | `prefab-interior-systems-orchestrator` | 合集总入口：意图识别、路由分派、跨专业冲突协调、变更影响传递 |
| 协调 | `prefab-interior-materials-expert` | 材料选型、规范解读、质量问题诊断与综合咨询 |
| 部品 | `prefab-partition-wall-solution` | 分户墙/室内隔墙选型：双空腔轻钢龙骨、双排龙骨、ALC 条板等路线的隔声/耐火/成本对比与施工控制 |
| 部品 | `prefab-wall-surface-system` | 墙面系统：无龙骨点式调平、龙骨调平（GL/UL/LGS）、干法直挂与饰面板工艺 |
| 部品 | `prefab-ceiling-system` | 集成/干法/模块化吊顶：隔声贡献估算、管线协同、检修口、标高控制与耐久维修比较 |
| 部品 | `prefab-floor-system` | 浮筑/架空/干法调平/快装地面：撞击声改善评估、标高推算、管线分离与更换维修路径 |
| 部品 | `prefab-bathroom-kitchen-system` | 整体卫浴与集成厨房：防水底盘、湿区接口、排水坡度、闭水试验、洁具安装 |
| 部品 | `prefab-storage-system` | 模块化收纳：柜体尺寸模数、墙地顶接口与安装顺序；橱柜尺寸可追溯至条文级来源，超系列值显式披露 |
| 专项 | `acoustic-calculation-engine` | 通用隔声计算引擎（M1—M7 物理模型，见下节） |
| 专项 | `prefab-mep-integration-system` | 机电管线集成：管线分离策略、检修路径、设备点位、安装顺序编排 |
| 质量 | `prefab-acceptance-checklist-generator` | 过程检查/隐蔽验收/分项验收/竣工交付清单生成、整改闭环，与可编辑 XLSX／DOCX 导出 |
| 质量 | `prefab-standards-reviewer` | 技术指标表的规范标准核验：版本有效性、条文定位、数据档位 |
| 质量 | `scanned-standard-clause-verify` | 扫描版/文本版标准 PDF 的身份核验与条文原文核验流水线（**公开面只含流程与判据**） |
| 质量 | `skill-qa-tester` | 技能质量测试：功能完整性、数据准确性、内部一致性、红线合规 |
| 治理 | `prefab-governance-sync` | 治理文件三层同步与发布流程（**公开面只作流程参考**，同步器不随包） |

> `waterproofing-expert`（建筑防水工程技术顾问）已于 2026-08-14 迁出本合集，独立维护于建筑专业辅材技能合集项目；本合集各技能保留对其的跨合集外部协同引用。

## 隔声计算引擎（ACE）

隔声是本合集估算密度最高的专业面，单独抽成通用引擎，由隔墙/楼地面/吊顶技能经编号接口（IC-03 空气声 / IC-08 撞击声 / IC-09 侧向传声）调用。

```mermaid
flowchart LR
    REQ["计算请求"] --> K{"参数与范围"}
    K -- "合法系统" --> PAUSE["定量暂停"]
    K -- "不足/超范围" --> DEG["诊断型降级"]
    PAUSE --> DEG
    DEG --> OUT["诊断输出"]
```

**当前输出规则**（PL-229 暂停期）：

| 输入情况 | 引擎返回 | 不返回 |
|---|---|---|
| 合法系统 | 诊断型降级：质量与频率诊断、声桥/缝隙风险、关键假设、适用边界、人工评估建议 | dB、数字区间、精度承诺 |
| 参数不足 / 超范围 | 降级结构：缺失项、风险识别、超出范围提示 | 猜填参数、确定数值 |
| 有匹配且可溯源的检测报告 | 可引用报告原评价量，并记录报告编号与匹配核对 | 以同材料或相似构造推定同构造；以 STC 当 Rw；把报告值外推为变体性能 |
| 需要工程结论 | 以 CMA/CNAS 检测报告和现行标准为准 | 以模型预测替代检测或验收 |

为什么宁可暂停：接口契约里的示例区间与内核复算出的区间，在空气声、撞击声、侧向传声三面上都对不齐，而判定「哪一侧对」需要**同构造的 CMA/CNAS 检测报告**——该外部真值尚未取得。在没有真值的条件下继续输出 dB，等于把一个未对齐的差值交给工地。模型本体（M1—M7：质量定律、Cremer 吻合效应、双叶墙 MSM 共振、空腔增益、撞击声改善量、声桥/缝隙修正、检测报告基准校准）与可执行内核 `ace_kernel.py` 随包保留，供研究复算与验证后恢复。

## 治理与证据体系

本合集不只是一组提示词——每个技能背后是同一套可核查的治理文件（随仓分发，位于 `skills/shared/`、`skills/standards-index.md` 与 `skills/platform-adapter-reference.md`）：

| 文件 | 承担什么 |
|---|---|
| `standards-index.md` | 规范标准索引：收录标准的编号、名称、效力状态（现行/废止/被替代）与**核验状态标注**；条数以该件头部计数现读为准（公开面不含 §八变更日志） |
| `shared/redlines-registry.md` | 红线注册表：逐技能登记“不得违反”的输出约束（如超范围禁给确定数值、评价量不得混用），条数以该件现读为准 |
| `shared/interface-contracts.md` | 技能间接口契约（IC-xx 编号化）：请求/响应字段约束的单一事实源 |
| `shared/standards-reasoning-rules.md` | 标准适用性推理规则：废止标准降级、替代切换、地域适用性等判则 |
| `shared/data-classification.md` | 数据分类与取证判据：四类量（限值/派生量/物理参数/叙述）的来源与分级要求 |
| `shared/glossary.md` | 跨技能共用术语表（触发词与专业名词的统一口径） |
| `shared/change-governance.md` | 变更治理规则：S/A/B/C 定级、审批流程与变更日志（**公开面不含 §九／§11.2**） |
| `shared/platform-adapter-reference.md` | 平台适配参考：跨智能体平台加载的适配说明 |

```mermaid
flowchart LR
    STD["现行标准"] --> CLS["来源分级"]
    RPT["检测报告"] -. "凭据" .-> CLS
    CLS --> BODY["技能正文"]
    RED["红线注册表"] --> BODY
    BODY --> OUT["分级输出"]
```

配套机制（写进技能正文、可被提问直接触发）：**已废止标准自动降级并提示替代关系**；**实验室评价量（Rw+C）与现场评价量（DnT,w+C）不得混用**；**估算与检测值不得互替**；数值缺依据时显式降级而不是编造。

### 核验证据包：把「核过什么」变成可复算的数字

[`verification/`](verification/) 回答三个问题：**自称核过什么、依据是什么类型、哪些还没核**。它不是宣传页，而是派生时**现读**包内索引、测试件与 CI 配置算出的机算产物——不手写数字，改了索引它跟着变，核验有缺口它看得见。未核项照原样计入摘要，不藏。

真正的实跑入口在包内（结果不复制进该目录，复制会过期）：`skills/prefab-standards-reviewer/sre_regression_test.py`、`sr_ic07_compliance_test.py`、`tools/smoke_test.py`，以及每次 push 都实跑的 `.github/workflows/smoke.yml`。

## 换掉领域，骨架不变

如果你不是做装配式装修的，这一节可能才是你要看的：本仓真正想验证的命题是**垂直领域的专家知识能不能被工程化成可审计、可复用的智能体资产**，而装修只是第一个实例。

把领域内容抽掉，剩下的骨架是七层，且每层都有文件承担者：

| 层 | 本仓的具体内容 | 换领域时对应什么 |
|---|---|---|
| 判据正文 | 15 个 `SKILL.md`：选型树、验证协议、构造层次、工序控制 | 你的专业判据与决策路径 |
| 术语与口径 | `shared/glossary.md` | 同一触发词在哪个技能是什么意思 |
| 依据清单 | `standards-index.md`：编号、效力状态、核验状态 | 你引用的规范／手册／API 版本台账 |
| 来源分级 | S1—S4 ＋ V1—V4 两轴 | 每个数值凭什么可信、缺什么就降级 |
| 禁止项 | `shared/redlines-registry.md` | 你的「不许编、不许越界」清单 |
| 技能间契约 | IC-xx 编号化请求/响应字段单源 | 跨技能调用的字段约束 |
| 变更与证据 | S/A/B/C 定级、三层同步、派生面纪律、`verification/` 机算指标 | 谁批准改哪一层、外部如何复算 |

三条从本仓实跑里得到的经验，比骨架本身更值钱：

1. **约束要写在能被机器撞的地方。** 只写进提示词的规则，命中率取决于模型当天状态；写进红线注册表并被测试件对撞的那批，退化会转红。开发侧那 22 道门禁的存在理由就是这个。
2. **「不知道」必须是一种可输出的状态。** 降级结构、🔲、`不能判` 这些看起来反直觉的出口，是「可复用的知识资产」与「看起来专业的内容」之间的分界线。隔声定量暂停（PL-229）是这套机制最贵的一次自检。
3. **数字不要手写第二份。** 收录数、用例数、文件数一旦在两处出现就会漂移。`verification/` 与派生器的存在，是为了让人记得的次数不成为正确性的前提。

> 诚实边界：这套骨架目前**以装修领域实例的形态随包存在**，可作参照实现；抽象成领域无关的通用模板尚未单独立项。想套用到自己的领域，先读 [ARCHITECTURE.md](ARCHITECTURE.md) 的扩展点与 [CONTRIBUTING.md](CONTRIBUTING.md) 的变更流程，再决定要不要动手。

## 质量与测试

本仓自带 **109 例**可跑的回归测试，全部随包；安装后即可复跑：

| 套件 | 例数 | 运行方式（安装后） |
|---|---|---|
| SRE 引擎回归 | 89 | `python <安装目录>/prefab-standards-reviewer/sre_regression_test.py` |
| IC-07 合规回归 | 20 | `python <安装目录>/prefab-standards-reviewer/sr_ic07_compliance_test.py` |

CI 在 Ubuntu／Windows × Python 3.10／3.12 上执行：包结构自检、安装到临时目录并复检、上述 109 例。开发仓另有 22 道门禁（台账一致性、数据分类、冻结基准、跨层字节一致、表结构等）不随包。

**边界，必须说**：公开面通过只证结构一致性与随包测试通过，**不证内容正确**。用例数是声明数不是通过数；数值与条文的正确性以官方现行标准、可溯源的检测报告与注册执业人员的判断为准。把「CI 全绿」读成「工程结论可信」，是这套机制最不希望被误读的一点。

## 公开面边界（随包 / 不随包）

本仓是**技能分发面**：只含技能本体与治理文件镜像。开发仓的过程记录、维护脚本与校验门禁（治理登记、批凭据、各道验证脚本）不随本仓分发。

| 类别 | 随包（合计 99 个文件） | 不随包 |
|---|---|---|
| 技能本体 | 15 个技能目录，66 个文件（含 5 个 Python 件：`ace_kernel.py`、`sre_reasoner.py`、2 件测试、验收清单导出脚本） | — |
| 治理文件 | `shared/` 8 件 ＋ 根级副本 2 件（`standards-index.md`、`platform-adapter-reference.md`），共 10 个文件 | 开发仓 `治理文件/` L1 源、`台账源/`、数据分类复核清单；`change-governance.md` 的 §九／§11.2；`standards-index.md` 的 §八 |
| 根级文件 | `README.md`、`ARCHITECTURE.md`、`LICENSE`、`CHANGELOG.md`、`.gitattributes`、`assets/logo.png`、`CONTRIBUTING.md`、`SECURITY.md`、`CODE_OF_CONDUCT.md`，共 9 个文件 | — |
| 工程工具 | `tools/`（安装器、自检、说明）3 件；`.github/`（CI、两类 issue 模板）3 件，共 6 个文件 | 开发仓的维护脚本、批凭据脚本、发布门禁 |
| 示例输出 | `examples/`（索引页 1 件 ＋ 示例 4 件），共 5 个文件 | — |
| 核验证据 | `verification/`（说明 1 件 ＋ 指标 1 件 ＋ 核验摘要 1 件），共 3 个文件 | 核验报告原件、标准原文与扫描件、检测报告 |
| 证据与记录 | — | `核验成果/`、`执行记录/`、`文档目录/`（含检测报告、核验报告、产品资料原件） |

**计数**：66 ＋ 10 ＋ 9 ＋ 6 ＋ 5 ＋ 3 ＝ 99 个文件；上表按文件归属划分，不重复计数。

**为什么这样切**：证据与记录可能含本机环境、项目信息与开发过程；维护脚本依赖开发仓的目录结构；台账是内部追溯面。正文中出现的这些路径只作**来源指认**，在本仓不可直接打开。

**派生纪律**：本仓由开发仓的派生器生成；内容改动先落开发仓，再重新派生，**不直接编辑本仓**（否则与开发仓漂移且无守卫）。

## 快速开始

```mermaid
flowchart LR
    D["下载/克隆"] --> I["python tools/install.py"]
    I --> S["python tools/smoke_test.py"]
    S --> R["重启平台"]
    R --> Q["直接提问"]
```

**方式 A：一条命令（推荐）**

```sh
git clone https://github.com/eiway112/prefab-interior-agent.git
cd prefab-interior-agent
python tools/install.py          # 可用 --target 指定其他技能目录、--dry-run 只看计划
python tools/smoke_test.py --target ~/.qoder/skills
```

更新已有安装：先 `python tools/install.py --dry-run` 看计划；安装器按受管清单与内容指纹工作，遇到本地修改默认整批拒绝，确认后 `--force` 备份替换，误改可用 `--restore` 从本机备份恢复。详见[更新与恢复](tools/README.md#更新与恢复)。

**方式 B：手动复制**

1. 下载本仓库：在仓库页面点击 **Code → Download ZIP**，或执行
   - GitHub：`git clone https://github.com/eiway112/prefab-interior-agent.git`
   - Gitee：`git clone https://gitee.com/eiway112/prefab-interior-agent.git`
2. 将 `skills/` 下所需的技能目录复制到你所用平台的**技能加载目录**（例如 Qoder：`~/.qoder/skills/`；Windows 下为 `C:\Users\<你的用户名>\.qoder\skills\`。其他平台请以其文档为准）。
3. **必装项**：请将全部技能目录连同 `skills/standards-index.md`、`skills/platform-adapter-reference.md` 与 `skills/shared/` 一并复制。技能正文硬引用 `../shared/`（红线注册表、接口契约、术语表等治理约束），缺 `shared/` 时技能仍会加载但**静默失去红线与契约约束、不报错**——只复制单个技能目录拿到的是弱化版。复制这些件不会补回开发仓的维护脚本、核验报告与执行记录。
4. 重启你的智能体平台，直接提问即可，例如：“酒店客房整体卫浴，SMC、彩钢板、瓷砖饰面三条路线怎么选？”

**装完自检**：`python tools/smoke_test.py --target <安装目录>` 应输出 PASS（15 个技能、8 份治理文件、标准索引）。缺任一件即停止，不要以「能加载」当作「完整可用」。

## 目录结构

```
prefab-interior-agent/
├── README.md / LICENSE / CHANGELOG.md / CONTRIBUTING.md
├── SECURITY.md / CODE_OF_CONDUCT.md
├── tools/                               ← install.py（安装）· smoke_test.py（自检）
├── .github/                             ← CI（push / PR 跑自检）· issue 模板
├── assets/                              ← 品牌资源
├── examples/                            ← 示例输出：四份真实应答样例 ＋ 索引
├── verification/                        ← 核验证据包：口径说明 ＋ 聚合指标 ＋ 核验摘要（派生时生成）
└── skills/                              ← 对外引用入口：技能本体与治理文件
    ├── prefab-*/ · acoustic-*/ · scanned-*/ · skill-qa-tester/
    │                                    ← 15 个技能目录（SKILL.md ＋ reference/examples 等）
    ├── standards-index.md               ← 规范标准索引（含核验状态标注）
    ├── platform-adapter-reference.md    ← 平台适配参考
    └── shared/                          ← 治理文件 8 件（契约·红线·术语·数据分级·变更治理等）
```

模块边界、数据流与扩展点见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 行尾前提（最容易踩的一条）

仓根 `.gitattributes` 声明 `* text eol=lf`，请保留。你的机器若设了 `core.autocrlf=true`（Windows 上常见，且可能是全局而非仓库级），删掉或绕过该文件会让清洁克隆的工作树整体转成 CRLF，从而与文件内部记录的字节形态不一致。自验法：`git ls-files --eol` 中文本件应落 `i/lf`。

## 运行环境

技能为纯文本资产，直接复制即可用，无运行依赖。ACE 可执行内核 `ace_kernel.py` 仅用 Python 3.8+ 标准库，唯一入口 `compute_request(ic_id, request)`，参数不足或超范围一律以降级结构返回而非抛错。开发仓的维护脚本另需 Python 3.10+，这些脚本不随包。

## 版本与迭代策略

- 本仓按语义化版本打标签发布（当前 `v1.5.0`）；引用请**锁定到 tag 或具体 commit**（`git describe --tags` 可读最近标签），避免跟随 main 拿到中间状态。
- 公开面变更见 [CHANGELOG.md](CHANGELOG.md)；各技能的具体版本以 `SKILL.md` frontmatter 的 `version` 字段为准。
- 规范标准修订、技能内容勘误以 PATCH/MINOR 发布；架构或契约级调整以 MAJOR 发布。
- **反馈与纠错**：欢迎开 Issue（使用问题、数据纠错、标准失效线索均可）。数据纠错类 Issue 承诺 **3 个工作日内**响应——这类 Issue 是本仓最有价值的外部输入，因为标准会修订、本仓会漏。安全类问题请走 [SECURITY.md](SECURITY.md) 的私密通道；协作方式见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 后续演进方向：单技能独立版本发布与 Releases 分发打包产物；隔声定量预测在取得匹配检测证据后恢复。

## 引用与署名

本仓库采用 [MIT License](LICENSE)。复制、引用或二次开发请保留版权声明；建议引用格式：

> 装配式装修智能体技能合集 vX.Y.Z，艾为智造（长沙）科技有限公司，https://github.com/eiway112/prefab-interior-agent

## 边界与免责声明

- 技能输出为工程技术参考意见，不替代注册执业人员的正式设计文件。
- 隔声、耐火等确定性性能数据须以 CMA/CNAS 检测报告和现行国家/行业标准为准；技能内置估算模型仅用于方案比较。
- 项目落地前请结合工程所在地法规、设计文件与产品实际参数核实。
