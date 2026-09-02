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

"""`futs.search`'s loop body as a Strategy plus an Aggregator.

Vendored from `examples/era/era_empirical_software.py` (see `__init__.py` for
the upstream commit). This is the part of the port that answers "how does the
tree search get workers, staleness and a barrier-free runtime" — and upstream's
answer is that it does not implement any of them: the loop body becomes an
`AggregatorProtocol`, `aggregator_factory` plugs it in, and `evolve` /
`async_evolve` supply the rest.

**Why this is vendored rather than re-implemented.** The two halves have to
agree about things that are not obvious from either one alone: that the parent
is chosen inside `propose` so a visit is reserved on the worker thread that will
use it; that a failed candidate still becomes a node so the rank denominator is
unchanged; that a card arriving late is still a legitimate expansion of the
parent it was drawn from and so has nothing to be stale against. Re-deriving
those is how a port stops being the algorithm it claims to be.

**What changed on the way in**, and nothing else:

* An ``on_event`` hook. Upstream writes a JSON result file at the end; this has
  to stream the search as it happens, because a `SubTask` binds a graph that a
  user watches being drawn. The hook fires where the facts already are — the
  parent in `propose`, the node and the decision in the aggregator — and it is
  the only reason any of this is not a copy.
* The task-specific half is gone. Upstream's `Splits`, `evaluate_source` and
  Kaggle prompt are replaced by the :class:`Domain`, which upstream already cut
  as the seam for exactly this.
* `ARTIFACT_ID` is per search, because one process runs many.
"""

from __future__ import annotations

import ast
import json
import math
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from agentdescent.aggregator import AggregatorConfig, MergeOutcome, MergeReport
from agentdescent.evolution import Task
from agentdescent.evolvable import Diff, EvidenceCard, vv_staleness
from agentdescent.ledger import CASConflict, Ledger
from agentdescent.staleness import StaleAction, get_policy

from .domain import Domain
from .program import Program, entry_source, program_id
from .tree import Node, PuctTree

#: Merge outcomes upstream names for this port. `MergeOutcome` covers the
#: framework's own; these two are the tree's.
TREE_UPDATED = "tree-updated"
NO_VALID_CANDIDATES = "no-valid-candidates"

#: What the engine calls when something happens. Ours; upstream has no such
#: thing and reports once, at the end.
OnEvent = Callable[[str, Dict[str, Any]], None]


def _noop(_kind: str, _payload: Dict[str, Any]) -> None:
    return None


