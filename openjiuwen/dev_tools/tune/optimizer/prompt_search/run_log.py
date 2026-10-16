# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""JSONL run logger for prompt-search optimization runs.

One JSONL event stream per run capturing candidate prompts, outputs, reward
scores, drift scores, convergence metrics, and the selected prompt.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union

from openjiuwen.core.common.logging import logger


class OptimizerRunLogger:
    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)

    def reset(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def record(self, stage: str, **details: Any) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            **details,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logger.warning(f"OptimizerRunLogger: failed to write log: {exc}")
        logger.debug(f"[PromptSearchOptimizer] {stage}: {_compact(details)}")


class NullRunLogger(OptimizerRunLogger):
    """Discard log events (default when no run directory is configured)."""

    def __init__(self) -> None:
        super().__init__(Path("."))

    def reset(self) -> None:
        return None

    def record(self, stage: str, **details: Any) -> None:
        logger.debug(f"[PromptSearchOptimizer] {stage}: {_compact(details)}")


def read_run_log(path: Union[str, Path], *, limit: int = 200) -> list[dict[str, Any]]:
    log_path = Path(path)
    if not log_path.is_file():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("stage"):
            entries.append(payload)
    return entries


def _compact(details: dict[str, Any]) -> str:
    if not details:
        return "{}"
    rendered = json.dumps(details, ensure_ascii=False, default=str)
    return rendered if len(rendered) <= 400 else rendered[:397] + "..."


__all__ = ["OptimizerRunLogger", "NullRunLogger", "read_run_log"]
