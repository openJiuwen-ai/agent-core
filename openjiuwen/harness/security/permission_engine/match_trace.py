# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Invocation-local match provenance, independent of authorization decisions."""

from contextvars import ContextVar
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any, Callable, TypeVar, cast

from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionMatch, PermissionResult

_TRACE: ContextVar[list[PermissionMatch] | None] = ContextVar("permission_matches", default=None)
_TARGETS: ContextVar[list[PermissionMatch] | None] = ContextVar("permission_targets", default=None)


def record_target(category: str, target: Any, permission: PermissionLevel, operation: str | None = None) -> None:
    """Record the resolved resource decision after source precedence was applied."""
    targets = _TARGETS.get()
    if targets is not None:
        item = PermissionMatch(category, str(target), permission, "", operation=operation)
        if item not in targets:
            targets.append(item)


def record_match(category: str, target: Any, permission: PermissionLevel, rule_id: Any, source: str | None = None,
                 operation: str | None = None, *, is_default: bool = False) -> None:
    """Record at the matcher, while the rule and its source are still known."""
    trace = _TRACE.get()
    if trace is not None:
        item = PermissionMatch(category, str(target), permission, str(rule_id), source, operation, is_default)
        if item not in trace:
            trace.append(item)


F = TypeVar("F", bound=Callable[..., Any])


def traced_permission(function: F) -> F:
    """Attach a separate trace to each sync/async public engine evaluation."""
    def finish(result: PermissionResult, trace: list[PermissionMatch]) -> PermissionResult:
        result.matches = tuple(dict.fromkeys((*result.matches, *trace)))
        result.targets = tuple(dict.fromkeys((*result.targets, *(_TARGETS.get() or []))))
        return result

    if iscoroutinefunction(function):
        @wraps(function)
        async def async_evaluate(*args, **kwargs):
            trace = []
            token = _TRACE.set(trace)
            target_token = _TARGETS.set([])
            try:
                return finish(await function(*args, **kwargs), trace)
            finally:
                _TRACE.reset(token)
                _TARGETS.reset(target_token)
        return cast(F, async_evaluate)

    @wraps(function)
    def evaluate(*args, **kwargs):
        trace = []
        token = _TRACE.set(trace)
        target_token = _TARGETS.set([])
        try:
            return finish(function(*args, **kwargs), trace)
        finally:
            _TRACE.reset(token)
            _TARGETS.reset(target_token)
    return cast(F, evaluate)
