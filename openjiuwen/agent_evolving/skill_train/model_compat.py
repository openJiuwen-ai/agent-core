# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility shims for migrated SkillOpt model helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class _TokenTracker:
    records: list[tuple[str, int, int]] = field(default_factory=list)

    def record(self, stage: str, input_tokens: int, output_tokens: int) -> None:
        self.records.append((stage, input_tokens, output_tokens))


tracker = _TokenTracker()

_REASONING_EFFORT: Optional[str] = None


def set_reasoning_effort(effort: str | None) -> None:
    """Set process-wide reasoning effort (SkillOpt model.reasoning_effort)."""
    global _REASONING_EFFORT
    text = str(effort or "").strip()
    _REASONING_EFFORT = text or None


def get_reasoning_effort() -> str | None:
    return _REASONING_EFFORT


def _needs_responses_api(deployment: str) -> bool:
    del deployment
    return False


def get_target_backend() -> str:
    return "openai_chat"


def is_target_exec_backend() -> bool:
    from openjiuwen.agent_evolving.skill_train.envs._codex_stub import is_target_exec_backend as _is_exec

    return _is_exec()


def is_target_chat_backend() -> bool:
    return True


def get_codex_exec_config() -> dict[str, Any]:
    return {}


def get_target_client() -> Any:
    """Legacy SpreadsheetBench helper — prefer chat_target_messages."""
    from openjiuwen.agent_evolving.skill_train.llm_client import get_target_client as _get

    return _get()
