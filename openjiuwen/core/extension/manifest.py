# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Read the package manifest and check that its entry files stay inside the package."""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension.data.json_values import valid_package_id
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.schema.manifest import ExtensionDecl, Manifest, PackageDependency
from openjiuwen.core.extension.versions import parse_version, specifier_matches

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MANIFEST_NAME = "manifest.json"
PACKAGE_TYPE = "extension"


def has_manifest(package_root: Path) -> bool:
    """Return True when the package carries a manifest.json."""

    return (Path(package_root) / MANIFEST_NAME).is_file()


def load_manifest(package_root: Path, *, require_domain: bool = False) -> Manifest:
    """Read ``package_root / manifest.json``.

    Raises:
        ExtensionFailure: The file is missing, unreadable, or the declaration
            does not meet the manifest rules.
    """
    root = package_root.expanduser().resolve()
    path = root / MANIFEST_NAME
    if not path.is_file():
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"{MANIFEST_NAME} is missing under {root}",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"cannot read {path.name}: {exc}",
            cause=exc,
        ) from exc
    if not isinstance(raw, dict):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"{path.name} top level must be a mapping",
        )
    return parse_manifest_mapping(raw, root, require_domain=require_domain)


def parse_manifest_mapping(
    raw: Mapping[str, Any], package_root: Path, *, require_domain: bool = False,
) -> Manifest:
    """Build a Manifest from an already loaded mapping.

    Raises:
        ExtensionFailure: A required field is missing or a value is illegal.
    """
    package_type = raw.get("package_type")
    if package_type != PACKAGE_TYPE:
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"package_type must be {PACKAGE_TYPE!r}, got {package_type!r}",
        )
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
    domain = package.get("domain")
    if not isinstance(domain, str) or not domain.strip():
        if require_domain:
            raise ExtensionFailure(StatusCode.EXTENSION_MANIFEST_INVALID,
                                   reason="package.domain must be a nonempty string")
        domain = None
    package_id = _required_package_id(package.get("id"))
    version = _required_version(package.get("version"), "package.version")
    display = raw.get("display")
    display = display if isinstance(display, Mapping) else {}
    display_name = _localized_text(display.get("name"))
    if display_name is None:
        display_name = _optional_text(package.get("displayName"), "package.displayName")
    if display_name is None:
        display_name = _localized_text(raw.get("display_name"))
    description = _localized_text(display.get("description"))
    if description is None:
        description = _optional_text(package.get("description"), "package.description")
    if description is None:
        description = _localized_text(raw.get("display_description"))

    requires = raw.get("requires") or {}
    if not isinstance(requires, dict):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason="requires must be a mapping",
        )
    requires_host = _optional_specifier(requires.get("host"), "requires.host")
    requires_packages = _package_dependencies(requires.get("packages"))

    extensions = _extensions(raw.get("extensions"), package_root)
    enable = _enable(raw)
    return Manifest(
        schema_version=1,
        package_id=package_id,
        version=version,
        display_name=display_name,
        description=description,
        requires_host=requires_host,
        requires_packages=requires_packages,
        extensions=extensions,
        domain=domain,
        enable=enable,
    )


def _enable(raw: Mapping[str, Any]) -> bool:
    """Read the optional package loading flag without coercing its type."""
    if "enable" not in raw:
        return True
    value = raw["enable"]
    if not isinstance(value, bool):
        raise ExtensionFailure(
            StatusCode.EXTENSION_MANIFEST_INVALID,
            reason=f"enable must be a boolean, got {value!r}",
        )
    return value


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


def _localized_text(value: object) -> str | None:
    """Read an asset manifest's ``{zh, en}`` text without validating its shape."""

    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        for key in ("zh", "en"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


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
