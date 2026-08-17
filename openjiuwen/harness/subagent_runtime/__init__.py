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
    SubagentActivity,
    SubagentMessage,
    SubagentTurn,
    ResumeResult,
    ShutdownOp,
    SpawnResult,
    SubagentMetadata,
    SubagentOp,
    SubagentRecord,
    SubagentSnapshot,
    SubagentStatus,
    SubagentStatusKind,
    UserInputOp,
    WaitResult,
    resolve_presentation,
)
from openjiuwen.harness.subagent_runtime.persistence import (
    DEFAULT_SNAPSHOT_PAGE_SIZE,
    SUBAGENTS_KEY,
    merge_subagent_bucket,
    read_subagent_bucket,
)
from openjiuwen.harness.subagent_runtime.registry import SpawnReservation, SubagentRegistry
from openjiuwen.harness.subagent_runtime.status import StatusChannel, StatusReceiver
from openjiuwen.harness.subagent_runtime.activity import ActivityProjector
from openjiuwen.harness.subagent_runtime.activity_events import (
    SUBAGENT_ACTIVITY_EVENT_TYPE,
    ActivityEmitter,
)
from openjiuwen.harness.subagent_runtime.transcript import TranscriptProjector
from openjiuwen.harness.subagent_runtime.transcript_events import (
    SUBAGENT_MESSAGE_EVENT_TYPE,
    TranscriptEmitter,
)
from openjiuwen.harness.subagent_runtime.status_events import SUBAGENT_UPDATED_EVENT_TYPE
from openjiuwen.harness.subagent_runtime.stream_output import TurnOutputAggregator

__all__ = [
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
]
