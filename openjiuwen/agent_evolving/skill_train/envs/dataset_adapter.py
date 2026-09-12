# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Item-list adapter base for split-backed QA environments.

New dataset benchmarks: see ``envs/README.md`` for the dataloader / evaluator /
``process_one`` wiring pattern.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openjiuwen.agent_evolving.skill_train.datasets.base import BatchSpec, SplitDataLoader
from openjiuwen.agent_evolving.skill_train.envs.base import EnvAdapter


def _materialize_payload(batch: BatchSpec) -> list:
    """Materialize ``BatchSpec.payload`` as a concrete item sequence."""
    payload = batch.payload
    if not payload:
        return []
    return list(payload)


def _unique_task_labels(rows: list[dict], fallback: str) -> list[str]:
    labels: list[str] = []
    for row in rows:
        raw = row.get("task_type")
        label = str(raw) if raw else fallback
        if label not in labels:
            labels.append(label)
    return labels if labels else [fallback]


def _all_split_rows(loader: SplitDataLoader) -> list[dict]:
    rows: list[dict] = []
    for partition in (loader.train_items, loader.val_items, loader.test_items):
        rows.extend(partition)
    return rows


class DatasetEnvAdapter(EnvAdapter):
    """EnvAdapter driven by a :class:`SplitDataLoader`.

    Subclasses assign ``self.dataloader`` during ``__init__`` and implement
    :meth:`rollout` with :meth:`get_task_types`. Reflection / prompt loading
    inherit from :class:`EnvAdapter` unless overridden.
    """

    dataloader: SplitDataLoader

    def setup(self, cfg: dict) -> None:
        EnvAdapter.setup(self, cfg)
        self.dataloader.setup(cfg)

    def get_dataloader(self) -> SplitDataLoader | None:
        return getattr(self, "dataloader", None)

    def collect_task_types(self, default: str) -> list[str]:
        """Distinct ``task_type`` values across train / val / test splits."""
        return _unique_task_labels(_all_split_rows(self.dataloader), default)

    def build_env_from_batch(self, batch: BatchSpec, **kwargs):
        """Expose the batch payload as the rollout item sequence."""
        _ = kwargs  # reserved for subclass overrides
        return _materialize_payload(batch)

    def _materialize_split_batch(
        self,
        fetch_batch: Callable[..., BatchSpec],
        batch_args: dict[str, Any],
        passthrough: dict[str, Any],
    ) -> list:
        batch = fetch_batch(**batch_args)
        return self.build_env_from_batch(batch, **passthrough)

    def build_train_env(self, batch_size: int, seed: int, **kwargs):
        batch_args = {"batch_size": batch_size, "seed": seed}
        return self._materialize_split_batch(
            self.dataloader.build_train_batch,
            batch_args,
            kwargs,
        )

    def build_eval_env(self, env_num: int, split: str, seed: int, **kwargs):
        batch_args = {"env_num": env_num, "split": split, "seed": seed}
        return self._materialize_split_batch(
            self.dataloader.build_eval_batch,
            batch_args,
            kwargs,
        )
