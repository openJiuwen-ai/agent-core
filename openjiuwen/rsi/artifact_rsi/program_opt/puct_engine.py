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

"""The PUCT tree search, wired the way upstream's own ERA port wires it.

`futs.search`'s loop body is a Strategy plus an Aggregator (`vendor/puct/search.py`);
AgentDescent supplies the workers, the ledger, the evidence cards, the staleness
handling and the barrier-free runtime. This module is the wiring plus the parts
that are ours: the scorecard as a :class:`Domain`, the model call, and turning
the search into the event stream the rest of the system reads.

**Why not a loop of our own.** An earlier revision drove `PuctTree` directly and
hand-rolled the wave scheduling, on the reasoning that `evolve()` reports only
round aggregates and could not describe a tree. That was wrong in its premise:
`aggregator_factory` is a documented parameter of both `evolve` and
`async_evolve`, `AggregatorProtocol` is the seam it plugs into, and upstream's
the upstream port puts the whole tree there. Everything the hand-rolled version had to
build — worker pool, virtual loss, wave dispatch, the "results as they finish"
consumer — is machinery the engine already owns and has tested.

**Three modes over one set of plug-ins.** `serial` and `sync` go through
`evolve` (one worker, or N with a round barrier); `async` goes through
`async_evolve` (no barrier, staleness policy live). Upstream uses that to show
the parallel runs are the same search; here it also means a real engine can be
run single-threaded when a reproduction needs one, without the stub.
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import events
from .candidates import TREE_FILE, TREE_SCHEMA_VERSION, CandidateStore, write_tree_snapshot
from .completion import CompletionUnavailable, CompletionUsage
from .engine import RunSpec
from .events import Emit
from .logging_config import get_logger
from .program import (
    bundle,
    extract_files,
    files_of,
    read_promise,
    reply_carries_program,
)
from .prompt import repair_prompt, with_promise_request
from .provision import missing_candidate_runtime
from .restore import RestoreError, restore_baseline, restore_tree
from .scorecard import KNOWN_NORMALIZE, SCORE_KEY
from .search import (
    PuctStrategy,
    PuctTreeAggregator,
    make_propose,
    make_reward,
    make_run,
)
from .tree import Node, PuctTree

log = get_logger("puct")

#: How long the run may sit past its expansion budget before the engine is told
#: to wind down. The budget is `max_iters`; this is the safety net for a backend
#: that never returns.
_MAX_SECONDS = 24 * 3600.0

#: How long `async_evolve` waits for in-flight workers once it is done.
_SHUTDOWN_GRACE = 120.0


def _script_domain(**kwargs: Any) -> Any:
    """`script_domain`, imported at call time.

    Deferred because building a `PuctEngine` must not pull in the sandbox
    plumbing: `server.py` constructs one at import to fill its engine table, and
    an import error there takes down the whole sidecar rather than one run.
    """
    from .script_domain import script_domain

    return script_domain(**kwargs)


def _remaining(spec: RunSpec) -> int:
    """Expansions this attempt may still make.

    A resumed run's node rows include the root, which was never an expansion, so
    the count subtracts it. Never below one: an attempt that may make no
    expansion at all would start, report nothing and settle as a success, which
    reads as "the search found no improvement" rather than "there was no budget
    left to look".
    """
    done = max(0, len(spec.resume_nodes) - 1)
    return max(1, int(spec.expansions) - done)


def _rollout_budget(expansions: int) -> int:
    """`max_iters` in the unit upstream actually counts: worker rollouts.

    Not expansions, and passing one for the other is what made a run of 20 stop
    at 5. Upstream increments its counter once per clean rollout, but only calls
    `propose` when that rollout scored **below** `solved_threshold` — a task it
    already solves needs no proposal. So every solved rollout spends a slot of
    the budget and produces no candidate, and upstream's own wrapper says as
    much by computing `max_iters = rounds * n_workers`.

    Measured on two live runs: a linkage search whose shards each held a single
    record scored 0 or 1 with nothing between, 11 of 16 shards came out at 1.0,
    and 20 rollouts yielded 5 expansions; a compression search yielded 8.

    Five times, then. A solved rollout costs one sandbox evaluation and **no
    model call**, so headroom here is paid in seconds rather than tokens, and
    the real cap on expansions is the tree's own `candidate_limit`, which
    refuses to select a parent past it.
    """
    return max(1, int(expansions)) * 5


class _Refusal(RuntimeError):
    """A run-level fault: the search cannot start, or cannot mean anything."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _Usage:
    """Token spend, on top of the framework's own counter.

    `agentdescent.agents.Usage` already does the hard half — thread-safe totals
    of calls, tokens and seconds, which is exactly what the cost event carries —
    so it is used rather than reimplemented.

    What it does not have is **which expansion** a call belonged to, and this
    engine needs that: with three calls in flight there is no such thing as "the
    last call", and blaming expansion 3's empty reply on expansion 5's token
    count is worse than not explaining it at all.
    """

    def __init__(self, already_spent: int = 0) -> None:
        from agentdescent.agents import Usage

        self.totals = Usage()
        # A resumed run continues one search, and the `cost` event is absolute
        # by contract. Starting this process's counter from what the previous
        # attempt spent is what keeps that true across a restart.
        self._already_spent = max(0, already_spent)
        self._per_expansion: Dict[int, CompletionUsage] = {}
        self._lock = threading.Lock()

    def add(self, iteration: int, usage: CompletionUsage) -> None:
        self.totals.record(completion_tokens=usage.completion,
                           prompt_tokens=max(0, usage.total - usage.completion))
        with self._lock:
            self._per_expansion[iteration] = usage

    def read(self) -> int:
        return self._already_spent + self.totals.total_tokens

    def of(self, iteration: int) -> Optional[CompletionUsage]:
        with self._lock:
            return self._per_expansion.get(iteration)


