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
import contextlib
import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.env import load_project_dotenv
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    paper_output_path,
    paper_scoring_dir,
    paper_tex_path,
    set_project_root,
    to_project_relative,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.config.settings import load_config
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import TerminalReport
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_preprocess import (
    LatexValidationError,
    PaperPreprocessAgent,
    PaperPreprocessInput,
)
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
    RsiUsage,
    RsiUsageTokens,
    RsiTreeNode,
    friendly_failure_reason,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.seed import NodeSeed, build_node_seed
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.storage import TaskStorage
from openjiuwen.rsi.usage import ModelUsageObserver, set_usage_node

# Anchored to the paper_opt package dir (two levels up from this file:
# tree_provider -> auto_research -> paper_opt), not the process's current
# working directory -- a bare "configs/pipeline.default.yaml" only resolved
# when the caller's cwd happened to be paper_opt/, which broke every caller
# that isn't (e.g. `pytest` run from the repo root).
_PAPER_OPT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = str(_PAPER_OPT_ROOT / "configs" / "pipeline.default.yaml")
_MISSING = object()
_MODEL_ENV_LOCK = asyncio.Lock()
logger = logging.getLogger(__name__)


def _usage_counter(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _model_value(value: Any, name: str) -> Any:
    if value is None:
        return None
    raw = getattr(value, name, None)
    return getattr(raw, "value", raw)


def _configure_for_model(config: dict[str, Any], model: Any) -> dict[str, Any]:
    """Overlay the AgentServer-resolved model on pipeline configuration."""
    if model is None:
        return config
    client = getattr(model, "model_client_config", None)
    request_config = getattr(model, "model_config", None)
    settings = dict(config.get("openjiuwen") or {})
    provider = _model_value(client, "client_provider")
    model_name = _model_value(request_config, "model_name")
    base_url = _model_value(client, "api_base")
    timeout = _model_value(client, "timeout")
    if provider:
        settings["provider"] = str(provider)
    if model_name:
        settings["model"] = str(model_name)
    if base_url:
        settings["base_url"] = str(base_url)
    if timeout:
        settings["timeout"] = timeout
    config["openjiuwen"] = settings
    return config


@contextlib.asynccontextmanager
async def _temporary_model_environment(
    model: Any,
    *,
    task_id: str | None = None,
) -> AsyncIterator[None]:
    """Expose the resolved model to legacy module and child-process code."""
    client = getattr(model, "model_client_config", None)
    request_config = getattr(model, "model_config", None)
    values = {
        "API_KEY": _model_value(client, "api_key"),
        "API_BASE": _model_value(client, "api_base"),
        "MODEL_PROVIDER": _model_value(client, "client_provider"),
        "MODEL_NAME": _model_value(request_config, "model_name"),
        "MODEL_TIMEOUT": _model_value(client, "timeout"),
    }
    values = {key: str(value) for key, value in values.items() if value not in (None, "")}
    if _MODEL_ENV_LOCK.locked():
        logger.warning(
            "[RSI] paper orchestrator waiting for the process-level model environment lock: task=%s",
            task_id or "<unknown>",
        )
    await _MODEL_ENV_LOCK.acquire()
    try:
        previous: dict[str, object] = {}
        for key, value in values.items():
            previous[key] = os.environ.get(key, _MISSING)
            os.environ[key] = value
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is _MISSING:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(value)
    finally:
        _MODEL_ENV_LOCK.release()


def _node_id(task_id: str, round_index: int) -> str:
    return f"artifact:{task_id}:node:{round_index}"


def _root_node_id(task_id: str) -> str:
    return f"artifact:{task_id}:root"


def _has_paper(run_id: str) -> bool:
    return paper_tex_path(run_id).exists() or paper_output_path(run_id).exists()


def _friendly_pruned_reason(
    *,
    terminal: TerminalReport | None = None,
    failure_class: str | None = None,
    phase: str | None = None,
) -> str:
    """Return a short, user-facing reason for a pruned paper node.

    The manager's failure and abort fields are intentionally used only to
    classify the phase.  Their raw contents can contain exception messages,
    stack details, or model/tool wording that is not suitable for the tree
    card.  Those details remain in the per-node manager workspace/logs.
    """

    if phase == "scoring" or failure_class == "scoring_error":
        return "评估结果未达到预期，已剪枝。"

    context_parts: list[str] = []
    for value in (
        failure_class,
        terminal.status if terminal is not None else None,
        terminal.abort_reason if terminal is not None else None,
        terminal.failure_reason if terminal is not None else None,
        terminal.summary if terminal is not None else None,
    ):
        if value:
            context_parts.append(value)
    context = " ".join(context_parts).lower()

    source_markers = (
        "topic_survey",
        "survey",
        "research",
        "source",
        "download",
        "fetch",
        "retriev",
        "文献",
        "资料",
    )
    for marker in source_markers:
        if marker in context:
            return "资料获取质量不佳，已剪枝。"

    execution_markers = (
        "experiment_execution",
        "execution",
        "experiment",
        "code_implementation",
        "smoke",
        "dataset",
        "run.py",
    )
    for marker in execution_markers:
        if marker in context:
            return "实验验证效果不佳，已剪枝。"

    paper_markers = ("reporting", "latex", "paper", "report", "pdf", "tex")
    if phase == "paper_generation":
        return "论文内容质量未达到要求，已剪枝。"
    for marker in paper_markers:
        if marker in context:
            return "论文内容质量未达到要求，已剪枝。"
    return "当前方案效果未达到要求，已剪枝。"


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


def _display_node(node: RsiTreeNode) -> RsiTreeNode:
    """Provider-facing copy of `node` for EventNode -- swaps `reason` for its
    friendly-text version (see schemas.py::friendly_failure_reason) without
    touching the node persisted to storage or fed into the next round's seed
    prompt via PaperTaskState.last_reason."""
    friendly = friendly_failure_reason(node)
    if friendly is None or friendly == node.reason:
        return node
    return node.model_copy(update={"reason": friendly})


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
    active `task_id`. See docs/paper_tree_orchestrator_design.md.
    """

    def __init__(
        self,
        *,
        task_id: str,
        run_dir: str,
        max_iterations: int,
        optimization_instruction: str | None,
        artifact_path: str | None,
        model: Any = None,
        web_proxy: str | None = None,
        config_path: str = DEFAULT_CONFIG_PATH,
        on_event: OnEvent | None = None,
    ) -> None:
        self.task_id = task_id
        self.storage = TaskStorage(run_dir)
        self.max_iterations = max_iterations
        self.optimization_instruction = optimization_instruction
        self.artifact_path = artifact_path
        self.initial_prompt = ""
        self.initial_research_paths: list[str] = []
        # AgentServer-resolved openjiuwen.core.foundation.llm.Model
        # instance (ArtifactEngineRequest.model), shared by scoring and all
        # model-backed pipeline modules. None retains standalone config/env
        # resolution without changing process-global model credentials.
        self.model = model
        self.web_proxy = str(web_proxy or "").strip() or None
        self.config_path = config_path
        # Loaded once for the task's lifetime -- reused for both the
        # per-node ManagerRuntime call and paper scoring, instead of
        # re-reading the same YAML on every node.
        self.config = load_config(config_path)
        topic_config = dict(self.config.get("topic_survey") or {})
        topic_config["web_proxy"] = self.web_proxy
        # A task proxy changes the network route, not the research workflow.
        # Keep an explicitly configured domestic scope intact, but make the
        # normal paper path global with or without a task-scoped proxy.
        configured_scope = str(topic_config.get("search_scope") or "").strip().lower()
        if configured_scope not in {"domestic", "global"}:
            topic_config["search_scope"] = "global"
        self.config["topic_survey"] = topic_config
        self.on_event = on_event
        self._task: asyncio.Task | None = None
        self._cancelled = False
        self._pause_requested = False
        self._usage_observer: ModelUsageObserver | None = None

    def _current_usage(self) -> RsiUsage | None:
        """Return the public paper-provider usage projection for this run."""
        observer = self._usage_observer
        if observer is None or observer.totals.call_count <= 0:
            return None
        tokens = observer.totals.tokens
        return RsiUsage(
            tokens=RsiUsageTokens(
                input=_usage_counter(tokens.input),
                output=_usage_counter(tokens.output),
                cache_hit=_usage_counter(tokens.cache_hit),
            ),
            cost_estimate=observer.totals.cost_estimate or 0.0,
            call_count=observer.totals.call_count,
        )

    async def _on_usage_event(self, event: Any) -> None:
        """Persist each call's cumulative usage before forwarding the event."""
        if getattr(event, "event_type", None) == "progress.usage":
            state = self.storage.load_task_state()
            usage = self._current_usage()
            if state is not None and usage is not None:
                state.usage = usage
                self.storage.save_task_state(state)
        if self.on_event is not None:
            await self.on_event(event)

    def _stage_input_artifact(self, artifact_path: str | None) -> str | None:
        """Copy the caller's input into this task's durable workspace.

        Every module in a node receives this path rather than the caller's
        original path.  That makes the input immutable from the pipeline's
        point of view and keeps retries/nodes reproducible after the caller's
        temporary upload location disappears.
        """
        if not artifact_path:
            return None
        source = Path(str(artifact_path)).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"uploaded artifact does not exist: {artifact_path!r}")

        run_dir = self.storage.run_dir.resolve()
        snapshot_dir = run_dir / "input" / "paper"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        # A second start/recovery of the same task should reuse the existing
        # task-local snapshot instead of copying it into itself.
        try:
            source.relative_to(snapshot_dir.resolve())
        except ValueError:
            pass
        else:
            return str(source)

        if not source.name:
            raise ValueError(f"uploaded artifact has no usable name: {artifact_path!r}")
        target = snapshot_dir / source.name
        if target.is_symlink() or target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.is_file():
            shutil.copy2(source, target)
        else:
            raise ValueError(f"uploaded artifact is not a regular file or directory: {artifact_path!r}")
        return str(target)

    def _prepare_uploaded_paper_context(self) -> None:
        """Create the manager-facing context and paths for an uploaded paper."""
        self.initial_prompt = ""
        self.initial_research_paths = []
        if not self.artifact_path:
            return

        run_dir = self.storage.run_dir.resolve()
        snapshot = Path(self.artifact_path).resolve()
        relative_snapshot = to_project_relative(snapshot, root=run_dir)
        input_dir = run_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        context_path = input_dir / "paper_context.md"
        context_path_relative = to_project_relative(context_path, root=run_dir)
        artifact_instruction = (
            f"The user's uploaded baseline artifact is available at `{relative_snapshot}` "
            "inside this task workspace. Read and use this task-local snapshot as the "
            "baseline for the modification; do not treat its existing results as new "
            "measurements."
        )

        initial_prompt = "TASK MODE: modify_paper\n\n" + artifact_instruction
        research_paths = [context_path_relative]
        main_tex = snapshot / "main.tex" if snapshot.is_dir() else None
        if main_tex is not None and main_tex.is_file():
            main_relative = to_project_relative(main_tex, root=run_dir)
            try:
                processed = PaperPreprocessAgent().run(PaperPreprocessInput(paper_dir=str(snapshot))).initial_prompt
            except LatexValidationError as exc:
                processed = (
                    "TASK MODE: modify_paper\n\n"
                    f"The staged directory could not be validated as a complete LaTeX paper: {exc}. "
                    f"Inspect `{main_relative}` and the other files under `{relative_snapshot}` directly."
                )
            else:
                # Keep the prompt portable and consistent with the relative
                # resource paths exposed to downstream agents.
                processed = processed.replace(str(main_tex), main_relative)
                processed = f"{processed}\n\n{artifact_instruction}"
            initial_prompt = processed
            # The explicit main.tex path is useful to experiment design even
            # though directory expansion intentionally ignores .tex files.
            research_paths.append(main_relative)
        elif snapshot.is_dir():
            # Directory expansion can still expose supported resources (for
            # example an uploaded PDF plus sidecar notes).
            initial_prompt += f" The directory contains the uploaded paper resources; inspect `{relative_snapshot}`."
            research_paths.append(relative_snapshot)
        else:
            research_paths.append(relative_snapshot)

        context_path.write_text(
            "\n".join(
                [
                    "# Uploaded baseline paper/input",
                    "",
                    f"Task-local snapshot: `{relative_snapshot}`",
                    "",
                    "The snapshot above is the immutable input for this task. It must be "
                    "available to research, design, implementation, execution, and reporting.",
                    "",
                    "## Initial paper context",
                    "",
                    initial_prompt,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.initial_prompt = initial_prompt
        self.initial_research_paths = research_paths

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> PaperTaskState:
        state = self.storage.load_task_state()
        if state is None:
            self.artifact_path = self._stage_input_artifact(self.artifact_path)
        else:
            self.artifact_path = self._stage_input_artifact(state.artifact_path or self.artifact_path)
            state.artifact_path = self.artifact_path
        self._prepare_uploaded_paper_context()
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
        has_upload = bool(self.artifact_path)
        root = RsiTreeNode(
            node_id=_root_node_id(self.task_id),
            iteration=0,
            parent_id=None,
            type="root",
            adopted=True,
            summary=(
                "Uploaded starting paper staged as the task-local baseline input."
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

    def _baseline_tex_path(self) -> Path | None:
        """Return the staged LaTeX entry point when the input is scoreable.

        The paper scorer is intentionally LaTeX-based.  Uploaded PDFs and
        other non-LaTeX inputs still participate in the normal optimization
        flow, but there is no honest baseline score to publish for them.
        """

        if not self.artifact_path:
            return None
        snapshot = Path(self.artifact_path).resolve()
        if snapshot.is_file() and snapshot.suffix.lower() == ".tex":
            return snapshot
        if snapshot.is_dir():
            main_tex = snapshot / "main.tex"
            if main_tex.is_file():
                return main_tex
        return None

    async def _ensure_baseline_score(self, state: PaperTaskState) -> None:
        """Score an uploaded baseline once before generating candidates.

        The score is persisted on both task state and the root node.  This
        keeps the public progress/report projections consistent and lets the
        existing tree UI show the baseline value without exposing the input
        paper as a downloadable/rendered result.
        """

        if state.baseline is not None or not self.artifact_path:
            return
        tex_path = self._baseline_tex_path()
        if tex_path is None:
            logger.info(
                "uploaded paper has no scoreable LaTeX entry point task=%s path=%s",
                self.task_id,
                self.artifact_path,
            )
            return

        try:
            set_usage_node(_root_node_id(self.task_id))
            baseline_score = await score_paper(
                tex_path=str(tex_path),
                output_dir=str(self.storage.run_dir / "baseline_scoring"),
                config=self.config,
                model=self.model,
            )
        except Exception as exc:  # noqa: BLE001 -- baseline must not block optimization
            logger.warning(
                "paper baseline scoring did not complete task=%s path=%s: %s",
                self.task_id,
                tex_path,
                exc,
            )
            return

        state.baseline = baseline_score.overall
        state.score = baseline_score.overall
        state.usage = self._current_usage()
        root = next(
            (node for node in self.storage.load_tree() if node.node_id == _root_node_id(self.task_id)),
            None,
        )
        if root is not None:
            extra = root.paper_extra
            if extra is not None:
                updated_extra = extra.model_copy(
                    update={
                        "score_overall": baseline_score.overall,
                        "score_breakdown": baseline_score.breakdown,
                    }
                )
                root = root.model_copy(
                    update={
                        "score": baseline_score.overall,
                        "extra": {"paper": updated_extra.model_dump(mode="json")},
                    }
                )
                self.storage.append_node(root)
                await self._emit(EventNode(node=root))
        self.storage.save_task_state(state)
        await self._emit(
            EventProgress(
                iteration=state.node_count,
                total_iterations=self.max_iterations,
                score=state.score,
                baseline=state.baseline,
                usage=self._current_usage(),
            )
        )

    async def pause(self, on_event: OnEvent | None = None) -> PaperTaskState:
        """Cancel the in-flight node and persist the task as paused.

        ManagerRuntime already turns ``CancelledError`` into a durable manager
        checkpoint.  The tree-level task must make the same transition before
        cancelling its loop so a concurrent worker poll cannot observe a
        running task after the pause request has been accepted.
        """
        state = self.storage.load_task_state()
        if state is None:
            raise RuntimeError("pause requested with no persisted task state")
        if state.status in {"completed", "failed", "paused", "terminated"}:
            return state

        self._pause_requested = True
        state.status = "paused"
        self.storage.save_task_state(state)

        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        state = self.storage.load_task_state() or state
        if state.status not in {"completed", "failed", "terminated"}:
            state.status = "paused"
            self.storage.save_task_state(state)

        event = EventStatus(status="paused")
        # The callback supplied at run() time is the canonical worker sink. A
        # direct provider caller can still receive the event when no run-time
        # sink was registered.
        if self.on_event is not None:
            await self._emit(event)
        elif on_event is not None:
            await on_event(event)
        return state

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
            await self._emit(EventNode(node=_display_node(node)))
        state = self.storage.load_task_state()
        if state is not None:
            state.status = "terminated"
            self.storage.save_task_state(state)
        await self._emit(EventStatus(status="terminated"))

    # -- main loop ----------------------------------------------------------
    async def _run_loop(self) -> None:
        state = self.storage.load_task_state()
        if state is None:
            raise RuntimeError("_run_loop started with no persisted task state")
        observer = ModelUsageObserver(self._on_usage_event)
        self._usage_observer = observer
        try:
            async with observer.observe():
                await observer.bind(state.model_dump(mode="python"), self.storage.run_dir)
                restored_usage = self._current_usage()
                if restored_usage is not None:
                    state.usage = restored_usage
                    self.storage.save_task_state(state)
                await self._run_loop_body(state)
                await observer.finish_pending()
        except Exception as exc:  # noqa: BLE001 -- observation must terminalize the task
            logger.exception("paper usage observation failed task=%s", self.task_id)
            state = self.storage.load_task_state() or state
            if state.status not in {"completed", "failed", "paused", "terminated"}:
                state.status = "failed"
                state.error_message = str(exc)
                self.storage.save_task_state(state)
                await self._emit(EventStatus(status="failed"))
        finally:
            with contextlib.suppress(Exception):
                await observer.finish_pending()
            state = self.storage.load_task_state() or state
            usage = self._current_usage()
            if usage is not None:
                state.usage = usage
            self.storage.save_task_state(state)
            self._usage_observer = None

    async def _run_loop_body(self, state: PaperTaskState) -> None:
        try:
            await self._ensure_baseline_score(state)
            while state.node_count < self.max_iterations and not self._cancelled and not self._pause_requested:
                await self._run_one_node(state)
            if not self._cancelled and not self._pause_requested:
                # Reaching the outer iteration budget is only a successful
                # task completion when at least one usable paper candidate was
                # adopted.  A run whose every candidate was pruned must not
                # look successful merely because the loop ran to its budget.
                if state.node_count > 0 and state.best_node_id is None:
                    state.status = "failed"
                    state.error_message = "未生成可用的论文结果。"
                    self.storage.save_task_state(state)
                    await self._emit(EventStatus(status="failed"))
                    return
                state.status = "completed"
                state.error_message = None
                self.storage.save_task_state(state)
                await self._emit(EventStatus(status="completed"))
            elif self._pause_requested and not self._cancelled:
                state.status = "paused"
                self.storage.save_task_state(state)
        except Exception as exc:  # noqa: BLE001 -- must not crash the loop silently
            if self._pause_requested and not self._cancelled:
                state.status = "paused"
                self.storage.save_task_state(state)
                return
            for node in self._finalize_pending_nodes(
                reason=f"task crashed: {exc}",
                failure_class="crashed",
            ):
                await self._emit(EventNode(node=_display_node(node)))
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
            initial_research_paths=self.initial_research_paths,
            initial_prompt=self.initial_prompt,
            task_mode="modify_paper" if self.artifact_path else "create_new_paper",
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
                stage={"id": "pipeline_run", "name": "正在规划研究流程"},
            )
        )
        terminal = await self._run_manager(seed)

        # ManagerRuntime forwards module starts through on_stage. Scoring happens whenever a paper exists at
        # all (even with no frontier score to compare against yet) — see
        # _build_node: the candidate's own score must still be computed and
        # stored so the *next* round has something to compare against.
        if _has_paper(seed.run_id):
            await self._emit(
                NodeStageEvent(
                    node_ref=node_id,
                    stage={"id": "score", "name": "正在评估论文"},
                )
            )

        try:
            node = await self._build_node(
                node_id=node_id,
                round_index=round_index,
                attempt=attempt,
                parent=frontier,
                node_run_id=seed.run_id,
                terminal=terminal,
            )
        except Exception:  # noqa: BLE001 -- one node must not kill the tree loop
            # Artifact discovery/finalization is part of this candidate's
            # lifecycle.  If it fails after the manager has returned, keep the
            # same node-level fallback semantics and let the next iteration
            # start from the last adopted frontier.
            logger.exception(
                "paper node finalization did not complete task=%s node=%s",
                self.task_id,
                node_id,
            )
            node = self._pruned_node(
                node_id=node_id,
                round_index=round_index,
                attempt=attempt,
                parent_id=parent_id,
                node_run_id=seed.run_id,
                reason=_friendly_pruned_reason(
                    terminal=terminal,
                    failure_class="node_finalization",
                    phase="pipeline",
                ),
                failure_class="node_finalization",
            )

        self.storage.append_node(node)
        state.node_count = round_index
        if node.snapshot_artifact_id:
            ref = self._safe_artifact_ref(node.node_id, seed.run_id)
            if ref is not None:
                self.storage.register_artifact(ref)
        if node.adopted:
            state.best_node_id = node.node_id
            state.frontier_node_id = node.node_id
            node_score = _node_score(node)
            if node_score is not None:
                state.score = node_score.overall
                # An instruction-only task has no uploaded paper to score;
                # its first usable candidate is the comparison baseline.
                # If an uploaded source exists but cannot be ingested, keep
                # baseline unset instead of claiming the candidate is the
                # user's original-paper score.
                if state.baseline is None and not self.artifact_path:
                    state.baseline = node_score.overall
            state.attempts_since_last_adoption = 0
            state.last_reason = None
        else:
            state.attempts_since_last_adoption = attempt
            state.last_reason = node.reason
        if self._pause_requested:
            state.status = "paused"
        self.storage.save_task_state(state)

        await self._emit(EventNode(node=_display_node(node)))
        await self._emit(
            EventProgress(
                iteration=state.node_count,
                total_iterations=self.max_iterations,
                score=state.score,
                baseline=state.baseline,
                usage=self._current_usage(),
            )
        )

        # A terminal non-complete manager result has already consumed the
        # manager/module retry budget for this candidate.  _build_node turns
        # it into a PRUNED node; keep the outer tree search alive so the next
        # iteration can try a fresh candidate from the last adopted frontier.

    async def _run_manager(self, seed: NodeSeed) -> TerminalReport:
        try:
            set_usage_node(seed.run_id)

            async def on_stage(module: str) -> None:
                labels = {
                    "manager": "正在规划下一阶段",
                    "topic_survey": "正在调研文献",
                    "experiment_design": "正在设计实验",
                    "code_implementation": "正在实现代码",
                    "experiment_execution": "正在执行实验",
                    "reflection": "正在分析与反思",
                    "reporting": "正在撰写论文",
                }
                node = next(
                    (n for n in self.storage.load_tree() if _node_run_id(n) == seed.run_id),
                    None,
                )
                if node is not None:
                    await self._emit(
                        NodeStageEvent(
                            node_ref=node.node_id,
                            stage={"id": module, "name": labels.get(module, module)},
                        )
                    )

            async with _temporary_model_environment(self.model, task_id=self.task_id):
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
                self.config = _configure_for_model(self.config, self.model)
                reflection = None
                if (self.config.get("manager") or {}).get("modules", {}).get("reflection", False):
                    reflection = ReflectionAgent(self.config, model=self.model)

                runtime = ManagerRuntime(
                    self.config,
                    model=self.model,
                    artifact_path=self.artifact_path,
                    reflection=reflection,
                    on_stage=on_stage,
                )
                return await runtime.arun(
                    topic=seed.topic,
                    research_paths=seed.research_paths or None,
                    run_id=seed.run_id,
                    objective=seed.objective,
                    constraints=seed.constraints or None,
                    initial_prompt=getattr(seed, "initial_prompt", ""),
                    task_mode=getattr(seed, "task_mode", "create_new_paper"),
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

        # A partial paper must not become the new baseline when the manager
        # did not reach a complete terminal state.  Keep a reference when one
        # exists for diagnostics/download-by-id, but never score or adopt it.
        if terminal.status != "complete":
            ref = self._safe_artifact_ref(node_id, node_run_id)
            return self._pruned_node(
                node_id=node_id,
                round_index=round_index,
                attempt=attempt,
                parent_id=parent_id,
                node_run_id=node_run_id,
                reason=_friendly_pruned_reason(terminal=terminal),
                failure_class=f"pipeline_{terminal.status}",
                ref=ref,
            )

        if not _has_paper(node_run_id):
            return self._pruned_node(
                node_id=node_id,
                round_index=round_index,
                attempt=attempt,
                parent_id=parent_id,
                node_run_id=node_run_id,
                reason=_friendly_pruned_reason(
                    terminal=terminal,
                    phase="paper_generation",
                ),
                failure_class="paper_generation",
            )

        ref = self._safe_artifact_ref(node_id, node_run_id)
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
            set_usage_node(node_id)
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
            logger.warning(
                "paper node scoring did not complete task=%s node=%s: %s",
                self.task_id,
                node_id,
                exc,
            )
            return self._pruned_node(
                node_id=node_id,
                round_index=round_index,
                attempt=attempt,
                parent_id=parent_id,
                node_run_id=node_run_id,
                reason=_friendly_pruned_reason(
                    terminal=terminal,
                    failure_class="scoring_error",
                    phase="scoring",
                ),
                failure_class="scoring_error",
                ref=ref,
                changes=changes,
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
                f"score {candidate_score.overall:g} > parent score {parent_score.overall:g}. {candidate_score.reason}"
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
            score=candidate_score.overall,
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

    @staticmethod
    def _pruned_node(
        *,
        node_id: str,
        round_index: int,
        attempt: int,
        parent_id: str | None,
        node_run_id: str,
        reason: str,
        failure_class: str,
        ref: ArtifactRef | None = None,
        changes: list[RsiChange] | None = None,
    ) -> RsiTreeNode:
        """Build the short, user-facing terminal representation of a node.

        The current frontier is deliberately not changed by this node.  The
        caller persists it and starts the next outer iteration from the last
        adopted candidate.
        """

        return RsiTreeNode(
            node_id=node_id,
            iteration=round_index,
            parent_id=parent_id,
            type="pruned",
            adopted=False,
            snapshot_artifact_id=ref.artifact_id if ref else None,
            reason=reason,
            failure_class=failure_class,
            changes=changes or [],
            extra={
                "paper": PaperNodeExtra(
                    logical_kind="pruned",
                    round_index=round_index,
                    attempt=attempt,
                    input_node_id=parent_id,
                    retry_of_node_id=parent_id,
                    outcome="failed",
                    artifacts=[ref] if ref else [],
                    node_run_id=node_run_id,
                ).model_dump(mode="json")
            },
        )

    def _safe_artifact_ref(self, node_id: str, node_run_id: str) -> ArtifactRef | None:
        """Return a candidate artifact reference without failing node cleanup."""

        if not _has_paper(node_run_id):
            return None
        try:
            return _artifact_ref_for_node(node_id, node_run_id)
        except OSError as exc:
            logger.warning(
                "paper node artifact could not be indexed task=%s node=%s: %s",
                self.task_id,
                node_id,
                exc,
            )
            return None

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
        if isinstance(event, NodeStageEvent):
            for node in self.storage.load_tree():
                if node.node_id == event.node_ref:
                    self.storage.append_node(
                        node.model_copy(
                            update={
                                "summary": event.stage.get("name"),
                                "extra": {**node.extra, "stage": dict(event.stage)},
                            }
                        )
                    )
                    break
        if self.on_event is not None:
            await self.on_event(event)
