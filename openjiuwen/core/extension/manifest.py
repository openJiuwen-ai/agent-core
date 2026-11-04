# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Read manifest.yaml and check that its entry files stay inside the package."""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.schema.manifest import ExtensionDecl, Manifest, PackageDependency
from openjiuwen.core.extension.versions import parse_version, specifier_matches
from openjiuwen.core.extension.data.json_values import valid_package_id

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def load_manifest(package_root: Path) -> Manifest:
    """Read ``package_root / manifest.yaml``.

    Raises:
        ExtensionFailure: The file is missing, unreadable, or the declaration
            does not meet the manifest rules.
    """
    root = package_root.expanduser().resolve()
    path = root / "manifest.yaml"
    if not path.is_file():
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"manifest.yaml is missing under {root}",
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"cannot read manifest.yaml: {exc}",
            cause=exc,
        ) from exc
    if not isinstance(raw, dict):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason="manifest.yaml top level must be a mapping",
        )
    return parse_manifest_mapping(raw, root)


def parse_manifest_mapping(raw: Mapping[str, Any], package_root: Path) -> Manifest:
    """Build a Manifest from an already loaded mapping.

    Raises:
        ExtensionFailure: A required field is missing or a value is illegal.
    """
    schema_version = raw.get("schemaVersion")
    if schema_version != 1:
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"schemaVersion must be 1, got {schema_version!r}",
        )
    package = raw.get("package")
    if not isinstance(package, dict):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason="package must be a mapping",
        )
    package_id = _required_package_id(package.get("id"))
    version = _required_version(package.get("version"), "package.version")
    display_name = _optional_text(package.get("displayName"), "package.displayName")
    description = _optional_text(package.get("description"), "package.description")

    requires = raw.get("requires") or {}
    if not isinstance(requires, dict):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason="requires must be a mapping",
        )
    requires_host = _optional_specifier(requires.get("host"), "requires.host")
    requires_packages = _package_dependencies(requires.get("packages"))

    extensions = _extensions(raw.get("extensions"), package_root)
    return Manifest(
        schema_version=1,
        package_id=package_id,
        version=version,
        display_name=display_name,
        description=description,
        requires_host=requires_host,
        requires_packages=requires_packages,
        extensions=extensions,
    )


def _required_package_id(value: object) -> str:
    """Require a package.id that is also a safe store filename."""
    if not valid_package_id(value):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"package.id is invalid: {value!r}",
        )
    return value


def _required_identifier(value: object, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None or ".." in value:
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"{field} is invalid: {value!r}",
        )
    return value


def _required_version(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"{field} must be a string",
        )
    try:
        parse_version(value)
    except ValueError as exc:
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"{field}: {exc}",
            cause=exc,
        ) from exc
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"{field} must be a non-empty string",
        )
    return value.strip()


def _optional_specifier(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"{field} must be a version specifier",
        )
    try:
        specifier_matches("0.0.0", value)
    except ValueError as exc:
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"{field}: {exc}",
            cause=exc,
        ) from exc
    return value.strip()


def _package_dependencies(value: object) -> tuple[PackageDependency, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason="requires.packages must be a list",
        )
    dependencies: list[PackageDependency] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason="each requires.packages item must be a mapping",
            )
        package_id = _required_identifier(item.get("id"), "requires.packages.id", _ID_RE)
        if package_id in seen:
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason=f"duplicate package dependency {package_id}",
            )
        seen.add(package_id)
        version = item.get("version")
        if not isinstance(version, str):
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason=f"package {package_id} is missing a version specifier",
            )
        try:
            specifier_matches("0.0.0", version)
        except ValueError as exc:
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason=f"package {package_id} version specifier: {exc}",
                cause=exc,
            ) from exc
        dependencies.append(PackageDependency(package_id=package_id, version=version.strip()))
    return tuple(dependencies)


def _extensions(value: object, package_root: Path) -> tuple[ExtensionDecl, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason="extensions must be a list",
        )
    declarations: list[ExtensionDecl] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason="each extensions item must be a mapping",
            )
        extension_id = _required_identifier(item.get("id"), "extensions.id", _ID_RE)
        if extension_id in seen:
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason=f"duplicate extension id {extension_id}",
            )
        seen.add(extension_id)
        runtime = item.get("runtime")
        if runtime != "backend":
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason=(
                    f"extension {extension_id} runtime must be 'backend', "
                    f"got {runtime!r}"
                ),
            )
        module = item.get("module")
        if not isinstance(module, str) or not module.strip():
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason=f"extension {extension_id} is missing module",
            )
        _ensure_entry_inside_root(package_root, module, extension_id)
        requires = _extension_requires(item.get("requires"), extension_id)
        declarations.append(
            ExtensionDecl(
                id=extension_id,
                runtime="backend",
                module=module.strip(),
                requires=requires,
            )
        )
    known = {item.id for item in declarations}
    for item in declarations:
        for required_id in item.requires:
            if required_id not in known:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_MANIFEST_INVALID,
                    reason=(
                        f"extension {item.id} requires {required_id}, "
                        "which is not in this package"
                    ),
                )
    return tuple(declarations)


def _extension_requires(value: object, extension_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"extension {extension_id} requires must be a list",
        )
    required: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason=f"extension {extension_id} requires items must be mappings",
            )
        required_id = _required_identifier(
            item.get("id"),
            f"{extension_id}.requires.id",
            _ID_RE,
        )
        if required_id in required:
            raise ExtensionFailure(
                StatusCode.EXTENSION_MANIFEST_INVALID,
                reason=f"extension {extension_id} duplicates dependency {required_id}",
            )
        required.append(required_id)
    return tuple(required)


def _ensure_entry_inside_root(package_root: Path, module: str, extension_id: str) -> None:
    relative = Path(module)
    has_bad_parts = any(part in ("", ".", "..") for part in relative.parts)
    has_dotted_segments = any("." in part for part in relative.with_suffix("").parts)
    invalid_entry = (
        relative.is_absolute()
        or relative.suffix != ".py"
        or relative.name == "__init__.py"
        or has_bad_parts
        or has_dotted_segments
    )
    if invalid_entry:
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=(
                f"extension {extension_id} entry must be a .py file under the package "
                f"root (not __init__.py, no dotted path segments): {module}"
            ),
        )
    root = package_root.resolve()
    entry = (root / relative).resolve()
    if not entry.is_relative_to(root):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"extension {extension_id} entry escapes the package root: {module}",
        )
    if not entry.is_file():
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"extension {extension_id} entry file does not exist: {module}",
        )