class PuctEngine:
    """One search: seed, expand N times, report the winner."""

    name = "puct"
    #: Candidates are model-written Python that gets executed. A run without a
    #: backend is refused at the seam in `server.py`, not here.
    requires_sandbox = True

    def __init__(
        self,
        *,
        completion_factory: Callable[..., Callable[[str], str]],
        domain_factory: Optional[Callable[..., Any]] = None,
        store_root: Optional[Path] = None,
    ) -> None:
        # Required, with no endpoint fallback. The contract routes every model
        # call through the request's injected `Model`; a default that built its
        # own HTTP client from spec fields was the one path that could bypass
        # it, and a bypass that exists is a bypass that eventually gets used.
        if completion_factory is None:
            raise ValueError(
                "PuctEngine needs a completion_factory: every model call goes "
                "through the request's injected Model"
            )
        self._completion_factory = completion_factory
        # The seam a test substitutes a fake domain through. Defaulted to the
        # scripted one because that is the only domain that executes anything;
        self._domain_factory = domain_factory or _script_domain
        # Same data dir the rest of the stack uses: a candidate source that
        # landed in the user's home directory would be invisible to every tool
        # that knows where this deployment keeps its state.
        self._store_root = store_root or Path(
            os.environ.get("SCIENCE_AGENT_EVOLVE_CANDIDATE_DIR")
            or Path(os.environ.get("SCIENCE_AGENT_DATA_DIR") or "data") / "evolve-candidates"
        )

    def run(self, spec: RunSpec, emit: Emit, should_stop: Callable[[], bool]) -> None:
        try:
            _refuse_unrunnable(spec)
        except _Refusal as refusal:
            # Nothing measurable was set up, so every candidate would fail
            # identically. Reporting that as a search which found nothing would
            # send the user looking at their candidates for a fault that is in
            # the run's configuration.
            emit(events.log("error", refusal.message))
            emit(events.search_finished("failed", None, 0))
            log.warning("run %s refused: %s", spec.search_id, refusal.message)
            return

        emit(events.search_started(spec.algorithm, spec.scorecard_hash))
        if spec.workers > 1 and _mode(spec) == "serial":
            emit(events.log("warn", f"this search asked for {spec.workers} workers, but the mode is serial"))

        with tempfile.TemporaryDirectory(prefix=f"evolve-ledger-{spec.search_id}-") as repo:
            try:
                self._search(spec, Path(repo), emit, should_stop)
            except _Refusal as refusal:
                emit(events.log("error", refusal.message))
                emit(events.search_finished("failed", None, 0))
                log.warning("run %s failed: %s", spec.search_id, refusal.message)

    # -- the search ------------------------------------------------------------

    def _search(
        self,
        spec: RunSpec,
        repo: Path,
        emit: Emit,
        should_stop: Callable[[], bool],
    ) -> None:
        from agentdescent.async_evolve import async_evolve
        from agentdescent.evolution import evolve
        from agentdescent.staleness import get_policy

        baseline: Dict[str, float] = {}
        # One way to score: the drafting model wrote the evaluator, so anything
        # a task can be scored by fits without a staging pipeline of its own.
        # Other measurement kinds are refused by name in `_refuse_unrunnable`.
        from .script_domain import ScriptError

        try:
            domain = self._domain_factory(
                scorecard=spec.scorecard,
                script=spec.script,
                capability=spec.sandbox,
                statement=str(spec.statement or ""),
                baseline_code=spec.baseline_code,
                entrypoint=spec.entrypoint,
                candidate_timeout=spec.candidate_timeout_seconds,
                baseline=baseline,
                mutation_template=spec.mutation_template,
            )
        except ScriptError as error:
            raise _Refusal(str(error)) from error

        tree = PuctTree(c_puct=float(spec.options.get("c_puct", 1.0)),
                        prior_exponent=_prior_exponent(spec.options),
                        # The budget is what is *left*: a run that made 12 of 20
                        # expansions and was interrupted has 8 to spend, and a
                        # limit counted from zero would give it 20 more.
                        candidate_limit=_remaining(spec) + max(0, len(spec.resume_nodes) - 1))
        # Beside the run's own log when the control plane named a directory, so
        # the whole search is one portable thing; the deployment's data
        # directory otherwise, which is what the probe and the tests use.
        store = CandidateStore(
            Path(spec.run_dir) if spec.run_dir else self._store_root,
            flat=bool(spec.run_dir),
        )
        usage = _Usage(spec.resume_tokens)
        resumed = bool(spec.resume_nodes)
        if resumed:
            try:
                restore_tree(tree, spec.resume_nodes, store=store,
                             search_id=spec.search_id, fallback_code=spec.baseline_code)
            except RestoreError as error:
                raise _Refusal(str(error)) from error
            needs = any(
                (c.get("normalize") or {}).get("kind") == "relative_to_baseline"
                for c in (spec.scorecard.get("criteria") or [])
            )
            ok, why = restore_baseline(baseline, spec.resume_baseline, required=needs)
            if not ok:
                raise _Refusal(why)
        reporter = _Reporter(
            spec, tree, domain, store, usage, emit,
            tree_path=store.run_path(spec.search_id, TREE_FILE),
        )

        complete = self._model_call(spec, usage, reporter, should_stop, tree)
        tasks = _group_tasks(spec)
        # No colon: the engine refuses an artifact id that is not a safe
        # filename, because it becomes one inside the ledger's git repo.
        artifact_id = "puct-" + "".join(
            char if char.isalnum() or char in "_.-" else "-" for char in spec.search_id
        )

        def factory(ledger: Any, verifier: Any, audit: Any, config: Any, policy: Any) -> Any:
            aggregator = PuctTreeAggregator(
                ledger, verifier, tree, config, policy,
                domain=domain, artifact_id=artifact_id, on_event=reporter.on_event,
            )
            # Seeded here so the root's numbers are what everything after is
            # normalised against — the domain's baseline is empty until now. A
            # resumed run skips both: its root is already in the tree, and its
            # baseline came back from the event log rather than a re-measurement
            # that would move the reference every score is relative to.
            if not resumed:
                aggregator.seed()
                baseline.update({
                    key: float(value) for key, value in tree.root().program.metrics.items()
                    if isinstance(value, (int, float)) and key != SCORE_KEY
                })
            return aggregator

        common: Dict[str, Any] = {
            "aggregator_factory": factory,
            "artifact_id": artifact_id,
            # A whole-program rewrite touches every key there is.
            "blast_radius": 1.0,
            # Positional, and documented as such: the held-out set is the tail of
            # the task list, so ordering rollout shards before gate shards makes
            # the engine's held-out set exactly the scorecard's gate shards.
            "held_out_frac": _group_held_out_frac(spec),
            "n_workers": max(1, min(spec.workers, spec.expansions)),
            "propose": make_propose(
                tree, complete, domain, on_event=reporter.on_event,
                # The fix-it loop runs here, on the worker, and checks on a
                # rollout shard. Reading the held-out ones would let it choose
                # between attempts on the split that decides the ranking.
                check=_rollout_check(domain, _rollout_shards(spec)),
                # Bound to this run's entrypoint: the repair prompt lists the
                # program, and which file the evaluator imports decides both the
                # order it is listed in and whether one block or several are
                # asked for back.
                repair_prompt=lambda code, error: repair_prompt(
                    code, error, spec.entrypoint, template=spec.repair_template),
            ),
            "repo_path": str(repo),
            "run": make_run(domain),
            # A held-out evaluation here is a sandboxed process, not an API call,
            # so the useful concurrency is the worker count.
            "eval_concurrency": max(1, min(spec.workers, spec.expansions)),
            # Never "solved": upstream skips `propose` on a rollout whose task
            # already scores past this, which makes sense for a multi-task bench
            # and none at all for a single-artifact tree search — the shard is a
            # *measurement*, not a task to finish, and a proposal is equally
            # valuable whichever shard this rollout happened to draw. Measured
            # on a live compression run: the evaluator clamped any shard
            # compressed past 50% to a flat 1.0, four of six rollout shards
            # saturated, and two-thirds of the rollout budget burned on skips —
            # a run planned for 20 expansions made 12 and stopped on max_iters
            # in under two minutes. The scorecard's own solvedThreshold keeps
            # governing the control plane; it just no longer turns rollouts
            # into no-ops.
            "solved_threshold": 2.0,
            "self_verify": False,
            "strategy": PuctStrategy(domain),
            "usage": None,
        }

        mode = _mode(spec)
        reward = make_reward(domain)
        try:
            outcome = None
            if mode == "async":
                outcome = async_evolve(
                    tasks, reward,
                    async_ratio=int(spec.options.get("async_ratio", 1)),
                    max_iters=_rollout_budget(_remaining(spec)),
                    max_seconds=_MAX_SECONDS,
                    shutdown_grace=_SHUTDOWN_GRACE,
                    # A discarded card is a whole trained-and-scored program, and
                    # the tree is append-only: a node's place in it is its parent
                    # index, so a late arrival is still a legitimate expansion of
                    # the parent it was drawn from. There is nothing for it to be
                    # stale against.
                    staleness_policy=get_policy(str(spec.options.get("staleness", "full"))),
                    **common,
                )
            else:
                outcome = evolve(
                    tasks, reward,
                    rounds=max(1, _remaining(spec) // max(1, common["n_workers"])),
                    max_concurrency=1 if mode == "serial" else common["n_workers"],
                    max_seconds=_MAX_SECONDS,
                    **common,
                )
        except RuntimeError as error:
            # `PuctTreeAggregator.seed` refuses a root that will not run, and the
            # engine surfaces it here. Every score in the search is relative to
            # the root, so without it there is nothing for "better" to mean.
            raise _Refusal(str(error)) from error

        # Whatever the framework decided is the only account of why a run of 24
        # expansions stopped at 7. Discarding it — which this did — leaves the
        # status line saying "succeeded" for a search whose workers died on the
        # second call, and nothing anywhere that says otherwise.
        reporter.note_outcome(outcome, spec.expansions)
        reporter.finish("stopped" if should_stop() else "succeeded")

    def _model_call(
        self,
        spec: RunSpec,
        usage: _Usage,
        reporter: "_Reporter",
        should_stop: Callable[[], bool],
        tree: PuctTree,
    ) -> Callable[[str, int], Tuple[str, str, Optional[float]]]:
        """`(prompt, iteration) -> (code, change_summary, promise)`.

        Owned here rather than handed to the framework as an `Agent` so a stop, a
        token count and an empty reply each mean something to this engine.
        """
        try:
            # No client-level sink: every call supplies its own, because with N
            # expansions in flight a shared one cannot say which expansion a
            # token count belongs to.
            complete = self._completion_factory(spec, None, should_stop)
        except CompletionUnavailable as error:
            raise _Refusal(f"this search has no access to a model: {error}") from error

        # Asked for only when the prior will read it: the request costs a line
        # of prompt and a line of reply, and a run at the upstream default
        # ignores the number, so asking anyway would be paying for nothing.
        ask_promise = _prior_exponent(spec.options) > 0.0

        def call(prompt: str, iteration: int,
                 parent_code: str = "") -> Tuple[str, str, Optional[float]]:
            if should_stop():
                # Upstream's own way of saying "no more expansions": past the
                # candidate limit, `select_parent` returns None and the workers
                # wind down. Reusing it means a stop looks like a finished
                # budget rather than a special case.
                tree.candidate_limit = 0
                return "", "", None
            reply = complete(
                with_promise_request(prompt, spec.prior_template) if ask_promise else prompt,
                lambda spent: usage.add(iteration, spent),
                lambda reason: reporter.note_failure(iteration, reason),
            )
            # Merged onto the parent's tree: the reply carries only the
            # files it changed, so the candidate is the parent's program
            # with those files replaced.
            files, summary = extract_files(
                reply, files_of(parent_code, spec.entrypoint), spec.entrypoint,
            )
            # A reply that proposed nothing merges to the parent, which
            # would be a valid program costing a full evaluation to learn
            # the parent's own score. Empty is what it is, and what the rest
            # of the engine already knows how to record.
            code = bundle(files) if reply_carries_program(reply) else ""
            if not _has_content(code, spec):
                reporter.note_empty(iteration)
            # Read from the same reply, so the prior costs no extra call.
            return code, summary, (read_promise(reply) if ask_promise else None)

        return call


def _has_content(code: str, spec: RunSpec) -> bool:
    """Whether a draw produced anything at all.

    A serialised empty tree is a non-empty string, so `code.strip()` — which is
    what a one-file genome could be checked by — would call every empty reply a
    program and never report one.
    """
    return any(text.strip() for text in files_of(code, spec.entrypoint).values())


# --- Turning the search into the event stream --------------------------------


class _Reporter:
    """The search's events, in the order the rest of the system reads them.

    Single-threaded by contract for everything but `note_empty`: the aggregator's
    `step` is the only caller of the node and sweep hooks, and `selected` is
    emitted from a worker but carries only that worker's own facts.
    """

    #: Class-level so an instance built without `__init__` — which several test
    #: harnesses here do — reads zero rather than raising from inside `swept`.
    discarded: int = 0

    def __init__(
        self,
        spec: RunSpec,
        tree: PuctTree,
        domain: Any,
        store: CandidateStore,
        usage: _Usage,
        emit: Emit,
        tree_path: Optional[Path] = None,
    ) -> None:
        self.spec = spec
        self.tree = tree
        #: Where the tree snapshot lands, or ``None`` when nobody asked for one.
        self._tree_path = tree_path
        self.domain = domain
        self.store = store
        self.usage = usage
        self.emit = emit
        # Empty until the framework returns. `finish` runs on paths where it
        # never does — a stop, a crash — and an absent reason has to be
        # distinguishable from a reason the framework declined to give.
        self._stop_reason = ""
        self._planned = spec.expansions
        self.attempted = 0
        self.scored = 0
        #: Proposals the staleness filter threw away. Counted so the finish
        #: event can say where the gap between planned and made went.
        self.discarded = 0
        #: ``node index -> {code_hash, adopted, reason, category}``. The tree
        #: itself holds none of these: the hash belongs to the candidate store
        #: and the verdict is the merger's, so the snapshot is this side's
        #: projection rather than a second copy of the tree's own state.
        self._detail: Dict[int, Dict[str, Any]] = {}
        #: Every finite score seen, rounded — one member after a whole run means
        #: the scoring could not tell any candidate from the seed. Seeded from
        #: the tree so a resumed attempt judges the *search*, not its last leg:
        #: three same-scoring expansions after a restart are not evidence that
        #: the scorecard is flat when the twelve before them were not.
        self._distinct_scores: set[float] = {
            round(float(node.score), 6) for node in tree.nodes
            if math.isfinite(float(node.score))
        }
        self.failures: List[str] = []
        self._empty: Dict[int, str] = {}
        self._sweep: List[Tuple[Node, Dict[str, Any]]] = []
        self._lock = threading.Lock()

    def note_failure(self, iteration: int, reason: str) -> None:
        """The call itself did not come back.

        Recorded ahead of `note_empty` and never overwritten by it: a call that
        was aborted after fifteen minutes and a model that answered with nothing
        are the same empty string one frame later, and they need opposite fixes.
        Measured on this stack: a thinking-enabled whole-program rewrite ran
        900 seconds without the provider sending a single response header, the
        proxy's ceiling cut it off, and the search recorded it as "the model
        returned an empty reply" — sending the reader to look for output that
        never existed.
        """
        with self._lock:
            self._empty[iteration] = f"this call returned nothing: {reason}"

    def note_empty(self, iteration: int) -> None:
        """Two failures wear the same empty reply and need opposite fixes."""
        spent = self.usage.of(iteration)
        with self._lock:
            if iteration in self._empty:
                # Already explained by the call that never came back.
                return
            if spent is not None and spent.capped:
                self._empty[iteration] = (
                    f"the model spent all {spent.completion} output tokens on hidden "
                    f"thinking and had not started the answer when it hit the "
                    f"{self.spec.max_tokens_per_call} per-call ceiling. Raise "
                    "max_tokens_per_call in the task's scorecard.json — whether the "
                    "model thinks at all is the injected Model's own configuration"
                )
            else:
                self._empty[iteration] = "the model returned an empty reply"

    def snapshot_tree(self) -> None:
        """Write the tree beside this search's candidates, atomically.

        Called after anything that changes what a reader would see: a node
        landing, a sweep's verdicts, the run finishing. The file is small — a
        few KB at twenty nodes — so rewriting it whole costs less than tracking
        what changed, and a crash then loses at most the last expansion.

        This is what answers "show me the tree" without replaying an event log.
        The stream stays the live channel; this is the one a reader can open.
        """
        if self._tree_path is None:
            return
        summary = self.tree.summary()
        summary["schemaVersion"] = TREE_SCHEMA_VERSION
        summary["search_id"] = self.spec.search_id
        summary["updated_at"] = events.utc_now()
        for node in summary.get("tree", []):
            detail = self._detail.get(node.get("index"))
            if detail:
                node.update(detail)
        write_tree_snapshot(self._tree_path, summary)

    def on_event(self, kind: str, payload: Dict[str, Any]) -> None:
        if kind == "selected":
            self.attempted += 1
            self.emit(events.selected(payload["parent_index"], payload["ancestors"]))
        elif kind == "seeded":
            seed_score = payload["metrics"].get(SCORE_KEY)
            if isinstance(seed_score, (int, float)) and math.isfinite(float(seed_score)):
                self._distinct_scores.add(round(float(seed_score), 6))
            # The seed's source goes into the same store an expansion's does.
            # Without it the detail view has no "before" to diff against, and
            # since almost every node's parent is the root, that meant every
            # diff in the run rendered as pure addition.
            seed_code = ""
            node = payload.get("node")
            if node is not None and getattr(node, "program", None) is not None:
                seed_code = getattr(node.program, "code", "") or ""
            seed_hash = self.store.put(self.spec.search_id, seed_code) if seed_code.strip() else None
            self._detail[0] = {"code_hash": seed_hash, "adopted": True, "reason": None,
                               "category": None}
            self.emit(events.seeded(
                0, payload["metrics"].get(SCORE_KEY),
                code_hash=seed_hash, code_chars=len(seed_code) or None,
                metrics={
                    key: float(value) for key, value in payload["metrics"].items()
                    if isinstance(value, (int, float)) and key != SCORE_KEY
                },
            ))
        elif kind == "discarded":
            # A proposal the staleness filter threw away. It cost a model call
            # and produced no node, so a run that planned 20 expansions and made
            # 18 has these as the difference — and until this branch existed the
            # aggregator emitted the event and nobody listened, leaving the
            # shortfall on the panel with no explanation anywhere.
            self.discarded += 1
            self.emit(events.log(
                "warn",
                "a candidate was thrown away before it could be measured: the tree had moved "
                "on by the time it came back, so it was answering a question that no longer "
                f"had a place in the search ({_change_summary_of(payload)})",
            ))
        elif kind == "node":
            self._node(payload)
        elif kind == "swept":
            self._swept(payload)
        elif kind == "repaired":
            # An extra model call the user paid for, so it is said out loud. Not
            # whether it worked: the worker checks on one rollout shard and the
            # final draw is not checked at all, so the only measurement that
            # answers "did it land" is the node's own score on the gate, which
            # arrives with the next `expanded` event.
            #
            # "scored nothing", not "did not run": a candidate that runs and
            # gets every case wrong lands here just as often as one that raises.
            self.emit(events.log(
                "info",
                "a candidate scored nothing and was drawn again with its own error in hand "
                "(attempt %s): %s" % (payload.get("attempt", 1), payload.get("why", "")),
            ))

    def _node(self, payload: Dict[str, Any]) -> None:
        node: Node = payload["node"]
        metrics: Dict[str, Any] = payload["metrics"]
        raw_score = metrics.get(SCORE_KEY)
        if isinstance(raw_score, (int, float)) and math.isfinite(float(raw_score)):
            # Rounded, so float noise between two identical evaluations does
            # not read as two different scores.
            self._distinct_scores.add(round(float(raw_score), 6))
        ops: Dict[str, Any] = payload["ops"]
        code = str(ops.get("code") or "")
        iteration = int(ops.get("iteration") or 0)
        valid = bool(node.program.valid)
        code_hash = self.store.put(self.spec.search_id, code) if code.strip() else None
        self._detail.setdefault(node.index, {})["code_hash"] = code_hash

        with self._lock:
            # Ours wins over the gate's. Both are true — the source *is* empty —
            # but "the gate refused an empty program" is the symptom and "the
            # model spent its whole budget thinking" is the cause, and only one
            # of them tells the user which knob to turn.
            empty = self._empty.pop(iteration, "")
            error = empty or node.program.error
        if not valid:
            self.failures.append(error or "the candidate produced no score")
        else:
            self.scored += 1

        self.emit(events.expanded(
            node.index, node.parent_index, _depth(self.tree, node),
            metrics.get(SCORE_KEY) if valid else None, valid,
            change_summary=str(ops.get("change_summary") or "") or None,
            code_hash=code_hash, code_chars=len(code) or None,
            error=error or None, iteration=iteration,
            # Written down because it steered the search. A prior that moves
            # where the budget goes and leaves no trace is a run nobody can
            # explain afterwards.
            promise=node.promise,
        ))
        if valid:
            criteria = {
                key: float(value) for key, value in metrics.items()
                if isinstance(value, (int, float)) and key not in (SCORE_KEY, "seconds")
            }
            # Both numbers are the held-out one under PUCT: a node is scored on
            # the gate shards and ranked on that same score. Saying so with two
            # equal fields beats inventing a rollout figure that is not measured.
            self.emit(events.evaluated(
                node.index, float(metrics[SCORE_KEY]), criteria,
                gate_score=float(metrics[SCORE_KEY]),
                rollout_score=float(metrics[SCORE_KEY]),
            ))
        self._sweep.append((node, metrics))

    def _swept(self, payload: Dict[str, Any]) -> None:
        best: Node = payload["best"]
        sweep, self._sweep = self._sweep, []
        for node, metrics in sweep:
            accepted = node.index == best.index and bool(payload.get("changed"))
            violated = metrics.get("violated")
            if node.program.valid:
                reason = "became the best node" if accepted else "did not beat the best node"
                category = None if accepted else "below-threshold"
            elif violated:
                reason = node.program.error or "tripped a veto"
                category = "constraint-violated"
            else:
                # Never reached the point of being compared, which is a different
                # problem from being compared and losing.
                reason = node.program.error or "the candidate did not run"
                category = "candidate-failed"
            self._detail.setdefault(node.index, {}).update(
                {"adopted": accepted, "reason": reason, "category": category})
            self.emit(events.merged(
                node.index, accepted, reason,
                category=category,
                rejected_by=str(violated) if violated else None,
            ))
        # After the verdicts, not before: a snapshot taken mid-sweep would show
        # nodes whose fate the merger has not decided yet.
        self.snapshot_tree()
        # Absolute, never a delta: a replayed delta double-counts where a
        # replayed absolute does not. Cents stay 0 — this system has no price
        # table, and a fabricated number would be shown to the user as fact.
        self.emit(events.cost(self.usage.read(), 0))

    def note_outcome(self, outcome: Any, planned: int) -> None:
        """Say why the search stopped, when that is not "it ran out of budget".

        `stop_reason` and `retired_workers` come back on the framework's result
        and were being dropped. A run that ends early because every worker
        retired, or because a backend error killed it, is not the same event as
        one that spent its expansions — and told apart only here.

        The reason is recorded on `search_finished` unconditionally, including
        the ordinary ones this stays quiet about. Suppressing the log line for
        `max_iters` keeps a normal ending from reading like a fault; suppressing
        the *field* as well left a run that stopped at 8 of 20 expansions with
        no recoverable account of why, and the one number that would have
        explained it had been thrown away by this method.
        """
        if outcome is None:
            return
        reason = str(getattr(outcome, "stop_reason", "") or "")
        error = str(getattr(outcome, "error", "") or "")
        retired = int(getattr(outcome, "retired_workers", 0) or 0)
        done = len(self.tree.nodes) - 1  # the seed is not an expansion
        self._stop_reason = reason
        self._planned = planned

        if error:
            self.emit(events.log("warn", f"the search was ended by an error: {error[:300]}"))
        if retired:
            self.emit(events.log(
                "warn",
                f"{retired} workers dropped out partway through — their model calls failed "
                "often enough in a row that the framework stopped retrying them. This search "
                "will make noticeably fewer expansions than planned.",
            ))
        if 0 <= done < planned and self.discarded:
            # Said even when the framework's own stop reason is one of the dull
            # ones. `max_iters` names the budget, and on a run whose selections
            # all landed the budget was not the binding constraint — the
            # discarded proposals were, and pointing the reader at a budget knob
            # that was never the problem is worse than saying nothing.
            self.emit(events.log(
                "info",
                f"planned {planned} expansions, made {done}; {self.discarded} proposal(s) came "
                "back after the tree had moved on and were thrown away before they could be "
                "measured.",
            ))
        elif 0 <= done < planned and reason and reason not in ("max_iters", "max_calls"):
            self.emit(events.log(
                "info",
                f"planned {planned} expansions, made {done}; it stopped because {reason}.",
            ))

    def finish(self, status: str) -> None:
        # Last, and on every path: a run that died mid-sweep still leaves a tree
        # worth reading, and this is the only write a crashed search gets.
        self.snapshot_tree()
        if status == "succeeded" and len(self._distinct_scores) == 1 and len(self.tree.nodes) > 3:
            # Succeeded is a claim that the search searched. When every valid
            # candidate landed on the same number as the seed — watched a
            # Gaussian-integral run finish 9 nodes all at 0.6666667 — the run
            # walked blind: no candidate ever outranked another, selection had
            # nothing to select on, and "became the best node" was float noise. That is
            # a scoring problem, not a candidate problem, and it deserves to be
            # said before the status line frames the run as an achievement.
            self.emit(events.log(
                "warn",
                f"all {len(self.tree.nodes)} candidates scored the same "
                f"({next(iter(self._distinct_scores)):.4f}). The scoring is insensitive to "
                "these changes and the search got no signal at all — most likely the "
                "candidates do not implement the interface the evaluator calls, or the "
                "scoring only looks at a part none of them touched.",
            ))
        if status == "succeeded" and self.attempted and not self.scored:
            # Not "the search found nothing": nothing ever ran. Reporting that as
            # success sends the user looking at their scorecard for a fault that
            # is not there.
            status = "failed"
            common = max(set(self.failures), key=self.failures.count) if self.failures else "no reason given"
            self.emit(events.log(
                "error",
                f"none of the {self.attempted} expansions produced a candidate that ran; "
                f"the most common reason was: {common}",
            ))

        best = self.tree.best()
        test_score: Optional[float] = None
        if status != "failed" and best.program.valid and self.domain.test_shards:
            # The only number in the run the search never optimised against,
            # which is the only reason a reported improvement means anything
            # outside this process.
            valid, metrics, error = self.domain.evaluate(
                best.program.code, self.domain.test_shards)
            if valid:
                test_score = float(metrics[SCORE_KEY])
            else:
                self.emit(events.log("warn", f"the best candidate failed on the test shards: {error}"))

        self.emit(events.search_finished(
            status, best.index if best.program.valid else None, len(self.tree.nodes),
            best_test_score=test_score,
            stop_reason=self._stop_reason,
            expansions_planned=self._planned,
        ))
        log.info("run %s finished: status=%s nodes=%d best=%d",
                 self.spec.search_id, status, len(self.tree.nodes), best.index)


# --- Refusals and wiring helpers ---------------------------------------------


def _change_summary_of(payload: dict[str, Any]) -> str:
    """The one line a discarded candidate left behind, for the warning."""
    summary = str((payload.get("ops") or {}).get("change_summary") or "").strip()
    return summary[:120] or "no summary"


def _refuse_unrunnable(spec: RunSpec) -> None:
    if spec.resume_from_sequence and not spec.resume_nodes:
        # Continuing the sequence numbering without the tree those numbers were
        # written against would append node 0 on top of a graph that already has
        # one, leaving a single index holding two different candidates.
        raise _Refusal(
            "this search was asked to resume but no tree came with it, so a new node would "
            "reuse an index the graph already spent"
        )
    if not spec.scorecard:
        raise _Refusal("this search was given no scorecard, so there is no way to tell candidates apart")
    # Before the search starts, alongside the other configuration faults, so a
    # bad number is a refusal rather than a run that begins and then dies.
    try:
        _prior_exponent(spec.options)
    except ValueError as error:
        raise _Refusal(str(error)) from error
    for criterion in spec.scorecard.get("criteria") or []:
        kind = (criterion.get("normalize") or {}).get("kind")
        if kind not in KNOWN_NORMALIZE:
            raise _Refusal(
                f"criterion \"{criterion.get('name', criterion.get('id'))}\" uses the "
                f"normalisation {kind!r}, which this side does not know; supported are "
                f"{sorted(KNOWN_NORMALIZE)}"
            )
    mode = _mode_of(spec)
    if mode != "custom_script":
        # One way to score. `llm_judge` has no judge channel here — the request
        # carries one optimizer model, and grading candidates with the model
        # that wrote them is self-scoring; `dataset_metric`/`test_gate` were
        # never ported. Refused by name rather than falling through to code
        # that has no domain for them.
        raise _Refusal(
            f"this engine scores by sandboxed evaluation only; a scorecard measured by "
            f"{mode!r} cannot run here — write an evaluator script instead"
        )
    if spec.packages:
        # Before anything is measured, and refused rather than warned about: a
        # run whose candidates were promised a library and did not get it fails
        # every expansion with ModuleNotFoundError and reads as a model that
        # cannot write code.
        from .provision import ProvisionError, ensure

        try:
            installed, _note = ensure(spec.packages)
        except ProvisionError as error:
            raise _Refusal(str(error)) from error
        if installed:
            log.info("run %s provisioned %s", spec.search_id, ", ".join(installed))
    if not spec.script.strip():
        # Said here rather than at the first expansion: every candidate would
        # fail identically, and a search that reports twelve failed candidates
        # sends the user reading candidates for a fault in the configuration.
        raise _Refusal("this scorecard is scored by an evaluator script but was given none")
    missing = missing_candidate_runtime()
    if missing:
        raise _Refusal(
            f"the sidecar is missing the candidate runtime: {', '.join(missing)}. "
            "The AST gate lets candidates import them, so without them every candidate "
            "fails. Run `uv sync --extra candidates` in services/evolve"
        )


def _split_of(spec: RunSpec) -> Dict[str, int]:
    """The scorecard's shard counts.

    Nothing is staged, so the counts live only on the card. What a shard *is*
    belongs to the evaluator — it builds case `i` from the index — so counting
    shards from a directory of rows would be wrong, which is why there is no
    directory.
    """
    for criterion in spec.scorecard.get("criteria") or []:
        split = (criterion.get("measure") or {}).get("split")
        if isinstance(split, dict):
            return {
                "gate": int(split.get("gateShards") or 0),
                "rollout": int(split.get("rolloutShards") or 0),
            }
    return {"gate": 0, "rollout": 0}


def _group_tasks(spec: RunSpec) -> List[Any]:
    """Rollout groups first, gate groups last.

    The engine splits its task list by position, so this ordering is what
    makes its held-out set exactly the groups that decide.
    """
    from agentdescent.evolution import Task

    counts = _split_of(spec)
    total = counts["rollout"] + counts["gate"]
    if counts["gate"] < 4:
        raise _Refusal(
            f"the hold-out needs at least 4 groups to tell an improvement from noise; "
            f"this card allots {counts['gate']}"
        )
    return [
        Task(id=f"{spec.search_id}:group-{index}", prompt=f"group {index}",
             meta={"shard": index})
        for index in range(total)
    ]


def _group_held_out_frac(spec: RunSpec) -> float:
    counts = _split_of(spec)
    total = counts["rollout"] + counts["gate"]
    if not counts["gate"] or not total:
        raise _Refusal("this scorecard allots no hold-out groups, so there is no acceptance gate")
    return counts["gate"] / total


def _mode_of(spec: RunSpec) -> str:
    """The scorecard's measurement kind. One card, one mode — a mixed card is
    refused up front rather than half-measured."""
    criteria = spec.scorecard.get("criteria") or []
    kinds = {str((c.get("measure") or {}).get("kind") or "") for c in criteria}
    if len(kinds) > 1:
        raise _Refusal(
            f"one scorecard mixes the measurement kinds {sorted(kinds)}; this engine does "
            f"one at a time"
        )
    return next(iter(kinds), "custom_script")


def _mode(spec: RunSpec) -> str:
    mode = str(spec.options.get("mode") or ("async" if spec.workers > 1 else "serial"))
    if mode not in ("async", "serial", "sync"):
        raise _Refusal(f"unknown search mode {mode!r}; the choices are serial / sync / async")
    return mode


def _prior_exponent(options: Dict[str, Any]) -> float:
    """``options["prior_exponent"]``: how sharply the model's rating bends P(s,a).

    ``0.0`` is upstream — a uniform ``1/N`` for every node, to the
    floating-point bit — and is what an unset option means, so a run that says
    nothing about the prior is the search upstream ships.

    The power is the point rather than a knob for its own sake. Upstream's own
    arithmetic: with a prior proportional to the rating, a candidate rated 8
    against a mean of 5.5 gets 1.45x the exploration term and a dead end rated 2
    still gets 0.36x. Squared, those become 2.12x and 0.13x — the difference
    between widening exploration and *aiming* it.
    """
    raw = options.get("prior_exponent")
    if raw is None:
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"prior_exponent must be a number, got {raw!r}") from error
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"prior_exponent must be a non-negative number, got {value!r}")
    return value


def _rollout_shards(spec: RunSpec) -> Tuple[int, ...]:
    """What the worker's fix-it check may read: the rollout split, never the gate.

    One shard is enough and one is what it takes: the question is "does this
    candidate work at all", not "how well", and every extra shard is time on a
    worker that could be drawing the next candidate instead.
    """
    return tuple(range(int(_split_of(spec)["rollout"])))[:1]


def _rollout_check(
    domain: Any, shards: Tuple[int, ...],
) -> Optional[Callable[[str], Tuple[bool, Dict[str, Any], str]]]:
    """`code -> (valid, metrics, error)` on a rollout shard, or nothing.

    Absent when there is no rollout shard to spare, which turns the fix-it loop
    off rather than letting it fall back to the gate — a loop that cannot check
    without reading the deciding split should not run at all.
    """
    if not shards:
        return None

    def check(code: str) -> Tuple[bool, Dict[str, Any], str]:
        return domain.evaluate(code, shards)

    return check


def _depth(tree: PuctTree, node: Node) -> int:
    depth, cursor = 0, node
    while cursor.parent_index is not None:
        cursor = tree.nodes[cursor.parent_index]
        depth += 1
    return depth
