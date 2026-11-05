# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Input and output models for ``fs.*`` events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from openjiuwen.core.events.ops_impl.common import OpsError, timeout_is_valid


class FsStatus(StrEnum):
    """Outcome category of one ``fs.*`` call."""

    OK = "ok"
    DENIED = "denied"
    TIMEOUT = "timeout"
    ERROR = "error"


class FsKind(StrEnum):
    """Kind of one filesystem entry."""

    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True, slots=True)
class FsEntry:
    """One directory entry as reported by the filesystem backend."""

    name: str
    path: str
    is_directory: bool
    size: int = 0
    modified_time: str = ""
    suffix: str | None = None


@dataclass(frozen=True, slots=True)
class FsAncestor:
    """One instruction file found above the starting directory."""

    name: str
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class FsReadInput:
    """Arguments of one ``fs.read`` event."""

    path: str
    mode: str = "text"
    head: int | None = None
    tail: int | None = None
    line_range: tuple[int, int] | None = None
    encoding: str = "utf-8"
    chunk_size: int | None = None
    only_read: bool = False
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class FsReadOutput:
    """Result of one ``fs.read`` event."""

    status: FsStatus
    path: str
    content: str | bytes | None = None
    mode: str = "text"
    error: OpsError | None = None


@dataclass(frozen=True, slots=True)
class FsWriteInput:
    """Arguments of one ``fs.write`` event."""

    path: str
    content: str | bytes
    mode: str = "text"
    append: bool = False
    prepend_newline: bool = False
    append_newline: bool = False
    create_if_not_exist: bool = True
    permissions: str = "644"
    encoding: str = "utf-8"
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class FsWriteOutput:
    """Result of one ``fs.write`` event."""

    status: FsStatus
    path: str
    size: int | None = None
    mode: str = "text"
    error: OpsError | None = None


@dataclass(frozen=True, slots=True)
class FsListInput:
    """Arguments of one ``fs.list`` event."""

    path: str
    recursive: bool = False
    max_depth: int | None = None
    sort_by: str = "name"
    sort_descending: bool = False
    file_types: tuple[str, ...] | None = None
    include_files: bool = True
    include_dirs: bool = False
    max_entries: int | None = None
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class FsListOutput:
    """Result of one ``fs.list`` event."""

    status: FsStatus
    root_path: str = ""
    entries: tuple[FsEntry, ...] = ()
    total_count: int = 0
    truncated: bool = False
    error: OpsError | None = None


@dataclass(frozen=True, slots=True)
class FsStatInput:
    """Arguments of one ``fs.stat`` event."""

    path: str
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class FsStatOutput:
    """Result of one ``fs.stat`` event; ``found`` is false when the path is absent."""

    status: FsStatus
    path: str
    found: bool = False
    kind: FsKind | None = None
    size: int | None = None
    modified_time: str | None = None
    error: OpsError | None = None


@dataclass(frozen=True, slots=True)
class FsExistsInput:
    """Arguments of one ``fs.exists`` event."""

    path: str
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class FsExistsOutput:
    """Result of one ``fs.exists`` event."""

    status: FsStatus
    exists: bool = False
    error: OpsError | None = None


@dataclass(frozen=True, slots=True)
class FsAncestorsInput:
    """Arguments of one ``fs.ancestors`` event."""

    names: tuple[str, ...]
    start: str | None = None
    stop_at: str | None = None
    max_files: int = 16
    max_bytes_per_file: int = 65536
    encoding: str = "utf-8"
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class FsAncestorsOutput:
    """Result of one ``fs.ancestors`` event, ordered from the outermost ancestor down."""

    status: FsStatus
    entries: tuple[FsAncestor, ...] = ()
    truncated: bool = False
    skipped: int = 0
    error: OpsError | None = None

_MODES = ("text", "bytes")
_SORT_BY = ("name", "modified_time", "size")


def _has_path(value: object) -> bool:
    """Return whether one path is a non-empty string."""

    return isinstance(value, str) and bool(value.strip())


def _has_mode(value: object) -> bool:
    """Return whether one read or write mode is supported."""

    return isinstance(value, str) and value in _MODES


def _is_positive_int(value: object) -> bool:
    """Return whether one limit is a positive integer."""

    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_optional_int(value: object) -> bool:
    """Return whether one optional field is an integer."""

    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _is_line_range(value: object) -> bool:
    """Return whether one line range is a pair of integers."""

    return value is None or (
        isinstance(value, (tuple, list))
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        and value[0] >= 1
        and value[1] >= value[0]
    )


