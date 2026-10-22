# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``spawn_teammate`` model selection inside a model group."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.models.allocator import IntelliRouterAllocator
from openjiuwen.agent_teams.models.pool import IntelliRouterConfig, IntelliRouterDeployment
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.member_options import get_member_model_ref
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import SpawnTeammateTool


def _model_group_pool():
    config = IntelliRouterConfig(
        deployments=[
            IntelliRouterDeployment(
                model_name="fast",
                api_key="sk-1",
                api_base="https://a.test",
                provider="deepseek",
                fallback_tag="fast",
            ),
            IntelliRouterDeployment(
                model_name="smart",
                api_key="sk-2",
                api_base="https://b.test",
                provider="openai",
                fallback_tag="smart",
            ),
        ],
    )
    return config.to_pool_entries()


@pytest_asyncio.fixture
async def db():
    token = set_session_id("spawn-model-group-session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(
            team_name="test-team",
            display_name="Test Team",
            leader_member_name="leader-1",
        )
        yield database
    finally:
        await database.close()
        reset_session_id(token)


def _backend(db, *, pool=None) -> TeamBackend:
    allocator = IntelliRouterAllocator(pool) if pool else None
    backend = TeamBackend(
        team_name="test-team",
        member_name="leader-1",
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
        model_pool_provider=(lambda: pool) if pool else None,
    )
    backend.update_model_allocator(allocator)
    return backend


def _tool(backend: TeamBackend) -> SpawnTeammateTool:
    return SpawnTeammateTool(backend, make_translator("en"), model_config_allocator=backend.allocate_model)


def _spawn_inputs(**extra) -> dict:
    return {"member_name": "dev-1", "display_name": "Dev", "desc": "helper", **extra}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_requested_model_is_persisted_as_route_tags(db):
    backend = _backend(db, pool=_model_group_pool())

    result = await _tool(backend).invoke(_spawn_inputs(model_name="smart"))

    assert result.success
    model_ref = get_member_model_ref(await backend.get_member("dev-1"))
    assert model_ref.model_name == "*"
    assert model_ref.route_tag == "smart"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_omitted_model_uses_the_whole_group(db):
    backend = _backend(db, pool=_model_group_pool())

    result = await _tool(backend).invoke(_spawn_inputs())

    assert result.success
    model_ref = get_member_model_ref(await backend.get_member("dev-1"))
    assert model_ref.model_name == "*"
    assert model_ref.route_tag is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_unselectable_model_fails_without_registering_the_member(db):
    backend = _backend(db, pool=_model_group_pool())

    result = await _tool(backend).invoke(_spawn_inputs(model_name="gpt-9"))

    assert not result.success
    assert "gpt-9" in result.error
    assert "fast, smart" in result.error
    assert "ask the user" in result.error
    assert await backend.get_member("dev-1") is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_model_group_description_lists_selectable_models(db):
    tool = _tool(_backend(db, pool=_model_group_pool()))

    description = tool.card.input_params["properties"]["model_name"]["description"]

    assert '{"selectable_models": ["fast", "smart"]}' in description
    assert "ask the user" in description
    # Credentials never reach the model.
    assert "sk-1" not in description


@pytest.mark.asyncio
@pytest.mark.level1
async def test_without_a_model_group_the_tool_is_unchanged(db):
    backend = _backend(db)
    tool = _tool(backend)

    description = tool.card.input_params["properties"]["model_name"]["description"]
    result = await tool.invoke(_spawn_inputs(model_name="anything"))

    assert "selectable_model_catalog" not in description
    # No pool: an unknown hint keeps falling back to the per-agent model.
    assert result.success
    assert get_member_model_ref(await backend.get_member("dev-1")) is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_requested_model_survives_rebuilding_the_member_from_db(db):
    """A member rebuilt from its DB row keeps routing to the requested model."""
    from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
    from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec

    leader = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="test-team",
        model_intelli_router=IntelliRouterConfig(
            deployments=[
                IntelliRouterDeployment(
                    model_name="fast",
                    api_key="sk-1",
                    api_base="https://a.test",
                    provider="deepseek",
                    fallback_tag="fast",
                ),
                IntelliRouterDeployment(
                    model_name="smart",
                    api_key="sk-2",
                    api_base="https://b.test",
                    provider="openai",
                    fallback_tag="smart",
                ),
            ],
        ),
    ).build()
    pool = leader._configurator.team_spec.model_pool
    backend = _backend(db, pool=pool)
    leader._configurator._infra.team_backend = backend
    await _tool(backend).invoke(_spawn_inputs(model_name="smart"))
    await _tool(backend).invoke(_spawn_inputs(member_name="dev-2"))
    spawn_manager = SpawnManager(
        state=leader._state,
        configurator=leader._configurator,
        team_agent_getter=lambda: leader,
    )

    pinned = await spawn_manager.build_context_from_db("dev-1")
    unpinned = await spawn_manager.build_context_from_db("dev-2")

    pinned_router = pinned.member_model.model_client_config.intelli_router
    assert pinned_router.strategy == "tag-filtered"
    assert pinned_router.strategy_kwargs == {"fallback_tag": "smart"}
    assert unpinned.member_model.model_client_config.intelli_router.strategy != "tag-filtered"
    # The context is serialized to JSON when a member starts in a subprocess.
    restored = type(pinned).model_validate(pinned.model_dump(mode="json"))
    assert restored.member_model.model_client_config.intelli_router.strategy_kwargs == {"fallback_tag": "smart"}


