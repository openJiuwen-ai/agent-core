# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared result bundle for OfficeQA dialogue runners."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _DialogueOutcome:
    """Result bundle returned by every OfficeQA dialogue runner."""

    system: str
    user: str
    response: str
    answer: str
    conversation: list[dict] = field(default_factory=list)
    fail_reason: str = ""
    response_metadata: dict = field(default_factory=dict)
