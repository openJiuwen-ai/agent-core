# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Load extension code into a candidate and publish it to the EventBus.

``prepare`` only fills a candidate, so a business trigger cannot see half a
package. ``publish`` / ``replace_published`` / ``unpublish`` write that
candidate to the Bus in one synchronous pass and keep the handler ids of each
package, so reload can hot-switch and uninstall can remove exactly its own
registrations.
"""

import asyncio
import functools
import hashlib
import importlib.util
import inspect
import sys
import threading
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.protocol import ExtensionEventBusProtocol
from openjiuwen.core.events.types import HandlerId, HandlerLevel
from openjiuwen.core.extension.errors import ExtensionFailure, OperationError, operation_error
from openjiuwen.core.extension.schema.manifest import Manifest
from openjiuwen.core.extension.schema.records import ActivePackageRecord, ExtensionPlan
from openjiuwen.core.extension.selection import evaluate_reload

_ALLOWED_LEVELS = {HandlerLevel.USER.value, HandlerLevel.BUILTIN.value}
PackageResourceCleanup: TypeAlias = Callable[[str, int, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class HandlerDeclaration:
    """One handler collected during prepare. It is not on the EventBus yet."""

    event_type: str
    handler: Callable[..., Any]
    match_pattern: Mapping[str, object]
    handler_level: str
    local_id: str


@dataclass(frozen=True, slots=True)
class ActivationCandidate:
    """A fully loaded package that can later be published together."""

    plan_id: str
    package_id: str
    generation: int
    module_names: tuple[str, ...]
    declarations: tuple[HandlerDeclaration, ...]


@dataclass(frozen=True, slots=True)
class PrepareResult:
    """Either a complete candidate or the reason loading stopped."""

    candidate: ActivationCandidate | None
    error: OperationError | None


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Handler ids written to the EventBus, or why the batch was dropped."""

    handler_ids: tuple[HandlerId, ...]
    error: OperationError | None


class ExtensionRegistrar:
    """The only registration surface an extension ``register`` method receives.

    USER and BUILTIN are accepted. CORE and any other level are rejected.
    The returned id is local to this candidate. The EventBus assigns the real
    handler id when the candidate is published.
    """

    def __init__(self) -> None:
        self.declarations: list[HandlerDeclaration] = []

    def register_event_handler(
        self,
        event_type: object,
        handler: Callable[..., Any],
        match_pattern: Mapping[str, object],
        handler_level: object = "user",
    ) -> str:
        """Collect a USER or BUILTIN declaration without publishing it yet."""
        level = _level_name(handler_level)
        if level not in _ALLOWED_LEVELS:
            raise ExtensionFailure(
                StatusCode.EXTENSION_HANDLER_LEVEL_FORBIDDEN,
                reason=(
                    f"extensions may register user or builtin, got {level!r}"
                ),
            )
        if not callable(handler):
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason="handler must be callable",
            )
        if not isinstance(match_pattern, Mapping):
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason="match_pattern must be a mapping",
            )
        local_id = f"local-{len(self.declarations) + 1}"
        self.declarations.append(
            HandlerDeclaration(
                event_type=_event_name(event_type),
                handler=handler,
                match_pattern=dict(match_pattern),
                handler_level=level,
                local_id=local_id,
            )
        )
        return local_id


