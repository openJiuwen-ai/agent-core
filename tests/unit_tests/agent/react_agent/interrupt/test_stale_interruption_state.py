# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for bug #4756 — stale interruption state after session restart.

When the workswarm app is closed while a tool interrupt is pending and the user
opens the same session again and types a new query, the framework must NOT
replay the OLD tool_call from the persisted interrupt state. Instead the new
query should be processed as a fresh turn.

The fix attaches ``trigger_invocation_id`` to the ToolInterruptionState at the
moment the interrupt is committed, so that on the next ``invoke()`` we can
detect that the on-disk state belongs to a previous invoke cycle and discard
it cleanly instead of treating it as a resume.
"""

from __future__ import annotations

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.state import (
    BaseInterruptionState,
    ToolInterruptionState,
)


def _tool_call(call_id: str = "call_001", name: str = "demo_tool") -> ToolCall:
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


def _make_tool_state(trigger_invocation_id: str = "") -> ToolInterruptionState:
    tool_call = _tool_call()
    return ToolInterruptionState(
        ai_message=AssistantMessage(content="", tool_calls=[tool_call]),
        iteration=0,
        interrupted_tools={tool_call.id: _make_entry(tool_call)},
        original_query="echo your-name",
        trigger_invocation_id=trigger_invocation_id,
    )


def _make_entry(tool_call: ToolCall):
    from openjiuwen.core.single_agent.interrupt.state import ToolInterruptEntry

    return ToolInterruptEntry(tool_call=tool_call, interrupt_requests={})


def _make_workflow_state(trigger_invocation_id: str = "") -> BaseInterruptionState:
    return BaseInterruptionState(
        ai_message=AssistantMessage(content=""),
        iteration=0,
        original_query="echo your-name",
        trigger_invocation_id=trigger_invocation_id,
    )


class TestTriggerInvocationIdStaleness:
    """``is_stale`` must distinguish cross-restart state from in-session resume."""

    def test_state_without_trigger_invocation_id_is_not_stale(self):
        """Legacy / pre-fix state never sets the field — keep old resume semantics."""
        state = _make_tool_state(trigger_invocation_id="")
        assert state.is_stale(current_invocation_id="any-new-id") is False

    def test_state_with_matching_invocation_id_is_not_stale(self):
        """Real in-session resume must still pass through."""
        state = _make_tool_state(trigger_invocation_id="req-abc-123")
        assert state.is_stale(current_invocation_id="req-abc-123") is False

    def test_state_with_mismatched_invocation_id_is_stale(self):
        """The bug: app restarted, the persisted interrupt must be flagged stale
        and dropped so the new user query runs as a fresh turn rather than
        replaying the OLD tool_call arguments.
        """
        state = _make_tool_state(trigger_invocation_id="req-old-restarted")
        assert state.is_stale(current_invocation_id="req-new-after-restart") is True

    def test_workflow_state_also_supports_staleness_check(self):
        """Workflow interrupts share the same persistence shape and the same bug,
        so BaseInterruptionState must expose the same predicate."""
        stale = _make_workflow_state(trigger_invocation_id="req-1")
        fresh = _make_workflow_state(trigger_invocation_id="req-1")

        assert stale.is_stale(current_invocation_id="req-2") is True
        assert fresh.is_stale(current_invocation_id="req-1") is False


class TestTriggerInvocationIdPersistence:
    """``trigger_invocation_id`` round-trips through Pydantic serialization so the
    value reaches the checkpointer and survives a process restart."""

    def test_round_trip_through_model_dump(self):
        state = _make_tool_state(trigger_invocation_id="req-xyz")
        payload = state.model_dump()
        assert payload["trigger_invocation_id"] == "req-xyz"

        restored = ToolInterruptionState.model_validate(payload)
        assert restored.trigger_invocation_id == "req-xyz"
        assert restored.is_stale(current_invocation_id="req-xyz") is False
        assert restored.is_stale(current_invocation_id="req-other") is True

    def test_default_value_is_empty_string(self):
        """Old serialized payloads on disk without the field must keep working."""
        state = _make_tool_state()
        assert state.trigger_invocation_id == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])