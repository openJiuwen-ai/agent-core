# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Internal request-local capabilities for LLM compatibility behavior."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

_disabled_thinking_fallback_allowed: ContextVar[bool] = ContextVar(
    "_openjiuwen_disabled_thinking_fallback_allowed",
    default=False,
)


def is_disabled_thinking_fallback_allowed() -> bool:
    """Return whether this request may retry without disabled-thinking fields."""
    return _disabled_thinking_fallback_allowed.get()


@contextmanager
def disabled_thinking_fallback_scope() -> Iterator[None]:
    """Allow disabled-thinking compatibility fallback in this request context only."""
    token: Token[bool] = _disabled_thinking_fallback_allowed.set(True)
    try:
        yield
    finally:
        _disabled_thinking_fallback_allowed.reset(token)
