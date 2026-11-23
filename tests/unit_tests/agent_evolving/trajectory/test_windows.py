# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context-window replay over v2 commit chains."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.agent_evolving.trajectory.messages import trajectory_to_messages
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map, iter_spans, span_attributes
from openjiuwen.agent_evolving.trajectory.windows import (
    apply_context_delta,
    iter_trajectory_events,
    rebase_window_chain,
    replay_windows,
    window_for_inference,
)
from openjiuwen.extensions.observability import semconv
from openjiuwen.extensions.observability.content_addressing import SequenceHead, build_sequence
from openjiuwen.extensions.observability.sequence_registry import lookup_sequence_head, register_sequence

_VECTORS = Path(__file__).parent / "fixtures" / "v2"
_SCHEMA = (
    Path(__file__).parents[4]
    / "openjiuwen"
    / "extensions"
    / "observability"
    / "schemas"
    / "trajectory_v2_payloads.schema.json"
)


def _trajectory(spans: list[dict[str, Any]]) -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": attributes_from_map({TRAJECTORY_ID: "windows"})},
                    "scopeSpans": [{"scope": {}, "spans": spans}],
                }
            ]
        }
    )


def _ids(window) -> list[str]:
    return [message["message_id"] for message in window]


def _vectors() -> list[Path]:
    return sorted(_VECTORS.glob("*.json"))


@pytest.mark.parametrize("path", _vectors(), ids=lambda path: path.stem)
def test_replay_matches_shared_vector(path: Path) -> None:
    vector = json.loads(path.read_text(encoding="utf-8"))
    replay = replay_windows(_trajectory(vector["records"]))
    expected = vector["expected"]

    windows = {
        subject: {window_id: _ids(window) for window_id, window in subject_windows.items()}
        for subject, subject_windows in replay.windows.items()
    }
    assert windows == expected["windows"]
    assert {span_id: _ids(window) for span_id, window in replay.by_inference.items()} == expected["by_inference"]
    assert [issue["code"] for issue in replay.issues] == expected["issue_codes"]
    for window_id, contents in expected.get("window_contents", {}).items():
        window = next(w[window_id] for w in replay.windows.values() if window_id in w)
        assert [message["content"] for message in window] == contents


@pytest.mark.parametrize("path", _vectors(), ids=lambda path: path.stem)
def test_vector_payloads_follow_the_published_schema(path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    vector = json.loads(path.read_text(encoding="utf-8"))
    events = iter_trajectory_events(_trajectory(vector["records"]))
    assert len(events) == len(vector["records"])
    errors = []
    for event in events:
        pointer = schema["eventKinds"][event.event_kind]
        validator = jsonschema.Draft202012Validator({**schema, "$ref": pointer})
        errors.extend(validator.iter_errors(dict(event.payload)))
    assert (not errors) is vector.get("payloads_conform_to_schema", True)


def _message(message_id: str, content: str = "") -> dict[str, Any]:
    return {"message_id": message_id, "role": "user", "origin": "harness_internal", "content": content or message_id}


def test_delta_operations_apply_in_order() -> None:
    base = [_message("a"), _message("b"), _message("c")]
    rebuilt = apply_context_delta(
        base,
        [
            {"op": "remove", "message_id": "b", "index": 1},
            {"op": "insert", "message_id": "d", "index": 0, "message": _message("d")},
            {"op": "move", "message_id": "a", "from_index": 1, "index": 2},
            {"op": "replace", "message_id": "c", "index": 1, "message": _message("c", "changed")},
        ],
    )
    assert rebuilt is not None
    assert _ids(rebuilt) == ["d", "c", "a"]
    assert rebuilt[1]["content"] == "changed"
    assert _ids(base) == ["a", "b", "c"]


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "insert", "message_id": "a", "index": 0, "message": _message("a")},
        {"op": "insert", "message_id": "z", "index": 5, "message": _message("z")},
        {"op": "remove", "message_id": "missing"},
        {"op": "move", "message_id": "a", "index": 1},
    ],
)
def test_delta_that_does_not_fit_the_base_is_refused(operation: dict[str, Any]) -> None:
    assert apply_context_delta([_message("a")], [operation]) is None


