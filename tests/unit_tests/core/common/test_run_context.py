# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the shared request-local runtime context."""

from openjiuwen.core.common.run_context import (
    HeartbeatReason,
    RunContext,
    normalize_run_context,
)


def test_normalize_run_context_keeps_typed_context_identity() -> None:
    context = RunContext(extra={"chain_id": "chain-1"})

    assert normalize_run_context(context) is context


def test_normalize_run_context_preserves_extension_fields_in_extra() -> None:
    context = normalize_run_context(
        {
            "reason": "interval",
            "session_id": "session-a",
            "context_mode": "lightweight",
            "extra": {"message_id": "message-1"},
            "chain_id": "chain-1",
        }
    )

    assert context == RunContext(
        reason=HeartbeatReason.INTERVAL,
        session_id="session-a",
        context_mode="lightweight",
        extra={
            "message_id": "message-1",
            "chain_id": "chain-1",
        },
    )
