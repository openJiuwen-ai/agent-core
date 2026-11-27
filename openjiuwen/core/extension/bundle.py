# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""System plugin bundle manifests and their shared install layout.

A bundle is an ``extension`` package whose root manifest declares
``is_bundle``. It carries independent system plugins under ``members``; each
member keeps its own package identity and ``package.domain``. The root copy
lives in the same ``<id>/<version>`` store layout as a package; its manifest is
parsed as a bundle and is never activated directly.

Only manifest rules and dependency ordering live here. Materializing copies,
loading packages and persisting records belong to the hosting processes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension.data.json_values import valid_package_id
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.manifest import BUNDLE_FLAG, MANIFEST_NAME, parse_manifest_mapping
from openjiuwen.core.extension.versions import parse_version, specifier_matches

#: A bundle carries at most this many members; a hard limit, not a setting.
MAX_BUNDLE_MEMBERS = 50
MANIFEST_LIMIT = 64 * 1024


@dataclass(frozen=True, slots=True)
class BundleMember:
    """One validated member package of a bundle."""

    package_id: str
    version: str
    domain: str
    path: str
    root: Path
    requires: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class BundleSpec:
    """A validated bundle root with members in declared order."""

    bundle_id: str
    version: str
    members: tuple[BundleMember, ...]


def is_bundle_document(document: Mapping[str, Any] | None) -> bool:
    """Return True only when the root manifest explicitly declares a bundle."""

    return isinstance(document, Mapping) and document.get(BUNDLE_FLAG) is True


def read_bundle_json(root: Path) -> dict[str, Any] | None:
    """Read a bundle root ``manifest.json`` without importing package code."""

    path = Path(root) / MANIFEST_NAME
    try:
        if not path.is_file() or path.stat().st_size > MANIFEST_LIMIT:
            return None
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, Mapping) else None


def read_bundle_spec(root: Path) -> BundleSpec:
    """Validate a bundle directory and return its members and dependency order.

    Raises:
        ExtensionFailure: The root is not a bundle, a member is invalid, the
            member limit is exceeded, or member dependencies are contradictory.
    """

    bundle_root = Path(root).expanduser().resolve()
    document = read_bundle_json(bundle_root)
    if document is None:
        _fail("bundle manifest.json is missing or unreadable")
    try:
        manifest = parse_manifest_mapping(document, bundle_root)
    except ExtensionFailure as exc:
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"bundle manifest.json is invalid: {exc.message}",
        ) from exc
    if not is_bundle_document(document):
        _fail("manifest.json does not declare is_bundle")
    raw_members = document.get("members")
    if not isinstance(raw_members, list) or not raw_members:
        _fail("bundle members must be a nonempty list")
    if len(raw_members) > MAX_BUNDLE_MEMBERS:
        _fail(f"bundle members exceed the limit of {MAX_BUNDLE_MEMBERS}")

    members: list[BundleMember] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for item in raw_members:
        relative = item.get("path") if isinstance(item, Mapping) else None
        if not isinstance(relative, str) or not relative.strip():
            _fail("each bundle member requires a nonempty path")
        member_path = _member_relative_path(relative)
        if member_path in seen_paths:
            _fail(f"duplicate bundle member path {member_path}")
        seen_paths.add(member_path)
        member_candidate = bundle_root / member_path
        if member_candidate.is_symlink():
            _fail(f"bundle member must not be a symbolic link: {member_path}")
        member_root = member_candidate.resolve()
        if not member_root.is_relative_to(bundle_root):
            _fail(f"bundle member escapes the bundle root: {member_path}")
        if not member_root.is_dir():
            _fail(f"bundle member is not a directory: {member_path}")
        if _is_nested_member(member_path, seen_paths):
            _fail(f"bundle member is nested in another member: {member_path}")
        member_document = read_bundle_json(member_root)
        if member_document is None:
            _fail(f"bundle member has no readable manifest.json: {member_path}")
        if is_bundle_document(member_document):
            _fail(f"nested bundles are not supported: {member_path}")
        try:
            member_manifest = parse_manifest_mapping(member_document, member_root, require_domain=True)
        except ExtensionFailure as exc:
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason=f"bundle member manifest is invalid: {member_path}: {exc.message}",
            ) from exc
        member_domain = (member_manifest.domain or "").strip().lower()
        if not member_domain:
            _fail(f"bundle member declares no domain: {member_path}")
        if member_manifest.package_id in seen_ids:
            raise ExtensionFailure(
                StatusCode.EXTENSION_DUPLICATE_PACKAGE_ID,
                package_id=member_manifest.package_id,
            )
        if member_manifest.package_id == manifest.package_id:
            _fail(f"a bundle member must not reuse the bundle id: {member_manifest.package_id}")
        seen_ids.add(member_manifest.package_id)
        members.append(
            BundleMember(
                package_id=member_manifest.package_id,
                version=member_manifest.version,
                domain=member_domain,
                path=member_path,
                root=member_root,
                requires=tuple(
                    (dependency.package_id, dependency.version)
                    for dependency in member_manifest.requires_packages
                ),
            )
        )
    for member in members:
        if not valid_package_id(member.package_id):
            _fail(f"bundle member package id is invalid: {member.package_id}")
        try:
            parse_version(member.version)
        except (TypeError, ValueError) as exc:
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason=f"bundle member version is invalid: {member.path}: {exc}",
            ) from exc
    spec = BundleSpec(bundle_id=manifest.package_id, version=manifest.version, members=tuple(members))
    _validate_member_dependencies(spec)
    return spec