class Runtime:
    """Active-package facts, in-flight counts, and candidate loading.

    Package management uses one asynchronous lock per package. A re-entrant
    lock protects call accounting and synchronous publication changes.
    """

    def __init__(self, *, cleanup_callbacks: Sequence[PackageResourceCleanup] = ()) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, ActivePackageRecord] = {}
        self._inflight: dict[str, int] = {}
        self._published: dict[str, tuple[HandlerId, ...]] = {}
        self._load_serial = 0
        self._cleanup_callbacks = tuple(cleanup_callbacks)
        self._blocked: set[str] = set()
        self._management_locks: dict[str, asyncio.Lock] = {}
        self._uninstall_versions: dict[str, tuple[str, ...]] = {}
        self._active_ops: set[EventHandlerOps] = set()

    def package_lock(self, package_id: str) -> asyncio.Lock:
        """Return the management lock for one package on this Runtime's loop."""
        return self._management_locks.setdefault(package_id, asyncio.Lock())

    def uninstall_versions(self, package_id: str, versions: Sequence[str] = ()) -> tuple[str, ...]:
        """Remember verified versions or return those retained for cleanup retry."""
        with self._lock:
            remembered = self._uninstall_versions.get(package_id, ())
            versions = tuple(dict.fromkeys((*remembered, *versions)))
            if versions:
                self._uninstall_versions[package_id] = versions
            return versions

    def stop_package_calls(self, package_id: str) -> None:
        """Stop new uses before cleaning an inactive package's resources."""
        with self._lock:
            if self._inflight.get(package_id, 0):
                raise ExtensionFailure(StatusCode.EXTENSION_PACKAGE_BUSY, package_id=package_id, reason="calls remain")
            self._blocked.add(package_id)

    def _finish_uninstall(self, package_id: str) -> None:
        """Release pending removal facts after every cleanup step succeeds."""
        with self._lock:
            self._uninstall_versions.pop(package_id, None)
            self._blocked.discard(package_id)

    async def cleanup_package_resources(self, package_id: str, generation: int, *, reason: str) -> None:
        """Await host callbacks followed by Core data cleanup in configured order.

        Callers stop resource users first. Callbacks must be idempotent;
        failures propagate so uninstall can retain its record for retry.
        """
        if reason not in ("uninstall", "reload", "shutdown"):
            raise ExtensionFailure(StatusCode.EXTENSION_LOAD_FAILED, reason="invalid cleanup reason")
        for callback in self._cleanup_callbacks:
            await callback(package_id, generation, reason)

    def active_packages(self) -> tuple[ActivePackageRecord, ...]:
        """Return active records sorted by package identifier."""
        with self._lock:
            return tuple(self._active[key] for key in sorted(self._active))

    def get_active(self, package_id: str) -> ActivePackageRecord | None:
        """Return the active record, including an unfinished uninstall record."""
        with self._lock:
            return self._active.get(package_id)

    def record_active(self, record: ActivePackageRecord) -> None:
        """Remember dependency facts after a package has been published.

        This does not write the EventBus registry.
        """
        with self._lock:
            self._active[record.package_id] = record

    def begin_calls(self, package_ids: Sequence[str], ops: EventHandlerOps | None = None) -> None:
        """Count one in-flight use of each package. The event layer will call this."""
        with self._lock:
            if ops is not None and ops not in self._active_ops:
                raise ExtensionFailure(StatusCode.EVENTS_OPS_INVALID, reason="the package handler has already finished")
            for package_id in package_ids:
                if package_id in self._blocked:
                    raise ExtensionFailure(
                        StatusCode.EXTENSION_PACKAGE_BUSY,
                        package_id=package_id,
                        reason="package resources are being removed",
                    )
            for package_id in package_ids:
                self._inflight[package_id] = self._inflight.get(package_id, 0) + 1

    def end_calls(self, package_ids: Sequence[str]) -> None:
        """Release one in-flight use recorded by ``begin_calls``."""
        with self._lock:
            requested: dict[str, int] = {}
            for package_id in package_ids:
                requested[package_id] = requested.get(package_id, 0) + 1
            for package_id, need in requested.items():
                current = self._inflight.get(package_id, 0)
                if current < need:
                    raise ExtensionFailure(
                        StatusCode.EXTENSION_INFLIGHT_MISMATCH,
                        package_id=package_id,
                        reason="no matching in-flight call to end",
                    )
            for package_id, need in requested.items():
                remaining = self._inflight[package_id] - need
                if remaining == 0:
                    self._inflight.pop(package_id, None)
                else:
                    self._inflight[package_id] = remaining

    def inflight_count(self, package_id: str) -> int:
        """Return the number of handlers and nested store calls still using a package."""
        with self._lock:
            return self._inflight.get(package_id, 0)

    def uninstall_decision(self, package_id: str) -> OperationError | None:
        """Return why uninstall must stop, or None when it may remove the package.

        A busy package is rejected immediately. This method does not wait.
        """
        with self._lock:
            return self._uninstall_decision_unlocked(package_id)

    def drop_active(self, package_id: str, *, missing_ok: bool = False) -> None:
        """Remove the active record when uninstall is allowed.

        Raises:
            ExtensionFailure: The package is missing, still required, or busy.
        """
        with self._lock:
            if missing_ok and package_id not in self._active:
                self._finish_uninstall(package_id)
                return
            error = self._uninstall_decision_unlocked(package_id)
            if error is not None:
                raise ExtensionFailure(error.status, **error.params)
            del self._active[package_id]
            self._finish_uninstall(package_id)

    def check_reload(self, package_id: str, new_manifest: Manifest) -> OperationError | None:
        """Check dependency compatibility before preparing a replacement."""
        if self.uninstall_versions(package_id):
            return operation_error(
                StatusCode.EXTENSION_PACKAGE_BUSY,
                package_id=package_id,
                reason="an unfinished uninstall must be retried first",
            )
        return evaluate_reload(self.active_packages(), package_id, new_manifest)

    def prepare(self, plan: ExtensionPlan) -> PrepareResult:
        """Load the plan's extensions and collect handler declarations.

        Every extension must succeed. On failure the modules loaded for this
        attempt are removed from ``sys.modules``. Nothing is written to the
        EventBus. Each attempt uses a new module name, so a later load does
        not reuse the previous module object.
        """
        with self._lock:
            self._load_serial += 1
            serial = self._load_serial
        loaded: list[str] = []
        registrar = ExtensionRegistrar()
        try:
            if plan.package.root is None:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_LOAD_FAILED,
                    reason=f"package {plan.package.package_id} has no root",
                )
            package_root = plan.package.root.resolve()
            for extension in plan.extensions:
                module_name = _module_name(
                    plan.package.package_id,
                    plan.target_generation,
                    extension.id,
                    serial,
                )
                entry_name, created = _load_entry(module_name, package_root, extension.module)
                loaded.extend(created)
                _call_register(entry_name, registrar)
        except ExtensionFailure as exc:
            _release_modules(loaded)
            return PrepareResult(candidate=None, error=exc.as_error())
        except Exception as exc:
            logger.exception(
                "extension prepare failed for package %s",
                plan.package.package_id,
            )
            _release_modules(loaded)
            return PrepareResult(
                candidate=None,
                error=operation_error(
                    StatusCode.EXTENSION_LOAD_FAILED,
                    reason=f"failed to load extension: {exc}",
                ),
            )
        return PrepareResult(
            candidate=ActivationCandidate(
                plan_id=plan.plan_id,
                package_id=plan.package.package_id,
                generation=plan.target_generation,
                module_names=tuple(loaded),
                declarations=tuple(registrar.declarations),
            ),
            error=None,
        )

    def _uninstall_decision_unlocked(self, package_id: str) -> OperationError | None:
        if package_id not in self._active:
            return operation_error(
                StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED,
                package_id=package_id,
                reason="package is not active",
            )
        dependents = [
            record.package_id
            for record in self._active.values()
            if record.package_id != package_id
            and any(item.package_id == package_id for item in record.requires_packages)
        ]
        if dependents:
            names = ", ".join(sorted(dependents))
            return operation_error(
                StatusCode.EXTENSION_PACKAGE_IN_USE,
                package_id=package_id,
                dependents=names,
            )
        if self._inflight.get(package_id, 0) > 0:
            return operation_error(
                StatusCode.EXTENSION_PACKAGE_BUSY,
                package_id=package_id,
                reason="in-flight calls remain; uninstall stops without unregistering",
            )
        return None

    def published_handlers(self, package_id: str) -> tuple[HandlerId, ...]:
        """Return the handler ids this Runtime published for one package."""
        with self._lock:
            return self._published.get(package_id, ())

    def publish(
        self,
        candidate: ActivationCandidate,
        plan: ExtensionPlan,
        bus: ExtensionEventBusProtocol,
        *,
        manifest: Manifest | None = None,
    ) -> PublishResult:
        """Write a whole candidate to the EventBus in one synchronous pass.

        Each declaration is registered with the plan's ``PackageMetadata``. If
        one registration fails, the ids already written are unregistered and
        the candidate is aborted, so the Bus never shows half a package. On
        success the package becomes an active record.

        ``manifest`` is optional; when given, the active record keeps the
        declared package dependencies that later installs consult.
        """
        registered, error = self._register_batch(candidate, plan, bus)
        if error is not None:
            self.abort(candidate)
            return PublishResult((), error)
        with self._lock:
            self._published[candidate.package_id] = tuple(registered)
            self.record_active(self._active_record(plan, candidate, manifest))
        return PublishResult(tuple(registered), None)

    def replace_published(
        self,
        candidate: ActivationCandidate,
        plan: ExtensionPlan,
        bus: ExtensionEventBusProtocol,
        *,
        manifest: Manifest | None = None,
    ) -> PublishResult:
        """Hot-switch one package: register the new batch, then drop the old ids.

        A failure only clears the new batch, so the handlers already on the Bus
        keep serving. Calls that started before the switch keep their own
        registry snapshot and therefore the previous code.
        """
        registered, error = self._register_batch(candidate, plan, bus)
        if error is not None:
            self.abort(candidate)
            return PublishResult((), error)
        with self._lock:
            previous = self._published.get(candidate.package_id, ())
            self._published[candidate.package_id] = tuple(registered)
            self.record_active(self._active_record(plan, candidate, manifest))
        _unregister_batch(bus, previous)
        return PublishResult(tuple(registered), None)

    def unpublish(
        self,
        package_id: str,
        bus: ExtensionEventBusProtocol,
        *,
        keep_record: bool = False,
    ) -> None:
        """Stop new calls and remove handlers, optionally retaining the record.

        Raises:
            ExtensionFailure: The package is missing, still required, or busy.
                Failed handler ids and the record remain available for retry.
        """
        with self._lock:
            error = self._uninstall_decision_unlocked(package_id)
            if error is not None:
                raise ExtensionFailure(error.status, **error.params)
            self._blocked.add(package_id)
            handler_ids = self._published.get(package_id, ())
            remaining = list(handler_ids)
            failure: Exception | None = None
            for handler_id in reversed(handler_ids):
                try:
                    bus.unregister_event_handler(handler_id)
                    remaining.remove(handler_id)
                except Exception as exc:
                    failure = exc
            self._published[package_id] = tuple(remaining)
            if failure is not None:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_LOAD_FAILED,
                    cause=failure,
                    reason="could not unregister every package handler; retry uninstall",
                ) from failure
            self._published.pop(package_id, None)
            if not keep_record:
                self.drop_active(package_id)

    def abort(self, candidate: ActivationCandidate) -> None:
        """Drop a candidate that was not published. Package files stay in place.

        Calling this twice for the same candidate is harmless.
        """
        _release_modules(candidate.module_names)

    def _register_batch(
        self,
        candidate: ActivationCandidate,
        plan: ExtensionPlan,
        bus: ExtensionEventBusProtocol,
    ) -> tuple[list[HandlerId], OperationError | None]:
        """Register every declaration, or undo the ids written so far."""
        registered: list[HandlerId] = []
        for declaration in candidate.declarations:
            try:
                handler_id = bus.register_event_handler(
                    declaration.event_type,
                    self._tracked_handler(candidate.package_id, declaration.handler),
                    declaration.match_pattern,
                    HandlerLevel(declaration.handler_level),
                    package_metadata=plan.package,
                )
            except ExtensionFailure as exc:
                _unregister_batch(bus, registered)
                return [], exc.as_error()
            except Exception as exc:
                logger.exception(
                    "failed to publish a handler of package %s",
                    candidate.package_id,
                )
                _unregister_batch(bus, registered)
                return [], operation_error(
                    StatusCode.EXTENSION_LOAD_FAILED,
                    reason=(
                        f"cannot register a handler for {declaration.event_type}: {exc}"
                    ),
                )
            registered.append(handler_id)
        return registered, None

    def _tracked_handler(self, package_id: str, handler: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
        wrapped = _async_handler(handler)

        @functools.wraps(handler)
        async def tracked(event: Any, ops: Any, event_next: Any) -> Any:
            self.begin_calls((package_id,))
            with self._lock:
                self._active_ops.add(ops)
            try:
                return await wrapped(event, ops, event_next)
            finally:
                with self._lock:
                    self._active_ops.discard(ops)
                self.end_calls((package_id,))

        return tracked

    def _active_record(
        self,
        plan: ExtensionPlan,
        candidate: ActivationCandidate,
        manifest: Manifest | None,
    ) -> ActivePackageRecord:
        root = plan.package.root or Path()
        if manifest is not None:
            return self.active_record_for(manifest, root, candidate)
        return ActivePackageRecord(
            package_id=candidate.package_id,
            version=plan.package.package_version or "",
            root=root.resolve(),
            generation=candidate.generation,
            requires_packages=(),
        )

    def active_record_for(
        self,
        manifest: Manifest,
        root: Path,
        candidate: ActivationCandidate,
    ) -> ActivePackageRecord:
        """Build the active record a later install will read. The Bus is unchanged."""
        if candidate.package_id != manifest.package_id:
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason=(
                    f"candidate package_id {candidate.package_id!r} does not match "
                    f"manifest {manifest.package_id!r}"
                ),
            )
        return ActivePackageRecord(
            package_id=manifest.package_id,
            version=manifest.version,
            root=root.resolve(),
            generation=candidate.generation,
            requires_packages=manifest.requires_packages,
        )


