# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto Harness orchestrator."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, List, Optional

from openjiuwen.auto_harness.artifacts import (
    ArtifactStore,
)
from openjiuwen.auto_harness.contexts import (
    SessionContext,
)
from openjiuwen.auto_harness.experience.experience_store import (
    ExperienceStore,
)
from openjiuwen.auto_harness.infra.ci_gate_runner import (
    CIGateRunner,
)
from openjiuwen.auto_harness.infra.fix_loop import (
    FixLoopController,
)
from openjiuwen.auto_harness.infra.pipeline_selector import (
    choose_session_pipeline,
)
from openjiuwen.auto_harness.infra.git_operations import (
    GitOperations,
)
from openjiuwen.auto_harness.infra.session_budget import (
    SessionBudgetController,
)
from openjiuwen.auto_harness.infra.worktree_manager import (
    WorktreeManager,
)
from openjiuwen.auto_harness.registry import (
    build_pipeline_registry,
    build_stage_registry,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    AutoHarnessRuntimeState,
    CycleResult,
    OptimizationTask,
    PipelineSelectionArtifact,
    ProjectProfile,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)
from openjiuwen.core.common.logging import (
    logger,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.rails.cancellation_rail import (
        CancellationRail,
    )
    from openjiuwen.core.single_agent.rail.base import (
        AgentRail,
    )
    from openjiuwen.harness.deep_agent import DeepAgent


async def _empty_aiter() -> AsyncIterator[Any]:
    """Return an async iterator that yields nothing."""
    for _ in ():
        yield


def _write_debug_artifact(
    runs_dir: str,
    filename: str,
    content: str,
) -> str:
    """Persist phase output for debugging and return the file path."""
    path = Path(runs_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _infer_agent_from_rails(
    stream_rails: Optional[List["AgentRail"]],
) -> Optional["DeepAgent"]:
    """Best-effort DeepAgent inference for integration layers.

    Some callers, including JiuwenClaw, already pass a stream rail that has
    been initialized with the live DeepAgent but do not thread the ``agent=``
    argument through separately. Accept that integration style here so the
    activate stage can still hot-load runtime extensions and emit testing
    guidance after user confirmation.
    """
    for rail in stream_rails or []:
        candidate = getattr(rail, "_deep_agent", None)
        if candidate is None:
            candidate = getattr(rail, "deep_agent", None)
        if candidate is not None:
            return candidate
    return None


class AutoHarnessOrchestrator:
    """Session controller and top-level pipeline dispatcher."""

    def __init__(
        self,
        config: AutoHarnessConfig,
        agent: Optional["DeepAgent"] = None,
        *,
        stream_rails: Optional[List["AgentRail"]] = None,
    ) -> None:
        self.config = config
        self._cancellation_rail: Optional["CancellationRail"] = None
        self.stream_rails: List["AgentRail"] = (
            list(stream_rails) if stream_rails else []
        )
        self.agent = (
            agent
            if agent is not None
            else _infer_agent_from_rails(
                self.stream_rails
            )
        )
        self._results: List[CycleResult] = []
        self.paths = config.build_paths()
        Path(self.paths.runtime_extensions_dir).mkdir(
            parents=True,
            exist_ok=True,
        )
        self.runtime = AutoHarnessRuntimeState(
            current_workspace=config.workspace,
            config_bootstrapped=config.config_bootstrapped,
            suggested_local_repo=config.suggested_local_repo,
        )
        self.project_profile: ProjectProfile = (
            config.build_project_profile()
        )
        self.artifacts = ArtifactStore()
        self.stage_registry = build_stage_registry(config)
        self.pipeline_registry = build_pipeline_registry(
            config,
            stage_registry=self.stage_registry,
        )
        self.experience_store = ExperienceStore(
            config.resolved_experience_dir
        )
        self.budget = SessionBudgetController(
            wall_clock_secs=config.session_budget_secs,
            cost_limit_usd=config.cost_limit_usd,
            task_timeout_secs=config.task_timeout_secs,
        )
        self.fix_loop = FixLoopController(
            phase1_max_retries=(
                config.fix_phase1_max_retries
            ),
            phase2_max_retries=(
                config.fix_phase2_max_retries
            ),
        )
        self.worktree_mgr = WorktreeManager(config)
        self.git = GitOperations(
            workspace="",
            remote=config.git_remote,
            base_branch=config.git_base_branch,
            fork_owner=config.fork_owner,
            upstream_owner=config.upstream_owner,
            upstream_repo=config.upstream_repo,
            gitcode_username=(
                config.resolve_gitcode_username()
            ),
            gitcode_token=(
                config.resolve_gitcode_token()
            ),
            user_name=config.git_user_name,
            user_email=config.git_user_email,
        )
        self.ci_gate = CIGateRunner(
            workspace="",
            config_path=config.ci_gate_config,
            python_executable=(
                config.resolve_ci_gate_python_executable()
            ),
            install_command=(
                config.ci_gate_install_command
            ),
        )
        self._last_cycle_result = CycleResult()
        self.task_contexts: dict[str, SessionContext] = {}
        self._pending_interactions: dict[
            str, asyncio.Future[Any]
        ] = {}
        self._cancelled: bool = False

        # Create and bind CancellationRail for agent-level cancellation
        self._setup_cancellation_rail()

    def _setup_cancellation_rail(self) -> None:
        """Create CancellationRail and add it to stream_rails."""
        # Import here to avoid circular dependency at module level
        from openjiuwen.auto_harness.rails.cancellation_rail import (
            CancellationRail,
        )

        self._cancellation_rail = CancellationRail()
        self._cancellation_rail.bind(self)
        self.stream_rails.append(self._cancellation_rail)
        logger.info(
            "[AutoHarnessOrchestrator] CancellationRail bound and added to stream_rails"
        )

    def cancel(self) -> None:
        """Request the orchestrator to stop execution.

        Called by the service when a cancellation request is received.
        Pipelines check should_cancel at iteration boundaries.
        CancellationRail (in stream_rails) checks at agent callbacks.
        """
        self._cancelled = True
        logger.info(
            "[AutoHarnessOrchestrator] cancellation requested"
        )

    @property
    def should_cancel(self) -> bool:
        """Return True if cancellation was requested.

        Pipelines should check this property at task iteration boundaries,
        similar to budget.should_stop checks.
        """
        return self._cancelled

    @staticmethod
    def _msg(text: str) -> OutputSchema:
        """Construct a message OutputSchema."""
        return OutputSchema(
            type="message",
            index=0,
            payload={"content": text},
        )

    @property
    def results(self) -> list[CycleResult]:
        """Return the latest session results."""
        return list(self._results)

    @property
    def last_cycle_result(self) -> CycleResult:
        """Return the latest task cycle result."""
        return self._last_cycle_result

    def record_cycle_result(
        self, result: CycleResult
    ) -> None:
        """Persist one task cycle result on the orchestrator."""
        self._last_cycle_result = result
        self._results.append(result)

    def message_output(self, text: str) -> OutputSchema:
        """Construct a message OutputSchema."""
        return self._msg(text)

    def create_interaction(
        self,
        interaction_id: str,
    ) -> "asyncio.Future[Any]":
        """Create a pending interaction future for a stage."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending_interactions[interaction_id] = fut
        return fut

    def _dispatch_message(
        self,
        message: dict[str, Any],
    ) -> bool:
        """Internal message dispatcher.

        Routes based on message content:
        - Has ``interaction_id`` → resolve pending interaction
        - Otherwise → reserved for future input types
        """
        interaction_id = message.get("interaction_id")
        if interaction_id:
            return self._resolve_interaction(
                interaction_id, message
            )
        return False

    def _resolve_interaction(
        self,
        interaction_id: str,
        response: Any,
    ) -> bool:
        """Resolve a pending interaction with user response."""
        fut = self._pending_interactions.pop(
            interaction_id, None
        )
        if fut is None or fut.done():
            return False
        fut.set_result(response)
        return True

    def run_session_stream(
        self,
        tasks: Optional[List[OptimizationTask]] = None,
        *,
        message: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        """Unified external API for session execution and interaction.

        When *message* is provided, dispatches it internally
        (e.g. resolves a pending interaction) and returns an
        empty async iterator.

        Otherwise runs the full session pipeline and returns
        an async iterator of OutputSchema chunks.
        """
        if message is not None:
            self._dispatch_message(message)
            return _empty_aiter()
        return self._stream_session_pipeline(tasks)

    async def _stream_session_pipeline(
        self,
        tasks: Optional[List[OptimizationTask]] = None,
    ) -> AsyncIterator[Any]:
        """Run the session pipeline, yielding OutputSchema chunks."""
        started_at = time.monotonic()
        self._results = []
        self._last_cycle_result = CycleResult()
        self.artifacts = ArtifactStore()
        self._cancelled = False  # Reset cancellation state for new session
        self.budget.start()
        yield self._msg("会话启动")
        logger.info(
            "[AutoHarnessOrchestrator] session started: local_repo=%s "
            "repo_url=%s pipeline_preference=%s task_timeout=%.1fs "
            "model_timeout=%.1fs session_budget=%.1fs tasks=%s",
            self.config.local_repo,
            self.config.repo_url,
            self.config.pipeline_preference,
            self.config.task_timeout_secs,
            self.config.model_timeout_secs,
            self.config.session_budget_secs,
            [task.topic for task in tasks or []],
        )

        if tasks is not None:
            self.artifacts.put(
                "input_tasks",
                list(tasks),
            )
        selected_pipeline = self._select_session_pipeline(
            tasks
        )
        self.runtime.selected_pipeline = (
            selected_pipeline.pipeline_name
        )
        logger.info(
            "[AutoHarnessOrchestrator] pipeline selected: pipeline=%s reason=%s confidence=%s",
            selected_pipeline.pipeline_name,
            getattr(selected_pipeline, "reason", ""),
            getattr(selected_pipeline, "confidence", ""),
        )
        self.artifacts.put(
            "pipeline_selection",
            selected_pipeline,
        )
        pipeline_name = selected_pipeline.pipeline_name
        spec = self.pipeline_registry.require(
            pipeline_name
        )
        stages_payload = [
            {"slot": slot, "display_name": dn}
            for slot, dn in spec.pipeline_cls.stage_order
        ]
        yield OutputSchema(
            type="message",
            index=0,
            payload={
                "content": (
                    f"Session pipeline: {pipeline_name}"
                ),
                "pipeline": pipeline_name,
                "stages": stages_payload,
            },
        )
        try:
            async for chunk in self._run_pipeline_stream(
                pipeline_name,
            ):
                yield chunk
        except Exception:
            logger.exception(
                "[AutoHarnessOrchestrator] pipeline exception: pipeline=%s elapsed=%.1fs results=%d",
                pipeline_name,
                time.monotonic() - started_at,
                len(self._results),
            )
            raise

        logger.info(
            "[AutoHarnessOrchestrator] session finished: results=%d elapsed=%.1fs budget_remaining=%.1fs",
            len(self._results),
            time.monotonic() - started_at,
            self.budget.remaining_secs,
        )
        yield OutputSchema(
            type="harness_session_finished",
            index=0,
            payload={
                "pipeline": pipeline_name,
                "status": "success",
                "results_count": len(self._results),
                "is_terminal": True,
            },
        )

    def _select_session_pipeline(
        self,
        tasks: Optional[List[OptimizationTask]] = None,
    ) -> PipelineSelectionArtifact:
        """Choose the session pipeline before any concrete pipeline runs."""
        return choose_session_pipeline(
            tasks=list(tasks or []),
            config=self.config,
            available_pipelines=self.pipeline_registry.names(),
        )

    def ensure_session_runtime_dir(self) -> Path:
        """Return the runtime extension directory for the current session."""
        path = (
            Path(self.paths.runtime_extensions_dir)
            / self.runtime.session_id
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _run_pipeline_stream(
        self,
        pipeline_name: str,
    ) -> AsyncIterator[Any]:
        """Execute a registered top-level pipeline."""
        spec = self.pipeline_registry.require(
            pipeline_name
        )
        pipeline = spec.pipeline_cls()
        ctx = SessionContext(orchestrator=self)
        started_at = time.monotonic()
        logger.info(
            "[AutoHarnessOrchestrator] pipeline start: pipeline=%s class=%s",
            pipeline_name,
            type(pipeline).__name__,
        )
        try:
            async for chunk in pipeline.stream(ctx):
                yield chunk
        except Exception:
            logger.exception(
                "[AutoHarnessOrchestrator] pipeline failed: pipeline=%s elapsed=%.1fs",
                pipeline_name,
                time.monotonic() - started_at,
            )
            raise
        else:
            logger.info(
                "[AutoHarnessOrchestrator] pipeline end: pipeline=%s elapsed=%.1fs results=%d",
                pipeline_name,
                time.monotonic() - started_at,
                len(self._results),
            )


def create_auto_harness_orchestrator(
    config: AutoHarnessConfig,
    *,
    agent: Optional["DeepAgent"] = None,
    stream_rails: Optional[List["AgentRail"]] = None,
) -> AutoHarnessOrchestrator:
    """Create an orchestrator instance."""
    return AutoHarnessOrchestrator(
        config,
        agent=agent,
        stream_rails=stream_rails,
    )
