# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve the runtime objects one ``ops`` call uses."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import SessionContext


def resolve_agent(context: SessionContext | None) -> Any | None:
    """Return the agent bound to the caller's session, if it is still alive."""

    if context is None:
        return None
    from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime

    return get_agent_runtime().get(str(context.session_id))


def resolve_sys_operation(agent: Any | None) -> Any | None:
    """Return the filesystem backend configured on the caller's agent."""

    config = getattr(agent, "deep_config", None)
    return getattr(config, "sys_operation", None)


def build_context(ops: EventHandlerOps) -> SessionContext:
    """Copy the session facts one call runs with."""

    base = ops.session_context
    if base is None:
        raise RuntimeError("ops call requires SessionContext")
    if base.cwd:
        return base
    return replace(base, cwd=_current_cwd())


@contextmanager
def agent_working_dir(agent: Any | None, context: SessionContext) -> Iterator[None]:
    """Run one call against the agent's roots, then restore the caller's.

    Event handlers run outside the agent loop, so the per-task ``CwdState`` is
    empty and relative paths would otherwise resolve against the process
    directory, which sits outside the agent sandbox.
    """

    config = getattr(agent, "deep_config", None) if agent is not None else None
    if config is None:
        yield
        return
    workspace = getattr(getattr(config, "workspace", None), "root_path", None)
    cwd_path = getattr(config, "cwd", None) or workspace or context.cwd
    if not cwd_path:
        yield
        return

    from openjiuwen.core.sys_operation import cwd

    previous = (
        cwd.get_cwd(),
        cwd.get_original_cwd(),
        cwd.get_project_root(),
        cwd.get_workspace(),
        cwd.get_team_workspace(),
    )
    cwd.init_cwd(
        cwd_path,
        project_root=getattr(config, "project_root", None) or cwd_path,
        workspace=workspace,
    )
    try:
        yield
    finally:
        cwd.init_cwd(
            previous[0],
            project_root=previous[2],
            workspace=previous[3],
            team_workspace=previous[4],
        )
        cwd.set_original_cwd(previous[1])


def _current_cwd() -> str:
    """Read the per-task working directory used by filesystem tools."""

    from openjiuwen.core.sys_operation.cwd import get_cwd

    return get_cwd()
