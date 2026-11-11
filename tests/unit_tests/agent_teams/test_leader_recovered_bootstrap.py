# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A cold-recovered leader is routed away from build_team only if its history holds the policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_teams.prompts import build_leader_bootstrap_section
from openjiuwen.agent_teams.rails.team_policy_rail import TeamPolicyRail
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.context_engine.processor.compressor.util import holds_team_policy
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


def _build_team_turn(result: str) -> list:
    call = ToolCall(id="c1", type="function", name="build_team", arguments="{}")
    return [AssistantMessage(content="", tool_calls=[call]), ToolMessage(content=result, tool_call_id="c1")]


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        ([], False),
        ([UserMessage(content="hello")], False),
        (_build_team_turn("Team created: team_name=t\n\npolicy"), True),
        (_build_team_turn("Existing team taken over: team_name=t\n\npolicy"), True),
        (_build_team_turn("Cannot use enable_task_verification: ..."), False),
        ([UserMessage(content="<marker>\n[TEAM_POLICY]\nrecovered policy")], True),
    ],
)
def test_holds_team_policy(messages, expected):
    assert holds_team_policy(messages) is expected


@pytest.mark.parametrize("language", ["cn", "en"])
def test_recovered_bootstrap_forbids_build_team_and_drops_the_routing(language):
    normal = build_leader_bootstrap_section(swarmflow_enabled=False, language=language).content[language]
    recovered = build_leader_bootstrap_section(
        swarmflow_enabled=False, language=language, recovered=True,
    ).content[language]

    assert recovered != normal
    assert ("不要调用 `build_team`" if language == "cn" else "Do not call `build_team`") in recovered
    assert ("两种情况" if language == "cn" else "either way") not in recovered


def _rail(*, restored: bool, rejects: bool = True) -> tuple[TeamPolicyRail, Mock, Mock]:
    backend = Mock()
    backend.hitt_enabled.return_value = False
    backend.history_restored.return_value = restored
    backend.rejects_rebuild = AsyncMock(return_value=rejects)
    rail = TeamPolicyRail(role=TeamRole.LEADER, team_backend=backend, language="en")
    rail._announce_unattached_state = AsyncMock()
    builder = Mock()
    rail.system_prompt_builder = builder
    return rail, backend, builder


def _last_bootstrap(builder: Mock) -> str:
    sections = [c.args[0] for c in builder.add_section.call_args_list if c.args[0].name == "team_bootstrap"]
    return sections[-1].content["en"]


def _ctx(messages: list) -> SimpleNamespace:
    return SimpleNamespace(context=SimpleNamespace(get_messages=lambda: messages))


@pytest.mark.asyncio
async def test_recovered_leader_holding_the_policy_is_not_routed_to_build_team():
    rail, backend, builder = _rail(restored=True)

    await rail.before_model_call(_ctx(_build_team_turn("Team created: t\n\npolicy")))

    assert "Do not call `build_team`" in _last_bootstrap(builder)
    backend.clear_history_restored.assert_not_called()


@pytest.mark.asyncio
async def test_recovered_leader_without_the_policy_can_still_receive_it():
    rail, backend, builder = _rail(restored=True)

    await rail.before_model_call(_ctx([UserMessage(content="hello")]))

    assert "Do not call `build_team`" not in _last_bootstrap(builder)
    backend.clear_history_restored.assert_called_once()


@pytest.mark.asyncio
async def test_recovered_leader_of_a_disbanded_team_may_rebuild():
    rail, _backend, builder = _rail(restored=True, rejects=False)

    await rail.before_model_call(_ctx(_build_team_turn("Team created: t\n\npolicy")))

    assert "Do not call `build_team`" not in _last_bootstrap(builder)


@pytest.mark.asyncio
async def test_leader_that_was_not_recovered_keeps_the_normal_bootstrap():
    rail, backend, builder = _rail(restored=False)

    await rail.before_model_call(_ctx([]))

    assert "Do not call `build_team`" not in _last_bootstrap(builder)
    backend.rejects_rebuild.assert_not_called()


@pytest.mark.asyncio
async def test_second_resume_after_a_host_built_team_received_the_policy_is_routed_away():
    """First resume has no policy and may call build_team; the next one must not."""
    rail, backend, builder = _rail(restored=True)

    await rail.before_model_call(_ctx([]))
    assert "Do not call `build_team`" not in _last_bootstrap(builder)
    backend.clear_history_restored.assert_called_once()

    await rail.before_invoke(_ctx([]))
    await rail.before_model_call(_ctx(_build_team_turn("Existing team taken over: t\n\npolicy")))
    assert "Do not call `build_team`" in _last_bootstrap(builder)


@pytest.mark.asyncio
async def test_continuation_is_checked_once_per_invocation():
    rail, backend, builder = _rail(restored=True)
    messages = _build_team_turn("Team created: t\n\npolicy")
    context = Mock()
    context.get_messages.return_value = messages
    ctx = SimpleNamespace(context=context)

    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)
    await rail.before_model_call(ctx)

    context.get_messages.assert_called_once()
    backend.rejects_rebuild.assert_awaited_once()
    assert "Do not call `build_team`" in _last_bootstrap(builder)

    await rail.before_invoke(ctx)
    await rail.before_model_call(ctx)
    assert context.get_messages.call_count == 2
    assert backend.rejects_rebuild.await_count == 2


@pytest.mark.asyncio
async def test_clean_team_invalidates_the_continuation_check():
    rail, backend, builder = _rail(restored=True)
    ctx = _ctx(_build_team_turn("Team created: t\n\npolicy"))
    await rail.before_model_call(ctx)
    backend.rejects_rebuild.return_value = False

    await rail.after_tool_call(SimpleNamespace(inputs=SimpleNamespace(tool_name="clean_team")))
    await rail.before_model_call(ctx)

    assert "Do not call `build_team`" not in _last_bootstrap(builder)
    assert backend.rejects_rebuild.await_count == 2
