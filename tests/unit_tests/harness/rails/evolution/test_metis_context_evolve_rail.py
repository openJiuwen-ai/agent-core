# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context-evolve lifecycle tests on the canonical trajectory runtime."""

import asyncio
import json
from types import SimpleNamespace
from typing import cast

import pytest

from openjiuwen.agent_evolving.optimizer.context_evolve_call import (
    ContextEvolveRecord,
)
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis import (
    EvolveState,
    MetisMemoryDelta,
    MetisMemoryStore,
)
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.schema import (
    EnvironmentTip,
    TipCategory,
)
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.single_agent.rail import AgentCallbackContext
from openjiuwen.harness.rails.evolution.context_evolve_rail import ContextEvolveRail, default_outcome_resolver
from openjiuwen.harness.rails.evolution.metis_context_evolve_rail import MetisContextEvolveRail

_REFLECT_JSON = (
    '[{"action": "create", "id": "env_tip", "label": "ENVIRONMENT",'
    ' "content": "Distilled environment fact.", "source": "success"}]'
)


class _FakeModel:
    async def invoke(self, model, messages, temperature=None, timeout=None, **kwargs):
        prompt = messages[0]["content"]
        if "Task Orchestrator" in prompt:
            return SimpleNamespace(content=json.dumps({"selected_tip_ids": ["seed_tip"], "selected_tool_ids": []}))
        if "## Knowledge Entry Categories" in prompt:
            return SimpleNamespace(content=f"```json\n{_REFLECT_JSON}\n```")
        return SimpleNamespace(content="```json\n[]\n```")


class _RecordingBuilder:
    def __init__(self):
        self.sections = {}

    def add_section(self, section):
        self.sections[section.name] = section

    def remove_section(self, name):
        self.sections.pop(name, None)


@pytest.fixture(autouse=True)
def _clear_writer_registry():
    ContextEvolveRail._WRITER_REGISTRY.clear()
    yield
    ContextEvolveRail._WRITER_REGISTRY.clear()


def _ctx(inputs=None):
    return AgentCallbackContext(agent=None, inputs=inputs, session=None)


def _trajectory(index: int = 1) -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map({TRAJECTORY_ID: f"traj-{index}", SESSION_ID: "session-1"})
                    },
                    "scopeSpans": [],
                }
            ]
        }
    )


def _make_rail(user_id="default"):
    store = MetisMemoryStore(persist_dir=None)
    return MetisContextEvolveRail(
        llm=cast(Model, _FakeModel()),
        model="fake-model",
        store=store,
        user_id=user_id,
        trajectory_span_processor=TrajectorySpanProcessor(),
    )


async def _prepared(rail, query="solve the task", result=None):
    await rail.before_task_iteration(_ctx(SimpleNamespace(query=query)))
    prepared = await rail._prepare_evolution_input(
        _trajectory(),
        _ctx(SimpleNamespace(result=result or {"status": "completed"})),
    )
    assert prepared is not None
    return prepared


async def _seed_tip(store, user_id="default"):
    tip = EnvironmentTip(
        id="seed_tip",
        source_task_ids=["t0"],
        content="Seeded fact.",
        category=TipCategory.ENVIRONMENT,
    )
    delta = MetisMemoryDelta(user_id=user_id, task_id="t0", state=EvolveState(tips=[tip]))
    await store.commit(ContextEvolveRecord(scope_id=user_id, algorithm="metis", payload=delta))


@pytest.mark.asyncio
async def test_read_side_retrieves_injects_and_removes_stale_section():
    rail = _make_rail()
    await _seed_tip(rail.store)
    await rail.before_task_iteration(_ctx(SimpleNamespace(query="do something")))

    builder = _RecordingBuilder()
    rail.init(SimpleNamespace(system_prompt_builder=builder))
    await rail.before_model_call(_ctx())
    assert "Seeded fact." in builder.sections["metis_task_memory"].render("cn")

    await rail.before_task_iteration(_ctx(SimpleNamespace(query="")))
    await rail.before_model_call(_ctx())
    assert "metis_task_memory" not in builder.sections


@pytest.mark.asyncio
async def test_prepared_input_captures_detached_task_facts():
    rail = _make_rail()
    await _seed_tip(rail.store)
    await rail.before_task_iteration(_ctx(SimpleNamespace(query="task q")))

    prepared = await rail._prepare_evolution_input(
        _trajectory(),
        _ctx(SimpleNamespace(result={"status": "completed"})),
    )
    assert prepared is not None
    assert prepared.facts["query"] == "task q"
    assert prepared.facts["outcome"] == "Success"
    assert prepared.facts["evolution_context"]["selected_tip_ids"] == ["seed_tip"]

    rail.last_retrieval.evolution_context["selected_tip_ids"].append("late")
    assert prepared.facts["evolution_context"]["selected_tip_ids"] == ["seed_tip"]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"result_type": "answer", "output": "done"}, "Success"),
        ({"result_type": "error", "error": "completion_timeout"}, "Failure"),
        ({"error": "aborted"}, "Failure"),
    ],
)
def test_default_outcome_resolver_accepts_current_framework_results(result, expected):
    assert default_outcome_resolver(_ctx(SimpleNamespace(result=result)), None) == expected


@pytest.mark.asyncio
async def test_concurrent_tasks_keep_invoke_local_queries_isolated():
    rail = _make_rail()
    await _seed_tip(rail.store)
    first_retrieved = asyncio.Event()
    second_retrieved = asyncio.Event()

    async def capture_first():
        await rail.before_task_iteration(_ctx(SimpleNamespace(query="first query")))
        first_retrieved.set()
        await second_retrieved.wait()
        prepared = await rail._prepare_evolution_input(
            _trajectory(1),
            _ctx(SimpleNamespace(result={"status": "completed"})),
        )
        assert prepared is not None
        return prepared.facts["query"]

    async def capture_second():
        await first_retrieved.wait()
        await rail.before_task_iteration(_ctx(SimpleNamespace(query="second query")))
        second_retrieved.set()
        prepared = await rail._prepare_evolution_input(
            _trajectory(2),
            _ctx(SimpleNamespace(result={"status": "completed"})),
        )
        assert prepared is not None
        return prepared.facts["query"]

    first_query, second_query = await asyncio.gather(capture_first(), capture_second())
    assert first_query == "first query"
    assert second_query == "second query"


@pytest.mark.asyncio
async def test_run_evolution_commits_record_to_store():
    rail = _make_rail()
    await rail.run_evolution(await _prepared(rail))

    state = await rail.store.load_state("default")
    assert [tip.id for tip in state.tips] == ["env_tip"]
    assert state.recent_queries == ["solve the task"]
    assert rail.last_retrieval is None


def test_duplicate_writer_fails_fast_and_different_scopes_coexist():
    first = _make_rail("u1")
    with pytest.raises(BaseError):
        _make_rail("u1")
    _make_rail("u2")
    first.close()
    _make_rail("u1")
