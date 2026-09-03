# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Program artifact optimization: the implementation AgentServer routes `program` to.

`PuctProgramArtifactProvider` satisfies the `ProgramArtifactProvider` protocol
(`program_opt.provider`), which is the contract's name and what `isinstance`
checks; this class is the PUCT-search implementation of it.

The search lives alongside this module (`puct_engine` and what it imports);
this module is the translation layer between it and the RSI contract. Three
things are being translated, and each is a shape change rather than a rename:

* **Nine search events into three task events.** The search speaks in
  selections, expansions, evaluations and merges; the contract speaks in status,
  progress and node. A node is only complete once the merger has ruled on it, so
  the projection buffers rather than forwarding each part.
* **A live stream into durable snapshots.** The contract requires `read_state`,
  `read_report` and `get_tree` to answer after a restart, so everything the
  events carry is written into `run_dir` as it happens.
* **In-process rather than over HTTP.** ScienceDiscovery runs this engine as a
  sidecar behind a control plane, which is why the engine takes its model as an
  injected callable. Here that seam is filled directly, so no model key crosses
  a process boundary and there is no proxy to stand up.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import logging

from openjiuwen.rsi.artifact_rsi.program_opt.engine import RunSpec
from openjiuwen.rsi.artifact_rsi.program_opt.probe import ProbeError, run_probe
from openjiuwen.rsi.artifact_rsi.program_opt.program import DEFAULT_ENTRYPOINT, bundle
from agentdescent.filetree import load_tree

from openjiuwen.rsi.artifact_rsi.program_opt.puct_engine import PuctEngine
from openjiuwen.rsi.artifact_rsi.program_opt.execution import (
    EvaluationExecution,
    ExecutionUnavailable,
    execution_from_sys_operation,
)
from openjiuwen.rsi.artifact_rsi.program_opt.runtime import (
    ModelConfigError,
    completion_factory_from_model,
)
from openjiuwen.rsi.artifact_rsi.program_opt.state import (
    ProgramRunState,
    read_report_file,
    read_state_file,
    read_tree_file,
)
from openjiuwen.rsi.artifact_rsi.request import ArtifactEngineRequest
from openjiuwen.rsi.events import EventStatus, OnEvent
from openjiuwen.rsi.schema import (
    ArtifactRef,
    ArtifactValidationResult,
    EngineReport,
    EngineResult,
    EngineState,
    TreeResponse,
)

#: How many candidates one task may draw before it stops.
#:
#: `max_iterations` is the contract's only budget field. The search additionally
#: needs a worker count, and one is the honest default: measured against a real
#: provider, four concurrent mutation calls each took 11-20 minutes where one
#: alone took 3, and the wave finished *slower* than running them in sequence.
DEFAULT_WORKERS = 1

#: The most a task may ask for. Each worker is one model call and one sandbox
#: evaluation in flight; past a handful the limit stops being this process.
MAX_WORKERS = 8


logger = logging.getLogger(__name__)


async def _notify(on_event: OnEvent | None, event: Any) -> None:
    """Deliver one event, honouring back-pressure but not dying of it.

    The contract's two rules meet here: the provider must *await* `on_event`
    (the queue's back-pressure is ours to carry), and a callback exception is
    an observability-channel fault that must not roll back persisted results
    or fail the task — AgentServer compensates through the query interfaces.
    Before this wrapper, a raising callback propagated into the engine and
    came back out as ENGINE_ERROR: the search died of a broken telescope.
    """
    if on_event is None:
        return
    try:
        await on_event(event)
    except Exception as error:  # noqa: BLE001 - observation must not kill the run
        logger.warning("event delivery failed (observability channel): %s", error)


