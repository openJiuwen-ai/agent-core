"""Persistent cache for ontology relation matching."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from filelock import FileLock

from openjiuwen.symphony.orchestration.graph.models import GraphDiagnostic, LLMMatch, RelationCandidate
from openjiuwen.symphony.shared.fingerprint import FingerprintLike
from openjiuwen.symphony.shared.identity import sanitize_metadata as sanitize_matcher_metadata

CACHE_RECORD_SCHEMA = "Symphony-relation-match-cache-v1"
CACHE_INDEX_SCHEMA = "Symphony-relation-match-cache-index-v1"
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True)
class RelationCacheStats:
    reused_count: int = 0
    resolved_count: int = 0
    stored_count: int = 0


class RelationMatchCache:
    """JSON cache keyed by candidate evidence, graph identity, and matcher config."""

    def __init__(
        self,
        path: str | Path,
        *,
        matcher_signature: dict[str, Any],
        fingerprints: Iterable[FingerprintLike],
    ) -> None:
        self.path = Path(path).resolve()
        self._lock = _path_lock(self.path)
        self._process_lock = FileLock(str(self.path) + ".lock", timeout=30)
        self.matcher_signature = sanitize_matcher_metadata(dict(matcher_signature))
        self.fingerprint_hashes = {item.id: _stable_sha256(item.graph_identity_dict()) for item in fingerprints}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, Any] = {}
        self._pending: dict[str, dict[str, Any]] = {}

    def load(self, candidate: RelationCandidate) -> tuple[list[LLMMatch], list[GraphDiagnostic]] | None:
        return self.load_many([candidate])[0]

    def load_many(
        self,
        candidates: Iterable[RelationCandidate],
    ) -> list[tuple[list[LLMMatch], list[GraphDiagnostic]] | None]:
        candidate_list = list(candidates)
        with self._lock, self._process_lock:
            self._records = _merge_records(self._load(), self._pending)
            records = [self._records.get(self._key(candidate)) for candidate in candidate_list]
        return [_decode_record(record) for record in records]

    def store(
        self,
        candidate: RelationCandidate,
        matches: list[LLMMatch],
        diagnostics: list[GraphDiagnostic] | None = None,
    ) -> None:
        with self._lock:
            key = self._key(candidate)
            record = {
                "schema_version": CACHE_RECORD_SCHEMA,
                "candidate_id": candidate.key,
                "matches": [match.to_dict() for match in matches],
                "diagnostics": [diagnostic.to_dict() for diagnostic in diagnostics or []],
                "updated_at": _utc_now(),
            }
            self._records[key] = record
            self._pending[key] = record

    def flush(self) -> None:
        with self._lock, self._process_lock:
            if not self._pending:
                return
            records = _merge_records(self._load(), self._pending)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"schema_version": CACHE_INDEX_SCHEMA, "records": records}
            temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
            self._records = records
            self._pending.clear()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != CACHE_INDEX_SCHEMA:
            return {}
        records = payload.get("records")
        return records if isinstance(records, dict) else {}

    def _key(self, candidate: RelationCandidate) -> str:
        return _stable_sha256(
            {
                "candidate": candidate.to_dict(),
                "source_fingerprint_hash": self.fingerprint_hashes.get(candidate.source_id, ""),
                "target_fingerprint_hash": self.fingerprint_hashes.get(candidate.target_id, ""),
                "matcher": self.matcher_signature,
            }
        )


def _decode_record(
    record: Any,
) -> tuple[list[LLMMatch], list[GraphDiagnostic]] | None:
    if not isinstance(record, dict) or record.get("schema_version") != CACHE_RECORD_SCHEMA:
        return None
    matches = record.get("matches", [])
    if not isinstance(matches, list):
        return None
    diagnostics = record.get("diagnostics", [])
    if not isinstance(diagnostics, list):
        return None
    return (
        [_match_from_dict(item) for item in matches if isinstance(item, dict)],
        [_diagnostic_from_dict(item) for item in diagnostics if isinstance(item, dict)],
    )


def matches_by_candidate(
    candidates: list[RelationCandidate],
    matches: list[LLMMatch],
) -> dict[str, list[LLMMatch]]:
    candidate_by_pair: dict[tuple[str, str], str] = {}
    for candidate in candidates:
        candidate_by_pair[(candidate.source_id, candidate.target_id)] = candidate.key
        candidate_by_pair[(candidate.target_id, candidate.source_id)] = candidate.key
    output: dict[str, list[LLMMatch]] = {candidate.key: [] for candidate in candidates}
    for match in matches:
        candidate_key = match.candidate_id or candidate_by_pair.get((match.source_id, match.target_id))
        if candidate_key in output:
            output[candidate_key].append(match)
    return output


def diagnostics_by_candidate(
    candidates: list[RelationCandidate],
    diagnostics: list[GraphDiagnostic],
) -> dict[str, list[GraphDiagnostic]]:
    output: dict[str, list[GraphDiagnostic]] = {candidate.key: [] for candidate in candidates}
    by_key = {candidate.key: candidate for candidate in candidates}
    by_pair: dict[tuple[str, str], str] = {}
    for candidate in candidates:
        by_pair[(candidate.source_id, candidate.target_id)] = candidate.key
        by_pair[(candidate.target_id, candidate.source_id)] = candidate.key
    for diagnostic in diagnostics:
        details = diagnostic.details
        match = details.get("match") if isinstance(details.get("match"), dict) else {}
        candidate_id = str(details.get("candidate_id") or match.get("candidate_id") or "")
        keys: list[str] = []
        if candidate_id in by_key:
            keys = [candidate_id]
        else:
            source_id = str(details.get("source_id") or match.get("source_id") or "")
            target_id = str(details.get("target_id") or match.get("target_id") or "")
            pair_key = by_pair.get((source_id, target_id))
            if pair_key is not None:
                keys = [pair_key]
        if not keys and diagnostic.skill_id:
            keys = [
                candidate.key
                for candidate in candidates
                if diagnostic.skill_id in {candidate.source_id, candidate.target_id}
            ]
        for key in keys or list(output):
            output[key].append(diagnostic)
    return output


def dedupe_diagnostics(diagnostics: Iterable[GraphDiagnostic]) -> list[GraphDiagnostic]:
    output = []
    seen = set()
    for diagnostic in diagnostics:
        marker = json.dumps(diagnostic.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if marker in seen:
            continue
        seen.add(marker)
        output.append(diagnostic)
    return output


def _path_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _merge_records(
    persisted: dict[str, Any],
    updates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    merged = dict(persisted)
    for key, update in updates.items():
        current = merged.get(key)
        if not isinstance(current, dict) or _record_rank(update) >= _record_rank(current):
            merged[key] = update
    return merged


def _record_rank(record: dict[str, Any]) -> tuple[str, str]:
    return (
        str(record.get("updated_at") or ""),
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _matcher_positive_int(matcher: Any, name: str) -> int:
    try:
        return max(1, int(getattr(matcher, name, 0) or 0))
    except (TypeError, ValueError):
        return 1


def matcher_window_size(matcher: Any) -> int:
    batch_size = _matcher_positive_int(matcher, "batch_size")
    max_workers = _matcher_positive_int(matcher, "max_workers")
    return batch_size * max_workers


def chunked(values: list[Any], size: int) -> list[list[Any]]:
    chunk_size = max(1, size)
    chunks = []
    for index in range(0, len(values), chunk_size):
        stop = index + chunk_size
        chunks.append(values[index:stop])
    return chunks


def _match_from_dict(payload: dict[str, Any]) -> LLMMatch:
    supporting_fields = payload.get("supporting_fields")
    candidate_id = payload.get("candidate_id")
    return LLMMatch(
        source_id=str(payload.get("source_id") or ""),
        target_id=str(payload.get("target_id") or ""),
        relation_type=str(payload.get("relation_type") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        method=str(payload.get("method") or "llm_ontology_match"),
        reasons=[str(item) for item in payload.get("reasons", [])],
        supporting_fields=supporting_fields if isinstance(supporting_fields, dict) else {},
        candidate_id=str(candidate_id) if candidate_id is not None else None,
        accepted=bool(payload.get("accepted", False)),
        diagnostics=[str(item) for item in payload.get("diagnostics", [])],
        raw=payload.get("raw") if isinstance(payload.get("raw"), dict) else {},
    )


def _diagnostic_from_dict(payload: dict[str, Any]) -> GraphDiagnostic:
    details = payload.get("details")
    capability_id = payload.get("capability_id")
    return GraphDiagnostic(
        stage=str(payload.get("stage") or "llm_match"),
        severity=str(payload.get("severity") or "warning"),
        code=str(payload.get("code") or "cached_diagnostic"),
        message=str(payload.get("message") or ""),
        skill_id=str(capability_id) if capability_id is not None else None,
        details=details if isinstance(details, dict) else {},
    )


def _stable_sha256(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(serialized)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
