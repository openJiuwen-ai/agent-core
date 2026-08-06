# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Process-level ownership registry for browser service resources."""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class BrowserServiceIdentity:
    """Stable process-level identity for one logical browser instance."""

    browser_key: str
    profile_name: str
    driver_mode: str
    display_mode: str
    managed_args: tuple[str, ...]
    browser_binary: str
    user_data_dir: str
    cdp_endpoint: str
    server_id: str


@dataclass(frozen=True)
class BrowserServiceRelease:
    """Ownership changes produced when one task-scoped service releases."""

    close_mcp_binding: bool
    next_heartbeat_owner: Optional[Any]


@dataclass(frozen=True)
class BrowserServiceResetSnapshot:
    """Resources that must be stopped for an explicit identity reset."""

    services: tuple[Any, ...]
    managed_drivers: tuple[Any, ...]


@dataclass
class _BrowserServiceEntry:
    services: weakref.WeakSet[Any] = field(default_factory=weakref.WeakSet)
    binding_services: weakref.WeakSet[Any] = field(default_factory=weakref.WeakSet)
    heartbeat_owner: Optional[weakref.ReferenceType[Any]] = None
    managed_drivers: list[Any] = field(default_factory=list)


class BrowserServiceRegistry:
    """Coordinate process resources without sharing task/model state.

    BrowserService objects remain task-scoped. Registry entries only coordinate
    the MCP binding, managed Chrome process handles, and heartbeat ownership for
    services that address the same browser identity.
    """

    def __init__(self) -> None:
        self._entries: dict[BrowserServiceIdentity, _BrowserServiceEntry] = {}
        self._lock = threading.RLock()

    def acquire(self, identity: BrowserServiceIdentity, service: Any) -> None:
        with self._lock:
            entry = self._entries.setdefault(identity, _BrowserServiceEntry())
            entry.services.add(service)

    def activate_binding(self, identity: BrowserServiceIdentity, service: Any) -> bool:
        """Mark an MCP binding active and return whether service owns heartbeat."""
        with self._lock:
            entry = self._entries.setdefault(identity, _BrowserServiceEntry())
            entry.services.add(service)
            entry.binding_services.add(service)
            owner = self._live_owner(entry)
            if owner is None:
                entry.heartbeat_owner = weakref.ref(service)
                owner = service
            return owner is service

    def release(
        self,
        identity: BrowserServiceIdentity,
        service: Any,
    ) -> BrowserServiceRelease:
        """Release a task binding and transfer heartbeat ownership if needed."""
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return BrowserServiceRelease(
                    close_mcp_binding=False,
                    next_heartbeat_owner=None,
                )

            was_binding_service = service in entry.binding_services
            was_owner = self._live_owner(entry) is service
            entry.binding_services.discard(service)
            entry.services.discard(service)

            next_owner = None
            if was_owner:
                next_owner = next(iter(entry.binding_services), None)
                entry.heartbeat_owner = (
                    weakref.ref(next_owner) if next_owner is not None else None
                )

            close_binding = was_binding_service and not entry.binding_services
            if not entry.services:
                # Chrome intentionally outlives the last task binding. Retain
                # only its process handle so an explicit identity-scoped
                # configuration restart can stop it after task collection.
                if not entry.managed_drivers:
                    self._entries.pop(identity, None)

            return BrowserServiceRelease(
                close_mcp_binding=close_binding,
                next_heartbeat_owner=next_owner,
            )

    def register_managed_driver(
        self,
        identity: BrowserServiceIdentity,
        service: Any,
        driver: Any,
    ) -> None:
        with self._lock:
            entry = self._entries.setdefault(identity, _BrowserServiceEntry())
            entry.services.add(service)
            if getattr(driver, "owns_process", False):
                # A new owner means prior handles for this exact identity are
                # stale (for example, the user closed the previous window).
                entry.managed_drivers = [driver]
                return
            if any(
                getattr(existing, "owns_process", False)
                for existing in entry.managed_drivers
            ):
                return
            if all(existing is not driver for existing in entry.managed_drivers):
                entry.managed_drivers.append(driver)

    def unregister_managed_driver(
        self,
        identity: BrowserServiceIdentity,
        driver: Any,
    ) -> None:
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return
            entry.managed_drivers = [
                existing
                for existing in entry.managed_drivers
                if existing is not driver
            ]
            if not entry.services and not entry.managed_drivers:
                self._entries.pop(identity, None)

    def begin_reset(
        self,
        identity: BrowserServiceIdentity,
    ) -> BrowserServiceResetSnapshot:
        """Detach shared process resources for one explicit reset operation."""
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return BrowserServiceResetSnapshot(services=(), managed_drivers=())

            services = tuple(entry.services)
            drivers = list(entry.managed_drivers)
            for service in services:
                driver = getattr(service, "managed_driver", None)
                if driver is not None and all(existing is not driver for existing in drivers):
                    drivers.append(driver)

            entry.binding_services.clear()
            entry.heartbeat_owner = None
            entry.managed_drivers.clear()
            if not entry.services:
                self._entries.pop(identity, None)

            return BrowserServiceResetSnapshot(
                services=services,
                managed_drivers=tuple(drivers),
            )

    def is_heartbeat_owner(
        self,
        identity: BrowserServiceIdentity,
        service: Any,
    ) -> bool:
        with self._lock:
            entry = self._entries.get(identity)
            return entry is not None and self._live_owner(entry) is service

    def discard_idle(self, identity: BrowserServiceIdentity) -> None:
        """Discard registry metadata after an explicit full shutdown."""
        with self._lock:
            entry = self._entries.get(identity)
            if entry is None:
                return
            if not entry.services and not entry.binding_services and not entry.managed_drivers:
                self._entries.pop(identity, None)

    def find_managed_driver_identities(
        self,
        *,
        browser_key: str,
        profile_name: str,
        display_mode: str,
        browser_binary: str,
    ) -> tuple[BrowserServiceIdentity, ...]:
        """Find preserved drivers for one exact logical browser identity."""
        with self._lock:
            matches: list[BrowserServiceIdentity] = []
            for identity, entry in self._entries.items():
                if not entry.managed_drivers:
                    continue
                if not isinstance(identity, BrowserServiceIdentity):
                    continue
                if identity.browser_key != browser_key:
                    continue
                if identity.profile_name != profile_name:
                    continue
                if identity.driver_mode != "managed":
                    continue
                if identity.display_mode != display_mode:
                    continue
                if identity.browser_binary != browser_binary:
                    continue
                matches.append(identity)
            return tuple(matches)

    def clear(self) -> None:
        """Clear metadata for isolated tests after their resources are stopped."""
        with self._lock:
            self._entries.clear()

    @staticmethod
    def _live_owner(entry: _BrowserServiceEntry) -> Optional[Any]:
        owner = entry.heartbeat_owner() if entry.heartbeat_owner is not None else None
        if owner is None or owner not in entry.binding_services:
            entry.heartbeat_owner = None
            return None
        return owner


BROWSER_SERVICE_REGISTRY = BrowserServiceRegistry()


__all__ = [
    "BROWSER_SERVICE_REGISTRY",
    "BrowserServiceIdentity",
    "BrowserServiceRegistry",
    "BrowserServiceRelease",
    "BrowserServiceResetSnapshot",
]
