# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto Harness 编排器 — 会话级任务调度。

瘦身后只负责：
- session 生命周期管理
- budget 检查
- 阶段调度（委托给 stages/）
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    List,
    Optional,
)

from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
    parse_tasks,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    CycleResult,
    Experience,
    ExperienceType,
    OptimizationTask,
    TaskStatus,
)
from openjiuwen.auto_harness.infra.session_budget import (
    SessionBudgetController,
)
from openjiuwen.auto_harness.infra.fix_loop import (
    FixLoopController,
)
from openjiuwen.auto_harness.experience.experience_store import (
    ExperienceStore,
)
from openjiuwen.auto_harness.infra.ci_gate_runner import (
    CIGateRunner,
)
from openjiuwen.auto_harness.infra.git_operations import (
    GitOperations,
)
from openjiuwen.auto_harness.infra.worktree_manager import (
    WorktreeManager,
)
from openjiuwen.auto_harness.stages.assess import (
    run_assess,
    run_assess_stream,
)
from openjiuwen.auto_harness.stages.plan import (
    run_plan_stream,
)
from openjiuwen.auto_harness.stages.implement import (
    run_in_worktree_stream,
)
from openjiuwen.auto_harness.stages.learnings import (
    run_learnings,
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
    """会话级编排器，驱动 assess → plan → execute 循环。

    每个 task 在独立的 git worktree 中执行，完成后清理。

    Args:
        config: Auto Harness 配置。
        agent: 可选的 DeepAgent 实例。
    """

    def __init__(
        self,
        config: AutoHarnessConfig,
        agent: Optional["DeepAgent"] = None,
    ) -> None:
        self.config = config
        self.agent = agent
        self._results: List[CycleResult] = []
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

    @staticmethod
    def _msg(text: str) -> OutputSchema:
        """构造 message 类型的 OutputSchema。"""
        return OutputSchema(
            type="message", index=0,
            payload={"content": text},
        )

    @property
    def results(self) -> list[CycleResult]:
        """Return latest session cycle results."""
        return list(self._results)

    # ----------------------------------------------------------
    # public API
    # ----------------------------------------------------------

    async def run_session(
        self,
        tasks: Optional[List[OptimizationTask]] = None,
    ) -> List[CycleResult]:
        """非流式执行（兼容接口）。"""
        async for _ in self.run_session_stream(tasks):
            pass
        return self._results

    async def run_session_stream(
        self,
        tasks: Optional[List[OptimizationTask]] = None,
    ) -> AsyncIterator[Any]:
        """流式执行，yield OutputSchema chunks。"""
        self._results = []
        self.budget.start()
        yield self._msg("会话启动")
        logger.info("Session started")

        if tasks is None:
            async for item in (
                self._run_assess_and_plan_stream()
            ):
                if isinstance(item, list):
                    tasks = item
                else:
                    yield item
            if not tasks:
                return

        capped = tasks[
            : self.config.max_tasks_per_session
        ]

        for task in capped:
            if self.budget.should_stop:
                logger.warning(
                    "Budget exhausted, "
                    "skipping remaining"
                )
                break
            if not self.budget.check_task_budget():
                logger.warning(
                    "Insufficient budget "
                    "for next task"
                )
                break

            async for chunk in (
                self._run_task_isolated_stream(task)
            ):
                yield chunk

        # Phase D: Learnings
        yield self._msg(
            "[Phase D] 反思与经验记录..."
        )
        async for chunk in run_learnings(
            self.config,
            self._results,
            self.experience_store,
        ):
            yield chunk

        logger.info(
            "Session finished: %d/%d tasks executed",
            len(self._results),
            len(capped),
        )

    async def _run_assess_and_plan_stream(
        self,
    ) -> AsyncIterator[Any]:
        """Run phases A1/A2 against a fresh remote snapshot."""
        original_workspace = self.config.workspace
        assess_wt = await self.worktree_mgr.prepare_readonly_snapshot(
            label="assess",
        )
        self.config.workspace = assess_wt
        try:
            yield self._msg(
                "[Phase A1] 评估当前状态..."
            )
            assessment = ""
            async for chunk in run_assess_stream(
                self.config, self.experience_store,
            ):
                yield chunk
                assessment += extract_text(chunk)

            if not assessment:
                assessment = await run_assess(
                    self.config,
                    self.experience_store,
                )
            if assessment.strip():
                _write_debug_artifact(
                    self.config.runs_dir,
                    "latest_assessment.md",
                    assessment,
                )

            logger.info(
                "Assessment complete (%d chars)",
                len(assessment),
            )

            yield self._msg(
                "[Phase A2] 制定优化计划..."
            )
            plan_text = ""
            async for chunk in run_plan_stream(
                self.config,
                assessment,
                self.experience_store,
            ):
                yield chunk
                plan_text += extract_text(chunk)
            if plan_text.strip():
                plan_path = _write_debug_artifact(
                    self.config.runs_dir,
                    "latest_plan.md",
                    plan_text,
                )
                yield self._msg(
                    f"规划原始输出已保存: {plan_path}"
                )
            tasks = parse_tasks(plan_text)

            if not tasks:
                yield self._msg(
                    "规划阶段未生成任务，session 结束"
                )
                logger.info(
                    "No tasks generated, session ends"
                )
                return

            yield tasks
        finally:
            self.config.workspace = original_workspace
            await self.worktree_mgr.cleanup(assess_wt)

    # ----------------------------------------------------------
    # task isolation
    # ----------------------------------------------------------

    async def _run_task_isolated_stream(
        self, task: OptimizationTask,
    ) -> AsyncIterator[Any]:
        """在超时保护下执行单个任务。"""
        task.status = TaskStatus.RUNNING
        logger.info("Task started: %s", task.topic)

        result: CycleResult
        try:
            queue: asyncio.Queue[Any] = (
                asyncio.Queue()
            )
            sentinel = object()

            async def _producer() -> CycleResult:
                try:
                    async for chunk in (
                        self._run_cycle_stream(task)
                    ):
                        await queue.put(chunk)
                    return self._last_cycle_result
                finally:
                    await queue.put(sentinel)

            producer_task = asyncio.create_task(
                asyncio.wait_for(
                    _producer(),
                    timeout=(
                        self.config.task_timeout_secs
                    ),
                )
            )

            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                yield item

            result = await producer_task

        except asyncio.TimeoutError:
            task.status = TaskStatus.TIMEOUT
            logger.error(
                "Task timed out: %s", task.topic,
            )
            await self.experience_store.record(Experience(
                type=ExperienceType.FAILURE,
                topic=task.topic,
                summary="task timeout",
                outcome="timeout",
            ))
            result = CycleResult(
                error="timeout",
                error_log="Task exceeded timeout",
            )
        except Exception as exc:
            task.status = TaskStatus.FAILED
            logger.exception(
                "Task failed: %s", task.topic,
            )
            await self.experience_store.record(Experience(
                type=ExperienceType.FAILURE,
                topic=task.topic,
                summary=str(exc)[:200],
                outcome="exception",
            ))
            result = CycleResult(
                error=str(exc)[:200],
                error_log=str(exc),
            )

        self._results.append(result)

    # ----------------------------------------------------------
    # cycle execution
    # ----------------------------------------------------------

    async def _run_cycle_stream(
        self, task: OptimizationTask,
    ) -> AsyncIterator[Any]:
        """单个 task 的完整执行循环。"""
        from openjiuwen.auto_harness.agent import (
            create_auto_harness_agent,
            create_commit_agent,
        )
        from openjiuwen.auto_harness.rails.edit_safety_rail import (
            EditSafetyRail,
        )

        related = await self.experience_store.search(
            task.topic,
        )
        wt_path = await self.worktree_mgr.prepare(
            task.topic,
        )
        self.git.set_workspace(wt_path)
        self.ci_gate.set_workspace(wt_path)
        edit_safety_rail = EditSafetyRail()
        edit_safety_rail.reset()
        preexisting_dirty_files = await self.git.list_dirty_files()
        task_agent = create_auto_harness_agent(
            self.config,
            workspace_override=wt_path,
            edit_safety_rail=edit_safety_rail,
        )
        commit_agent = create_commit_agent(
            self.config,
            workspace_override=wt_path,
        )

        yield self._msg(
            f"任务准备就绪: {task.topic}"
        )

        result_holder: List[CycleResult] = []
        try:
            async for chunk in (
                run_in_worktree_stream(
                    self.config,
                    task,
                    related,
                    agent=task_agent,
                    commit_agent=commit_agent,
                    git=self.git,
                    ci_gate=self.ci_gate,
                    fix_loop=self.fix_loop,
                    experience_store=self.experience_store,
                    edit_safety_rail=edit_safety_rail,
                    preexisting_dirty_files=preexisting_dirty_files,
                    msg_factory=self._msg,
                    result_holder=result_holder,
                )
            ):
                yield chunk
        finally:
            await self.worktree_mgr.cleanup(wt_path)

        self._last_cycle_result = (
            result_holder[0]
            if result_holder
            else CycleResult()
        )


def create_auto_harness_orchestrator(
    config: AutoHarnessConfig,
) -> AutoHarnessOrchestrator:
    """创建 orchestrator 实例。"""
    return AutoHarnessOrchestrator(
        config, agent=None,
    )
