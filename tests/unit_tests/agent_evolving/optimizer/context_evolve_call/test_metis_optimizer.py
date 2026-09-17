# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for MetisContextEvolveOptimizer through the SingleDimUpdater/execute_updates contract."""

from types import SimpleNamespace
from typing import cast

import pytest

from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis import EvolveState, MetisMemoryDelta
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis_optimizer import MetisContextEvolveOptimizer
from openjiuwen.agent_evolving.protocols import (
    APPEND_MODE,
    STATE_EFFECT,
    TASK_COMPLETED_SIGNAL,
    TASK_MEMORY_TARGET,
)
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.types import UpdateValue
from openjiuwen.agent_evolving.update_execution import execute_updates
from openjiuwen.agent_evolving.updater import SingleDimUpdater
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.operator.context_evolve_call.metis import MetisContextEvolveOperator

_REFLECT_JSON = (
    '[{"action": "create", "id": "env_tip", "label": "ENVIRONMENT",'
    ' "content": "A reusable environment fact.", "source": "success"}]'
)


class _FakeModel:
    """core Model stand-in for invoke_text_with_retry."""

    async def invoke(self, model, messages, temperature=None, timeout=None, **kwargs):
        prompt = messages[0]["content"]
        if "## Knowledge Entry Categories" in prompt:
            return SimpleNamespace(content=f"```json\n{_REFLECT_JSON}\n```")
        return SimpleNamespace(content="```json\n[]\n```")


def _signal(user_id="u1"):
    return EvolutionSignal(
        signal_type=TASK_COMPLETED_SIGNAL,
        section="",
        excerpt="do the task",
        context={
            "task_id": "task_1",
            "query": "do the task",
            "outcome": "Success",
            "selected_tip_ids": [],
            "user_id": user_id,
        },
    )


def _updater_and_operator(user_id="u1"):
    operator = MetisContextEvolveOperator(user_id)
    optimizer = MetisContextEvolveOptimizer(cast(Model, _FakeModel()), "fake-model")
    return SingleDimUpdater(optimizer), operator


@pytest.mark.asyncio
async def test_process_generates_delta_update():
    updater, operator = _updater_and_operator()
    operators = {operator.operator_id: operator}
    bound = updater.bind(
        operators=operators,
        targets=[TASK_MEMORY_TARGET],
        scope_states={"u1": EvolveState()},
    )
    assert bound == 1

    updates = await updater.process([], [_signal()], {})

    key = (operator.operator_id, TASK_MEMORY_TARGET)
    assert set(updates) == {key}
    value = updates[key]
    assert isinstance(value, UpdateValue)
    assert value.mode == APPEND_MODE and value.effect == STATE_EFFECT
    delta = value.payload
    assert isinstance(delta, MetisMemoryDelta)
    assert delta.user_id == "u1" and delta.new_tip_ids == ["env_tip"]

    results = execute_updates(operators, updates)
    assert len(results) == 1 and results[0].ok
    assert results[0].records == [delta]
    assert results[0].lifecycle_stage == "local_apply_completed"
