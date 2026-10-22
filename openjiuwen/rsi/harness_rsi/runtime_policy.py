# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Whitelisted candidate runtime policy under an evaluator-owned ceiling."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

RUNTIME_POLICY_FILE = "runtime.yaml"
RUNTIME_POLICY_KEYS = frozenset({"max_iterations", "on_max_iterations"})
ITERATION_LIMIT_STRATEGIES = ("error", "return_observations")


@dataclass(frozen=True, slots=True)
class HarnessRuntimePolicy:
    """Effective configuration; the host ceiling cannot be changed by a candidate."""

    max_iterations: int
    on_max_iterations: str
    max_iterations_hard_limit: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_runtime_policy(data: Any, *, hard_limit: int = 300) -> dict[str, Any]:
    """Validate only the two supported package configuration fields."""
    if not isinstance(data, dict):
        raise ValueError("runtime.yaml must contain a mapping")
    unknown = set(data) - RUNTIME_POLICY_KEYS
    if unknown:
        raise ValueError(f"runtime.yaml contains unsupported keys: {sorted(str(key) for key in unknown)}")
    if type(hard_limit) is not int or hard_limit < 1:
        raise ValueError("solver_max_iterations_hard_limit must be a positive integer")
    if "max_iterations" in data:
        value = data["max_iterations"]
        if type(value) is not int or not 1 <= value <= hard_limit:
            raise ValueError(f"runtime.yaml.max_iterations must be an integer between 1 and {hard_limit}")
    if "on_max_iterations" in data and data["on_max_iterations"] not in ITERATION_LIMIT_STRATEGIES:
        raise ValueError(f"runtime.yaml.on_max_iterations must be one of {ITERATION_LIMIT_STRATEGIES}")
    return dict(data)


def read_runtime_policy(
    harness_path: str | Path,
    *,
    default_max_iterations: int = 300,
    default_on_max_iterations: str = "error",
    hard_limit: int = 300,
) -> HarnessRuntimePolicy:
    """Read a package policy without loading plugins, code, or credentials."""
    root = Path(harness_path).expanduser().resolve()
    if root.is_file():
        root = root.parent
    policy_path = root / RUNTIME_POLICY_FILE
    values = {"max_iterations": default_max_iterations, "on_max_iterations": default_on_max_iterations}
    source = "evaluator_default"
    if policy_path.exists():
        if policy_path.is_symlink() or not policy_path.resolve().is_relative_to(root):
            raise ValueError("runtime.yaml must be a package-local regular file")
        if policy_path.stat().st_size > 16384:
            raise ValueError("runtime.yaml exceeds the 16 KiB configuration limit")
        overrides = validate_runtime_policy(yaml.safe_load(policy_path.read_text(encoding="utf-8")), hard_limit=hard_limit)
        values.update(overrides)
        source = RUNTIME_POLICY_FILE
    validate_runtime_policy(values, hard_limit=hard_limit)
    return HarnessRuntimePolicy(values["max_iterations"], values["on_max_iterations"], hard_limit, source)
