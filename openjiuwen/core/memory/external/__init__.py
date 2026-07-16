# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""External memory provider subsystem."""

from openjiuwen.core.memory.external.agentarts_memory_provider import AgentArtsMemoryProvider
from openjiuwen.core.memory.external.mem0_provider import Mem0MemoryProvider
from openjiuwen.core.memory.external.openjiuwen_memory_provider import OpenJiuwenMemoryProvider
from openjiuwen.core.memory.external.openviking_memory_provider import OpenVikingMemoryProvider
from openjiuwen.core.memory.external.lakebase_memory_provider import LakeBaseMemoryProvider
from openjiuwen.core.memory.external.provider import MemoryProvider

try:
    from openjiuwen.core.memory.external.jiuwen_memory_provider import JiuwenMemoryProvider
except ModuleNotFoundError as exc:
    if exc.name != "jiuwen_memory":
        raise
    JiuwenMemoryProvider = None

__all__ = [
    "MemoryProvider",
    "AgentArtsMemoryProvider",
    "OpenJiuwenMemoryProvider",
    "OpenVikingMemoryProvider",
    "LakeBaseMemoryProvider",
    "Mem0MemoryProvider",
]

if JiuwenMemoryProvider is not None:
    __all__.append("JiuwenMemoryProvider")
