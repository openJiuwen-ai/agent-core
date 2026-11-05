# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package installation facts shared by independent local runtimes."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import math
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, TypeVar

import portalocker
import yaml

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension.data.files import run_file_operation, sync_directory
from openjiuwen.core.extension.errors import ExtensionFailure, OperationError
from openjiuwen.core.extension.manifest import has_manifest, load_manifest
from openjiuwen.core.extension.schema.manifest import Manifest
from openjiuwen.core.extension.schema.records import ActivationReport, ActivePackageRecord
from openjiuwen.core.extension.versions import specifier_matches

if TYPE_CHECKING:
    from openjiuwen.core.extension.package_manager import PackageManager
    from openjiuwen.core.extension.selection import StartupInstallPlan

PackageOperation: TypeAlias = Literal["install", "reload", "uninstall"]
_T = TypeVar("_T")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STATES = {"pending", "waiting", "installing", "reloading", "uninstalling", "success", "failed", "unknown"}


@dataclass(frozen=True, slots=True)
class PackageOperationToken:
    """Identity of one admitted operation, never supplied by a network client."""

    run_id: str
    process_id: str
    package_id: str
    operation_id: str


@dataclass(frozen=True, slots=True)
class PackageInstallState:
    """A detached report row; inactive owners never expose an active version."""

    package_id: str
    domain: str | None
    process_id: str | None
    active_version: str | None
    requires: Mapping[str, str]
    status: str
    reason: Mapping[str, str] | None
    pending: Mapping[str, Any] | None

    @property
    def successful(self) -> bool:
        return self.active_version is not None and self.pending is None and self.status in {"success", "failed"}

    @property
    def dependency_version(self) -> str | None:
        if self.successful:
            return self.active_version
        if self.pending and self.status in {"pending", "waiting", "installing", "reloading"}:
            return self.pending["version"]
        return None


def _fail(status: StatusCode, **params: Any) -> None:
    raise ExtensionFailure(status, **params)


def _report_error(reason: str) -> None:
    _fail(StatusCode.EXTENSION_REPORT_FAILED, reason=reason)


def _requirements(manifest: Manifest) -> dict[str, str]:
    return {item.package_id: item.version for item in manifest.requires_packages}


def _is_positive_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


