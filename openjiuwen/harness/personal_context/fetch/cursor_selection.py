"""Shared latest-first candidate selection and bounded cursor helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Mapping, cast

_SELECTION_KEY = "_selection"
_RECEIPT_FIELDS = ("resource_lane", "stable_id", "revision_id", "candidate_time")
_FAILURE_FIELDS = ("token", "item_ref", "code", "message", "failed_at")
_FAILURE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32}$")
_FAILURE_EXAMPLE_LIMIT = 20


def _normalized_time(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("candidate_time must be a non-empty RFC 3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("candidate_time must be RFC 3339") from exc
    if parsed.tzinfo is None:
        raise ValueError("candidate_time must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _candidate_copy(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("candidate must be an object")
    candidate = deepcopy(dict(value))
    for field_name in ("resource_lane", "stable_id", "revision_id", "locator"):
        field_value = candidate.get(field_name)
        if not isinstance(field_value, str) or not field_value.strip():
            raise ValueError(f"candidate {field_name} must be a non-empty string")
    candidate["candidate_time"] = _normalized_time(candidate.get("candidate_time"))
    return candidate


def _receipt(candidate: Mapping[str, object]) -> dict[str, str]:
    return {field_name: str(candidate[field_name]) for field_name in _RECEIPT_FIELDS}


def _version_key(candidate: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(candidate["resource_lane"]),
        str(candidate["stable_id"]),
        str(candidate["revision_id"]),
    )


def _resource_key(candidate: Mapping[str, object]) -> tuple[str, str]:
    return str(candidate["resource_lane"]), str(candidate["stable_id"])


def _digest_token_part(*values: str) -> bytes:
    payload = "\0".join(values).encode("utf-8")
    return hashlib.sha256(payload).digest()[:12]


def _failure_token(candidate: Mapping[str, object]) -> str:
    lane, stable_id, revision_id = _version_key(candidate)
    payload = _digest_token_part(lane, stable_id) + _digest_token_part(revision_id)
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _failure_resource_prefix(candidate: Mapping[str, object]) -> str:
    lane, stable_id = _resource_key(candidate)
    return base64.urlsafe_b64encode(_digest_token_part(lane, stable_id)).decode("ascii").rstrip("=")


def _failed_selection_copy(value: object) -> dict[str, object]:
    if value is None:
        return {"version": 1, "tokens": [], "examples": []}
    if not isinstance(value, Mapping) or set(value) != {"version", "tokens", "examples"}:
        raise ValueError("cursor failed selection has an invalid shape")
    if value.get("version") != 1:
        raise ValueError("cursor failed selection has an unsupported version")
    raw_tokens = value.get("tokens")
    raw_examples = value.get("examples")
    if not isinstance(raw_tokens, list) or not isinstance(raw_examples, list):
        raise ValueError("cursor failed selection tokens and examples must be lists")
    tokens: list[str] = []
    for token in raw_tokens:
        if not isinstance(token, str) or not _FAILURE_TOKEN.fullmatch(token):
            raise ValueError("cursor contains an invalid failure token")
        tokens.append(token)
    if len(tokens) != len(set(tokens)):
        raise ValueError("cursor contains duplicate failure tokens")
    token_set = set(tokens)
    examples: list[dict[str, object]] = []
    if len(raw_examples) > _FAILURE_EXAMPLE_LIMIT:
        raise ValueError("cursor contains too many failure examples")
    for raw_example in raw_examples:
        if not isinstance(raw_example, Mapping) or set(raw_example) != set(_FAILURE_FIELDS):
            raise ValueError("cursor contains an invalid failure example")
        example = deepcopy(dict(raw_example))
        token = example["token"]
        code = example["code"]
        if not isinstance(token, str) or token not in token_set:
            raise ValueError("cursor failure example does not match a token")
        if isinstance(code, bool) or not isinstance(code, int):
            raise ValueError("cursor failure code must be an integer")
        for field_name in ("item_ref", "message"):
            field_value = example[field_name]
            if not isinstance(field_value, str) or not field_value.strip() or len(field_value) > 256:
                raise ValueError(f"cursor failure {field_name} must be a bounded non-empty string")
        example["failed_at"] = _normalized_time(example["failed_at"])
        examples.append(example)
    return {"version": 1, "tokens": sorted(tokens), "examples": examples}


def _selection_copy(cursor: Mapping[str, object] | None) -> dict[str, object]:
    raw = cursor.get(_SELECTION_KEY) if cursor is not None else None
    if raw is None:
        return {
            "completed": [],
            "latest_seen_time": None,
            "earliest_considered": None,
            "failed": _failed_selection_copy(None),
        }
    if not isinstance(raw, Mapping):
        raise ValueError("cursor _selection must be an object")
    unknown = set(raw) - {"completed", "latest_seen_time", "earliest_considered", "failed"}
    if unknown:
        raise ValueError("cursor _selection contains unknown fields")
    raw_completed = raw.get("completed", [])
    if not isinstance(raw_completed, list):
        raise ValueError("cursor completed receipts must be a list")
    completed: list[dict[str, str]] = []
    for raw_receipt in raw_completed:
        if not isinstance(raw_receipt, Mapping) or set(raw_receipt) != set(_RECEIPT_FIELDS):
            raise ValueError("cursor contains an invalid completed receipt")
        completed.append(_receipt(_candidate_copy({**raw_receipt, "locator": "cursor://receipt"})))
    latest = raw.get("latest_seen_time")
    earliest = raw.get("earliest_considered")
    return {
        "completed": completed,
        "latest_seen_time": _normalized_time(latest) if latest is not None else None,
        "earliest_considered": _normalized_time(earliest) if earliest is not None else None,
        "failed": _failed_selection_copy(raw.get("failed")),
    }


def _time_sort_value(value: object) -> int:
    parsed = datetime.fromisoformat(_normalized_time(value).replace("Z", "+00:00"))
    return (
        parsed.toordinal() * 86_400_000_000
        + parsed.hour * 3_600_000_000
        + parsed.minute * 60_000_000
        + parsed.second * 1_000_000
        + parsed.microsecond
    )


def _receipt_sort_key(receipt: Mapping[str, object]) -> tuple[int, str, str, str]:
    timestamp = _time_sort_value(receipt["candidate_time"])
    lane, stable_id, revision_id = _version_key(receipt)
    return -timestamp, lane, stable_id, revision_id


def _encoded_size(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("cursor must be JSON serializable") from exc


def candidate_in_time_range(
    candidate_time: str,
    time_range: Mapping[str, object],
    run_started_at: datetime,
) -> bool:
    """Apply the configured all/recent/fixed interval to one candidate time."""

    if run_started_at.tzinfo is None:
        raise ValueError("run_started_at must include a timezone")
    candidate_value = datetime.fromisoformat(_normalized_time(candidate_time).replace("Z", "+00:00"))
    run_start = run_started_at.astimezone(UTC)
    mode = time_range.get("mode")
    if mode == "all" and set(time_range) == {"mode"}:
        return True
    if mode == "recent" and set(time_range) == {"mode", "recent_days"}:
        recent_days = time_range.get("recent_days")
        if isinstance(recent_days, bool) or not isinstance(recent_days, int) or recent_days <= 0:
            raise ValueError("recent_days must be a positive integer")
        return run_start - timedelta(days=recent_days) <= candidate_value <= run_start
    if mode == "fixed" and set(time_range) == {"mode", "start_at", "end_at"}:
        start_at = datetime.fromisoformat(_normalized_time(time_range.get("start_at")).replace("Z", "+00:00"))
        end_at = datetime.fromisoformat(_normalized_time(time_range.get("end_at")).replace("Z", "+00:00"))
        if start_at >= end_at:
            raise ValueError("fixed time range start must be before end")
        return start_at <= candidate_value < end_at
    raise ValueError("time_range must be all, recent, or fixed")


def select_latest_candidates(
    candidates: tuple[dict[str, object], ...],
    cursor: dict[str, object] | None,
    limit: int,
    *,
    retry_quarantined: bool = False,
) -> tuple[dict[str, object], ...]:
    """Select new changes first, then the newest unfinished historical holes."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if not isinstance(candidates, tuple):
        raise ValueError("candidates must be a tuple")
    selection = _selection_copy(cursor)
    completed_receipts = cast(list[dict[str, str]], selection["completed"])
    completed_keys = {_version_key(receipt) for receipt in completed_receipts}
    failed_selection = cast(dict[str, object], selection["failed"])
    failed_tokens = set(cast(list[str], failed_selection["tokens"]))
    failed_resource_prefixes = {token[:16] for token in failed_tokens}
    known_revisions: dict[tuple[str, str], set[str]] = {}
    for receipt in completed_receipts:
        known_revisions.setdefault(_resource_key(receipt), set()).add(str(receipt["revision_id"]))
    latest_seen = selection["latest_seen_time"]
    earliest = selection["earliest_considered"]

    deduplicated: dict[tuple[str, str, str], dict[str, object]] = {}
    for raw_candidate in candidates:
        candidate = _candidate_copy(raw_candidate)
        key = _version_key(candidate)
        current = deduplicated.get(key)
        if current is None or _time_sort_value(candidate["candidate_time"]) > _time_sort_value(
            current["candidate_time"]
        ):
            deduplicated[key] = candidate

    pending: list[tuple[int, dict[str, object]]] = []
    for key, candidate in deduplicated.items():
        if key in completed_keys:
            continue
        failure_token = _failure_token(candidate)
        quarantined = failure_token in failed_tokens
        if quarantined and not retry_quarantined:
            continue
        resource_key = _resource_key(candidate)
        revisions = known_revisions.get(resource_key, set())
        changed_revision = bool(revisions) and str(candidate["revision_id"]) not in revisions
        changed_failed_revision = (
            _failure_resource_prefix(candidate) in failed_resource_prefixes and not quarantined
        )
        if (
            earliest is not None
            and _time_sort_value(candidate["candidate_time"]) < _time_sort_value(earliest)
            and not changed_revision
            and not changed_failed_revision
        ):
            continue
        newly_visible = latest_seen is None or _time_sort_value(candidate["candidate_time"]) > _time_sort_value(
            latest_seen
        )
        pending.append(
            (
                0 if changed_revision or changed_failed_revision or newly_visible or quarantined else 1,
                candidate,
            )
        )

    pending.sort(
        key=lambda item: (
            item[0],
            -_time_sort_value(item[1]["candidate_time"]),
            str(item[1]["resource_lane"]),
            str(item[1]["stable_id"]),
            str(item[1]["revision_id"]),
        )
    )
    return tuple(candidate for _priority, candidate in pending[:limit])


