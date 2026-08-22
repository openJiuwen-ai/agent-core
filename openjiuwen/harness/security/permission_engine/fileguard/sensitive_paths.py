# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Load package sensitive paths and merge them into ``file_guard.paths``.

Engine evaluation consumes the merged ``paths`` only. YAML entries carry
default ``action``; this module copies it onto read/write/exec and does not
map ``severity``. Overlay cannot widen a package path to allow.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_SENSITIVE_PATHS_CACHE: tuple[str, float, list[dict[str, Any]]] | None = None
_HOME_PREFIX = "~/"
_VALID_ACTIONS = frozenset({"ask", "deny", "allow"})
_AXIS_RANK = {"deny": 0, "ask": 1, "allow": 2}


def _package_rules_yaml_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "harness":
            return parent / "resources" / "builtin_rules.yaml"
    return here.parent.parent.parent / "resources" / "builtin_rules.yaml"


def _load_package_yaml() -> dict[str, Any]:
    path = _package_rules_yaml_path()
    if not path.is_file():
        logger.warning(
            "[PermissionEngine] permission.sensitive_paths.yaml_missing package_path=%s",
            path,
        )
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _expand_home_path(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith(_HOME_PREFIX) or text == "~":
        home = Path.home().as_posix().rstrip("/")
        rest = text[1:].lstrip("/")
        return f"{home}/{rest}" if rest else home
    return text


def _yaml_action(item: dict[str, Any]) -> str | None:
    action = item.get("action")
    if not (isinstance(action, str) and action.strip()):
        logger.warning(
            "[PermissionEngine] permission.sensitive_paths.missing_action id=%r",
            item.get("id"),
        )
        return None
    action = action.strip().lower()
    if action not in _VALID_ACTIONS:
        logger.warning(
            "[PermissionEngine] permission.sensitive_paths.invalid_action id=%r action=%r",
            item.get("id"),
            action,
        )
        return None
    if action == "allow":
        logger.warning(
            "[PermissionEngine] permission.sensitive_paths.skip id=%r reason=allow_not_allowed",
            item.get("id"),
        )
        return None
    return action


def load_package_sensitive_paths() -> list[dict[str, Any]]:
    """file_guard path entries from package ``sensitive_paths`` (YAML ``action``)."""
    global _SENSITIVE_PATHS_CACHE
    path = _package_rules_yaml_path()
    if not path.is_file():
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    key = str(path.resolve())
    if _SENSITIVE_PATHS_CACHE is not None:
        ck, mt, entries = _SENSITIVE_PATHS_CACHE
        if ck == key and mt == mtime:
            return [dict(e) for e in entries]
    data = _load_package_yaml()
    raw_list = data.get("sensitive_paths") or []
    out: list[dict[str, Any]] = []
    if isinstance(raw_list, list):
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            raw_path = item.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            action = _yaml_action(item)
            if action is None:
                continue
            entry = dict(item)
            entry["path"] = _expand_home_path(raw_path)
            entry["match"] = str(item.get("match") or "glob")
            entry["read"] = action
            entry["write"] = action
            entry["exec"] = action
            entry["layer"] = "builtin"
            out.append(entry)
    _SENSITIVE_PATHS_CACHE = (key, mtime, out)
    return [dict(e) for e in out]


def _stricter_axis(left: str, right: str) -> str:
    lv = _AXIS_RANK.get(str(left).strip().lower(), 1)
    rv = _AXIS_RANK.get(str(right).strip().lower(), 1)
    return "deny" if min(lv, rv) == 0 else ("ask" if min(lv, rv) == 1 else "allow")


def _merge_sensitive_paths(
    overlay_paths: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    packaged = load_package_sensitive_paths()
    by_key: dict[str, dict[str, Any]] = {}
    for item in packaged:
        key = str(item.get("path") or "").replace("\\", "/")
        if key:
            by_key[key] = dict(item)
    merged: list[dict[str, Any]] = []
    used: set[str] = set()
    for item in overlay_paths:
        path = str(item.get("path") or "").replace("\\", "/")
        if path in by_key:
            floor = by_key[path]
            used.add(path)
            combined = dict(item)
            for axis in ("read", "write", "exec"):
                overlay_val = str(item.get(axis) or "").strip().lower()
                floor_val = str(floor.get(axis) or "ask").strip().lower()
                if overlay_val == "allow" and floor_val != "allow":
                    combined[axis] = floor_val
                elif overlay_val:
                    combined[axis] = _stricter_axis(overlay_val, floor_val)
                else:
                    combined[axis] = floor_val
            combined["layer"] = "builtin"
            merged.append(combined)
        else:
            merged.append(dict(item))
    for key, item in by_key.items():
        if key not in used:
            merged.append(dict(item))
    return merged


def merge_package_sensitive_paths(
    permissions: dict[str, Any] | None,
    *,
    inject: bool | None = None,
) -> dict[str, Any]:
    """Merge package sensitive paths into ``file_guard.paths`` when enabled."""
    cfg = deepcopy(permissions) if isinstance(permissions, dict) else {}
    fg = cfg.get("file_guard")
    fg_enabled = isinstance(fg, dict) and bool(fg.get("enabled"))
    should_inject = inject if inject is not None else fg_enabled
    if not should_inject:
        return cfg
    if not isinstance(fg, dict):
        fg = {"enabled": True, "paths": []}
        cfg["file_guard"] = fg
    paths = [p for p in (fg.get("paths") or []) if isinstance(p, dict)]
    cfg["file_guard"]["paths"] = _merge_sensitive_paths(paths)
    return cfg


__all__ = [
    "load_package_sensitive_paths",
    "merge_package_sensitive_paths",
]
