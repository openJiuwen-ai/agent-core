# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""AutoHarnessOrchestrator unit tests."""

from __future__ import annotations

import asyncio
import tempfile
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from openjiuwen.auto_harness.orchestrator import (
    AutoHarnessOrchestrator,
)
from openjiuwen.auto_harness.pipelines import (
    EXTENDED_EVOLVE_PIPELINE,
    META_EVOLVE_PIPELINE,
)
from openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.meta_evolve_task_pipeline import (
    PRTaskPipeline,
    prepare_task_runtime,
)
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline import (
    ExtensionTaskPipeline,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    CycleResult,
    ExtensionDesign,
    ExtensionDesignArtifact,
    Gap,
    GapAnalysisArtifact,
    SessionResultsArtifact,
    OptimizationTask,
    StageResult,
    TaskStatus,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)

_ASSESS_STAGE_MOD = (
    "openjiuwen.auto_harness.stages.assess"
)
_PLAN_STAGE_MOD = "openjiuwen.auto_harness.stages.plan"
_LEARNINGS_STAGE_MOD = (
    "openjiuwen.auto_harness.stages.learnings"
)
_TASK_PIPELINE_MOD = (
    "openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.meta_evolve_task_pipeline"
)
_SKILL_SOURCE_MANAGER_MOD = (
    "openjiuwen.auto_harness.infra.skill_source_manager"
)


async def _collect(agen):
    items = []
    async for item in agen:
        items.append(item)
    return items


async def _noop_agen(*args, **kwargs):
    del args, kwargs
    return
    yield  # noqa: RET504


async def _noop_ensure_skill_sources(*args, **kwargs):
    """Mock for ensure_skill_sources that returns empty list."""
    del args, kwargs
    return []


