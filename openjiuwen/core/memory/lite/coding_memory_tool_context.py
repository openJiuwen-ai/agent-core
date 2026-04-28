# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime context for coding_memory tools."""

from __future__ import annotations

from dataclasses import dataclass

from openjiuwen.core.memory.lite.memory_tool_context_base import LiteMemoryToolContextBase


@dataclass
class CodingMemoryToolContext(LiteMemoryToolContextBase):
    """Holds state for ``coding_memory_*`` tools (node ``coding_memory``)."""

    coding_memory_dir: str = ""
    node_name: str = "coding_memory"


__all__ = ["CodingMemoryToolContext"]
