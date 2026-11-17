# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared value objects and limits for ``ops`` calls; timeouts are milliseconds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

DEFAULT_OPS_TIMEOUT: Final[int] = 30_000
MAX_OPS_TIMEOUT: Final[int] = 600_000


def no_match_pattern(event_input: object, match_pattern: object) -> bool:
    """Accept a request only when the handler declares no match pattern."""

    del event_input
    return not match_pattern


@dataclass(frozen=True, slots=True)
class OpsError:
    """Structured failure reason carried by an ops output."""

    kind: str
    message: str


def resolve_timeout_ms(timeout_ms: int | None, default_ms: int = DEFAULT_OPS_TIMEOUT) -> int:
    """Return the effective budget for one ops call."""

    if timeout_ms is None:
        return default_ms
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        raise TypeError("timeout_ms must be an int or None")
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")
    return min(timeout_ms, MAX_OPS_TIMEOUT)


def timeout_is_valid(timeout_ms: object) -> bool:
    """Return whether a timeout resolves to a positive budget."""

    return timeout_ms is None or (isinstance(timeout_ms, int) and not isinstance(timeout_ms, bool) and timeout_ms > 0)


def is_timeout_error(error: BaseException) -> bool:
    """Return whether an exception or one of its causes is a timeout."""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, TimeoutError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False
