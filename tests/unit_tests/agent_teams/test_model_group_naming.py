# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for naming the model a model-group team member actually ran on.

A model-group pool runs every member on the logical ``"*"`` model and the
router picks the real one per call. These tests cover where that name now
reaches a reader: context-usage stream records, the persisted routed model,
and the leader's roster -- and that a non-model-group team is left untouched.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.models.pool import ModelPoolEntry, model_group_label
from openjiuwen.agent_teams.prompts.messages import diff_roster, format_member_line
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TeamContextTracker
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.member_options import (
    append_member_served_model,
    dump_member_options,
    get_member_served_models,
    load_member_options,
)
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.single_agent import AgentCard


def _group_entry(display_name: str | None = "test") -> ModelPoolEntry:
    """Build the canonical single ``"*"`` model-group pool entry."""
    metadata: dict[str, Any] = {"client": {"intelli_router": {"model_group_id": "mgp_1", "deployments": []}}}
    if display_name is not None:
        metadata["model_group"] = {"id": "mgp_1", "display_name": display_name}
    return ModelPoolEntry(
        model_name="*",
        api_key="",
        api_base_url="",
        api_provider="intelli_router",
        metadata=metadata,
    )


def _route(model_name: str, route_id: str) -> OutputSchema:
    """A selected-route event as the core stream writer emits it."""
    return OutputSchema(
        type="llm_route",
        index=0,
        payload={"status": "selected", "model_group_id": "mgp_1", "model_name": model_name, "route_id": route_id},
    )


def _usage(phase: str = "post_call", model: str = "*") -> OutputSchema:
    """A context-usage snapshot carrying the configured model name."""
    return OutputSchema(
        type="context.usage",
        index=0,
        payload={
            "phase": phase,
            "provider": "intelli_router",
            "model": model,
            "deployment": None,
            "kv_cache": {"session": {"aggregation_key": {"provider": "intelli_router", "model": model}}},
        },
    )


class _FakeRuntime:
    """Runtime exposing only ``outputs`` -- all ``_forward_outputs`` reads."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def outputs(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()


async def _forward(chunks: list[Any], *, label: str | None, recorded: list[str], record_ok: bool = True) -> list[Any]:
    """Pump ``chunks`` through a controller and return what it forwarded."""

    async def _noop(_: Any) -> None:
        return None

    async def _recorder(model_name: str) -> bool:
        recorded.append(model_name)
        return record_ok

    blueprint = SimpleNamespace(member_name="dev", role=TeamRole.TEAMMATE)
    controller = StreamController(
        blueprint_getter=lambda: blueprint,
        state=TeamAgentState(),
        resources=PrivateAgentResources(harness=_FakeRuntime(chunks)),
        status_updater=_noop,
        execution_updater=_noop,
        model_group_label_getter=lambda: label,
        served_model_recorder=_recorder,
    )
    controller.stream_queue = asyncio.Queue()
    await controller._forward_outputs()
    # The served-model DB write rides a background task off the stream; drain
    # it so the recorded list is settled before the caller asserts on it.
    if controller._served_model_tasks:
        await asyncio.gather(*controller._served_model_tasks, return_exceptions=True)
    forwarded = []
    while not controller.stream_queue.empty():
        forwarded.append(controller.stream_queue.get_nowait())
    return forwarded


# ----------------------------------------------------------------------
# model_group_label
# ----------------------------------------------------------------------


@pytest.mark.level0
def test_model_group_label_prefers_display_name() -> None:
    """The host-supplied display name names the group."""
    assert model_group_label([_group_entry("test")]) == "test"


@pytest.mark.level1
def test_model_group_label_falls_back_to_group_id() -> None:
    """Without a display name the router's model_group_id is used."""
    assert model_group_label([_group_entry(None)]) == "mgp_1"


@pytest.mark.level0
def test_model_group_label_is_none_for_a_plain_pool() -> None:
    """An ordinary model pool is not a model group."""
    plain = ModelPoolEntry(model_name="qwen-plus", api_key="k", api_base_url="http://x", api_provider="OpenAI")
    assert model_group_label([plain]) is None
    assert model_group_label([]) is None


