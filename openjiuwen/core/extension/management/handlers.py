# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Core Handler orchestration for the nine package management events.

The flows follow the architecture document: ``package.install`` parses each
source, resolves the batch, and then plans and applies one package at a time;
``package.uninstall`` refuses a busy or required package before touching the
Bus; ``package.reload`` hot-switches a package without uninstalling it first.
Each step that is an operation of its own is a real event trigger, so a caller
can request it separately and read the same result.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events.protocol import ExtensionEventBusProtocol
from openjiuwen.core.events.types import Event
from openjiuwen.core.extension.errors import ExtensionFailure, OperationError, operation_error
from openjiuwen.core.extension.management.context import ManagementContext, context_for
from openjiuwen.core.extension.management.types import (
    EXTENSION_APPLY_EVENT,
    EXTENSION_PLAN_EVENT,
    EXTENSION_ROLLBACK_EVENT,
    PACKAGE_PARSE_EVENT,
    PACKAGE_RESOLVE_EVENT,
    ApplyRequest,
    InstallRequest,
    PackageOperationResult,
    ParsedPackage,
    ParseRequest,
    ParseResult,
    PlanOperation,
    PlanRequest,
    PlanResult,
    QueryRequest,
    QueryResult,
    ReloadRequest,
    ResolveRequest,
    ResolveResult,
    RollbackRequest,
    RollbackResult,
    UninstallRequest,
)
from openjiuwen.core.extension.manifest import load_manifest
from openjiuwen.core.extension.schema.manifest import Manifest
from openjiuwen.core.extension.schema.mcp import McpServerSpec, declarations
from openjiuwen.core.extension.schema.records import ActivationReport, ActivePackageRecord, ExtensionPlan
from openjiuwen.core.extension.selection import resolve_install_set


async def handle_package_parse(request: ParseRequest) -> ParseResult:
    """Read one local source: package root, manifest, and entry boundaries."""
    context = context_for(request.bus)
    source = Path(request.source)
    try:
        opened = context.package_manager.open_source(source)
    except ExtensionFailure as exc:
        return ParseResult(source=source, error=exc.as_error())
    return ParseResult(
        source=source,
        package=ParsedPackage(
            manifest=opened.manifest,
            root=opened.root,
            source=source,
            opened=opened,
        ),
    )


async def handle_package_resolve(request: ResolveRequest) -> ResolveResult:
    """Decide the install order, or whether one package may be uninstalled."""
    context = context_for(request.bus)
    if request.operation == "install":
        resolved = resolve_install_set(
            request.candidates,
            context.runtime.active_packages(),
            context.host_version,
        )
        return ResolveResult(
            operation="install",
            ordered=resolved.ordered,
            rejected=resolved.rejected,
            fatal=resolved.fatal,
        )
    if request.operation == "uninstall":
        package_id = request.package_id or ""
        if context.runtime.get_active(package_id) is None:
            try:
                if context.runtime.uninstall_versions(package_id) or context.package_manager.managed_versions(
                    package_id
                ):
                    return ResolveResult(operation="uninstall")
            except ExtensionFailure as exc:
                return ResolveResult(operation="uninstall", error=exc.as_error())
        return ResolveResult(
            operation="uninstall",
            error=context.runtime.uninstall_decision(package_id),
        )
    return ResolveResult(
        operation=str(request.operation),
        fatal=operation_error(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason=f"unsupported resolve operation: {request.operation!r}",
        ),
    )


async def handle_package_query(request: QueryRequest) -> QueryResult:
    """Report the facts the Runtime and the install store can confirm now."""
    context = context_for(request.bus)
    runtime = context.runtime
    active = runtime.active_packages()
    installed = tuple(context.package_manager.list_installed())
    if request.package_id is not None:
        active = tuple(item for item in active if item.package_id == request.package_id)
        installed = tuple(item for item in installed if item.parent.name == request.package_id)
    return QueryResult(
        active=active,
        installed=installed,
        inflight={item.package_id: runtime.inflight_count(item.package_id) for item in active},
        published={item.package_id: runtime.published_handlers(item.package_id) for item in active},
    )


async def handle_extension_plan(request: PlanRequest) -> PlanResult:
    """Order the extensions of one package for this operation."""
    context = context_for(request.bus)
    previous = context.runtime.get_active(request.manifest.package_id)
    try:
        plan = context.orchestrator.build_plan(
            request.package_root,
            request.manifest,
            request.operation,
            previous,
        )
    except ExtensionFailure as exc:
        return PlanResult(error=exc.as_error())
    return PlanResult(plan=plan)


