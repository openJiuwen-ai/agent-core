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

    def is_owned(self) -> bool:
        """Check that the current task still owns this open scope."""
        return not self._closed and self._task is _current_task()

    def close(self) -> None:
        """End evidence access for this scope."""
        self._closed = True

    def bind(self, tool: BashTool, args: dict[str, Any]) -> None:
        """Bind the trusted native tool after its execution arguments are written back."""
        scope, _ = _CURRENT.get()
        if scope is not self or not self.is_owned():
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
        scope.close()
        _CURRENT.reset(token)


def current_shell_path_analysis(tool_name: str, args: Mapping[str, Any]) -> ShellPathAnalysis | None:
    """Return the current evidence for matching native shell arguments."""
    scope, result = _CURRENT.get()
    if tool_name != "bash" or scope is None or result is None:
        return None
    if scope.is_owned() and result.matches(args):
        return result
    return None
