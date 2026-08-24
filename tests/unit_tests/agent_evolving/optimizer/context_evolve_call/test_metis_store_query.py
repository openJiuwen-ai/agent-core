# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for MetisMemoryStore persistence and Manager-only retrieval."""

import json

import pytest

from openjiuwen.agent_evolving.optimizer.context_evolve_call import ContextEvolveRecord
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis import (
    EvolveState,
    MetisMemoryDelta,
    MetisMemoryStore,
    MetisQueryService,
)
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.manager_select import parse_unified_response
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.schema import (
    CodeTool,
    EnvironmentTip,
    ExecutionPlan,
    TaskReference,
    TipCategory,
)


class _FakeManagerLLM:
    """Selects every offered tip and no tools."""

    def __init__(self, tip_ids=None, tool_ids=None):
        self.tip_ids = tip_ids
        self.tool_ids = tool_ids or []

    async def async_generate(self, prompt: str) -> str:
        import json
        import re

        if self.tip_ids is None:
            offered = re.findall(r"^- (\S+) \[", prompt, flags=re.MULTILINE)
        else:
            offered = self.tip_ids
        return json.dumps({"selected_tip_ids": offered, "selected_tool_ids": self.tool_ids})


def _env_tip(tip_id, content):
    return EnvironmentTip(id=tip_id, source_task_ids=["t0"], content=content, category=TipCategory.ENVIRONMENT)


def _tool(name, implementation="return 1", deps=None):
    return CodeTool(
        id=name,
        source_task_ids=["t0"],
        function_name=name,
        docstring=f"{name} helper.",
        parameters={},
        return_annotation="int",
        implementation=implementation,
        dependencies=list(deps or []),
    )


def _delta(user_id, tips=(), tools=(), recent=()):
    return MetisMemoryDelta(
        user_id=user_id,
        task_id="t1",
        state=EvolveState(tips=list(tips), tools=list(tools), recent_queries=list(recent)),
        new_tip_ids=[t.id for t in tips],
        new_tool_ids=[t.id for t in tools],
    )


def _record(delta):
    """Wrap a delta in the dimension's commit envelope, as the rail does."""
    return ContextEvolveRecord(scope_id=delta.user_id, algorithm="metis", payload=delta)


def test_parse_unified_response_uses_last_valid_selection_json():
    response = """\
Reasoning with an unrelated code block:
```json
{"stage": "analysis"}
```
An intermediate selection:
```json
{"selected_tip_ids": ["tip_old"], "selected_tool_ids": []}
```
Final answer:
```json
{"selected_tip_ids": ["tip_final"], "selected_tool_ids": ["tool_final"]}
```
Trailing malformed output:
```json
{"selected_tip_ids":
```
"""

    assert parse_unified_response(response) == (["tip_final"], ["tool_final"])


@pytest.mark.asyncio
async def test_commit_rejects_foreign_algorithm_record():
    from openjiuwen.core.common.exception.errors import BaseError

    store = MetisMemoryStore(persist_dir=None)
    delta = _delta("u1")
    with pytest.raises(BaseError):
        await store.commit(ContextEvolveRecord(scope_id="u1", algorithm="other", payload=delta))


@pytest.mark.asyncio
async def test_commit_and_reload_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = MetisMemoryStore()
    await store.commit(
        _record(_delta("u1", tips=[_env_tip("e1", "alpha fact")], tools=[_tool("helper_a")], recent=["alpha q"]))
    )

    reloaded = MetisMemoryStore()
    state = await reloaded.load_state("u1")
    assert [t.id for t in state.tips] == ["e1"]
    assert [t.id for t in state.tools] == ["helper_a"]
    assert state.recent_queries == ["alpha q"]

    tips, tools = await reloaded.load_candidates("u1")
    assert [t.id for t in tips] == ["e1"]
    assert [t.id for t in tools] == ["helper_a"]

    snapshot = json.loads((tmp_path / "memories" / "metis" / "u1.json").read_text(encoding="utf-8"))
    assert snapshot["version"] == 2
    assert set(snapshot) == {"version", "tips", "tools", "recent_queries"}

    custom_dir = tmp_path / "custom"
    custom = MetisMemoryStore(persist_dir=str(custom_dir))
    await custom.commit(_record(_delta("u2")))
    assert (custom_dir / "u2.json").exists()


@pytest.mark.asyncio
async def test_manager_receives_all_live_candidates():
    store = MetisMemoryStore(persist_dir=None)
    dead = _env_tip("dead", "alpha but invalidated")
    dead.is_invalidated = True
    await store.commit(
        _record(
            _delta(
                "u1",
                tips=[_env_tip("e_alpha", "alpha fact"), _env_tip("e_beta", "beta fact"), dead],
                tools=[_tool("helper_a")],
            )
        )
    )

    tips, tools = await store.load_candidates("u1")
    assert [t.id for t in tips] == ["e_alpha", "e_beta"]
    assert [t.id for t in tools] == ["helper_a"]

    service = MetisQueryService(store=store, llm=_FakeManagerLLM(tip_ids=["e_beta"]))
    result = await service.retrieve("u1", "alpha question")
    assert result.evolution_context["selected_tip_ids"] == ["e_beta"]
    assert "beta fact" in result.content
    assert "alpha fact" not in result.content


@pytest.mark.asyncio
async def test_retrieve_expands_plan_and_code_dependencies():
    store = MetisMemoryStore(persist_dir=None)
    plan = ExecutionPlan(
        id="plan_1",
        source_task_ids=["t0"],
        content="When alpha: use helper_a.",
        category=TipCategory.EXECUTION_PLAN,
        related_tasks=[TaskReference(task_id="t0", task_query="alpha q")],
        dependent_tool_names=["helper_a"],
    )
    helper_a = _tool("helper_a", implementation="return helper_b()", deps=["helper_b"])
    helper_b = _tool("helper_b")
    await store.commit(_record(_delta("u1", tips=[plan], tools=[helper_a, helper_b])))

    service = MetisQueryService(store=store, llm=_FakeManagerLLM(tip_ids=["plan_1"]))
    result = await service.retrieve("u1", "alpha task")

    assert result.evolution_context["selected_tip_ids"] == ["plan_1"]
    # helper_a pulled by the plan link, helper_b by transitive closure.
    assert result.metadata["selected_tool_ids"] == ["helper_a", "helper_b"]
    assert "When alpha" in result.content
    assert "helper_a" in result.content and "helper_b" in result.content


@pytest.mark.asyncio
async def test_retrieve_empty_library_returns_empty():
    store = MetisMemoryStore(persist_dir=None)
    service = MetisQueryService(store=store, llm=_FakeManagerLLM())
    result = await service.retrieve("u1", "anything")
    assert result.content == ""
    assert result.evolution_context["selected_tip_ids"] == []
