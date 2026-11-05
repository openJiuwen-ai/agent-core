# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""State dependencies for opaque host targets and retriable change delivery."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.extension.data.json_values import copy_json
from openjiuwen.core.extension.data.state_models import StateAddress, StateSnapshot

StateChangeListener = Callable[[StateAddress, int], None]


class StateChanges:
    """Publish committed changes without converting listener failures to write failures."""

    def __init__(self) -> None:
        self._listeners: set[StateChangeListener] = set()
        self._pending: dict[tuple[StateChangeListener, StateAddress], int] = {}

    def subscribe(self, listener: StateChangeListener) -> Callable[[], None]:
        """Return an idempotent release function for a synchronous enqueue callback."""
        self._listeners.add(listener)

        def unsubscribe() -> None:
            self._listeners.discard(listener)
            for key in tuple(self._pending):
                if key[0] == listener:
                    del self._pending[key]

        return unsubscribe

    def publish(self, address: StateAddress, version: int) -> None:
        """Remember failed notifications after the file lock has been released."""
        for listener in tuple(self._listeners):
            self._pending[listener, address] = version
        self.retry_pending()

    def retry_pending(self) -> None:
        """Retry enqueue operations; no listener is awaited under a storage lock."""
        for (listener, address), version in tuple(self._pending.items()):
            try:
                listener(address, version)
            except Exception:
                logger.exception("extension state change listener failed for package %s", address.package)
            else:
                if self._pending.get((listener, address)) == version:
                    self._pending.pop((listener, address), None)

    def drop_package(self, package_id: str) -> None:
        """Release notifications for data explicitly removed by its owner."""
        for key in tuple(self._pending):
            if key[1].package == package_id:
                del self._pending[key]


@dataclass(slots=True)
class _Build:
    target_key: str
    snapshots: dict[StateAddress, StateSnapshot] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class StateDependencies:
    """Collect fixed address snapshots per build, committing only delivered targets."""

    def __init__(self) -> None:
        self._builds: dict[str, _Build] = {}
        self._targets: dict[str, dict[StateAddress, int]] = {}
        self._current: ContextVar[str | None] = ContextVar("state_build", default=None)

    @property
    def current_build(self) -> str | None:
        """Return the call-local build identity, including an invalidated scope."""
        return self._current.get()

    @contextmanager
    def collect(self, target_key: str) -> Iterator[str]:
        """Collect nested state reads until the host finishes this build."""
        render_id = self.begin(target_key)
        token = self._current.set(render_id)
        try:
            yield render_id
        finally:
            self._current.reset(token)
            self.end(render_id)

    def begin(self, target_key: str) -> str:
        """Start one host build without replacing the target's last good dependencies."""
        if not isinstance(target_key, str) or not target_key:
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="target_key must be a non-empty string")
        render_id = uuid.uuid4().hex
        self._builds[render_id] = _Build(target_key)
        return render_id

    async def read(
        self,
        render_id: str,
        address: StateAddress,
        load: Callable[[], Awaitable[StateSnapshot]],
    ) -> StateSnapshot:
        """Fix the first read of an address, also for concurrent reads in one build."""
        build = self._require_build(render_id)
        async with build.lock:
            self._require_build(render_id)
            if address not in build.snapshots:
                snapshot = await load()
                self._require_build(render_id)
                build.snapshots[address] = snapshot
            snapshot = build.snapshots[address]
            return StateSnapshot(snapshot.exists, copy_json(snapshot.value), snapshot.version)

    def reads(self, render_id: str) -> Mapping[StateAddress, int]:
        """Copy this build's observed versions for a post-delivery recheck."""
        return {address: snapshot.version for address, snapshot in self._require_build(render_id).snapshots.items()}

    def commit(self, render_id: str, target_key: str) -> None:
        """Replace dependencies only when the host confirms the exact delivered target."""
        build = self._require_build(render_id)
        if build.target_key != target_key:
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="delivered target differs from build target")
        self._targets[target_key] = dict(self.reads(render_id))

    def end(self, render_id: str) -> None:
        """Release a temporary build without changing committed dependencies."""
        self._builds.pop(render_id, None)

    def targets(self, address: StateAddress) -> tuple[str, ...]:
        """Return the opaque target keys that depend on this exact address."""
        return tuple(key for key, reads in self._targets.items() if address in reads)

    def target_reads(self) -> dict[str, dict[StateAddress, int]]:
        """Return independent versions for host-managed cross-process polling."""
        return {key: dict(reads) for key, reads in self._targets.items()}

    def drop_target(self, target_key: str) -> None:
        """Invalidate both completed and in-progress work for a withdrawn target."""
        self._targets.pop(target_key, None)
        for render_id, build in tuple(self._builds.items()):
            if build.target_key == target_key:
                self.end(render_id)

    def drop_package(self, package_id: str) -> None:
        """Release dependencies on removed data, including unfinished reads."""
        for reads in self._targets.values():
            for address in tuple(reads):
                if address.package == package_id:
                    del reads[address]
        for render_id, build in tuple(self._builds.items()):
            if any(address.package == package_id for address in build.snapshots):
                self.end(render_id)

    def _require_build(self, render_id: str) -> _Build:
        build = self._builds.get(render_id)
        if build is None:
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="state build has ended or was withdrawn")
        return build
