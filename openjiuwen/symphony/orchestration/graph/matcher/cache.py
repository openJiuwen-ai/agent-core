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
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from openjiuwen.symphony.orchestration.graph.models import LLMMatch, RelationCandidate, SkillRegistry
from openjiuwen.symphony.shared.fingerprint import (
    CapabilityFingerprint,
    Fingerprint,
    coerce_fingerprint,
)

FingerprintInput = Fingerprint | CapabilityFingerprint | dict[str, Any]

CACHE_RECORD_SCHEMA = "Symphony-relation-match-cache-v1"
CACHE_INDEX_SCHEMA = "Symphony-relation-match-cache-index-v1"
_ENDPOINT_MATCHER_FIELDS = frozenset({"api_base", "api_url", "base_url", "endpoint", "endpoint_url"})
_SECRET_MATCHER_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True)
class RelationCacheStats:
    reused_count: int = 0
    resolved_count: int = 0
    stored_count: int = 0


class CachedOntologyMatcher:
    """Wrap an ontology matcher with a persistent per-candidate cache."""

    def __init__(
        self,
        matcher: Any,
        cache_path: str | Path,
        *,
        fingerprints: Iterable[FingerprintInput],
    ) -> None:
        self.matcher = matcher
        self.cache = RelationMatchCache(
            cache_path,
            matcher_signature=_matcher_signature(matcher),
            fingerprints=fingerprints,
        )
        self.diagnostics: list[Any] = []
        self.stats = RelationCacheStats()

    async def match(
        self,
        registry: SkillRegistry,
        candidates: Iterable[RelationCandidate],
    ) -> list[LLMMatch]:
        self.diagnostics = []
        self.stats = RelationCacheStats()
        candidate_list = list(candidates)
        cached_matches: list[tuple[int, list[LLMMatch]]] = []
        misses: list[tuple[int, RelationCandidate]] = []
        for index, candidate in enumerate(candidate_list):
            matches = self.cache.load(candidate)
            if matches is None:
                misses.append((index, candidate))
            else:
                cached_matches.append((index, matches))

        resolved_by_index: dict[int, list[LLMMatch]] = {}
        diagnostics: list[Any] = []
        for window in _chunked(misses, _matcher_window_size(self.matcher)):
            miss_candidates = [candidate for _, candidate in window]
            resolved_matches = list(await self.matcher.match(registry, miss_candidates))
            resolved_by_candidate = _matches_by_candidate(miss_candidates, resolved_matches)
            for index, candidate in window:
                matches = resolved_by_candidate.get(candidate.key, [])
                resolved_by_index[index] = matches
                self.cache.store(candidate, matches)
            self.cache.flush()
            diagnostics.extend(list(getattr(self.matcher, "diagnostics", [])))

        combined: list[tuple[int, LLMMatch]] = []
        for index, matches in cached_matches:
            combined.extend((index, match) for match in matches)
        for index, matches in resolved_by_index.items():
            combined.extend((index, match) for match in matches)
        self.diagnostics = diagnostics
        self.stats = RelationCacheStats(
            reused_count=len(cached_matches),
            resolved_count=len(misses),
            stored_count=len(misses),
        )
        combined.sort(
            key=lambda item: (item[0], item[1].source_id, item[1].target_id),
        )
        return [match for _index, match in combined]

    def manifest_metadata(self) -> dict[str, Any]:
        metadata_method = getattr(self.matcher, "manifest_metadata", None)
        if callable(metadata_method):
            metadata = _sanitize_matcher_metadata(dict(metadata_method()))
        else:
            metadata = {"matcher": self.matcher.__class__.__name__}
        metadata["relation_cache"] = {
            "schema_version": CACHE_RECORD_SCHEMA,
            "reused_count": self.stats.reused_count,
            "resolved_count": self.stats.resolved_count,
        }
        return metadata

    @property
    def thresholds(self) -> dict[str, float]:
        return dict(getattr(self.matcher, "thresholds", {}))


