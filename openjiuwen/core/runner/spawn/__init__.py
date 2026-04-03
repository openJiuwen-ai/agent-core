# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.runner.spawn.child_process import (
    execute_agent,
    handle_health_check,
    handle_shutdown,
    process_message_loop,
    read_input_from_stdin,
    run_spawned_process,
    write_output_to_stdout,
)
from openjiuwen.core.runner.spawn.agent_config import (
    ClassAgentSpawnConfig,
    parse_spawn_agent_config,
    SpawnAgentConfig,
    SpawnAgentKind,
    deserialize_runner_config,
    serialize_runner_config,
)
from openjiuwen.core.runner.spawn.process_manager import (
    SpawnConfig,
    SpawnedProcessHandle,
    spawn_process,
)
from openjiuwen.core.runner.spawn.protocol import (
    Message,
    MessageType,
    deserialize_message,
    deserialize_message_from_stream,
    serialize_message,
    serialize_message_to_stream,
)

__all__ = [
    "Message",
    "MessageType",
    "serialize_message",
    "deserialize_message",
    "serialize_message_to_stream",
    "deserialize_message_from_stream",
    "read_input_from_stdin",
    "write_output_to_stdout",
    "handle_health_check",
    "handle_shutdown",
    "execute_agent",
    "process_message_loop",
    "run_spawned_process",
    "ClassAgentSpawnConfig",
    "parse_spawn_agent_config",
    "SpawnAgentConfig",
    "SpawnAgentKind",
    "deserialize_runner_config",
    "serialize_runner_config",
    "SpawnConfig",
    "SpawnedProcessHandle",
    "spawn_process",
]
