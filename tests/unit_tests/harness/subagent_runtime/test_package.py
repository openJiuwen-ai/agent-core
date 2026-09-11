# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Smoke tests for subagent_runtime public exports."""

from __future__ import annotations

import openjiuwen.harness.subagent_runtime as subagent_runtime


def test_public_exports_match_all() -> None:
    assert set(subagent_runtime.__all__) == {
        "ActivityEmitter",
        "ActivityProjector",
        "DEFAULT_SNAPSHOT_PAGE_SIZE",
        "SUBAGENTS_KEY",
        "SUBAGENT_ACTIVITY_EVENT_TYPE",
        "SUBAGENT_MESSAGE_EVENT_TYPE",
        "SUBAGENT_UPDATED_EVENT_TYPE",
        "WAIT_TIMEOUT_MS_DEFAULT",
        "WAIT_TIMEOUT_MS_MAX",
        "WAIT_TIMEOUT_MS_MIN",
        "SubagentActivity",
        "SubagentMessage",
        "SubagentTurn",
        "ShutdownOp",
        "SpawnReservation",
        "SpawnResult",
        "StatusChannel",
        "StatusReceiver",
        "SubagentControl",
        "SubagentInstance",
        "SubagentMetadata",
        "SubagentOp",
        "SubagentRecord",
        "SubagentRegistry",
        "ResumeResult",
        "SubagentRuntimeConfig",
        "SubagentSessionManager",
        "SubagentSnapshot",
        "SubagentStatus",
        "SubagentStatusKind",
        "TranscriptEmitter",
        "TranscriptProjector",
        "TurnOutputAggregator",
        "UserInputOp",
        "WaitResult",
        "build_subagent_id",
        "build_subagent_runtime_error",
        "merge_subagent_bucket",
        "new_task_id",
        "raise_subagent_capacity_invalid",
        "raise_subagent_not_found",
        "read_subagent_bucket",
        "resolve_presentation",
    }


def test_import_public_symbols() -> None:
    for name in subagent_runtime.__all__:
        assert hasattr(subagent_runtime, name)
