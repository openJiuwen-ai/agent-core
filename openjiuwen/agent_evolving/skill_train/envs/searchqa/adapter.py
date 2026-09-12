# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SearchQA DatasetEnvAdapter — split loading bridged to batch rollout."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openjiuwen.agent_evolving.skill_train.envs.dataset_adapter import DatasetEnvAdapter
from openjiuwen.agent_evolving.skill_train.envs.searchqa.dataloader import SearchQADataLoader
from openjiuwen.agent_evolving.skill_train.envs.searchqa.rollout import SearchQABatchConfig, run_batch


@dataclass
class SearchQARolloutRuntime:
    """Public rollout knobs mirrored onto the adapter."""

    max_turns: int
    exec_timeout: int
    workers: int
    max_completion_tokens: int
    task_timeout: int = field(init=False)

    def __post_init__(self) -> None:
        base = int(self.exec_timeout)
        extended = base * 3 + 60
        minimum = base + 60
        self.task_timeout = extended if extended >= minimum else minimum


class SearchQAAdapter(DatasetEnvAdapter):
    """Connect SearchQA splits to parallel skill_train rollouts."""

    def __init__(self, **kwargs: Any) -> None:
        self.analyst_workers = int(kwargs.get("analyst_workers", 16) or 16)
        self.failure_only = bool(kwargs.get("failure_only", False))
        self.minibatch_size = int(kwargs.get("minibatch_size", 8) or 8)
        self.edit_budget = int(kwargs.get("edit_budget", 4) or 4)

        runtime = SearchQARolloutRuntime(
            max_turns=int(kwargs.get("max_turns", 1) or 1),
            exec_timeout=int(kwargs.get("exec_timeout", 120) or 120),
            workers=int(kwargs.get("workers", 64) or 64),
            max_completion_tokens=int(kwargs.get("max_completion_tokens", 16384) or 16384),
        )
        self.runtime = runtime
        self.max_turns = runtime.max_turns
        self.exec_timeout = runtime.exec_timeout
        self.workers = runtime.workers
        self.max_completion_tokens = runtime.max_completion_tokens

        self.dataloader = SearchQADataLoader(
            split_dir=str(kwargs.get("split_dir", "") or ""),
            data_path=str(kwargs.get("data_path", "") or ""),
            split_mode=str(kwargs.get("split_mode", "ratio") or "ratio"),
            split_ratio=str(kwargs.get("split_ratio", "2:1:7") or "2:1:7"),
            split_seed=int(kwargs.get("split_seed", 42) or 42),
            split_output_dir=str(kwargs.get("split_output_dir", "") or ""),
            seed=int(kwargs.get("seed", 42) or 42),
            limit=int(kwargs.get("limit", 0) or 0),
        )

    def get_task_types(self) -> list[str]:
        return ["qa"]

    def _compose_rollout_cfg(
        self,
        out_dir: str,
        skill_content: str,
        rollout_extras: dict,
    ) -> SearchQABatchConfig:
        rt = self.runtime
        trace_map = rollout_extras.get("diagnostic_trace_context_by_id") or {}
        return SearchQABatchConfig(
            out_root=out_dir,
            skill_content=skill_content,
            max_turns=rt.max_turns,
            exec_timeout=rt.exec_timeout,
            workers=rt.workers,
            max_completion_tokens=rt.max_completion_tokens,
            diagnostic_mode=bool(rollout_extras.get("diagnostic_mode", False)),
            diagnostic_instruction=str(rollout_extras.get("diagnostic_instruction", "")),
            diagnostic_trace_context_by_id=trace_map,
            task_timeout=rt.task_timeout,
        )

    def rollout(
        self,
        env_manager,
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict]:
        """Score SearchQA items under *skill_content* (resume-aware)."""
        sequence = list(env_manager)
        return run_batch(sequence, self._compose_rollout_cfg(out_dir, skill_content, kwargs))
