# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pluggable rollout/eval hooks for the online training scheduler."""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RolloutRequest:
    """Input passed from scheduler to a user-provided rollouter."""

    user_id: str
    samples: list[dict[str, Any]]
    prompts: list[Any]
    training_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RolloutResult:
    """Result returned by a rollouter.

    ``trajectories`` is intentionally not merged into the training batch yet.
    The current release only wires the control flow and logs the returned
    trajectory count.
    """

    success: bool = True
    trajectories: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class EvalRequest:
    """Input passed from PPO executor to a user-provided evaler."""

    user_id: str
    lora_id: str
    lora_version: str
    lora_path: str
    base_model_path: str
    samples: list[dict[str, Any]]
    training_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    """Result returned by an evaler."""

    passed: bool = True
    score: float | None = None
    target_score: float | None = None
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


def load_plugin(spec: str | None) -> Any | None:
    """Load ``module:attr`` or ``module.attr`` and instantiate classes."""
    if not spec:
        return None
    normalized = spec.strip()
    if not normalized:
        return None

    if ":" in normalized:
        module_name, attr_name = normalized.split(":", 1)
    else:
        module_name, _, attr_name = normalized.rpartition(".")
    if not module_name or not attr_name:
        raise ValueError(f"Invalid plugin spec: {spec!r}")

    module = importlib.import_module(module_name)
    plugin = getattr(module, attr_name)
    if inspect.isclass(plugin):
        return plugin()
    return plugin


async def call_rollouter(plugin: Any, request: RolloutRequest) -> RolloutResult:
    target = getattr(plugin, "rollout", plugin)
    value = target(request)
    if inspect.isawaitable(value):
        value = await value
    return coerce_rollout_result(value)


async def call_evaler(plugin: Any, request: EvalRequest) -> EvalResult:
    target = getattr(plugin, "evaluate", plugin)
    value = target(request)
    if inspect.isawaitable(value):
        value = await value
    return coerce_eval_result(value)


def coerce_rollout_result(value: Any) -> RolloutResult:
    if isinstance(value, RolloutResult):
        return value
    if value is None:
        return RolloutResult()
    if isinstance(value, list):
        return RolloutResult(trajectories=value)
    if isinstance(value, dict):
        return RolloutResult(
            success=bool(value.get("success", True)),
            trajectories=list(value.get("trajectories") or []),
            metrics=dict(value.get("metrics") or {}),
            reason=str(value.get("reason") or ""),
        )
    return RolloutResult(success=bool(value))


def coerce_eval_result(value: Any) -> EvalResult:
    if isinstance(value, EvalResult):
        return value
    if value is None:
        return EvalResult()
    if isinstance(value, bool):
        return EvalResult(passed=value)
    if isinstance(value, dict):
        score = value.get("score")
        target_score = value.get("target_score")
        return EvalResult(
            passed=bool(value.get("passed", True)),
            score=float(score) if score is not None else None,
            target_score=float(target_score) if target_score is not None else None,
            reason=str(value.get("reason") or ""),
            metrics=dict(value.get("metrics") or {}),
        )
    return EvalResult(passed=bool(value))
