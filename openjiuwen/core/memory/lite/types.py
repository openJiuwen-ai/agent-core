# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Memory system type definitions."""

from dataclasses import dataclass


@dataclass
class MemoryChunk:
    """A chunk of memory content."""
    text: str
    start_line: int
    end_line: int