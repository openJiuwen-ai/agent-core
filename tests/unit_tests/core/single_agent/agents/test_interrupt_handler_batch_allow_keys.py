# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for ToolInterruptHandler._collect_batch_allow_keys (P3-2).

Locks the consent boundary of resume-time batch-scoped allow: only sibling
calls the user actually answered with approved=True contribute an
argument-fingerprint key (compute_batch_allow_key); unanswered or rejected
siblings contribute nothing. Loosening this (e.g. collecting bare tool names
or keys of unanswered siblings) must turn the red-line tests red.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.state import (
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.harness.rails.security.tool_security_rail import compute_batch_allow_key


def _entry(call_id: str, name: str, args: dict) -> ToolInterruptEntry:
    return ToolInterruptEntry(
        tool_call=ToolCall(
            id=call_id,
            type="function",
            name=name,
            arguments=json.dumps(args, ensure_ascii=False),
        ),
        interrupt_requests={},
    )


def _state(*entries: ToolInterruptEntry) -> ToolInterruptionState:
    return ToolInterruptionState(
        ai_message=AssistantMessage(content="approval required"),
        iteration=1,
        interrupted_tools={e.tool_call.id: e for e in entries},
    )


def _approval(call_id: str, approved: bool = True) -> InteractiveInput:
    approval = InteractiveInput()
    approval.update(call_id, {"approved": approved})
    return approval


def test_approved_sibling_contributes_key() -> None:
    entry_a = _entry("call-a", "write_file", {"path": "/tmp/a"})
    state = _state(entry_a)

    keys = ToolInterruptHandler._collect_batch_allow_keys(state, _approval("call-a"))

    assert keys == {compute_batch_allow_key(entry_a.tool_call)}


def test_partial_approval_redline_does_not_expand_to_unanswered_sibling() -> None:
    """RED LINE: approving only write_file(/tmp/a) must not key
    write_file(/etc/passwd) for silent replay approval."""
    entry_a = _entry("call-a", "write_file", {"path": "/tmp/a"})
    entry_b = _entry("call-b", "write_file", {"path": "/etc/passwd"})
    state = _state(entry_a, entry_b)

    keys = ToolInterruptHandler._collect_batch_allow_keys(state, _approval("call-a"))

    key_a = compute_batch_allow_key(entry_a.tool_call)
    key_b = compute_batch_allow_key(entry_b.tool_call)
    assert key_a != key_b
    assert keys == {key_a}
    assert key_b not in keys


def test_rejected_sibling_contributes_no_key() -> None:
    entry_a = _entry("call-a", "write_file", {"path": "/tmp/a"})
    state = _state(entry_a)

    keys = ToolInterruptHandler._collect_batch_allow_keys(
        state, _approval("call-a", approved=False)
    )

    assert keys == set()


def test_all_approved_collects_all_keys() -> None:
    entry_a = _entry("call-a", "write_file", {"path": "/tmp/a"})
    entry_b = _entry("call-b", "bash", {"command": "ls /safe"})
    state = _state(entry_a, entry_b)
    approval = InteractiveInput()
    approval.update("call-a", {"approved": True})
    approval.update("call-b", {"approved": True})

    keys = ToolInterruptHandler._collect_batch_allow_keys(state, approval)

    assert keys == {
        compute_batch_allow_key(entry_a.tool_call),
        compute_batch_allow_key(entry_b.tool_call),
    }


def test_object_form_answer_is_recognized() -> None:
    entry_a = _entry("call-a", "write_file", {"path": "/tmp/a"})
    state = _state(entry_a)
    approval = InteractiveInput()
    approval.update("call-a", SimpleNamespace(approved=True))

    keys = ToolInterruptHandler._collect_batch_allow_keys(state, approval)

    assert keys == {compute_batch_allow_key(entry_a.tool_call)}


def test_plain_text_input_yields_no_keys() -> None:
    entry_a = _entry("call-a", "write_file", {"path": "/tmp/a"})
    state = _state(entry_a)

    assert ToolInterruptHandler._collect_batch_allow_keys(state, "继续") == set()


def test_empty_interactive_input_yields_no_keys() -> None:
    entry_a = _entry("call-a", "write_file", {"path": "/tmp/a"})
    state = _state(entry_a)

    assert ToolInterruptHandler._collect_batch_allow_keys(state, InteractiveInput()) == set()


def test_answer_for_unknown_id_yields_no_keys() -> None:
    entry_a = _entry("call-a", "write_file", {"path": "/tmp/a"})
    state = _state(entry_a)

    keys = ToolInterruptHandler._collect_batch_allow_keys(state, _approval("call-other"))

    assert keys == set()