class PuctProgramArtifactProvider:
    """Program optimization by repeatedly rewriting a program and keeping what scores better.

    One instance serves many tasks; per-task state lives under each request's
    `run_dir` rather than on the instance, so a restart loses nothing a query
    could have answered.
    """

    artifact_type: Literal["program"] = "program"

    def __init__(
        self,
        *,
        sys_operation: Any | None = None,
        execution: EvaluationExecution | None = None,
    ) -> None:
        #: agent-core's own sandbox: a `SysOperation` (mode=sandbox) that
        #: AgentServer registered as a `SysOperationCard` and resolved via
        #: `Runner.resource_mgr.get_sys_operation(card_id)`. The provider-local
        #: bwrap/seatbelt isolation is gone — this is the only way a candidate
        #: is ever executed.
        self._sys_operation = sys_operation
        #: Test seam: a ready-made execution wins over building one from the
        #: SysOperation, the same way an injected completion factory does.
        self._execution = execution
        #: `task_id -> stop flag`. The search polls it between expansions, which
        #: is what makes `terminate` land at a node boundary rather than mid
        #: sandbox run.
        self._stopping: dict[str, threading.Event] = {}
        #: `task_id -> live state`, so `pause`/`terminate` can set the stop
        #: *intent* on the run they are stopping. Same lifetime as the flag.
        self._live: dict[str, ProgramRunState] = {}
        self._lock = threading.Lock()

    # -- validation ------------------------------------------------------------

    def validate_input(self, artifact_path: str | None) -> ArtifactValidationResult:
        """Whether this path is a program the search could start from.

        Starts no optimization and creates no task snapshot. Deliberately cheap
        and synchronous: the contract's `rsi.dataset.validate` is a form check
        the user waits on. Whether the *scoring* can tell a good candidate from
        a bad one is a different and far more expensive question — it costs
        sandboxed evaluations — and it is asked inside `run`.
        """
        errors: list[dict[str, str]] = []
        if not str(artifact_path or "").strip():
            errors.append({
                "code": "ARTIFACT_PATH_REQUIRED",
                "message": "program optimization needs a starting program",
            })
            return ArtifactValidationResult(valid=False, errors=errors)

        path = Path(artifact_path).expanduser()
        if not path.exists():
            errors.append({
                "code": "ARTIFACT_NOT_FOUND",
                "message": f"nothing at {artifact_path}",
            })
            return ArtifactValidationResult(valid=False, errors=errors)

        # A directory is a program too. What is being optimized is a file tree
        # — one file is the common case and not the only one — so a seed that
        # is a package is read whole, at its own relative paths.
        try:
            files = _seed_files(path)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            return ArtifactValidationResult(valid=False, errors=[{
                "code": "ARTIFACT_UNREADABLE",
                "message": f"{artifact_path} could not be read as a program: {error}",
            }])

        if not any(text.strip() for text in files.values()):
            # And stop: an empty program has nothing further to say, and
            # letting it flow on used to double-report the same fact as the
            # gate's "empty source".
            return ArtifactValidationResult(valid=False, errors=[
                {"code": "ARTIFACT_EMPTY", "message": "the program is empty"},
            ])

        entrypoint = _entrypoint_of(files)
        if entrypoint is None:
            errors.append({
                "code": "ARTIFACT_ENTRYPOINT_UNCLEAR",
                "message": "the evaluator has to import one of these files and it is not "
                           f"clear which: {', '.join(sorted(files)[:10])}. Name it "
                           f"`{DEFAULT_ENTRYPOINT}`, or set `entrypoint` in the scorecard.",
            })
            return ArtifactValidationResult(valid=not errors, errors=errors)
        source = files[entrypoint]

        # A shape check on the starting point only. Candidates are *not* put
        # through this during the search -- the sandbox is what confines them,
        # and the evaluator's own import is what decides whether one is usable.
        # What it buys here is that a seed that is not a program at all — bad
        # syntax, a forbidden import, a dunder — is refused while the user is
        # still looking at the form. It deliberately does not require any
        # particular function: only the scorecard knows what the evaluator
        # calls, and this method never sees the scorecard.
        from openjiuwen.rsi.artifact_rsi.program_opt.program import (
            local_roots,
            validate_source,
        )

        # The program's own modules are importable from within it, so the gate
        # is told what they are. Otherwise `from helpers.scale import factor`
        # reads as a dependency nobody installed.
        ok, reason = validate_source(source, local=local_roots(files))
        if not ok:
            errors.append({"code": "ARTIFACT_REJECTED_BY_GATE", "message": reason})

        return ArtifactValidationResult(valid=not errors, errors=errors)

    # -- execution -------------------------------------------------------------

    async def run(
        self,
        request: ArtifactEngineRequest,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        """Start a new program optimization task and stream its progress.

        Every state, node, report and artifact an event refers to is persisted
        before `on_event` is invoked; `running` goes out first and a terminal
        status last.

        The search is synchronous and CPU/IO bound — it spends its time in
        sandboxed evaluations and model calls — so it runs on a worker thread and
        events are handed back to the caller's loop. Awaiting `on_event` from
        that thread would deadlock; scheduling onto the loop and waiting is what
        keeps the contract's back-pressure rule (`await on_event`) honest.
        """
        return await self._drive(request, on_event, resumed=False)

    async def resume(
        self,
        request: ArtifactEngineRequest,
        on_event: OnEvent | None = None,
    ) -> EngineResult:
        """Continue the original task without creating a second root.

        The tree is read back from `run_dir`, so the node indices a resumed run
        appends continue where the previous attempt stopped. Without it a new
        node would take an index the first attempt already spent, and one index
        would hold two different candidates.

        A `terminated` task is not resumable — that is the whole difference
        between `terminate` and `pause`, and honouring it here is what makes
        the distinction real rather than a status label.
        """
        prior = read_state_file(request.task_id)
        if prior is not None and prior.status == "terminated":
            return EngineResult(
                task_id=request.task_id,
                status="terminated",
                final_node_id=prior.best_node_id,
                error_code="TERMINATED_NOT_RESUMABLE",
                error_message="this task was terminated; terminate is final — "
                              "only a paused task can be resumed",
            )
        return await self._drive(request, on_event, resumed=True)

    async def pause(self, task_id: str, on_event: OnEvent | None = None) -> EngineResult:
        """Stop at the next node boundary, resumable under the same task.

        The whole stop mechanism is `terminate`'s — the same flag, polled at
        the same boundary, a candidate mid-evaluation finished and recorded.
        The two differ only in what the stopped search folds to: `paused` is
        the one non-terminal outcome, and the one `resume` accepts.
        """
        with self._lock:
            flag = self._stopping.get(task_id)
            live = self._live.get(task_id)
        if flag is None or live is None:
            # Nothing is running: there is no boundary to stop at, and saying
            # "paused" about a task that is not moving would be a lie the next
            # query contradicts.
            state = read_state_file(task_id)
            return EngineResult(
                task_id=task_id,
                status=state.status if state else "created",
                final_node_id=state.best_node_id if state else None,
                error_code="TASK_NOT_RUNNING",
                error_message="pause reached no running search for this task",
            )
        live.stopped_status = "paused"
        flag.set()
        await _notify(on_event, EventStatus(status="paused"))
        return EngineResult(
            task_id=task_id,
            status="paused",
            final_node_id=live.best_node_id,
            error_code=None,
            error_message=None,
        )

    async def terminate(self, task_id: str, on_event: OnEvent | None = None) -> EngineResult:
        """Stop the task permanently at the next node boundary, keeping
        everything already measured.

        Not a kill: a candidate mid-evaluation is finished and recorded. The
        alternative would throw away a sandbox run the user already paid for and
        leave the tree describing a node that has no score.
        """
        with self._lock:
            flag = self._stopping.get(task_id)
            live = self._live.get(task_id)
        if flag is None or live is None:
            # Nothing is in flight, so nothing gets terminated — and saying
            # "terminated" about a completed task is a status change the next
            # `read_state` contradicts, delivered to the event stream as fact.
            # Same honesty rule `pause` follows. Terminating an already
            # terminated task is the one idempotent success in this branch.
            state = read_state_file(task_id)
            status = state.status if state else "created"
            return EngineResult(
                task_id=task_id,
                status=status,
                final_node_id=state.best_node_id if state else None,
                error_code=None if status == "terminated" else "TASK_NOT_RUNNING",
                error_message=None if status == "terminated"
                else "terminate reached no running search for this task",
            )
        # A terminate that races a pause wins: the stronger intent, and the
        # one the caller can still see refused nowhere.
        live.stopped_status = "terminated"
        flag.set()
        await _notify(on_event, EventStatus(status="terminated"))
        return EngineResult(
            task_id=task_id,
            status="terminated",
            final_node_id=live.best_node_id,
            error_code=None,
            error_message=None,
        )

    # -- queries ---------------------------------------------------------------

    def read_state(self, task_id: str) -> EngineState:
        """The latest durable state, read without side effects."""
        state = read_state_file(task_id)
        if state is None:
            return EngineState(
                task_id=task_id, status="created", iteration=0, total_iterations=0,
                best_node_id=None, score=None, baseline=None, updated_at="",
                error_code="TASK_NOT_FOUND", error_message="no state for this task",
            )
        return state.to_engine_state()

    def read_report(self, task_id: str) -> EngineReport:
        """The current or final report, with every artifact the task produced."""
        report = read_report_file(task_id)
        if report is None:
            return EngineReport(
                task_id=task_id, status="created", best_node_id=None,
                artifact_index=[], summary="no report for this task",
            )
        return report

    def get_tree(self, task_id: str) -> TreeResponse:
        """The complete tree, rejected branches included."""
        tree = read_tree_file(task_id)
        return tree if tree is not None else TreeResponse(nodes=[], depth=0, iteration=0)

    def locate_artifact(self, task_id: str, artifact_id: str | None = None) -> ArtifactRef:
        """The task's final program, or one node's snapshot.

        `artifact_id=None` selects the final artifact. The path is
        provider-local: AgentServer owns the ownership check and the URL.

        Every candidate the search wrote is on disk, addressed by the hash of its
        own content — so an id is stable by construction and there is no lookup
        table that could disagree with the tree.
        """
        report = read_report_file(task_id)
        index = list(report.artifact_index) if report else []
        if not index:
            raise FileNotFoundError(f"no artifacts recorded for task {task_id}")
        if artifact_id is None:
            # The best node's own `snapshot_artifact_id`, not a scan for a
            # matching `node_id`. Artifacts are addressed by content, so two
            # nodes that reached the same program share one — and that one can
            # only name a single node. Scanning by node_id therefore misses
            # whenever the winner is not the node the artifact was recorded
            # under, and the fall-through returns a different program.
            artifact_id = self._best_artifact_id(task_id, report.best_node_id if report else None)
            if artifact_id is None:
                return index[-1]
        for ref in index:
            if ref.artifact_id == artifact_id:
                return ref
        raise FileNotFoundError(f"artifact {artifact_id} does not belong to task {task_id}")

    def _best_artifact_id(self, task_id: str, best_node_id: str | None) -> str | None:
        """Which artifact the winning node points at."""
        if not best_node_id:
            return None
        tree = read_tree_file(task_id)
        for node in tree.nodes if tree else ():
            if node.node_id == best_node_id:
                return node.snapshot_artifact_id
        return None

    # -- the bridge ------------------------------------------------------------

    async def _drive(
        self,
        request: ArtifactEngineRequest,
        on_event: OnEvent | None,
        *,
        resumed: bool,
    ) -> EngineResult:
        # Before anything touches disk: a second `run` or `resume` on a task
        # whose search is still in flight would put two engines on one
        # `run_dir` — both writing state.json and tree.json, and the second
        # registration would steal the stop flag so pause/terminate could only
        # reach the newcomer. Refused whole, with the running task untouched.
        with self._lock:
            if request.task_id in self._stopping:
                return EngineResult(
                    task_id=request.task_id,
                    status="running",
                    final_node_id=None,
                    error_code="TASK_ALREADY_RUNNING",
                    error_message="this task already has a search in flight; "
                                  "pause or terminate it before starting another",
                )
        loop = asyncio.get_running_loop()
        run_dir = Path(request.run_dir)
        state = ProgramRunState(
            task_id=request.task_id,
            run_dir=run_dir,
            total_iterations=int(request.max_iterations),
        )
        if resumed:
            # One task, one durable record. A fresh state would truncate
            # `nodes.json` to this attempt's own nodes on its first write —
            # losing every earlier branch from `get_tree`, and destroying the
            # paused run's tree even when the resume is then refused.
            state.rehydrate()

        try:
            if request.model is None:
                # The contract routes the model resource through AgentServer:
                # it resolves `model_refs["optimizer"]` and hands over a live
                # instance. Nothing here may fall back to reading a config —
                # that is exactly the client-building the contract forbids.
                raise ModelConfigError(
                    "an initialized Model instance is required: AgentServer resolves "
                    "model_refs['optimizer'] via Runner.resource_mgr.get_model"
                )
            execute = self._execution or execution_from_sys_operation(
                self._sys_operation, loop)
            spec = self._spec_for(request, resumed=resumed)
        except (ModelConfigError, ExecutionUnavailable,
                FileNotFoundError, ValueError) as error:
            code = type(error).__name__.replace("Error", "").upper() or "INVALID_REQUEST"
            state.fail(code, str(error))
            await _notify(on_event, EventStatus(status="failed"))
            return EngineResult(
                task_id=request.task_id, status="failed", final_node_id=None,
                error_code=code, error_message=str(error),
            )

        stop = threading.Event()
        with self._lock:
            self._stopping[request.task_id] = stop
            self._live[request.task_id] = state

        # Every event the search emits is projected, persisted, then handed to
        # the caller's loop — in that order, because the contract requires a
        # snapshot to be durable before the event announcing it is delivered.
        #
        # Held for the whole of that, because with more than one worker the
        # engine emits from N threads at once and neither half is safe under
        # that. `ProgramRunState` has no lock of its own: two folds interleaving
        # would race the node map, and `_persist` walking `sorted(self.nodes)`
        # while another thread inserts raises outright. Ordering matters too —
        # the contract's events are a sequence, and two threads delivering at
        # once is not one.
        fold = threading.Lock()

        def sink(emitted: dict[str, Any]) -> None:
            with fold:
                _fold_and_deliver(emitted)

        def _fold_and_deliver(emitted: dict[str, Any]) -> None:
            for event in state.absorb(emitted):
                if on_event is None:
                    continue
                future = asyncio.run_coroutine_threadsafe(on_event(event), loop)
                # Waited on, not fired and forgotten: the contract makes the
                # provider carry the queue's back-pressure. But waited-on is
                # not died-of: a callback exception is an observability fault,
                # and the search it was watching must outlive it.
                try:
                    future.result()
                except Exception as error:  # noqa: BLE001
                    logger.warning(
                        "event delivery failed (observability channel): %s", error)

        await _notify(on_event, EventStatus(status="running"))
        state.start()

        # Before any budget is spent: score the starting point, then score a copy
        # deliberately made worse. Two numbers that come back the same mean the
        # scoring cannot separate a good candidate from a bad one, and a search
        # on flat terrain is a random walk that looks completely normal from
        # outside -- every event fires, every candidate is recorded, and the run
        # simply reports that it found nothing. Worth a handful of evaluations to
        # turn that into a sentence naming the evaluator.
        try:
            await asyncio.to_thread(run_probe, spec, execute)
        except ProbeError as refusal:
            state.fail("PROBE_REFUSED", str(refusal))
            await _notify(on_event, EventStatus(status="failed"))
            return EngineResult(
                task_id=request.task_id, status="failed", final_node_id=None,
                error_code="PROBE_REFUSED", error_message=str(refusal),
            )

        engine = PuctEngine(
            completion_factory=completion_factory_from_model(request.model, loop),
            evaluation_execution=execute)
        try:
            await asyncio.to_thread(engine.run, spec, sink, stop.is_set)
        except Exception as error:  # noqa: BLE001 - a crash is a failed task, not a crashed server
            state.fail("ENGINE_ERROR", str(error))
            await _notify(on_event, EventStatus(status="failed"))
            return EngineResult(
                task_id=request.task_id, status="failed", final_node_id=state.best_node_id,
                error_code="ENGINE_ERROR", error_message=str(error),
            )
        finally:
            with self._lock:
                self._stopping.pop(request.task_id, None)
                self._live.pop(request.task_id, None)

        state.finish()
        await _notify(on_event, EventStatus(status=state.status))
        return EngineResult(
            task_id=request.task_id,
            status=state.status,
            final_node_id=state.best_node_id,
            error_code=state.error_code,
            error_message=state.error_message,
        )

    def _spec_for(
        self,
        request: ArtifactEngineRequest,
        *,
        resumed: bool,
    ) -> RunSpec:
        """The contract's six fields plus everything the search additionally needs.

        Two of those extras have no home in `ArtifactEngineRequest` and are read
        from the task's own directory instead:

        * `scorecard` / `script` — how a candidate is scored. The search cannot
          rank without it, and the contract has no field for it, so it is read
          from `<run_dir>/scorecard.json`. A task without one is refused rather
          than scored by something this provider made up.
        * the tree, when resuming.
        """
        run_dir = Path(request.run_dir)
        scorecard_path = run_dir / "scorecard.json"
        if not scorecard_path.is_file():
            raise FileNotFoundError(
                f"program optimization needs a scorecard at {scorecard_path}: it is what ranks "
                "candidates, and nothing in ArtifactEngineRequest carries one"
            )
        card = json.loads(scorecard_path.read_text(encoding="utf-8"))

        files: dict[str, str] = {}
        if request.artifact_path:
            files = _seed_files(Path(request.artifact_path).expanduser())
        entrypoint = str(card.get("entrypoint") or "") or _entrypoint_of(files) or DEFAULT_ENTRYPOINT
        if files and entrypoint not in files:
            raise ValueError(
                f"the scorecard names {entrypoint!r} as the entrypoint and the program "
                f"does not contain it: {', '.join(sorted(files)[:10])}"
            )

        templates = _prompt_templates(run_dir)
        spec = RunSpec(
            search_id=request.task_id,
            algorithm="puct",
            expansions=int(request.max_iterations),
            workers=_workers_from(card.get("workers")),
            scorecard=card.get("scorecard", card),
            scorecard_hash=str(card.get("hash") or "sha256:inline"),
            statement=str(card.get("statement") or ""),
            script=str(card.get("script") or ""),
            packages=_packages_from(card.get("packages")),
            mutation_template=templates["mutation"],
            repair_template=templates["repair"],
            prior_template=templates["prior"],
            baseline_code=bundle(files),
            entrypoint=entrypoint,
            run_dir=str(run_dir),
            options=dict(card.get("options") or {}),
            reply_format=str(card.get("reply_format") or "").strip() or "files",
            # From the scorecard when it says, else `RunSpec`'s default. The
            # model's own request config is opaque here — the contract hands
            # over an initialized instance, not its settings — so the per-run
            # ceiling is the task's to raise. A reasoning model needs more
            # than the 16k default to return anything at all; a scorecard for
            # one should say so.
            max_tokens_per_call=int(card.get("max_tokens_per_call")
                                    or RunSpec.max_tokens_per_call),
        )
        if not resumed:
            return spec

        nodes, baseline, sequence = _resume_from(run_dir)
        return replace(
            spec,
            resume_nodes=nodes,
            resume_baseline=baseline,
            resume_from_sequence=sequence,
        )


def _prompt_templates(run_dir: Path) -> dict[str, str]:
    """The task's own prompt wording, from `run_dir/prompts/`, defaults empty.

    Different tasks need differently assembled prompts — the words are the
    task's to choose, so they are data beside the scorecard rather than code
    inside the provider. Each file is validated against its slot vocabulary
    here, at load: a typo like `${statment}` refused by name now is a sentence;
    discovered mid-run it is a prompt with a hole in it, optimised against for
    the whole budget.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.prompt import (
        MUTATION_REQUIRED,
        MUTATION_SLOTS,
        PRIOR_SLOTS,
        REPAIR_SLOTS,
        validate_template,
    )

    out = {"mutation": "", "repair": "", "prior": ""}
    vocabularies = {
        "mutation": (MUTATION_SLOTS, MUTATION_REQUIRED),
        "repair": (REPAIR_SLOTS, frozenset()),
        "prior": (PRIOR_SLOTS, frozenset()),
    }
    for name, (allowed, required) in vocabularies.items():
        path = run_dir / "prompts" / f"{name}.md"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        validate_template(f"prompts/{name}.md", text, allowed, required)
        out[name] = text
    return out


def _workers_from(value: Any) -> int:
    """How many expansions may be in flight at once.

    One by default, which is what every task got before this was readable: the
    engine emits from each worker thread and the fold that receives those
    events is only now safe under more than one. Bounded rather than trusted —
    the number costs concurrent model calls and concurrent sandbox
    evaluations, and a card asking for two hundred would be asking for the
    provider to melt rather than for a faster search.
    """
    try:
        workers = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WORKERS
    return max(1, min(workers, MAX_WORKERS))


def _seed_files(path: Path) -> dict[str, str]:
    """The starting program as `{relpath: text}`, from a file or a directory.

    A single file is placed at the default entrypoint, which is how every
    one-file run has always worked. A directory keeps its own layout, so a seed
    that is already a package is not renamed into this provider's conventions
    just to be optimized.
    """
    if path.is_dir():
        return load_tree(str(path))
    return {DEFAULT_ENTRYPOINT: path.read_text(encoding="utf-8")}


def _entrypoint_of(files: Mapping[str, str]) -> str | None:
    """Which file the evaluator imports, when the scorecard did not say.

    Guessed only where the guess cannot be wrong: the conventional name, a
    package of that name, or a tree with exactly one Python file in it. A
    directory of five modules gets asked rather than guessed at — picking one
    would send every candidate to an evaluator importing the wrong thing, and
    the run would report that the program never works.
    """
    if not files:
        return DEFAULT_ENTRYPOINT
    for name in (DEFAULT_ENTRYPOINT, f"{Path(DEFAULT_ENTRYPOINT).stem}/__init__.py"):
        if name in files:
            return name
    python = [path for path in files if path.endswith(".py")]
    return python[0] if len(python) == 1 else None


#: A distribution name, optionally pinned. Nothing else.
#:
#: `packages` is written by a model — it works out from the task that a boosting
#: library is wanted, because the person who typed "bring the error down" has no
#: reason to know that. Readability is therefore the whole security boundary: a
#: name a reviewer can recognise is safe to install, and a path, a URL, a VCS ref
#: or a pip option is not, however plausible the surrounding text makes it look.
#: ScienceDiscovery states this rule in the skill that writes the field; here the
#: skill is not in the picture, so it is enforced where the field is read.
_PACKAGE_SPEC = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:==[A-Za-z0-9][A-Za-z0-9.*+!_-]*)?$"
)


def _packages_from(raw: Any) -> tuple[str, ...]:
    """The scorecard's extra dependencies, or a refusal naming the bad one."""
    if not raw:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError("scorecard packages must be a list of distribution names")
    names: list[str] = []
    for entry in raw:
        name = str(entry).strip()
        if not name:
            continue
        if not _PACKAGE_SPEC.match(name):
            raise ValueError(
                f"refusing to install {name!r}: packages takes bare distribution names, "
                "optionally pinned with ==version, and nothing else"
            )
        names.append(name)
    return tuple(names)


def _resume_from(run_dir: Path) -> tuple[tuple[dict[str, Any], ...], dict[str, float], int]:
    """The previous attempt's tree, read back off disk.

    The snapshot the search writes after every expansion is exactly what a
    resume needs, which is why resuming needs nothing from AgentServer beyond
    the same `run_dir`.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.candidates import TREE_FILE

    path = run_dir / TREE_FILE
    if not path.is_file():
        return (), {}, 0
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    nodes = tuple(snapshot.get("tree") or ())
    return nodes, dict(snapshot.get("baseline") or {}), len(nodes)


__all__ = ["DEFAULT_WORKERS", "PuctProgramArtifactProvider"]