class PackageInstallReport:
    """Own a YAML report, its transaction lock and one process-lifetime lock."""

    def __init__(
        self,
        path: Path,
        *,
        poll_interval: float = 0.5,
        dependency_timeout: float = 300.0,
        lock_timeout: float = 5.0,
    ) -> None:
        for value in (poll_interval, dependency_timeout, lock_timeout):
            if not _is_positive_finite_number(value):
                _report_error("report intervals must be positive finite numbers")
        original = Path(path).expanduser()
        if original.is_symlink():
            _report_error("report must not be a symbolic link")
        self.path = original.resolve()
        self.poll_interval = poll_interval
        self.dependency_timeout = dependency_timeout
        self.lock_timeout = lock_timeout
        self.install_dir: Path | None = None
        self.domain: str | None = None
        self.process_id = uuid.uuid4().hex
        self.run_id: str | None = None
        self._owner_lock: portalocker.Lock | None = None
        self._scope: ContextVar[PackageOperationToken | None] = ContextVar("package_operation", default=None)
        self._tokens: dict[str, PackageOperationToken] = {}
        self._executing: set[str] = set()
        self._abandoned: set[str] = set()

    @property
    def lock_directory(self) -> Path:
        return self.path.with_suffix(".locks")

    def _owner_path(self, domain: str | None) -> Path:
        scope = "all" if domain is None else f"domain:{domain}"
        return self.lock_directory / f"{hashlib.sha256(scope.encode()).hexdigest()}.lock"

    async def _transaction(
        self, change: Callable[[dict[str, Any]], _T], *, write: bool = True,
        timeout: float | None = None,
    ) -> _T:
        def operate() -> _T:
            try:
                with portalocker.Lock(
                    str(self.path) + ".lock", mode="a+b",
                    timeout=self.lock_timeout if timeout is None else timeout,
                    check_interval=min(0.05, self.lock_timeout),
                ):
                    data = self._read()
                    result = change(data)
                    if write:
                        self._write(data)
                    return result
            except portalocker.exceptions.LockException as exc:
                raise ExtensionFailure(StatusCode.EXTENSION_REPORT_FAILED, reason="report lock timeout") from exc
            except (OSError, ValueError, yaml.YAMLError) as exc:
                raise ExtensionFailure(StatusCode.EXTENSION_REPORT_FAILED, reason=f"report I/O failed: {exc}") from exc

        return await run_file_operation(operate)

    def _read(self) -> dict[str, Any]:
        if self.path.is_symlink():
            _report_error("report path is a symbolic link")
        if not self.path.exists():
            return {}
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ExtensionFailure(StatusCode.EXTENSION_REPORT_FAILED, reason=f"cannot read report: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            _report_error("invalid report format; refusing to replace it")
        if not isinstance(data.get("run_id"), str) or not isinstance(data.get("install_dir"), str):
            _report_error("report identity is missing")
        if not isinstance(data.get("processes"), dict) or not isinstance(data.get("packages"), dict):
            _report_error("report processes/packages must be mappings")
        if data.get("startup_order") is not None and not isinstance(data["startup_order"], list):
            _report_error("invalid startup order")
        for owner in data["processes"].values():
            if not isinstance(owner, dict) or owner.get("state") not in {"starting", "running", "stopping"}:
                _report_error("invalid process registration")
            if owner.get("domain") is not None and not isinstance(owner["domain"], str):
                _report_error("invalid process domain")
        for package_id, row in data["packages"].items():
            if not isinstance(package_id, str) or not _ID.fullmatch(package_id) or ".." in package_id:
                _report_error("invalid package id in report")
            if not isinstance(row, dict) or row.get("status") not in _STATES:
                _report_error(f"invalid package row: {package_id}")
            if row.get("domain") is not None and not isinstance(row["domain"], str):
                _report_error("invalid package domain")
            if not isinstance(row.get("requires"), dict):
                _report_error("invalid active dependencies")
            if not {"process_id", "active_version", "reason", "pending"}.issubset(row):
                _report_error("incomplete package row")
            if row["process_id"] is not None and row["process_id"] not in data["processes"]:
                _report_error("package owner is not registered")
            if row["active_version"] is not None and not isinstance(row["active_version"], str):
                _report_error("invalid active version")
            if row["reason"] is not None and (not isinstance(row["reason"], dict)
                    or not all(isinstance(row["reason"].get(key), str) for key in ("code", "message"))):
                _report_error("invalid failure reason")
            pending = row.get("pending")
            if pending is not None:
                if not isinstance(pending, dict) or pending.get("operation") not in {"install", "reload", "uninstall"}:
                    _report_error("invalid pending operation")
                if not isinstance(pending.get("operation_id"), str) or not isinstance(pending.get("version"), str):
                    _report_error("invalid pending operation identity")
                if not isinstance(pending.get("requires"), dict):
                    _report_error("invalid pending dependencies")
        if self.install_dir is not None and Path(data["install_dir"]).resolve() != self.install_dir:
            _report_error("report belongs to another installation directory")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        raw = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".install-report-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            sync_directory(self.path.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _locked(self, path: Path) -> bool:
        if self._owner_lock is not None and path == self._owner_path(self.domain):
            return True
        if path.is_symlink():
            _report_error("owner lock is a symbolic link")
        probe = portalocker.Lock(path, mode="a+b", timeout=0, flags=portalocker.LOCK_EX | portalocker.LOCK_NB)
        try:
            probe.acquire()
        except portalocker.exceptions.AlreadyLocked:
            return True
        except portalocker.exceptions.LockException as exc:
            # portalocker uses LockException for a nonblocking busy lock on Windows.
            if getattr(exc, "errno", None) in {11, 13, 36} or "locking" in str(exc).lower():
                return True
            raise
        else:
            probe.release()
            return False

    def _owner_alive(self, data: dict[str, Any], process_id: str | None) -> bool:
        owner = data["processes"].get(process_id)
        return owner is not None and owner["state"] != "stopping" and self._locked(self._owner_path(owner["domain"]))

    async def open(self, install_dir: Path, *, domain: str | None) -> None:
        if self.run_id is not None:
            _report_error("report service already opened")
        self.install_dir = Path(install_dir).expanduser().resolve()
        self.domain = domain
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_directory.mkdir(parents=True, exist_ok=True)

        def register(data: dict[str, Any]) -> None:
            live = {
                process_id: owner for process_id, owner in data.get("processes", {}).items()
                if self._locked(self._owner_path(owner["domain"]))
            }
            held = any(self._locked(path) for path in self.lock_directory.glob("*.lock"))
            if not data and held:
                _report_error("report missing while an installation owner is alive")
            duplicate_scope = any(
                owner["domain"] == domain or owner["domain"] is None or domain is None
                for owner in live.values()
            )
            if duplicate_scope:
                _fail(
                    StatusCode.EXTENSION_PACKAGE_BUSY,
                    package_id="*",
                    reason="installation scope already has an owner",
                )
            if not live and held:
                _report_error("owner lock has no matching process registration")
            lock = portalocker.Lock(
                self._owner_path(domain), mode="a+b", timeout=0, flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
            )
            lock.acquire()
            self._owner_lock = lock
            if not live:
                data.clear()
                data.update(
                    schema_version=1,
                    run_id=uuid.uuid4().hex,
                    install_dir=str(self.install_dir),
                    startup_order=None,
                    processes={},
                    packages={},
                )
            else:
                data["processes"] = live
                if not any(owner["state"] == "starting" for owner in live.values()):
                    data["startup_order"] = None
                for row in data["packages"].values():
                    if row["domain"] == domain:
                        if row["pending"] is None and row["active_version"] is not None:
                            row["pending"] = {"operation_id": uuid.uuid4().hex, "operation": "install",
                                              "version": row["active_version"], "requires": row["requires"]}
                        row.update(process_id=None, active_version=None, requires={}, status="pending", reason=None)
                    elif row["process_id"] not in live:
                        row["process_id"] = None

            data["processes"][self.process_id] = {"domain": domain, "state": "starting"}
            self.run_id = data["run_id"]

        try:
            await self._transaction(register)
        except BaseException:
            if self._owner_lock is not None:
                self._owner_lock.release()
                self._owner_lock = None
            self.run_id = None
            raise

    def _require_owner(self, data: dict[str, Any]) -> None:
        if self.run_id is None or data.get("run_id") != self.run_id or self.process_id not in data.get("processes", {}):
            _report_error("stale or closed report owner")

    def _states(self, data: dict[str, Any]) -> dict[str, PackageInstallState]:
        rows = {}
        for package_id, row in data["packages"].items():
            alive = self._owner_alive(data, row["process_id"])
            rows[package_id] = PackageInstallState(
                package_id, row["domain"], row["process_id"], row["active_version"] if alive else None,
                copy.deepcopy(row["requires"]), row["status"], copy.deepcopy(row["reason"]),
                copy.deepcopy(row["pending"]),
            )
        return rows

    async def read_packages(self, package_id: str | None = None) -> dict[str, PackageInstallState]:
        def read(data: dict[str, Any]) -> dict[str, PackageInstallState]:
            self._require_owner(data)
            states = self._states(data)
            return states if package_id is None else {key: value for key, value in states.items() if key == package_id}

        return await self._transaction(read, write=False)

    @staticmethod
    def _pending(manifest: Manifest, operation: str) -> dict[str, Any]:
        return {"operation_id": uuid.uuid4().hex, "operation": operation,
                "version": manifest.version, "requires": _requirements(manifest)}

    @classmethod
    def _row(cls, manifest: Manifest) -> dict[str, Any]:
        return {"domain": manifest.domain, "process_id": None, "active_version": None, "requires": {},
                "status": "pending", "reason": None, "pending": cls._pending(manifest, "install")}

    async def prepare_startup(self, package_manager: PackageManager) -> StartupInstallPlan:
        from openjiuwen.core.extension.selection import RejectedPackage, build_startup_plan

        def prepare(data: dict[str, Any]) -> Any:
            self._require_owner(data)
            manifests = []
            errors = []
            for root in package_manager.list_installed():
                try:
                    manifest = load_manifest(root, require_domain=package_manager.require_domain)
                    package_manager.validate_manifest(manifest, match_domain=False)
                    if manifest.package_id != root.parent.name or manifest.version != root.name:
                        _fail(StatusCode.EXTENSION_MANIFEST_INVALID, reason=f"manifest does not match directory {root}")
                    manifests.append(manifest)
                except ExtensionFailure as exc:
                    errors.append(RejectedPackage(root.parent.name, exc.as_error()))
            states = tuple(state for state in self._states(data).values()
                           if self._owner_alive(data, state.process_id))
            if not any(owner["state"] == "starting" and self._owner_alive(data, process_id)
                       for process_id, owner in data["processes"].items()):
                data["startup_order"] = None
            data["processes"][self.process_id]["state"] = "starting"
            order = data["startup_order"]
            if order is not None:
                available = build_startup_plan(manifests, states).ordered
                by_version = {(item.package_id, item.version): item for item in (*manifests, *available)}
                existing = []
                for package_id in order:
                    row = data["packages"].get(package_id)
                    if row is None:
                        _report_error("startup package disappeared from the report")
                    version = row["pending"]["version"] if row["pending"] else row["active_version"]
                    manifest = by_version.get((package_id, version))
                    if version is None and row["status"] == "failed":
                        from openjiuwen.core.extension.versions import parse_version
                        candidates = [item for item in manifests if item.package_id == package_id]
                        manifest = max(candidates, key=lambda item: parse_version(item.version), default=None)
                    if manifest is None:
                        _report_error(f"startup source disappeared: {package_id}/{version}")
                    requires = row["pending"]["requires"] if row["pending"] else row["requires"]
                    domain_changed = manifest.domain != row["domain"]
                    dependencies_changed = version is not None and _requirements(manifest) != requires
                    if domain_changed or dependencies_changed:
                        _report_error(f"startup source changed: {package_id}")
                    existing.append(manifest)
                manifests = existing
            plan = build_startup_plan(manifests, states)
            plan = type(plan)(plan.ordered, (*plan.rejected, *errors))
            selected = {item.package_id: item for item in plan.ordered}
            if order is None:
                data["startup_order"] = list(selected)
            for manifest in plan.ordered:
                row = data["packages"].get(manifest.package_id)
                if row is None:
                    data["packages"][manifest.package_id] = self._row(manifest)
                else:
                    owner_dead = not self._owner_alive(data, row["process_id"])
                    failed_local = row["process_id"] == self.process_id and row["status"] == "failed"
                    inactive_local = row["active_version"] is None and row["pending"] is None
                    if owner_dead or (failed_local and inactive_local):
                        data["packages"][manifest.package_id] = self._row(manifest)
            by_id = {manifest.package_id: manifest for manifest in manifests}
            for rejection in plan.rejected:
                manifest = by_id.get(rejection.package_id)
                if manifest is not None and not self._states(data).get(manifest.package_id, None):
                    data["packages"][manifest.package_id] = self._row(manifest)
                row = data["packages"].get(rejection.package_id)
                if row is not None and row["active_version"] is None:
                    row.update(status="failed", pending=None, reason={"code": rejection.error.code,
                                                                      "message": rejection.error.message})
            return plan

        return await self._transaction(prepare)

    def _check_manifest(self, manifest: Manifest) -> None:
        if self.domain is not None and manifest.domain != self.domain:
            _fail(
                StatusCode.EXTENSION_DOMAIN_MISMATCH,
                reason=f"package {manifest.package_id} belongs to {manifest.domain!r}",
            )

    def _check_dependents(self, data: dict[str, Any], manifest: Manifest, operation: str) -> None:
        if operation == "install":
            return
        for package_id, row in data["packages"].items():
            if package_id == manifest.package_id or not self._owner_alive(data, row["process_id"]):
                continue
            pending = row["pending"]
            if pending and manifest.package_id in pending["requires"]:
                _fail(
                    StatusCode.EXTENSION_PACKAGE_BUSY,
                    package_id=manifest.package_id,
                    reason=f"reserved by {package_id}",
                )
            required = row["requires"].get(manifest.package_id) if row["active_version"] else None
            if required is None:
                continue
            if operation == "uninstall":
                _fail(StatusCode.EXTENSION_PACKAGE_IN_USE, package_id=manifest.package_id, dependents=package_id)
            if not specifier_matches(manifest.version, required):
                _fail(StatusCode.EXTENSION_DEPENDENT_INCOMPATIBLE, package_id=manifest.package_id,
                      dependent=package_id, required=required, version=manifest.version)

    async def begin_operation(
        self, manifest: Manifest, operation: PackageOperation, *, startup: bool = False,
    ) -> PackageOperationToken:
        self._check_manifest(manifest)
        issued: list[PackageOperationToken] = []

        def begin(data: dict[str, Any]) -> PackageOperationToken:
            self._require_owner(data)
            if data["processes"][self.process_id]["state"] == "stopping":
                _report_error("installation owner is stopping")
            startup_running = data["startup_order"] is not None and any(
                owner["state"] == "starting" and self._owner_alive(data, process_id)
                for process_id, owner in data["processes"].items()
            )
            if not startup and startup_running:
                _fail(StatusCode.EXTENSION_PACKAGE_BUSY, package_id=manifest.package_id,
                      reason="startup installation is still running")
            if startup:
                planned = data["packages"].get(manifest.package_id)
                not_selected = manifest.package_id not in (data["startup_order"] or ())
                missing_plan = planned is None or planned["pending"] is None
                plan_changed = not missing_plan and (
                    planned["pending"]["version"] != manifest.version
                    or planned["pending"]["requires"] != _requirements(manifest)
                )
                if not_selected or missing_plan or plan_changed:
                    _report_error("package does not match the published startup plan")
            if operation not in {"install", "reload", "uninstall"}:
                _report_error("unsupported package operation")
            for package_id in data["packages"]:
                if package_id == manifest.package_id:
                    continue
                if os.path.normcase(package_id) == os.path.normcase(manifest.package_id):
                    _fail(StatusCode.EXTENSION_DUPLICATE_PACKAGE_ID, package_id=manifest.package_id)
            # Direct ZIP operations must check every disk version, not only report rows.
            directory = self.install_dir / manifest.package_id
            for root in directory.iterdir() if directory.is_dir() else ():
                if root.is_dir() and not root.name.startswith(".") and has_manifest(root):
                    try:
                        existing = load_manifest(root, require_domain=self.domain is not None)
                    except ExtensionFailure:
                        owned = data["packages"].get(manifest.package_id)
                        local_uninstall = operation == "uninstall" and owned is not None
                        owned_by_process = local_uninstall and owned["process_id"] == self.process_id
                        same_domain = owned_by_process and owned["domain"] == manifest.domain
                        active_version = same_domain and root.name == owned["active_version"]
                        pending = owned["pending"] if same_domain else None
                        pending_version = pending is not None and root.name == pending["version"]
                        if active_version or pending_version:
                            continue
                        raise
                    if existing.package_id != manifest.package_id or existing.domain != manifest.domain:
                        _fail(StatusCode.EXTENSION_DUPLICATE_PACKAGE_ID, package_id=manifest.package_id)
            row = data["packages"].get(manifest.package_id)
            if row is not None and row["domain"] != manifest.domain:
                _fail(StatusCode.EXTENSION_DUPLICATE_PACKAGE_ID, package_id=manifest.package_id)
            if row is not None and self._owner_alive(data, row["process_id"]):
                if row["pending"]:
                    local_uninstall = operation == "uninstall" and row["process_id"] == self.process_id
                    failed_or_abandoned = (
                        row["status"] in {"failed", "unknown"}
                        or row["pending"]["operation_id"] in self._abandoned
                    )
                    not_executing = row["pending"]["operation_id"] not in self._executing
                    retry_cleanup = local_uninstall and failed_or_abandoned and not_executing
                    if not retry_cleanup:
                        _fail(StatusCode.EXTENSION_PACKAGE_BUSY, package_id=manifest.package_id,
                              reason="operation remains unresolved")
                if operation == "install" and row["active_version"]:
                    _fail(StatusCode.EXTENSION_PACKAGE_EXISTS, package_id=manifest.package_id,
                          display_name=manifest.display_name or manifest.package_id)
            self._check_dependents(data, manifest, operation)
            if row is None:
                row = self._row(manifest)
                data["packages"][manifest.package_id] = row
            elif not self._owner_alive(data, row["process_id"]):
                row.update(active_version=None, requires={})
            pending = self._pending(manifest, operation)
            row.update(process_id=self.process_id, status={"install": "waiting", "reload": "reloading",
                                                          "uninstall": "uninstalling"}[operation],
                       reason=None, pending=pending)
            # Check cycles introduced by online operations using active and pending edges.
            graph = {}
            for package_id, item in data["packages"].items():
                dependencies = set(item["requires"])
                if item["pending"]:
                    dependencies.update(item["pending"]["requires"])
                graph[package_id] = dependencies
            stack = list(graph.get(manifest.package_id, ()))
            seen = set()
            while stack:
                current = stack.pop()
                if current == manifest.package_id:
                    _fail(StatusCode.EXTENSION_DEPENDENCY_CYCLE, packages=manifest.package_id)
                if current not in seen:
                    seen.add(current)
                    stack.extend(graph.get(current, ()))
            token = PackageOperationToken(
                self.run_id,
                self.process_id,
                manifest.package_id,
                pending["operation_id"],
            )
            self._tokens[token.operation_id] = token
            issued.append(token)
            return token

        try:
            return await self._transaction(begin)
        except BaseException:
            # Cancellation or a failed flush may follow an already committed admission.
            # No caller received this token, so it cannot be executing package code.
            for token in issued:
                self._abandoned.add(token.operation_id)
                try:
                    await self.finish_operation(token, report=None, active=None)
                except ExtensionFailure:
                    from openjiuwen.core.common.logging import logger
                    logger.exception("could not record abandoned package admission")
            raise

    def _check_token(self, data: dict[str, Any], token: PackageOperationToken) -> dict[str, Any]:
        self._require_owner(data)
        row = data["packages"].get(token.package_id)
        wrong_owner = token.run_id != self.run_id or token.process_id != self.process_id
        missing_operation = row is None or row["pending"] is None
        stale_row = not missing_operation and (
            row["process_id"] != self.process_id
            or row["pending"]["operation_id"] != token.operation_id
        )
        if wrong_owner or missing_operation or stale_row:
            _report_error("stale package operation")
        return row

    async def wait_for_dependencies(self, token: PackageOperationToken) -> None:
        deadline = time.monotonic() + self.dependency_timeout

        def check(data: dict[str, Any]) -> bool:
            row = self._check_token(data, token)
            pending = row["pending"]
            if pending["operation"] == "uninstall":
                return True
            for package_id, required in pending["requires"].items():
                dependency = data["packages"].get(package_id)
                if dependency is None:
                    _fail(StatusCode.EXTENSION_DEPENDENCY_NOT_MET, package_id=token.package_id,
                          reason=f"dependency {package_id} is missing")
                alive = self._owner_alive(data, dependency["process_id"])
                if dependency["active_version"] is not None and alive and dependency["pending"] is None:
                    if not specifier_matches(dependency["active_version"], required):
                        _fail(StatusCode.EXTENSION_DEPENDENCY_NOT_MET, package_id=token.package_id,
                              reason=f"dependency {package_id} version does not match {required}")
                    continue
                if dependency["status"] in {"failed", "unknown"} or (
                    dependency["process_id"] is not None and not alive
                ):
                    _fail(StatusCode.EXTENSION_DEPENDENCY_NOT_MET, package_id=token.package_id,
                          reason=f"dependency {package_id} unavailable: {dependency['reason']}")
                row["status"] = "waiting" if pending["operation"] == "install" else "reloading"
                return False
            row["status"] = "installing" if pending["operation"] == "install" else "reloading"
            return True

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail(StatusCode.EXTENSION_DEPENDENCY_TIMEOUT, package_id=token.package_id)
            if await self._transaction(check, timeout=min(self.lock_timeout, remaining)):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail(StatusCode.EXTENSION_DEPENDENCY_TIMEOUT, package_id=token.package_id)
            await asyncio.sleep(min(self.poll_interval, remaining))

    async def validate_operation(self, token: PackageOperationToken, manifest: Manifest, operation: str) -> None:
        self._check_manifest(manifest)

        def validate(data: dict[str, Any]) -> None:
            row = self._check_token(data, token)
            pending = row["pending"]
            if pending["operation"] != operation or pending["version"] != manifest.version:
                _report_error("operation does not match the manifest or plan")
            if pending["requires"] != _requirements(manifest):
                _report_error("dependencies changed after admission")
            self._check_dependents(data, manifest, operation)
            if operation != "uninstall":
                self._require_dependencies(data, pending["requires"], token.package_id)

        await self._transaction(validate, write=False)

    def _require_dependencies(self, data: dict[str, Any], requirements: Mapping[str, str], package_id: str) -> None:
        for dependency_id, required in requirements.items():
            row = data["packages"].get(dependency_id)
            missing = row is None
            unavailable = not missing and (
                not self._owner_alive(data, row["process_id"])
                or row["pending"] is not None
                or row["active_version"] is None
            )
            wrong_version = not (missing or unavailable) and not specifier_matches(
                row["active_version"],
                required,
            )
            if missing or unavailable or wrong_version:
                _fail(StatusCode.EXTENSION_DEPENDENCY_NOT_MET, package_id=package_id,
                      reason=f"dependency {dependency_id} changed after admission")

    async def reject_package(self, manifest: Manifest, error: OperationError) -> None:
        """Persist a known local rejection even when it occurs before loading."""
        self._check_manifest(manifest)

        def reject(data: dict[str, Any]) -> None:
            self._require_owner(data)
            row = data["packages"].get(manifest.package_id)
            has_local_pending = (
                row is not None
                and row["process_id"] == self.process_id
                and row["pending"] is not None
            )
            active_or_foreign = row is not None and (
                row["domain"] != manifest.domain or row["active_version"] is not None
            )
            if active_or_foreign or has_local_pending:
                return
            if row is None:
                row = self._row(manifest)
                data["packages"][manifest.package_id] = row
            row.update(process_id=self.process_id, status="failed", pending=None,
                       reason={"code": error.code, "message": error.message})
        await self._transaction(reject)

    @contextmanager
    def operation_scope(self, token: PackageOperationToken):
        """Authorize synchronous file steps only within an admitted operation."""
        if self._tokens.get(token.operation_id) != token:
            _report_error("operation token was not issued by this service")
        current = self._scope.set(token)
        self._executing.add(token.operation_id)
        try:
            yield
        finally:
            self._executing.discard(token.operation_id)
            self._scope.reset(current)

    def require_file_operation(self, package_id: str) -> None:
        token = self._scope.get()
        if token is None or token.package_id != package_id or self._tokens.get(token.operation_id) != token:
            _report_error("managed file mutation requires an admitted package operation")

    def current_operation(self, package_id: str) -> PackageOperationToken:
        self.require_file_operation(package_id)
        return self._scope.get()

    @contextmanager
    def file_operation_scope(self, package_id: str):
        """Serialize final directory publication/deletion with discovery."""
        token = self.current_operation(package_id)
        try:
            with portalocker.Lock(str(self.path) + ".lock", mode="a+b", timeout=self.lock_timeout,
                                  check_interval=min(0.05, self.lock_timeout)):
                self._check_token(self._read(), token)
                yield
        except portalocker.exceptions.LockException as exc:
            raise ExtensionFailure(StatusCode.EXTENSION_REPORT_FAILED, reason="report lock timeout") from exc

    async def finish_operation(
        self,
        token: PackageOperationToken,
        *,
        report: ActivationReport | None,
        active: ActivePackageRecord | None,
        cleanup_pending: bool = False,
    ) -> None:
        def finish(data: dict[str, Any]) -> None:
            row = self._check_token(data, token)
            if report is None:
                row.update(status="unknown", reason={"code": "UNKNOWN", "message": "query local runtime before retry"})
                return
            if report.status == "SUCCESS" and report.operation == "uninstall":
                del data["packages"][token.package_id]
                return
            if report.status == "SUCCESS" and active is None:
                _report_error("success was not confirmed by the local runtime")
            if report.status == "SUCCESS":
                self._require_dependencies(data, row["pending"]["requires"], token.package_id)
                if active.version != row["pending"]["version"] or active.domain != row["domain"]:
                    _report_error("local activation does not match the admitted package")
            if active is not None:
                row.update(active_version=active.version,
                           requires={item.package_id: item.version for item in active.requires_packages})
            else:
                row.update(active_version=None, requires={})
            row["status"] = "success" if report.status == "SUCCESS" else "failed"
            row["reason"] = None if report.error is None else {
                "code": report.error.code,
                "message": report.error.message,
            }
            if not cleanup_pending:
                row["pending"] = None

        await self._transaction(finish)
        if report is not None and (not cleanup_pending or report.status == "SUCCESS"):
            self._tokens.pop(token.operation_id, None)
            self._abandoned.discard(token.operation_id)

    async def reconcile_packages(
        self, active: tuple[ActivePackageRecord, ...], *, cleanup_packages: frozenset[str] = frozenset(),
    ) -> None:
        """Confirm a completed local publication after a report commit failed."""
        def reconcile(data: dict[str, Any]) -> None:
            self._require_owner(data)
            for record in active:
                row = data["packages"].get(record.package_id)
                if row is None or row["process_id"] != self.process_id or row["pending"] is None:
                    continue
                pending = row["pending"]
                cannot_reconcile = any(
                    (
                        pending["operation_id"] in self._executing,
                        record.package_id in cleanup_packages,
                        pending["operation"] == "uninstall",
                        pending["version"] != record.version,
                        record.domain != row["domain"],
                    )
                )
                if cannot_reconcile:
                    continue
                try:
                    self._require_dependencies(data, pending["requires"], record.package_id)
                except ExtensionFailure:
                    continue
                row.update(active_version=record.version,
                           requires={item.package_id: item.version for item in record.requires_packages},
                           status="success", reason=None, pending=None)
                self._tokens.pop(pending["operation_id"], None)
        await self._transaction(reconcile)

    async def mark_started(self) -> None:
        def started(data: dict[str, Any]) -> None:
            self._require_owner(data)
            data["processes"][self.process_id]["state"] = "running"

        await self._transaction(started)

    async def mark_stopping(self) -> None:
        def stop(data: dict[str, Any]) -> None:
            self._require_owner(data)
            data["processes"][self.process_id]["state"] = "stopping"
        await self._transaction(stop)

    async def close(self) -> None:
        if self._owner_lock is None:
            return

        def stop(data: dict[str, Any]) -> None:
            self._require_owner(data)
            data["processes"][self.process_id]["state"] = "stopping"

        try:
            await self._transaction(stop)
        finally:
            self._owner_lock.release()
            self._owner_lock = None
            self.run_id = None
