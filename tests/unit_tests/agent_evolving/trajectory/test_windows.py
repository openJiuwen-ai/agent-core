# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context-window replay over v2 commit chains."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map, iter_spans
from openjiuwen.agent_evolving.trajectory.windows import (
    apply_context_delta,
    iter_trajectory_events,
    rebase_window_chain,
    replay_windows,
    window_for_inference,
)
from openjiuwen.extensions.observability import semconv

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


def test_trimmed_window_keeps_events_off_the_budget_and_stays_replayable() -> None:
    from openjiuwen.agent_evolving.trajectory.windows import trim_trajectory_window

    spans = [span for sequence, commit in enumerate(_chain(), 1) for span in (_request(sequence), commit)]
    history = _trajectory(spans)

    trimmed = trim_trajectory_window(history, 1)

    kept = list(iter_spans(trimmed))
    assert [span["spanId"] for span in kept if span["name"] == "chat model"] == ["inference-3"]
    replay = replay_windows(trimmed)
    assert replay.issues == ()
    assert _ids(window_for_inference(replay, {"spanId": "inference-3"})) == ["a", "b", "c"]
    # Nothing beyond the request, its own commit and the restated base.
    assert len(kept) == 3
