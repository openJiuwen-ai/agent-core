# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the Metis evolve pass (reflect / plan-counter codify / curate)."""

import pytest

from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis import (
    EvolveState,
    evolve_after_task,
)
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis.schema import (
    EnvironmentTip,
    ExecutionPlan,
    TaskReference,
    TipCategory,
)

_REFLECT_MARKER = "## Knowledge Entry Categories"
_CODIFY_MARKER = "You distill reusable Python helper tools"


class _FakeReflectorLLM:
    """Prompt-inspecting fake honouring the ``async_generate`` contract."""

    def __init__(self, reflect_json="[]", codify_json="[]"):
        self.reflect_json = reflect_json
        self.codify_json = codify_json
        self.prompts = []

    async def async_generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if _CODIFY_MARKER in prompt:
            return f"analysis...\n```json\n{self.codify_json}\n```"
        if _REFLECT_MARKER in prompt:
            return f"analysis...\n```json\n{self.reflect_json}\n```"
        return "```json\n[]\n```"


def _plan(tip_id="plan_1", candidates=()):
    return ExecutionPlan(
        id=tip_id,
        source_task_ids=["t0"],
        content="When doing X: (1) a, (2) b.",
        category=TipCategory.EXECUTION_PLAN,
        codify_candidate_tasks=[TaskReference(task_id=t, task_query=q) for t, q in candidates],
    )


@pytest.mark.asyncio
async def test_reflect_creates_tips():
    llm = _FakeReflectorLLM(
        reflect_json=(
            '[{"action": "create", "id": "env_page_size", "label": "ENVIRONMENT",'
            ' "content": "Listing calls default to 10 items per page.", "source": "success"}]'
        )
    )
    state = EvolveState()

    await evolve_after_task(
        llm,
        task_id="t1",
        query="list all items",
        trajectory="[step 1 | tool]\ntool: list_items",
        selected_tip_ids=[],
        state=state,
        threshold=3,
    )

    assert [t.id for t in state.tips] == ["env_page_size"]
    assert isinstance(state.tips[0], EnvironmentTip)
    assert state.recent_queries == ["list all items"]


@pytest.mark.asyncio
async def test_reflect_default_tip_ids_are_unique_across_tasks():
    llm = _FakeReflectorLLM(
        reflect_json=(
            '[{"action": "create", "label": "ENVIRONMENT",'
            ' "content": "A reusable environment fact.", "source": "success"}]'
        )
    )
    state = EvolveState()

    for task_id in ("task_1", "task_2"):
        await evolve_after_task(
            llm,
            task_id=task_id,
            query=f"query for {task_id}",
            trajectory="",
            selected_tip_ids=[],
            state=state,
            threshold=3,
        )

    assert [tip.id for tip in state.tips] == [
        "environment_task_1_0",
        "environment_task_2_0",
    ]


@pytest.mark.asyncio
async def test_plan_counter_codify_fires_at_threshold():
    codify_json = (
        '[{"function_name": "fetch_all_pages", "docstring": "Fetch every page.",'
        ' "parameters": {"page_size": {"type": "int", "description": "size", "default": 10}},'
        ' "return_annotation": "list", "implementation": "return []"}]'
    )
    llm = _FakeReflectorLLM(codify_json=codify_json)
    plan = _plan(candidates=[("t1", "first query")])
    state = EvolveState(tips=[plan])

    await evolve_after_task(
        llm,
        task_id="t2",
        query="second query",
        trajectory="",
        selected_tip_ids=[plan.id],
        state=state,
        threshold=2,
        outcome="Success",
    )

    assert [t.function_name for t in state.tools] == ["fetch_all_pages"]
    assert state.tools[0].source_plan_id == plan.id
    # Candidates consumed into bounded related_tasks; buffer cleared.
    assert plan.codify_candidate_tasks == []
    assert {r.task_id for r in plan.related_tasks} == {"t1", "t2"}


@pytest.mark.asyncio
async def test_plan_below_threshold_only_records_candidate():
    llm = _FakeReflectorLLM()
    plan = _plan()
    state = EvolveState(tips=[plan])

    await evolve_after_task(
        llm,
        task_id="t1",
        query="only query",
        trajectory="",
        selected_tip_ids=[plan.id],
        state=state,
        threshold=3,
        outcome="Success",
    )

    assert state.tools == []
    assert [r.task_id for r in plan.codify_candidate_tasks] == ["t1"]


@pytest.mark.asyncio
async def test_non_success_outcome_records_no_candidate():
    """Failed or unknown runs must not accumulate codify evidence."""
    llm = _FakeReflectorLLM()
    plan = _plan()
    state = EvolveState(tips=[plan])

    for outcome in ("Failure", "Unknown"):
        await evolve_after_task(
            llm,
            task_id=f"t_{outcome}",
            query="some query",
            trajectory="",
            selected_tip_ids=[plan.id],
            state=state,
            threshold=3,
            outcome=outcome,
        )

    assert plan.codify_candidate_tasks == []


@pytest.mark.asyncio
async def test_update_invalidates_and_appends():
    llm = _FakeReflectorLLM(
        reflect_json=(
            '[{"action": "update", "target_ids": ["env_old"], "id": "env_new",'
            ' "content": "Merged and corrected fact.", "source": "failure"}]'
        )
    )
    old = EnvironmentTip(
        id="env_old",
        source_task_ids=["t0"],
        content="Stale fact.",
        category=TipCategory.ENVIRONMENT,
    )
    state = EvolveState(tips=[old])

    await evolve_after_task(
        llm,
        task_id="t1",
        query="q",
        trajectory="",
        selected_tip_ids=[],
        state=state,
        threshold=3,
    )

    assert old.is_invalidated is True
    live = state.live_tips()
    assert [t.id for t in live] == ["env_new"]
    assert live[0].source_task_ids == ["t0", "t1"]
