# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Built-in CLI model selection for external-CLI members: spec, options, backend and tools."""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, ExternalCliBuiltinModel
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.member_options import (
    MemberBuiltinModel,
    build_member_options,
    get_member_builtin_model,
    get_member_model_ref,
    load_member_options,
    promote_member_fallback_model,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import SetMemberModelTool, SpawnExternalCliTool, create_team_tools
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from tests.test_logger import logger

_TEAM = "builtin_team"


def _claude_spec(**overrides: object) -> ExternalCliAgentSpec:
    values: dict[str, object] = {
        "cli_agent": "claude",
        "builtin_models": [
            ExternalCliBuiltinModel(name="sonnet", efforts=["low", "high"], default_effort="high"),
            ExternalCliBuiltinModel(name="haiku", description="routine work"),
        ],
    }
    values.update(overrides)
    return ExternalCliAgentSpec(**values)


@pytest_asyncio.fixture
async def db() -> AsyncIterator[TeamDatabase]:
    token = set_session_id("builtin-session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(team_name=_TEAM, display_name="Builtin", leader_member_name="leader1")
        yield database
    finally:
        reset_session_id(token)
        await database.close()


def _backend(db: TeamDatabase, *specs: ExternalCliAgentSpec) -> TeamBackend:
    return TeamBackend(
        team_name=_TEAM,
        member_name="leader1",
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
        external_cli_agents=list(specs),
    )


async def _spawn(backend: TeamBackend, **inputs: object) -> object:
    tool = SpawnExternalCliTool(backend, make_translator("en"))
    values: dict[str, object] = {
        "member_name": "claude-1",
        "display_name": "Claude One",
        "prompt": "worker",
        "cli_agent": "claude",
        "fallback_model_name": None,
    }
    values.update(inputs)
    return await tool.invoke(values)


@pytest.mark.level0
def test_builtin_catalog_is_validated_on_the_spec() -> None:
    spec = _claude_spec()
    assert spec.find_builtin_model("haiku").description == "routine work"
    assert spec.find_builtin_model("opus") is None
    with pytest.raises(ValueError, match="default_effort"):
        ExternalCliBuiltinModel(name="sonnet", efforts=["low"], default_effort="max")
    with pytest.raises(ValueError, match="unique"):
        _claude_spec(builtin_models=[ExternalCliBuiltinModel(name="a"), ExternalCliBuiltinModel(name="a")])
    with pytest.raises(ValueError, match="builtin_models is only valid"):
        ExternalCliAgentSpec(cli_agent="gemini", builtin_models=[ExternalCliBuiltinModel(name="a")])


@pytest.mark.level0
def test_member_options_round_trip_and_fallback_promotion_drops_the_builtin_model() -> None:
    raw = build_member_options(
        fallback_model_ref={"model_name": "pool-model", "model_index": 0},
        builtin_model=MemberBuiltinModel(model="sonnet", effort="low"),
        cli_agent="claude",
    )
    assert load_member_options(raw).builtin_model == MemberBuiltinModel(model="sonnet", effort="low")

    promoted = promote_member_fallback_model(raw)
    record = {"options": promoted}
    assert get_member_builtin_model(record) is None, "the fallback endpoint replaces the built-in model"
    assert get_member_model_ref(record).model_name == "pool-model"


@pytest.mark.level0
@pytest.mark.asyncio
async def test_resolve_builtin_model_checks_catalog_and_applies_default_effort(db: TeamDatabase) -> None:
    backend = _backend(db, _claude_spec(), ExternalCliAgentSpec(cli_agent="codex"))
    assert backend.builtin_models_enabled()

    selection, reason = backend.resolve_builtin_model("claude", "sonnet", None)
    assert selection == MemberBuiltinModel(model="sonnet", effort="high") and reason == ""
    assert backend.resolve_builtin_model("claude", "haiku", None)[0] == MemberBuiltinModel(model="haiku")
    assert "not declared" in backend.resolve_builtin_model("claude", "opus", None)[1]
    assert "not supported" in backend.resolve_builtin_model("claude", "haiku", "low")[1]
    assert "declares no builtin_models" in backend.resolve_builtin_model("codex", "gpt-5.5", None)[1]


@pytest.mark.level0
@pytest.mark.asyncio
async def test_spawn_persists_the_builtin_model(db: TeamDatabase) -> None:
    backend = _backend(db, _claude_spec())
    result = await _spawn(backend, builtin_model="sonnet", effort="low")
    assert result.success, result.error

    row = await db.member.get_member("claude-1", _TEAM)
    assert get_member_builtin_model(row) == MemberBuiltinModel(model="sonnet", effort="low")


@pytest.mark.level1
@pytest.mark.asyncio
async def test_spawn_rejects_invalid_builtin_choices(db: TeamDatabase) -> None:
    backend = _backend(db, _claude_spec())
    assert "requires 'builtin_model'" in (await _spawn(backend, effort="low")).error
    assert "not declared" in (await _spawn(backend, builtin_model="opus")).error
    exclusive = await _spawn(backend, builtin_model="sonnet", model_name="pool-model")
    assert "mutually exclusive" in exclusive.error
    assert await db.member.get_member("claude-1", _TEAM) is None

    plain = _backend(db, ExternalCliAgentSpec(cli_agent="claude"))
    gated = await _spawn(plain, builtin_model="sonnet")
    assert "unavailable" in gated.error, "MCP clients bypass the schema, so the tool rejects gated input"


@pytest.mark.level0
@pytest.mark.asyncio
async def test_builtin_parameters_and_prose_follow_the_catalog_gate(db: TeamDatabase) -> None:
    t = make_translator("en")
    enabled = SpawnExternalCliTool(_backend(db, _claude_spec()), t)
    properties = enabled.card.input_params["properties"]
    assert {"builtin_model", "effort"} <= set(properties)
    assert '"name": "sonnet"' in properties["builtin_model"]["description"]
    assert "Built-in models" in enabled.card.description

    disabled = SpawnExternalCliTool(_backend(db, ExternalCliAgentSpec(cli_agent="claude")), t)
    assert "builtin_model" not in disabled.card.input_params["properties"]
    assert "builtin_model" not in disabled.card.description
    assert "\n\n\n" not in disabled.card.description

    enabled_names = {tool.card.name for tool in create_team_tools(role="leader", agent_team=_backend(db, _claude_spec()))}
    disabled_names = {
        tool.card.name
        for tool in create_team_tools(role="leader", agent_team=_backend(db, ExternalCliAgentSpec(cli_agent="claude")))
    }
    member_names = {tool.card.name for tool in create_team_tools(role="teammate", agent_team=_backend(db, _claude_spec()))}
    assert "set_member_model" in enabled_names
    assert "set_member_model" not in disabled_names
    assert "set_member_model" not in member_names


@pytest.mark.level0
@pytest.mark.asyncio
async def test_set_member_model_persists_then_switches_the_live_member(db: TeamDatabase) -> None:
    backend = _backend(db, _claude_spec())
    assert (await _spawn(backend, builtin_model="sonnet", effort="low")).success
    switched: list[tuple[str, MemberBuiltinModel]] = []

    async def apply(member_name: str, builtin: MemberBuiltinModel) -> bool:
        switched.append((member_name, builtin))
        return True

    backend.set_member_model_fn(apply)
    tool = SetMemberModelTool(backend, make_translator("en"))

    effort_only = await tool.invoke({"member_name": "claude-1", "effort": "high"})
    assert effort_only.success, effort_only.error
    assert (effort_only.data["model"], effort_only.data["effort"]) == ("sonnet", "high")
    assert "applies before its next turn" in tool.render_for_llm(effort_only)

    model_only = await tool.invoke({"member_name": "claude-1", "model": "haiku"})
    assert (model_only.data["model"], model_only.data["effort"]) == ("haiku", None)
    row = await db.member.get_member("claude-1", _TEAM)
    assert get_member_builtin_model(row) == MemberBuiltinModel(model="haiku")
    assert switched == [
        ("claude-1", MemberBuiltinModel(model="sonnet", effort="high")),
        ("claude-1", MemberBuiltinModel(model="haiku")),
    ]
    logger.info("builtin model switch result: %s", tool.render_for_llm(model_only))


@pytest.mark.level1
@pytest.mark.asyncio
async def test_set_member_model_without_a_live_member_applies_at_next_start(db: TeamDatabase) -> None:
    backend = _backend(db, _claude_spec())
    assert (await _spawn(backend)).success

    async def not_running(member_name: str, builtin: MemberBuiltinModel) -> bool:
        _ = member_name, builtin
        return False

    backend.set_member_model_fn(not_running)
    missing_model = await backend.set_member_model("claude-1", model=None, effort="low")
    assert not missing_model.ok and "'model' is required" in missing_model.reason

    result = await backend.set_member_model("claude-1", model="sonnet", effort=None)
    assert result.ok and not result.applied_live
    assert result.effort == "high", "a new model without an effort takes its default"


@pytest.mark.level1
@pytest.mark.asyncio
async def test_set_member_model_rejects_members_it_cannot_switch(db: TeamDatabase) -> None:
    backend = _backend(db, _claude_spec())
    assert not (await backend.set_member_model("claude-1", model="sonnet", effort=None)).ok
    assert "'model' or 'effort' is required" in (await backend.set_member_model("x", model=None, effort=None)).reason

    await backend.spawn_member(
        member_name="pool-member",
        display_name="Pool",
        agent_card=AgentCard(id=f"{_TEAM}_pool-member", name="Pool", description=""),
        cli_agent="claude",
        allocation=_Allocation(),
    )
    on_pool = await backend.set_member_model("pool-member", model="sonnet", effort=None)
    assert "team model pool endpoint" in on_pool.reason

    member = _backend(db, _claude_spec())
    member.is_leader = False
    assert "Only the leader" in (await member.set_member_model("pool-member", model="sonnet", effort=None)).reason


class _Allocation:
    """Minimal pool allocation persisted as a model reference."""

    @staticmethod
    def to_db_ref() -> dict[str, object]:
        return {"model_name": "pool-model", "model_index": 0}
