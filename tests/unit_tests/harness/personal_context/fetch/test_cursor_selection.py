from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from openjiuwen.harness.personal_context.fetch.cursor_selection import (
    compact_cursor,
    record_completed_candidates,
    select_latest_candidates,
)


_BASE = datetime(2026, 8, 1, tzinfo=UTC)


def _candidate(
    index: int,
    *,
    revision: str = "revision-1",
    candidate_time: datetime | None = None,
    lane: str = "document",
) -> dict[str, object]:
    timestamp = candidate_time or (_BASE + timedelta(hours=index))
    return {
        "stable_id": f"item-{index:05d}",
        "revision_id": revision,
        "candidate_time": timestamp.isoformat().replace("+00:00", "Z"),
        "resource_lane": lane,
        "locator": f"https://example.test/items/{index}",
    }


def _ids(candidates: tuple[dict[str, object], ...]) -> list[str]:
    return [str(candidate["stable_id"]) for candidate in candidates]


def test_latest_pending_items_fill_each_run_without_crossing_unfinished_holes() -> None:
    cursor = record_completed_candidates(None, (_candidate(100),))
    available = tuple(_candidate(index) for index in range(90, 111))

    expected = (
        ["item-00110", "item-00109", "item-00108"],
        ["item-00107", "item-00106", "item-00105"],
        ["item-00104", "item-00103", "item-00102"],
        ["item-00101", "item-00099", "item-00098"],
    )
    for expected_ids in expected:
        selected = select_latest_candidates(available, cursor, 3)
        assert _ids(selected) == expected_ids
        cursor = record_completed_candidates(cursor, selected)

    newly_visible = tuple(_candidate(index) for index in range(90, 114))
    assert _ids(select_latest_candidates(newly_visible, cursor, 3)) == [
        "item-00113",
        "item-00112",
        "item-00111",
    ]


def test_same_timestamp_has_stable_order_and_remainder_survives_next_run() -> None:
    shared_time = datetime(2026, 8, 25, 8, tzinfo=UTC)
    candidates = tuple(
        {
            **_candidate(index, candidate_time=shared_time, lane=lane),
            "stable_id": stable_id,
        }
        for index, lane, stable_id in (
            (3, "wiki", "c"),
            (2, "document", "b"),
            (1, "document", "a"),
        )
    )

    first = select_latest_candidates(candidates, None, 2)
    assert [(item["resource_lane"], item["stable_id"]) for item in first] == [
        ("document", "a"),
        ("document", "b"),
    ]
    cursor = record_completed_candidates(None, first)
    second = select_latest_candidates(candidates, cursor, 2)
    assert [(item["resource_lane"], item["stable_id"]) for item in second] == [("wiki", "c")]


def test_new_revision_is_prioritized_over_older_unfinished_history() -> None:
    completed = _candidate(50, revision="revision-1", candidate_time=_BASE + timedelta(hours=10))
    latest_boundary = _candidate(100, candidate_time=_BASE + timedelta(hours=30))
    cursor = record_completed_candidates(None, (completed, latest_boundary))
    changed = _candidate(50, revision="revision-2", candidate_time=_BASE + timedelta(hours=10))
    historical = _candidate(49, candidate_time=_BASE + timedelta(hours=20))

    selected = select_latest_candidates((historical, changed), cursor, 1)

    assert selected == (changed,)


def test_recording_is_discrete_deduplicated_and_does_not_mutate_input() -> None:
    original = {"provider_page": "token-a"}
    completed = (_candidate(1), _candidate(1), _candidate(2))

    updated = record_completed_candidates(original, completed)

    assert original == {"provider_page": "token-a"}
    assert updated["provider_page"] == "token-a"
    selection = updated["_selection"]
    assert isinstance(selection, dict)
    assert len(selection["completed"]) == 2


def test_compaction_keeps_complete_newest_time_groups_and_monotonic_cutoff() -> None:
    cursor: dict[str, object] | None = None
    candidates: list[dict[str, object]] = []
    for index in range(6000):
        group_time = _BASE + timedelta(hours=index // 25)
        candidate = {
            **_candidate(index, candidate_time=group_time),
            "locator": "https://example.test/" + ("x" * 96) + f"/{index}",
        }
        candidates.append(candidate)
    cursor = record_completed_candidates(cursor, tuple(candidates))
    assert len(json.dumps(cursor, separators=(",", ":")).encode()) > 512 * 1024

    compacted = compact_cursor(cursor)

    encoded = json.dumps(compacted, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= 384 * 1024
    selection = compacted["_selection"]
    assert isinstance(selection, dict)
    receipts = selection["completed"]
    assert isinstance(receipts, list)
    remaining_times = {str(receipt["candidate_time"]) for receipt in receipts}
    original_counts: dict[str, int] = {}
    remaining_counts: dict[str, int] = {}
    for candidate in candidates:
        timestamp = str(candidate["candidate_time"])
        original_counts[timestamp] = original_counts.get(timestamp, 0) + 1
    for receipt in receipts:
        timestamp = str(receipt["candidate_time"])
        remaining_counts[timestamp] = remaining_counts.get(timestamp, 0) + 1
    assert all(remaining_counts[timestamp] == original_counts[timestamp] for timestamp in remaining_times)
    assert selection["earliest_considered"] == min(remaining_times)

    previous_cutoff = str(selection["earliest_considered"])
    older = _candidate(99999, candidate_time=_BASE)
    updated = record_completed_candidates(compacted, (older,))
    recompressed = compact_cursor(updated)
    next_selection = recompressed["_selection"]
    assert str(next_selection["earliest_considered"]) >= previous_cutoff
    assert select_latest_candidates((older,), recompressed, 1) == ()


def test_compaction_fails_when_non_selection_provider_state_alone_exceeds_limit() -> None:
    oversized = {"provider_state": "x" * (512 * 1024)}

    try:
        compact_cursor(oversized)
    except ValueError as exc:
        assert "cursor" in str(exc)
    else:
        raise AssertionError("oversized provider cursor must fail closed")