def dependency_levels(spec: BundleSpec) -> tuple[tuple[BundleMember, ...], ...]:
    """Return member levels where every level depends only on earlier levels.

    Raises:
        ExtensionFailure: A member requires itself or the members form a cycle.
    """

    by_id = {member.package_id: member for member in spec.members}
    edges = {
        member.package_id: {package_id for package_id, _ in member.requires if package_id in by_id}
        for member in spec.members
    }
    for package_id, dependencies in edges.items():
        if package_id in dependencies:
            raise ExtensionFailure(
                StatusCode.EXTENSION_DEPENDENCY_CYCLE, packages=package_id,
            )
    levels: list[tuple[BundleMember, ...]] = []
    remaining = set(by_id)
    while remaining:
        ready = sorted(package_id for package_id in remaining if not (edges[package_id] & remaining))
        if not ready:
            cycle = ", ".join(sorted(remaining))
            raise ExtensionFailure(StatusCode.EXTENSION_DEPENDENCY_CYCLE, packages=cycle)
        levels.append(tuple(by_id[package_id] for package_id in ready))
        remaining.difference_update(ready)
    return tuple(levels)


def bundle_version_dir(install_dir: Path, bundle_id: str, version: str) -> Path:
    """Return the managed root copy of one bundle version."""

    return Path(install_dir).expanduser().resolve() / bundle_id / version


def _validate_member_dependencies(spec: BundleSpec) -> None:
    """Check member-to-member requirements against the declared member versions."""

    by_id = {member.package_id: member for member in spec.members}
    for member in spec.members:
        for package_id, required in member.requires:
            dependency = by_id.get(package_id)
            if dependency is None:
                continue
            try:
                matched = specifier_matches(dependency.version, required)
            except ValueError as exc:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_MANIFEST_INVALID,
                    reason=f"bundle member {member.package_id} dependency specifier is invalid: {exc}",
                ) from exc
            if not matched:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_DEPENDENCY_NOT_MET,
                    package_id=member.package_id,
                    reason=(
                        f"dependency {package_id} version {dependency.version} "
                        f"does not match {required}"
                    ),
                )


def _member_relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        _fail(f"bundle member path must be relative without '..': {value}")
    if any(part.startswith(".") for part in relative.parts):
        _fail(f"bundle member path must not be hidden: {value}")
    return relative.as_posix()


def _is_nested_member(path: str, paths: set[str]) -> bool:
    for other in paths:
        if other == path:
            continue
        if path.startswith(f"{other}/") or other.startswith(f"{path}/"):
            return True
    return False


def _fail(reason: str, status: StatusCode = StatusCode.EXTENSION_MANIFEST_INVALID) -> None:
    raise ExtensionFailure(status, reason=reason)
