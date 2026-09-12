# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility shims for migrated skill_train model helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class _TokenTracker:
    records: list[tuple[str, int, int]] = field(default_factory=list)

    def record(self, stage: str, input_tokens: int, output_tokens: int) -> None:
        """Append a token-usage record for the given stage."""
        self.records.append((stage, input_tokens, output_tokens))


tracker = _TokenTracker()


class _ReasoningEffortState:
    """Process-wide holder for reasoning effort configuration."""

    value: Optional[str] = None


_reasoning_effort = _ReasoningEffortState()


def set_reasoning_effort(effort: str | None) -> None:
    """Set process-wide reasoning effort (skill_train model.reasoning_effort)."""
    text = str(effort or "").strip()
    _reasoning_effort.value = text or None


def get_reasoning_effort() -> str | None:
    """Return the configured process-wide reasoning effort."""
    return _reasoning_effort.value


def _needs_responses_api(deployment: str) -> bool:
    """Return whether the deployment requires the Responses API."""
    del deployment
    return False


def get_target_backend() -> str:
    """Return the canonical target inference backend name."""
    return "openai_chat"


def is_target_exec_backend() -> bool:
    """Return whether the target backend uses the exec API."""
    return False


def is_target_chat_backend() -> bool:
    """Return whether the target backend uses chat completions."""
    return True
