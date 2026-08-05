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
        "SpawnResult",
        "StatusChannel",
        "StatusReceiver",
        "SubagentMetadata",
        "SubagentOp",
        "SubagentRuntimeConfig",
        "SubagentStatus",
        "SubagentStatusKind",
        "UserInputOp",
        "WaitResult",
        "build_subagent_id",
        "new_task_id",
        "resolve_presentation",
    }


def test_import_public_symbols() -> None:
    for name in subagent_runtime.__all__:
        assert hasattr(subagent_runtime, name)