async def handle_extension_apply(request: ApplyRequest) -> ActivationReport:
    """Run one plan and report what actually happened."""
    context = context_for(request.bus)
    if request.plan.operation == "uninstall":
        return await _apply_uninstall(context, request)
    return await _apply_load(context, request)


async def handle_extension_rollback(request: RollbackRequest) -> RollbackResult:
    """Drop a candidate that never reached the Bus, and its own store copy."""
    context = context_for(request.bus)
    plan = request.plan
    released: tuple[str, ...] = ()
    if request.candidate is not None:
        released = request.candidate.module_names
        context.runtime.abort(request.candidate)
    removed = False
    residue: OperationError | None = None
    if request.store_copy_created:
        package_id = plan.package.package_id
        version = plan.package.package_version or ""
        try:
            context.package_manager.remove_installed(package_id, version)
            removed = True
        except ExtensionFailure as exc:
            residue = exc.as_error()
    return RollbackResult(
        plan_id=plan.plan_id,
        package_id=plan.package.package_id,
        released_modules=released,
        removed_store_copy=removed,
        error=request.error,
        residue=residue,
    )


async def handle_package_install(request: InstallRequest) -> PackageOperationResult:
    """Install every source: parse, resolve the batch, then plan and apply."""
    context = context_for(request.bus)
    bus = request.bus
    reports: list[ActivationReport] = []
    parsed: list[ParsedPackage] = []
    try:
        for source in request.paths:
            result = await bus.trigger(
                Event(PACKAGE_PARSE_EVENT, ParseRequest(bus=bus, source=Path(source)))
            )
            if not isinstance(result, ParseResult) or result.package is None:
                error = result.error if isinstance(result, ParseResult) else _invalid_result(PACKAGE_PARSE_EVENT)
                reports.append(_report(str(source), "install", error))
                continue
            parsed.append(result.package)
        if parsed:
            reports.extend(await _install_batch(context, bus, parsed))
    finally:
        for item in parsed:
            if item.opened is not None:
                item.opened.close()
    return PackageOperationResult(operation="install", reports=tuple(reports))


async def handle_package_uninstall(request: UninstallRequest) -> PackageOperationResult:
    """Remove one package from the Bus and delete its install copy."""
    context = context_for(request.bus)
    bus = request.bus
    package_id = request.package_id
    resolved = await bus.trigger(
        Event(
            PACKAGE_RESOLVE_EVENT,
            ResolveRequest(bus=bus, operation="uninstall", package_id=package_id),
        )
    )
    if not isinstance(resolved, ResolveResult):
        return _single("uninstall", package_id, _invalid_result(PACKAGE_RESOLVE_EVENT))
    if resolved.error is not None:
        return _single("uninstall", package_id, resolved.error)
    record = context.runtime.get_active(package_id)
    if record is None:
        return await _uninstall_inactive(context, package_id)
    return await _plan_and_apply(bus, "uninstall", _manifest_for_record(record), record.root)


async def handle_package_reload(request: ReloadRequest) -> PackageOperationResult:
    """Hot-switch one package from the files currently in the install store."""
    context = context_for(request.bus)
    bus = request.bus
    package_id = request.package_id
    record = context.runtime.get_active(package_id)
    if record is None:
        return _single(
            "reload",
            package_id,
            operation_error(
                StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED,
                package_id=package_id,
                reason="package is not active; cannot reload",
            ),
        )
    root = context.package_manager.version_dir(package_id, record.version)
    if not (root / "manifest.yaml").is_file():
        root = record.root
    try:
        manifest = load_manifest(root)
    except ExtensionFailure as exc:
        return _single("reload", package_id, exc.as_error())
    error = context.runtime.check_reload(package_id, manifest)
    if error is not None:
        return _single("reload", package_id, error)
    return await _plan_and_apply(bus, "reload", manifest, root)


async def _plan_and_apply(
    bus: ExtensionEventBusProtocol,
    operation: PlanOperation,
    manifest: Manifest,
    package_root: Path,
    *,
    store_copy_created: bool = False,
) -> PackageOperationResult:
    """Trigger ``extension.plan`` and, when it holds, ``extension.apply``."""
    report = await _plan_and_apply_report(
        bus,
        operation,
        manifest,
        package_root,
        store_copy_created=store_copy_created,
    )
    return PackageOperationResult(operation=operation, reports=(report,))


