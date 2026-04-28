# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for VALID_SECTIONS in checkpointing types."""

from __future__ import annotations

from openjiuwen.agent_evolving.checkpointing.types import VALID_SECTIONS


def test_valid_sections_contains_original_sections():
    """原始四个 section 必须存在。"""
    assert "Instructions" in VALID_SECTIONS
    assert "Examples" in VALID_SECTIONS
    assert "Troubleshooting" in VALID_SECTIONS
    assert "Scripts" in VALID_SECTIONS


def test_valid_sections_contains_collaboration():
    """Collaboration section 必须存在。"""
    assert "Collaboration" in VALID_SECTIONS


def test_valid_sections_contains_roles():
    """Roles section 必须存在（TeamSkill 专用）。"""
    assert "Roles" in VALID_SECTIONS


def test_valid_sections_contains_constraints():
    """Constraints section 必须存在（TeamSkill 专用）。"""
    assert "Constraints" in VALID_SECTIONS


def test_valid_sections_total_count():
    """总共 7 个 section。"""
    assert len(VALID_SECTIONS) == 7
