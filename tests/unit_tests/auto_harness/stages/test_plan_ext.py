# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Extension plan stage prompt tests."""

from __future__ import annotations

from openjiuwen.auto_harness.schema import (
    Gap,
    GapAnalysisArtifact,
)
from openjiuwen.auto_harness.infra.parsers import (
    parse_extension_designs,
)
from openjiuwen.auto_harness.stages.plan import (
    _build_design,
    _build_fallback_designs,
    _build_design_query,
    _cap_extension_designs,
)


def test_design_query_selects_components_by_gap_semantics():
    query = _build_design_query(
        GapAnalysisArtifact(
            gaps=[
                Gap(
                    id="gap_1",
                    feature="conversation_budget_report",
                    gap_description=(
                        "每 5 次工具执行自动报告进度和 token 花销"
                    ),
                    impact=0.9,
                    feasibility=0.8,
                )
            ]
        )
    )

    assert "按用户目标选择最轻组件组合" in query
    assert "周期触发" in query
    assert "不要强制包含 rail" in query
    assert "最多输出 10 个 ExtensionDesign" in query


def test_design_query_uses_configured_extension_limit():
    query = _build_design_query(
        GapAnalysisArtifact(gaps=[]),
        max_designs=3,
    )

    assert "最多输出 3 个 ExtensionDesign" in query


def test_design_query_preserves_domain_artifacts_for_ppt_extensions():
    query = _build_design_query(
        GapAnalysisArtifact(
            gaps=[
                Gap(
                    id="gap_1",
                    competitor="用户需求",
                    feature="huawei_ppt_generator",
                    gap_description=(
                        "生成华为风格 PPT，包含模板规范和文件生成能力"
                    ),
                    impact=0.9,
                    feasibility=0.8,
                )
            ]
        )
    )

    assert "huawei_ppt_generator" in query
    assert "`office_ppt_generator`" in query
    assert "Tool" in query
    assert "Skill" in query
    assert "skill-creator" in query
    assert "assets/" in query
    assert "references/" in query
    assert "真实产物契约" in query
    assert "PPTX/DOCX" in query
    assert "JSON/Markdown/纯文本" in query
    assert "ppt/presentation.xml" in query
    assert "不要使用 `user_demand_*`" in query
    assert "需求收集" in query


def test_fallback_design_infers_tool_skill_for_ppt_generation():
    design = _build_design(
        Gap(
            id="gap_1",
            competitor="用户需求",
            feature="huawei_ppt_generator",
            gap_description=(
                "生成华为风格 PPT，包含模板规范和文件生成能力"
            ),
            suggested_approach=(
                "创建 PPT 生成 Tool 和华为风格 Skill"
            ),
        )
    )

    assert design.extension_name == "huawei_ppt_generator"
    assert design.kind == "capability"
    assert design.components == ["tool", "skill"]
    resources = design.harness_config_patch["resources"]
    assert "tools" in resources
    assert "skills" in resources
    assert "rails" not in resources


def test_parse_extension_designs_preserves_execution_fields():
    package_name, designs = parse_extension_designs(
        """
        ```json
        [
          {
            "gap_id": "gap_guard",
            "extension_name": "huawei_filename_guard",
            "kind": "constraint",
            "depends_on": [],
            "applies_to": ["huawei_ppt_generator"],
            "components": ["rail"]
          },
          {
            "gap_id": "gap_ppt",
            "extension_name": "huawei_ppt_generator",
            "depends_on": ["huawei_filename_guard"],
            "components": ["tool", "skill"]
          }
        ]
        ```
        """
    )

    # Old format (array) returns None for package_name
    assert package_name is None
    assert [design.extension_name for design in designs] == [
        "huawei_filename_guard",
        "huawei_ppt_generator",
    ]
    assert designs[0].kind == "constraint"
    assert designs[0].applies_to == ["huawei_ppt_generator"]
    assert designs[1].kind == "capability"
    assert designs[1].depends_on == [
        "huawei_filename_guard"
    ]


def test_parse_extension_designs_new_format_with_package_name():
    """Test parsing new format with package_name and designs."""
    package_name, designs = parse_extension_designs(
        """
        ```json
        {
          "package_name": "huawei_office_generator",
          "designs": [
            {
              "gap_id": "gap_guard",
              "extension_name": "huawei_filename_guard",
              "kind": "constraint",
              "depends_on": [],
              "applies_to": ["huawei_ppt_generator"],
              "components": ["rail"]
            },
            {
              "gap_id": "gap_ppt",
              "extension_name": "huawei_ppt_generator",
              "depends_on": ["huawei_filename_guard"],
              "components": ["tool", "skill"]
            }
          ]
        }
        ```
        """
    )

    # New format returns package_name
    assert package_name == "huawei_office_generator"
    assert [design.extension_name for design in designs] == [
        "huawei_filename_guard",
        "huawei_ppt_generator",
    ]
    assert designs[0].kind == "constraint"
    assert designs[1].kind == "capability"


def test_fallback_designs_keep_constraints_outside_capability_cap():
    designs = _build_fallback_designs(
        [
            Gap(
                id="guard",
                feature="huawei_filename_guard",
                gap_description=(
                    "所有文件写入前必须强制检查文件名后缀"
                ),
                impact=0.4,
                feasibility=0.4,
            ),
            Gap(
                id="ppt",
                feature="huawei_ppt_generator",
                gap_description="生成华为风格 PPT",
                impact=0.9,
                feasibility=0.9,
            ),
            Gap(
                id="excel",
                feature="finance_excel_processor",
                gap_description="处理财务 Excel",
                impact=0.8,
                feasibility=0.8,
            ),
        ],
        max_capabilities=1,
    )

    assert [design.extension_name for design in designs] == [
        "huawei_filename_guard",
        "huawei_ppt_generator",
    ]
    assert designs[0].kind == "constraint"
    assert "rail" in designs[0].components


def test_cap_extension_designs_limits_total_designs_with_constraints_first():
    designs = [
        _build_design(
            Gap(
                id="ppt",
                feature="huawei_ppt_generator",
                gap_description="生成华为风格 PPT",
                impact=0.9,
                feasibility=0.9,
            )
        ),
        _build_design(
            Gap(
                id="guard",
                feature="huawei_filename_guard",
                gap_description=(
                    "所有文件写入前必须强制检查文件名后缀"
                ),
                impact=0.4,
                feasibility=0.4,
            )
        ),
        _build_design(
            Gap(
                id="excel",
                feature="finance_excel_processor",
                gap_description="处理财务 Excel",
                impact=0.8,
                feasibility=0.8,
            )
        ),
    ]

    capped = _cap_extension_designs(designs, 2)

    assert [design.extension_name for design in capped] == [
        "huawei_filename_guard",
        "huawei_ppt_generator",
    ]
