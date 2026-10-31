# 核验证据包

本目录回答三个问题：**这套技能自称核过什么、依据是什么类型、哪些还没核**。它不是宣传页，也不是合规结论，而是把开发侧已有的核验记录与测试入口汇总成可复算的数字。

> **本目录由开发仓的派生器在派生时生成，不手工编辑。** 数字随 `skills/standards-index.md`、包内测试件与 CHANGELOG 变化而变化；手工改这里会在下一次派生时被覆盖。

## 里面有什么

| 文件 | 内容 | 由什么算出 |
|---|---|---|
| [`metrics.json`](metrics.json) | 聚合指标：技能数与用例数、标准收录与核验状态分布、CI 组合、发布版本数 | 派生时现读包内文件计算，逐项附来源件 sha256 前 12 位 |
| [`standards-verification.md`](standards-verification.md) | 核验方法与结论摘要：核了什么、依据类型、结论等级分布、未核项 | 派生时现读 `skills/standards-index.md` 计算 |
| `README.md` | 本说明 | 开发仓模板 |

真正的**实跑入口**在包内，结果不复制到本目录（复制会过期）：

| 入口 | 位置 |
|---|---|
| 引擎回归（SRE） | `skills/prefab-standards-reviewer/sre_regression_test.py` |
| IC-07 合规回归 | `skills/prefab-standards-reviewer/sr_ic07_compliance_test.py` |
| 包结构与安装复检 | `tools/smoke_test.py`、`tools/install.py` |
| CI（每次 push 实跑） | `.github/workflows/smoke.yml`，矩阵见 `metrics.json` 的 `ci` 字段，结果见本仓 Actions 页 |
| 示例输出 | `examples/`（真实应答样例） |

## 怎么复现

```sh
# 1) 包结构自检
python tools/smoke_test.py

# 2) 两套回归（随包，纯标准库）
python skills/prefab-standards-reviewer/sre_regression_test.py
python skills/prefab-standards-reviewer/sr_ic07_compliance_test.py

# 3) 安装到临时目录后再复检（模拟真实安装）
python tools/install.py --target /tmp/skills-check
python tools/smoke_test.py --target /tmp/skills-check
```

CI 在上述 OS × Python 版本矩阵上执行上述步骤（矩阵与组合数见 [`metrics.json`](metrics.json) 的 `ci` 字段）；本目录不保存 CI 结果快照。

## 这些数字怎么读

1. **用例数是声明数，不是通过数**。`metrics.json` 的 `tests` 是测试文件中声明的 `test_` 方法数（静态计数）；是否全绿以 CI 或你本地复跑的结果为准。
2. **核验统计是索引的现读投影**。收录数、状态分布、核验状态分布都从 `skills/standards-index.md` 的表格解析而来；它反映索引登记了什么，不证明每条条文都逐字核过。
3. **不含原文，也不含项目数据**。本目录不复制第三方标准原文、扫描件、检测报告与项目图纸；这些即使存在开发侧，也不随分发面发布。
4. **「未核项」是公开的**。索引里状态为待核验／无法核验的条目，以及「待补充标准清单」的条目数，原样计入摘要——不藏缺口。
5. **不是合规结论**。数值与条文的正确性以官方现行标准、可溯源的检测报告与注册执业人员的判断为准。

## 为什么单独做这个目录

公开分发面此前能证明「包是完整的、测试能跑」，但外部读者看不到「核验做了什么、做到哪一步」。把这句话变成机算产物而不是手写承诺，是这套证据包的全部目的：改了索引，这里跟着变；核验有缺口，这里看得见。
