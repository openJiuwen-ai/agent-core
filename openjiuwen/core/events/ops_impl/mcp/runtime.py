# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve the resource manager used by ``mcp.*`` calls."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

ResourceMgrProvider = Callable[[], object | None]

_resource_mgr_provider: ResourceMgrProvider | None = None


def resolve_ability_manager(agent: Any | None) -> Any | None:
    """Return the ability manager that owns the caller's agent tools."""

    return getattr(agent, "ability_manager", None)


def set_resource_mgr_provider(provider: ResourceMgrProvider | None) -> ResourceMgrProvider | None:
    """Install the process-wide resource source; return the previous one."""

    global _resource_mgr_provider
    previous = _resource_mgr_provider
    _resource_mgr_provider = provider
    return previous


def require_resource_mgr() -> object:
    """Return the installed resource manager or the runner's default."""

    if _resource_mgr_provider is not None:
        resource_mgr = _resource_mgr_provider()
    else:
        from openjiuwen.core.runner import Runner

        resource_mgr = Runner.resource_mgr
    if resource_mgr is None:
        raise RuntimeError("no ResourceMgr is installed for ops calls")
    return resource_mgr
