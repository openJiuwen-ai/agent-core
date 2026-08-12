# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Synchronous persistence for canonical :class:`Trajectory` snapshots."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Protocol

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import (
    CASE_ID,
    MEMBER_ID,
    SESSION_ID,
    TEAM_ID,
    TRAJECTORY_SOURCE,
)

logger = logging.getLogger(__name__)


class TrajectoryStore(Protocol):
    """Protocol implemented by synchronous trajectory archives."""

    def save(self, trajectory: Trajectory, version: str | None = None) -> None:
        """Persist one canonical trajectory snapshot."""

    def load(
        self,
        trajectory_id: str,
        version: str | None = None,
    ) -> Trajectory | None:
        """Load the oldest matching trajectory, if present."""

    def query(
        self,
        *,
        version: str | None = None,
        session_id: str | None = None,
        team_id: str | None = None,
        member_id: str | None = None,
        case_id: str | None = None,
        source: str | None = None,
    ) -> list[Trajectory]:
        """Return snapshots matching stable resource metadata."""


def _version_name(version: str | None) -> str:
    value = "default" if version is None else str(version)
    if value in {"", ".", ".."} or any(separator in value for separator in ("/", "\\")):
        raise ValueError("version must be a simple path component")
    return value


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_safe(model_dump())
        except Exception:
            logger.warning(
                "Failed to serialize trajectory value with model_dump(); falling back to string representation.",
                exc_info=True,
            )
    return str(value)


def to_json_compatible(value: Any) -> Any:
    """Serialize legacy RSI snapshots during the trajectory migration."""

    return _json_safe(value)


def _query_filters(**values: str | None) -> dict[str, str]:
    filters: dict[str, str] = {}
    for key, value in values.items():
        if value is not None:
            filters[key] = str(value)
    return filters


def _resource_value(trajectory: Trajectory, *keys: str) -> Any:
    attributes = trajectory.resource_attributes
    for key in keys:
        if key in attributes and attributes[key] is not None:
            return attributes[key]
    return None


def _metadata_value(trajectory: Trajectory, key: str) -> str | None:
    if key == "trajectory_id":
        value = trajectory.trajectory_id
    elif key == "session_id":
        value = _resource_value(trajectory, SESSION_ID)
    elif key == "team_id":
        value = _resource_value(trajectory, TEAM_ID)
    elif key == "member_id":
        value = _resource_value(trajectory, MEMBER_ID)
    elif key == "case_id":
        value = _resource_value(trajectory, CASE_ID)
    elif key == "source":
        value = _resource_value(trajectory, TRAJECTORY_SOURCE) or "offline"
    else:
        raise KeyError(key)
    return None if value is None else str(value)


def _canonical_input(trajectory: Any) -> Trajectory:
    if isinstance(trajectory, Trajectory):
        return Trajectory.from_otlp(trajectory.to_otlp())

    # Remove this migration branch with the legacy runtime model.  Import it
    # lazily because trajectory/__init__.py still loads Store before types.py.
    from openjiuwen.agent_evolving.trajectory.types import Trajectory as LegacyTrajectory

    if isinstance(trajectory, LegacyTrajectory):
        from openjiuwen.agent_evolving.trajectory.legacy import upgrade_legacy_record

        if not isinstance(trajectory.otlp_trace, Mapping):
            raise ValueError("legacy trajectory requires an OTLP payload")
        return upgrade_legacy_record(trajectory.otlp_trace)
    raise TypeError("store accepts only canonical Trajectory or legacy OTLP trajectory")


def _canonical_record(data: Mapping[str, Any]) -> Trajectory:
    # Resolve the historical parser lazily so package initialization does not
    # pull in the Team runtime graph.
    from openjiuwen.agent_evolving.trajectory.legacy import is_legacy_record, upgrade_legacy_record

    if is_legacy_record(data):
        return upgrade_legacy_record(data)
    return Trajectory.from_otlp(data)


class InMemoryTrajectoryStore:
    """Process-local canonical trajectory archive."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Trajectory]] = {}

    def save(self, trajectory: Trajectory, version: str | None = None) -> None:
        canonical = _canonical_input(trajectory)
        version_name = _version_name(version)
        self._data.setdefault(version_name, {})[canonical.trajectory_id] = canonical

    def load(self, trajectory_id: str, version: str | None = None) -> Trajectory | None:
        return self._data.get(_version_name(version), {}).get(str(trajectory_id))

    def query(
        self,
        *,
        version: str | None = None,
        session_id: str | None = None,
        team_id: str | None = None,
        member_id: str | None = None,
        case_id: str | None = None,
        source: str | None = None,
    ) -> list[Trajectory]:
        filters = _query_filters(
            session_id=session_id,
            team_id=team_id,
            member_id=member_id,
            case_id=case_id,
            source=source,
        )
        trajectories = list(self._data.get(_version_name(version), {}).values())
        return [
            trajectory
            for trajectory in trajectories
            if all(_metadata_value(trajectory, key) == value for key, value in filters.items())
        ]


class FileTrajectoryStore:
    """Append-only JSONL archive with read-only historical conversion."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, version: str | None) -> Path:
        return self._base_dir / f"trajectories_{_version_name(version)}.jsonl"

    def save(self, trajectory: Trajectory, version: str | None = None) -> None:
        canonical = _canonical_input(trajectory)
        payload = canonical.to_otlp()
        resource_spans = payload.get("resourceSpans")
        if not isinstance(resource_spans, list) or not resource_spans:
            raise ValueError("trajectory payload must contain non-empty resourceSpans")
        record = {"resourceSpans": _json_safe(resource_spans)}
        with self._get_file_path(version).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    @staticmethod
    def _records(path: Path) -> Iterator[dict[str, Any]]:
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(record, dict):
                    yield record

    @staticmethod
    def _decode(record: Mapping[str, Any]) -> Trajectory | None:
        try:
            return _canonical_record(record)
        except (TypeError, ValueError, KeyError):
            return None

    def load(self, trajectory_id: str, version: str | None = None) -> Trajectory | None:
        target = str(trajectory_id)
        for record in self._records(self._get_file_path(version)):
            trajectory = self._decode(record)
            if trajectory is not None and trajectory.trajectory_id == target:
                return trajectory
        return None

    def query(
        self,
        *,
        version: str | None = None,
        session_id: str | None = None,
        team_id: str | None = None,
        member_id: str | None = None,
        case_id: str | None = None,
        source: str | None = None,
    ) -> list[Trajectory]:
        filters = _query_filters(
            session_id=session_id,
            team_id=team_id,
            member_id=member_id,
            case_id=case_id,
            source=source,
        )
        results: list[Trajectory] = []
        for record in self._records(self._get_file_path(version)):
            trajectory = self._decode(record)
            if trajectory is None:
                continue
            if all(_metadata_value(trajectory, key) == value for key, value in filters.items()):
                results.append(trajectory)
        return results


__all__ = [
    "FileTrajectoryStore",
    "InMemoryTrajectoryStore",
    "TrajectoryStore",
]