class PuctStrategy:
    """One executable program, and the parser that turns a reply into a Diff."""

    def __init__(self, domain: Domain) -> None:
        self.domain = domain

    def initial(self) -> Dict[str, str]:
        code = self.domain.initial_program
        return {
            "code": code,
            "program_id": program_id(code),
            "change_summary": self.domain.initial_summary,
        }

    def render(self, state: Dict[str, str]) -> str:
        return state.get("code", self.domain.initial_program)

    def keys(self) -> Sequence[str]:
        # `promise` is the model's own rating of the direction, carried so the
        # aggregator can hand it to `FlatPuct` as `P(s, a)`. Metadata about the
        # proposal rather than part of the program, and empty unless the run
        # asked for a prior.
        return ("code", "program_id", "change_summary", "parent_id", "parent_index",
                "promise")

    def to_diff(
        self,
        state: Dict[str, str],
        proposal: str,
        author: str,
        base_version: int,
        target: str,
    ) -> Optional[Diff]:
        try:
            payload = json.loads(proposal)
            code = str(payload["code"]).strip()
            iteration = int(payload["iteration"])
            parent_index = int(payload["parent_index"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        # An empty reply still becomes a diff, and so still becomes a node that
        # scores `-inf`. Upstream's `futs.search` appends the node regardless --
        # dropping it here would shrink the rank denominator and raise the `1/N`
        # prior for every later iteration, which is a different search. It can
        # never reach the ledger: `-inf` is never the best node.
        pid = program_id(code)
        return Diff(
            diff_id=f"{author}:{pid}:{iteration}:{base_version}",
            target=target,
            ops={
                "code": code,
                "program_id": pid,
                "change_summary": str(payload.get("change_summary") or ""),
                "parent_id": str(payload.get("parent_id") or ""),
                "parent_index": str(parent_index),
                "iteration": str(iteration),
                "error": str(payload.get("error") or ""),
                "promise": str(payload.get("promise") or ""),
            },
            author=author,
        )


def _first_doc_line(code: str) -> str:
    """The module docstring's first line, or empty — what a copied label equals."""
    try:
        doc = ast.get_docstring(ast.parse(code))
    except SyntaxError:
        return ""
    return (doc or "").strip().splitlines()[0][:200] if doc else ""


def _read_promise_op(raw: Any) -> Optional[float]:
    """The rating as it survives the ops dict, which is strings all the way.

    Absent, empty and unparsable all mean the same thing — nobody rated this
    node — and `FlatPuct._priors` gives such a node the mean of the rated ones
    rather than zero.
    """
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


#: How many times a dead candidate may be debugged before the search moves on.
#:
#: Two, not one: a repair that reads only the original traceback is guessing,
#: and over two live runs eight one-shot repairs landed two. Not many more
#: either — every attempt is a model call the user pays for, and a candidate
#: that resists two fixes is usually a design that wants replacing, which the
#: next expansion does anyway.
_REPAIR_ATTEMPTS = 2

#: What a nothing-came-back candidate scores, wherever it is noticed.
NO_CANDIDATE = "no candidate program came back; the model call returned nothing"


def _reward(domain: Domain, valid: bool, metrics: Dict[str, Any]) -> float:
    """A candidate's reward, or the floor when it did not produce one."""
    if not valid:
        return 0.0
    try:
        return float(domain.reward(dict(metrics)))
    except Exception:
        return 0.0


#: How much of a failure reason the repair log line carries.
_WHY_CHARS = 300


def make_propose(
    tree: PuctTree,
    complete: Callable[[str, int, str], Tuple[str, str, Optional[float]]],
    domain: Domain,
    *,
    on_event: OnEvent = _noop,
    check: Optional[Callable[[str], Tuple[bool, Dict[str, Any], str]]] = None,
    repair_prompt: Optional[Callable[[str, str], str]] = None,
    repair_attempts: int = _REPAIR_ATTEMPTS,
) -> Callable[[str, Task, str, float], Optional[str]]:
    """Select a parent and ask the model to rewrite it.

    Runs on a worker thread. The selection happens **here**, not in the
    aggregator, because `PuctTree.select_parent` reserves the visit as it picks —
    upstream's parallel virtual loss — and the thread that is about to spend
    minutes on this parent is the one that should have reserved it.

    ``complete`` is ``(prompt, iteration, parent_code) -> (code, change_summary,
    promise)``. The parent's own program goes with the prompt because a reply
    names only the files it changed, so the engine needs the tree those edits
    land on in order to build the whole candidate.
    the engine owns the model call so that a stop, a token count and an empty
    reply all mean something to it. ``promise`` is the model's own rating of the
    direction, read out of the same reply — `P(s, a)` for `FlatPuct`, and
    ``None`` when the run did not ask for one or the model did not answer.

    **The fix-it loop is here, on the worker, and it is off unless a caller
    supplies ``check``.** Upstream appends a candidate that fails as a node
    scoring `-inf` and moves on; a task asks for the loop when that is too
    wasteful. Why a task would: a `-inf` node is a permanent dead end — its
    rank_score is 0, `FlatPuct` never selects it again, and the direction dies
    with it, even when the failure was a missing cast or a step size slightly
    too large.

    Two things about where it runs, and both are the reason it moved here from
    the aggregator:

    * **``check`` reads a rollout shard, never the held-out ones.** A loop that
      measured on the gate would be choosing between attempts on the split that
      decides the ranking, and a repaired candidate's gate score would be a
      maximum over three measurements where an unrepaired one's is a single
      measurement. That is a thumb on the scale of the search's own ordering.
    * **On a worker, in parallel.** The merger is one thread by the protocol's
      contract, and a repair costs a model call plus an evaluation. Spent there,
      nothing merges while it runs — and proposals waiting in the buffer go
      stale and are discarded, which is a second model call thrown away for
      every one this saved.

    The last draw is the one that goes forward, not the best of them: the loop
    buys attempts, it does not hide a failure. If they run out, the candidate
    becomes the `-inf` node upstream would have appended on the first one.
    """

    def propose(rendered: str, task: Task, output: str, reward: float) -> Optional[str]:
        selection = tree.select_parent()
        if selection is None:
            return None
        iteration, parent = selection
        on_event("selected", {
            "ancestors": _ancestor_visits(tree, parent),
            "iteration": iteration,
            "parent_index": parent.index,
        })
        prompt = domain.prompt(parent.program)
        code, summary, promise = "", "", None
        attempts = max(1, repair_attempts) if check and repair_prompt else 1
        for attempt in range(attempts):
            code, summary, drawn = complete(prompt, iteration, parent.program.code)
            # Kept from the first reply that carried one: a redraw is the same
            # direction being written again, so its rating is a second reading
            # of one direction rather than a reading of a second.
            promise = drawn if promise is None else promise
            last = attempt == attempts - 1
            if last or not code.strip():
                break
            valid, metrics, error = check(code)
            if not _is_dead(domain, valid, metrics):
                break
            # Only that an attempt was made, never whether it landed: the final
            # draw is not checked -- there is no retry left to spend on the
            # answer, and the merger measures it moments later anyway. Whether
            # the redraw worked is the node's own score, which is the only
            # measurement that was actually taken on the deciding split.
            on_event("repaired", {"attempt": attempt + 1, "why": _tail(error, _WHY_CHARS)})
            prompt = repair_prompt(code, error)
        # A summary copied verbatim from the parent is not a summary: the
        # docstring first line doubles as the node's label, and on one live
        # compression run every candidate kept the seed's spec-style header,
        # so sixteen nodes all read "Lossless text compression: …". An empty
        # label is honest about carrying no information; sixteen identical
        # ones actively claim the candidates are the same thing.
        parent_summary = (parent.program.change_summary or "").strip()
        parent_doc = _first_doc_line(entry_source(parent.program.code))
        if summary.strip() and summary.strip() in (parent_summary, parent_doc):
            summary = ""
        return json.dumps(
            {
                "change_summary": summary,
                "code": code,
                "iteration": iteration,
                "parent_id": parent.program.program_id,
                "parent_index": parent.index,
                "promise": "" if promise is None else repr(promise),
            },
            separators=(",", ":"),
        )

    return propose



def _tail(text: str, limit: int) -> str:
    """The failure in one line: the exception, without the stack that reached it.

    A traceback's useful half is its last line — the exception and what it says.
    Everything before it is the call stack, which is longer and which the reader
    can already guess. Two wrong cuts before this one, both on live runs: a
    plain head slice ended mid-path three frames in, one token before the
    sentence that said what had gone wrong; then a 300-character tail kept the
    whole traceback, and six of them turned the run panel into a wall of stack
    frames.

    The frames are not lost. The repair prompt gets the error untrimmed, which
    is the reader that needs a file and a line, and the node carries it too.
    This is for the log stream, where one line per event is the whole format.
    """
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    text = lines[-1].strip() if lines else ""
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


def _is_dead(domain: Domain, valid: bool, metrics: Dict[str, Any]) -> bool:
    """Whether a candidate did not work at all, as opposed to working badly.

    Not `valid` on its own. An evaluator that catches its own exceptions -- the
    shape every mode here asks for -- reports a *successful measurement of a
    broken candidate*: `{"valid": true, "metrics": {"score": 0.0}}`, because
    from its side the measurement did succeed. So `valid` means the evaluation
    ran, never that the candidate runs, and on a live compression run seven
    candidates that crashed on every shard all arrived here as valid.

    The floor of the reward is what separates the two. Every domain hands back
    a reward already oriented so that larger is better, so nothing at or below
    zero has anything left to lose.
    """
    if not valid:
        return True
    try:
        return float(domain.reward(dict(metrics))) <= 0.0
    except Exception:
        return False


def _evaluate(
    domain: Domain, code: str, shards: Sequence[int],
) -> Tuple[bool, Dict[str, Any], str]:
    """`domain.evaluate`, except that an empty candidate never reaches it.

    Diverges from upstream, in one place used by both the rollout and the
    aggregator. A model call that returned nothing produces no program, and the
    drafted evaluator scores that as an ordinary zero — it imports a module
    with nothing in it and counts every case wrong, coming back
    `valid=True, score=0`: a node asserting the
    search tried this direction and it was worthless. It tried nothing, and the
    tree ranks against that assertion for the rest of the run. Only the measured
    mode gets this right, and only because its AST gate happens to refuse an
    empty source.
    """
    if not code.strip():
        return False, {"score": float("-inf")}, NO_CANDIDATE
    return domain.evaluate(code, shards)


def make_run(domain: Domain) -> Callable[[str, Task], str]:
    """One shard of the split is one rollout."""

    def run(rendered: str, task: Task) -> str:
        valid, metrics, error = _evaluate(domain, rendered, (int(task.meta["shard"]),))
        return json.dumps(
            {"error": error, "metrics": metrics, "valid": valid},
            separators=(",", ":"), default=str,
        )

    return run


def make_reward(domain: Domain) -> Callable[[Task, str], float]:
    """The engine's `reward`, reading the payload `make_run` writes."""

    def reward(task: Task, output: str) -> float:
        try:
            payload = json.loads(output)
            if not payload.get("valid"):
                return 0.0
            return domain.reward(payload["metrics"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return 0.0

    return reward


class PuctTreeAggregator:
    """FUTS's expand-execute-append step as an AgentDescent merge optimizer.

    ``ingest`` is called from many worker threads and ``step`` from one, which
    is the protocol's own threading contract; the buffer is guarded and nothing
    else is shared.
    """


    def __init__(
        self,
        ledger: Ledger,
        verifier: Any,
        tree: PuctTree,
        config: AggregatorConfig,
        staleness_policy: Any,
        *,
        domain: Domain,
        artifact_id: str,
        on_event: OnEvent = _noop,
    ) -> None:
        self.ledger = ledger
        self.verifier = verifier
        self.tree = tree
        self.config = config
        self.staleness_policy = staleness_policy or get_policy("guarded")
        self.domain = domain
        self.artifact_id = artifact_id
        self.on_event = on_event
        self.cards: List[EvidenceCard] = []
        self._cards_lock = threading.Lock()
        self._seeded = False
        self.meter = None

    # -- what a node is scored on ---------------------------------------------

    def _held_out_shards(self) -> Tuple[int, ...]:
        shards = tuple(sorted(int(task.meta["shard"]) for task in self.verifier.held_out))
        if not shards:
            raise RuntimeError("the PUCT search needs held-out shards to score a node")
        return shards

    def seed(self) -> None:
        if self._seeded:
            return
        self._seeded = True
        head = self.ledger.snapshot(Ledger.DEV).get(self.artifact_id)
        code = head.state.get("code", self.domain.initial_program)
        valid, metrics, error = _evaluate(self.domain, code, self._held_out_shards())
        if not valid:
            # Upstream prints the initial score and carries on even if it is
            # -inf. Refusing instead: a root that cannot run means the sandbox
            # or the data is broken, and every child would inherit it.
            raise RuntimeError(f"the initial program failed to run: {error}")
        root = self.tree.seed(
            Program(program_id(code), 0, None, code, self.domain.initial_summary,
                    metrics, valid, error),
            float(metrics["score"]),
        )
        self.on_event("seeded", {"metrics": metrics, "node": root})

    def ingest(self, card: EvidenceCard) -> None:
        with self._cards_lock:
            self.cards.append(card)

    def _staleness_filter(
        self, head_version: Dict[str, int], cards: Sequence[EvidenceCard],
    ) -> Tuple[List[EvidenceCard], List[EvidenceCard]]:
        survivors: List[EvidenceCard] = []
        discarded: List[EvidenceCard] = []
        for card in cards:
            eta = vv_staleness(head_version, card.base_version)
            alpha = 0 if card.diff.contract_breaking else self.config.alpha_tail
            action = self.staleness_policy.decide(eta, alpha, card.diff.contract_breaking)
            if action is StaleAction.DISCARD:
                discarded.append(card)
            else:
                # REBASE is safe: a survivor is fully re-executed against the
                # held-out shards before it can become a node, and its place in
                # the tree is its parent index, which the head cannot move.
                survivors.append(card if eta == 0 else card.rebased_onto(head_version))
        if self.meter is not None:
            self.meter.add("stale_considered", len(cards))
            self.meter.add("stale_discarded", len(discarded))
        return survivors, discarded

    # -- the merge -------------------------------------------------------------

    def step(self) -> List[MergeReport]:
        self.seed()
        with self._cards_lock:
            cards, self.cards = self.cards, []
        if not cards:
            return []

        snapshot = self.ledger.snapshot(Ledger.DEV)
        head = snapshot.get(self.artifact_id)
        base_vv = {self.artifact_id: snapshot.version.get(self.artifact_id, 0)}
        survivors, discarded = self._staleness_filter(base_vv, cards)
        for card in discarded:
            self.on_event("discarded", {"ops": dict(card.diff.ops)})

        before = self.tree.best()
        valid_candidates = 0
        for card in survivors:
            ops = card.diff.ops
            code = ops.get("code", "")
            valid, metrics, error = _evaluate(self.domain, code, self._held_out_shards())
            program = Program(
                program_id(code),
                int(ops.get("iteration", "0")),
                ops.get("parent_id") or None,
                code,
                ops.get("change_summary", ""),
                metrics,
                valid,
                error or ops.get("error", ""),
            )
            # `float(metrics["score"])` is -inf on failure, which is upstream's
            # own sentinel -- the node is appended either way.
            node = self.tree.add_node(program, float(metrics["score"]),
                                      int(ops.get("parent_index", "0")),
                                      promise=_read_promise_op(ops.get("promise")))
            valid_candidates += int(valid)
            self.on_event("node", {"metrics": metrics, "node": node, "ops": dict(ops)})

        best = self.tree.best()
        accepted: Optional[Diff] = None
        committed_version: Optional[int] = None
        category = TREE_UPDATED
        if not survivors:
            category = MergeOutcome.ALL_STALE.value
        elif not valid_candidates:
            category = NO_VALID_CANDIDATES
        if best.program.code != head.state.get("code"):
            accepted = Diff(
                diff_id=f"tree-best:{best.program.program_id}:{head.version}",
                target=self.artifact_id,
                ops={
                    "code": best.program.code,
                    "program_id": best.program.program_id,
                    "change_summary": best.program.change_summary,
                    "parent_id": best.program.parent_id or "",
                    "parent_index": str(best.parent_index or 0),
                },
                author="era-tree",
            )
            try:
                _, committed_version = self.ledger.commit(
                    head.apply(accepted), base_vv, branch=Ledger.DEV,
                    message="era: commit best node in the FUTS tree",
                )
                category = MergeOutcome.COMMITTED.value
            except CASConflict:
                accepted = None
                category = MergeOutcome.CAS_CONFLICT.value

        # Which node became the best, if one did. Under PUCT that *is* what
        # acceptance means: there is no per-candidate statistical gate, the tree's
        # rank ordering is the selection pressure, and the ledger publishes the
        # best node. Reported here rather than per node above because it is only
        # knowable once the whole sweep has landed.
        self.on_event("swept", {
            "best": best,
            "category": category,
            "changed": best.index != before.index,
            "committed": committed_version,
        })

        return [
            MergeReport(
                self.artifact_id, accepted, False, len(cards), len(survivors),
                len(discarded), 0, self.domain.reward(best.program.metrics),
                committed_version,
                f"valid={valid_candidates}/{len(survivors)} nodes={len(self.tree.nodes)}",
                category,
            )
        ]


def _ancestor_visits(tree: PuctTree, node: Node) -> List[Dict[str, int]]:
    """The selected node's visit count and every ancestor's, absolute.

    Read here rather than by the caller: `select_parent` backpropagates under
    the tree's own lock, and the counts on the wire have to be the ones the
    selection saw.
    """
    out: List[Dict[str, int]] = []
    cursor: Optional[Node] = node
    while cursor is not None:
        out.append({"nodeIndex": cursor.index, "visits": cursor.num_visits})
        cursor = None if cursor.parent_index is None else tree.nodes[cursor.parent_index]
    return out
