# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Canonical subagent type names shared by the menu, lookup, and tool schema.

The model-facing ``task_tool`` description, ``create_subagent`` lookup, and
``subagent_type`` JSON Schema enum must all read the same names. A missing
card name is omitted from the menu rather than silently advertised as
``general-purpose``.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable, Optional

GENERAL_PURPOSE_TYPE = "general-purpose"
_GENERAL_PURPOSE_ALIASES = frozenset({
    GENERAL_PURPOSE_TYPE,
    "general_purpose",
    "general_agent",
    "generalPurpose",
})


def subagent_type_name(spec: Any) -> Optional[str]:
    """Return the lookup name of a subagent spec, or None if it has none.

    Duck-types ``SubAgentConfig.agent_card`` and ``DeepAgent.card`` so a
    duplicate class object from another import path still matches. A present
    but unnamed ``agent_card`` does not hide a named ``card``.
    """
    for card in (getattr(spec, "agent_card", None), getattr(spec, "card", None)):
        name = getattr(card, "name", None)
        if isinstance(name, str):
            stripped = name.strip()
            if stripped:
                return stripped
    return None


def listed_subagent_types(subagents: Optional[Iterable[Any]]) -> list[str]:
    """Return unique, ordered type names that can appear in the tool menu."""
    names: list[str] = []
    seen: set[str] = set()
    for spec in subagents or []:
        name = subagent_type_name(spec)
        if name is None or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def resolved_type_from_parent(parent: Any, requested: str) -> Optional[str]:
    """Resolve ``requested`` through ``parent.resolve_subagent_type`` if real.

    A missing or non-callable resolver (typical of SimpleNamespace test
    doubles) keeps the original name. A resolver that returns ``None`` is an
    explicit rejection. Non-string returns (unittest MagicMock) are ignored
    so Mock parents still spawn with the model-supplied type.
    """
    requested_name = str(requested or "").strip()
    resolver = getattr(parent, "resolve_subagent_type", None)
    if not callable(resolver):
        return requested_name or None
    resolved = resolver(requested_name)
    if resolved is None:
        return None
    if isinstance(resolved, str):
        stripped = resolved.strip()
        return stripped or None
    return requested_name or None


def listed_types_from_parent(parent: Any) -> list[str]:
    """Return ``parent.available_subagent_types()`` when it yields strings."""
    lister = getattr(parent, "available_subagent_types", None)
    if not callable(lister):
        return []
    names: list[str] = []
    try:
        listed = lister()
    except Exception:
        return []
    for name in listed or []:
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def resolve_subagent_type(
    requested: str,
    available: Iterable[str],
) -> Optional[str]:
    """Map a model-supplied type onto a name in ``available``.

    ``general_agent`` / ``general_purpose`` / ``generalPurpose`` resolve to
    ``general-purpose`` only when that name is actually registered.
    """
    requested_name = str(requested or "").strip()
    if not requested_name:
        return None
    available_names = list(available)
    if requested_name in available_names:
        return requested_name
    if (
        requested_name in _GENERAL_PURPOSE_ALIASES
        and GENERAL_PURPOSE_TYPE in available_names
    ):
        return GENERAL_PURPOSE_TYPE
    return None


def format_unknown_subagent_type(
    requested: str,
    available: Iterable[str],
    language: str = "cn",
) -> str:
    """Build a model-visible error that lists the live type names."""
    names = [name for name in available if name]
    if language == "cn":
        listed = "、".join(names) if names else "（无）"
        return (
            f"未知 subagent_type='{requested}'。当前可用：{listed}。"
            "请用列表中的精确名称重试。"
        )
    listed = ", ".join(names) if names else "(none)"
    return (
        f"Unknown subagent_type='{requested}'. Available: {listed}. "
        "Retry with an exact name from the list."
    )


def apply_subagent_type_enum(card: Any, names: Iterable[str]) -> None:
    """Write the live type list onto ``card.input_params.properties.subagent_type``.

    Copies the schema before mutating so a shared provider dict is never
    edited in place. An empty name list drops ``enum`` (free string) rather
    than advertising an empty choice set.
    """
    params = getattr(card, "input_params", None)
    if not isinstance(params, dict):
        return
    params = copy.deepcopy(params)
    properties = params.get("properties")
    if not isinstance(properties, dict):
        return
    spec = properties.get("subagent_type")
    if not isinstance(spec, dict):
        return
    enum_names = [name for name in names if name]
    if enum_names:
        spec["enum"] = enum_names
    else:
        spec.pop("enum", None)
    card.input_params = params


def tool_owner_id_of(agent: Any) -> Optional[str]:
    """Owner id used to qualify this agent's stateful tools."""
    config = getattr(agent, "deep_config", None)
    owner = getattr(config, "tool_owner_id", None)
    if isinstance(owner, str) and owner.strip():
        return owner.strip()
    card = getattr(agent, "card", None)
    card_id = getattr(card, "id", None)
    if isinstance(card_id, str) and card_id.strip():
        return card_id.strip()
    return None
