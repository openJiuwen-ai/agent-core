# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Minimal agent-facing evolution subject schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_SUPPORTED_SUBJECT_KINDS = frozenset({"skill", "team-skill", "swarm-skill"})


@dataclass(frozen=True)
class EvolutionSubject:
    """Normalized subject envelope for agent-facing evolution operations."""

    kind: str
    name: str
    scope: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return the stable agent-facing subject payload."""
        payload: dict[str, Any] = {"kind": self.kind, "name": self.name}
        if self.scope is not None:
            payload["scope"] = dict(self.scope)
        return payload


def normalize_evolution_subject_kind(kind: str) -> str:
    """Normalize legacy evolution subject kind aliases."""
    normalized = str(kind).strip()
    if normalized == "team-skill":
        return "swarm-skill"
    return normalized


def normalize_subject(raw: Any, *, allowed_kinds: set[str] | None = None) -> EvolutionSubject:
    """Normalize an agent-facing subject envelope."""
    if not isinstance(raw, dict):
        raise ValueError("subject must be an object")
    kind = str(raw.get("kind", "")).strip()
    if not kind:
        raise ValueError("subject.kind is required")

    allowed = allowed_kinds or set(_SUPPORTED_SUBJECT_KINDS)
    normalized_allowed = {normalize_evolution_subject_kind(allowed_kind) for allowed_kind in allowed}
    normalized_kind = normalize_evolution_subject_kind(kind)
    if normalized_kind not in normalized_allowed:
        raise ValueError(f"subject.kind must be one of: {', '.join(sorted(allowed))}")

    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("subject.name is required")

    scope = raw.get("scope")
    if scope is not None and not isinstance(scope, dict):
        raise ValueError("subject.scope must be an object when provided")

    return EvolutionSubject(kind=normalized_kind, name=name, scope=scope)


__all__ = [
    "EvolutionSubject",
    "normalize_evolution_subject_kind",
    "normalize_subject",
]
