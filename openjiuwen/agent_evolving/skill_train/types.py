# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Typed payloads exchanged across skill_train pipeline stages.

Serialization is driven by field-spec tables so callers can keep using
plain dicts while typed objects stay the in-process contract. Gate and
batch types are re-exported for a single import path.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Callable, Literal, Mapping, Sequence

from openjiuwen.agent_evolving.skill_train.datasets.base import (  # noqa: F401  # pylint: disable=unused-import
    BatchSpec,
)
from openjiuwen.agent_evolving.skill_train.gate import (  # noqa: F401  # pylint: disable=unused-import
    GateAction,
    GateResult,
    GateState,
)

EditOp = Literal["append", "insert_after", "replace", "delete"]
SourceKind = Literal["failure", "success"]

_EMPTY = ""


def _text(value: Any, fallback: str = _EMPTY) -> str:
    return fallback if value is None else str(value)


def _intish(value: Any, fallback: int = 0) -> int:
    if value is None or value is False:
        return fallback
    return int(value)


def _floatish(value: Any, fallback: float = 0.0) -> float:
    if value is None or value is False:
        return fallback
    return float(value)


def _present(value: Any) -> bool:
    return value is not None and value != _EMPTY


@dataclass(frozen=True, slots=True)
class _CodecField:
    key: str
    decode: Callable[[Any], Any]
    always: bool = False


def _decode_row(raw: Mapping[str, Any], table: Sequence[_CodecField]) -> dict[str, Any]:
    return {item.key: item.decode(raw.get(item.key)) for item in table}


def _encode_row(
    obj: Any,
    table: Sequence[_CodecField],
    *,
    required: Sequence[str] = (),
) -> dict[str, Any]:
    must = set(required)
    encoded: dict[str, Any] = {}
    for item in table:
        value = getattr(obj, item.key)
        if item.key in must or item.always or _present(value):
            encoded[item.key] = value
    return encoded


_EDIT_TABLE: tuple[_CodecField, ...] = (
    _CodecField("op", lambda v: v or "append", always=True),
    _CodecField("content", _text, always=True),
    _CodecField("target", _text),
    _CodecField("support_count", lambda v: v),
    _CodecField("source_type", lambda v: v),
    _CodecField("merge_level", lambda v: v),
    _CodecField("update_origin", _text),
    _CodecField("update_target", _text),
)


@dataclass
class FailureSummaryEntry:
    """Aggregated failure category emitted by error analysts."""

    failure_type: str
    count: int = 0
    description: str = _EMPTY

    @classmethod
    def from_dict(cls, payload: dict) -> FailureSummaryEntry:
        kind = _text(payload.get("failure_type"))
        times = _intish(payload.get("count"))
        detail = _text(payload.get("description"))
        return cls(failure_type=kind, count=times, description=detail)

    def to_dict(self) -> dict:
        return dict(
            failure_type=self.failure_type,
            count=self.count,
            description=self.description,
        )


@dataclass
class Edit:
    """One structural change applied to a skill document."""

    op: EditOp
    content: str = _EMPTY
    target: str = _EMPTY
    support_count: int | None = None
    source_type: SourceKind | None = None
    merge_level: int | None = None
    update_origin: str = _EMPTY
    update_target: str = _EMPTY

    @classmethod
    def from_dict(cls, payload: dict) -> Edit:
        return cls(**_decode_row(payload, _EDIT_TABLE))

    def to_dict(self) -> dict:
        return _encode_row(self, _EDIT_TABLE, required=("op", "content"))


def _as_edit(value: Any) -> Edit:
    if isinstance(value, Edit):
        return value
    return Edit.from_dict(value)


@dataclass
class Patch:
    """Ordered edit bundle plus selection rationale."""

    edits: list[Edit] = field(default_factory=list)
    reasoning: str = _EMPTY
    ranking_details: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, payload: dict) -> Patch:
        raw_edits = payload.get("edits") or ()
        edits = [_as_edit(item) for item in raw_edits]
        return cls(
            edits=edits,
            reasoning=_text(payload.get("reasoning")),
            ranking_details=payload.get("ranking_details"),
        )

    def to_dict(self) -> dict:
        body: dict[str, Any] = {
            "reasoning": self.reasoning,
            "edits": [
                item.to_dict() if isinstance(item, Edit) else item for item in self.edits
            ],
        }
        if self.ranking_details is not None:
            body["ranking_details"] = self.ranking_details
        return body


