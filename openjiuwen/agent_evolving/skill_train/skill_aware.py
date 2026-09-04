# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill-aware reflection stubs (Phase 1: disabled by default)."""

from __future__ import annotations

from typing import Any


def is_skill_aware_enabled() -> bool:
    return False


def get_skill_aware_appendix_source() -> str:
    return "both"


def augment_error_prompt(prompt: str) -> str:
    return prompt


def augment_success_prompt(prompt: str) -> str:
    return prompt


def extract_appendix_notes(result: dict[str, Any]) -> list[str]:
    del result
    return []
