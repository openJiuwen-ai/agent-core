"""Durable evidence log and immutable observation/merged revision storage."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

from filelock import FileLock

from openjiuwen.symphony.observation.contracts import GraphSnapshot
from openjiuwen.symphony.observation.identity import stable_hash

EVIDENCE_SCHEMA = "symphony.graph-evidence.v1"
OBSERVATION_SCHEMA = "symphony.graph-observation.v1"
MERGED_SCHEMA = "symphony.graph-merged.v1"


@dataclass(frozen=True)
class EvidenceAppendResult:
    sequence: int
    duplicate: bool
    eligible: bool
    reason: str


class EvidenceStore:
    """Append-only SQLite evidence log shared by all graph scopes."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "evolution"
        self.path = self.root / "evidence.sqlite3"
        self._lock = FileLock(str(self.root / ".evidence.lock"), timeout=30)

    def append(
        self,
        *,
        graph_scope_id: str,
        evidence_id: str,
        observed_at: str,
        eligible: bool,
        reason: str,
        payload: Mapping[str, Any],
    ) -> EvidenceAppendResult:
        self._ensure_schema()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_digest = stable_hash(payload)
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                """
                SELECT sequence, eligible, reason, payload_digest
                FROM evidence
                WHERE graph_scope_id = ? AND evidence_id = ?
                """,
                (graph_scope_id, evidence_id),
            ).fetchone()
            if existing is not None:
                if str(existing[3]) != payload_digest:
                    raise ValueError(f"evidence_id already refers to different evidence: {evidence_id}")
                return EvidenceAppendResult(
                    sequence=int(existing[0]),
                    duplicate=True,
                    eligible=bool(existing[1]),
                    reason=str(existing[2] or ""),
                )
            cursor = connection.execute(
                """
                INSERT INTO evidence (
                    graph_scope_id, evidence_id, observed_at, eligible, reason, payload_digest, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (graph_scope_id, evidence_id, observed_at, int(eligible), reason, payload_digest, serialized),
            )
            connection.commit()
            return EvidenceAppendResult(
                sequence=int(cursor.lastrowid),
                duplicate=False,
                eligible=eligible,
                reason=reason,
            )

    def iter_after(
        self,
        graph_scope_id: str,
        sequence: int,
    ) -> Iterable[tuple[int, bool, dict[str, Any]]]:
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, eligible, payload_json
                FROM evidence
                WHERE graph_scope_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (graph_scope_id, int(sequence)),
            ).fetchall()
        for current_sequence, eligible, payload_json in rows:
            payload = json.loads(str(payload_json))
            if isinstance(payload, dict):
                yield int(current_sequence), bool(eligible), payload

    def scope_ids(self) -> tuple[str, ...]:
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT graph_scope_id FROM evidence ORDER BY graph_scope_id ASC"
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _ensure_schema(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    graph_scope_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    eligible INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(graph_scope_id, evidence_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS evidence_scope_sequence ON evidence(graph_scope_id, sequence)"
            )
            connection.execute(f"PRAGMA user_version = {1}")
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection


class RevisionStore:
    """Publish immutable observation and merged manifests per graph scope."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "evolution"
        self._lock = FileLock(str(self.root / ".revision.lock"), timeout=30)

    def projection_lock(self, graph_scope_id: str) -> FileLock:
        """Serialize projection transactions across backend processes."""

        scope_root = self._scope_root(graph_scope_id)
        scope_root.mkdir(parents=True, exist_ok=True)
        return FileLock(
            str(scope_root / ".projection.lock"),
            timeout=30,
        )

    def read_overlay(self, graph_scope_id: str) -> dict[str, Any] | None:
        current = self._read_current(graph_scope_id)
        if current is None:
            return None
        scope_root = self._scope_root(graph_scope_id)
        revision = str(current.get("observation_revision") or "")
        if not revision:
            return None
        return _read_json(scope_root / "observations" / revision / "overlay.json")

    def read_snapshot(
        self,
        graph_scope_id: str,
        merged_revision: str | None = None,
    ) -> GraphSnapshot | None:
        pointer = self._read_current(graph_scope_id)
        if pointer is None:
            return None
        scope_root = self._scope_root(graph_scope_id)
        if merged_revision is not None:
            manifest_path = scope_root / "merged" / merged_revision / "manifest.json"
            if not manifest_path.is_file():
                raise FileNotFoundError(f"Unknown Symphony merged revision: {merged_revision}")
            pointer = _read_json(manifest_path)
        observation_revision = str(pointer.get("observation_revision") or "")
        overlay = _read_json(scope_root / "observations" / observation_revision / "overlay.json")
        return GraphSnapshot(
            graph_scope_id=graph_scope_id,
            static_revision=str(pointer["static_revision"]),
            observation_revision=observation_revision,
            merged_revision=str(pointer["merged_revision"]),
            high_water_sequence=int(pointer.get("high_water_sequence") or 0),
            overlay=overlay,
        )

    def publish(
        self,
        *,
        graph_scope_id: str,
        static_revision: str,
        high_water_sequence: int,
        edges: Mapping[str, Mapping[str, Any]],
        generated_at: str,
        projection_status: str = "ready",
        diagnostics: Iterable[str] = (),
    ) -> GraphSnapshot:
        canonical_overlay = {
            "schema_version": OBSERVATION_SCHEMA,
            "graph_scope_id": graph_scope_id,
            "static_revision": static_revision,
            "high_water_sequence": int(high_water_sequence),
            "edges": {key: dict(edges[key]) for key in sorted(edges)},
            "projection_status": projection_status,
            "diagnostics": sorted({str(item) for item in diagnostics if str(item)}),
        }
        observation_revision = f"observation-{stable_hash(canonical_overlay)[:20]}"
        overlay = {
            **canonical_overlay,
            "observation_revision": observation_revision,
            "generated_at": generated_at,
        }
        canonical_merged = {
            "schema_version": MERGED_SCHEMA,
            "graph_scope_id": graph_scope_id,
            "static_revision": static_revision,
            "observation_revision": observation_revision,
        }
        merged_revision = f"merged-{stable_hash(canonical_merged)[:20]}"
        manifest = {
            **canonical_merged,
            "merged_revision": merged_revision,
            "high_water_sequence": int(high_water_sequence),
            "generated_at": generated_at,
        }
        current = {
            "schema_version": MERGED_SCHEMA,
            "graph_scope_id": graph_scope_id,
            "static_revision": static_revision,
            "observation_revision": observation_revision,
            "merged_revision": merged_revision,
            "high_water_sequence": int(high_water_sequence),
            "generated_at": generated_at,
        }

        scope_root = self._scope_root(graph_scope_id)
        with self._lock:
            observation_path = scope_root / "observations" / observation_revision / "overlay.json"
            merged_path = scope_root / "merged" / merged_revision / "manifest.json"
            _write_immutable_json(observation_path, overlay)
            overlay = _read_json(observation_path)
            _write_immutable_json(merged_path, manifest)
            manifest = _read_json(merged_path)
            current["generated_at"] = manifest["generated_at"]
            _write_json_atomic(scope_root / "current.json", current)
        return GraphSnapshot(
            graph_scope_id=graph_scope_id,
            static_revision=static_revision,
            observation_revision=observation_revision,
            merged_revision=merged_revision,
            high_water_sequence=high_water_sequence,
            overlay=overlay,
        )

    def _read_current(self, graph_scope_id: str) -> dict[str, Any] | None:
        path = self._scope_root(graph_scope_id) / "current.json"
        if not path.is_file():
            return None
        return _read_json(path)

    def _scope_root(self, graph_scope_id: str) -> Path:
        scope_hash = stable_hash({"graph_scope_id": graph_scope_id})[:20]
        return self.root / "scopes" / scope_hash


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Symphony revision payload must be an object: {path}")
    return payload


def _write_immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.is_file():
        existing = _read_json(path)
        existing_canonical = {key: value for key, value in existing.items() if key != "generated_at"}
        requested_canonical = {key: value for key, value in payload.items() if key != "generated_at"}
        if existing_canonical != requested_canonical:
            raise ValueError(f"Immutable Symphony revision already exists with different content: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, payload)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
