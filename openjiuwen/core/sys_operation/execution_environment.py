# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable tool-local process environment overlays.

Only overlays cross sandbox boundaries; the execution host owns its base
environment. A null value removes an inherited variable.
"""

import os
import re

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType


def _freeze(values: Mapping[str, str | None]) -> Mapping[str, str | None]:
    for key, value in values.items():
        if not isinstance(key, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None:
            raise ValueError("Invalid execution environment variable name")
        if value is not None and (not isinstance(value, str) or "\0" in value):
            raise ValueError("Execution environment values must be strings or null")
    return MappingProxyType(dict(values))


@dataclass(frozen=True)
class ExecutionEnvironment:
    """Environment defaults and policy overrides for one tool invocation."""

    values: Mapping[str, str | None] = field(default_factory=dict, repr=False)
    overrides: Mapping[str, str | None] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _freeze(self.values))
        object.__setattr__(self, "overrides", _freeze(self.overrides))

    def merge(self, explicit: Mapping[str, str | None] | None = None) -> dict[str, str | None]:
        """Merge layers without assuming the execution host's case rules.

        Reinsert overwritten keys so iteration order preserves layer precedence
        when a Windows execution host later folds differently cased names.
        """
        result = {}
        for layer in (self.values, explicit or {}, self.overrides):
            for key, value in _freeze(layer).items():
                result.pop(key, None)
                result[key] = value
        return result


_CURRENT: ContextVar[ExecutionEnvironment] = ContextVar(
    "execution_environment", default=ExecutionEnvironment(),
)


@contextmanager
def execution_environment_scope(environment: ExecutionEnvironment) -> Iterator[None]:
    """Bind an immutable snapshot and restore it even on cancellation."""
    token = _CURRENT.set(environment)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_execution_environment() -> ExecutionEnvironment:
    """Return this tool's environment snapshot."""
    return _CURRENT.get()


def execution_environment_overlay(
    explicit: Mapping[str, str | None] | None = None,
) -> dict[str, str | None]:
    """Resolve an overlay without copying host environment variables."""
    return _CURRENT.get().merge(explicit)


def apply_environment_overlay(
    base: Mapping[str, str], overlay: Mapping[str, str | None],
) -> dict[str, str]:
    """Apply values and removals to a copy of the execution host environment."""
    result = {key.upper() if os.name == "nt" else key: value for key, value in base.items()}
    for key, value in _freeze(overlay).items():
        key = key.upper() if os.name == "nt" else key
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result
