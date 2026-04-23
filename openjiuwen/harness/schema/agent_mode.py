# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Agent mode enum for DeepAgent."""
from __future__ import annotations

from enum import Enum


class AgentMode(str, Enum):
    """DeepAgent operation mode.

    Attributes:
        PLAN: Read-only planning mode — LLM explores codebase and writes a
            plan file before any modifications are made.
        NORMAL: Normal execution mode (default).
    """

    PLAN = "plan"
    NORMAL = "normal"


__all__ = ["AgentMode"]
