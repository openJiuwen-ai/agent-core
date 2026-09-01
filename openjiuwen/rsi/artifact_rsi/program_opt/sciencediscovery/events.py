# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The event stream this sidecar emits, and its NDJSON encoding.

Mirrors ``EvolveEvent`` / ``EvolveEventRecord`` in ``packages/schema`` — the Node
API parses exactly these shapes. Kept as plain dicts built by small helpers
rather than as pydantic models: the union has ten arms whose only consumer is
``json.dumps``, and a second schema definition here would be a second thing to
keep in sync.

Two encoding rules that are **not** style choices:

* ``-inf`` never goes on the wire. ``json.dumps`` writes it as the bare token
  ``-Infinity``, which is not valid JSON and which strict parsers (including
  ``JSON.parse``) reject. A failed candidate carries ``score: null`` and is
  marked ``valid: false``; the node still enters the tree, because dropping it
  would change the rank denominator of every later iteration.
* Counters are **absolute, never deltas**. ``visits`` is the non-idempotent
  occupancy) are the only non-idempotent quantities in the system; a replayed
  delta double-counts where a replayed absolute does not. That is what lets the
  API drop everything at or below its watermark and lets a mid-run Neo4j outage
  be repaired by replaying the log.
"""

from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

#: What the sidecar hands its engines: one call per event.
Emit = Callable[[dict[str, Any]], None]


def finite(value: float | None) -> float | None:
    """``None`` for anything that is not a finite number.

    The failure sentinel upstream is ``-inf``; see the module docstring for why
    it must not reach the wire.
    """
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventStream:
    """Assigns the monotonic sequence numbers the API's watermark relies on.

    Sequence numbering starts at 1 and is per search, so a resumed run continues
    the numbering it was handed rather than restarting (``start_at``).

    **Locked**, because with parallel workers `emit` is called from N threads at
    once and `+= 1` is not atomic. Two events sharing a sequence number is not a
    cosmetic problem: the API drops everything at or below its watermark, so the
    second one is silently discarded as a replay.
    """

    def __init__(self, start_at: int = 0) -> None:
        self._sequence = start_at
        self._lock = threading.Lock()

    @property
    def last_sequence(self) -> int:
        with self._lock:
            return self._sequence

    def record(self, event: dict[str, Any], created_at: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        return {
            "createdAt": created_at or utc_now(),
            "event": event,
            "sequence": sequence,
        }


#: A keep-alive. The NDJSON reader skips empty lines, so this carries no
#: sequence number, writes nothing to the log, and cannot be mistaken for
#: something that happened — it only proves the socket is still alive during a
#: long expansion.
HEARTBEAT = b"\n"


def encode_ndjson(records: Iterator[dict[str, Any] | bytes]) -> Iterator[bytes]:
    """One JSON object per line. ``allow_nan=False`` makes the -inf rule a
    crash here rather than a parse error three processes downstream.

    Raw bytes pass through untouched: that is how the keep-alive gets onto the
    wire without pretending to be a record.
    """
    for record in records:
        if isinstance(record, bytes):
            yield record
            continue
        yield (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


# --- Event constructors -----------------------------------------------------
# One per arm of the schema union. Thin by design: the value of having them is
# that the field names live in exactly one place on this side of the wire.


def search_started(algorithm: str, scorecard_hash: str) -> dict[str, Any]:
    return {"algorithm": algorithm, "scorecardHash": scorecard_hash, "type": "search_started"}


def seeded(
    node_index: int,
    baseline_score: float | None,
    *,
    code_hash: str | None = None,
    code_chars: int | None = None,
    metrics: dict[str, float] | None = None,
) -> dict[str, Any]:
    """The root node, carrying its source the same way an expansion does.

    `metrics` is the root's raw measurements, and it is here so the run can be
    resumed. Every `relative_to_baseline` criterion divides by this reference,
    so a resume that re-measured the root instead would move it -- `seconds`
    alone differs run to run -- and every score already written down would be
    on a different scale from every score after the restart.

    `code_hash` was missing here while `expanded` had it, and the detail view
    diffs a candidate against `parent.codeHash`. Almost every node's parent is
    the root — a flat tree is this search's normal shape — so with no hash on the seed
    the "before" side was empty and *every* diff rendered as pure addition,
    with nothing ever shown as removed.
    """
    event: dict[str, Any] = {
        "baselineScore": finite(baseline_score),
        "nodeIndex": node_index,
        "type": "seeded",
    }
    if metrics:
        event["metrics"] = {key: round(float(value), 6) for key, value in metrics.items()}
    if code_hash:
        event["codeHash"] = code_hash
    if code_chars:
        event["codeChars"] = code_chars
    return event


def selected(
    node_index: int,
    ancestor_visits: list[dict[str, int]],
    rank_score: float | None = None,
    puct: float | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "ancestorVisits": ancestor_visits,
        "nodeIndex": node_index,
        "type": "selected",
    }
    if rank_score is not None:
        event["rankScore"] = round(rank_score, 6)
    if puct is not None:
        event["puct"] = round(puct, 6)
    return event


def expanded(
    node_index: int,
    parent_index: int | None,
    depth: int,
    score: float | None,
    valid: bool,
    *,
    change_summary: str | None = None,
    code_hash: str | None = None,
    code_chars: int | None = None,
    error: str | None = None,
    iteration: int | None = None,
    promise: float | None = None,
    worker: int | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "depth": depth,
        "nodeIndex": node_index,
        "parentIndex": parent_index,
        "score": finite(score),
        "type": "expanded",
        "valid": valid,
    }
    for key, value in (
        ("changeSummary", change_summary),
        ("codeHash", code_hash),
        ("codeChars", code_chars),
        ("error", error),
        ("iteration", iteration),
        ("promise", None if promise is None else round(float(promise), 4)),
        ("worker", worker),
    ):
        if value is not None:
            event[key] = value
    return event


def evaluated(
    node_index: int,
    reward: float,
    criteria: dict[str, float],
    *,
    gate_score: float | None = None,
    rollout_score: float | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "criteria": {key: round(value, 6) for key, value in criteria.items()},
        "nodeIndex": node_index,
        "reward": round(reward, 6),
        "type": "evaluated",
    }
    if gate_score is not None:
        event["gateScore"] = round(gate_score, 6)
    if rollout_score is not None:
        event["rolloutScore"] = round(rollout_score, 6)
    return event


def merged(
    node_index: int,
    accepted: bool,
    reason: str,
    *,
    category: str | None = None,
    rejected_by: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "accepted": accepted,
        "nodeIndex": node_index,
        "reason": reason,
        "type": "merged",
    }
    if category is not None:
        event["category"] = category
    if rejected_by is not None:
        event["rejectedBy"] = rejected_by
    return event


def cost(tokens: int, cents: int) -> dict[str, Any]:
    return {"cents": cents, "tokens": tokens, "type": "cost"}


def search_finished(
    status: str,
    best_node_index: int | None,
    candidates: int,
    *,
    best_test_score: float | None = None,
    stop_reason: str = "",
    expansions_planned: int | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "bestNodeIndex": best_node_index,
        "candidates": candidates,
        "status": status,
        "type": "search_finished",
    }
    if best_test_score is not None:
        event["bestTestScore"] = round(best_test_score, 6)
    # Recorded even when it is the dull one. Whether a run that planned 20
    # expansions and made 8 hit its iteration cap, lost its workers or timed
    # out is answerable only from here, and only if it is written down while
    # the framework's result is still in hand.
    if stop_reason:
        event["stopReason"] = stop_reason
    if expansions_planned is not None:
        event["expansionsPlanned"] = expansions_planned
    return event


def log(level: str, message: str) -> dict[str, Any]:
    return {"level": level, "message": message, "type": "log"}