def record_completed_candidates(
    cursor: dict[str, object] | None,
    completed: tuple[dict[str, object], ...],
) -> dict[str, object]:
    """Return a copied cursor with discrete successful-version receipts."""

    if cursor is not None and not isinstance(cursor, dict):
        raise ValueError("cursor must be an object or null")
    if not isinstance(completed, tuple):
        raise ValueError("completed candidates must be a tuple")
    updated = deepcopy(cursor) if cursor is not None else {}
    selection = _selection_copy(updated)
    receipts = cast(list[dict[str, str]], selection["completed"])
    failed_selection = cast(dict[str, object], selection["failed"])
    failed_tokens = set(cast(list[str], failed_selection["tokens"]))
    failure_examples = cast(list[dict[str, object]], failed_selection["examples"])
    earliest = selection["earliest_considered"]
    by_key = {_version_key(receipt): receipt for receipt in receipts}
    latest = selection["latest_seen_time"]
    for raw_candidate in completed:
        candidate = _candidate_copy(raw_candidate)
        resource_prefix = _failure_resource_prefix(candidate)
        failed_tokens = {token for token in failed_tokens if not token.startswith(resource_prefix)}
        failure_examples = [
            example
            for example in failure_examples
            if not str(example["token"]).startswith(resource_prefix)
        ]
        candidate_time = str(candidate["candidate_time"])
        if earliest is not None and _time_sort_value(candidate_time) < _time_sort_value(earliest):
            continue
        key = _version_key(candidate)
        receipt = _receipt(candidate)
        current = by_key.get(key)
        if current is None or _time_sort_value(candidate_time) > _time_sort_value(current["candidate_time"]):
            by_key[key] = receipt
        if latest is None or _time_sort_value(candidate_time) > _time_sort_value(latest):
            latest = candidate_time
    selection["completed"] = sorted(by_key.values(), key=_receipt_sort_key)
    selection["latest_seen_time"] = latest
    failed_selection["tokens"] = sorted(failed_tokens)
    failed_selection["examples"] = failure_examples
    selection["failed"] = failed_selection
    updated[_SELECTION_KEY] = selection
    return updated


