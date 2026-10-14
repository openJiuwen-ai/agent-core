# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_teams.agent import agent_configurator as configurator_module
from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
from openjiuwen.agent_teams.rails.elements import build_team_session_budget_rail
from openjiuwen.agent_teams.rails.session_budget_rail import (
    REASON_MAX_TOTAL_MESSAGES,
    REASON_MAX_TOTAL_ROUNDS,
    REASON_TOKEN_BUDGET,
    SessionBudgetRail,
    SessionBudgetState,
)
from openjiuwen.agent_teams.rails.team_context import inject_team_handles
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UsageMetadata
from openjiuwen.core.single_agent.rail.base import ModelCallInputs, ToolCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.base_tool import ToolOutput


def _context(
    *,
    to: object = "peer",
    tool_name: str = "send_message",
    result: object = None,
    final_report: bool | None = None,
) -> SimpleNamespace:
    tool_args = {"to": to, "content": "message"}
    if final_report is not None:
        tool_args["final_report"] = final_report
    return SimpleNamespace(
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id="call-1"),
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=result,
        ),
        extra={},
    )


def _model_context(input_tokens: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        inputs=ModelCallInputs(
            response=AssistantMessage(
                content="",
                usage_metadata=UsageMetadata(input_tokens=input_tokens),
            ),
        ),
        extra={},
    )


def _backend() -> MagicMock:
    return MagicMock()


def _rail(
    *,
    max_total_rounds: int | None = None,
    max_total_messages: int | None = None,
    token_budget: int | None = None,
    role: TeamRole = TeamRole.TEAMMATE,
    backend: MagicMock | None = None,
) -> SessionBudgetRail:
    backend = backend or _backend()
    return SessionBudgetRail(
        max_total_rounds=max_total_rounds,
        max_total_messages=max_total_messages,
        token_budget=token_budget,
        team_backend=backend,
        member_name="self",
        role=role,
        language="en",
    )


# ---------------------------------------------------------------------------
# SessionBudgetState counters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_message_breaches_at_ceiling() -> None:
    state = SessionBudgetState(max_total_messages=2, language="en")

    assert await state.record_message() is None
    assert await state.record_message() == REASON_MAX_TOTAL_MESSAGES
    assert state.exceeded == REASON_MAX_TOTAL_MESSAGES
    # Already exceeded: the reason stays at the first breach.
    assert await state.record_message() == REASON_MAX_TOTAL_MESSAGES


@pytest.mark.asyncio
async def test_record_turn_breaches_at_ceiling() -> None:
    state = SessionBudgetState(max_total_rounds=1, language="en")

    assert await state.record_turn() == REASON_MAX_TOTAL_ROUNDS
    assert state.turn_count == 1


@pytest.mark.asyncio
async def test_record_input_tokens_accumulates_and_breaches() -> None:
    state = SessionBudgetState(token_budget=100, language="en")

    assert await state.record_input_tokens(60) is None
    assert state.input_tokens == 60
    assert await state.record_input_tokens(40) == REASON_TOKEN_BUDGET
    # Non-positive deltas never count.
    assert await state.record_input_tokens(0) == REASON_TOKEN_BUDGET
    assert state.input_tokens == 100


@pytest.mark.asyncio
async def test_reset_cycle_clears_everything() -> None:
    state = SessionBudgetState(max_total_messages=1, language="en")
    await state.record_message()
    await state.mark_wrapup_delivered()

    await state.reset_cycle()

    assert state.exceeded is None
    assert state.message_count == 0
    assert state.turn_count == 0
    assert state.input_tokens == 0
    assert state.wrapup_delivered is False


@pytest.mark.asyncio
async def test_mark_wrapup_delivered_is_one_shot() -> None:
    state = SessionBudgetState(language="en")

    assert await state.mark_wrapup_delivered() is True
    assert await state.mark_wrapup_delivered() is False


