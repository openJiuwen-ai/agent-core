# coding: utf-8
"""Regression tests for the agent-team prompt routing rules."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts import build_team_workflow_section, load_template
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.locales import make_translator


@pytest.mark.parametrize(
    ("language", "markers"),
    [
        (
            "cn",
            (
                "多 Agent 入口判断",
                "按以下优先级判断",
                "明确交付或实际执行",
                "必须启用多 Agent",
                "实质增益",
                "严格简单例外（直接回答）",
                "批量或成套产出",
                "只要有一项不满足，就不得直接回答",
                "最终结果形态",
                "区分“怎么处理”与“最终要得到什么”",
                "独立可验收",
            ),
        ),
        (
            "en",
            (
                "Multi-Agent Entry Decision",
                "following order of priority",
                "Explicit delivery or real execution",
                "multi-agent execution is mandatory",
                "material value",
                "Strict simple exception (direct answer)",
                "Batch or suite-style output",
                "If any condition is not met, do not answer directly",
                "form of the final result",
                "separate *how to process the request* from *what the user ultimately wants*",
                "independently verifiable",
            ),
        ),
    ],
)
def test_leader_policy_routes_by_multi_agent_value_and_final_result(language, markers):
    policy = load_template("leader_policy", language).content

    for marker in markers:
        assert marker in policy


@pytest.mark.parametrize("team_mode", ["default", "predefined", "hybrid"])
@pytest.mark.parametrize(
    ("language", "markers"),
    [
        (
            "cn",
            (
                "按最终结果形态选择分支",
                "**思辨分支**",
                "**任务协作分支**",
                "禁止 `view_task` / `create_task`",
            ),
        ),
        (
            "en",
            (
                "Branch by the expected form of the final result",
                "**Debate branch**",
                "**Task-collaboration branch**",
                "do not call `view_task` or `create_task`",
            ),
        ),
    ],
)
def test_leader_workflows_branch_before_task_operations(team_mode, language, markers):
    section = build_team_workflow_section(
        role=TeamRole.LEADER,
        team_mode=team_mode,
        language=language,
    )

    assert section is not None
    content = section.render(language)
    for marker in markers:
        assert marker in content


@pytest.mark.parametrize("desc_key", ["create_task", "create_task_scheduled"])
@pytest.mark.parametrize(
    ("language", "markers"),
    [
        ("cn", ("最终结果形态", "处理过程", "独立可验收")),
        ("en", ("form of the final result", "handling process", "independently verifiable")),
    ],
)
def test_create_task_variants_gate_on_final_result_shape(desc_key, language, markers):
    desc = make_translator(language)(desc_key)

    for marker in markers:
        assert marker in desc


@pytest.mark.parametrize(
    ("language", "markers"),
    [
        ("cn", ("**思辨分支**", "**任务协作分支**", "禁止 `view_task` / `create_task`")),
        (
            "en",
            ("**Debate branch**", "**Task-collaboration branch**", "do not call `view_task` or `create_task`"),
        ),
    ],
)
def test_build_team_description_branches_before_task_creation(language, markers):
    desc = make_translator(language)("build_team")

    for marker in markers:
        assert marker in desc


@pytest.mark.parametrize(
    "desc_key",
    ["spawn_teammate", "spawn_human_agent", "spawn_bridge_agent", "spawn_external_cli"],
)
@pytest.mark.parametrize(
    ("language", "markers"),
    [
        ("cn", ("思辨分支用 `send_message`", "任务协作分支才用 `create_task`")),
        ("en", ("on the debate branch", "only on the task-collaboration branch")),
    ],
)
def test_spawn_descriptions_preserve_the_selected_branch(desc_key, language, markers):
    desc = make_translator(language)(desc_key)

    for marker in markers:
        assert marker in desc
