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

"""A deterministic fake search: no model calls, no sandbox, no randomness.

Its job is to let everything *around* the engine — the run lifecycle, the SSE
bridge, the graph mirror, the dashboard and the search canvas — be built and
tested end to end before a single model token is spent, and to keep being the
fixture those layers are tested against afterwards.

Two decisions make it worth more than a happy-path mock:

**It runs the real selection rule.** Flat PUCT over *all* nodes
(``rank_score = rank / (N - 1)``, single node → 0.5;
``puct = rank_score + c_puct · (1/N) · √Σvisits / (1 + visits)``), with the visit
reserved at selection time. So the fake tree has the shape a real one has —
root-heavy early, branching once siblings exist — and the canvas is exercised
against something it will actually see. The formula is ~15 lines and gives the
vendored tree an obvious place to slot in later.

**It exercises the paths that are easy to get wrong**, not just the good one:
one expansion fails (``score: null`` + ``valid: false``, and the node still
enters the tree), and one is refused by a constraint
(``category: "constraint-violated"``) rather than by the statistical gate. Those
are precisely the two cases the UI must render distinguishably.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Callable

from . import events
from .engine import RunSpec
from .events import Emit
from .logging_config import get_logger

log = get_logger("stub")

#: Score deltas applied to the selected parent, cycled. ``None`` is a failed
#: expansion. Fixed rather than random so the whole event sequence is a
#: function of (expansions, baseline) alone — the frontend and graph tests
#: assert against it.
_SCORE_STEPS: tuple[float | None, ...] = (0.062, 0.041, None, 0.028, 0.019, 0.013, 0.009, 0.006)

#: 1-based expansion index that is refused by a scorecard constraint. Chosen to
#: land after a failure so the two refusal kinds appear in one short run.
_CONSTRAINED_EXPANSION = 5
_CONSTRAINT_ID = "too-slow"

_DEFAULT_BASELINE = 0.5
_C_PUCT = 1.0


@dataclass
class _Node:
    index: int
    parent_index: int | None
    depth: int
    score: float | None
    visits: int = 0


class StubEngine:
    """The deterministic engine. Stateless between runs."""

    name = "stub"
    #: Nothing is executed, so no isolation is needed. Every engine that runs
    #: candidate code sets this and is refused without a sandbox.
    requires_sandbox = False

    def run(self, spec: RunSpec, emit: Emit, should_stop: Callable[[], bool]) -> None:
        baseline = _DEFAULT_BASELINE if spec.baseline_score is None else spec.baseline_score
        emit(events.search_started(spec.algorithm, spec.scorecard_hash))

        nodes: list[_Node] = [_Node(index=0, parent_index=None, depth=0, score=baseline)]
        emit(events.seeded(0, baseline))

        status = "succeeded"
        tokens = 0
        for iteration in range(1, spec.expansions + 1):
            if should_stop():
                status = "stopped"
                log.info("stub run %s stopped at iteration %d", spec.search_id, iteration)
                break

            parent, rank_score, puct = _select(nodes)
            ancestors = _reserve_visits(nodes, parent)
            emit(events.selected(parent.index, ancestors, rank_score, puct))

            index = len(nodes)
            step = _SCORE_STEPS[(iteration - 1) % len(_SCORE_STEPS)]
            valid = step is not None
            score = None if step is None else _clamp((parent.score or baseline) + step)
            node = _Node(index=index, parent_index=parent.index, depth=parent.depth + 1, score=score)
            node.visits = 1
            nodes.append(node)

            emit(events.expanded(
                index,
                parent.index,
                node.depth,
                score,
                valid,
                change_summary=_change_summary(iteration, valid),
                code_hash=_code_hash(spec.search_id, index),
                code_chars=420 + 37 * index,
                error=None if valid else "the candidate failed to run: SyntaxError: unexpected EOF while parsing",
                iteration=iteration,
                worker=(iteration - 1) % max(spec.workers, 1),
            ))

            if valid and score is not None:
                emit(events.evaluated(index, score, {"stub": score}, gate_score=score, rollout_score=score))
                if iteration == _CONSTRAINED_EXPANSION:
                    emit(events.merged(
                        index, False, "training time 412s is over the 300s veto limit",
                        category="constraint-violated", rejected_by=_CONSTRAINT_ID,
                    ))
                else:
                    accepted = score > _best_score(nodes[:-1], baseline)
                    emit(events.merged(
                        index, accepted,
                        "the hold-out gate score improved" if accepted else "the improvement is not significant",
                        category=None if accepted else "below-threshold",
                    ))
            else:
                emit(events.merged(
                    index, False, "the candidate would not run; recorded as a failed node", category="below-threshold",
                ))

            tokens += 1400 + 60 * iteration
            emit(events.cost(tokens, tokens // 100))

        best = _best_node(nodes)
        emit(events.search_finished(
            status,
            best.index if best else None,
            len(nodes),
            best_test_score=best.score if best else None,
        ))
        log.info(
            "stub run %s finished: status=%s nodes=%d best=%s",
            spec.search_id, status, len(nodes), best.index if best else None,
        )


# --- Flat PUCT --------------------------------------------------------------


def _select(nodes: list[_Node]) -> tuple[_Node, float, float]:
    """``argmax(puct)`` over **every** node — there is no descent from the root,
    which is what "flat" names. Exploitation enters through the *rank*, not the
    raw score, so the exploration constant means the same thing whatever the
    metric's units are."""
    ranked = sorted(nodes, key=lambda node: (node.score is not None, node.score or 0.0))
    total = len(nodes)
    total_visits = sum(node.visits for node in nodes)
    best: tuple[_Node, float, float] | None = None
    for rank, node in enumerate(ranked):
        rank_score = 0.5 if total == 1 else rank / (total - 1)
        explore = _C_PUCT * (1.0 / total) * math.sqrt(max(total_visits, 0)) / (1 + node.visits)
        puct = rank_score + explore
        if best is None or puct > best[2]:
            best = (node, rank_score, puct)
    assert best is not None
    return best


def _reserve_visits(nodes: list[_Node], selected: _Node) -> list[dict[str, int]]:
    """Backpropagate one visit to the selected node and every ancestor, and
    return their **absolute** counts.

    Reserving at selection rather than after execution is the standard parallel
    virtual loss: without it ``argmax(puct)`` is deterministic and every worker
    in a batch is handed the same parent. With one proposal in flight nothing can
    observe the tree between the two points, so it is the same algorithm.
    """
    touched: list[dict[str, int]] = []
    cursor: _Node | None = selected
    while cursor is not None:
        cursor.visits += 1
        touched.append({"nodeIndex": cursor.index, "visits": cursor.visits})
        cursor = None if cursor.parent_index is None else nodes[cursor.parent_index]
    return touched


# --- Helpers ----------------------------------------------------------------


def _clamp(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 6)


def _best_score(nodes: list[_Node], baseline: float) -> float:
    scores = [node.score for node in nodes if node.score is not None]
    return max(scores) if scores else baseline


def _best_node(nodes: list[_Node]) -> _Node | None:
    scored = [node for node in nodes if node.score is not None]
    return max(scored, key=lambda node: node.score or 0.0) if scored else None


def _change_summary(iteration: int, valid: bool) -> str:
    if not valid:
        return "rewrote the feature engineering section (the candidate failed the syntax check)"
    return f"mutation {iteration}: added interaction features and adjusted the regularisation strength"


def _code_hash(search_id: str, index: int) -> str:
    digest = hashlib.sha256(f"{search_id}:{index}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