def record_failed_candidates(
    cursor: dict[str, object] | None,
    failures: tuple[dict[str, object], ...],
    *,
    failed_at: str,
) -> dict[str, object]:
    """Return a copied cursor with opaque failed-version quarantine receipts."""

    if cursor is not None and not isinstance(cursor, dict):
        raise ValueError("cursor must be an object or null")
    if not isinstance(failures, tuple):
        raise ValueError("failed candidates must be a tuple")
    normalized_failed_at = _normalized_time(failed_at)
    updated = deepcopy(cursor) if cursor is not None else {}
    selection = _selection_copy(updated)
    failed_selection = cast(dict[str, object], selection["failed"])
    tokens = set(cast(list[str], failed_selection["tokens"]))
    examples = {
        str(example["token"]): example
        for example in cast(list[dict[str, object]], failed_selection["examples"])
    }
    latest = selection["latest_seen_time"]
    for raw_failure in failures:
        candidate = _candidate_copy(raw_failure)
        item_ref = raw_failure.get("item_ref")
        code = raw_failure.get("code")
        message = raw_failure.get("message")
        if not isinstance(item_ref, str) or not item_ref.strip() or len(item_ref) > 256:
            raise ValueError("failed candidate item_ref must be a bounded non-empty string")
        if isinstance(code, bool) or not isinstance(code, int):
            raise ValueError("failed candidate code must be an integer")
        if not isinstance(message, str) or not message.strip() or len(message) > 256:
            raise ValueError("failed candidate message must be a bounded non-empty string")
        resource_prefix = _failure_resource_prefix(candidate)
        tokens = {token for token in tokens if not token.startswith(resource_prefix)}
        examples = {
            token: example
            for token, example in examples.items()
            if not token.startswith(resource_prefix)
        }
        token = _failure_token(candidate)
        tokens.add(token)
        examples[token] = {
            "token": token,
            "item_ref": item_ref,
            "code": code,
            "message": message,
            "failed_at": normalized_failed_at,
        }
        candidate_time = str(candidate["candidate_time"])
        if latest is None or _time_sort_value(candidate_time) > _time_sort_value(latest):
            latest = candidate_time
    failed_selection["tokens"] = sorted(tokens)
    failed_selection["examples"] = sorted(
        examples.values(),
        key=lambda example: (-_time_sort_value(example["failed_at"]), str(example["token"])),
    )[:_FAILURE_EXAMPLE_LIMIT]
    selection["failed"] = failed_selection
    selection["latest_seen_time"] = latest
    updated[_SELECTION_KEY] = selection
    return updated


