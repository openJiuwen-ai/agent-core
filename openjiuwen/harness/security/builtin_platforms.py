"""Platform tags for package builtin rules / sensitive paths."""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from typing import Any

_WINDOWS_ALIASES = frozenset({"windows", "win32", "win", "nt"})
_UNIX_ALIASES = frozenset({"unix", "linux", "darwin", "macos", "posix"})
_ALL_ALIASES = frozenset({"all", "*", "any"})

VALID_PLATFORMS = frozenset({"windows", "unix", "all"})


def normalize_builtin_platform(raw: str | None = None) -> str:
    """Map ``sys.platform`` / aliases to ``windows`` | ``unix``."""
    text = (raw if raw is not None else sys.platform).strip().lower()
    if text in _WINDOWS_ALIASES:
        return "windows"
    if text in _UNIX_ALIASES:
        return "unix"
    if sys.platform == "win32":
        return "windows"
    return "unix"


def resolve_active_platforms(platform: str | None = None) -> frozenset[str]:
    """Platforms loaded for a request. ``None`` uses the current OS."""
    return frozenset({normalize_builtin_platform(platform), "all"})


def _declared_platforms(raw: Any) -> frozenset[str]:
    if raw is None:
        return frozenset({"all"})
    values: Iterable[Any]
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        values = raw
    else:
        return frozenset({"all"})
    out: set[str] = set()
    for item in values:
        token = str(item or "").strip().lower()
        if not token or token in _ALL_ALIASES:
            out.add("all")
            continue
        if token in _WINDOWS_ALIASES:
            out.add("windows")
            continue
        if token in _UNIX_ALIASES:
            out.add("unix")
    return frozenset(out) or frozenset({"all"})


def entry_matches_platforms(entry: Mapping[str, Any], active: Iterable[str]) -> bool:
    """True when an entry's ``platforms`` intersects ``active`` (omit / all = any)."""
    declared = _declared_platforms(entry.get("platforms"))
    if "all" in declared:
        return True
    wanted = {str(p).strip().lower() for p in active if str(p).strip()}
    return bool(declared & wanted)


def filter_entries_for_platform(
    entries: Iterable[Mapping[str, Any]],
    *,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    active = resolve_active_platforms(platform)
    return [dict(item) for item in entries if entry_matches_platforms(item, active)]