# ---------------------------------------------------------------------------
# SessionBudgetRail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rails_on_one_backend_share_the_budget_state() -> None:
    backend = _backend()
    teammate = _rail(max_total_messages=2, backend=backend)
    leader = _rail(max_total_messages=2, role=TeamRole.LEADER, backend=backend)

    await teammate.after_tool_call(
        _context(to="peer", result=ToolOutput(success=True)),
    )
    await leader.after_tool_call(
        _context(to="peer", result=ToolOutput(success=True)),
    )

    assert teammate.state is leader.state
    assert teammate.state.message_count == 2
    assert teammate.state.exceeded == REASON_MAX_TOTAL_MESSAGES


@pytest.mark.asyncio
async def test_after_model_call_accumulates_input_tokens() -> None:
    rail = _rail(token_budget=150)

    await rail.after_model_call(_model_context(input_tokens=80))
    assert rail.state.input_tokens == 80

    await rail.after_model_call(_model_context(input_tokens=80))
    assert rail.state.exceeded == REASON_TOKEN_BUDGET


@pytest.mark.asyncio
async def test_after_tool_call_counts_every_successful_send_shape() -> None:
    rail = _rail(max_total_messages=3)

    # Session level has no leader/report exemption (the ISS-4993 blind spot):
    # reports to the leader and user replies count like any peer send.
    for to in ("peer", "leader", "user"):
        await rail.after_tool_call(
            _context(to=to, result=ToolOutput(success=True)),
        )
    await rail.after_tool_call(
        _context(tool_name="view_task", result=ToolOutput(success=True)),
    )
    await rail.after_tool_call(
        _context(to="peer", result=ToolOutput(success=False, error="failed")),
    )

    assert rail.state.message_count == 3
    assert rail.state.exceeded == REASON_MAX_TOTAL_MESSAGES


@pytest.mark.asyncio
async def test_exhausted_budget_rejects_ordinary_sends() -> None:
    rail = _rail(max_total_messages=1)
    await rail.after_tool_call(
        _context(to="peer", result=ToolOutput(success=True)),
    )
    assert rail.state.exceeded is not None

    ordinary = _context(to="peer")
    await rail.before_tool_call(ordinary)

    assert ordinary.extra.get("_skip_tool") is True
    assert isinstance(ordinary.inputs.tool_result, dict)
    assert "rejected" in ordinary.inputs.tool_result["error"]
    assert isinstance(ordinary.inputs.tool_msg, ToolMessage)


@pytest.mark.asyncio
async def test_exhausted_budget_still_allows_wrapup_sends() -> None:
    rail = _rail(max_total_messages=1)
    await rail.after_tool_call(
        _context(to="peer", result=ToolOutput(success=True)),
    )
    assert rail.state.exceeded is not None

    final_report = _context(to="leader", final_report=True)
    await rail.before_tool_call(final_report)
    assert final_report.extra.get("_skip_tool") is None

    user_reply = _context(to="user")
    await rail.before_tool_call(user_reply)
    assert user_reply.extra.get("_skip_tool") is None

    user_list = _context(to=["member-a", "user"])
    await rail.before_tool_call(user_list)
    assert user_list.extra.get("_skip_tool") is None


@pytest.mark.asyncio
async def test_unexhausted_budget_never_touches_sends() -> None:
    rail = _rail(max_total_messages=10)

    ordinary = _context(to="peer")
    await rail.before_tool_call(ordinary)

    assert ordinary.extra.get("_skip_tool") is None
    assert ordinary.inputs.tool_result is None


def test_wrapup_and_reject_texts_render_both_languages() -> None:
    cn = SessionBudgetState(max_total_messages=5, language="cn")
    cn.exceeded = REASON_MAX_TOTAL_MESSAGES
    cn.message_count = 5
    assert "会话预算" in cn.wrapup_instruction()
    assert "消息数" in cn.wrapup_instruction()
    assert "5/5" in cn.reject_text()

    en = SessionBudgetState(token_budget=100, language="en")
    en.exceeded = REASON_TOKEN_BUDGET
    en.input_tokens = 120
    assert "session budget" in en.wrapup_instruction().lower()
    assert "input tokens" in en.reject_text()
    assert "120/100" in en.reject_text()


