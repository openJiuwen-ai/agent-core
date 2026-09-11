"""Shared portable-path checks for PersonalContext-managed Context trees."""

from __future__ import annotations

import ntpath
import os
import stat
import unicodedata
from pathlib import Path, PurePosixPath

PORTABLE_FORBIDDEN = frozenset('<>:"/\\|?*')
WINDOWS_DEVICE_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
MAX_SEGMENT_CHARS = 80
MAX_SEGMENT_UTF8_BYTES = 240
SEMANTIC_NAME_MAX_CHARS = 20


def _extended_path(path: Path) -> Path:
    """Return a Windows extended path without resolving links."""

    if os.name != "nt":
        return path
    absolute = str(path.absolute())
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path(ntpath.join("\\\\?\\UNC", absolute[2:]))
    drive, tail = ntpath.splitdrive(absolute)
    namespace_root = f"\\\\?\\{drive}\\"
    return Path(ntpath.join(namespace_root, tail.lstrip("\\/")))


def _path_exists(path: Path) -> bool:
    return _extended_path(path).exists()


def _path_is_link_or_reparse(path: Path) -> bool:
    extended = _extended_path(path)
    return path.is_symlink() or is_reparse_point(path) or extended.is_symlink() or is_reparse_point(extended)


def portable_context_segment_is_safe(segment: str) -> bool:
    """Return whether one Context path segment is portable and canonical."""

    try:
        encoded = segment.encode("utf-8")
    except UnicodeError:
        return False
    if not segment or segment in {".", ".."} or unicodedata.normalize("NFC", segment) != segment:
        return False
    if len(segment) > MAX_SEGMENT_CHARS or len(encoded) > MAX_SEGMENT_UTF8_BYTES or segment.endswith((" ", ".")):
        return False
    if any(character in PORTABLE_FORBIDDEN or unicodedata.category(character) in {"Cc", "Cf"} for character in segment):
        return False
    return segment.split(".", 1)[0].casefold() not in WINDOWS_DEVICE_NAMES


def semantic_context_segment_is_safe(segment: str, *, markdown_file: bool) -> bool:
    """Return whether one new user-visible semantic name fits the product limit."""

    if not portable_context_segment_is_safe(segment):
        return False
    name = segment
    if markdown_file:
        if not segment.casefold().endswith(".md") or segment.casefold() == "description.md":
            return False
        name = segment[:-3]
    return bool(name) and len(name) <= SEMANTIC_NAME_MAX_CHARS


def is_reparse_point(path: Path) -> bool:
    """Return whether an existing Windows path is a filesystem reparse point."""

    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def assert_existing_chain_is_plain(path: Path, *, stop: Path) -> None:
    """Reject links and reparse points from ``path`` through the managed root."""

    if _path_is_link_or_reparse(stop):
        raise ValueError("managed Context path traverses a link or reparse point")
    try:
        stop_resolved = stop.resolve(strict=True)
    except OSError as exc:
        raise ValueError("managed Context root is unavailable") from exc
    try:
        relative = path.absolute().relative_to(stop_resolved)
    except ValueError as exc:
        raise ValueError("managed Context path escaped its root") from exc
    current = stop_resolved
    for part in relative.parts:
        current /= part
        if _path_is_link_or_reparse(current):
            raise ValueError("managed Context path traverses a link or reparse point")


def resolve_context_relative_path(root: Path, value: object, *, must_exist: bool) -> Path:
    """Resolve one canonical POSIX path below a plain managed Context root."""

    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Context path must be a POSIX relative path")
    pure = PurePosixPath(value)
    if value in {".", ".."} or pure.is_absolute() or pure.as_posix() != value:
        raise ValueError("Context path is unsafe")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("Context path is unsafe")
    if any(not portable_context_segment_is_safe(part) for part in pure.parts):
        raise ValueError("Context path contains a non-portable segment")
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("managed Context root is unavailable") from exc
    candidate = root_resolved.joinpath(*pure.parts)
    chain_start = candidate if _path_exists(candidate) or _path_is_link_or_reparse(candidate) else candidate.parent
    assert_existing_chain_is_plain(chain_start, stop=root)
    if must_exist and not _path_exists(candidate):
        raise ValueError("Context path is unavailable")
    return candidate


__all__ = [
    "MAX_SEGMENT_CHARS",
    "MAX_SEGMENT_UTF8_BYTES",
    "PORTABLE_FORBIDDEN",
    "SEMANTIC_NAME_MAX_CHARS",
    "assert_existing_chain_is_plain",
    "is_reparse_point",
    "portable_context_segment_is_safe",
    "resolve_context_relative_path",
    "semantic_context_segment_is_safe",
]
