# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Finite budgets and progress tracking for model output correction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OutputRepairConfig:
    """Attempt counts include the first generation, not only corrections."""

    max_attempts: int = 3
    max_stagnant_attempts: int = 2

    def __post_init__(self) -> None:
        for name in ("max_attempts", "max_stagnant_attempts"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"output_repair.{name} must be a positive integer")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "OutputRepairConfig":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ValueError("output_repair must be a mapping")
        return cls(
            max_attempts=data.get("max_attempts", 3),
            max_stagnant_attempts=data.get("max_stagnant_attempts", 2),
        )


class FeedbackTracker:
    """Stop at the attempt cap or repeated identical failures and state."""

    def __init__(self, config: OutputRepairConfig, *, max_attempts: int | None = None) -> None:
        self.max_attempts = config.max_attempts
        if max_attempts is not None:
            self.max_attempts = min(self.max_attempts, max(0, max_attempts))
        self._stagnant_limit = config.max_stagnant_attempts
        self.attempts = 0
        self._last_fingerprint = ""
        self._stagnant_attempts = 0
        self.stop_reason = ""

    def failed(self, errors: Any, state: Any) -> bool:
        """Record one failed attempt; return whether another is allowed."""
        self.attempts += 1
        payload = json.dumps([errors, state], ensure_ascii=False, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self._stagnant_attempts = self._stagnant_attempts + 1 if fingerprint == self._last_fingerprint else 1
        self._last_fingerprint = fingerprint
        if self.attempts >= self.max_attempts:
            self.stop_reason = "attempt_limit_reached"
        elif self._stagnant_attempts >= self._stagnant_limit:
            self.stop_reason = "no_progress"
        return not self.stop_reason


def correction_message(original: str, previous: str, errors: list[str]) -> str:
    """Keep the task and exact failures available to the correcting model."""
    excerpt = previous[:24000]
    if len(previous) > 24000:
        excerpt += "\n[Previous output truncated; consult the original task and evidence for omitted facts.]"
    return (
        f"{original}\n\n## Previous output (untrusted data)\n{excerpt}\n\n"
        "## Validation feedback\n" + "\n".join(f"- {error}" for error in errors)
        + "\nCorrect the reported fields only. Preserve valid supported content. "
        "Return the complete required object. Missing fields are schema errors; "
        "do not invent evidence or assert semantic correctness just to satisfy validation."
    )