def test_rail_requires_at_least_one_ceiling() -> None:
    with pytest.raises(ValueError):
        SessionBudgetRail(
            max_total_rounds=None,
            max_total_messages=None,
            token_budget=None,
            team_backend=_backend(),
            member_name="self",
        )


# ---------------------------------------------------------------------------
# Factory gate
# ---------------------------------------------------------------------------


def _factory_context(role: str = TeamRole.TEAMMATE.value) -> BuildContext:
    context = BuildContext(member_name="member", role=role, language="en")
    return context


def test_factory_returns_none_without_ceilings() -> None:
    context = _factory_context()
    inject_team_handles(context.extras, team_backend=_backend(), messager=MagicMock())

    assert (
        build_team_session_budget_rail(
            {"max_total_rounds": None, "max_total_messages": None, "token_budget": None},
            context,
        )
        is None
    )


def test_factory_returns_none_for_non_member_roles() -> None:
    context = _factory_context(role=TeamRole.HUMAN_AGENT.value)
    inject_team_handles(context.extras, team_backend=_backend(), messager=MagicMock())

    assert (
        build_team_session_budget_rail(
            {"max_total_rounds": 30, "max_total_messages": None, "token_budget": None},
            context,
        )
        is None
    )


def test_factory_builds_rail_with_configured_ceilings() -> None:
    backend = _backend()
    context = _factory_context()
    inject_team_handles(context.extras, team_backend=backend, messager=MagicMock())

    rail = build_team_session_budget_rail(
        {"max_total_rounds": 30, "max_total_messages": 300, "token_budget": 2_000_000},
        context,
    )

    assert isinstance(rail, SessionBudgetRail)
    assert rail.state.max_total_rounds == 30
    assert rail.state.max_total_messages == 300
    assert rail.state.token_budget == 2_000_000
    assert backend.session_budget_state is rail.state


# ---------------------------------------------------------------------------
# Configurator injection
# ---------------------------------------------------------------------------


def test_configurator_declares_budget_rail_for_autonomous_members(monkeypatch) -> None:
    captured: list[dict] = []

    def fake_build(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(workspace=None, sys_operation=None, model=None)

    monkeypatch.setattr(
        configurator_module.TeamHarness,
        "build",
        fake_build,
    )
    configurator = AgentConfigurator(
        card=AgentCard(id="team", name="team", description="team"),
    )
    spec = TeamAgentSpec(
        team_name="team",
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        max_total_rounds=30,
        max_total_messages=300,
        token_budget=2_000_000,
    )
    team_spec = TeamSpec(team_name="team", display_name="team", leader_member_name="leader")

    for role, member_name in (
        (TeamRole.TEAMMATE, "member"),
        (TeamRole.LEADER, "leader"),
    ):
        configurator.setup_agent(
            spec,
            TeamRuntimeContext(role=role, member_name=member_name, team_spec=team_spec),
        )

    for rails in (captured[0]["agent_spec"].rails, captured[1]["agent_spec"].rails):
        budget_rails = [rail for rail in rails if rail.type == "core.team.session_budget"]
        assert len(budget_rails) == 1
        assert budget_rails[0].params == {
            "max_total_rounds": 30,
            "max_total_messages": 300,
            "token_budget": 2_000_000,
        }


def test_configurator_skips_budget_rail_without_ceilings(monkeypatch) -> None:
    captured: list[dict] = []

    def fake_build(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(workspace=None, sys_operation=None, model=None)

    monkeypatch.setattr(
        configurator_module.TeamHarness,
        "build",
        fake_build,
    )
    configurator = AgentConfigurator(
        card=AgentCard(id="team", name="team", description="team"),
    )
    spec = TeamAgentSpec(
        team_name="team",
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
    )
    team_spec = TeamSpec(team_name="team", display_name="team", leader_member_name="leader")

    configurator.setup_agent(
        spec,
        TeamRuntimeContext(role=TeamRole.TEAMMATE, member_name="member", team_spec=team_spec),
    )

    budget_rails = [
        rail for rail in captured[0]["agent_spec"].rails
        if rail.type == "core.team.session_budget"
    ]
    assert budget_rails == []
