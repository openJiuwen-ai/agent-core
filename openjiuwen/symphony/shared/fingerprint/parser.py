# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Safe, application-independent parsing of ``SKILL.md`` manifests."""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from openjiuwen.symphony.models import CapabilityDescriptor, CapabilityIO
from openjiuwen.symphony.shared.fingerprint.normalization import (
    normalize_capability_type,
    normalize_io_specs_with_issues,
)
from openjiuwen.symphony.shared.fingerprint.safe_filesystem import open_regular_file_no_follow

_DEFAULT_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_DEFAULT_MAX_FRONTMATTER_DEPTH = 32
_DEFAULT_MAX_FRONTMATTER_ITEMS = 10_000


class _ManifestStructureError(Exception):
    """Internal, item-scoped signal for cyclic or oversized YAML values."""


class _ManifestTooLargeError(Exception):
    """Internal, item-scoped signal for a manifest that exceeds its byte cap."""


@dataclass(frozen=True)
class ManifestDiagnostic:
    """Structured, content-free diagnostic for one manifest."""

    code: str
    message: str
    severity: str = "error"
    path: str = ""
    capability_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""

        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "path": self.path,
            "capability_id": self.capability_id,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ParsedSkillManifest(Mapping[str, Any]):
    """Plain parsed data plus its validated descriptor-compatible view."""

    descriptor: CapabilityDescriptor | None
    inputs: tuple[CapabilityIO, ...] = ()
    outputs: tuple[CapabilityIO, ...] = ()
    category: str = ""
    tags: tuple[str, ...] = ()
    semantic_content: str = ""
    entrypoint_content_hash: str = ""
    body: str = ""
    frontmatter: Mapping[str, Any] = field(default_factory=dict)
    entrypoint: str = "SKILL.md"
    diagnostics: tuple[ManifestDiagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether parsing produced a usable descriptor."""

        return self.descriptor is not None

    @property
    def capability_id(self) -> str:
        """Return the parsed ID, or an empty string for a failed manifest."""

        return self.descriptor.capability_id if self.descriptor is not None else ""

    def to_descriptor_data(self) -> dict[str, Any] | None:
        """Return the public descriptor fields without private source text."""

        if self.descriptor is None:
            return None
        return self.descriptor.model_dump(mode="json", exclude={"semantic_content"})

    def to_dict(self) -> dict[str, Any]:
        """Return parser output as a JSON-safe public view.

        The full manifest remains available on ``semantic_content`` for the
        extraction pipeline, but it is intentionally excluded from this
        serializable view because it may contain caller-owned instructions or
        frontmatter credentials.
        """

        descriptor_data = self.to_descriptor_data() or {}
        return {
            **descriptor_data,
            "inputs": [item.model_dump(mode="json") for item in self.inputs],
            "outputs": [item.model_dump(mode="json") for item in self.outputs],
            "category": self.category,
            "tags": list(self.tags),
            "entrypoint": self.entrypoint,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


class SkillManifestParser:
    """Parse a ``SKILL.md`` without consulting application-global state."""

    def __init__(
        self,
        *,
        max_manifest_bytes: int = _DEFAULT_MAX_MANIFEST_BYTES,
        max_frontmatter_depth: int = _DEFAULT_MAX_FRONTMATTER_DEPTH,
        max_frontmatter_items: int = _DEFAULT_MAX_FRONTMATTER_ITEMS,
    ) -> None:
        if not all(
            isinstance(value, int) and value > 0
            for value in (max_manifest_bytes, max_frontmatter_depth, max_frontmatter_items)
        ):
            _raise_parser_config_error("manifest parser limits must be positive integers")
        self.max_manifest_bytes = max_manifest_bytes
        self.max_frontmatter_depth = max_frontmatter_depth
        self.max_frontmatter_items = max_frontmatter_items

    def parse(
        self,
        entrypoint: Path | str,
        *,
        root: Path | str | None = None,
        capability_id_hint: str | None = None,
        capability_type: str = "skill",
        source: str = "skill-folder",
        display_path: str | None = None,
    ) -> ParsedSkillManifest:
        """Parse one entrypoint, returning diagnostics instead of item-level errors."""

        path = Path(entrypoint)
        if _looks_like_directory(path):
            path = path / "SKILL.md"
        shown_path = display_path or _safe_display_path(path, root)
        path_error = _validate_entrypoint(path, root)
        if path_error is not None:
            return _failed_manifest(path_error, shown_path)
        try:
            if path.lstat().st_size > self.max_manifest_bytes:
                return _failed_manifest(
                    "SKILL.md exceeds the configured manifest size limit",
                    shown_path,
                    code="manifest_too_large",
                )
        except OSError:
            return _failed_manifest(
                "manifest could not be inspected before reading",
                shown_path,
                code="manifest_read_failed",
            )

        try:
            manifest_read = _read_manifest_text(
                path,
                self.max_manifest_bytes,
                root=Path(root) if root is not None else None,
            )
        except _ManifestTooLargeError:
            return _failed_manifest(
                "SKILL.md exceeds the configured manifest size limit",
                shown_path,
                code="manifest_too_large",
            )
        except (OSError, UnicodeError):
            return _failed_manifest(
                "manifest could not be read as UTF-8 text",
                shown_path,
                code="manifest_read_failed",
            )
        if manifest_read is None:
            return _failed_manifest(
                "SKILL.md entrypoint changed while it was being read",
                shown_path,
                code="manifest_read_failed",
            )
        text, entrypoint_content_hash = manifest_read

        frontmatter_text, body, frontmatter_status = _split_frontmatter(text)
        diagnostics: list[ManifestDiagnostic] = []
        if frontmatter_status == "missing":
            diagnostics.append(
                ManifestDiagnostic(
                    code="missing_frontmatter",
                    message="SKILL.md does not start with YAML frontmatter",
                    severity="warning",
                    path=shown_path,
                )
            )
            raw_frontmatter: object = {}
        elif frontmatter_status == "unterminated":
            return _failed_manifest(
                "SKILL.md YAML frontmatter is not terminated",
                shown_path,
                code="invalid_frontmatter",
                semantic_content=text,
                body=text,
            )
        else:
            try:
                raw_frontmatter = _load_unique_yaml(frontmatter_text)
            except (RecursionError, yaml.YAMLError) as exc:
                return ParsedSkillManifest(
                    descriptor=None,
                    semantic_content=text,
                    body=body,
                    entrypoint=shown_path,
                    diagnostics=(
                        ManifestDiagnostic(
                            code="invalid_frontmatter",
                            message="SKILL.md YAML frontmatter could not be parsed",
                            path=shown_path,
                            details=_yaml_error_details(exc),
                        ),
                    ),
                )

        if raw_frontmatter is None:
            raw_frontmatter = {}
        if not isinstance(raw_frontmatter, Mapping):
            return ParsedSkillManifest(
                descriptor=None,
                semantic_content=text,
                body=body,
                entrypoint=shown_path,
                diagnostics=(
                    ManifestDiagnostic(
                        code="invalid_frontmatter",
                        message="SKILL.md YAML frontmatter must be a mapping",
                        path=shown_path,
                    ),
                ),
            )

        try:
            frontmatter = _to_json_mapping(
                raw_frontmatter,
                max_depth=self.max_frontmatter_depth,
                max_items=self.max_frontmatter_items,
            )
        except (_ManifestStructureError, RecursionError):
            return ParsedSkillManifest(
                descriptor=None,
                semantic_content=text,
                body=body,
                entrypoint=shown_path,
                diagnostics=(
                    ManifestDiagnostic(
                        code="unsafe_frontmatter_structure",
                        message="SKILL.md YAML frontmatter is cyclic, too deep, or too large",
                        path=shown_path,
                    ),
                ),
            )
        explicit_id = _first_nonempty(frontmatter, "capability_id", "id")
        raw_name = _string_value(frontmatter.get("name"))
        unsafe_explicit_id = bool(explicit_id) and not _is_safe_capability_id(explicit_id)
        unsafe_derived_id = not explicit_id and bool(raw_name) and _contains_path_traversal(raw_name)
        if unsafe_explicit_id or unsafe_derived_id:
            return ParsedSkillManifest(
                descriptor=None,
                semantic_content=text,
                body=body,
                frontmatter=frontmatter,
                entrypoint=shown_path,
                diagnostics=(
                    ManifestDiagnostic(
                        code="unsafe_capability_id",
                        message="capability ID contains path traversal or path separators",
                        path=shown_path,
                    ),
                ),
            )

        generated_id = _slugify_capability_id(raw_name or capability_id_hint or path.parent.name)
        capability_id = explicit_id or generated_id
        if not capability_id:
            return _failed_manifest(
                "capability ID could not be derived from the manifest or folder",
                shown_path,
                code="missing_capability_id",
                semantic_content=text,
                body=body,
            )

        name = raw_name or capability_id
        description = _string_value(frontmatter.get("description"))
        parsed_type = normalize_capability_type(
            frontmatter.get("capability_type"),
            default=normalize_capability_type(capability_type),
        )
        inputs, input_issues = normalize_io_specs_with_issues(frontmatter.get("inputs"), direction="input")
        outputs, output_issues = normalize_io_specs_with_issues(frontmatter.get("outputs"), direction="output")
        category = _string_value(frontmatter.get("category"))
        tags = _normalize_tags(frontmatter.get("tags"))

        for direction, issues in (("inputs", input_issues), ("outputs", output_issues)):
            diagnostics.extend(
                ManifestDiagnostic(
                    code=issue.code,
                    message=issue.message,
                    severity="warning",
                    path=shown_path,
                    capability_id=capability_id,
                    details={"direction": direction, "index": issue.index},
                )
                for issue in issues
            )

        metadata: dict[str, Any] = {"entrypoint": shown_path}
        version = _string_value(frontmatter.get("version"))
        if version:
            metadata["version"] = version

        try:
            descriptor = CapabilityDescriptor(
                capability_id=capability_id,
                capability_type=parsed_type,
                name=name,
                description=description,
                source=source,
                available=_to_bool(frontmatter.get("available"), default=True),
                inputs=inputs,
                outputs=outputs,
                classification=category,
                tags=tags,
                semantic_content=text,
                metadata=metadata,
            )
            # ``semantic_content`` is source material rather than a label. The
            # shared model trims ordinary string fields, so restore the exact
            # UTF-8 SKILL.md text after the remaining fields have validated.
            descriptor = descriptor.model_copy(update={"semantic_content": text})
        except (TypeError, ValidationError):
            return ParsedSkillManifest(
                descriptor=None,
                inputs=inputs,
                outputs=outputs,
                category=category,
                tags=tags,
                semantic_content=text,
                body=body,
                frontmatter=frontmatter,
                entrypoint=shown_path,
                diagnostics=(
                    *diagnostics,
                    ManifestDiagnostic(
                        code="invalid_descriptor",
                        message="parsed manifest failed CapabilityDescriptor validation",
                        path=shown_path,
                        capability_id=capability_id,
                    ),
                ),
            )

        return ParsedSkillManifest(
            descriptor=descriptor,
            inputs=inputs,
            outputs=outputs,
            category=category,
            tags=tags,
            semantic_content=text,
            entrypoint_content_hash=entrypoint_content_hash,
            body=body,
            frontmatter=frontmatter,
            entrypoint=shown_path,
            diagnostics=tuple(diagnostics),
        )


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate mapping key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_unique_yaml(content: str) -> object:
    loader = _UniqueKeySafeLoader(content)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def _split_frontmatter(text: str) -> tuple[str, str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return "", text, "missing"
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") in {"---", "..."}:
            body_start = index + 1
            return "".join(lines[1:index]), "".join(lines[body_start:]), "ok"
    return "", text, "unterminated"


def _validate_entrypoint(path: Path, root: Path | str | None) -> str | None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return "SKILL.md entrypoint does not exist or cannot be inspected"
    if stat.S_ISLNK(mode):
        return "symlinked SKILL.md entrypoints are not allowed"
    if not stat.S_ISREG(mode):
        return "SKILL.md entrypoint is not a regular file"
    if root is None:
        return None

    root_path = Path(root)
    try:
        root_mode = root_path.lstat().st_mode
    except OSError:
        return "scan root does not exist or cannot be inspected"
    if stat.S_ISLNK(root_mode):
        return "symlinked scan roots are not allowed"
    if not stat.S_ISDIR(root_mode):
        return "scan root is not a directory"

    root_absolute = Path(os.path.abspath(root_path))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError:
        return "SKILL.md entrypoint escapes the explicit scan root"

    current = root_absolute
    for part in relative.parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return "symlink traversal below the scan root is not allowed"
        except OSError:
            return "SKILL.md path cannot be inspected safely"
    return None


def _looks_like_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _read_manifest_text(path: Path, max_bytes: int, *, root: Path | None) -> tuple[str, str] | None:
    file_descriptor = open_regular_file_no_follow(path, root=root)
    if file_descriptor is None:
        return None
    try:
        with os.fdopen(file_descriptor, "rb", closefd=False) as stream:
            data = stream.read(max_bytes + 1)
    finally:
        os.close(file_descriptor)
    if len(data) > max_bytes:
        raise _ManifestTooLargeError
    return data.decode("utf-8-sig"), hashlib.sha256(data).hexdigest()


def _failed_manifest(
    message: str,
    path: str,
    *,
    code: str = "invalid_entrypoint",
    semantic_content: str = "",
    body: str = "",
) -> ParsedSkillManifest:
    return ParsedSkillManifest(
        descriptor=None,
        semantic_content=semantic_content,
        body=body,
        entrypoint=path,
        diagnostics=(ManifestDiagnostic(code=code, message=message, path=path),),
    )


def _safe_display_path(path: Path, root: Path | str | None) -> str:
    if root is None:
        return path.name
    try:
        return Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root))).as_posix()
    except ValueError:
        return path.name


def _first_nonempty(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _string_value(payload.get(key))
        if value:
            return value
    return ""


def _string_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    return ""


def _slugify_capability_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE)
    normalized = normalized.strip("-._")
    return normalized if _is_safe_capability_id(normalized) else ""


def _is_safe_capability_id(value: str) -> bool:
    if not value or any(unicodedata.category(character).startswith("C") for character in value):
        return False
    if Path(value).is_absolute() or value.startswith(("/", "\\")):
        return False
    parts = re.split(r"[/\\]+", value)
    return len(parts) == 1 and parts[0] not in {".", ".."}


def _contains_path_traversal(value: str) -> bool:
    if Path(value).is_absolute() or value.startswith(("/", "\\")):
        return True
    if re.match(r"^[a-zA-Z]:[/\\]", value):
        return True
    return ".." in re.split(r"[/\\]+", value)


def _normalize_tags(raw_value: object) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    if isinstance(raw_value, str):
        values: Sequence[object] = re.split(r"[,;]", raw_value)
    elif isinstance(raw_value, Sequence) and not isinstance(raw_value, (bytes, bytearray)):
        values = raw_value
    else:
        values = (raw_value,)

    tags: list[str] = []
    seen: set[str] = set()
    for item in values:
        tag = _string_value(item)
        key = tag.casefold()
        if tag and key not in seen:
            tags.append(tag)
            seen.add(key)
    return tuple(tags)


def _to_bool(raw_value: object, *, default: bool) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        lowered = raw_value.strip().casefold()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    if isinstance(raw_value, int) and raw_value in {0, 1}:
        return bool(raw_value)
    return default


def _to_json_mapping(
    value: Mapping[object, object],
    *,
    max_depth: int,
    max_items: int,
) -> dict[str, Any]:
    state = {"items": 0}
    converted = _to_json_value(
        value,
        ancestors=set(),
        depth=0,
        max_depth=max_depth,
        max_items=max_items,
        state=state,
    )
    if not isinstance(converted, dict):
        raise _ManifestStructureError
    return converted


def _to_json_value(
    value: object,
    *,
    ancestors: set[int],
    depth: int,
    max_depth: int,
    max_items: int,
    state: dict[str, int],
) -> Any:
    if depth > max_depth:
        raise _ManifestStructureError
    state["items"] += 1
    if state["items"] > max_items:
        raise _ManifestStructureError
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping | Sequence) and not isinstance(value, (str, bytes, bytearray)):
        identity = id(value)
        if identity in ancestors:
            raise _ManifestStructureError
        descendants = {*ancestors, identity}
        if isinstance(value, Mapping):
            return {
                str(key): _to_json_value(
                    item,
                    ancestors=descendants,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    state=state,
                )
                for key, item in value.items()
            }
        return [
            _to_json_value(
                item,
                ancestors=descendants,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                state=state,
            )
            for item in value
        ]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _yaml_error_details(error: BaseException) -> dict[str, Any]:
    mark = getattr(error, "problem_mark", None)
    if mark is None:
        return {"error_type": type(error).__name__}
    return {
        "error_type": type(error).__name__,
        "line": int(mark.line) + 1,
        "column": int(mark.column) + 1,
    }


def _raise_parser_config_error(reason: str) -> NoReturn:
    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.exception.errors import build_error

    raise build_error(StatusCode.COMPONENT_SYMPHONY_CONFIG_ERROR, reason=reason)


__all__ = [
    "ManifestDiagnostic",
    "ParsedSkillManifest",
    "SkillManifestParser",
]