# ----------------------------------------------------------------------
# StreamController: context.usage naming + routed-model recording
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_post_call_usage_names_the_routed_model() -> None:
    """The usage snapshot after a route event names that route's model."""
    recorded: list[str] = []
    out = await _forward([_route("qwen-plus", "rte_a"), _usage()], label="test", recorded=recorded)

    usage = out[1].payload
    assert usage["model"] == "qwen-plus"
    assert usage["deployment"] == "rte_a"
    assert usage["kv_cache"]["session"]["aggregation_key"]["model"] == "qwen-plus"
    assert recorded == ["qwen-plus"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_usage_without_a_route_names_the_model_group() -> None:
    """No selected route (e.g. an out-of-band compaction) -> the group's name."""
    out = await _forward([_usage(phase="compact"), _usage()], label="test", recorded=[])

    assert [chunk.payload["model"] for chunk in out] == ["test", "test"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_route_is_consumed_by_one_post_call_snapshot() -> None:
    """A route names only its own call; the next call without one gets the group."""
    out = await _forward([_route("qwen-plus", "rte_a"), _usage(), _usage()], label="test", recorded=[])

    assert out[1].payload["model"] == "qwen-plus"
    assert out[2].payload["model"] == "test"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_routed_model_is_recorded_only_on_change() -> None:
    """Repeated routes to the same model write once; a switch writes again."""
    recorded: list[str] = []
    chunks = [_route("qwen-plus", "a"), _usage(), _route("qwen-plus", "a"), _usage(), _route("glm-4", "b"), _usage()]
    await _forward(chunks, label="test", recorded=recorded)

    assert recorded == ["qwen-plus", "glm-4"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_unpersisted_routed_model_is_retried() -> None:
    """A write that found no member row is retried on the next route."""
    recorded: list[str] = []
    chunks = [_route("qwen-plus", "a"), _usage(), _route("qwen-plus", "a"), _usage()]
    await _forward(chunks, label="test", recorded=recorded, record_ok=False)

    assert recorded == ["qwen-plus", "qwen-plus"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_plain_pool_chunks_pass_through_untouched() -> None:
    """Outside a model group nothing is renamed and nothing is recorded."""
    recorded: list[str] = []
    out = await _forward([_route("qwen-plus", "a"), _usage(model="*")], label=None, recorded=recorded)

    assert out[1].payload["model"] == "*"
    assert recorded == []


@pytest.mark.asyncio
@pytest.mark.level1
async def test_concrete_model_usage_is_left_alone() -> None:
    """A snapshot that already names a real model is not rewritten."""
    out = await _forward([_route("glm-4", "a"), _usage(model="qwen-plus")], label="test", recorded=[])

    assert out[1].payload["model"] == "qwen-plus"


# ----------------------------------------------------------------------
# Roster rendering
# ----------------------------------------------------------------------


@pytest.mark.level0
def test_member_line_without_model_fields_is_unchanged() -> None:
    """A plain roster row renders exactly as before."""
    line = format_member_line({"member_name": "dev", "display_name": "Dev", "desc": "codes"})
    assert line == "- member_name=dev display_name=Dev :: codes"


@pytest.mark.level0
def test_member_line_shows_model_group_and_models_used() -> None:
    """A model-group leader roster row names the group and the models used."""
    line = format_member_line(
        {"member_name": "dev", "display_name": "Dev", "model_group": "test", "models_used": "glm-4 (latest call)"}
    )
    assert line == "- member_name=dev display_name=Dev model_group=test models_used=glm-4 (latest call)"


@pytest.mark.level1
def test_roster_diff_announces_a_routed_model_change() -> None:
    """A newly used model is a roster update."""
    old = [{"member_name": "dev", "display_name": "Dev", "model_group": "test", "models_used": "a"}]
    new = [{"member_name": "dev", "display_name": "Dev", "model_group": "test", "models_used": "a, b"}]
    assert [m["models_used"] for m in diff_roster(old, new).changed] == ["a, b"]


# ----------------------------------------------------------------------
# TeamContextTracker roster
# ----------------------------------------------------------------------


def _served(*models: str) -> str | None:
    """Options carrying a served-model history, one model per ms tick."""
    options = None
    for index, model in enumerate(models):
        options = append_member_served_model(options, model, 1_700_000_000_000 + index * 60_000)
    return options


class _RosterBackend:
    """Backend serving two peers (the leader's own row is never listed)."""

    def __init__(self, *, label: str | None) -> None:
        self._label = label
        self._dev = SimpleNamespace(
            member_name="dev", display_name="Dev", desc="codes", role="teammate",
            options=_served("GLM-5.3", "deepseek-v4-flash"),
        )
        self._new = SimpleNamespace(member_name="new", display_name="New", desc="", role="teammate", options=None)

    def model_group_label(self) -> str | None:
        return self._label

    async def list_members(self):
        return [self._dev, self._new]


def _roster_tracker(backend: _RosterBackend, role: TeamRole) -> TeamContextTracker:
    return TeamContextTracker(team_backend=backend, member_name="lead", role=role, language="cn")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_roster_lists_each_members_models_used() -> None:
    """The leader sees every model a member used, the latest call's marked."""
    roster = await _roster_tracker(_RosterBackend(label="test"), TeamRole.LEADER)._list_roster()

    by_name = {entry["member_name"]: entry for entry in roster}
    used = by_name["dev"]["models_used"]
    assert used.index("GLM-5.3 (since ") < used.index("deepseek-v4-flash (since ")
    assert used.endswith(", latest call)")
    assert by_name["new"]["models_used"] == "none yet"
    assert set(by_name) == {"dev", "new"}, "the leader's own model lives in its runtime prompt, not the roster"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_roster_is_unchanged_outside_a_model_group() -> None:
    """A plain-pool leader's roster carries no model fields."""
    roster = await _roster_tracker(_RosterBackend(label=None), TeamRole.LEADER)._list_roster()

    assert [entry["member_name"] for entry in roster] == ["dev", "new"]
    assert all(set(entry) == {"member_name", "display_name", "desc", "role"} for entry in roster)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_teammate_roster_carries_no_model_fields() -> None:
    """Only the leader's roster names models."""
    roster = await _roster_tracker(_RosterBackend(label="test"), TeamRole.TEAMMATE)._list_roster()

    assert all("model_group" not in entry for entry in roster)


# ----------------------------------------------------------------------
# Served-model history
# ----------------------------------------------------------------------


@pytest.mark.level1
def test_served_model_history_records_changes_only_and_is_bounded() -> None:
    """Repeats are not changes; the oldest entries fall off past the bound."""
    options = _served("a", "a", "b", "c", "d", "e", "f", "f")

    models = [item.model for item in get_member_served_models(SimpleNamespace(options=options))]
    assert models == ["b", "c", "d", "e", "f"]


@pytest.mark.level1
def test_plain_member_options_are_untouched() -> None:
    """A member never routed by a model group stores no served-model key."""
    assert get_member_served_models(SimpleNamespace(options=None)) == []
    assert append_member_served_model(None, "a", 1) is not None
    assert "served_models" not in (dump_member_options(load_member_options(None)) or "")


@pytest_asyncio.fixture
async def db():
    """In-memory team database with one team and one member."""
    token = set_session_id("session_id")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(team_name="t", display_name="T", leader_member_name="lead")
        await database.member.create_member(
            member_name="dev",
            team_name="t",
            display_name="Dev",
            agent_card=AgentCard(name="dev").model_dump_json(),
            status="ready",
        )
        yield database
    finally:
        await database.close()
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_served_model_write_appends_and_bumps_roster_mtime(db) -> None:
    """A new model lands in the history and moves the roster probe; a repeat does not."""
    before = await db.member.get_members_max_updated_at("t")
    await asyncio.sleep(0.01)

    assert await db.member.update_member_served_model("dev", "t", "qwen-plus") is True
    after_first = await db.member.get_members_max_updated_at("t")
    await asyncio.sleep(0.01)
    assert await db.member.update_member_served_model("dev", "t", "qwen-plus") is True

    member = await db.member.get_member("dev", "t")
    assert [item.model for item in get_member_served_models(member)] == ["qwen-plus"]
    assert after_first > before
    assert await db.member.get_members_max_updated_at("t") == after_first


@pytest.mark.asyncio
@pytest.mark.level1
async def test_served_model_write_reports_a_missing_member(db) -> None:
    """No row (the leader before build_team) -> False, so the caller retries."""
    assert await db.member.update_member_served_model("lead", "t", "qwen-plus") is False


@pytest.mark.asyncio
@pytest.mark.level1
async def test_roster_and_history_use_the_group_model_name() -> None:
    """Roster and history name the model as the group offers it, not by provider name."""
    route = OutputSchema(
        type="llm_route",
        index=0,
        payload={"status": "selected", "model_name": "deepseek-flash", "routed_model": "deepseek-v4-flash"},
    )
    recorded: list[str] = []
    out = await _forward([route, _usage()], label="test", recorded=recorded)

    assert recorded == ["deepseek-v4-flash"]
    assert out[1].payload["model"] == "deepseek-v4-flash"