class RelationMatchCache:
    """JSON cache keyed by candidate evidence, graph identity, and matcher config."""

    def __init__(
        self,
        path: str | Path,
        *,
        matcher_signature: dict[str, Any],
        fingerprints: Iterable[FingerprintInput],
    ) -> None:
        self.path = Path(path).resolve()
        self._lock = _path_lock(self.path)
        self.matcher_signature = _sanitize_matcher_metadata(dict(matcher_signature))
        normalized = (coerce_fingerprint(item) for item in fingerprints)
        self.fingerprint_hashes = {item.id: _stable_sha256(item.graph_identity_dict()) for item in normalized}
        with self._lock:
            self._records = self._load()
        self._pending: dict[str, dict[str, Any]] = {}

    def load(self, candidate: RelationCandidate) -> list[LLMMatch] | None:
        with self._lock:
            self._records = _merge_records(self._load(), self._pending)
            record = self._records.get(self._key(candidate))
        if not isinstance(record, dict) or record.get("schema_version") != CACHE_RECORD_SCHEMA:
            return None
        matches = record.get("matches", [])
        if not isinstance(matches, list):
            return None
        return [_match_from_dict(item) for item in matches if isinstance(item, dict)]

    def store(self, candidate: RelationCandidate, matches: list[LLMMatch]) -> None:
        with self._lock:
            key = self._key(candidate)
            record = {
                "schema_version": CACHE_RECORD_SCHEMA,
                "candidate_id": candidate.key,
                "matches": [match.to_dict() for match in matches],
                "updated_at": _utc_now(),
            }
            self._records[key] = record
            self._pending[key] = record

    def flush(self) -> None:
        with self._lock:
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


def _matches_by_candidate(
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


def _matcher_signature(matcher: Any) -> dict[str, Any]:
    metadata_method = getattr(matcher, "manifest_metadata", None)
    if callable(metadata_method):
        metadata = _sanitize_matcher_metadata(dict(metadata_method()))
    else:
        metadata = {"matcher": matcher.__class__.__name__}
    signature = dict(metadata)
    thresholds = getattr(matcher, "thresholds", None)
    if thresholds is not None:
        signature["thresholds"] = dict(thresholds)
    return signature


def _sanitize_matcher_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized_key = key.strip().lower().replace("-", "_")
        if _is_secret_field(normalized_key):
            continue
        if normalized_key in _ENDPOINT_MATCHER_FIELDS:
            if value is not None:
                sanitized[f"{key}_sha256"] = _sha256_text(_normalize_endpoint(str(value)))
            continue
        if isinstance(value, dict):
            sanitized[key] = _sanitize_matcher_metadata(value)
        elif isinstance(value, (list, tuple)):
            sanitized[key] = [_sanitize_matcher_metadata(item) if isinstance(item, dict) else item for item in value]
        else:
            sanitized[key] = value
    return sanitized


def _is_secret_field(normalized_key: str) -> bool:
    return normalized_key in _SECRET_MATCHER_FIELDS or any(
        normalized_key.endswith(f"_{suffix}") for suffix in _SECRET_MATCHER_FIELDS
    )


def _normalize_endpoint(value: str) -> str:
    text = value.strip()
    try:
        parsed = urlsplit(text)
        if not parsed.scheme or not parsed.hostname:
            return text.rstrip("/")
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname.lower()
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = parsed.port
        if port is not None and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
            hostname = f"{hostname}:{port}"
        path = parsed.path.rstrip("/")
        return urlunsplit((scheme, hostname, path, "", ""))
    except ValueError:
        return text.rstrip("/")


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


def _matcher_window_size(matcher: Any) -> int:
    batch_size = _matcher_positive_int(matcher, "batch_size")
    max_workers = _matcher_positive_int(matcher, "max_workers")
    return batch_size * max_workers


def _chunked(values: list[Any], size: int) -> list[list[Any]]:
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


def _stable_sha256(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(serialized)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