@pytest.mark.asyncio
@pytest.mark.level1
async def test_predefined_external_cli_member_is_not_pinned_to_a_route_tag(db):
    """An external CLI runs on a physical deployment, never on the routed "*" entry."""
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, ExternalCliMemberSpec, TeamMemberSpec

    pool = _model_group_pool()
    backend = TeamBackend(
        team_name="cli-team",
        member_name="leader-1",
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
        model_pool_provider=lambda: pool,
        predefined_members=[
            ExternalCliMemberSpec(
                member_name="cli-1",
                display_name="CLI",
                model_name="smart",
                external_cli=ExternalCliAgentSpec(cli_agent="codex"),
            ),
            TeamMemberSpec(member_name="dev-9", display_name="Dev", model_name="smart"),
        ],
    )
    backend.update_model_allocator(IntelliRouterAllocator(pool))

    await backend.build_team(
        display_name="CLI Team",
        desc="",
        leader_display_name="Leader",
        leader_desc="",
    )

    assert get_member_model_ref(await backend.get_member("cli-1")) is None
    assert get_member_model_ref(await backend.get_member("dev-9")).route_tag == "smart"


# ---------------------------------------------------------------------------
# create_task inline members obey the same model-group contract
# ---------------------------------------------------------------------------


def _create_task_tool(backend: TeamBackend):
    from openjiuwen.agent_teams.tools.tool_task import ScheduledTaskCreateTool

    return ScheduledTaskCreateTool(backend, make_translator("en"), model_config_allocator=backend.allocate_model)


def _inline_task(task_id: str, member_name: str, **member_extra) -> dict:
    member = {"member_name": member_name, "display_name": "Dev", "desc": "helper", "lifetime": "shutdown"}
    return {"task_id": task_id, "title": task_id, "content": "c", "member": {**member, **member_extra}}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_inline_member_requested_model_is_persisted_as_route_tags(db):
    backend = _backend(db, pool=_model_group_pool())

    result = await _create_task_tool(backend).invoke({"tasks": [_inline_task("t1", "dev-1", model_name="smart")]})

    assert result.success, result.error
    model_ref = get_member_model_ref(await backend.get_member("dev-1"))
    assert model_ref.model_name == "*"
    assert model_ref.route_tag == "smart"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_inline_member_unselectable_model_fails_and_rolls_back_the_batch(db):
    backend = _backend(db, pool=_model_group_pool())

    result = await _create_task_tool(backend).invoke(
        {
            "tasks": [
                _inline_task("t1", "dev-1", model_name="fast"),
                _inline_task("t2", "dev-2", model_name="gpt-9"),
            ]
        }
    )

    assert not result.success
    assert "gpt-9" in result.error
    assert "fast, smart" in result.error
    assert "ask the user" in result.error
    # The member registered earlier in the batch is rolled back, and no task lands.
    assert await backend.get_member("dev-1") is None
    assert await backend.get_member("dev-2") is None
    assert await backend.task_manager.get("t1") is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_inline_member_schema_lists_selectable_models(db):
    tool = _create_task_tool(_backend(db, pool=_model_group_pool()))

    member = tool.card.input_params["properties"]["tasks"]["items"]["properties"]["member"]
    description = member["properties"]["model_name"]["description"]

    assert '{"selectable_models": ["fast", "smart"]}' in description
    assert "sk-1" not in description


@pytest.mark.asyncio
@pytest.mark.level1
async def test_inline_member_without_a_model_group_is_unchanged(db):
    backend = _backend(db)
    tool = _create_task_tool(backend)

    member = tool.card.input_params["properties"]["tasks"]["items"]["properties"]["member"]
    result = await tool.invoke({"tasks": [_inline_task("t1", "dev-1", model_name="anything")]})

    assert "selectable_model_catalog" not in member["properties"]["model_name"]["description"]
    assert result.success, result.error
    assert get_member_model_ref(await backend.get_member("dev-1")) is None
