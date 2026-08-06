# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Canonical OTLP builder for offline trajectory data.

This builder accepts already-normalized span dictionaries.  It intentionally
does not construct legacy ``TrajectoryStep``/detail objects; the result is a
single immutable :class:`~openjiuwen.agent_evolving.trajectory.model.Trajectory`.
"""

from __future__ import annotations

import hashlib
import uuid
from copy import deepcopy
from collections.abc import Mapping
from typing import Any

from openjiuwen.extensions.observability import semconv

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import (
    CASE_ID,
    TRAJECTORY_ID,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
    TRAJECTORY_SCOPE_NAME,
    TRAJECTORY_SOURCE,
)
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_from_map,
    normalize_span,
    span_identity,
    span_sort_key,
)


_RESOURCE_ALIASES = {
    "session_id": semconv.AT_SESSION_ID,
    "member_id": semconv.AT_MEMBER_ID,
    "team_id": semconv.AT_TEAM_ID,
    "source": TRAJECTORY_SOURCE,
    "case_id": CASE_ID,
    "trajectory_id": TRAJECTORY_ID,
}


def _text(value: Any) -> str:
    return str(value) if value is not None else ""


class TrajectoryBuilder:
    """Accumulate canonical OTLP spans for one offline execution."""

    def __init__(
        self,
        session_id: str,
        source: str = "offline",
        case_id: str | None = None,
        member_id: str | None = None,
        team_id: str | None = None,
        meta: Mapping[str, Any] | None = None,
        max_spans: int | None = None,
        *,
        trajectory_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        if session_id is None or str(session_id) == "":
            raise ValueError("session_id is required")
        if max_spans is not None and max_spans < 1:
            raise ValueError("max_spans must be >= 1")
        self.session_id = str(session_id)
        self.source = str(source)
        self.case_id = None if case_id is None else str(case_id)
        self.member_id = None if member_id is None else str(member_id)
        self.team_id = None if team_id is None else str(team_id)
        self.max_spans = max_spans
        self.trajectory_id = str(trajectory_id or uuid.uuid4())
        self.trace_id = str(trace_id or self.trajectory_id)
        self.meta = dict(meta or {})
        self._spans: list[dict[str, Any]] = []
        self._identities: set[tuple[str, str]] = set()

    @property
    def spans(self) -> list[dict[str, Any]]:
        """Return detached spans currently held by the builder."""

        return deepcopy(self._spans)

    def record_span(self, span: Mapping[str, Any]) -> None:
        """Record one detached canonical span, ignoring duplicate identities."""

        if not isinstance(span, Mapping):
            raise TypeError("span must be a mapping")
        normalized = normalize_span(span)
        normalized.setdefault("traceId", self.trace_id)
        if not normalized.get("spanId"):
            digest = hashlib.sha256(
                f"{self.trace_id}:{len(self._spans)}:{normalized.get('name', '')}".encode()
            ).hexdigest()
            normalized["spanId"] = digest[:16]
        identity = span_identity(normalized)
        if identity is not None:
            if identity in self._identities:
                return
            self._identities.add(identity)
        self._spans.append(normalized)
        self._spans.sort(key=span_sort_key)
        if self.max_spans is not None and len(self._spans) > self.max_spans:
            removed = self._spans[:-self.max_spans]
            self._spans = self._spans[-self.max_spans:]
            for removed_span in removed:
                removed_identity = span_identity(removed_span)
                if removed_identity is not None:
                    self._identities.discard(removed_identity)

    def add_span(self, span: Mapping[str, Any]) -> None:
        """Alias for :meth:`record_span` for collector-style callers."""

        self.record_span(span)

    def _resource_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            TRAJECTORY_ID: self.trajectory_id,
            TRAJECTORY_SCHEMA_VERSION_ATTR: TRAJECTORY_SCHEMA_VERSION,
            TRAJECTORY_SOURCE: self.source,
            semconv.AT_SESSION_ID: self.session_id,
        }
        if self.case_id is not None:
            attrs[CASE_ID] = self.case_id
        if self.member_id is not None:
            attrs[semconv.AT_MEMBER_ID] = self.member_id
        if self.team_id is not None:
            attrs[semconv.AT_TEAM_ID] = self.team_id
        for key, value in self.meta.items():
            if value is None:
                continue
            target = _RESOURCE_ALIASES.get(str(key), str(key))
            if target in attrs:
                continue
            attrs[target] = deepcopy(value)
        return attrs

    def build(self) -> Trajectory:
        """Build an immutable canonical OTLP trajectory snapshot."""

        payload = {
            "resourceSpans": [
                {
                    "resource": {"attributes": attributes_from_map(self._resource_attributes())},
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": TRAJECTORY_SCOPE_NAME,
                                "version": TRAJECTORY_SCHEMA_VERSION,
                            },
                            "spans": deepcopy(self._spans),
                        }
                    ],
                }
            ]
        }
        return Trajectory.from_otlp(payload)


__all__ = ["TrajectoryBuilder"]
