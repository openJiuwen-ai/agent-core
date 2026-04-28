# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime context for general (non-coding) lite memory tools."""

from __future__ import annotations

from dataclasses import dataclass

from openjiuwen.core.memory.lite.memory_tool_context_base import LiteMemoryToolContextBase


@dataclass
class MemoryToolContext(LiteMemoryToolContextBase):
    """Holds state for ``memory_search`` / ``memory_get`` / read-write tools (node ``memory``)."""


__all__ = ["MemoryToolContext"]
