# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent-facing evolution draft schema."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from openjiuwen.agent_evolving.protocols import (
    EVOLUTION_SUBJECT_KIND_VALUES,
    EVOLUTION_TARGET_VALUES,
    SIMPLIFY_ACTION_VALUES,
    VALID_SECTIONS,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError

_SUMMARY_LIMIT = 160
MAX_EVOLUTION_REVIEW_PROPOSALS = 3
_VALID_TARGETS = set(EVOLUTION_TARGET_VALUES)
_SIMPLIFY_ACTIONS = set(SIMPLIFY_ACTION_VALUES)
SUPPORTED_EXPERIENCE_SUBJECT_KINDS = frozenset({"skill", "swarm-skill"})


def _validation_error(message: str) -> ValidationError:
    return ValidationError(
        StatusCode.SCHEMA_VALIDATE_INVALID,
        reason=message,
        data="agent-facing evolution draft",
    )


@dataclass(frozen=True)
class EvolutionSubject:
    """Normalized subject envelope for agent-facing evolution drafts."""

    kind: str
    name: str
    scope: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return the stable agent-facing subject payload."""
        payload: dict[str, Any] = {"kind": self.kind, "name": self.name}
        if self.scope:
            payload["scope"] = dict(self.scope)
        return payload


def normalize_evolution_subject_kind(kind: str) -> str:
    """Normalize legacy evolution subject kind aliases."""
    normalized = str(kind).strip()
    if normalized == "team-skill":
        return "swarm-skill"
    return normalized


def supported_experience_subject_kinds() -> set[str]:
    """Return supported evolution experience subject kinds."""
    return set(SUPPORTED_EXPERIENCE_SUBJECT_KINDS)


def normalize_subject(raw: Any, *, allowed_kinds: set[str] | None = None) -> EvolutionSubject:
    """Normalize an agent-facing subject envelope."""
    if not isinstance(raw, dict):
        raise _validation_error("subject must be an object")
    kind = str(raw.get("kind", "")).strip()
    if not kind:
        raise _validation_error("subject.kind is required")
    allowed = allowed_kinds or set(EVOLUTION_SUBJECT_KIND_VALUES)
    normalized_allowed = {normalize_evolution_subject_kind(allowed_kind) for allowed_kind in allowed}
    normalized_kind = normalize_evolution_subject_kind(kind)
    if normalized_kind not in normalized_allowed:
        raise _validation_error(f"subject.kind must be one of: {', '.join(sorted(allowed))}")
    kind = normalized_kind
    name = str(raw.get("name", "")).strip()
    if not name:
        raise _validation_error("subject.name is required")
    scope = raw.get("scope")
    if scope is not None and not isinstance(scope, dict):
        raise _validation_error("subject.scope must be an object")
    return EvolutionSubject(kind=kind, name=name, scope=scope)


@dataclass(frozen=True)
class ExperienceDraft:
    """Base class for agent-facing experience drafts."""

    subject: EvolutionSubject


@dataclass(frozen=True)
class EvolveDraft(ExperienceDraft):
    """Normalized evolve draft with separate approval and persistence views."""

    experiences: list[dict[str, Any]]

    def approval_view(self) -> dict[str, Any]:
        """Return the approval-facing view, including run-scoped evidence refs."""
        return {"experiences": [dict(item) for item in self.experiences]}

    def persistence_view(self) -> dict[str, Any]:
        """Return the persistence-facing view without run-scoped evidence refs."""
        experiences = []
        for item in self.experiences:
            persisted = dict(item)
            persisted.pop("source_refs", None)
            experiences.append(persisted)
        return {"experiences": experiences}


@dataclass(frozen=True)
class SimplifyDraft(ExperienceDraft):
    """Normalized simplify draft."""

    actions: list[dict[str, Any]]

    def approval_view(self) -> dict[str, Any]:
        """Return the approval-facing simplify draft view."""
        return {"actions": [dict(item) for item in self.actions]}

    def persistence_view(self) -> dict[str, Any]:
        """Return the persistence-facing simplify draft view."""
        return {"actions": [dict(item) for item in self.actions]}


def normalize_evolve_draft(
    subject: EvolutionSubject,
    experiences: list[dict[str, Any]],
) -> EvolveDraft:
    """Normalize agent-submitted evolve draft items."""
    if not isinstance(experiences, list) or not experiences:
        raise _validation_error("experiences must be a non-empty list")
    normalized = []
    for index, raw in enumerate(experiences):
        if not isinstance(raw, dict):
            raise _validation_error(f"experience at index {index} must be an object")
        content = str(raw.get("content", "")).strip()
        if not content:
            raise _validation_error("experience content is required")
        target = str(raw.get("target", "body") or "body").strip()
        if target not in _VALID_TARGETS:
            raise _validation_error(f"invalid target: {target}")
        item: dict[str, Any] = {
            "summary": _normalize_summary(raw),
            "content": content,
            "target": target,
            "section": _normalize_section(raw, target),
            "reason": str(raw.get("reason", "") or "").strip(),
        }
        script_filename = _normalize_script_filename(raw.get("script_filename"))
        if script_filename:
            item["script_filename"] = script_filename
        for key in ("script_language", "script_purpose"):
            value = str(raw.get(key, "") or "").strip()
            if value:
                item[key] = value
        refs = _normalize_source_refs(raw.get("source_refs"))
        if refs:
            item["source_refs"] = refs
        normalized.append(item)
    return EvolveDraft(subject=subject, experiences=normalized)


def normalize_simplify_draft(subject: EvolutionSubject, actions: list[dict[str, Any]]) -> SimplifyDraft:
    """Normalize agent-submitted simplify actions."""
    if not isinstance(actions, list) or not actions:
        raise _validation_error("actions must be a non-empty list")
    normalized = []
    for index, raw in enumerate(actions):
        if not isinstance(raw, dict):
            raise _validation_error(f"action at index {index} must be an object")
        action = str(raw.get("action", "")).strip().upper()
        if action not in _SIMPLIFY_ACTIONS:
            raise _validation_error(f"invalid simplify action: {action}")
        record_id = str(raw.get("record_id", "")).strip()
        if not record_id:
            raise _validation_error("record_id is required")
        item: dict[str, Any] = {
            "action": action,
            "record_id": record_id,
            "reason": str(raw.get("reason", "") or "").strip(),
        }
        new_content = str(raw.get("new_content", "") or "").strip()
        merge_remove_ids = _normalize_merge_remove_ids(raw.get("merge_remove_ids") or [], record_id=record_id)
        if action == "REFINE" and not new_content:
            raise _validation_error("REFINE requires new_content")
        if action == "MERGE":
            if not new_content:
                raise _validation_error("MERGE requires new_content")
            if not merge_remove_ids:
                raise _validation_error("MERGE requires merge_remove_ids")
        if new_content and action in {"REFINE", "MERGE"}:
            item["new_content"] = new_content
        if merge_remove_ids:
            item["merge_remove_ids"] = merge_remove_ids
        normalized.append(item)
    return SimplifyDraft(subject=subject, actions=normalized)


def validate_simplify_record_refs(draft: SimplifyDraft, *, existing_record_ids: set[str]) -> None:
    """Validate simplify action record references against persisted record ids."""
    for action in draft.actions:
        record_id = action["record_id"]
        if record_id not in existing_record_ids:
            raise _validation_error(f"record not found: {record_id}")
        for remove_id in action.get("merge_remove_ids", []):
            if remove_id not in existing_record_ids:
                raise _validation_error(f"record not found: {remove_id}")


def _normalize_summary(raw: dict[str, Any]) -> str:
    summary = " ".join(str(raw.get("summary", "")).split())
    if not summary:
        raise _validation_error("experience summary is required")
    if len(summary) > _SUMMARY_LIMIT:
        raise _validation_error(f"experience summary must be at most {_SUMMARY_LIMIT} characters")
    return summary


def _normalize_section(raw: dict[str, Any], target: str) -> str:
    if target == "script":
        return "Scripts"
    default = "Instructions" if target == "description" else "Troubleshooting"
    section = str(raw.get("section", default) or default).strip()
    if section not in VALID_SECTIONS:
        raise _validation_error(f"invalid section: {section}")
    return section


def _normalize_script_filename(value: Any) -> str | None:
    filename = str(value or "").strip()
    if not filename:
        return None
    posix = PurePosixPath(filename)
    windows = PureWindowsPath(filename)
    if filename in {".", ".."} or posix.is_absolute() or windows.is_absolute():
        raise _validation_error("script_filename must be a file name")
    if posix.name != filename or windows.name != filename:
        raise _validation_error("script_filename must be a file name")
    return filename


def _normalize_source_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _validation_error("source_refs must be a list")
    refs = []
    for ref in value:
        normalized_ref = str(ref).strip()
        if normalized_ref:
            refs.append(normalized_ref)
    return refs


def _normalize_merge_remove_ids(raw_ids: list[Any], *, record_id: str) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw_id in raw_ids:
        remove_id = str(raw_id).strip()
        if not remove_id:
            raise _validation_error("merge_remove_ids must not contain empty values")
        if remove_id == record_id:
            raise _validation_error("merge_remove_ids must not contain record_id")
        if remove_id in seen:
            raise _validation_error(f"duplicate merge_remove_id: {remove_id}")
        seen.add(remove_id)
        normalized.append(remove_id)
    return normalized


__all__ = [
    "EvolutionSubject",
    "ExperienceDraft",
    "EvolveDraft",
    "SimplifyDraft",
    "normalize_evolve_draft",
    "normalize_simplify_draft",
    "normalize_subject",
    "validate_simplify_record_refs",
]