def _read_window_is_valid(value: FsReadInput) -> bool:
    """Return whether the requested read window is single and text-only."""

    windows = (value.head is not None, value.tail is not None, value.line_range is not None)
    if sum(windows) > 1:
        return False
    return value.mode == "text" or not any(windows)


def validate_read_input(value: object) -> bool:
    """Return whether one ``fs.read`` request can be executed."""

    return (
        isinstance(value, FsReadInput)
        and _has_path(value.path)
        and _has_mode(value.mode)
        and _is_optional_int(value.head)
        and _is_optional_int(value.tail)
        and _is_line_range(value.line_range)
        and _is_optional_int(value.chunk_size)
        and _read_window_is_valid(value)
        and timeout_is_valid(value.timeout_ms)
    )


def validate_write_input(value: object) -> bool:
    """Return whether one ``fs.write`` request can be executed."""

    return (
        isinstance(value, FsWriteInput)
        and _has_path(value.path)
        and _has_mode(value.mode)
        and isinstance(value.content, (str, bytes))
        and isinstance(value.permissions, str)
        and bool(value.permissions)
        and timeout_is_valid(value.timeout_ms)
    )


def validate_list_input(value: object) -> bool:
    """Return whether one ``fs.list`` request can be executed."""

    return (
        isinstance(value, FsListInput)
        and _has_path(value.path)
        and isinstance(value.sort_by, str)
        and value.sort_by in _SORT_BY
        and (value.max_entries is None or _is_positive_int(value.max_entries))
        and (value.max_depth is None or _is_positive_int(value.max_depth))
        and (
            value.file_types is None
            or (
                isinstance(value.file_types, (tuple, list))
                and all(isinstance(item, str) and bool(item) for item in value.file_types)
            )
        )
        and timeout_is_valid(value.timeout_ms)
    )


def validate_stat_input(value: object) -> bool:
    """Return whether one ``fs.stat`` request can be executed."""

    return isinstance(value, FsStatInput) and _has_path(value.path) and timeout_is_valid(value.timeout_ms)


def validate_exists_input(value: object) -> bool:
    """Return whether one ``fs.exists`` request can be executed."""

    return isinstance(value, FsExistsInput) and _has_path(value.path) and timeout_is_valid(value.timeout_ms)


def validate_ancestors_input(value: object) -> bool:
    """Return whether one ``fs.ancestors`` request can be executed."""

    return (
        isinstance(value, FsAncestorsInput)
        and isinstance(value.names, (tuple, list))
        and bool(value.names)
        and all(_has_path(name) for name in value.names)
        and (value.start is None or isinstance(value.start, str))
        and (value.stop_at is None or isinstance(value.stop_at, str))
        and _is_positive_int(value.max_files)
        and _is_positive_int(value.max_bytes_per_file)
        and timeout_is_valid(value.timeout_ms)
    )


def read_failure(request: FsReadInput, status: FsStatus, error: OpsError) -> FsReadOutput:
    """Build the failed result of one ``fs.read`` call."""

    return FsReadOutput(status=status, path=request.path, mode=request.mode, error=error)


def write_failure(request: FsWriteInput, status: FsStatus, error: OpsError) -> FsWriteOutput:
    """Build the failed result of one ``fs.write`` call."""

    return FsWriteOutput(status=status, path=request.path, mode=request.mode, error=error)


def list_failure(request: FsListInput, status: FsStatus, error: OpsError) -> FsListOutput:
    """Build the failed result of one ``fs.list`` call."""

    return FsListOutput(status=status, root_path=request.path, error=error)


def stat_failure(request: FsStatInput, status: FsStatus, error: OpsError) -> FsStatOutput:
    """Build the failed result of one ``fs.stat`` call."""

    return FsStatOutput(status=status, path=request.path, error=error)


def exists_failure(request: FsExistsInput, status: FsStatus, error: OpsError) -> FsExistsOutput:
    """Build the failed result of one ``fs.exists`` call."""

    del request
    return FsExistsOutput(status=status, error=error)


def ancestors_failure(request: FsAncestorsInput, status: FsStatus, error: OpsError) -> FsAncestorsOutput:
    """Build the failed result of one ``fs.ancestors`` call."""

    del request
    return FsAncestorsOutput(status=status, error=error)
