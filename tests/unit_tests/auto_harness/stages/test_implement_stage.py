# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Implement-stage helper tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from openjiuwen.auto_harness.contexts import (
    TaskContext,
    TaskRuntime,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    Experience,
    ExperienceType,
    OptimizationTask,
    StageResult,
)
from openjiuwen.auto_harness.stages.implement import (
    ImplementStage,
    _build_prompt_debug_stats,
    _extract_repo_edit_candidates,
    run_implement_stream,
)
from openjiuwen.auto_harness.stages.verify import (
    _iter_ci_gate_messages,
    _start_fix_loop,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)


def _msg(text: str) -> OutputSchema:
    return OutputSchema(
        type="message",
        index=0,
        payload={"content": text},
    )


class _FakeFixLoop:
    async def run(
        self,
        ci_runner,
        agent_fixer,
        evaluator=None,
    ):
        del evaluator
        await ci_runner()
        await agent_fixer("E501 line too long")

        class _Result:
            success = False
            error_log = ["Phase 1 failed"]

        return _Result()


class _PassThroughFixLoop:
    async def run(
        self,
        ci_runner,
        agent_fixer,
        evaluator=None,
    ):
        del evaluator
        ci_result = await ci_runner()
        await agent_fixer(ci_result.errors)

        class _Result:
            success = False
            error_log = ["Phase 1 failed"]

        return _Result()


class _FakeCIGate:
    async def run(self, action="all"):
        del action
        return {
            "passed": False,
            "gates": [{
                "name": "lint",
                "passed": False,
                "output": "E501 line too long",
            }],
            "errors": "[lint]\nE501 line too long",
        }


class _WarningOnlyDetailCIGate:
    async def run(self, action="all"):
        del action
        return {
            "passed": False,
            "gates": [{
                "name": "test",
                "passed": False,
                "output": (
                    "=================================== FAILURES ===================================\n"
                    "E   AssertionError: expected value\n"
                    "\n"
                    "=========================== short test summary info ============================\n"
                    "FAILED tests/unit_tests/core/foundation/tool/test_api_param_mapper.py::test_x"
                ),
            }],
            "errors": (
                "[test]\n"
                "=================================== FAILURES ===================================\n"
                "E   AssertionError: expected value\n"
                "\n"
                "=========================== short test summary info ============================\n"
                "FAILED tests/unit_tests/core/foundation/tool/test_api_param_mapper.py::test_x"
            ),
        }


class _PromptCapturingAgent:
    def __init__(self) -> None:
        self.query = ""
        self.session = None

    async def stream(
        self,
        inputs,
        session=None,
        stream_modes=None,
    ):
        del stream_modes
        self.query = inputs["query"]
        self.session = session
        yield _msg("ok")


class _StreamingAgent:
    def __init__(self) -> None:
        self.session = None

    async def stream(
        self,
        inputs,
        session=None,
        stream_modes=None,
    ):
        del inputs
        del stream_modes
        self.session = session
        yield OutputSchema(
            type="llm_output",
            index=0,
            payload={
                "content": "## 任务完成总结\n\n实现已完成。",
            },
        )


class _TaskFailedStreamingAgent:
    async def stream(
        self,
        inputs,
        session=None,
        stream_modes=None,
    ):
        del inputs
        del session
        del stream_modes
        yield OutputSchema(
            type="controller_output",
            index=0,
            payload={
                "type": "task_failed",
                "data": [
                    {
                        "type": "text",
                        "text": (
                            "[181001] model call failed, "
                            "reason: openAI API async stream error: ReadTimeout"
                        ),
                    }
                ],
                "metadata": {"task_id": "t1"},
            },
        )


class _FakeSession:
    def __init__(self) -> None:
        self.pre_run_inputs = None
        self.pre_run_calls = 0
        self.post_run_calls = 0

    async def pre_run(self, **kwargs):
        self.pre_run_calls += 1
        self.pre_run_inputs = kwargs.get("inputs")
        return self

    async def post_run(self):
        self.post_run_calls += 1
        return self


class _FakeEditSafetyRail:
    def edited_files(self) -> list[str]:
        return ["openjiuwen/harness/cli/cli.py"]