def _unregister_batch(
    bus: ExtensionEventBusProtocol,
    handler_ids: Sequence[HandlerId],
) -> None:
    """Remove handler ids in reverse registration order, best effort."""
    for handler_id in reversed(tuple(handler_ids)):
        try:
            bus.unregister_event_handler(handler_id)
        except Exception:
            logger.exception("failed to unregister handler %s", handler_id)


def _async_handler(handler: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
    """Return a handler the EventBus can await.

    Extensions may register plain functions, but the Bus rejects a handler
    whose result is not awaitable. A synchronous callable therefore runs
    inside an async adapter; an async one is published unchanged.
    """
    if inspect.iscoroutinefunction(handler):
        return handler

    @functools.wraps(handler)
    async def adapter(event: Any, ops: Any, event_next: Any) -> Any:
        result = handler(event, ops, event_next)
        if inspect.isawaitable(result):
            return await result
        return result

    return adapter


def _module_name(package_id: str, generation: int, extension_id: str, serial: int) -> str:
    raw = f"{package_id}\n{generation}\n{extension_id}\n{serial}".encode()
    digest = hashlib.sha256(raw).hexdigest()[:24]
    return f"openjiuwen_ext_{digest}"


def _load_entry(root_name: str, package_root: Path, relative_module: str) -> tuple[str, list[str]]:
    """Load the entry file as a member of a fresh package.

    Loading follows the repo pattern in ``runtime_extension_loader`` /
    ``workflow/engine/loader``: ``importlib.util.spec_from_file_location`` plus
    ``exec_module``. The package root is ``package_root``, so
    ``from .handler import x`` in the entry file resolves to the sibling file.
    """
    package_root = package_root.resolve()
    relative = Path(relative_module)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ExtensionFailure(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason=f"entry path is invalid: {relative_module}",
        )
    entry = (package_root / relative).resolve()
    if entry.is_symlink() or not entry.is_file() or not entry.is_relative_to(package_root):
        raise ExtensionFailure(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason=f"cannot load module {entry}",
        )
    parts = relative.with_suffix("").parts
    created: list[str] = []
    try:
        _ensure_package(root_name, package_root, package_root, created)
        parent_parts: list[str] = []
        for part in parts[:-1]:
            parent_parts.append(part)
            parent_name = f"{root_name}.{'.'.join(parent_parts)}"
            _ensure_package(
                parent_name,
                package_root.joinpath(*parent_parts),
                package_root,
                created,
            )
        entry_name = f"{root_name}.{'.'.join(parts)}"
        package_name = root_name if len(parts) == 1 else f"{root_name}.{'.'.join(parts[:-1])}"
        _load_module_from_path(
            entry_name,
            entry,
            package_name=package_name,
            search_locations=None,
            created=created,
        )
    except Exception:
        _release_modules(created)
        raise
    return entry_name, created


def _ensure_package(
    name: str,
    path: Path,
    package_boundary: Path,
    created: list[str],
) -> None:
    if name in created:
        return
    package_path = path.resolve()
    if not package_path.is_relative_to(package_boundary):
        raise ExtensionFailure(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason=f"package path escapes package root: {package_path}",
        )
    init = package_path / "__init__.py"
    if init.exists():
        init = init.resolve()
        if init.is_symlink() or not init.is_file() or not init.is_relative_to(package_boundary):
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason=f"package __init__.py escapes package root: {init}",
            )
        _load_module_from_path(
            name,
            init,
            package_name=name,
            search_locations=[str(package_path)],
            created=created,
        )
        return
    module = types.ModuleType(name)
    module.__path__ = [str(package_path)]
    module.__package__ = name
    sys.modules[name] = module
    _attach_to_parent(name, module)
    created.append(name)


