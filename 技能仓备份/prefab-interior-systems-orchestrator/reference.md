# 装配式装修技能合集路线图

## 当前技能层级

| 层级 | 技能 | 定位 |
|---|---|---|
| 总入口 | prefab-interior-systems-orchestrator | 意图识别、路由协调、多技能汇总 |
| 综合兜底 | prefab-interior-materials-expert | 综合材料、工艺、质量与趋势咨询 |
| 专项部品 | prefab-partition-wall-solution | 隔墙系统 |
| 专项部品 | prefab-wall-surface-system | 墙面饰面系统 |
| 专项部品 | prefab-ceiling-system | 吊顶系统 |
| 专项部品 | prefab-floor-system | 楼地面系统 |
| 专项部品 | prefab-bathroom-kitchen-system | 厨卫系统 |
| 专项部品 | prefab-storage-system | 收纳系统 |
| 协同专项 | prefab-mep-integration-system | 机电管线分离与部品接口 |
| 质量专项 | prefab-acceptance-checklist-generator | 验收清单与质量控制 |
| 质量中台 | prefab-standards-reviewer | 标准引用与版本复核 |
| QA 中台 | skill-qa-tester | 技能测试与回归 |
| 内部引擎 | acoustic-calculation-engine | 隔声计算引擎，不直面用户，经 IC-03／IC-08／IC-09 由隔墙、楼地面、吊顶技能调用 |
| 核验中台 | scanned-standard-clause-verify | 规范标准文本的条文级取真（IC-11 服务端） |
| 治理中台 | prefab-governance-sync | 治理文件三层同步与发布执行（IC-13 服务端）；变更定级与审批的事实源仍是 `change-governance.md`，本技能只承担执行层 |

> **档位形制**：上表「层级」列分两支＝**面向用户**（总入口／综合兜底／专项部品／协同专项／质量专项与各类中台，均可由总入口路由块命中）与**内部引擎**（`acoustic-calculation-engine`，不直面用户、无路由触发词，仅按契约编号 IC-03／IC-08／IC-09 被调用）。两支不同档，不得把内部引擎列为可路由的专项技能。

## 扩展原则

1. 每个部品专项技能只维护本系统的选型、接口、施工和验收知识。
2. 标准版本状态统一引用 `../standards-index.md`。
3. 跨平台执行动作统一引用 `../platform-adapter-reference.md`。
4. 不在多个技能内重复维护长标准清单；必要时只保留本技能最高频的标准索引。
5. 当专项技能与综合技能重叠时，专项技能优先，综合技能兜底。

## 部品专项统一结构

每个新增部品专项技能应包含：

- 角色定位
- 适用场景
- 系统分类
- 选型矩阵
- 接口边界
- 施工控制
- 验收要点
- 标准引用策略
- 跨技能路由
- 专业边界与免责
