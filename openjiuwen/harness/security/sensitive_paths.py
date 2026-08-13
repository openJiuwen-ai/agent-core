"""Package builtin sensitive paths (file_guard), loaded from builtin_rules.yaml."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.harness.security.builtin_platforms import filter_entries_for_platform
from openjiuwen.harness.security.tiered_policy import get_package_builtin_rules_path

logger = logging.getLogger(__name__)

_CACHE: tuple[str, float, list[dict[str, Any]]] | None = None
_VALID_ACTIONS = frozenset({"ask", "deny"})


def _expand_path_pattern(raw: str, home: Path | None = None) -> str:
    text = (raw or "").strip().replace("\\", "/")
    if not text.startswith("~/") and text != "~":
        return text
    root = (home or Path.home()).resolve()
    rest = "" if text == "~" else text[2:]
    if not rest:
        return root.as_posix().rstrip("/") + "/**"
    return f"{root.as_posix().rstrip('/')}/{rest.lstrip('/')}"


def _entry_from_raw(raw: dict[str, Any], *, home: Path | None = None) -> dict[str, Any] | None:
    path = raw.get("path")
    action = str(raw.get("action") or "").strip().lower()
    if not isinstance(path, str) or not path.strip() or action not in _VALID_ACTIONS:
        return None
    match = str(raw.get("match") or "glob").strip().lower() or "glob"
    expanded = _expand_path_pattern(path, home=home)
    entry = {
        "id": raw.get("id"),
        "path": expanded,
        "match": match,
        "read": action,
        "write": action,
        "exec": action,
        "layer": "builtin",
    }
    if raw.get("platforms") is not None:
        entry["platforms"] = raw.get("platforms")
    return entry


def get_builtin_sensitive_path_entries(
    *,
    home: Path | None = None,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    """Return file_guard path dicts (uniform axes) from package yaml ``sensitive_paths``."""
    global _CACHE
    path = get_package_builtin_rules_path()
    if not path.is_file():
        logger.warning("[PermissionEngine] builtin_sensitive_paths.missing path=%s", path)
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    # home-dependent expansion: include home in cache key
    home_key = str((home or Path.home()).resolve())
    key = f"{path.resolve()}|{home_key}"
    if _CACHE is not None:
        ck, mt, entries = _CACHE
        if ck == key and mt == mtime:
            return filter_entries_for_platform(entries, platform=platform)
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out: list[dict[str, Any]] = []
    for raw in data.get("sensitive_paths") or []:
        if not isinstance(raw, dict):
            continue
        entry = _entry_from_raw(raw, home=home)
        if entry:
            out.append(entry)
    _CACHE = (key, mtime, out)
    return filter_entries_for_platform(out, platform=platform)
