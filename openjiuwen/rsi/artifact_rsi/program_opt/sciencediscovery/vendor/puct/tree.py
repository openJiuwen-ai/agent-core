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

"""`futs.Node` and `futs.search`'s node list, made safe for N workers.

Lifted from `examples/era/era_empirical_software.py`; see `__init__.py` for the
upstream commit. Four things here are pinned by upstream's own unit tests and
must not be "improved":

1. ``c_puct = 1.0``, the rank normalisation including the single-node 0.5 case,
   and the uniform prior ``P = 1/N``.
2. **A node is appended for every expansion, including a failed one.** Upstream
   scores a failed candidate ``-inf`` and appends it anyway; dropping it would
   change the rank denominator and the prior on every later iteration.
3. Selection is ``argmax(puct)`` over **every** node — there is no descent from
   the root, which is what "flat" names. Exploitation enters through the *rank*,
   so the exploration constant means the same thing whatever the metric's units
   are, and one candidate scoring ``-inf`` cannot swamp the term.
4. The visit is reserved at **selection**, not after execution. With one
   proposal in flight nothing can observe the tree between those two points, so
   it is the same algorithm; with N in flight it is the standard parallel
   virtual loss, and without it ``argmax(puct)`` is deterministic and every
   worker in a batch is handed the same parent.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agentdescent.selection import Candidate, FlatPuct, SelectionContext

from .program import Program

#: The artifact id the selection rows carry. Only ever compared with itself.
ARTIFACT_ID = "puct"

def finite(value: Any) -> Optional[float]:
    """``-inf`` is upstream's failure sentinel and is not valid strict JSON.

    ``json.dump`` writes it as the bare token ``-Infinity``, which most parsers
    reject — so a result file carrying one is readable by exactly the tool that
    wrote it. Failure is already recorded as ``valid: false``; the score field
    carries ``None`` instead.
    """
    import math

    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None

@dataclass
class Node:
    """`futs.Node`, with the program payload this port carries alongside it."""

    index: int
    parent_index: Optional[int]
    program: Program
    score: float
    num_visits: int = 0
    #: The model's own rating of how far this direction could go *after tuning*
    #: -- ``P(s, a)`` for `FlatPuct`, and ``None`` when it was not asked or did
    #: not answer. Deliberately not the score: a first-draft solver that is slow
    #: today can be the right idea, and the tree has no other way to tell that
    #: apart from a rewrap of the parent that is fine today and finished.
    promise: Optional[float] = None

    def summary(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "parent_index": self.parent_index,
            "program_id": self.program.program_id,
            "iteration": self.program.iteration,
            "change_summary": self.program.change_summary,
            "score": finite(self.score),
            "rmse": self.program.metrics.get("rmse"),
            "num_visits": self.num_visits,
            "promise": self.promise,
            "valid": self.program.valid,
            "error": self.program.error,
            "code_chars": len(self.program.code),
        }


@dataclass
class PuctTree:
    """`futs.search`'s node list, made safe for N workers to expand at once.

    The only behavioural difference from upstream is *when* a visit is counted:
    upstream backpropagates after `execute_fn` returns, this reserves at
    selection. With one proposal in flight nothing else can observe the tree
    between those two points, so the visit counts every selection sees are
    identical -- `tests/test_puct_example.py` pins that against a transcription
    of upstream's loop.
    """

    c_puct: float = 1.0
    #: Weight on :attr:`Node.promise` in the PUCT prior. ``0.0`` is upstream --
    #: a uniform ``1/N`` for every node -- and is the default, so the port is
    #: faithful unless asked otherwise.
    prior_exponent: float = 0.0
    candidate_limit: Optional[int] = None
    nodes: List[Node] = field(default_factory=list)
    _next_iteration: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _policy: FlatPuct = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._policy = FlatPuct(self.c_puct, self.prior_exponent)

    def seed(self, program: Program, score: float) -> Node:
        with self._lock:
            if self.nodes:
                return self.nodes[0]
            root = Node(0, None, program, score)
            self.nodes.append(root)
            return root

    def _backpropagate_locked(self, node: Node) -> None:
        """`futs.backpropagate_visit` -- the node, then every ancestor."""
        node.num_visits += 1
        if node.parent_index is not None:
            self._backpropagate_locked(self.nodes[node.parent_index])

    def select_parent(self) -> Optional[Tuple[int, Node]]:
        with self._lock:
            if not self.nodes:
                raise RuntimeError("the PUCT tree has not been seeded")
            iteration = self._next_iteration
            if self.candidate_limit is not None and iteration > self.candidate_limit:
                return None
            self._next_iteration += 1
            rows = tuple(
                Candidate(
                    artifact_id=ARTIFACT_ID,
                    version=node.index,
                    score=node.score,
                    selected=node.num_visits,
                    parent=node.parent_index,
                    prior=node.promise,
                )
                for node in self.nodes
            )
            ctx = SelectionContext(head=rows[0], candidates=rows, n_workers=1)
            chosen = self.nodes[self._policy.select(ctx, 1)[0].version]
            self._backpropagate_locked(chosen)
            return iteration, chosen

    def add_node(self, program: Program, score: float, parent_index: Optional[int],
                 promise: Optional[float] = None) -> Node:
        """Append an expansion. A failed program is a node too, scoring -inf."""
        with self._lock:
            if parent_index is None or not 0 <= parent_index < len(self.nodes):
                parent_index = 0
            node = Node(len(self.nodes), parent_index, program, score, num_visits=1,
                        promise=promise)
            self.nodes.append(node)
            return node

    def best(self) -> Node:
        with self._lock:
            if not self.nodes:
                raise RuntimeError("the PUCT tree is empty")
            return max(self.nodes, key=lambda node: node.score)

    def root(self) -> Node:
        with self._lock:
            return self.nodes[0]

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            valid = [node for node in self.nodes if node.program.valid]
            depths = []
            for node in self.nodes:
                depth, cursor = 0, node
                while cursor.parent_index is not None:
                    cursor = self.nodes[cursor.parent_index]
                    depth += 1
                depths.append(depth)
            return {
                "nodes": len(self.nodes),
                "valid_nodes": len(valid),
                "max_depth": max(depths) if depths else 0,
                "root_visits": self.nodes[0].num_visits if self.nodes else 0,
                "c_puct": self.c_puct,
                "prior_exponent": self.prior_exponent,
                "tree": [node.summary() for node in self.nodes],
            }