def _event_span(
    sequence: int,
    payload: dict[str, Any],
    *,
    parent: str,
    subject: str = "main",
    epoch: str = "epoch",
) -> dict[str, Any]:
    return {
        "traceId": "1" * 32,
        "spanId": f"{sequence:016x}",
        "parentSpanId": parent,
        "name": "context.window.commit",
        "startTimeUnixNano": str(sequence * 10),
        "endTimeUnixNano": str(sequence * 10),
        "attributes": attributes_from_map(
            {
                semconv.OJ_TRAJECTORY_RECORD_KIND: "event",
                semconv.OJ_TRAJECTORY_EVENT_ID: f"event-{subject}-{sequence}",
                semconv.OJ_TRAJECTORY_EVENT_KIND: "context.window.commit",
                semconv.OJ_TRAJECTORY_SUBJECT_ID: subject,
                semconv.OJ_TRAJECTORY_SEQUENCE_EPOCH: epoch,
                semconv.OJ_TRAJECTORY_SUBJECT_SEQUENCE: sequence,
                semconv.OJ_TRAJECTORY_RECORDED_AT_UNIX_NANO: sequence * 10,
                semconv.OJ_TRAJECTORY_PAYLOAD: json.dumps(payload),
            }
        ),
    }


def _chain() -> list[dict[str, Any]]:
    a, b, c = _message("a"), _message("b"), _message("c")
    return [
        _event_span(
            1,
            {
                "window_id": "w1",
                "base_window_id": None,
                "complete": True,
                "delta": [],
                "messages": [a],
                "transition_kind": "epoch_baseline",
                "baseline_reason": "runtime_epoch_start",
            },
            parent="inference-1",
        ),
        _event_span(
            2,
            {
                "window_id": "w2",
                "base_window_id": "w1",
                "complete": True,
                "delta": [{"op": "insert", "message_id": "b", "index": 1, "message": b}],
            },
            parent="inference-2",
        ),
        _event_span(
            3,
            {
                "window_id": "w3",
                "base_window_id": "w2",
                "complete": True,
                "delta": [{"op": "insert", "message_id": "c", "index": 2, "message": c}],
            },
            parent="inference-3",
        ),
    ]


def test_window_for_inference_is_the_window_its_commit_states() -> None:
    replay = replay_windows(_trajectory(_chain()))
    assert _ids(window_for_inference(replay, {"spanId": "inference-3"})) == ["a", "b", "c"]
    assert window_for_inference(replay, {"spanId": "inference-9"}) is None
    assert replay.issues == ()


def test_commit_without_its_inference_parent_is_reported() -> None:
    orphan = _chain()[0]
    del orphan["parentSpanId"]
    replay = replay_windows(_trajectory([orphan]))
    assert [issue["code"] for issue in replay.issues] == ["v2.missing_physical_request"]
    assert replay.by_inference == {}


def test_trimmed_chain_gets_a_replayable_baseline() -> None:
    history = _trajectory(_chain())
    retained = _trajectory(_chain()[2:])
    assert [issue["code"] for issue in replay_windows(retained).issues] == ["v2.missing_base_window"]

    rebased = rebase_window_chain(history, retained)

    replay = replay_windows(rebased)
    assert replay.issues == ()
    assert _ids(replay.by_inference["inference-3"]) == ["a", "b", "c"]
    # The restated base is not a request of its own.
    assert set(replay.by_inference) == {"inference-3"}
    names = [span["name"] for span in iter_spans(rebased)]
    assert names == ["context.window.commit", "context.window.commit"]
    # A chain that already starts at a baseline is left alone.
    assert rebase_window_chain(history, history) is history