async def _plan_and_apply_report(
    bus: ExtensionEventBusProtocol,
    operation: PlanOperation,
    manifest: Manifest,
    package_root: Path,
    *,
    store_copy_created: bool = False,
) -> ActivationReport:
    planned = await bus.trigger(
        Event(
            EXTENSION_PLAN_EVENT,
            PlanRequest(
                bus=bus,
                operation=operation,
                manifest=manifest,
                package_root=package_root,
            ),
        )
    )
    if not isinstance(planned, PlanResult) or planned.plan is None:
        error = planned.error if isinstance(planned, PlanResult) else _invalid_result(EXTENSION_PLAN_EVENT)
        return _report(manifest.package_id, operation, error)
    applied = await bus.trigger(
        Event(
            EXTENSION_APPLY_EVENT,
            ApplyRequest(
                bus=bus,
                plan=planned.plan,
                manifest=manifest,
                store_copy_created=store_copy_created,
            ),
        )
    )
    if not isinstance(applied, ActivationReport):
        return _report(manifest.package_id, operation, _invalid_result(EXTENSION_APPLY_EVENT))
    return applied


async def _install_batch(
    context: ManagementContext,
    bus: ExtensionEventBusProtocol,
    parsed: Sequence[ParsedPackage],
) -> list[ActivationReport]:
    """Resolve the parsed batch, then install the packages it allows."""
    resolved = await bus.trigger(
        Event(
            PACKAGE_RESOLVE_EVENT,
            ResolveRequest(
                bus=bus,
                operation="install",
                candidates=tuple(item.manifest for item in parsed),
            ),
        )
    )
    if not isinstance(resolved, ResolveResult):
        error = _invalid_result(PACKAGE_RESOLVE_EVENT)
        return [_report(item.manifest.package_id, "install", error) for item in parsed]
    if resolved.fatal is not None:
        return [_report(item.manifest.package_id, "install", resolved.fatal) for item in parsed]

    sources = {item.manifest.package_id: item for item in parsed}
    reports: list[ActivationReport] = []
    failed: set[str] = set()
    for rejection in resolved.rejected:
        failed.add(rejection.package_id)
        reports.append(_report(rejection.package_id, "install", rejection.error))
    for manifest in resolved.ordered:
        blocked = sorted(
            item.package_id for item in manifest.requires_packages if item.package_id in failed
        )
        if blocked:
            failed.add(manifest.package_id)
            reports.append(
                _report(
                    manifest.package_id,
                    "install",
                    operation_error(
                        StatusCode.EXTENSION_DEPENDENCY_NOT_MET,
                        package_id=manifest.package_id,
                        reason=f"dependency {', '.join(blocked)} failed in this batch",
                    ),
                )
            )
            continue
        report = await _install_one(context, bus, sources[manifest.package_id])
        if report.status == "FAILED":
            failed.add(manifest.package_id)
        reports.append(report)
    return reports


async def _install_one(
    context: ManagementContext,
    bus: ExtensionEventBusProtocol,
    package: ParsedPackage,
) -> ActivationReport:
    """Copy one package into the install store, then plan and apply it."""
    manifest = package.manifest
    try:
        root = context.package_manager.copy_into_store(package.root, manifest)
    except ExtensionFailure as exc:
        return _report(manifest.package_id, "install", exc.as_error())
    return await _plan_and_apply_report(
        bus,
        "install",
        manifest,
        root,
        store_copy_created=root != package.root.resolve(),
    )


async def _apply_load(context: ManagementContext, request: ApplyRequest) -> ActivationReport:
    """Serialize publication against resource cleanup of the same package."""
    lock = context.runtime.package_lock(request.plan.package.package_id)
    if lock.locked():
        return _failed(
            request.plan,
            operation_error(
                StatusCode.EXTENSION_PACKAGE_BUSY,
                package_id=request.plan.package.package_id,
                reason="another management operation is running",
            ),
        )
    async with lock:
        if context.runtime.uninstall_versions(request.plan.package.package_id):
            return _failed(
                request.plan,
                operation_error(
                    StatusCode.EXTENSION_PACKAGE_BUSY,
                    package_id=request.plan.package.package_id,
                    reason="an unfinished uninstall must be retried first",
                ),
            )
        record = context.runtime.get_active(request.plan.package.package_id)
        generation = None if record is None else record.generation
        if generation != request.plan.previous_generation:
            return _failed(request.plan, _invalid_result("stale extension plan"), current_generation=generation)
        return await _apply_load_locked(context, request)