def _load_module_from_path(
    name: str,
    module_path: Path,
    *,
    package_name: str,
    search_locations: list[str] | None,
    created: list[str],
) -> None:
    # Drop stale bytecode so a same-second rewrite is visible on Windows.
    _forget_bytecode(module_path)
    kwargs: dict[str, Any] = {}
    if search_locations is not None:
        kwargs["submodule_search_locations"] = search_locations
    spec = importlib.util.spec_from_file_location(name, module_path, **kwargs)
    if spec is None or spec.loader is None:
        raise ExtensionFailure(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason=f"cannot create import spec for {module_path}",
        )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = package_name
    sys.modules[name] = module
    _attach_to_parent(name, module)
    created.append(name)
    spec.loader.exec_module(module)


def _forget_bytecode(module_path: Path) -> None:
    try:
        cached = Path(importlib.util.cache_from_source(str(module_path)))
    except (NotImplementedError, ValueError, OSError):
        return
    if cached.is_file():
        cached.unlink(missing_ok=True)
    importlib.invalidate_caches()


def _attach_to_parent(name: str, module: types.ModuleType) -> None:
    parent_name, dot, attr = name.rpartition(".")
    if not dot or parent_name not in sys.modules:
        return
    setattr(sys.modules[parent_name], attr, module)


def _call_register(module_name: str, registrar: ExtensionRegistrar) -> None:
    module = sys.modules[module_name]
    factory = getattr(module, "create_extension", None)
    if not callable(factory):
        raise ExtensionFailure(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason=f"{module_name} has no create_extension()",
        )
    extension = factory()
    register = getattr(extension, "register", None)
    if not callable(register):
        raise ExtensionFailure(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason="extension instance has no register method",
        )
    register(registrar)


def _release_modules(module_names: Sequence[str]) -> None:
    for name in module_names:
        sys.modules.pop(name, None)
        prefix = f"{name}."
        for loaded in [key for key in sys.modules if key.startswith(prefix)]:
            sys.modules.pop(loaded, None)


def _level_name(handler_level: object) -> str:
    if isinstance(handler_level, str):
        return handler_level.strip().lower()
    value = getattr(handler_level, "value", None)
    if isinstance(value, str):
        return value.strip().lower()
    return str(handler_level).strip().lower()


def _event_name(event_type: object) -> str:
    if isinstance(event_type, str):
        return event_type
    value = getattr(event_type, "value", None)
    if isinstance(value, str):
        return value
    return str(event_type)
