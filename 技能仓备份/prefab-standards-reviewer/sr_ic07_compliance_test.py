"""
SR IC-07 闭环合规测试集
========================
版本：v1.0（2026-08-13，CG-20260813-038）
运行：python sr_ic07_compliance_test.py

覆盖（TestDocAnchor 模式：以 Markdown SOT 为锚定源，三向比对）：
  1. SKILL.md IC-07 接收方契约锚定 ↔ interface-contracts.md §4.4 IC-07 字段约束表
  2. SKILL.md 红线摘要表 ↔ redlines-registry.md §六 SR 红线（双向一致）
  3. 注册表 SR 计数链（头部总数 / §二映射表 / §六章节 / §19.1 / §19.2）自洽
  4. SKILL.md 多文件认知架构（A-D 分层）锚定
  5. reference.md 五类问题检测规则锚点
反向验证：任一 SOT 被篡改（编号漂移/枚举漂移/计数漂移）须触发失败。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).parent
SHARED_DIR = SKILL_DIR.parent / "shared"

SKILL_MD = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
REFERENCE_MD = (SKILL_DIR / "reference.md").read_text(encoding="utf-8")
CONTRACTS_MD = (SHARED_DIR / "interface-contracts.md").read_text(encoding="utf-8")
REGISTRY_MD = (SHARED_DIR / "redlines-registry.md").read_text(encoding="utf-8")

# ---- 锚定常数（与 interface-contracts.md §4.4 IC-07 字段约束表一致） ----
IC07_REQUEST_FIELDS = ["引用的标准编号", "引用条文号", "项目所在地", "应用场景描述"]
IC07_STATUS_ENUM = ["现行有效", "即将实施", "过渡期", "被部分替代", "已废止", "未知"]
IC07_OPINION_ENUM = ["可引用", "仅供参考", "需替代", "不适用", "需人工确认"]
SR_REDLINE_COUNT = 14          # P0×3 + P1×7 + P2×4
REGISTRY_TOTAL_COUNT = 178     # 全合集红线总数（2026-08-14 ME 注册+AC-R-P1-5 后基线；CG-20260817-006 同步）
SR_VERSION = "2.2.0"


def _ic07_contract_block() -> str:
    """截取 interface-contracts.md 中 IC-07 小节（至下一 IC 小节为止）。"""
    m = re.search(r"#### IC-07：任何技能 → 标准复核工具(.*?)(?=\n#### IC-10)", CONTRACTS_MD, re.S)
    assert m, "interface-contracts.md 未找到 IC-07 小节"
    return m.group(1)


def _registry_sr_block() -> str:
    """截取注册表§六 SR 章节（至§七为止）。"""
    m = re.search(r"## 六、已注册红线：标准复核工具（SR）(.*?)\n## 七、", REGISTRY_MD, re.S)
    assert m, "redlines-registry.md 未找到§六 SR 章节"
    return m.group(1)


class TestIc07ContractAnchor(unittest.TestCase):
    """SKILL.md 接口契约章节 ↔ interface-contracts.md §4.4 IC-07。"""

    def test_ic07_section_exists(self):
        self.assertIn("## 接口契约（IC-07 / IC-10 接收方）", SKILL_MD)
        self.assertIn("§4.4", SKILL_MD)

    def test_request_fields_all_marked_required(self):
        for f in IC07_REQUEST_FIELDS:
            self.assertIn(f, SKILL_MD, f"SKILL.md 缺 IC-07 请求字段：{f}")

    def test_status_enum_matches_contract(self):
        block = _ic07_contract_block()
        for v in IC07_STATUS_ENUM:
            self.assertIn(v, block, f"契约文件 IC-07 标准状态枚举缺：{v}")
            self.assertIn(v, SKILL_MD, f"SKILL.md 标准状态枚举缺：{v}")

    def test_opinion_enum_matches_contract(self):
        block = _ic07_contract_block()
        for v in IC07_OPINION_ENUM:
            self.assertIn(v, block, f"契约文件 IC-07 合规性意见枚举缺：{v}")
            self.assertIn(v, SKILL_MD, f"SKILL.md 合规性意见枚举缺：{v}")

    def test_schema_pointer_present(self):
        self.assertIn("IC-07-Request", _ic07_contract_block())
        self.assertIn("IC-07", SKILL_MD)


class TestRedlineBidirectional(unittest.TestCase):
    """SKILL.md 红线摘要表 ↔ 注册表§六，双向一致。"""

    def _skill_redlines(self) -> set:
        return set(re.findall(r"SR-R-P[012]-\d+", SKILL_MD))

    def _registry_redlines(self) -> set:
        return set(re.findall(r"SR-R-P[012]-\d+", _registry_sr_block()))

    def test_registry_has_14(self):
        ids = self._registry_redlines()
        self.assertEqual(len(ids), SR_REDLINE_COUNT, f"注册表§六 SR 红线数 {len(ids)} ≠ {SR_REDLINE_COUNT}")

    def test_skill_covers_all_registry(self):
        missing = self._registry_redlines() - self._skill_redlines()
        self.assertEqual(missing, set(), f"SKILL.md 缺红线编号：{sorted(missing)}")

    def test_no_unregistered_in_skill(self):
        extra = self._skill_redlines() - self._registry_redlines()
        self.assertEqual(extra, set(), f"SKILL.md 出现未注册红线编号：{sorted(extra)}")

    def test_local_five_mapping_closed(self):
        # v2.1.0 本地 5 条红线全量吸收验证
        self.assertIn("SR-R-P0-3", SKILL_MD)  # 超越专业边界
        self.assertIn("SR-R-P1-7", SKILL_MD)  # 臆造条款号
        self.assertIn("SR-R-P2-4", SKILL_MD)  # 推荐品牌
        self.assertIn("历史本地红线映射", SKILL_MD)


class TestRegistryCountChain(unittest.TestCase):
    """注册表 SR 计数链自洽。"""

    def test_header_total(self):
        self.assertIn(f"**注册红线总数**：{REGISTRY_TOTAL_COUNT} 条", REGISTRY_MD)
        self.assertIn("SR 14 条", REGISTRY_MD)

    def test_mapping_table_sr_row(self):
        self.assertIn("**SR** | prefab-standards-reviewer | 质量工具 | ✅ 已注册（14 条）", REGISTRY_MD)

    def test_section_six_count_row(self):
        self.assertIn("P0 3 条 + P1 7 条 + P2 4 条 = 共 14 条", _registry_sr_block())

    def test_stats_19_1(self):
        m = re.search(r"\| \*\*合计\*\* \|(.*?)\| 100% \|", REGISTRY_MD)
        self.assertTrue(m, "§19.1 合计行未找到")
        row = m.group(1)
        nums = [int(x) for x in re.findall(r"\*\*(\d+)\*\*", row)]
        self.assertEqual(sum(nums[:-1]), nums[-1], "§19.1 各技能合计 ≠ 总数")
        self.assertEqual(nums[-1], REGISTRY_TOTAL_COUNT)

    def test_stats_19_2_sr_row(self):
        self.assertIn("| SR | 标准复核 | 3 | 7 | 4 | 14 | ✅ 已注册 |", REGISTRY_MD)


class TestCognitiveArchitecture(unittest.TestCase):
    """SKILL.md 多文件认知架构（A-D 分层）。"""

    def test_section_exists(self):
        self.assertIn("## 多文件认知架构", SKILL_MD)

    def test_layer_annotations(self):
        self.assertIn("A 层（原则红线）", SKILL_MD)
        self.assertIn("B 层（方法论）", SKILL_MD)
        self.assertIn("D 层（演示示例）", SKILL_MD)
        self.assertIn("E 层产品解决方案库不适用", SKILL_MD)

    def test_file_pointers_exist(self):
        self.assertTrue((SKILL_DIR / "reference.md").exists())
        self.assertTrue((SKILL_DIR / "examples.md").exists())
        self.assertTrue((SKILL_DIR / "sre_reasoner.py").exists())

    def test_version_frontmatter(self):
        m = re.search(r"^version:\s*(\S+)", SKILL_MD, re.M)
        self.assertTrue(m, "frontmatter 无 version 字段")
        self.assertEqual(m.group(1), SR_VERSION)


class TestReferenceDetectionRules(unittest.TestCase):
    """reference.md 五类问题检测规则锚点（QA 对抗用例依赖）。"""

    def test_five_detection_anchors(self):
        # 对应 SKILL.md 问题诊断表五类高危问题
        self.assertIn("GB 55038-2025", REFERENCE_MD)              # 标准年份准确性锚
        self.assertIn("DnT,w+C", REFERENCE_MD)                    # 指标体系（现场指标）锚
        self.assertIn("Rw+C", REFERENCE_MD)                       # 指标体系（实验室指标）锚
        self.assertIn("GB 50118-2010", REFERENCE_MD)              # 隔声设计标准锚
        self.assertIn("GB 55037-2022", REFERENCE_MD)              # 防火替代关系锚

    def test_skill_problem_taxonomy(self):
        for t in ["标准年份错误", "指标数值错误", "指标体系错误", "边界符号错误", "依据标准错配"]:
            self.assertIn(t, SKILL_MD, f"SKILL.md 问题诊断表缺类型：{t}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
