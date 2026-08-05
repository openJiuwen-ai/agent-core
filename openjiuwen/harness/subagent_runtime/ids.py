# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Subagent and task identifier generation."""

from __future__ import annotations

from uuid import uuid4


def new_task_id() -> str:
    """Return a new opaque task identifier."""
    return uuid4().hex


def build_subagent_id(
    parent_session_id: str,
    subagent_type: str,
    *,
    sticky: bool,
) -> str:
    """Build a subagent session id aligned with TaskTool._build_sub_session_id."""
    normalized_type = str(subagent_type or "").strip()
    if sticky:
        return f"{parent_session_id}_sub_{normalized_type}"
    return f"{parent_session_id}_sub_{normalized_type}_{uuid4().hex[:8]}"
