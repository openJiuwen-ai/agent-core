# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Load package command rules and inline them into effective ``rules``.

Engine evaluation consumes effective ``rules`` only. Do not call these loaders
from ``evaluate_tiered_policy``. Each YAML entry carries default ``action``;
this module copies it and does not map ``severity``.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_COMMAND_RULES_CACHE: tuple[str, float, list[dict[str, Any]]] | None = None
_VALID_ACTIONS = frozenset({"ask", "deny", "allow"})


def _package_rules_yaml_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "harness":
            return parent / "resources" / "builtin_rules.yaml"
    return here.parent.parent / "resources" / "builtin_rules.yaml"


def get_package_builtin_rules_path() -> Path:
    """Absolute path of package ``resources/builtin_rules.yaml``."""
    return _package_rules_yaml_path()


def _load_package_yaml() -> dict[str, Any]:
    path = _package_rules_yaml_path()
    if not path.is_file():
        logger.warning(
            "[PermissionEngine] permission.command_rules.yaml_missing package_path=%s",
            path,
        )
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _yaml_action(item: dict[str, Any]) -> str | None:
    action = item.get("action")
    if not (isinstance(action, str) and action.strip()):
        logger.warning(
            "[PermissionEngine] permission.command_rules.missing_action id=%r",
            item.get("id"),
        )
        return None
    action = action.strip().lower()
    if action not in _VALID_ACTIONS:
        logger.warning(
            "[PermissionEngine] permission.command_rules.invalid_action id=%r action=%r",
            item.get("id"),
            action,
        )
        return None
    if action == "allow":
        logger.warning(
            "[PermissionEngine] permission.command_rules.skip id=%r reason=allow_not_allowed",
            item.get("id"),
        )
        return None
    return action


def load_package_command_rules() -> list[dict[str, Any]]:
    """Raw package command rules (default ``action`` lives on each YAML entry)."""
    global _COMMAND_RULES_CACHE
    path = _package_rules_yaml_path()
    if not path.is_file():
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    key = str(path.resolve())
    if _COMMAND_RULES_CACHE is not None:
        ck, mt, rules = _COMMAND_RULES_CACHE
        if ck == key and mt == mtime:
            return rules
    data = _load_package_yaml()
    rules = [r for r in (data.get("rules") or []) if isinstance(r, dict)]
    _COMMAND_RULES_CACHE = (key, mtime, rules)
    return rules


def inline_package_command_rules(
    permissions: dict[str, Any] | None,
) -> dict[str, Any]:
    """Copy package command rules into effective ``rules`` using YAML ``action``."""
    cfg = deepcopy(permissions) if isinstance(permissions, dict) else {}
    existing = cfg.get("rules") if isinstance(cfg.get("rules"), list) else []
    user_rules = [
        dict(r)
        for r in existing
        if isinstance(r, dict) and r.get("layer") != "builtin"
    ]
    package_rules: list[dict[str, Any]] = []
    for raw in load_package_command_rules():
        action = _yaml_action(raw)
        if action is None:
            continue
        item = dict(raw)
        item["layer"] = "builtin"
        item["action"] = action
        package_rules.append(item)
    cfg["rules"] = package_rules + user_rules
    return cfg


__all__ = [
    "get_package_builtin_rules_path",
    "inline_package_command_rules",
    "load_package_command_rules",
]
