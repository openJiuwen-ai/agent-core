# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for InvokeResultAdapterRail and _convert_dict_to_schema.

Covers the AFTER_INVOKE conversion from ReActAgent raw result dict to
legacy LLMAgent output schema:
  - interrupt -> List[OutputSchema] (filtered by pending component_id)
  - answer/error -> {"output": str, "result_type": str}
  - rail no-op when raw result is None
"""

import asyncio

import pytest

from openjiuwen.core.application.llm_agent.rails.invoke_result_adapter_rail import (
    INVOKE_RESULT_KEY,
    InvokeResultAdapterRail,
    _convert_dict_to_schema,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext


class _StubWorkflowState:
    """Minimal workflow_execution_state stub carrying a .result list."""

    def __init__(self, result):
        self.result = result


class _StubSchema:
    """Schema stub with .payload.id matching the real OutputSchema surface."""

    class _Payload:
        def __init__(self, id):
            self.id = id

    def __init__(self, id):
        self.payload = self._Payload(id)


class _StubInputs:
    """Inputs stub exposing .result as an attribute.

    InvokeResultAdapterRail.after_invoke reads getattr(ctx.inputs, "result"),
    NOT ctx.inputs["result"] — so a plain dict would always yield None.
    This stub mirrors the real EventInputs object shape.
    """

    def __init__(self, result):
        self.result = result


def _make_ctx(result):
    """Build an AgentCallbackContext whose inputs.result = result.

    agent is None here — InvokeResultAdapterRail.after_invoke never reads it.
    """
    return AgentCallbackContext(agent=None, inputs=_StubInputs(result))


# ---------------------------------------------------------------------------
# _convert_dict_to_schema
# ---------------------------------------------------------------------------


class TestConvertDictToSchema:
    def test_interrupt_returns_filtered_schemas(self):
        """interrupt result returns only schemas whose payload.id matches
        the first pending component_id."""
        matching = _StubSchema(id="comp-A")
        other = _StubSchema(id="comp-B")
        state = _StubWorkflowState(result=[matching, other])

        result = {
            "result_type": "interrupt",
            "workflow_execution_state": state,
            "component_ids": ["comp-A"],
        }

        out = _convert_dict_to_schema(result)
        assert out == [matching]

    def test_interrupt_empty_component_ids_returns_all_schemas(self):
        """When component_ids is empty, pending_id is None and every
        schema in workflow_state.result is returned."""
        first = _StubSchema(id="comp-A")
        second = _StubSchema(id="comp-B")
        state = _StubWorkflowState(result=[first, second])

        result = {
            "result_type": "interrupt",
            "workflow_execution_state": state,
            "component_ids": [],
        }

        out = _convert_dict_to_schema(result)
        assert out == [first, second]

    def test_interrupt_no_workflow_state_returns_empty_list(self):
        """interrupt with no workflow_execution_state yields an empty list."""
        result = {
            "result_type": "interrupt",
            "workflow_execution_state": None,
            "component_ids": ["comp-A"],
        }
        assert _convert_dict_to_schema(result) == []

    def test_interrupt_state_non_list_result_returns_empty(self):
        """interrupt where workflow_state.result is not a list returns []."""
        state = _StubWorkflowState(result="not-a-list")
        result = {
            "result_type": "interrupt",
            "workflow_execution_state": state,
            "component_ids": [],
        }
        assert _convert_dict_to_schema(result) == []

    def test_answer_returns_output_dict(self):
        """Non-interrupt answer shape: {"output": str, "result_type": str}."""
        result = {"result_type": "answer", "output": "hello world"}
        out = _convert_dict_to_schema(result)
        assert out == {"output": "hello world", "result_type": "answer"}

    def test_error_returns_output_dict(self):
        result = {"result_type": "error", "output": "boom"}
        out = _convert_dict_to_schema(result)
        assert out == {"output": "boom", "result_type": "error"}

    def test_unknown_result_type_still_returns_dict(self):
        """Any non-interrupt result_type falls through to the dict branch."""
        result = {"result_type": "custom", "output": "ok"}
        out = _convert_dict_to_schema(result)
        assert out == {"output": "ok", "result_type": "custom"}

    def test_missing_output_key_defaults_to_empty_string(self):
        result = {"result_type": "answer"}
        out = _convert_dict_to_schema(result)
        assert out == {"output": "", "result_type": "answer"}


# ---------------------------------------------------------------------------
# InvokeResultAdapterRail
# ---------------------------------------------------------------------------


class TestInvokeResultAdapterRail:
    def test_priority_is_90(self):
        """Priority documented as 90 so MemoryRail (50) reads raw first."""
        assert InvokeResultAdapterRail.priority == 90

    def test_after_invoke_writes_adapted_result_to_extra(self):
        ctx = _make_ctx({"result_type": "answer", "output": "hi"})
        rail = InvokeResultAdapterRail()

        asyncio.run(rail.after_invoke(ctx))

        assert ctx.extra[INVOKE_RESULT_KEY] == {
            "output": "hi",
            "result_type": "answer",
        }

    def test_after_invoke_noop_when_result_is_none(self):
        """ctx.inputs.result = None short-circuits without writing extra."""
        ctx = _make_ctx(None)
        rail = InvokeResultAdapterRail()

        asyncio.run(rail.after_invoke(ctx))

        assert INVOKE_RESULT_KEY not in ctx.extra

    def test_after_invoke_noop_when_inputs_has_no_result_attr(self):
        """ctx.inputs with no .result attribute at all (getattr returns None)."""
        ctx = AgentCallbackContext(agent=None, inputs=object())
        rail = InvokeResultAdapterRail()

        asyncio.run(rail.after_invoke(ctx))

        assert INVOKE_RESULT_KEY not in ctx.extra