class _FakeGit:
    def __init__(
        self,
        *,
        status_text: str = "",
        diff_files: list[str] | None = None,
    ) -> None:
        self._status_text = status_text
        self._diff_files = diff_files or []

    async def status_porcelain(self) -> str:
        return self._status_text

    async def diff_name_only(
        self,
        revision: str = "HEAD",
    ) -> list[str]:
        del revision
        return list(self._diff_files)


class TestImplementStageHelpers(
    IsolatedAsyncioTestCase,
):
    async def test_run_implement_stream_includes_edit_scope(self):
        agent = _PromptCapturingAgent()
        task = OptimizationTask(
            topic="restrict-scope",
            description="只允许改 harness/core 与配套文件",
            files=["openjiuwen/harness/cli/ui/renderer.py"],
        )
        related = [
            Experience(
                type=ExperienceType.INSIGHT,
                topic="scope",
                summary="keep changes inside harness/core",
            )
        ]

        chunks = [
            chunk
            async for chunk in run_implement_stream(
                agent,
                task,
                related,
            )
        ]

        assert len(chunks) == 1
        assert "`openjiuwen/harness/**`" in agent.query
        assert "`openjiuwen/core/**`" in agent.query
        assert "`openjiuwen/harness/cli/README.md`" in agent.query
        assert "`tests/**`" in agent.query
        assert "`examples/**`" in agent.query
        assert "`docs/en/`" in agent.query
        assert "`docs/zh/`" in agent.query
        assert "范围外" in agent.query
        assert "默认直接开始实施修改" in agent.query
        assert "不要等待人工确认" in agent.query
        assert "是否需要我开始实现" in agent.query
        assert agent.session is None

    def test_build_prompt_debug_stats(self):
        prompt = "line1\nline2"

        stats = _build_prompt_debug_stats(prompt)

        assert stats == {
            "chars": 11,
            "lines": 2,
            "bytes": 11,
        }

    async def test_run_implement_stream_manages_session_lifecycle(
        self,
    ):
        agent = _PromptCapturingAgent()
        session = _FakeSession()
        task = OptimizationTask(topic="session-task")

        chunks = [
            chunk
            async for chunk in run_implement_stream(
                agent,
                task,
                [],
                session=session,
            )
        ]

        assert len(chunks) == 1
        assert agent.session is session
        assert session.pre_run_calls == 1
        assert session.pre_run_inputs == {
            "query": agent.query
        }
        assert session.post_run_calls == 1

    async def test_run_implement_stream_uses_supplied_prompt(
        self,
    ):
        agent = _PromptCapturingAgent()
        task = OptimizationTask(topic="session-task")

        chunks = [
            chunk
            async for chunk in run_implement_stream(
                agent,
                task,
                [],
                prompt="custom prompt",
            )
        ]

        assert len(chunks) == 1
        assert agent.query == "custom prompt"

    async def test_stage_emits_ready_message_before_agent_summary(
        self,
    ):
        stage = ImplementStage()
        agent = _StreamingAgent()
        ctx = TaskContext(
            orchestrator=SimpleNamespace(
                artifacts=SimpleNamespace(),
                git=_FakeGit(
                    status_text=(
                        " M openjiuwen/harness/cli/cli.py"
                    )
                ),
            ),
            task=OptimizationTask(topic="补全 auto-harness 文档"),
            runtime=TaskRuntime(
                related=[],
                wt_path="/tmp/worktree",
                edit_safety_rail=_FakeEditSafetyRail(),
                preexisting_dirty_files=[],
                task_agent=agent,
                commit_agent=None,
            ),
        )

        items = [
            item
            async for item in stage.stream(ctx)
        ]

        assert items[0].type == "message"
        assert (
            items[0].payload["content"]
            == "任务准备就绪: 补全 auto-harness 文档"
        )
        assert items[1].type == "message"
        assert (
            items[1].payload["content"]
            == "[1/5] 执行代码修改"
        )
        assert items[2].type == "llm_output"
        assert (
            "任务完成总结"
            in items[2].payload["content"]
        )
        assert agent.session is None
        assert isinstance(items[3], StageResult)
        assert items[3].messages == []

    async def test_stage_passes_runtime_task_session_to_agent(
        self,
    ):
        stage = ImplementStage()
        agent = _StreamingAgent()
        session = _FakeSession()
        ctx = TaskContext(
            orchestrator=SimpleNamespace(
                artifacts=SimpleNamespace(),
                git=_FakeGit(
                    status_text=(
                        " M openjiuwen/harness/tools/filesystem.py"
                    )
                ),
            ),
            task=OptimizationTask(topic="session-aware implement"),
            runtime=TaskRuntime(
                related=[],
                wt_path="/tmp/worktree",
                edit_safety_rail=_FakeEditSafetyRail(),
                preexisting_dirty_files=[],
                task_agent=agent,
                commit_agent=None,
                task_session=session,
            ),
        )

        items = [
            item
            async for item in stage.stream(ctx)
        ]

        assert isinstance(items[-1], StageResult)
        assert items[-1].status == "success"
        assert agent.session is session
        assert session.pre_run_calls == 1
        assert session.post_run_calls == 1

    async def test_stage_uses_git_changes_even_if_rail_is_empty(
        self,
    ):
        stage = ImplementStage()
        ctx = TaskContext(
            orchestrator=SimpleNamespace(
                artifacts=SimpleNamespace(),
                git=_FakeGit(
                    status_text="",
                    diff_files=[
                        "openjiuwen/harness/tools/filesystem.py"
                    ],
                ),
            ),
            task=OptimizationTask(topic="git-diff 检测"),
            runtime=TaskRuntime(
                related=[],
                wt_path="/tmp/worktree",
                edit_safety_rail=SimpleNamespace(
                    edited_files=lambda: []
                ),
                preexisting_dirty_files=[],
                task_agent=_StreamingAgent(),
                commit_agent=None,
            ),
        )

        items = [
            item
            async for item in stage.stream(ctx)
        ]

        assert isinstance(items[-1], StageResult)
        assert items[-1].status == "success"
        assert items[-1].artifacts[
            "code_change"
        ].edited_files == [
            "openjiuwen/harness/tools/filesystem.py"
        ]

    async def test_stage_fails_with_task_failed_error_before_git_check(
        self,
    ):
        stage = ImplementStage()
        ctx = TaskContext(
            orchestrator=SimpleNamespace(
                artifacts=SimpleNamespace(),
                config=AutoHarnessConfig(),
                git=_FakeGit(
                    status_text=(
                        " M openjiuwen/harness/tools/filesystem.py"
                    ),
                    diff_files=[
                        "openjiuwen/harness/tools/filesystem.py"
                    ],
                ),
            ),
            task=OptimizationTask(topic="模型超时"),
            runtime=TaskRuntime(
                related=[],
                wt_path="/tmp/worktree",
                edit_safety_rail=SimpleNamespace(
                    edited_files=lambda: [
                        "openjiuwen/harness/tools/filesystem.py"
                    ]
                ),
                preexisting_dirty_files=[],
                task_agent=_TaskFailedStreamingAgent(),
                commit_agent=None,
            ),
        )

        items = [
            item
            async for item in stage.stream(ctx)
        ]

        assert isinstance(items[-1], StageResult)
        assert items[-1].status == "failed"
        assert "ReadTimeout" in items[-1].error
        assert "Implement model call failed after" in items[-1].error
        assert "prompt_chars=" in items[-1].error
        assert "model_timeout_secs=300.0" in items[-1].error
        assert (
            "No allowed repo file was changed"
            not in items[-1].error
        )
        assert ctx.task.status.value == "failed"

    async def test_stage_fails_when_git_reports_no_repo_edits(
        self,
    ):
        stage = ImplementStage()
        ctx = TaskContext(
            orchestrator=SimpleNamespace(
                artifacts=SimpleNamespace(),
                git=_FakeGit(),
            ),
            task=OptimizationTask(topic="空跑实现"),
            runtime=TaskRuntime(
                related=[],
                wt_path="/tmp/worktree",
                edit_safety_rail=SimpleNamespace(
                    edited_files=lambda: []
                ),
                preexisting_dirty_files=[],
                task_agent=_StreamingAgent(),
                commit_agent=None,
            ),
        )

        items = [
            item
            async for item in stage.stream(ctx)
        ]

        assert isinstance(items[-1], StageResult)
        assert items[-1].status == "failed"
        assert (
            "No allowed repo file was changed"
            in items[-1].error
        )
        assert ctx.task.status.value == "failed"

    def test_extract_repo_edit_candidates_tolerates_stripped_status_prefix(
        self,
    ):
        edited_files = _extract_repo_edit_candidates(
            status_text="M openjiuwen/harness/tools/filesystem.py",
            diff_files=[],
        )

        assert edited_files == [
            "openjiuwen/harness/tools/filesystem.py"
        ]

    async def test_stage_ignores_preexisting_dirty_files(
        self,
    ):
        stage = ImplementStage()
        ctx = TaskContext(
            orchestrator=SimpleNamespace(
                artifacts=SimpleNamespace(),
                git=_FakeGit(
                    status_text=(
                        " M openjiuwen/harness/tools/filesystem.py"
                    ),
                ),
            ),
            task=OptimizationTask(topic="预脏文件不算本轮改动"),
            runtime=TaskRuntime(
                related=[],
                wt_path="/tmp/worktree",
                edit_safety_rail=SimpleNamespace(
                    edited_files=lambda: []
                ),
                preexisting_dirty_files=[
                    "openjiuwen/harness/tools/filesystem.py"
                ],
                task_agent=_StreamingAgent(),
                commit_agent=None,
            ),
        )

        items = [
            item
            async for item in stage.stream(ctx)
        ]

        assert isinstance(items[-1], StageResult)
        assert items[-1].status == "failed"
        assert (
            "No allowed repo file was changed"
            in items[-1].error
        )

    def test_iter_ci_gate_messages_contains_summary_and_excerpt(
        self,
    ):
        messages = _iter_ci_gate_messages({
            "passed": False,
            "gates": [
                {
                    "name": "lint",
                    "passed": False,
                    "output": "E501 line too long",
                },
                {
                    "name": "test",
                    "passed": True,
                    "output": "ok",
                },
            ],
            "errors": "",
        })
        assert messages[0] == "CI 结果: lint=FAIL, test=PASS"
        assert messages[1] == "[lint] E501 line too long"

    async def test_start_fix_loop_emits_progress_messages(
        self,
    ):
        task, queue, done = _start_fix_loop(
            config=AutoHarnessConfig(),
            task=OptimizationTask(topic="fix lint"),
            agent=None,
            git=object(),
            ci_gate=_FakeCIGate(),
            fix_loop_ctrl=_FakeFixLoop(),
            msg_factory=_msg,
        )

        items = []
        while not done.is_set() or not queue.empty():
            items.append(await queue.get())

        ok, result = await task
        assert ok is False
        assert result.error_log == ["Phase 1 failed"]

        texts = [
            item.payload["content"]
            for item in items
        ]
        assert "[修复循环] 第 1 次重跑 CI" in texts
        assert "[修复循环] CI 结果: lint=FAIL" in texts
        assert "[修复循环] 第 1 次修复" in texts
        assert "[修复循环] 修复目标:\nE501 line too long" in texts
        assert "[修复循环] 修复耗尽" in texts

    async def test_start_fix_loop_omits_warning_summary_in_fix_target(
        self,
    ):
        task, queue, done = _start_fix_loop(
            config=AutoHarnessConfig(),
            task=OptimizationTask(topic="fix pytest failure"),
            agent=None,
            git=object(),
            ci_gate=_WarningOnlyDetailCIGate(),
            fix_loop_ctrl=_PassThroughFixLoop(),
            msg_factory=_msg,
        )

        items = []
        while not done.is_set() or not queue.empty():
            items.append(await queue.get())

        await task
        texts = [
            item.payload["content"]
            for item in items
        ]
        joined = "\n".join(texts)
        assert "AssertionError: expected value" in joined
        assert "PydanticDeprecatedSince20" not in joined
