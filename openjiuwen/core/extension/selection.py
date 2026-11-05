# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Choose which packages can be installed, and in what order."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension.errors import OperationError, operation_error
from openjiuwen.core.extension.ordering import topological_order
from openjiuwen.core.extension.schema.manifest import Manifest
from openjiuwen.core.extension.schema.records import ActivePackageRecord
from openjiuwen.core.extension.versions import specifier_matches


@dataclass(frozen=True, slots=True)
class RejectedPackage:
    """A package that will not be loaded, with the reason."""

    package_id: str
    error: OperationError


@dataclass(frozen=True, slots=True)
class ResolveInstallResult:
    """Ordered packages that may be loaded, plus packages that may not.

    ``fatal`` is set when the whole batch stops, which happens when one
    package id appears twice. Otherwise a rejected package does not remove
    an unrelated package from ``ordered``.
    """

    fatal: OperationError | None
    ordered: tuple[Manifest, ...]
    rejected: tuple[RejectedPackage, ...]


def resolve_install_set(
    candidates: Sequence[Manifest],
    active: Sequence[ActivePackageRecord],
    host_version: str | None,
) -> ResolveInstallResult:
    """Decide install order from manifests and the packages already active.

    An id that is already active is rejected. Two candidates with the same id
    reject the whole batch.
    """
    duplicates = _duplicate_ids(candidates)
    if duplicates:
        package_id = ", ".join(duplicates)
        fatal = operation_error(
            StatusCode.EXTENSION_DUPLICATE_PACKAGE_ID,
            package_id=package_id,
        )
        return ResolveInstallResult(fatal=fatal, ordered=(), rejected=())

    active_by_id = {record.package_id: record for record in active}
    rejected: dict[str, OperationError] = {}
    pool: dict[str, Manifest] = {}
    for manifest in candidates:
        error = _direct_rejection(manifest, active_by_id, host_version)
        if error is None:
            pool[manifest.package_id] = manifest
        else:
            rejected[manifest.package_id] = error

    _reject_unsatisfied_dependencies(pool, rejected, active_by_id)
    ordered, remaining = topological_order(
        tuple(pool.values()),
        identifier=lambda manifest: manifest.package_id,
        dependencies=lambda manifest: tuple(
            item.package_id for item in manifest.requires_packages
        ),
    )
    cycle_ids, blocked_ids = _split_remaining(pool, remaining)
    for package_id in cycle_ids:
        rejected[package_id] = operation_error(
            StatusCode.EXTENSION_DEPENDENCY_CYCLE,
            packages=package_id,
        )
    for package_id in blocked_ids:
        rejected[package_id] = operation_error(
            StatusCode.EXTENSION_DEPENDENCY_NOT_MET,
            package_id=package_id,
            reason="a dependency was rejected because of a cycle or earlier failure",
        )
    rejected_rows = tuple(
        RejectedPackage(package_id, error)
        for package_id, error in sorted(rejected.items(), key=lambda item: item[0])
    )
    return ResolveInstallResult(fatal=None, ordered=tuple(ordered), rejected=rejected_rows)


def evaluate_reload(
    active: Sequence[ActivePackageRecord],
    package_id: str,
    new_manifest: Manifest,
) -> OperationError | None:
    """Return an error when the new manifest cannot replace the active package.

    Dependents that still accept ``new_manifest.version`` do not block reload.
    """
    active_by_id = {record.package_id: record for record in active}
    current = active_by_id.get(package_id)
    if current is None:
        return operation_error(
            StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED,
            package_id=package_id,
            reason="package is not active; cannot reload",
        )
    if new_manifest.package_id != package_id:
        return operation_error(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=(
                f"reload expected package {package_id}, "
                f"manifest has {new_manifest.package_id}"
            ),
        )
    dependency_error = _dependency_error(new_manifest, active_by_id, ignore_id=package_id)
    if dependency_error is not None:
        return dependency_error
    for record in active:
        if record.package_id == package_id:
            continue
        for requirement in record.requires_packages:
            if requirement.package_id != package_id:
                continue
            if not specifier_matches(new_manifest.version, requirement.version):
                return operation_error(
                    StatusCode.EXTENSION_DEPENDENT_INCOMPATIBLE,
                    package_id=package_id,
                    dependent=record.package_id,
                    required=requirement.version,
                    version=new_manifest.version,
                )
    return None


