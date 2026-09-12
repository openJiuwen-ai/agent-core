# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task-local identity of the agent execution subject currently running."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class ExecutionSubject:
    """Stable identity and lineage for one concrete agent execution."""

    subject_id: str
    display_name: str
    kind: str
    parent_subject_id: str = ""
    session_id: str = ""


_CURRENT_EXECUTION_SUBJECT: ContextVar[ExecutionSubject | None] = ContextVar(
    "openjiuwen_current_execution_subject",
    default=None,
)


def current_execution_subject() -> ExecutionSubject | None:
    """Return the subject bound to the current async execution context."""
    return _CURRENT_EXECUTION_SUBJECT.get()


@contextmanager
def execution_subject_scope(subject: ExecutionSubject) -> Iterator[None]:
    """Bind one execution subject without leaking across concurrent tasks."""
    token = _CURRENT_EXECUTION_SUBJECT.set(subject)
    try:
        yield
    finally:
        _CURRENT_EXECUTION_SUBJECT.reset(token)


__all__ = [
    "ExecutionSubject",
    "current_execution_subject",
    "execution_subject_scope",
]
