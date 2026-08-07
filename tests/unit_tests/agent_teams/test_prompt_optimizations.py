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
                "多 Agent 为默认、Leader 直接回答为极窄例外",
                "任务型请求默认启用多 Agent",
                "思考型请求默认启用多 Agent",
                "质量能够受益时启用多 Agent",
                "不同专业能力、受众立场、构思路径或评价维度",
                "有意义的互补",
                "极简直答例外",
                "单步即可可靠完成",
                "任务短小、产物单一或容易完成本身不构成直答理由",
                "或者不确定是否满足例外，就启用多 Agent",
                "最终结果形态",
                "区分“怎么处理”与“最终要得到什么”",
                "独立可验收",
            ),
        ),
        (
            "en",
            (
                "Multi-Agent Entry Decision",
                "multi-agent execution is the default and a direct Leader answer is a very narrow exception",
                "Task requests default to multi-agent execution",
                "Thinking requests default to multi-agent execution",
                "Use multiple agents whenever quality can benefit",
                "different areas of expertise, audience perspectives, ideation approaches, or evaluation dimensions",
                "meaningfully complementary contributions",
                "Ultra-simple direct-answer exception",
                "completed reliably in one step",
                "A short task, a single deliverable, or ease of completion is not by itself a reason to answer directly",
                "if unsure whether the exception applies—use multi-agent execution",
                "form of the final result",
                "separate *how to process the request* from *what the user ultimately wants*",
                "independently verifiable",
            ),
        ),
    ],
)
def test_leader_policy_defaults_to_multi_agent_and_routes_by_final_result(language, markers):
    policy = load_template("leader_policy", language).content

    for marker in markers:
        assert marker in policy


@pytest.mark.parametrize(
    ("language", "leader_markers", "teammate_markers"),
    [
        (
            "cn",
            ("识别提前收束建议", "关键遗漏或实质冲突", "软信号", "一次必要的简短补充"),
            ("建议收束", "边际收益很低", "软协作信号", "一次必要的简短补充", "各自向 Leader 汇报要点"),
        ),
        (
            "en",
            (
                "Recognize an early convergence suggestion",
                "critical omission or substantive conflict",
                "soft signal",
                "one necessary concise supplement",
            ),
            (
                "suggestion to converge",
                "low marginal value",
                "soft coordination signal",
                "one necessary concise supplement",
                "report key points to the Leader",
            ),
        ),
    ],
)
def test_debate_prompts_allow_soft_early_convergence(language, leader_markers, teammate_markers):
    leader_policy = load_template("leader_policy", language).content
    teammate_policy = load_template("teammate_policy", language).content

    for marker in leader_markers:
        assert marker in leader_policy
    for marker in teammate_markers:
        assert marker in teammate_policy


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