@dataclass
class RawPatch:
    """Reflect-stage analyst payload with provenance metadata."""

    patch: Patch
    source_type: SourceKind = "failure"
    batch_size: int = 0
    failure_summary: list[FailureSummaryEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict | None) -> RawPatch | None:
        if payload is None:
            return None
        nested = payload.get("patch")
        if nested is None:
            body = payload
        elif isinstance(nested, dict):
            body = nested
        else:
            return None
        summary = [
            FailureSummaryEntry.from_dict(row)
            for row in (payload.get("failure_summary") or ())
            if isinstance(row, dict)
        ]
        return cls(
            patch=Patch.from_dict(body),
            source_type=payload.get("source_type") or "failure",
            batch_size=_intish(payload.get("batch_size")),
            failure_summary=summary,
        )

    def to_dict(self) -> dict:
        packed: dict[str, Any] = {"patch": self.patch.to_dict()}
        packed["source_type"] = self.source_type
        packed["batch_size"] = self.batch_size
        rows = self.failure_summary
        if rows:
            packed["failure_summary"] = list(map(FailureSummaryEntry.to_dict, rows))
        return packed


_CORE_ROLLOUT = frozenset({"id", "hard", "soft", "extras"})


@dataclass
class RolloutResult:
    """Per-episode evaluation record with env-specific spillover in ``extras``."""

    id: str
    hard: float
    soft: float
    n_turns: int = 0
    fail_reason: str = _EMPTY
    task_type: str = _EMPTY
    task_description: str = _EMPTY
    predicted_answer: str = _EMPTY
    question: str = _EMPTY
    reference_text: str = _EMPTY
    target_system_prompt: str = _EMPTY
    target_user_prompt: str = _EMPTY
    spreadsheet_preview: str = _EMPTY
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def _known_keys(cls) -> frozenset[str]:
        names = {item.name for item in fields(cls)}
        names.discard("extras")
        return frozenset(names)

    @classmethod
    def _optional_attr_names(cls) -> tuple[str, ...]:
        return tuple(item.name for item in fields(cls) if item.name not in _CORE_ROLLOUT)

    @classmethod
    def from_dict(cls, payload: dict) -> RolloutResult:
        known = cls._known_keys()
        spill = {key: value for key, value in payload.items() if key not in known}
        return cls(
            id=_text(payload.get("id")),
            hard=_floatish(payload.get("hard")),
            soft=_floatish(payload.get("soft")),
            n_turns=_intish(payload.get("n_turns")),
            fail_reason=_text(payload.get("fail_reason")),
            task_type=_text(payload.get("task_type")),
            task_description=_text(payload.get("task_description")),
            predicted_answer=_text(payload.get("predicted_answer")),
            question=_text(payload.get("question")),
            reference_text=_text(payload.get("reference_text")),
            target_system_prompt=_text(payload.get("target_system_prompt")),
            target_user_prompt=_text(payload.get("target_user_prompt")),
            spreadsheet_preview=_text(payload.get("spreadsheet_preview")),
            extras=spill,
        )

    def to_dict(self) -> dict:
        body: dict[str, Any] = {"id": self.id, "hard": self.hard, "soft": self.soft}
        for attr in self._optional_attr_names():
            value = getattr(self, attr)
            if value:
                body[attr] = value
        body.update(self.extras)
        return body


_SLOW_TABLE: tuple[_CodecField, ...] = (
    _CodecField("reasoning", _text, always=True),
    _CodecField("slow_update_content", _text, always=True),
    _CodecField("action", _text),
    _CodecField("time_s", lambda v: v),
    _CodecField("prev_hard", lambda v: v),
    _CodecField("curr_hard", lambda v: v),
    _CodecField("selection_hard", lambda v: v),
    _CodecField("selection_soft", lambda v: v),
    _CodecField("candidate_hash", _text),
    _CodecField("update_origin", _text),
    _CodecField("update_target", _text),
)


@dataclass
class SlowUpdateResult:
    """Epoch-level slow-update decision and supporting metrics."""

    reasoning: str = _EMPTY
    slow_update_content: str = _EMPTY
    action: str = _EMPTY
    time_s: float | None = None
    prev_hard: float | None = None
    curr_hard: float | None = None
    selection_hard: float | None = None
    selection_soft: float | None = None
    candidate_hash: str = _EMPTY
    update_origin: str = _EMPTY
    update_target: str = _EMPTY

    @classmethod
    def from_dict(cls, payload: dict | None) -> SlowUpdateResult | None:
        if payload is None:
            return None
        return cls(**_decode_row(payload, _SLOW_TABLE))

    def to_dict(self) -> dict:
        return _encode_row(
            self,
            _SLOW_TABLE,
            required=("reasoning", "slow_update_content"),
        )