def _split_remaining(
    pool: Mapping[str, Manifest],
    remaining: Sequence[str],
) -> tuple[list[str], list[str]]:
    """Separate ids that sit in a cycle from ids merely blocked by one."""
    remaining_ids = set(remaining)
    graph = {
        package_id: [
            item.package_id
            for item in pool[package_id].requires_packages
            if item.package_id in remaining_ids
        ]
        for package_id in remaining_ids
    }
    cycles: list[str] = []
    blocked: list[str] = []
    for package_id in remaining:
        if _reaches_self(package_id, graph):
            cycles.append(package_id)
        else:
            blocked.append(package_id)
    return cycles, blocked


def _reaches_self(start: str, graph: Mapping[str, Sequence[str]]) -> bool:
    stack = list(graph.get(start, ()))
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current == start:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, ()))
    return False


def _duplicate_ids(candidates: Sequence[Manifest]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for manifest in candidates:
        if manifest.package_id in seen and manifest.package_id not in duplicates:
            duplicates.append(manifest.package_id)
        seen.add(manifest.package_id)
    return duplicates


def _direct_rejection(
    manifest: Manifest,
    active_by_id: Mapping[str, ActivePackageRecord],
    host_version: str | None,
) -> OperationError | None:
    existing = active_by_id.get(manifest.package_id)
    if existing is not None:
        display = manifest.display_name or existing.package_id
        return operation_error(
            StatusCode.EXTENSION_PACKAGE_EXISTS,
            package_id=manifest.package_id,
            display_name=display,
        )
    if manifest.requires_host is not None:
        if host_version is None:
            return operation_error(
                StatusCode.EXTENSION_HOST_VERSION_MISSING,
                package_id=manifest.package_id,
                reason=f"requires host {manifest.requires_host}",
            )
        try:
            matched = specifier_matches(host_version, manifest.requires_host)
        except ValueError as exc:
            return operation_error(
                StatusCode.EXTENSION_HOST_VERSION_TOO_LOW,
                package_id=manifest.package_id,
                reason=str(exc),
            )
        if not matched:
            return operation_error(
                StatusCode.EXTENSION_HOST_VERSION_TOO_LOW,
                package_id=manifest.package_id,
                reason=(
                    f"requires host {manifest.requires_host}, "
                    f"current host is {host_version}"
                ),
            )
    return None


def _reject_unsatisfied_dependencies(
    pool: dict[str, Manifest],
    rejected: dict[str, OperationError],
    active_by_id: Mapping[str, ActivePackageRecord],
) -> None:
    changed = True
    while changed:
        changed = False
        for package_id, manifest in list(pool.items()):
            error = _dependency_error(manifest, active_by_id, pool, rejected)
            if error is None:
                continue
            rejected[package_id] = error
            del pool[package_id]
            changed = True


def _dependency_error(
    manifest: Manifest,
    active_by_id: Mapping[str, ActivePackageRecord],
    pool: Mapping[str, Manifest] | None = None,
    rejected: Mapping[str, OperationError] | None = None,
    ignore_id: str | None = None,
) -> OperationError | None:
    available = pool or {}
    blocked = rejected or {}
    for requirement in manifest.requires_packages:
        if requirement.package_id == ignore_id:
            continue
        if requirement.package_id in blocked:
            return operation_error(
                StatusCode.EXTENSION_DEPENDENCY_NOT_MET,
                package_id=manifest.package_id,
                reason=(
                    f"dependency {requirement.package_id} failed: "
                    f"{blocked[requirement.package_id].message}"
                ),
            )
        if requirement.package_id in available:
            dependency = available[requirement.package_id]
            if not specifier_matches(dependency.version, requirement.version):
                return operation_error(
                    StatusCode.EXTENSION_DEPENDENCY_NOT_MET,
                    package_id=manifest.package_id,
                    reason=(
                        f"requires {requirement.package_id} {requirement.version}, "
                        f"batch version is {dependency.version}"
                    ),
                )
            continue
        active = active_by_id.get(requirement.package_id)
        if active is None:
            return operation_error(
                StatusCode.EXTENSION_DEPENDENCY_NOT_MET,
                package_id=manifest.package_id,
                reason=f"dependency {requirement.package_id} is not active",
            )
        if not specifier_matches(active.version, requirement.version):
            return operation_error(
                StatusCode.EXTENSION_DEPENDENCY_NOT_MET,
                package_id=manifest.package_id,
                reason=(
                    f"requires {requirement.package_id} {requirement.version}, "
                    f"active version is {active.version}"
                ),
            )
    return None
