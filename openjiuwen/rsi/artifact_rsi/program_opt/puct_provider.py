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

from openjiuwen.rsi.artifact_rsi.program_opt.engine import RunSpec
from openjiuwen.rsi.artifact_rsi.program_opt.probe import ProbeError, run_probe
from openjiuwen.rsi.artifact_rsi.program_opt.program import DEFAULT_ENTRYPOINT, bundle
from openjiuwen.rsi.artifact_rsi.program_opt.runtime import (
    ModelConfigError,
    SandboxUnavailable,
    SearchEngineUnavailable,
    completion_factory_from_model,
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


class PuctProgramArtifactProvider:
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
            errors.append({"code": "ARTIFACT_EMPTY", "message": "the program is empty"})

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
        # What it buys here is that a seed which no evaluator could call is
        # refused while the user is still looking at the form.
        #
        # Note the entrypoint name is hard-coded to `train_and_predict`, which
        # is the contract of the task this algorithm was ported from. A
        # `custom_script` run's real contract is whatever its evaluator calls,
        # so this is stricter than the search itself is.
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
                best_node_id=None, score=None, baseline=None, usage=None, updated_at="",
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
            engine_type = _load_engine()
            if request.model is None:
                # The contract routes the model resource through AgentServer:
                # it resolves `model_refs["optimizer"]` and hands over a live
                # instance. Nothing here may fall back to reading a config —
                # that is exactly the client-building the contract forbids.
                raise ModelConfigError(
                    "an initialized Model instance is required: AgentServer resolves "
                    "model_refs['optimizer'] via Runner.resource_mgr.get_model"
                )
            capability = require_sandbox(self._sandbox_backend)
            spec = self._spec_for(request, capability, resumed=resumed)
        except (ModelConfigError, SandboxUnavailable, SearchEngineUnavailable,
                FileNotFoundError, ValueError) as error:
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

        engine = engine_type(completion_factory=_counting(
            completion_factory_from_model(request.model, loop), state))
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
            baseline_code=bundle(files),
            entrypoint=entrypoint,
            run_dir=str(run_dir),
            sandbox=capability,
            options=dict(card.get("options") or {}),
            # From the scorecard when it says, else `RunSpec`'s default. The
            # model's own request config is opaque here — the contract hands
            # over an initialized instance, not its settings — so the per-run
            # ceiling is the task's to raise. A reasoning model needs more
            # than the 16k default to return anything at all; a scorecard for
            # one should say so.
            max_tokens_per_call=int(card.get("max_tokens_per_call")
                                    or RunSpec.max_tokens_per_call),
            thinking=str(card.get("thinking") or ""),
            # Deliberately empty. The engine's mutation calls go through the
            # injected `request.model`; these fields exist for a judge built
            # from an endpoint, and with an injected instance there is none.
            # An `llm_judge` scorecard is refused by `_judge_spec` with a
            # sentence saying the search was given no judge — refused, not
            # silently graded by something this provider made up.
            llm_url="",
            llm_token="",
            judge_url="",
            judge_token="",
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


def _load_engine() -> Any:
    """`PuctEngine`, imported at call time rather than at module scope.

    `puct_engine` is the one module on the chain that hard-imports
    `agentdescent`, and `artifact_rsi/__init__` imports this file eagerly --
    at module scope a missing wheel would make the whole `openjiuwen.rsi`
    package unimportable rather than making one provider unusable.

    The extra is optional, so "not installed" is an ordinary configuration
    fault and deserves the sentence that fixes it, not a `ModuleNotFoundError`
    raised three files deep in the vendored search.
    """
    try:
        from openjiuwen.rsi.artifact_rsi.program_opt.puct_engine import PuctEngine
    except ImportError as error:
        raise SearchEngineUnavailable(
            "program optimization needs the search engine: "
            "pip install 'openjiuwen[program-opt]' "
            f"({error})"
        ) from error
    return PuctEngine


def _seed_files(path: Path) -> dict[str, str]:
    """The starting program as `{relpath: text}`, from a file or a directory.

    A single file is placed at the default entrypoint, which is how every
    one-file run has always worked. A directory keeps its own layout, so a seed
    that is already a package is not renamed into this provider's conventions
    just to be optimized.
    """
    if path.is_dir():
        from agentdescent.filetree import load_tree

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


__all__ = ["DEFAULT_WORKERS", "PuctProgramArtifactProvider"]
