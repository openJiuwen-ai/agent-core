# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Sample recording helpers for the gateway."""

from __future__ import annotations

import json
from typing import Any


class SampleRecorder:
    """Own lightweight sample bookkeeping and optional local dumps."""

    def __init__(
        self,
        *,
        sample_file: str,
        dump_token_ids: bool = False,
    ) -> None:
        self._sample_file = sample_file
        self._dump_token_ids = bool(dump_token_ids)

        self._total_samples = 0

    @staticmethod
    def _append_jsonl(path: str, payload: dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def record_sample(self, sample: dict[str, Any]) -> None:
        """Record one scored sample for observability/debugging."""
        self._total_samples += 1
        self._append_jsonl(self._sample_file, sample if self._dump_token_ids else _sample_for_log(sample))

    async def snapshot_stats(self) -> dict[str, int]:
        """Return lightweight sample counters."""
        return {"total_samples": self._total_samples}


def _sample_for_log(sample: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(sample, dict):
        return {}
    out = dict(sample)
    trajectory = out.get("trajectory")
    if not isinstance(trajectory, dict):
        return out
    trajectory_out = dict(trajectory)
    for key in ("input_ids", "prompt_ids", "response_ids", "response_logprobs"):
        value = trajectory_out.pop(key, None)
        if isinstance(value, list):
            trajectory_out[f"{key}_len"] = len(value)
    out["trajectory"] = trajectory_out
    return out
