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

"""Rebuilding a :class:`PuctTree` so an interrupted search can carry on.

**The tree snapshot and the candidate store hold different halves.** The
snapshot (``tree.json``) carries every node's index, parent, score, visit count
and rating; the candidate store holds the program bodies, content-addressed. So
a resume reads node *rows* from the snapshot and rehydrates the bodies by hash,
which is why a resume needs nothing beyond the same ``run_dir``.

What has to come back, and why each one:

``index``/``parent_index``
    The tree is append-only and a node's index is its identity in the graph.
    Restoring them is what stops a new node reusing an index the graph already
    spent -- the failure the old refusal existed to prevent.
``score``
    `FlatPuct` exploits *ranks*, so a missing score does not merely mis-place
    one node: it shifts the rank of every node above it.
``visits``
    Absolute, and the whole of the exploration term's denominator. Restarting
    them at zero would send the search back over ground it already covered.
``promise``
    The prior. Dropping it silently returns the run to a uniform `1/N` halfway
    through, which is a different search than the one the user started.
``metrics``
    Not for the search -- for the report. `gain`, the final test evaluation and
    the criteria columns all read them, and none of that survives a resume that
    keeps only the aggregate.

A **valid** node whose body the store cannot find is a refusal, not a gap: it
can still be selected -- its rank is real -- and the mutation prompt would then
be built from an empty parent, which spends a model call to ask for a rewrite of
nothing. An *invalid* node is the opposite case and must not refuse: the
reporter stores only text it was given, so a model timeout or an empty reply
leaves that node with no body by design, and treating the ordinary failure as
corruption made every run containing one permanently unresumable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .candidates import CandidateStore
from .logging_config import get_logger
from .program import Program, program_id
from .tree import Node, PuctTree

log = get_logger("restore")


class RestoreError(RuntimeError):
    """The rows cannot become a tree. A run-level fault, never a candidate one."""


def restore_tree(
    tree: PuctTree,
    rows: Sequence[Mapping[str, Any]],
    *,
    store: CandidateStore,
    search_id: str,
    fallback_code: str = "",
) -> PuctTree:
    """Fill ``tree`` from ``rows``, in index order.

    ``fallback_code`` stands in for the root only. The root's body is the
    baseline program the control plane staged for this run, so it is known
    without the store -- and a resume whose store was wiped can still get that
    one right.
    """
    if not rows:
        return tree
    if tree.nodes:
        raise RestoreError("the tree was already seeded; restoring on top would duplicate the root")

    ordered = sorted(rows, key=lambda row: int(row.get("index", 0)))
    missing: List[int] = []
    nodes: List[Node] = []

    for position, row in enumerate(ordered):
        index = int(row.get("index", position))
        if index != position:
            # Indices are positions in `tree.nodes`; a gap would make every
            # later `parent_index` point at the wrong node.
            raise RestoreError(
                f"the restored nodes are not contiguous: expected index {position}, got {index}"
            )
        code = _body(row, store=store, search_id=search_id,
                     fallback=fallback_code if index == 0 else "")
        if code is None:
            if row.get("valid"):
                missing.append(index)
                continue
            # A candidate that never produced text has no body to be missing:
            # the reporter only stores what it was given, so a model timeout or
            # an empty reply leaves `code_hash` null by design. Refusing here
            # meant one such candidate — the ordinary case, not the exotic one —
            # made the whole run unresumable for ever. It is restored as what it
            # was: a node holding its index, its rank and its failure.
            code = ""
        nodes.append(Node(
            index=index,
            parent_index=_parent(row, index),
            program=Program(
                program_id(code),
                int(row.get("iteration") or 0),
                None,
                code,
                str(row.get("change_summary") or ""),
                _metrics(row),
                bool(row.get("valid")),
                str(row.get("error") or ""),
            ),
            score=_score(row),
            num_visits=max(0, int(row.get("visits") or 0)),
            promise=_promise(row),
        ))

    if missing:
        raise RestoreError(
            f"{len(missing)} of {len(ordered)} candidate sources are missing from this "
            f"candidate store (nodes {missing[:8]}{'…' if len(missing) > 8 else ''}), so the "
            "search cannot be resumed from them; starting again is the only way forward"
        )

    tree.nodes.extend(nodes)
    # Iterations are what the model call is labelled with and what the tree
    # counts against `candidate_limit`. Continuing from the highest one seen
    # keeps a resumed run's numbering monotone in the event log.
    tree._next_iteration = max((node.program.iteration for node in nodes), default=0) + 1
    log.info("restored %d node(s), resuming at iteration %d", len(nodes), tree._next_iteration)
    return tree


def _body(
    row: Mapping[str, Any], *, store: CandidateStore, search_id: str, fallback: str,
) -> Optional[str]:
    code_hash = str(row.get("code_hash") or "")
    if code_hash:
        found = store.get(search_id, code_hash)
        if found is not None:
            return found
    return fallback or None


def _parent(row: Mapping[str, Any], index: int) -> Optional[int]:
    if index == 0:
        return None
    raw = row.get("parent_index")
    # Upstream's own fallback in `add_node`: a parent that is not in the tree
    # becomes the root rather than an exception, because a node with a real
    # score still belongs in the ranking.
    return int(raw) if isinstance(raw, int) and 0 <= raw < index else 0


def _score(row: Mapping[str, Any]) -> float:
    raw = row.get("score")
    if not isinstance(raw, (int, float)):
        # `-inf` is the tree's own sentinel for a candidate that did not run, and
        # `null` on the wire is how the event log writes it -- JSON has no
        # infinity. Reading it as 0.0 would rank a crash above a bad program.
        return float("-inf")
    return float(raw)


def _promise(row: Mapping[str, Any]) -> Optional[float]:
    raw = row.get("promise")
    if not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    # `_priors` filters on `> 0`, so anything at or below it is the same as
    # never having been rated -- and saying so keeps `Node.promise` honest.
    return value if value > 0 else None


def _metrics(row: Mapping[str, Any]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {
        key: float(value) for key, value in (row.get("metrics") or {}).items()
        if isinstance(value, (int, float))
    }
    metrics["score"] = _score(row)
    return metrics


def restore_baseline(
    baseline: Dict[str, float],
    values: Optional[Mapping[str, Any]],
    required: bool = False,
) -> Tuple[bool, str]:
    """Put back what the criteria are normalised against.

    Returns ``(restored, why_not)``. Every ``relative_to_baseline`` criterion
    divides by this, so a resume that rebuilt it by re-measuring the root would
    move the reference -- and with it every score in the run, including the ones
    already written down.

    **Only a scorecard that divides by it needs it.** An evaluator handing back a
    ``[0, 1]`` score normalises by ``identity``, which has nothing to be relative
    *to*; requiring a reference such a run never used refused every resume there
    was. So ``required`` is the caller's reading of the scorecard, and an empty
    reference is an answer rather than a fault when it is false.
    """
    rows = {
        key: float(value) for key, value in (values or {}).items()
        if isinstance(value, (int, float)) and key != "score"
    }
    if not rows:
        if not required:
            return True, ""
        return False, (
            "this run's seed event does not carry the root's measurements, so there is "
            "nothing to normalise the criteria against; runs started before resume "
            "existed cannot be continued"
        )
    baseline.update(rows)
    return True, ""
