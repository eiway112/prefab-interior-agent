# AGENTS.md — prefab-interior-agent 公开分发面

本目录由开发仓派生，禁止在公开仓直接编辑；改动先落开发仓模板源或技能源，再重新派生并逐字节复核。

## 目录结构

- `程序文件/tools/`：安装器、包自检与使用说明。
- `程序文件/assets/`：公开面品牌资源。
- `skills/`：技能本体、共享治理文件与标准索引。
- `examples/`：由技能示例抽取的公开应答样例。
- `verification/`：派生时生成的核验口径、指标与摘要。
- `.github/`：持续集成与Issue模板。

## 变更与验证

1. 在开发仓修改源件，运行派生器写出公开面，再对公开面运行逐字节检查。
2. 在公开面根目录运行 `python 程序文件/tools/smoke_test.py`。
3. 安装预览运行 `python 程序文件/tools/install.py --dry-run`。
4. 随包回归运行 `skills/prefab-standards-reviewer/sre_regression_test.py` 与 `sr_ic07_compliance_test.py`。

公开面通过只证结构一致性与随包测试通过，不证工程内容正确；标准取值与检测结论仍须可追溯来源。
