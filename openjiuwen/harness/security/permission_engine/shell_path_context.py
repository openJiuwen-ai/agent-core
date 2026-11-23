# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Invocation-scoped path evidence for native shell permission checks."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openjiuwen.harness.security.permission_engine.fileguard.shell_paths import ShellPathAnalysis
    from openjiuwen.harness.tools.shell.bash._tool import BashTool

__all__ = ["current_shell_path_analysis", "shell_path_call"]


def _current_task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class _ShellPathScope:
    def __init__(self) -> None:
        self._task = _current_task()
        self._closed = False
        self._bound = False

    def bind(self, tool: BashTool, args: dict[str, Any]) -> None:
        """Bind the trusted native tool after its execution arguments are written back."""
        scope, _ = _CURRENT.get()
        if self._closed or scope is not self or self._task is not _current_task():
            raise RuntimeError("Shell paths require the active permission scope")
        if self._bound:
            raise RuntimeError("Shell paths are already bound in this permission scope")
        self._bound = True
        try:
            analysis = tool.analyze_local_paths(args)
        except (OSError, RuntimeError, ValueError):
            return
        _CURRENT.set((self, analysis))


_CURRENT: ContextVar[tuple[_ShellPathScope | None, ShellPathAnalysis | None]] = ContextVar(
    "shell_path_scope", default=(None, None),
)


@contextmanager
def shell_path_call() -> Iterator[_ShellPathScope]:
    """Isolate path evidence for one permission attempt."""
    scope = _ShellPathScope()
    token = _CURRENT.set((scope, None))
    try:
        yield scope
    finally:
        scope._closed = True
        _CURRENT.reset(token)


def current_shell_path_analysis(tool_name: str, args: Mapping[str, Any]) -> ShellPathAnalysis | None:
    """Return the current evidence for matching native shell arguments."""
    scope, result = _CURRENT.get()
    if (
        scope is not None and not scope._closed and scope._task is _current_task() and result is not None
    ):
        if tool_name == "bash" and result.matches(args):
            return result
    return None
