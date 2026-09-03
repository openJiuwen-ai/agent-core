"""Drives the paper-improvement tree: repeatedly seeds and runs one full
ManagerRuntime pass per node, scores the result against the current best
node's stored score (see judge.py), and persists the resulting
RsiTreeNode. See docs/paper_tree_orchestrator_design.md "Node lifecycle".

Deliberately does not import or modify anything in
`auto_research/modules/` or `auto_research/pipeline/` beyond calling the
already-public `ManagerRuntime(...).arun(...)` and reading already-public
`common/workspace.py` path helpers against each node's own run_id.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.env import load_project_dotenv
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    paper_output_path,
    paper_scoring_dir,
    paper_tex_path,
    set_project_root,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.config.settings import load_config
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import TerminalReport
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.agent import ReflectionAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.manager import ManagerRuntime
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.judge import PaperScore, score_paper
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.schemas import (
    ArtifactRef,
    EventNode,
    EventProgress,
    EventStatus,
    NodeStageEvent,
    OnEvent,
    PaperNodeExtra,
    PaperTaskState,
    RsiChange,
    RsiTreeNode,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.seed import NodeSeed, build_node_seed
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.storage import TaskStorage

# Anchored to the paper_opt package dir (two levels up from this file:
# tree_provider -> auto_research -> paper_opt), not the process's current
# working directory -- a bare "configs/pipeline.default.yaml" only resolved
# when the caller's cwd happened to be paper_opt/, which broke every caller
# that isn't (e.g. `pytest` run from the repo root).
_PAPER_OPT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = str(_PAPER_OPT_ROOT / "configs" / "pipeline.default.yaml")


def _node_id(task_id: str, round_index: int) -> str:
    return f"artifact:{task_id}:node:{round_index}"


def _root_node_id(task_id: str) -> str:
    return f"artifact:{task_id}:root"


def _has_paper(run_id: str) -> bool:
    return paper_tex_path(run_id).exists() or paper_output_path(run_id).exists()


def _artifact_ref_for_node(node_id: str, run_id: str) -> ArtifactRef | None:
    if not _has_paper(run_id):
        return None
    pdf = paper_output_path(run_id)
    tex = paper_tex_path(run_id)
    primary = pdf if pdf.exists() else tex
    sha256 = hashlib.sha256(primary.read_bytes()).hexdigest()
    return ArtifactRef(
        artifact_id=f"A-paper:{node_id}",
        node_id=node_id,
        name=primary.name,
        kind="paper_snapshot",
        path=str(primary),
        sha256=sha256,
        download_url=None,
    )


def _node_run_id(node: RsiTreeNode | None) -> str | None:
    if node is None:
        return None
    extra = node.paper_extra
    return extra.node_run_id if extra else None


def _node_score(node: RsiTreeNode | None) -> PaperScore | None:
    """The node's stored absolute score (see judge.py), if it has one.
    `None` for the root, a failed node, or any node scored before this
    field existed."""
    if node is None:
        return None
    extra = node.paper_extra
    if extra is None or extra.score_overall is None:
        return None
    return PaperScore(overall=extra.score_overall, breakdown=dict(extra.score_breakdown))


class PaperTreeOrchestrator:
    """Owns one task's tree of paper-improvement attempts. One instance per
    active `task_id`. See docs/paper_tree_orchestrator_design.md."""

    def __init__(
        self,
        *,
        task_id: str,
        run_dir: str,
        max_iterations: int,
        optimization_instruction: str | None,
        artifact_path: str | None,
        model: Any = None,
        config_path: str = DEFAULT_CONFIG_PATH,
        on_event: OnEvent | None = None,
    ) -> None:
        self.task_id = task_id
        self.storage = TaskStorage(run_dir)
        self.max_iterations = max_iterations
        self.optimization_instruction = optimization_instruction
        self.artifact_path = artifact_path
        # AgentServer-resolved openjiuwen.core.foundation.llm.Model
        # instance (ArtifactEngineRequest.model). Currently only threaded
        # into paper scoring (see _build_node) -- NOT yet into the
        # six-module ManagerRuntime pipeline, since only 3 of its 6 module
        # agents (manager/experiment_design/topic_survey) have a `model=`
        # injection seam today; reflection/reporting/code_implementation
        # don't, and ManagerRuntime itself has no direct pass-through
        # parameter (see docs/agent_core_rsi_migration_risks.md's model
        # verification notes). `None` is a legitimate value here -- every
        # downstream consumer that accepts it self-resolves its own model
        # from `config` instead.
        self.model = model
        self.config_path = config_path
        # Loaded once for the task's lifetime -- reused for both the
        # per-node ManagerRuntime call and paper scoring, instead of
        # re-reading the same YAML on every node.
        self.config = load_config(config_path)
        self.on_event = on_event
        self._task: asyncio.Task | None = None
        self._cancelled = False

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> PaperTaskState:
        state = self.storage.load_task_state()
        if state is None:
            state = PaperTaskState(
                task_id=self.task_id,
                run_dir=str(self.storage.run_dir),
                status="running",
                max_iterations=self.max_iterations,
                optimization_instruction=self.optimization_instruction,
                artifact_path=self.artifact_path,
            )
            self._ensure_root_node(state)
        else:
            state.status = "running"
        self.storage.save_task_state(state)
        await self._emit(EventStatus(status="running"))
        self._task = asyncio.create_task(self._run_loop())
        return state

    def _ensure_root_node(self, state: PaperTaskState) -> None:
        # NOTE: the uploaded-paper case (artifact_path set) is not actually
        # ingested into a comparable paper artifact yet — seed.py's
        # `build_prior_paper_prompt` only reads *parent nodes'* own compiled
        # papers, not the root's raw upload. Root is treated as score-less
        # regardless, so round 1 always auto-adopts (see `_node_score`/
        # scoring-skip logic in `_build_node`).
        has_upload = bool(self.artifact_path)
        root = RsiTreeNode(
            node_id=_root_node_id(self.task_id),
            iteration=0,
            parent_id=None,
            type="root",
            adopted=True,
            summary=(
                "Uploaded starting paper (ingestion not yet wired in)."
                if has_upload
                else "No starting paper; first node writes from scratch."
            ),
            extra={
                "paper": PaperNodeExtra(
                    logical_kind="root",
                    round_index=0,
                    attempt=1,
                    outcome="success",
                    node_run_id=None,
                ).model_dump(mode="json")
            },
        )
        self.storage.append_node(root)
        # Only the internal frontier pointer -- root is never a reporting
        # node and must not be visible as the public best_node_id (see
        # schemas.py::PaperTaskState field comments).
        state.frontier_node_id = root.node_id

    async def terminate(self) -> None:
        self._cancelled = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for node in self._finalize_pending_nodes(
            reason="task terminated while this reporting attempt was in progress",
            failure_class="terminated",
        ):
            await self._emit(EventNode(node=node))
        state = self.storage.load_task_state()
        if state is not None:
            state.status = "terminated"
            self.storage.save_task_state(state)
        await self._emit(EventStatus(status="terminated"))

    # -- main loop ----------------------------------------------------------
    async def _run_loop(self) -> None:
        state = self.storage.load_task_state()
        assert state is not None
        try:
            while state.node_count < self.max_iterations and not self._cancelled:
                await self._run_one_node(state)
            if not self._cancelled:
                state.status = "completed"
                self.storage.save_task_state(state)
                await self._emit(EventStatus(status="completed"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- must not crash the loop silently
            for node in self._finalize_pending_nodes(
                reason=f"task crashed: {exc}",
                failure_class="crashed",
            ):
                await self._emit(EventNode(node=node))
            state.status = "failed"
            state.error_message = str(exc)
            self.storage.save_task_state(state)
            await self._emit(EventStatus(status="failed"))

    async def _run_one_node(self, state: PaperTaskState) -> None:
        round_index = state.node_count + 1
        # Assigned up front (not inside _build_node) so NodeStageEvent has a
        # stable node_ref to attach to while the node is still in flight —
        # see docs/paper_tree_orchestrator_design.md's NodeStageEvent note.
        node_id = _node_id(self.task_id, round_index)
        frontier = self._frontier_node(state)
        attempt = state.attempts_since_last_adoption + 1
        parent_id = frontier.node_id if frontier else None

        seed = build_node_seed(
            task_id=self.task_id,
            round_index=round_index,
            optimization_instruction=self.optimization_instruction,
            retry_reason=state.last_reason,
            parent_run_id=_node_run_id(frontier),
        )

        # Persist a placeholder before any NodeStageEvent references
        # node_id, so it always resolves to a real tree.json entry (per
        # docs/autoresearch_module-endpoint.md §5.2's "persist before
        # emit" rule and NodeStageEvent's "locate the same node by
        # node_ref" contract). _build_node's final result later overwrites
        # this same node_id via storage.append_node's upsert semantics.
        placeholder = RsiTreeNode(
            node_id=node_id,
            iteration=round_index,
            parent_id=parent_id,
            type="reporting",
            adopted=False,
            summary="Reporting attempt in progress.",
            extra={
                "paper": PaperNodeExtra(
                    logical_kind="reporting",
                    round_index=round_index,
                    attempt=attempt,
                    input_node_id=parent_id,
                    retry_of_node_id=parent_id,
                    outcome="pending",
                    node_run_id=seed.run_id,
                ).model_dump(mode="json")
            },
        )
        self.storage.append_node(placeholder)
        await self._emit(EventNode(node=placeholder))

        await self._emit(
            NodeStageEvent(
                node_ref=node_id,
                stage={"id": "pipeline_run", "name": "Running research pipeline"},
            )
        )
        terminal = await self._run_manager(seed)

        # Only the two stages we can actually observe from outside
        # ManagerRuntime today — see docs/paper_tree_orchestrator_design.md
        # "NodeStageEvent granularity" note. Finer-grained per-module stages
        # would need tailing manager/events.jsonl from a background task,
        # deliberately deferred. Scoring happens whenever a paper exists at
        # all (even with no frontier score to compare against yet) — see
        # _build_node: the candidate's own score must still be computed and
        # stored so the *next* round has something to compare against.
        if _has_paper(seed.run_id):
            await self._emit(
                NodeStageEvent(
                    node_ref=node_id,
                    stage={"id": "score", "name": "Scoring paper"},
                )
            )

        node = await self._build_node(
            node_id=node_id,
            round_index=round_index,
            attempt=attempt,
            parent=frontier,
            node_run_id=seed.run_id,
            terminal=terminal,
        )

        self.storage.append_node(node)
        state.node_count = round_index
        if node.snapshot_artifact_id:
            ref = _artifact_ref_for_node(node.node_id, seed.run_id)
            if ref is not None:
                self.storage.register_artifact(ref)
        if node.adopted:
            state.best_node_id = node.node_id
            state.frontier_node_id = node.node_id
            state.attempts_since_last_adoption = 0
            state.last_reason = None
        else:
            state.attempts_since_last_adoption = attempt
            state.last_reason = node.reason
        self.storage.save_task_state(state)

        await self._emit(EventNode(node=node))
        await self._emit(
            EventProgress(
                iteration=state.node_count,
                total_iterations=self.max_iterations,
                score=None,
                baseline=None,
                usage=None,
            )
        )

    async def _run_manager(self, seed: NodeSeed) -> TerminalReport:
        try:
            # Every workspace_dir(run_id)-derived path the six-module
            # pipeline writes to (survey/design/code/execution/reflection/
            # reporting/manager state) must land under *this task's*
            # caller-assigned run_dir, not some global/auto-detected repo
            # root -- see docs/agent_core_rsi_migration_risks.md Risk 2.
            # Re-set on every call (not just once at task start) so this
            # task's node stays correct even if something else in the
            # process changed the global root in between -- cheap
            # self-healing given _PROJECT_ROOT is still shared mutable
            # state, not truly per-task (see that same doc's concurrency
            # caveat: this is not safe for two *different* tasks running
            # concurrently in one process).
            set_project_root(self.storage.run_dir)
            load_project_dotenv()
            reflection = None
            if (self.config.get("manager") or {}).get("modules", {}).get("reflection", False):
                reflection = ReflectionAgent(self.config)
            runtime = ManagerRuntime(self.config, reflection=reflection)
            return await runtime.arun(
                topic=seed.topic,
                research_paths=seed.research_paths or None,
                run_id=seed.run_id,
                objective=seed.objective,
                constraints=seed.constraints or None,
            )
        except Exception as exc:  # noqa: BLE001 -- defensive: arun() itself already
            # turns internal failures into a TerminalReport; this only
            # covers construction-time/unexpected failures outside that,
            # which must still become a failed node, not crash the tree loop.
            return TerminalReport(
                status="failed",
                run_id=seed.run_id,
                failure_reason=str(exc),
                summary=f"orchestrator: unexpected exception running node: {exc}",
            )

    async def _build_node(
        self,
        *,
        node_id: str,
        round_index: int,
        attempt: int,
        parent: RsiTreeNode | None,
        node_run_id: str,
        terminal: TerminalReport,
    ) -> RsiTreeNode:
        parent_id = parent.node_id if parent else None

        if not _has_paper(node_run_id):
            return RsiTreeNode(
                node_id=node_id,
                iteration=round_index,
                parent_id=parent_id,
                type="reporting",
                adopted=False,
                reason=(
                    terminal.failure_reason
                    or terminal.abort_reason
                    or terminal.summary
                    or "no paper produced"
                ),
                failure_class=f"pipeline_{terminal.status}",
                extra={
                    "paper": PaperNodeExtra(
                        logical_kind="rejected",
                        round_index=round_index,
                        attempt=attempt,
                        input_node_id=parent_id,
                        retry_of_node_id=parent_id,
                        outcome="failed",
                        node_run_id=node_run_id,
                    ).model_dump(mode="json")
                },
            )

        ref = _artifact_ref_for_node(node_id, node_run_id)
        changes = [
            RsiChange(
                operation="generate",
                function="reporting",
                target="paper/",
                summary="Generated a new paper version.",
            )
        ]

        # Score the candidate unconditionally (not just when there's a
        # parent score to compare against) -- the *next* round needs this
        # node's score as its own comparison baseline once/if this node
        # gets adopted.
        try:
            candidate_score = await score_paper(
                tex_path=str(paper_tex_path(node_run_id)),
                output_dir=str(paper_scoring_dir(node_run_id)),
                config=self.config,
                model=self.model,
            )
        except Exception as exc:  # noqa: BLE001 -- a broken/unavailable scorer
            # must never silently let an unvetted paper win a comparison.
            # The paper still gets an artifact ref (it genuinely exists),
            # but this node can't be adopted or trusted as a future
            # comparison baseline (score_overall stays unset below).
            return RsiTreeNode(
                node_id=node_id,
                iteration=round_index,
                parent_id=parent_id,
                type="reporting",
                adopted=False,
                summary=terminal.summary or None,
                snapshot_artifact_id=ref.artifact_id if ref else None,
                reason=str(exc),
                failure_class="scoring_error",
                changes=changes,
                extra={
                    "paper": PaperNodeExtra(
                        logical_kind="rejected",
                        round_index=round_index,
                        attempt=attempt,
                        input_node_id=parent_id,
                        retry_of_node_id=parent_id,
                        outcome="rejected",
                        artifacts=[ref] if ref else [],
                        node_run_id=node_run_id,
                    ).model_dump(mode="json")
                },
            )

        parent_score = _node_score(parent)

        if parent_score is None:
            # Nothing to compare against yet (root has no paper, or this is
            # the first successful node) -- same "first candidate always
            # becomes the baseline" rule the design doc specifies.
            adopted, reason, failure_class = True, None, None
        elif candidate_score.overall > parent_score.overall:
            # Strictly greater, not >=: a tied score is not evidence of an
            # actual improvement, so ties reject rather than adopt -- avoids
            # the frontier churning on noise. Revisit once the real scorer
            # exists and score deltas are meaningful.
            adopted = True
            reason = (
                f"score {candidate_score.overall:g} > "
                f"parent score {parent_score.overall:g}. {candidate_score.reason}"
            ).strip()
            failure_class = None
        else:
            adopted = False
            reason = (
                f"score {candidate_score.overall:g} did not exceed "
                f"parent score {parent_score.overall:g}. {candidate_score.reason}"
            ).strip()
            failure_class = "rejected_by_score"

        return RsiTreeNode(
            node_id=node_id,
            iteration=round_index,
            parent_id=parent_id,
            type="reporting",
            adopted=adopted,
            summary=terminal.summary or None,
            snapshot_artifact_id=ref.artifact_id if ref else None,
            reason=reason,
            failure_class=failure_class,
            changes=changes,
            extra={
                "paper": PaperNodeExtra(
                    logical_kind="adopted" if adopted else "rejected",
                    round_index=round_index,
                    attempt=attempt,
                    input_node_id=parent_id,
                    retry_of_node_id=parent_id,
                    outcome="success" if adopted else "rejected",
                    artifacts=[ref] if ref else [],
                    node_run_id=node_run_id,
                    score_overall=candidate_score.overall,
                    score_breakdown=candidate_score.breakdown,
                ).model_dump(mode="json")
            },
        )

    # -- helpers ------------------------------------------------------------
    def _frontier_node(self, state: PaperTaskState) -> RsiTreeNode | None:
        if not state.frontier_node_id:
            return None
        for node in self.storage.load_tree():
            if node.node_id == state.frontier_node_id:
                return node
        return None

    def _finalize_pending_nodes(self, *, reason: str, failure_class: str) -> list[RsiTreeNode]:
        """Rewrite any node still at outcome="pending" (the NodeStageEvent
        placeholder _run_one_node persists before running a round -- see
        that method) into a terminal rejected node. A cancelled terminate()
        or a crash caught by _run_loop's except Exception can otherwise
        leave the in-flight round's tree.json entry stuck at "pending"
        forever, since only a successful _build_node overwrites it. Callers
        must persist this before their own terminal EventStatus and emit a
        matching EventNode for each returned node."""
        finalized: list[RsiTreeNode] = []
        for node in self.storage.load_tree():
            extra = node.paper_extra
            if extra is None or extra.outcome != "pending":
                continue
            updated_extra = extra.model_copy(update={"outcome": "rejected"})
            updated_node = node.model_copy(
                update={
                    "reason": reason,
                    "failure_class": failure_class,
                    "extra": {"paper": updated_extra.model_dump(mode="json")},
                }
            )
            self.storage.append_node(updated_node)
            finalized.append(updated_node)
        return finalized

    async def _emit(self, event) -> None:
        if self.on_event is not None:
            await self.on_event(event)
