# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public package lifecycle events shared by every extension host.

``package.install`` already drives agent-core's Registry. This module extends
that same event with two host-level contracts:

- ``HostPackageInstallRequest``: ask one host process to load a member package
  it owns. Hosts that use agent-core's Registry keep sending ``InstallRequest``
  instead; both shapes travel on ``package.install``.
- ``PackageInstallOutcome``: the result carried by
  ``package.install_finish`` / ``package.install_failed``.

Hosts register callbacks per bus. The event names and payloads are owned here
so the Gateway and AgentServer share one contract without importing each other.
"""

from __future__ import annotations

import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HostPackageInstallRequest:
    """Input of ``package.install`` when a host loads one of its own members."""

    package_id: str
    version: str
    operation: str = "install"
    container_id: str = ""
    container_version: str = ""
    event_id: str = ""


@dataclass(frozen=True, slots=True)
class PackageInstallOutcome:
    """Result published as ``package.install_finish`` or ``..._failed``."""

    event_id: str
    operation: str
    package_id: str
    version: str
    status: str
    active: bool
    enabled: bool
    error: str = ""
    container_id: str = ""
    container_version: str = ""


HostInstallHandler = Callable[[HostPackageInstallRequest], Awaitable[Any]]
HostOutcomeHandler = Callable[[PackageInstallOutcome], Awaitable[Any]]

_INSTALL_HANDLERS: "weakref.WeakKeyDictionary[Any, HostInstallHandler]" = weakref.WeakKeyDictionary()
_OUTCOME_HANDLERS: "weakref.WeakKeyDictionary[Any, HostOutcomeHandler]" = weakref.WeakKeyDictionary()


def register_host_package_lifecycle(
    bus: Any,
    *,
    install_handler: HostInstallHandler,
    outcome_handler: HostOutcomeHandler,
) -> None:
    """Register the host callbacks used by the shared package events."""

    _INSTALL_HANDLERS[bus] = install_handler
    _OUTCOME_HANDLERS[bus] = outcome_handler


def host_install_handler(bus: Any) -> HostInstallHandler | None:
    return _INSTALL_HANDLERS.get(bus)


def host_outcome_handler(bus: Any) -> HostOutcomeHandler | None:
    return _OUTCOME_HANDLERS.get(bus)
