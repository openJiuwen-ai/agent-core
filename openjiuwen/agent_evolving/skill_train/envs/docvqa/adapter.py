# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DocVQA DatasetEnvAdapter — split loader + vision batch rollout."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openjiuwen.agent_evolving.skill_train.envs.dataset_adapter import DatasetEnvAdapter
from openjiuwen.agent_evolving.skill_train.envs.docvqa.dataloader import DocVQADataLoader
from openjiuwen.agent_evolving.skill_train.envs.docvqa.rollout import DocVQABatchConfig, run_batch


@dataclass
class DocVQARolloutRuntime:
    """Public rollout knobs mirrored onto the adapter."""

    max_turns: int
    exec_timeout: int
    workers: int
    image_detail: str
    max_completion_tokens: int
    task_timeout: int = field(init=False)

    def __post_init__(self) -> None:
        base = int(self.exec_timeout)
        stretched = base * 3 + 60
        floor = base + 60
        self.task_timeout = stretched if stretched >= floor else floor


class DocVQAAdapter(DatasetEnvAdapter):
    """Document-image VQA adapter used by skill_train ReflACT loops."""

    def __init__(self, **kwargs: Any) -> None:
        self.analyst_workers = int(kwargs.get("analyst_workers", 16) or 16)
        self.failure_only = bool(kwargs.get("failure_only", False))
        self.minibatch_size = int(kwargs.get("minibatch_size", 8) or 8)
        self.edit_budget = int(kwargs.get("edit_budget", 4) or 4)

        runtime = DocVQARolloutRuntime(
            max_turns=int(kwargs.get("max_turns", 1) or 1),
            exec_timeout=int(kwargs.get("exec_timeout", 120) or 120),
            workers=int(kwargs.get("workers", 16) or 16),
            image_detail=str(kwargs.get("image_detail", "auto") or "auto"),
            max_completion_tokens=int(kwargs.get("max_completion_tokens", 16384) or 16384),
        )
        self.runtime = runtime
        self.max_turns = runtime.max_turns
        self.exec_timeout = runtime.exec_timeout
        self.workers = runtime.workers
        self.image_detail = runtime.image_detail
        self.max_completion_tokens = runtime.max_completion_tokens

        self.dataloader = DocVQADataLoader(
            split_dir=str(kwargs.get("split_dir", "") or ""),
            data_path=str(kwargs.get("data_path", "") or ""),
            split_mode=str(kwargs.get("split_mode", "split_dir") or "split_dir"),
            split_ratio=str(kwargs.get("split_ratio", "2:1:7") or "2:1:7"),
            split_seed=int(kwargs.get("split_seed", 42) or 42),
            split_output_dir=str(kwargs.get("split_output_dir", "") or ""),
            seed=int(kwargs.get("seed", 42) or 42),
            limit=int(kwargs.get("limit", 0) or 0),
        )

    def get_task_types(self) -> list[str]:
        return self.collect_task_types("docvqa")

    def _rollout_batch_cfg(
        self,
        out_dir: str,
        skill_content: str,
        rollout_extras: dict,
    ) -> DocVQABatchConfig:
        rt = self.runtime
        return DocVQABatchConfig(
            out_root=out_dir,
            skill_content=skill_content,
            max_turns=rt.max_turns,
            exec_timeout=rt.exec_timeout,
            workers=rt.workers,
            image_detail=rt.image_detail,
            max_completion_tokens=rt.max_completion_tokens,
            diagnostic_mode=bool(rollout_extras.get("diagnostic_mode", False)),
            diagnostic_instruction=str(rollout_extras.get("diagnostic_instruction", "")),
            task_timeout=rt.task_timeout,
        )

    def rollout(self, env_manager, skill_content: str, out_dir: str, **kwargs) -> list[dict]:
        sequence = list(env_manager)
        return run_batch(sequence, self._rollout_batch_cfg(out_dir, skill_content, kwargs))
