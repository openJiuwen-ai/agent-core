# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Correlation scope for model calls made by context compression processors."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_current_operation_id: ContextVar[str] = ContextVar(
    "context_compression_operation_id",
    default="",
)


@contextmanager
def context_compression_operation(operation_id: str) -> Iterator[None]:
    """Bind one real processor lifecycle operation to its nested model calls."""
    token = _current_operation_id.set(str(operation_id or ""))
    try:
        yield
    finally:
        _current_operation_id.reset(token)


def current_context_compression_operation_id() -> str:
    """Return the active compression operation id, or an empty string."""
    return _current_operation_id.get()


def stamp_context_compression_model_kwargs(kwargs: dict) -> None:
    """Add callback-only compaction correlation inside a real processor scope."""
    operation_id = current_context_compression_operation_id()
    if not operation_id:
        return
    kwargs["request_purpose"] = "compaction"
    kwargs["context_operation_id"] = operation_id
