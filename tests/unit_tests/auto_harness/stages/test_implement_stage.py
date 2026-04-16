# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_implement_stage — implement 阶段消息输出测试。"""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from openjiuwen.auto_harness.rails.edit_safety_rail import (
    EditSafetyRail,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    CommitFacts,
    CycleResult,
    Experience,
    OptimizationTask,
)
from openjiuwen.auto_harness.stages.implement import (
    _iter_ci_gate_messages,
    _start_fix_loop,
    run_in_worktree_stream,
)
from openjiuwen.auto_harness.tools.commit_tool import (
    CommitTool,
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
        ci_result = await ci_runner()
        await agent_fixer(ci_result.errors)

        class _Result:
            success = False
            error_log = ["Phase 1 failed"]

        return _Result()


class _FakeCIGate:
    async def run(self, action="all"):
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


class _PassingCIGate:
    async def run(self, action="all"):
        return {
            "passed": True,
            "gates": [{
                "name": "lint",
                "passed": True,
                "output": "ok",
            }],
            "errors": "",
        }


class _FakeGit:
    def __init__(
        self,
        branch_name: str = "auto-harness/test",
        dirty_files: list[str] | None = None,
    ):
        self.branch_name = branch_name
        self._head = "base123"
        self.push = AsyncMock(
            return_value={"success": True}
        )
        self.create_pr = AsyncMock(
            return_value={
                "success": True,
                "pr_url": "https://gitcode.com/openJiuwen/agent-core/pulls/123",
            }
        )
        self.commit_calls: list[tuple[str, list[str]]] = []
        self._status = {
            "dirty_files": list(dirty_files or []),
            "tracked_modified_files": list(dirty_files or []),
            "untracked_files": [],
            "renamed_files": [],
        }

    async def _git(self, *args):
        return 0, "ok"

    async def current_head(self):
        return self._head

    async def current_branch(self):
        return self.branch_name

    async def collect_status(self):
        return self._status

    async def diff_stat(self, paths=None):
        return " 2 files changed, 4 insertions(+)"

    async def commit(self, message, files):
        self.commit_calls.append((message, list(files)))
        self._head = "commit456"
        return {
            "success": True,
            "commit_sha": self._head,
            "staged_files": list(files),
        }


class _FakeCommitAgent:
    def __init__(
        self,
        commit_tool: CommitTool | None = None,
        commit_files: list[str] | None = None,
        rationale: str = "Update source and tests.",
    ):
        self._commit_tool = commit_tool
        self._commit_files = commit_files or []
        self._rationale = rationale
        self.prompts: list[str] = []

    async def stream(self, payload):
        self.prompts.append(payload["query"])
        if len(self.prompts) == 1:
            yield _msg("agent applied change")
            return

        if self._commit_tool is not None:
            await self._commit_tool.invoke({
                "message": "fix(auto-harness): apply task",
                "files": self._commit_files,
                "rationale": self._rationale,
            })
        yield _msg("agent attempted commit")


class _FakeExperienceStore:
    def __init__(self):
        self.record = AsyncMock()


class TestImplementStageHelpers(
    IsolatedAsyncioTestCase,
):
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
        assert (
            "[修复循环] CI 结果: lint=FAIL"
            in texts
        )
        assert "[修复循环] 第 1 次修复" in texts
        assert (
            "[修复循环] 修复目标:\n"
            "E501 line too long"
        ) in texts
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

    async def test_run_in_worktree_stream_commits_without_push_when_remote_disabled(
        self,
    ):
        config = AutoHarnessConfig(
            git_user_name="auto-harness",
            git_user_email="autoharness@petalmail.com",
            git_remote="",
            fork_owner="auto-harness",
            gitcode_username="auto-harness",
        )
        task = OptimizationTask(
            topic="add tiny comment",
            description="Add one short clarifying comment.",
            files=["openjiuwen/auto_harness/schema.py"],
        )
        git = _FakeGit(
            branch_name="auto-harness/local-only",
            dirty_files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
        )
        commit_facts_ref = [CommitFacts()]
        commit_tool = CommitTool(
            git=git,
            facts_provider=lambda: commit_facts_ref[0],
        )
        agent = _FakeCommitAgent(
            commit_tool=commit_tool,
            commit_files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
        )
        edit_safety_rail = EditSafetyRail()
        edit_safety_rail._edited_files = {
            "openjiuwen/auto_harness/schema.py",
            "tests/unit_tests/auto_harness/test_schema.py",
        }
        experience_store = _FakeExperienceStore()
        result_holder: list[CycleResult] = []

        items = []
        async for item in run_in_worktree_stream(
            config,
            task,
            related=[
                Experience(
                    topic="prior",
                    summary="ok",
                )
            ],
            agent=agent,
            git=git,
            ci_gate=_PassingCIGate(),
            fix_loop=_FakeFixLoop(),
            experience_store=experience_store,
            edit_safety_rail=edit_safety_rail,
            preexisting_dirty_files=[],
            commit_facts_ref=commit_facts_ref,
            commit_tool=commit_tool,
            msg_factory=_msg,
            result_holder=result_holder,
        ):
            items.append(item)

        texts = [
            item.payload["content"] for item in items
        ]
        assert "[4/5] 收集提交事实" in texts
        assert "[5/5] 生成提交计划并提交" in texts
        assert "任务完成（本地提交）" in texts
        assert git.commit_calls == [(
            "fix(auto-harness): apply task",
            [
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
        )]
        assert commit_facts_ref[0].allowed_files == [
            "openjiuwen/auto_harness/schema.py",
            "tests/unit_tests/auto_harness/test_schema.py",
        ]
        git.push.assert_not_awaited()
        git.create_pr.assert_not_awaited()
        experience_store.record.assert_awaited_once()
        recorded = (
            experience_store.record.await_args.args[0]
        )
        assert recorded.type.value == "optimization"
        assert recorded.pr_url == ""
        assert task.status.value == "success"
        assert result_holder == [
            CycleResult(success=True, pr_url="")
        ]

    async def test_run_in_worktree_stream_pushes_and_creates_pr_after_commit(
        self,
    ):
        config = AutoHarnessConfig(
            git_remote="autoharness",
            git_user_name="auto-harness",
            git_user_email="autoharness@petalmail.com",
            fork_owner="auto-harness",
            gitcode_username="auto-harness",
        )
        task = OptimizationTask(
            topic="clarify schema test",
            description="Adapt source and old verify-related test.",
            files=["openjiuwen/auto_harness/schema.py"],
        )
        git = _FakeGit(
            branch_name="auto-harness/clarify-schema-test",
            dirty_files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_existing_schema.py",
            ],
        )
        commit_facts_ref = [CommitFacts()]
        commit_tool = CommitTool(
            git=git,
            facts_provider=lambda: commit_facts_ref[0],
        )
        agent = _FakeCommitAgent(
            commit_tool=commit_tool,
            commit_files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_existing_schema.py",
            ],
            rationale="Adapt verify-related legacy test.",
        )
        edit_safety_rail = EditSafetyRail()
        edit_safety_rail._edited_files = {
            "openjiuwen/auto_harness/schema.py",
            "tests/unit_tests/auto_harness/test_existing_schema.py",
        }
        experience_store = _FakeExperienceStore()
        result_holder: list[CycleResult] = []
        ci_gate = _PassingCIGate()
        ci_gate.run = AsyncMock(return_value={
            "passed": True,
            "gates": [{
                "name": "lint",
                "passed": True,
                "output": (
                    "FAILED tests/unit_tests/auto_harness/test_existing_schema.py::test_x"
                ),
            }],
            "errors": (
                "FAILED tests/unit_tests/auto_harness/test_existing_schema.py::test_x"
            ),
        })

        items = []
        async for item in run_in_worktree_stream(
            config,
            task,
            related=[],
            agent=agent,
            git=git,
            ci_gate=ci_gate,
            fix_loop=_FakeFixLoop(),
            experience_store=experience_store,
            edit_safety_rail=edit_safety_rail,
            preexisting_dirty_files=[],
            commit_facts_ref=commit_facts_ref,
            commit_tool=commit_tool,
            msg_factory=_msg,
            result_holder=result_holder,
        ):
            items.append(item)

        texts = [
            item.payload["content"] for item in items
        ]
        assert "[后置] 创建 PR" in texts
        assert (
            "任务完成: https://gitcode.com/openJiuwen/agent-core/pulls/123"
            in texts
        )
        assert commit_facts_ref[0].legacy_related_test_files == [
            "tests/unit_tests/auto_harness/test_existing_schema.py"
        ]
        git.push.assert_awaited_once_with(
            branch_name="auto-harness/clarify-schema-test"
        )
        git.create_pr.assert_awaited_once()
        assert result_holder == [
            CycleResult(
                success=True,
                pr_url="https://gitcode.com/openJiuwen/agent-core/pulls/123",
            )
        ]

    async def test_run_in_worktree_stream_stops_when_commit_guard_rejects_twice(
        self,
    ):
        config = AutoHarnessConfig(
            git_remote="autoharness",
            git_user_name="auto-harness",
            git_user_email="autoharness@petalmail.com",
            fork_owner="auto-harness",
            gitcode_username="auto-harness",
        )
        task = OptimizationTask(
            topic="clarify schema test",
            description="Touch one declared file only.",
            files=["openjiuwen/auto_harness/schema.py"],
        )
        git = _FakeGit(
            branch_name="auto-harness/clarify-schema-test",
            dirty_files=[
                "openjiuwen/auto_harness/schema.py",
                "docs/tmp.md",
            ],
        )
        commit_facts_ref = [CommitFacts()]
        commit_tool = CommitTool(
            git=git,
            facts_provider=lambda: commit_facts_ref[0],
        )
        agent = _FakeCommitAgent(
            commit_tool=commit_tool,
            commit_files=[
                "openjiuwen/auto_harness/schema.py",
                "docs/tmp.md",
            ],
            rationale="Attempt to commit unrelated file.",
        )
        edit_safety_rail = EditSafetyRail()
        edit_safety_rail._edited_files = {
            "openjiuwen/auto_harness/schema.py",
            "docs/tmp.md",
        }
        experience_store = _FakeExperienceStore()
        result_holder: list[CycleResult] = []

        items = []
        async for item in run_in_worktree_stream(
            config,
            task,
            related=[],
            agent=agent,
            git=git,
            ci_gate=_PassingCIGate(),
            fix_loop=_FakeFixLoop(),
            experience_store=experience_store,
            edit_safety_rail=edit_safety_rail,
            preexisting_dirty_files=[],
            commit_facts_ref=commit_facts_ref,
            commit_tool=commit_tool,
            msg_factory=_msg,
            result_holder=result_holder,
        ):
            items.append(item)

        texts = [
            item.payload["content"] for item in items
        ]
        assert any(
            "提交失败，重试一次" in text
            for text in texts
        )
        assert any(
            "提交失败: Commit plan includes files outside allowed scope: docs/tmp.md"
            in text
            for text in texts
        )
        git.push.assert_not_awaited()
        git.create_pr.assert_not_awaited()
        recorded = (
            experience_store.record.await_args.args[0]
        )
        assert recorded.type.value == "failure"
        assert task.status.value == "failed"
        assert result_holder == [
            CycleResult(
                success=False,
                error="Commit plan includes files outside allowed scope: docs/tmp.md",
            )
        ]
