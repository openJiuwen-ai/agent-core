# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for VALID_SECTIONS in checkpointing types."""

from __future__ import annotations

from openjiuwen.agent_evolving.checkpointing.types import VALID_SECTIONS

def test_valid_sections_contains_required_sections():
    """验证 required sections 与总数。"""
    expected = {
        "Instructions",
        "Examples",
        "Troubleshooting",
        "Scripts",
        "Collaboration",
        "Roles",
        "Constraints",
        "Workflow",
    }
    assert expected <= set(VALID_SECTIONS)
    assert len(VALID_SECTIONS) == len(expected)