async def _apply_load_locked(context: ManagementContext, request: ApplyRequest) -> ActivationReport:
    """Load an install or reload plan and publish it in one pass."""
    plan = request.plan
    if any(context.runtime.uninstall_versions(item.package_id) for item in request.manifest.requires_packages):
        return _failed(
            plan,
            operation_error(
                StatusCode.EXTENSION_DEPENDENCY_NOT_MET,
                package_id=plan.package.package_id,
                reason="a dependency is being uninstalled",
            ),
        )
    prepared = context.runtime.prepare(plan)
    if prepared.candidate is None:
        return _failed(plan, prepared.error or _invalid_result("prepare"))
    if plan.operation == "reload":
        released = await _release_replaced_mcp(plan)
        if released is not None:
            context.runtime.abort(prepared.candidate)
            return _failed(plan, released)
    if plan.operation == "install":
        published = context.runtime.publish(
            prepared.candidate,
            plan,
            context.bus,
            manifest=request.manifest,
        )
    else:
        published = context.runtime.replace_published(
            prepared.candidate,
            plan,
            context.bus,
            manifest=request.manifest,
        )
    if published.error is not None:
        await request.bus.trigger(
            Event(
                EXTENSION_ROLLBACK_EVENT,
                RollbackRequest(
                    bus=request.bus,
                    plan=plan,
                    candidate=prepared.candidate,
                    error=published.error,
                    store_copy_created=request.store_copy_created,
                ),
            )
        )
        return _failed(plan, published.error)
    _declare_mcp(plan.package.package_id, plan.mcp_servers)
    return ActivationReport(
        plan_id=plan.plan_id,
        package_id=plan.package.package_id,
        operation=plan.operation,
        status="SUCCESS",
        previous_generation=plan.previous_generation,
        current_generation=plan.target_generation,
    )


async def _apply_uninstall(context: ManagementContext, request: ApplyRequest) -> ActivationReport:
    """Hold the package lock through unregister, callbacks, and file removal."""
    plan = request.plan
    lock = context.runtime.package_lock(plan.package.package_id)
    if lock.locked():
        return _failed(
            plan,
            operation_error(
                StatusCode.EXTENSION_PACKAGE_BUSY,
                package_id=plan.package.package_id,
                reason="another management operation is running",
            ),
            current_generation=plan.previous_generation,
        )
    async with lock:
        return await _apply_uninstall_locked(context, request)


async def _apply_uninstall_locked(context: ManagementContext, request: ApplyRequest) -> ActivationReport:
    """Remove one package from the Bus, then delete its version directory."""
    plan = request.plan
    package_id = plan.package.package_id
    decision = context.runtime.uninstall_decision(package_id)
    if decision is not None:
        return _failed(plan, decision, current_generation=plan.previous_generation)
    try:
        record = context.runtime.get_active(package_id)
        if record is None or record.generation != plan.previous_generation:
            return _failed(plan, _invalid_result("stale uninstall plan"))
        active_version = plan.package.package_version or request.manifest.version
        known_versions = (*context.runtime.uninstall_versions(package_id), active_version)
        versions = context.package_manager.managed_versions(package_id, known_versions=known_versions)
        context.runtime.uninstall_versions(package_id, (*versions, active_version))
        context.runtime.unpublish(package_id, context.bus, keep_record=True)
        await _remove_package_resources(context, package_id, record.generation)
        context.runtime.drop_active(package_id)
    except Exception as exc:
        return _failed(plan, _cleanup_error(exc), current_generation=plan.previous_generation)
    return ActivationReport(
        plan_id=plan.plan_id,
        package_id=package_id,
        operation=plan.operation,
        status="SUCCESS",
        previous_generation=plan.previous_generation,
        current_generation=None,
    )


def _declare_mcp(package_id: str, specs: tuple[McpServerSpec, ...]) -> None:
    """Record a package's declarations after its handlers reached the Bus."""
    declarations.declare(package_id, specs)


async def _release_replaced_mcp(plan: ExtensionPlan) -> OperationError | None:
    """Drop connections whose declaration changed or disappeared on reload."""
    previous = declarations.specs_for(plan.package.package_id)
    current = {spec.name: spec for spec in plan.mcp_servers}
    stale = tuple(
        spec
        for spec in previous
        if spec.name not in current or current[spec.name].digest != spec.digest
    )
    return await _remove_mcp_servers(stale)


async def _remove_mcp_servers(specs: Sequence[McpServerSpec]) -> OperationError | None:
    """Close the servers in ``specs``; absence is not an error."""
    if not specs:
        return None
    manager = _resource_mgr_or_none()
    if manager is None:
        return None
    for spec in specs:
        try:
            removed = await manager.remove_mcp_server(
                spec.server_id,
                skip_if_tag_not_exists=True,
                ignore_exception=True,
            )
        except Exception as exc:
            return operation_error(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason=f"cannot release MCP server {spec.server_name}: {exc}",
            )
        failure = _result_failure(removed)
        if failure is not None:
            return operation_error(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason=f"cannot release MCP server {spec.server_name}: {failure}",
            )
    return None


