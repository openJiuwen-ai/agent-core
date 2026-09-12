# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Abstract environment contract for skill_train trainers.

Adapters bridge the training loop to a concrete benchmark: build batches,
run rollouts, optionally reflect into patches, and expose task-type labels.

QA item-list benchmarks usually subclass
:class:`~openjiuwen.agent_evolving.skill_train.envs.dataset_adapter.DatasetEnvAdapter`
instead of implementing :class:`EnvAdapter` from scratch. See ``envs/README.md``.
"""

from __future__ import annotations

import os
import random
from abc import ABC, abstractmethod
from typing import Any

from openjiuwen.agent_evolving.skill_train.datasets.base import BaseDataLoader, BatchSpec
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt
from openjiuwen.agent_evolving.skill_train.update_modes import (
    is_full_rewrite_minibatch_mode,
    is_rewrite_mode,
)

_PREVIEW_CHARS = 400
_FALLBACK_TASK = "unknown"

_ERROR_PROMPT_BY_MODE = (
    (is_full_rewrite_minibatch_mode, "analyst_error_full_rewrite"),
    (is_rewrite_mode, "analyst_error_rewrite"),
)
_SUCCESS_PROMPT_BY_MODE = (
    (is_full_rewrite_minibatch_mode, "analyst_success_full_rewrite"),
    (is_rewrite_mode, "analyst_success_rewrite"),
)


def _as_id(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _index_by_id(items: list[dict]) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _as_id(item.get("id"))
        if key is not None:
            indexed[key] = item
    return indexed


def _task_label(result: dict, item: dict) -> str:
    for source in (result, item):
        for field in ("task_type", "subtype"):
            raw = source.get(field)
            if raw:
                return str(raw)
    return _FALLBACK_TASK


def _pair_by_outcome(
    results: list[dict],
    item_by_id: dict[str, dict],
    *,
    want_hard: bool,
) -> list[tuple[dict, dict]]:
    pairs: list[tuple[dict, dict]] = []
    for result in results:
        key = _as_id(result.get("id"))
        if key is None or key not in item_by_id:
            continue
        hard = bool(result.get("hard"))
        if hard == want_hard:
            pairs.append((result, item_by_id[key]))
    return pairs


def _sample_diverse(
    pool: list[tuple[dict, dict]],
    quota: int,
    rng: random.Random,
) -> list[dict]:
    if quota <= 0 or not pool:
        return []
    order = list(pool)
    rng.shuffle(order)

    chosen: list[dict] = []
    used_ids: set[str] = set()
    used_types: set[str] = set()

    for result, item in order:
        item_id = str(item["id"])
        label = _task_label(result, item)
        if item_id in used_ids or label in used_types:
            continue
        chosen.append(item)
        used_ids.add(item_id)
        used_types.add(label)
        if len(chosen) >= quota:
            return chosen

    for _, item in order:
        item_id = str(item["id"])
        if item_id in used_ids:
            continue
        chosen.append(item)
        used_ids.add(item_id)
        if len(chosen) >= quota:
            break
    return chosen


def _resolve_prediction_dir(out_dir: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    nested = os.path.join(out_dir, "rollout", "predictions")
    if os.path.isdir(nested):
        return nested
    return os.path.join(out_dir, "predictions")


def _prompt_for_mode(
    loader,
    mode: str | None,
    table: tuple[tuple[Any, str], ...],
    default_name: str,
) -> str | None:
    for predicate, name in table:
        if predicate(mode):
            hit = loader(name)
            if hit is not None:
                return hit
    return loader(default_name)


def _patches_root(out_dir: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    return os.path.join(out_dir, "patches")


class EnvAdapter(ABC):
    """Trainer-facing hook surface for one skill_train environment."""

    def setup(self, cfg: dict) -> None:
        """One-shot trainer hook before the outer loop; default stores ``cfg``."""
        self._cfg = dict(cfg)

    def get_dataloader(self) -> BaseDataLoader | None:
        """Optional task dataloader; default returns ``None``."""
        return None

    def requires_ray(self) -> bool:
        """Whether Ray must be initialized for this adapter."""
        return False

    @abstractmethod
    def get_task_types(self) -> list[str]:
        """Distinct task-type strings used for stratified sampling."""

    @abstractmethod
    def rollout(self, env_manager, skill_content: str, out_dir: str, **kwargs) -> list[dict]:
        """Score episodes under ``skill_content``; write artifacts under ``out_dir``.

        Each returned row must include ``id``, binary ``hard``, and ``soft`` in
        ``[0, 1]``. Extra environment-private keys are allowed.
        """

    @abstractmethod
    def build_train_env(self, batch_size: int, seed: int, **kwargs):
        """Materialize the train-side env manager (or item list) for one batch."""

    @abstractmethod
    def build_eval_env(self, env_num: int, split: str, seed: int, **kwargs):
        """Materialize an eval-side env manager sized to ``env_num`` on ``split``."""

    def build_reference_text(self, item: dict) -> str:
        """Hidden reference blob used during reflection, if present."""
        return str(item.get("reference_text") or "").strip()

    def get_reference_metadata(self, item: dict) -> dict:
        """Compact preview metadata for hidden reference material."""
        text = self.build_reference_text(item)
        if not text:
            return {"fields": [], "preview": ""}
        return {"fields": ["reference_text"], "preview": text[:_PREVIEW_CHARS]}

    def attach_reference_context(
        self,
        results: list[dict],
        items: list[dict] | None,
    ) -> list[dict]:
        """Stamp matching item reference text onto each rollout row."""
        if not results or not items:
            return list(results or [])

        by_id = _index_by_id(items)
        stamped: list[dict] = []
        for row in results:
            copy = dict(row)
            key = _as_id(row.get("id"))
            source = by_id.get(key) if key is not None else None
            if source is not None:
                blob = self.build_reference_text(source)
                if blob:
                    copy["reference_text"] = blob
            stamped.append(copy)
        return stamped

    def select_representative_items(
        self,
        results: list[dict],
        items: list[dict] | None,
        *,
        n_failures: int,
        n_successes: int,
        seed: int | None = None,
    ) -> list[dict]:
        """Pick a compact, type-diverse mix of failures then successes."""
        if not items:
            return []

        lookup = _index_by_id(items)
        rng = random.Random(seed)
        fails = _pair_by_outcome(results, lookup, want_hard=False)
        wins = _pair_by_outcome(results, lookup, want_hard=True)

        picked = _sample_diverse(fails, n_failures, rng)
        already = {str(item["id"]) for item in picked}
        for item in _sample_diverse(wins, n_successes, rng):
            if str(item["id"]) not in already:
                picked.append(item)
                already.add(str(item["id"]))
        return picked

    def build_env_from_batch(self, batch: BatchSpec, **kwargs):
        """Route a :class:`BatchSpec` to the matching train or eval builder."""
        phase = batch.phase
        size = batch.batch_size
        seed = batch.seed
        if phase != "train":
            return self.build_eval_env(
                env_num=size,
                split=batch.split,
                seed=seed,
                **kwargs,
            )
        return self.build_train_env(batch_size=size, seed=seed, **kwargs)

    def reflect(
        self,
        results: list[dict],
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict | None]:
        """Convert rollout rows into analyst patches via shared minibatch reflect.

        Override only when an environment needs a custom reflection pipeline.
        Callers drop ``None`` entries from the returned list.
        """
        from openjiuwen.agent_evolving.skill_train.reflect import ReflectRequest, run_minibatch_reflect

        return run_minibatch_reflect(
            ReflectRequest(
                results=results,
                skill_content=skill_content,
                prediction_dir=_resolve_prediction_dir(out_dir, kwargs.get("prediction_dir")),
                patches_dir=_patches_root(out_dir, kwargs.get("patches_dir")),
                workers=self.analyst_workers,
                failure_only=self.failure_only,
                minibatch_size=self.minibatch_size,
                edit_budget=self.edit_budget,
                random_seed=kwargs.get("random_seed"),
                error_system=self.get_error_minibatch_prompt(),
                success_system=self.get_success_minibatch_prompt(),
                step_buffer_context=str(kwargs.get("step_buffer_context") or ""),
                meta_skill_context=str(kwargs.get("meta_skill_context") or ""),
                update_mode=getattr(self, "_cfg", {}).get("skill_update_mode", "patch"),
            )
        )

    @property
    def _env_name(self) -> str:
        """Env package segment after ``envs`` in this adapter's module path."""
        segments = type(self).__module__.split(".")
        try:
            env_idx = segments.index("envs")
        except ValueError:
            return ""
        next_idx = env_idx + 1
        return segments[next_idx] if next_idx < len(segments) else ""

    def _load_env_prompt(self, name: str) -> str | None:
        """Load ``name`` preferring env-local prompts; missing files yield ``None``."""
        try:
            return load_prompt(name, env=self._env_name)
        except FileNotFoundError:
            return None

    def _update_mode(self) -> str:
        return getattr(self, "_cfg", {}).get("skill_update_mode", "patch")

    def get_error_minibatch_prompt(self) -> str | None:
        return _prompt_for_mode(
            self._load_env_prompt,
            self._update_mode(),
            _ERROR_PROMPT_BY_MODE,
            "analyst_error",
        )

    def get_success_minibatch_prompt(self) -> str | None:
        return _prompt_for_mode(
            self._load_env_prompt,
            self._update_mode(),
            _SUCCESS_PROMPT_BY_MODE,
            "analyst_success",
        )