def _request(sequence: int) -> dict[str, Any]:
    return {
        "traceId": "1" * 32,
        "spanId": f"inference-{sequence}",
        "name": "chat model",
        "startTimeUnixNano": str(sequence * 10 - 5),
        "endTimeUnixNano": str(sequence * 10 + 5),
        "attributes": attributes_from_map({semconv.GEN_AI_OPERATION_NAME: "chat"}),
    }


def test_trimmed_window_keeps_events_off_the_budget_and_stays_replayable(monkeypatch) -> None:
    from openjiuwen.agent_evolving.trajectory.windows import trim_trajectory_window

    spans = [span for sequence, commit in enumerate(_chain(), 1) for span in (_request(sequence), commit)]
    key = semconv.GEN_AI_INPUT_MESSAGES
    stated = '[{"role":"user","parts":[{"type":"text","content":"kept prompt"}]}]'
    source = build_sequence(key, stated)
    assert source is not None and register_sequence(source)
    head = lookup_sequence_head(source.seq_hash)
    assert head is not None
    spans[-2]["attributes"].extend(attributes_from_map({key: head}))
    history = _trajectory(spans)
    with monkeypatch.context() as patch:
        def unexpected_read(_head):
            pytest.fail("window trimming and rebase must not materialize GenAI messages")

        patch.setattr(SequenceHead, "materialize", unexpected_read)
        trimmed = trim_trajectory_window(history, 1)
        assert trajectory_to_messages(trimmed)

    kept = list(iter_spans(trimmed))
    assert [span["spanId"] for span in kept if span["name"] == "chat model"] == ["inference-3"]
    replay = replay_windows(trimmed)
    assert replay.issues == ()
    assert _ids(window_for_inference(replay, {"spanId": "inference-3"})) == ["a", "b", "c"]
    # Nothing beyond the request, its own commit and the restated base.
    assert len(kept) == 3
    assert next(span_attributes(span)[key] for span in kept if span["name"] == "chat model") == stated


@pytest.mark.parametrize("limit", [None, 3, 4])
def test_online_window_matches_first_batch_and_followup_duplicate_rules(monkeypatch, limit) -> None:
    from openjiuwen.agent_evolving.trajectory import spans
    from openjiuwen.agent_evolving.trajectory.windows import OnlineTrajectoryWindow, trim_trajectory_window

    metadata = {TRAJECTORY_ID: "windows"}

    def item(span_id, when, name):
        return {
            "traceId": "trace", "spanId": span_id, "name": name,
            "startTimeUnixNano": str(when), "endTimeUnixNano": str(when),
        }

    def batch(groups):
        return Trajectory.from_otlp({
            "resourceSpans": [
                {
                    "resource": {"attributes": attributes_from_map(metadata), "producer": group},
                    "schemaUrl": "resource-schema",
                    "scopeSpans": [{"scope": {"name": group}, "schemaUrl": "scope-schema", "spans": records}],
                }
                for group, records in groups
            ],
        })

    batches = [
        batch([
            ("primary", [item("a", 1, "first"), item("a", 1, "tied"), item("", 1, "missing-1")]),
            ("other", [item("a", 1, "other"), item("b", 2, "b")]),
        ]),
        batch([("primary", [item("a", 5, "replay"), item("c", 3, "new"), item("c", 4, "repeat"),
                            item("", 1, "missing-2")])]),
        batch([("other", [item("d", 0, "late"), item("e", 6, "newest")])]),
        batch([("primary", [item("d", 9, "late retry")])]),
    ]
    expected = None
    snapshots = []
    for increment in batches:
        merged = increment if expected is None else spans.merge_trajectories(expected, increment)
        expected = trim_trajectory_window(merged, limit)
        payload = expected.to_otlp()
        for resource in payload["resourceSpans"]:
            resource["resource"]["attributes"] = attributes_from_map(metadata)
        snapshots.append(payload)

    normalized_names = []
    normalize = spans.normalize_span

    def track(span):
        normalized_names.append(span["name"])
        return normalize(span)

    monkeypatch.setattr(spans, "normalize_span", track)
    window = OnlineTrajectoryWindow()
    expected_calls = [
        ["first", "tied", "missing-1", "other", "b"],
        ["new", "missing-2"],
        ["late", "newest"],
        [] if limit is None else ["late retry"],
    ]
    for increment, snapshot, calls in zip(batches, snapshots, expected_calls):
        normalized_names.clear()
        window = window.updated(increment, limit)
        assert window.snapshot(metadata).to_otlp() == snapshot
        assert window._identities == _payload_identities(snapshot)
        assert normalized_names == calls