def quarantined_candidate_diagnostics(
    cursor: dict[str, object] | None,
) -> tuple[int, tuple[dict[str, object], ...]]:
    """Return the quarantine count and bounded credential-free public examples."""

    selection = _selection_copy(cursor)
    failed_selection = cast(dict[str, object], selection["failed"])
    tokens = cast(list[str], failed_selection["tokens"])
    examples = cast(list[dict[str, object]], failed_selection["examples"])
    public_examples = tuple(
        {field_name: deepcopy(example[field_name]) for field_name in _FAILURE_FIELDS if field_name != "token"}
        for example in examples
    )
    return len(tokens), public_examples


def compact_cursor(
    cursor: dict[str, object],
    *,
    hard_limit_bytes: int = 512 * 1024,
    target_bytes: int = 384 * 1024,
) -> dict[str, object]:
    """Drop complete oldest receipt-time groups while advancing a permanent cutoff."""

    if not isinstance(cursor, dict):
        raise ValueError("cursor must be an object")
    if isinstance(hard_limit_bytes, bool) or not isinstance(hard_limit_bytes, int):
        raise ValueError("cursor byte limits are invalid")
    if isinstance(target_bytes, bool) or not isinstance(target_bytes, int):
        raise ValueError("cursor byte limits are invalid")
    if target_bytes <= 0 or hard_limit_bytes < target_bytes:
        raise ValueError("cursor byte limits are invalid")
    compacted = deepcopy(cursor)
    if _encoded_size(compacted) <= target_bytes:
        return compacted
    if _SELECTION_KEY not in compacted:
        raise ValueError("cursor cannot be compacted below the hard limit")
    selection = _selection_copy(compacted)
    receipts = cast(list[dict[str, str]], selection["completed"])
    existing_cutoff = selection["earliest_considered"]
    if existing_cutoff is not None:
        receipts = [
            receipt
            for receipt in receipts
            if _time_sort_value(receipt["candidate_time"]) >= _time_sort_value(existing_cutoff)
        ]
    groups: dict[str, list[dict[str, str]]] = {}
    for receipt in receipts:
        groups.setdefault(str(receipt["candidate_time"]), []).append(receipt)
    ordered_times = sorted(groups, key=_time_sort_value)
    while len(ordered_times) > 1:
        retained_times = ordered_times[1:]
        retained = [receipt for timestamp in retained_times for receipt in groups.get(timestamp, [])]
        cutoff = retained_times[0]
        if existing_cutoff is not None and _time_sort_value(existing_cutoff) > _time_sort_value(cutoff):
            cutoff = str(existing_cutoff)
        selection["completed"] = sorted(retained, key=_receipt_sort_key)
        selection["earliest_considered"] = cutoff
        compacted[_SELECTION_KEY] = selection
        ordered_times = retained_times
        if _encoded_size(compacted) <= target_bytes:
            return compacted
    selection["completed"] = sorted(
        [receipt for timestamp in ordered_times for receipt in groups.get(timestamp, [])],
        key=_receipt_sort_key,
    )
    if ordered_times:
        cutoff = ordered_times[0]
        if existing_cutoff is not None and _time_sort_value(existing_cutoff) > _time_sort_value(cutoff):
            cutoff = str(existing_cutoff)
        selection["earliest_considered"] = cutoff
    compacted[_SELECTION_KEY] = selection
    if _encoded_size(compacted) > hard_limit_bytes:
        raise ValueError("cursor cannot be compacted below the hard limit")
    return compacted
