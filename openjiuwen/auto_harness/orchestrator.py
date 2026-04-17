# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto Harness orchestrator."""

from __future__ import annotations

import logging
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
from openjiuwen.auto_harness.infra.git_operations import (
    GitOperations,
)
from openjiuwen.auto_harness.infra.session_budget import (
    SessionBudgetController,
)
from openjiuwen.auto_harness.infra.worktree_manager import (
    WorktreeManager,
)
from openjiuwen.auto_harness.pipelines import (
    META_EVOLVE_PIPELINE,
    normalize_pipeline_name,
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

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

logger = logging.getLogger(__name__)


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


class AutoHarnessOrchestrator:
    """Session controller and top-level pipeline dispatcher."""

    def __init__(
        self,
        config: AutoHarnessConfig,
        agent: Optional["DeepAgent"] = None,
    ) -> None:
        self.config = config
        self.agent = agent
        self._results: List[CycleResult] = []
        self.paths = config.build_paths()
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

    async def run_session_stream(
        self,
        tasks: Optional[List[OptimizationTask]] = None,
    ) -> AsyncIterator[Any]:
        """Stream session execution as OutputSchema chunks."""
        self._results = []
        self._last_cycle_result = CycleResult()
        self.artifacts = ArtifactStore()
        self.budget.start()
        yield self._msg("会话启动")
        logger.info("Session started")

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
        self.artifacts.put(
            "pipeline_selection",
            selected_pipeline,
        )
        yield self._msg(
            "Session pipeline: "
            f"{selected_pipeline.pipeline_name}"
        )
        async for chunk in self._run_pipeline_stream(
            selected_pipeline.pipeline_name,
        ):
            yield chunk

        logger.info(
            "Session finished: %d tasks executed",
            len(self._results),
        )

    def _select_session_pipeline(
        self,
        tasks: Optional[List[OptimizationTask]] = None,
    ) -> PipelineSelectionArtifact:
        """Choose the session pipeline before any concrete pipeline runs."""
        available = self.pipeline_registry.names()
        if not available:
            raise ValueError(
                "No pipelines registered for auto-harness session"
            )

        explicit = sorted(
            {
                normalize_pipeline_name(task.pipeline_name)
                for task in (tasks or [])
                if task.pipeline_name
            }
        )
        if len(explicit) > 1:
            raise ValueError(
                "Conflicting task pipeline_name values in one session: "
                + ", ".join(explicit)
            )
        if explicit:
            selected = explicit[0]
            reason = "tasks requested explicit pipeline"
        elif len(available) == 1:
            selected = available[0]
            reason = "single registered pipeline"
        elif META_EVOLVE_PIPELINE in available:
            selected = META_EVOLVE_PIPELINE
            reason = "default session pipeline"
        else:
            selected = available[0]
            reason = "fallback to first registered pipeline"

        if selected not in available:
            fallback = (
                META_EVOLVE_PIPELINE
                if META_EVOLVE_PIPELINE in available
                else available[0]
            )
            return PipelineSelectionArtifact(
                pipeline_name=fallback,
                reason=(
                    "requested session pipeline unsupported, "
                    f"fallback to {fallback}"
                ),
                confidence=0.0,
                fallback_pipeline=fallback,
            )

        alternatives = [
            name for name in available if name != selected
        ]
        return PipelineSelectionArtifact(
            pipeline_name=selected,
            reason=reason,
            alternatives=alternatives,
            confidence=1.0,
            fallback_pipeline=selected,
        )

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
        async for chunk in pipeline.stream(ctx):
            yield chunk


def create_auto_harness_orchestrator(
    config: AutoHarnessConfig,
) -> AutoHarnessOrchestrator:
    """Create an orchestrator instance."""
    return AutoHarnessOrchestrator(
        config,
        agent=None,
    )
