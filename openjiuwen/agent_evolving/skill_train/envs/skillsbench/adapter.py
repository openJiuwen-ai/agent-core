# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillsBench DatasetEnvAdapter."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import logger

from openjiuwen.agent_evolving.skill_train.envs.dataset_adapter import DatasetEnvAdapter
from openjiuwen.agent_evolving.skill_train.envs.skillsbench.dataloader import (
    DEFAULT_SPLIT_RATIO,
    SkillsBenchDataLoader,
    default_tasks_dir,
)
from openjiuwen.agent_evolving.skill_train.envs.skillsbench.rollout import (
    SkillsBenchBatchConfig,
    run_batch,
)


class SkillsBenchAdapter(DatasetEnvAdapter):
    """Connect a local SkillsBench ``tasks/`` tree to skill_train rollouts."""

    def __init__(self, **kwargs: Any) -> None:
        self.analyst_workers = int(kwargs.get("analyst_workers", 4) or 4)
        self.failure_only = bool(kwargs.get("failure_only", False))
        self.minibatch_size = int(kwargs.get("minibatch_size", 4) or 4)
        self.edit_budget = int(kwargs.get("edit_budget", 4) or 4)
        self.exec_timeout = int(kwargs.get("exec_timeout", 900) or 900)
        self.workers = int(kwargs.get("workers", 1) or 1)
        self.verifier_mode = str(kwargs.get("verifier_mode") or "").strip() or None
        verifier_timeout = int(kwargs.get("verifier_timeout_sec", 900) or 900)
        self.task_timeout = self.exec_timeout + verifier_timeout

        data_path = str(kwargs.get("data_path") or "").strip()
        if not data_path:
            data_path = str(kwargs.get("split_dir") or "").strip() or str(default_tasks_dir())
        self.dataloader = SkillsBenchDataLoader(
            split_dir=str(kwargs.get("split_dir", "") or ""),
            data_path=data_path,
            split_mode=str(kwargs.get("split_mode", "ratio") or "ratio"),
            split_ratio=str(kwargs.get("split_ratio", DEFAULT_SPLIT_RATIO) or DEFAULT_SPLIT_RATIO),
            split_seed=int(kwargs.get("split_seed", kwargs.get("seed", 42)) or 42),
            split_output_dir=str(kwargs.get("split_output_dir", "") or ""),
            seed=int(kwargs.get("seed", 42) or 42),
            limit=int(kwargs.get("limit", 0) or 0),
        )

    def get_task_types(self) -> list[str]:
        return self.collect_task_types("skillsbench")

    def rollout(self, env_manager, skill_content: str, out_dir: str, **kwargs) -> list[dict]:
        """Score each task with its bundled verifier."""
        from openjiuwen.agent_evolving.skill_train.skill_bundle import resolve_rollout_skills

        skill_view, bundle = resolve_rollout_skills(skill_content, kwargs.get("skills"))
        items = list(env_manager)
        logger.info(
            "[skillsbench] batch n=%s skills=%s verifier=%s exec_timeout=%ss task_timeout=%ss workers=%s out=%s",
            len(items),
            ",".join(bundle) or "-",
            self.verifier_mode or "default",
            self.exec_timeout,
            self.task_timeout,
            self.workers,
            out_dir,
        )
        return run_batch(
            items,
            SkillsBenchBatchConfig(
                out_root=out_dir,
                skill_content=skill_view,
                exec_timeout=self.exec_timeout,
                workers=self.workers,
                task_timeout=self.task_timeout,
                verifier_mode=self.verifier_mode,
                skills=bundle,
                verifier_log_root=str(kwargs.get("verifier_log_root") or ""),
            ),
        )