class TestOrchestratorRunSession(
    IsolatedAsyncioTestCase,
):
    def _make_orchestrator(self, tmpdir: str):
        cfg = AutoHarnessConfig(
            data_dir=tmpdir,
            session_budget_secs=3600.0,
            task_timeout_secs=600.0,
        )
        return AutoHarnessOrchestrator(
            cfg, agent=None,
        )

    async def test_orchestrator_infers_agent_from_stream_rail(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                session_budget_secs=3600.0,
                task_timeout_secs=600.0,
            )
            deep_agent = Mock()
            rail = Mock()
            rail._deep_agent = deep_agent

            orch = AutoHarnessOrchestrator(
                cfg,
                stream_rails=[rail],
            )

            assert orch.agent is deep_agent

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    async def test_session_selects_meta_pipeline_before_running(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)

            async def _fake_pipeline_stream(
                pipeline_name,
            ):
                assert pipeline_name == META_EVOLVE_PIPELINE
                assert orch.runtime.selected_pipeline == (
                    META_EVOLVE_PIPELINE
                )
                selection = orch.artifacts.require(
                    "pipeline_selection"
                )
                assert selection.pipeline_name == (
                    META_EVOLVE_PIPELINE
                )
                yield orch._msg("pipeline running")

            orch._run_pipeline_stream = _fake_pipeline_stream
            chunks = await _collect(
                orch.run_session_stream(tasks=None)
            )

            msg_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert (
                f"Session pipeline: {META_EVOLVE_PIPELINE}"
                in msg_chunks
            )

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    async def test_session_respects_extended_pipeline_preference(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                pipeline_preference=EXTENDED_EVOLVE_PIPELINE,
            )
            orch = AutoHarnessOrchestrator(cfg, agent=None)

            async def _fake_pipeline_stream(
                pipeline_name,
            ):
                assert pipeline_name == EXTENDED_EVOLVE_PIPELINE
                yield orch._msg("pipeline running")

            orch._run_pipeline_stream = _fake_pipeline_stream
            chunks = await _collect(
                orch.run_session_stream(tasks=None)
            )

            msg_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert (
                f"Session pipeline: {EXTENDED_EVOLVE_PIPELINE}"
                in msg_chunks
            )

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    @patch(
        f"{_SKILL_SOURCE_MANAGER_MOD}.ensure_skill_sources",
        new=_noop_ensure_skill_sources,
    )
    async def test_extended_assess_and_plan_keep_workspace(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                workspace="/repo/local",
                pipeline_preference=EXTENDED_EVOLVE_PIPELINE,
            )
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )
            original_workspace = cfg.workspace
            seen_workspaces = []

            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_assess_stream(
                _stage, ctx,
            ):
                seen_workspaces.append(
                    ctx.orchestrator.config.workspace
                )
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "# Report"},
                )
                yield StageResult(
                    artifacts={
                        "gap_analysis": GapAnalysisArtifact(
                            gaps=[Gap(id="g1", feature="t1")]
                        )
                    }
                )

            async def _fake_plan_stream(
                _stage, ctx,
            ):
                seen_workspaces.append(
                    ctx.orchestrator.config.workspace
                )
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "design"},
                )
                yield StageResult(
                    artifacts={
                        "extension_design": ExtensionDesignArtifact(
                            designs=[
                                ExtensionDesign(
                                    extension_name="t1",
                                    components=["rail", "tool"],
                                )
                            ]
                        )
                    }
                )

            async def _fake_isolated(_orch, design):
                assert design.extension_name == "t1"
                orch._results.append(
                    CycleResult(success=True),
                )
                return
                yield  # noqa: RET504

            with patch(
                f"{_ASSESS_STAGE_MOD}.ExtendAssessStage.stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.ExtendPlanStage.stream",
                new=_fake_plan_stream,
            ), patch.object(
                ExtensionTaskPipeline,
                "run_build_verify_isolated_stream",
                new=_fake_isolated,
            ):
                await _collect(
                    orch.run_session_stream(tasks=None)
                )

            assert seen_workspaces == [
                original_workspace,
                original_workspace,
            ]
            assert cfg.workspace == original_workspace
            orch.worktree_mgr.cleanup.assert_not_awaited()

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    async def test_direct_tasks_run_assess_and_plan(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()
            called = {"assess": 0, "plan": 0}

            async def _fake_assess_stream(_stage, _ctx):
                called["assess"] += 1
                yield StageResult(artifacts={})

            async def _fake_plan_stream(_stage, _ctx):
                called["plan"] += 1
                from openjiuwen.auto_harness.schema import (
                    TaskPlanArtifact,
                )

                yield StageResult(
                    artifacts={
                        "task_plan": TaskPlanArtifact(
                            tasks=[OptimizationTask(topic="t1")],
                            raw_plan="",
                        )
                    }
                )

            async def _fake_isolated(_orch, task):
                orch._results.append(
                    CycleResult(
                        success=True,
                        summary=task.topic,
                    ),
                )
                yield orch._msg(task.topic)

            with patch(
                f"{_ASSESS_STAGE_MOD}.MetaAssessStage.stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.MetaPlanStage.stream",
                new=_fake_plan_stream,
            ), patch.object(
                PRTaskPipeline,
                "run_isolated_stream",
                new=_fake_isolated,
            ):
                chunks = await _collect(
                    orch.run_session_stream(
                        tasks=[OptimizationTask(topic="t1")]
                    )
                )

            msg_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert "t1" in msg_chunks
            assert called == {"assess": 1, "plan": 1}

    async def test_competitor_signal_selects_extended_pipeline(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)

            selection = orch._select_session_pipeline(
                [
                    OptimizationTask(
                        topic=(
                            "吸收 hermes 的动态能力创建机制"
                        )
                    )
                ]
            )

            assert selection.pipeline_name == (
                EXTENDED_EVOLVE_PIPELINE
            )

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    @patch(
        f"{_SKILL_SOURCE_MANAGER_MOD}.ensure_skill_sources",
        new=_noop_ensure_skill_sources,
    )
    async def test_extended_pipeline_runs_extension_task_pipeline(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)

            async def _fake_isolated(_orch, design):
                assert design.extension_name == "hermes"
                orch._results.append(
                    CycleResult(
                        success=True,
                        summary=design.extension_name,
                    )
                )
                yield orch._msg(
                    f"ext:{design.extension_name}"
                )

            with patch.object(
                ExtensionTaskPipeline,
                "run_build_verify_isolated_stream",
                new=_fake_isolated,
            ):
                chunks = await _collect(
                    orch.run_session_stream(
                        tasks=[
                            OptimizationTask(
                                topic="吸收 hermes"
                            )
                        ]
                    )
                )

            message_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert (
                f"Session pipeline: {EXTENDED_EVOLVE_PIPELINE}"
                in message_chunks
            )
            assert "ext:hermes" in message_chunks
            session_results = orch.artifacts.require(
                "session_results"
            )
            assert isinstance(
                session_results, SessionResultsArtifact
            )
            assert session_results.results[0].summary == "hermes"

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    @patch(
        f"{_SKILL_SOURCE_MANAGER_MOD}.ensure_skill_sources",
        new=_noop_ensure_skill_sources,
    )
    async def test_session_stream_passthroughs_ext_assess_and_plan_chunks(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.config.pipeline_preference = EXTENDED_EVOLVE_PIPELINE
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_assess_stream(
                _stage, ctx,
            ):
                del ctx
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "# streamed assess"},
                )
                yield StageResult(
                    artifacts={
                        "gap_analysis": GapAnalysisArtifact(
                            gaps=[Gap(id="g1", feature="t1")]
                        )
                    }
                )

            async def _fake_plan_stream(
                _stage, ctx,
            ):
                del ctx
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "streamed design"},
                )
                yield StageResult(
                    artifacts={
                        "extension_design": ExtensionDesignArtifact(
                            designs=[
                                ExtensionDesign(
                                    extension_name="t1",
                                    components=["rail", "tool"],
                                )
                            ]
                        )
                    }
                )

            async def _fake_isolated(_orch, design):
                orch._results.append(
                    CycleResult(
                        success=True,
                        summary=design.extension_name,
                    ),
                )
                return
                yield  # noqa: RET504

            with patch(
                f"{_ASSESS_STAGE_MOD}.ExtendAssessStage.stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.ExtendPlanStage.stream",
                new=_fake_plan_stream,
            ), patch.object(
                ExtensionTaskPipeline,
                "run_build_verify_isolated_stream",
                new=_fake_isolated,
            ):
                chunks = await _collect(
                    orch.run_session_stream(tasks=None)
                )

            llm_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "llm_output"
            ]
            message_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert "# streamed assess" in llm_chunks
            assert "streamed design" in llm_chunks
            assert "# streamed assess" not in message_chunks
            assert "streamed design" not in message_chunks

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    @patch(
        f"{_SKILL_SOURCE_MANAGER_MOD}.ensure_skill_sources",
        new=_noop_ensure_skill_sources,
    )
    async def test_extended_pipeline_caps_extension_design_tasks(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.config.pipeline_preference = EXTENDED_EVOLVE_PIPELINE
            orch.config.max_tasks_per_session = 1
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_assess_stream(
                _stage, ctx,
            ):
                del ctx
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "# Report"},
                )
                yield StageResult(
                    artifacts={
                        "gap_analysis": GapAnalysisArtifact(
                            gaps=[
                                Gap(id="g1", feature="t1"),
                                Gap(id="g2", feature="t2"),
                            ]
                        )
                    }
                )

            async def _fake_plan_stream(
                _stage, ctx,
            ):
                del ctx
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "two designs"},
                )
                yield StageResult(
                    artifacts={
                        "extension_design": ExtensionDesignArtifact(
                            designs=[
                                ExtensionDesign(
                                    extension_name="t1",
                                    components=["rail", "tool"],
                                ),
                                ExtensionDesign(
                                    extension_name="t2",
                                    components=["rail", "tool"],
                                ),
                            ]
                        )
                    }
                )

            seen_names = []

            async def _fake_isolated(_orch, design):
                seen_names.append(design.extension_name)
                orch._results.append(
                    CycleResult(
                        success=True,
                        summary=design.extension_name,
                    ),
                )
                return
                yield  # noqa: RET504

            with patch(
                f"{_ASSESS_STAGE_MOD}.ExtendAssessStage.stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.ExtendPlanStage.stream",
                new=_fake_plan_stream,
            ), patch.object(
                ExtensionTaskPipeline,
                "run_build_verify_isolated_stream",
                new=_fake_isolated,
            ):
                chunks = await _collect(
                    orch.run_session_stream(tasks=None)
                )

            assert seen_names == ["t1"]
            message_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert (
                "Session pipeline: extended_evolve_pipeline"
                in message_chunks
            )

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    @patch(
        f"{_SKILL_SOURCE_MANAGER_MOD}.ensure_skill_sources",
        new=_noop_ensure_skill_sources,
    )
    async def test_extended_pipeline_runs_constraints_before_capabilities(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.config.pipeline_preference = EXTENDED_EVOLVE_PIPELINE
            orch.config.max_tasks_per_session = 1
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_assess_stream(_stage, ctx):
                del ctx
                yield StageResult(
                    artifacts={
                        "gap_analysis": GapAnalysisArtifact(
                            gaps=[Gap(id="g1", feature="t1")]
                        )
                    }
                )

            async def _fake_plan_stream(_stage, ctx):
                del ctx
                yield StageResult(
                    artifacts={
                        "extension_design": ExtensionDesignArtifact(
                            designs=[
                                ExtensionDesign(
                                    extension_name="cap_1",
                                    kind="capability",
                                ),
                                ExtensionDesign(
                                    extension_name="guard",
                                    kind="constraint",
                                ),
                                ExtensionDesign(
                                    extension_name="cap_2",
                                    kind="capability",
                                ),
                            ]
                        )
                    }
                )

            seen_names = []

            async def _fake_isolated(_orch, design):
                seen_names.append(design.extension_name)
                orch.record_cycle_result(
                    CycleResult(
                        success=True,
                        summary=design.extension_name,
                    )
                )
                return
                yield  # noqa: RET504

            with patch(
                f"{_ASSESS_STAGE_MOD}.ExtendAssessStage.stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.ExtendPlanStage.stream",
                new=_fake_plan_stream,
            ), patch.object(
                ExtensionTaskPipeline,
                "run_build_verify_isolated_stream",
                new=_fake_isolated,
            ):
                await _collect(
                    orch.run_session_stream(tasks=None)
                )

            assert seen_names == ["guard"]

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    @patch(
        f"{_SKILL_SOURCE_MANAGER_MOD}.ensure_skill_sources",
        new=_noop_ensure_skill_sources,
    )
    async def test_extended_pipeline_skips_failed_constraint_dependencies(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.config.pipeline_preference = EXTENDED_EVOLVE_PIPELINE
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_assess_stream(_stage, ctx):
                del ctx
                yield StageResult(
                    artifacts={
                        "gap_analysis": GapAnalysisArtifact(
                            gaps=[Gap(id="g1", feature="t1")]
                        )
                    }
                )

            async def _fake_plan_stream(_stage, ctx):
                del ctx
                yield StageResult(
                    artifacts={
                        "extension_design": ExtensionDesignArtifact(
                            designs=[
                                ExtensionDesign(
                                    extension_name="guard",
                                    kind="constraint",
                                ),
                                ExtensionDesign(
                                    extension_name="blocked",
                                    depends_on=["guard"],
                                ),
                                ExtensionDesign(
                                    extension_name="free",
                                ),
                            ]
                        )
                    }
                )

            seen_names = []

            async def _fake_isolated(_orch, design):
                seen_names.append(design.extension_name)
                orch.record_cycle_result(
                    CycleResult(
                        success=(
                            design.extension_name != "guard"
                        ),
                        summary=design.extension_name,
                    )
                )
                return
                yield  # noqa: RET504

            with patch(
                f"{_ASSESS_STAGE_MOD}.ExtendAssessStage.stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.ExtendPlanStage.stream",
                new=_fake_plan_stream,
            ), patch.object(
                ExtensionTaskPipeline,
                "run_build_verify_isolated_stream",
                new=_fake_isolated,
            ):
                await _collect(
                    orch.run_session_stream(tasks=None)
                )

            assert seen_names == ["guard", "free"]
            assert [result.summary for result in orch.results] == [
                "guard",
                "free",
                "skipped extension blocked",
            ]

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    @patch(
        f"{_SKILL_SOURCE_MANAGER_MOD}.ensure_skill_sources",
        new=_noop_ensure_skill_sources,
    )
    async def test_extension_design_tasks_are_capped_by_task_limit(self):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.config.pipeline_preference = EXTENDED_EVOLVE_PIPELINE
            orch.config.max_tasks_per_session = 2
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_assess_stream(_stage, ctx):
                del ctx
                yield StageResult(
                    artifacts={
                        "gap_analysis": GapAnalysisArtifact(
                            gaps=[Gap(id="g1", feature="t1")]
                        )
                    }
                )

            async def _fake_plan_stream(_stage, ctx):
                del ctx
                yield StageResult(
                    artifacts={
                        "extension_design": ExtensionDesignArtifact(
                            designs=[
                                ExtensionDesign(
                                    extension_name=f"t{i}",
                                    components=["rail", "tool"],
                                )
                                for i in range(5)
                            ]
                        )
                    }
                )

            async def _fake_isolated(_orch, design):
                orch._results.append(
                    CycleResult(
                        success=True,
                        summary=design.extension_name,
                    ),
                )
                return
                yield  # noqa: RET504

            with patch(
                f"{_ASSESS_STAGE_MOD}.ExtendAssessStage.stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.ExtendPlanStage.stream",
                new=_fake_plan_stream,
            ), patch.object(
                ExtensionTaskPipeline,
                "run_build_verify_isolated_stream",
                new=_fake_isolated,
            ):
                await _collect(
                    orch.run_session_stream(tasks=None)
                )
            assert len(orch.results) == 2

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    @patch(
        f"{_SKILL_SOURCE_MANAGER_MOD}.ensure_skill_sources",
        new=_noop_ensure_skill_sources,
    )
    async def test_extended_pipeline_starts_next_extension_with_partial_budget(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.config.pipeline_preference = EXTENDED_EVOLVE_PIPELINE
            orch.config.max_tasks_per_session = 2
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_assess_stream(_stage, ctx):
                del ctx
                yield StageResult(
                    artifacts={
                        "gap_analysis": GapAnalysisArtifact(
                            gaps=[Gap(id="g1", feature="t1")]
                        )
                    }
                )

            async def _fake_plan_stream(_stage, ctx):
                del ctx
                yield StageResult(
                    artifacts={
                        "extension_design": ExtensionDesignArtifact(
                            designs=[
                                ExtensionDesign(
                                    extension_name="t1",
                                ),
                                ExtensionDesign(
                                    extension_name="t2",
                                ),
                            ]
                        )
                    }
                )

            seen_names = []

            async def _fake_isolated(_orch, design):
                seen_names.append(design.extension_name)
                orch.record_cycle_result(
                    CycleResult(
                        success=True,
                        summary=design.extension_name,
                    )
                )
                if design.extension_name == "t1":
                    assert orch.budget._start is not None
                    orch.budget._start -= 3300.0
                return
                yield  # noqa: RET504

            with patch(
                f"{_ASSESS_STAGE_MOD}.ExtendAssessStage.stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.ExtendPlanStage.stream",
                new=_fake_plan_stream,
            ), patch.object(
                ExtensionTaskPipeline,
                "run_build_verify_isolated_stream",
                new=_fake_isolated,
            ):
                await _collect(
                    orch.run_session_stream(tasks=None)
                )

            assert seen_names == ["t1", "t2"]


class TestTaskPipeline(IsolatedAsyncioTestCase):
    async def test_prepare_task_runtime_creates_task_session_and_fix_agent(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )
            orch.experience_store.search = AsyncMock(
                return_value=[]
            )
            orch.worktree_mgr.prepare = AsyncMock(
                return_value=f"{d}/worktrees/task-1"
            )
            orch.git.set_workspace = Mock()
            orch.ci_gate.set_workspace = Mock()
            orch.git.list_dirty_files = AsyncMock(
                return_value=["openjiuwen/harness/tools/filesystem.py"]
            )

            task_agent = type(
                "_TaskAgent",
                (),
                {"card": object()},
            )()
            fix_agent = object()
            commit_agent = object()
            task_session = object()

            with patch(
                "openjiuwen.auto_harness.agents.create_auto_harness_agent",
                side_effect=[task_agent, fix_agent],
            ) as create_task_agent, patch(
                "openjiuwen.auto_harness.agents.create_commit_agent",
                return_value=commit_agent,
            ) as create_commit_agent, patch(
                "openjiuwen.core.session.agent.create_agent_session",
                return_value=task_session,
            ) as create_agent_session:
                runtime = await prepare_task_runtime(
                    orch,
                    OptimizationTask(topic="task-1"),
                )

            assert runtime.task_agent is task_agent
            assert runtime.fix_agent is fix_agent
            assert runtime.commit_agent is commit_agent
            assert runtime.task_session is task_session
            assert runtime.preexisting_dirty_files == [
                "openjiuwen/harness/tools/filesystem.py"
            ]

            first_call = create_task_agent.call_args_list[0]
            assert (
                first_call.kwargs["workspace_override"]
                == f"{d}/worktrees/task-1"
            )
            assert "enable_task_loop" not in first_call.kwargs
            edit_safety_rail = first_call.kwargs[
                "edit_safety_rail"
            ]
            assert edit_safety_rail is runtime.edit_safety_rail

            second_call = create_task_agent.call_args_list[1]
            assert (
                second_call.kwargs["workspace_override"]
                == f"{d}/worktrees/task-1"
            )
            assert (
                second_call.kwargs["edit_safety_rail"]
                is edit_safety_rail
            )
            assert (
                second_call.kwargs["enable_task_loop"]
                is False
            )
            assert (
                second_call.kwargs["enable_task_planning"]
                is False
            )
            assert (
                second_call.kwargs["enable_progress_repeat"]
                is False
            )

            create_commit_agent.assert_called_once_with(
                orch.config,
                workspace_override=f"{d}/worktrees/task-1",
                extra_rails=orch.stream_rails,
            )
            create_agent_session.assert_called_once_with(
                session_id="auto-harness-task-1",
                card=task_agent.card,
                close_stream_on_post_run=False,
            )

    async def test_timeout_handling(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                task_timeout_secs=0.01,
            )
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            async def _slow_task_stream(
                orchestrator, task,
            ):
                del orchestrator, task
                await asyncio.sleep(10)
                yield  # async generator

            task = OptimizationTask(topic="slow")
            with patch.object(
                PRTaskPipeline,
                "_run_task_stream",
                new=_slow_task_stream,
            ):
                await _collect(
                    PRTaskPipeline.run_isolated_stream(
                        orch,
                        task,
                    )
                )
            assert orch._results[-1].error == "timeout"
            assert task.status == TaskStatus.TIMEOUT

    async def test_exception_handling(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            async def _boom_task_stream(
                orchestrator, task,
            ):
                del orchestrator, task
                raise RuntimeError("kaboom")
                yield  # noqa: RET504

            task = OptimizationTask(topic="boom")
            with patch.object(
                PRTaskPipeline,
                "_run_task_stream",
                new=_boom_task_stream,
            ):
                await _collect(
                    PRTaskPipeline.run_isolated_stream(
                        orch,
                        task,
                    )
                )
            assert "kaboom" in orch._results[-1].error
            assert task.status == TaskStatus.FAILED

    async def test_resolve_task_result_from_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )
            task = OptimizationTask(topic="done")
            orch.artifacts.put(
                "task_result",
                CycleResult(success=True, summary="done"),
                task_id="done",
            )
            result = PRTaskPipeline._resolve_task_result(
                orch, task
            )
            assert result.success is True

    async def test_orchestrator_initializes_task_contexts(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            assert orch.task_contexts == {}