def test_incremental_trim_repairs_commit_chain_without_normalizing_history(monkeypatch) -> None:
    from openjiuwen.agent_evolving.trajectory import spans
    from openjiuwen.agent_evolving.trajectory.windows import OnlineTrajectoryWindow, trim_trajectory_window

    records = _chain()
    initial = _trajectory([_request(1), records[0], _request(2), records[1]])
    addition = _trajectory([_request(3), records[2]])
    expected = trim_trajectory_window(spans.merge_trajectories(initial, addition), 2)
    window = OnlineTrajectoryWindow().updated(initial, 2)
    normalized = []
    normalize = spans.normalize_span

    def track(span):
        normalized.append(span["spanId"])
        return normalize(span)

    monkeypatch.setattr(spans, "normalize_span", track)
    window = window.updated(addition, 2)
    snapshot = window.snapshot({TRAJECTORY_ID: "windows"})
    assert snapshot == expected
    assert window._identities == _payload_identities(snapshot.to_otlp())
    assert normalized == [_request(3)["spanId"], records[2]["spanId"]]
    replay = replay_windows(snapshot)
    assert replay.issues == ()
    assert _ids(replay.by_inference["inference-3"]) == ["a", "b", "c"]


def _payload_identities(payload: dict[str, Any]) -> set[tuple[str, str]]:
    from openjiuwen.agent_evolving.trajectory.spans import iter_payload_spans, span_identity

    identities = (span_identity(span) for span in iter_payload_spans(payload))
    return {identity for identity in identities if identity is not None}


def test_shared_window_rebuilds_index_from_retained_spans(monkeypatch) -> None:
    from openjiuwen.agent_evolving.trajectory import spans
    from openjiuwen.agent_evolving.trajectory.windows import OnlineTrajectoryWindow

    metadata = {TRAJECTORY_ID: "windows"}
    trace_id = "1" * 32
    old_spans = [_request(index) for index in range(1, 201)]
    window = OnlineTrajectoryWindow().updated(_trajectory(old_spans), 200)
    original = window.updated(_trajectory([old_spans[-1]]), 200)
    original_payload = original.snapshot(metadata).to_otlp()
    window = original

    seen = []
    normalize = spans.normalize_span

    def track(span):
        seen.append(spans.span_identity(span))
        return normalize(span)

    monkeypatch.setattr(spans, "normalize_span", track)
    cases = [
        (_request(201), [201], range(2, 202)),
        (_request(201), [], range(2, 202)),
        (_request(0), [0], range(2, 202)),
        ({**_request(202), "spanId": "inference-1"}, [1], [*range(3, 202), 1]),
    ]
    for incoming, normalized, retained in cases:
        seen.clear()
        window = window.updated(_trajectory([incoming]), 200)
        snapshot = window.snapshot(metadata).to_otlp()
        assert seen == [(trace_id, f"inference-{number}") for number in normalized]
        assert [span["spanId"] for span in spans.iter_payload_spans(snapshot)] == [
            f"inference-{number}" for number in retained
        ]
        assert window._identities == _payload_identities(snapshot)

    assert original.snapshot(metadata).to_otlp() == original_payload
