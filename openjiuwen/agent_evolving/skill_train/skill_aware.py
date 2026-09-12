# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill-aware reflection stubs (Phase 1: disabled by default)."""

from __future__ import annotations

from typing import Any


def is_skill_aware_enabled() -> bool:
    """Return whether skill-aware reflection is active (Phase 1: always false)."""
    return False


def get_skill_aware_appendix_source() -> str:
    """Return the configured appendix source token for skill-aware prompts."""
    return "both"


def augment_error_prompt(prompt: str) -> str:
    """Return the error-analysis prompt unchanged when skill-aware mode is off."""
    return prompt


def augment_success_prompt(prompt: str) -> str:
    """Return the success-analysis prompt unchanged when skill-aware mode is off."""
    return prompt


def extract_appendix_notes(result: dict[str, Any]) -> list[str]:
    """Extract appendix notes from an analyst result (stub returns empty list)."""
    del result
    return []
