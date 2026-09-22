# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for resume batch-scoped allow (P3-2) in PermissionInterruptRail.

Red-line: a single allow_once on one sibling call must NEVER approve a sibling
call with different arguments. Batch expansion is keyed by an argument
fingerprint (compute_batch_allow_key: tool name + canonical-args digest), NOT
by the bare tool name; reverting to bare-name keys (CR-001) must turn these
tests red.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import RESUME_BATCH_ALLOW_KEYS
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.interrupt_base import (
    ApproveResult,
    InterruptResult,
    RejectResult,
)
from openjiuwen.harness.rails.security.tool_security_rail import (
    PermissionInterruptRail,
    compute_batch_allow_key,
)
from openjiuwen.harness.security.models import PermissionLevel, PermissionResult


def _tool_call(name: str, args: dict, call_id: str = "call-1") -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        name=name,
        arguments=json.dumps(args, ensure_ascii=False),
    )


def _ask_rail() -> PermissionInterruptRail:
    """Rail with a stubbed engine that always decides ASK."""
    engine = MagicMock()
    engine.check_permission = AsyncMock(return_value=PermissionResult(
        permission=PermissionLevel.ASK,
        matched_rule=None,
        reason="unit-test ASK",
    ))
    return PermissionInterruptRail(config={}, engine=engine)


def _ctx(extra: dict | None = None) -> AgentCallbackContext:
    return AgentCallbackContext(agent=MagicMock(), extra=dict(extra or {}))


# --- compute_batch_allow_key (pure function) -------------------------------

def test_compute_batch_allow_key_none_and_empty_name() -> None:
    assert compute_batch_allow_key(None) == ""
    assert compute_batch_allow_key(_tool_call("", {"a": 1})) == ""


def test_compute_batch_allow_key_stable_for_same_args() -> None:
    a = _tool_call("write_file", {"path": "/tmp/a", "content": "x"})
    b = _tool_call("write_file", {"path": "/tmp/a", "content": "x"}, call_id="call-2")
    assert compute_batch_allow_key(a) == compute_batch_allow_key(b)
    assert compute_batch_allow_key(a).startswith("write_file:")


def test_compute_batch_allow_key_differs_for_different_args() -> None:
    a = _tool_call("write_file", {"path": "/tmp/a"})
    b = _tool_call("write_file", {"path": "/etc/passwd"})
    assert compute_batch_allow_key(a) != compute_batch_allow_key(b)


def test_compute_batch_allow_key_differs_for_different_tool() -> None:
    a = _tool_call("write_file", {"path": "/tmp/a"})
    b = _tool_call("read_file", {"path": "/tmp/a"})
    assert compute_batch_allow_key(a) != compute_batch_allow_key(b)


def test_compute_batch_allow_key_ignores_key_order() -> None:
    a = _tool_call("write_file", {"path": "/tmp/a", "content": "x"})
    b = _tool_call("write_file", {"content": "x", "path": "/tmp/a"})
    assert compute_batch_allow_key(a) == compute_batch_allow_key(b)


def test_compute_batch_allow_key_dict_arguments_pass_through() -> None:
    # parse_tool_args accepts raw dict arguments defensively; ToolCall's
    # schema enforces str, so bypass validation via model_construct.
    call = ToolCall.model_construct(
        id="call-d", type="function", name="write_file",
        arguments={"path": "/tmp/a"},
    )
    assert compute_batch_allow_key(call) == compute_batch_allow_key(
        _tool_call("write_file", {"path": "/tmp/a"})
    )


# --- resolve_interrupt first_check batch_allow branch -----------------------

@pytest.mark.asyncio
@pytest.mark.level1
async def test_batch_allow_approves_identical_sibling_on_replay() -> None:
    """During resume replay, a sibling call with identical tool name AND
    arguments that the user already approved is approved without a new card."""
    approved_call = _tool_call(
        "write_file", {"path": "/tmp/a", "content": "x"}, call_id="call-a",
    )
    rail = _ask_rail()
    ctx = _ctx({RESUME_BATCH_ALLOW_KEYS: {compute_batch_allow_key(approved_call)}})

    decision = await rail.resolve_interrupt(
        ctx=ctx,
        tool_call=_tool_call(
            "write_file", {"path": "/tmp/a", "content": "x"}, call_id="call-b",
        ),
        user_input=None,
        auto_confirm_config={},
    )
    assert isinstance(decision, ApproveResult)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_batch_allow_redline_different_args_must_interrupt() -> None:
    """RED LINE: allow_once on write_file(/tmp/a) must NOT approve a sibling
    with different arguments (e.g. write_file(/etc/passwd)); the unanswered
    sibling must issue a new interrupt card instead."""
    approved_call = _tool_call("write_file", {"path": "/tmp/a"}, call_id="call-a")
    rail = _ask_rail()
    ctx = _ctx({RESUME_BATCH_ALLOW_KEYS: {compute_batch_allow_key(approved_call)}})

    decision = await rail.resolve_interrupt(
        ctx=ctx,
        tool_call=_tool_call("write_file", {"path": "/etc/passwd"}, call_id="call-b"),
        user_input=None,
        auto_confirm_config={},
    )
    assert isinstance(decision, InterruptResult)
    assert isinstance(decision.request, InterruptRequest)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_batch_allow_redline_bare_tool_name_key_is_not_honored() -> None:
    """RED LINE: a bare tool-name key (the pre-fix CR-001 semantics) in the
    batch set must NOT approve a call with different arguments."""
    rail = _ask_rail()
    ctx = _ctx({RESUME_BATCH_ALLOW_KEYS: {"write_file"}})

    decision = await rail.resolve_interrupt(
        ctx=ctx,
        tool_call=_tool_call("write_file", {"path": "/etc/passwd"}),
        user_input=None,
        auto_confirm_config={},
    )
    assert isinstance(decision, InterruptResult)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_batch_allow_empty_set_or_missing_or_mismatched_key_interrupts() -> None:
    """Fail-closed for privilege expansion: empty batch set, missing extra
    key, or a key from an unrelated call all leave the ASK untouched."""
    rail = _ask_rail()
    approved_call = _tool_call("write_file", {"path": "/tmp/a"})
    unrelated_key = compute_batch_allow_key(_tool_call("read_file", {"path": "/x"}))

    for extra in (
        {},
        {RESUME_BATCH_ALLOW_KEYS: set()},
        {RESUME_BATCH_ALLOW_KEYS: {unrelated_key}},
    ):
        ctx = _ctx(extra)
        decision = await rail.resolve_interrupt(
            ctx=ctx,
            tool_call=approved_call,
            user_input=None,
            auto_confirm_config={},
        )
        assert isinstance(decision, InterruptResult), extra


@pytest.mark.asyncio
@pytest.mark.level1
async def test_batch_allow_does_not_override_engine_deny() -> None:
    """Engine decisions short-circuit before batch keys: an explicit DENY is
    rejected even when an identical sibling was previously approved."""
    engine = MagicMock()
    engine.check_permission = AsyncMock(return_value=PermissionResult(
        permission=PermissionLevel.DENY,
        matched_rule=None,
        reason="denied by policy",
    ))
    rail = PermissionInterruptRail(config={}, engine=engine)
    call = _tool_call("write_file", {"path": "/tmp/a"})
    ctx = _ctx({RESUME_BATCH_ALLOW_KEYS: {compute_batch_allow_key(call)}})

    decision = await rail.resolve_interrupt(
        ctx=ctx,
        tool_call=call,
        user_input=None,
        auto_confirm_config={},
    )
    assert isinstance(decision, RejectResult)
