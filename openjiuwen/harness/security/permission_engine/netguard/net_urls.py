# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Load package net_urls and merge them into ``net_guard.urls``.

Engine evaluation consumes the merged ``urls`` dict. Overlay cannot widen a
package deny to allow. ``ask`` is not a net_guard action.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_NET_URLS_CACHE: tuple[str, float, dict[str, str]] | None = None
_VALID_ACTIONS = frozenset({"allow", "deny"})


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
            "[PermissionEngine] permission.net_urls.yaml_missing package_path=%s",
            path,
        )
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _parse_action(value: Any) -> str | None:
    raw: Any = value
    if isinstance(value, dict):
        raw = value.get("action") if value.get("action") is not None else value.get("fetch")
    if not isinstance(raw, str) or not raw.strip():
        return None
    action = raw.strip().lower()
    if action not in _VALID_ACTIONS:
        return None
    return action


def load_package_net_urls() -> dict[str, str]:
    """Package hostname/URL keys mapped to ``allow`` / ``deny``."""
    global _NET_URLS_CACHE
    path = _package_rules_yaml_path()
    if not path.is_file():
        return {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    key = str(path.resolve())
    if _NET_URLS_CACHE is not None:
        ck, mt, entries = _NET_URLS_CACHE
        if ck == key and mt == mtime:
            return dict(entries)
    data = _load_package_yaml()
    raw = data.get("net_urls") or {}
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for pattern, value in raw.items():
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            action = _parse_action(value)
            if action is None:
                logger.warning(
                    "[PermissionEngine] permission.net_urls.skip pattern=%r reason=invalid_action",
                    pattern,
                )
                continue
            out[pattern.strip()] = action
    _NET_URLS_CACHE = (key, mtime, dict(out))
    return dict(out)


def _has_package_net_urls(urls: Any) -> bool:
    if not isinstance(urls, dict):
        return False
    packaged = load_package_net_urls()
    return bool(packaged) and set(packaged).issubset(urls)


def _stricter(left: str, right: str) -> str:
    if left == "deny" or right == "deny":
        return "deny"
    return "allow"


def merge_package_net_urls(
    permissions: dict[str, Any] | None,
    *,
    inject: bool | None = None,
) -> dict[str, Any]:
    """Merge package net_urls into ``net_guard.urls`` when enabled."""
    cfg = deepcopy(permissions) if isinstance(permissions, dict) else {}
    ng = cfg.get("net_guard")
    ng_enabled = isinstance(ng, dict) and bool(ng.get("enabled"))
    should_inject = inject if inject is not None else ng_enabled
    if not should_inject:
        return cfg
    if not isinstance(ng, dict):
        ng = {"enabled": True, "defaults": "allow", "urls": {}}
        cfg["net_guard"] = ng
    urls = ng.get("urls")
    if not isinstance(urls, dict):
        urls = {}
    merged: dict[str, str] = {}
    for pattern, value in urls.items():
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        action = _parse_action(value)
        if action is None:
            continue
        merged[pattern.strip()] = action
    for pattern, floor in load_package_net_urls().items():
        overlay = merged.get(pattern)
        if overlay == "allow" and floor == "deny":
            merged[pattern] = "deny"
        elif overlay is None:
            merged[pattern] = floor
        else:
            merged[pattern] = _stricter(overlay, floor)
    ng["urls"] = merged
    cfg["net_guard"] = ng
    return cfg


__all__ = [
    "load_package_net_urls",
    "merge_package_net_urls",
]