def _result_failure(result: object) -> BaseException | None:
    """Return the first failed ``Result`` in one remove call, if any."""
    items = result if isinstance(result, list) else [result]
    for item in items:
        if item is not None and getattr(item, "is_err", lambda: False)():
            return item.msg()
    return None


def _resource_mgr_or_none() -> object | None:
    """Return the installed resource manager, or None when the host has none."""
    from openjiuwen.core.events.ops_impl.mcp.runtime import require_resource_mgr

    try:
        return require_resource_mgr()
    except RuntimeError:
        return None


async def _remove_package_resources(context: ManagementContext, package_id: str, generation: int) -> None:
    stripped = await _remove_mcp_servers(declarations.specs_for(package_id))
    if stripped is not None:
        raise ExtensionFailure(stripped.status, **stripped.params)
    await context.runtime.cleanup_package_resources(package_id, generation, reason="uninstall")
    for version in context.runtime.uninstall_versions(package_id):
        context.package_manager.remove_installed(package_id, version, missing_ok=True)
    declarations.withdraw(package_id)


async def _uninstall_inactive(context: ManagementContext, package_id: str) -> PackageOperationResult:
    lock = context.runtime.package_lock(package_id)
    if lock.locked():
        return _single(
            "uninstall",
            package_id,
            operation_error(
                StatusCode.EXTENSION_PACKAGE_BUSY,
                package_id=package_id,
                reason="another management operation is running",
            ),
        )
    async with lock:
        try:
            versions = context.package_manager.managed_versions(
                package_id,
                known_versions=context.runtime.uninstall_versions(package_id),
            )
            if not versions and not context.runtime.uninstall_versions(package_id):
                return _single(
                    "uninstall",
                    package_id,
                    operation_error(
                        StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED,
                        package_id=package_id,
                        reason="no managed install copy",
                    ),
                )
            context.runtime.stop_package_calls(package_id)
            context.runtime.uninstall_versions(package_id, versions)
            await _remove_package_resources(context, package_id, 0)
            context.runtime.drop_active(package_id, missing_ok=True)
        except Exception as exc:
            return _single("uninstall", package_id, _cleanup_error(exc))
    return PackageOperationResult(
        operation="uninstall",
        reports=(
            ActivationReport(
                plan_id="",
                package_id=package_id,
                operation="uninstall",
                status="SUCCESS",
                previous_generation=None,
                current_generation=None,
            ),
        ),
    )


def _cleanup_error(failure: Exception) -> OperationError:
    if isinstance(failure, BaseError):
        params: dict[str, Any] = dict(failure.params)
        return operation_error(failure.status, **params)
    return operation_error(StatusCode.EXTENSION_LOAD_FAILED, reason=f"package cleanup failed: {failure}")


def _manifest_for_record(record: ActivePackageRecord) -> Manifest:
    """Describe an active package without depending on its files.

    Uninstall targets come from the Runtime records. The manifest is reread
    when it is still readable and still matches; otherwise the record itself
    carries everything an uninstall plan needs.
    """
    try:
        manifest = load_manifest(record.root)
    except ExtensionFailure:
        manifest = None
    if (
        manifest is not None
        and manifest.package_id == record.package_id
        and manifest.version == record.version
    ):
        return manifest
    return Manifest(
        schema_version=1,
        package_id=record.package_id,
        version=record.version,
        display_name=None,
        description=None,
        requires_host=None,
        requires_packages=record.requires_packages,
        extensions=(),
    )


def _failed(
    plan: ExtensionPlan,
    error: OperationError,
    *,
    current_generation: int | None = None,
) -> ActivationReport:
    return ActivationReport(
        plan_id=plan.plan_id,
        package_id=plan.package.package_id,
        operation=plan.operation,
        status="FAILED",
        previous_generation=plan.previous_generation,
        current_generation=current_generation,
        error=error,
    )


def _report(package_id: str, operation: str, error: OperationError | None) -> ActivationReport:
    """Build a FAILED report for a package that never reached a plan."""
    return ActivationReport(
        plan_id="",
        package_id=package_id,
        operation=operation,
        status="FAILED",
        previous_generation=None,
        current_generation=None,
        error=error or _invalid_result(operation),
    )


def _single(operation: str, package_id: str, error: OperationError) -> PackageOperationResult:
    return PackageOperationResult(
        operation=operation,
        reports=(_report(package_id, operation, error),),
    )


def _invalid_result(event_type: str) -> OperationError:
    return operation_error(
        StatusCode.EXTENSION_LOAD_FAILED,
        reason=f"{event_type} returned no usable result",
    )
