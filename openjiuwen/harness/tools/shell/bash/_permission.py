# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bash tool permission mode enum (legacy export).

Command adjudication lives in PermissionInterruptRail / ``agent_shield``.
"""
from __future__ import annotations

from enum import Enum


class PermissionMode(str, Enum):
    """Bash tool permission enforcement mode (legacy; unused by the rail path)."""

    AUTO = "auto"
    READ_ONLY = "read_only"
    ACCEPT_EDITS = "accept_edits"
    BYPASS = "bypass"
