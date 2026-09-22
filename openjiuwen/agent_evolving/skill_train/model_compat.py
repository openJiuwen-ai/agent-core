# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility shims for migrated skill_train model helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

#: Target backends that run an external agent CLI per item instead of a chat-completions call.
EXEC_TARGET_BACKENDS = frozenset({"jiuwenswarm_cli_exec"})
CHAT_TARGET_BACKENDS = frozenset({"openai_chat"})
SUPPORTED_TARGET_BACKENDS = frozenset(EXEC_TARGET_BACKENDS | CHAT_TARGET_BACKENDS)
_EXEC_BACKENDS = EXEC_TARGET_BACKENDS
_DEFAULT_TARGET_BACKEND = "openai_chat"
_TARGET_BACKEND_ENV = "TARGET_BACKEND"


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


def _normalize_backend(backend: str | None) -> str:
    name = str(backend or "").strip().lower() or _DEFAULT_TARGET_BACKEND
    if name not in SUPPORTED_TARGET_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_TARGET_BACKENDS))
        raise ValueError(f"unsupported target backend {backend!r}; supported: {supported}")
    return name


class _TargetBackendState:
    """Process-wide holder for the target inference backend name.

    Seeded from ``TARGET_BACKEND`` so subprocess workers inherit the choice.
    """

    def __init__(self) -> None:
        raw = os.environ.get(_TARGET_BACKEND_ENV, "").strip()
        self.value: str = raw if raw in SUPPORTED_TARGET_BACKENDS else _DEFAULT_TARGET_BACKEND


_reasoning_effort = _ReasoningEffortState()
_target_backend = _TargetBackendState()


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


def set_target_backend(backend: str | None) -> str:
    """Set the process-wide target backend used for rollout.

    Returns the normalized backend name and mirrors it into
    ``TARGET_BACKEND`` so spawned workers see the same choice.

    Raises:
        ValueError: when *backend* is not in ``SUPPORTED_TARGET_BACKENDS``.
    """
    name = _normalize_backend(backend)
    _target_backend.value = name
    os.environ[_TARGET_BACKEND_ENV] = name
    return name


def get_target_backend() -> str:
    """Return the canonical target inference backend name."""
    return _target_backend.value or _DEFAULT_TARGET_BACKEND


def is_target_exec_backend() -> bool:
    """Return whether the target backend uses a CLI/exec harness."""
    return get_target_backend() in _EXEC_BACKENDS


def is_target_chat_backend() -> bool:
    """Return whether the target backend uses chat completions."""
    return not is_target_exec_backend()
