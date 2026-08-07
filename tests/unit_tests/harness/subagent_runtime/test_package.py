# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Smoke tests for subagent_runtime public exports."""

from __future__ import annotations

import openjiuwen.harness.subagent_runtime as subagent_runtime


def test_public_exports_match_all() -> None:
    assert set(subagent_runtime.__all__) == {
        "WAIT_TIMEOUT_MS_DEFAULT",
        "WAIT_TIMEOUT_MS_MAX",
        "WAIT_TIMEOUT_MS_MIN",
        "ShutdownOp",
        "SpawnReservation",
        "SpawnResult",
        "StatusChannel",
        "StatusReceiver",
        "SubagentControl",
        "SubagentInstance",
        "SubagentMetadata",
        "SubagentOp",
        "SubagentRegistry",
        "SubagentRuntimeConfig",
        "SubagentSessionManager",
        "SubagentStatus",
        "SubagentStatusKind",
        "TurnOutputAggregator",
        "UserInputOp",
        "WaitResult",
        "build_subagent_id",
        "build_subagent_runtime_error",
        "new_task_id",
        "raise_subagent_capacity_invalid",
        "raise_subagent_not_found",
        "resolve_presentation",
    }


def test_import_public_symbols() -> None:
    for name in subagent_runtime.__all__:
        assert hasattr(subagent_runtime, name)
