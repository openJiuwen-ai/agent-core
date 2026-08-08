# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Subagent runtime foundation: types, status, registry, and instance worker."""

from openjiuwen.harness.subagent_runtime.config import (
    WAIT_TIMEOUT_MS_DEFAULT,
    WAIT_TIMEOUT_MS_MAX,
    WAIT_TIMEOUT_MS_MIN,
    SubagentRuntimeConfig,
)
from openjiuwen.harness.subagent_runtime.errors import (
    build_subagent_runtime_error,
    raise_subagent_capacity_invalid,
    raise_subagent_not_found,
)
from openjiuwen.harness.subagent_runtime.ids import build_subagent_id, new_task_id
from openjiuwen.harness.subagent_runtime.control import SubagentControl
from openjiuwen.harness.subagent_runtime.instance import SubagentInstance
from openjiuwen.harness.subagent_runtime.session_manager import SubagentSessionManager
from openjiuwen.harness.subagent_runtime.models import (
    ClosedSubagentRecord,
    ShutdownOp,
    SpawnResult,
    SubagentMetadata,
    SubagentOp,
    SubagentStatus,
    SubagentStatusKind,
    UserInputOp,
    WaitResult,
    resolve_presentation,
)
from openjiuwen.harness.subagent_runtime.registry import SpawnReservation, SubagentRegistry
from openjiuwen.harness.subagent_runtime.status import StatusChannel, StatusReceiver
from openjiuwen.harness.subagent_runtime.stream_output import TurnOutputAggregator

__all__ = [
    "WAIT_TIMEOUT_MS_DEFAULT",
    "WAIT_TIMEOUT_MS_MAX",
    "WAIT_TIMEOUT_MS_MIN",
    "ClosedSubagentRecord",
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
]
