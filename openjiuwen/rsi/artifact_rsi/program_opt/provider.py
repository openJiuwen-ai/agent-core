# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Program artifact optimization: the provider AgentServer routes `program` to.

`ProgramArtifactProvider` is the implementation, not a protocol restating one.
The structural contract is `artifact_rsi.provider.ArtifactProvider`, which this
satisfies and which `isinstance` still checks; a second protocol here would only
repeat it, and there is one program optimizer.

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
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from openjiuwen.rsi.artifact_rsi.program_opt.engine import RunSpec
from openjiuwen.rsi.artifact_rsi.program_opt.probe import ProbeError, run_probe
from openjiuwen.rsi.artifact_rsi.program_opt.runtime import (
    ModelConfigError,
    SandboxUnavailable,
    completion_factory_for,
    load_model_endpoint,
    require_sandbox,
)
from openjiuwen.rsi.artifact_rsi.program_opt.state import (
    ProgramRunState,
    read_report_file,
    read_state_file,
    read_tree_file,
)
from openjiuwen.rsi.artifact_rsi.request import ArtifactEngineRequest
from openjiuwen.rsi.events import EventStatus, OnEvent, emit
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


class ProgramArtifactProvider:
    """Program optimization by repeatedly rewriting a program and keeping what scores better.

    One instance serves many tasks; per-task state lives under each request's
    `run_dir` rather than on the instance, so a restart loses nothing a query
    could have answered.
    """

    artifact_type: Literal["program"] = "program"

    def __init__(self, *, sandbox_backend: str | None = None) -> None:
        #: Names an isolation backend explicitly, for a deployment that knows
        #: better than the probe. Absent means detect.
        self._sandbox_backend = sandbox_backend
        #: `task_id -> stop flag`. The search polls it between expansions, which
        #: is what makes `terminate` land at a node boundary rather than mid
        #: sandbox run.
        self._stopping: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # -- validation ------------------------------------------------------------

    def validate_input(self, artifact_path: str | None) -> ArtifactValidationResult:
        """Whether this path is a program the search could start from.

        Starts no optimization and creates no task snapshot. Deliberately cheap
        and synchronous: the contract's `rsi.dataset.validate` is a form check
        the user waits on. Whether the *scoring* can tell a good
        candidate from a bad one is a different and far more expensive question —
        it costs four sandboxed evaluations — and it is asked inside `run`.
        """
        errors: list[dict[str, str]] = []
        if not str(artifact_path or "").strip():
            errors.append({
                "code": "ARTIFACT_PATH_REQUIRED",
                "message": "program optimization needs a starting program",
            })
            return ArtifactValidationResult(valid=False, errors=errors)

        path = Path(artifact_path).expanduser()
        if not path.is_file():
            errors.append({
                "code": "ARTIFACT_NOT_FOUND",
                "message": f"no file at {artifact_path}",
            })
            return ArtifactValidationResult(valid=False, errors=errors)

        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            return ArtifactValidationResult(valid=False, errors=[{
                "code": "ARTIFACT_UNREADABLE",
                "message": f"{artifact_path} could not be read as UTF-8 text: {error}",
            }])

        if not source.strip():
            errors.append({"code": "ARTIFACT_EMPTY", "message": "the program is empty"})

        # The same AST gate every candidate passes. Checking the starting point
        # against it here means a seed that could never be rewritten is refused
        # while the user is still looking at the form, rather than after a run
        # has been created and every expansion has failed identically.
        from openjiuwen.rsi.artifact_rsi.program_opt.program import (
            validate_source,
        )

        ok, reason = validate_source(source)
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
        """
        return await self._drive(request, on_event, resumed=True)

    async def pause(self, task_id: str, on_event: OnEvent | None = None) -> EngineResult:
        """Not implemented: the search has no state between node boundaries.

        `terminate` plus `resume` gets most of the way there — the tree is
        durable and a resumed task continues it — but pausing means stopping
        *and staying resumable under the same task*, and the contract's
        `paused` is a non-terminal status. Returning the code rather than
        pretending, so AgentServer can hide the control.
        """
        return EngineResult(
            task_id=task_id,
            status=self._status_of(task_id),
            final_node_id=None,
            error_code="NOT_IMPLEMENTED",
            error_message="program optimization does not support pause yet; "
                          "terminate and resume continues the same tree",
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
        if flag is not None:
            flag.set()
        state = read_state_file(task_id)
        await emit(on_event, EventStatus(status="terminated"))
        return EngineResult(
            task_id=task_id,
            status="terminated",
            final_node_id=state.best_node_id if state else None,
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
                score=None, baseline=None, usage=None, updated_at="",
                error_code="TASK_NOT_FOUND", error_message="no state for this task",
            )
        return state.to_engine_state()

    def read_report(self, task_id: str) -> EngineReport:
        """The current or final report, with every artifact the task produced."""
        report = read_report_file(task_id)
        if report is None:
            return EngineReport(
                task_id=task_id, status="created", best_node_id=None, usage=None,
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
            # The final artifact is the best node's, which the report names.
            best = report.best_node_id if report else None
            for ref in index:
                if best and ref.node_id == best:
                    return ref
            return index[-1]
        for ref in index:
            if ref.artifact_id == artifact_id:
                return ref
        raise FileNotFoundError(f"artifact {artifact_id} does not belong to task {task_id}")

    # -- the bridge ------------------------------------------------------------

    def _status_of(self, task_id: str) -> str:
        state = read_state_file(task_id)
        return state.status if state else "created"

    async def _drive(
        self,
        request: ArtifactEngineRequest,
        on_event: OnEvent | None,
        *,
        resumed: bool,
    ) -> EngineResult:
        loop = asyncio.get_running_loop()
        run_dir = Path(request.run_dir)
        state = ProgramRunState(
            task_id=request.task_id,
            run_dir=run_dir,
            total_iterations=int(request.max_iterations),
        )

        try:
            endpoint = load_model_endpoint(request.model_config)
            capability = require_sandbox(self._sandbox_backend)
            spec = self._spec_for(request, capability, endpoint, resumed=resumed)
        except (ModelConfigError, SandboxUnavailable, FileNotFoundError, ValueError) as error:
            code = type(error).__name__.replace("Error", "").upper() or "INVALID_REQUEST"
            state.fail(code, str(error))
            await emit(on_event, EventStatus(status="failed"))
            return EngineResult(
                task_id=request.task_id, status="failed", final_node_id=None,
                error_code=code, error_message=str(error),
            )

        stop = threading.Event()
        with self._lock:
            self._stopping[request.task_id] = stop

        # Every event the search emits is projected, persisted, then handed to
        # the caller's loop — in that order, because the contract requires a
        # snapshot to be durable before the event announcing it is delivered.
        def sink(record: dict[str, Any]) -> None:
            for event in state.absorb(record):
                if on_event is None:
                    continue
                future = asyncio.run_coroutine_threadsafe(on_event(event), loop)
                # Waited on, not fired and forgotten: the contract makes the
                # provider carry the queue's back-pressure.
                future.result()

        await emit(on_event, EventStatus(status="running"))
        state.start()

        # Before any budget is spent: score the starting point, then score a copy
        # deliberately made worse. Two numbers that come back the same mean the
        # scoring cannot separate a good candidate from a bad one, and a search
        # on flat terrain is a random walk that looks completely normal from
        # outside -- every event fires, every candidate is recorded, and the run
        # simply reports that it found nothing. Worth a handful of evaluations to
        # turn that into a sentence naming the evaluator.
        try:
            await asyncio.to_thread(run_probe, spec)
        except ProbeError as refusal:
            state.fail("PROBE_REFUSED", str(refusal))
            await emit(on_event, EventStatus(status="failed"))
            return EngineResult(
                task_id=request.task_id, status="failed", final_node_id=None,
                error_code="PROBE_REFUSED", error_message=str(refusal),
            )

        # Imported here, not at module scope: this is the one module on the
        # chain that hard-imports `agentdescent`, and `artifact_rsi/__init__`
        # imports this file eagerly. At module scope it would make the whole
        # `openjiuwen.rsi` package unimportable wherever that wheel is absent.
        from openjiuwen.rsi.artifact_rsi.program_opt.puct_engine import PuctEngine

        engine = PuctEngine(completion_factory=_counting(completion_factory_for(endpoint), state))
        try:
            await asyncio.to_thread(engine.run, spec, sink, stop.is_set)
        except Exception as error:  # noqa: BLE001 - a crash is a failed task, not a crashed server
            state.fail("ENGINE_ERROR", str(error))
            await emit(on_event, EventStatus(status="failed"))
            return EngineResult(
                task_id=request.task_id, status="failed", final_node_id=state.best_node_id,
                error_code="ENGINE_ERROR", error_message=str(error),
            )
        finally:
            with self._lock:
                self._stopping.pop(request.task_id, None)

        state.finish()
        await emit(on_event, EventStatus(status=state.status))
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
        capability: Any,
        endpoint: dict[str, Any],
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

        source = ""
        if request.artifact_path:
            source = Path(request.artifact_path).expanduser().read_text(encoding="utf-8")

        spec = RunSpec(
            search_id=request.task_id,
            algorithm="puct",
            expansions=int(request.max_iterations),
            workers=DEFAULT_WORKERS,
            scorecard=card.get("scorecard", card),
            scorecard_hash=str(card.get("hash") or "sha256:inline"),
            statement=str(card.get("statement") or ""),
            script=str(card.get("script") or ""),
            rubric=str(card.get("rubric") or ""),
            source_material=str(card.get("source_material") or ""),
            packages=_packages_from(card.get("packages")),
            baseline_code=source,
            run_dir=str(run_dir),
            sandbox=capability,
            options=dict(card.get("options") or {}),
            # From the model config rather than the scorecard: both are
            # properties of the model being called, and `RunSpec`'s own
            # defaults are ScienceDiscovery's, not this deployment's. The
            # 16k default in particular is below the floor a reasoning model
            # needs to return anything at all.
            max_tokens_per_call=int(endpoint["max_tokens"]),
            thinking=str(endpoint["thinking"]),
            # The engine takes its model as an injected callable and never reads
            # these; the pre-flight probe builds its own judge and does. One
            # endpoint for both, because a run graded by a different model from
            # the one the probe cleared has not been cleared.
            llm_url=endpoint["endpoint"],
            llm_token=endpoint["token"],
            judge_url=endpoint["endpoint"],
            judge_token=endpoint["token"],
        )
        if not resumed:
            return spec

        nodes, baseline, tokens, sequence = _resume_from(run_dir)
        return replace(
            spec,
            resume_nodes=nodes,
            resume_baseline=baseline,
            resume_tokens=tokens,
            resume_from_sequence=sequence,
        )


def _counting(factory: Any, state: ProgramRunState) -> Any:
    """The same completion factory, with every call counted.

    The engine passes `on_usage=None` at both of its call sites, so there is no
    hook inside it to count from; wrapping what it calls is where the number can
    be had without changing the engine. Locked because a search with more than
    one worker calls the model from several threads at once, and `+= 1` on a
    Python attribute is three bytecodes rather than one.
    """
    counter = threading.Lock()

    def counted(*args: Any, **kwargs: Any) -> Any:
        complete = factory(*args, **kwargs)

        def call(*call_args: Any, **call_kwargs: Any) -> Any:
            with counter:
                state.model_calls += 1
            return complete(*call_args, **call_kwargs)

        return call

    return counted


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


def _resume_from(run_dir: Path) -> tuple[tuple[dict[str, Any], ...], dict[str, float], int, int]:
    """The previous attempt's tree, read back off disk.

    The snapshot the search writes after every expansion is exactly what a
    resume needs, which is why resuming needs nothing from AgentServer beyond
    the same `run_dir`.
    """
    from openjiuwen.rsi.artifact_rsi.program_opt.candidates import TREE_FILE

    path = run_dir / TREE_FILE
    if not path.is_file():
        return (), {}, 0, 0
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    nodes = tuple(snapshot.get("tree") or ())
    return nodes, dict(snapshot.get("baseline") or {}), int(snapshot.get("tokens") or 0), len(nodes)


__all__ = ["DEFAULT_WORKERS", "ProgramArtifactProvider"]
