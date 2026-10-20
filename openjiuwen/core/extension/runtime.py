# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Load extension code into a candidate and remember active package facts.

Publishing handlers to the EventBus is not implemented here. A business
trigger reads the Bus registry snapshot; this module only prepares the
candidate that a later commit would write in one synchronous update.
"""

import hashlib
import importlib.util
import sys
import threading
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.events.types import HandlerLevel
from openjiuwen.core.extension.errors import ExtensionFailure, OperationError, operation_error
from openjiuwen.core.extension.schema.manifest import Manifest
from openjiuwen.core.extension.schema.records import ActivePackageRecord, ExtensionPlan
from openjiuwen.core.extension.selection import evaluate_reload

_ALLOWED_LEVELS = {HandlerLevel.USER.value, HandlerLevel.BUILTIN.value}


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

    Design asks for sequential root management inside one Registry (Q06 still
    open). A re-entrant lock closes the uninstall_decision/drop_active and
    begin_calls/end_calls TOCTOU windows on shared state.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, ActivePackageRecord] = {}
        self._inflight: dict[str, int] = {}
        self._load_serial = 0

    def active_packages(self) -> tuple[ActivePackageRecord, ...]:
        with self._lock:
            return tuple(self._active[key] for key in sorted(self._active))

    def get_active(self, package_id: str) -> ActivePackageRecord | None:
        with self._lock:
            return self._active.get(package_id)

    def record_active(self, record: ActivePackageRecord) -> None:
        """Remember dependency facts after a package has been published.

        This does not write the EventBus registry.
        """
        with self._lock:
            self._active[record.package_id] = record

    def begin_calls(self, package_ids: Sequence[str]) -> None:
        """Count one in-flight use of each package. The event layer will call this."""
        with self._lock:
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
        with self._lock:
            return self._inflight.get(package_id, 0)

    def uninstall_decision(self, package_id: str) -> OperationError | None:
        """Return why uninstall must stop, or None when it may remove the package.

        A busy package is rejected immediately. This method does not wait.
        """
        with self._lock:
            return self._uninstall_decision_unlocked(package_id)

    def drop_active(self, package_id: str) -> None:
        """Remove the active record when uninstall is allowed.

        Raises:
            ExtensionFailure: The package is missing, still required, or busy.
        """
        with self._lock:
            error = self._uninstall_decision_unlocked(package_id)
            if error is not None:
                raise ExtensionFailure(error.status, **error.params)
            del self._active[package_id]

    def check_reload(self, package_id: str, new_manifest: Manifest) -> OperationError | None:
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

    def abort(self, candidate: ActivationCandidate) -> None:
        """Drop a candidate that was not published. Package files stay in place."""
        _release_modules(candidate.module_names)

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
