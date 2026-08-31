# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Action group definition loading for member optimization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.core.common.logging import logger
from openjiuwen.rsi.harness_rsi.member_optimizer.action_groups.policy import (
    filter_action_definitions,
)
from openjiuwen.rsi.harness_rsi.schema import ActionDefinition

_BUILTIN_GROUP_OPERATIONS: dict[str, tuple[str, ...]] = {
    "prompt": ("add", "modify", "remove"),
    "rail": ("add", "modify", "remove"),
    "skill": ("add", "modify", "remove"),
    "tool": ("add", "modify", "remove"),
}


def _builtin_action_definitions(group: str) -> list[ActionDefinition]:
    """Build the executable definitions for one symbolic built-in group."""
    return [
        ActionDefinition(
            name=f"{group}_{operation}",
            group=group,
            operation=operation,
            function=f"{operation}_{group}",
            purpose=f"{operation.title()} a package-local {group} surface",
            requires_search=False,
            is_destructive=operation == "remove",
        )
        for operation in _BUILTIN_GROUP_OPERATIONS[group]
    ]


def load_action_definitions(
    config_paths: list[str],
) -> list[ActionDefinition]:
    """Load policy-compatible ActionDefinition objects from config files.

    Config files may document supported action groups, but they cannot expand
    the executable policy beyond the current Auto Harness package surfaces.
    """
    definitions: list[ActionDefinition] = []

    for config_path in config_paths:
        if not config_path:
            continue
        symbolic_group = str(config_path).strip().lower()
        if symbolic_group in _BUILTIN_GROUP_OPERATIONS:
            definitions.extend(_builtin_action_definitions(symbolic_group))
            continue
        path = Path(config_path).expanduser().resolve()
        if not path.is_file():
            continue

        try:
            data = _load_json_or_yaml(path)
        except (OSError, json.JSONDecodeError, yaml.YAMLError, TypeError) as exc:
            logger.warning("failed to load action definitions from %s: %s", path, exc)
            data = {}

        actions = data.get("actions", [])
        for action_data in actions:
            if not isinstance(action_data, dict):
                continue
            definition: ActionDefinition | None = None
            try:
                definition = ActionDefinition(
                    name=action_data.get("name", ""),
                    group=action_data.get("group", ""),
                    operation=action_data.get("operation", ""),
                    function=action_data.get("function", ""),
                    purpose=action_data.get("purpose", ""),
                    optimizable_modules=action_data.get("optimizable_modules", []),
                    requires_search=action_data.get("requires_search", False),
                    requires_install=action_data.get("requires_install", False),
                    dependency_resources=action_data.get("dependency_resources", {}),
                    parameters_schema=action_data.get("parameters_schema", {}),
                    is_destructive=action_data.get("is_destructive", False),
                    validation_rules=action_data.get("validation_rules", []),
                )
            except (TypeError, ValueError) as exc:
                logger.warning("ignored invalid action definition from %s: %s", path, exc)
            if definition is not None:
                definitions.append(definition)

    return filter_action_definitions(definitions)


def _load_json_or_yaml(path: Path) -> dict[str, Any]:
    """Load a file as YAML or JSON."""
    with open(path, encoding="utf-8") as f:
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(f) or {}
        return json.load(f)


__all__ = [
    "load_action_definitions",
]
