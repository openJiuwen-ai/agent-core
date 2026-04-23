# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Publish PR stage tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.auto_harness.artifacts import (
    ArtifactStore,
)
from openjiuwen.auto_harness.contexts import (
    TaskContext,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    CommitArtifact,
    CommitFacts,
    OptimizationTask,
    VerifyReportArtifact,
)
from openjiuwen.auto_harness.stages.publish_pr import (
    PublishPRStage,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)


class _FakeGit:
    def __init__(self):
        self.push = AsyncMock(return_value={"success": True})
        self.create_pr = AsyncMock(
            return_value={
                "success": True,
                "pr_url": "https://gitcode.com/pr/1",
            }
        )


class _FakeDraftAgent:
    async def stream(self, payload):
        assert "任务主题: 修复 PR draft" in payload["query"]
        yield OutputSchema(
            type="llm_output",
            index=0,
            payload={
                "content": (
                    "```json\n"
                    "{\n"
                    '  "title": "fix(harness): 补齐 PR draft",\n'
                    '  "kind": "bug",\n'
                    '  "body": "<!--  Thanks for sending a pull request!  Here are some tips for you:\\n\\n'
                    '1) If this is your first time, please read our contributor guidelines: https://gitcode.com/openJiuwen/community/blob/master/CONTRIBUTING.md\\n\\n'
                    '2) If you want to contribute your code but don\\u0027t know who will review and merge, please add label `openjiuwen-assistant` to the pull request, we will find and do it as soon as possible.\\n'
                    '-->\\n\\n'
                    '**What type of PR is this?**\\n'
                    '<!--\\n'
                    '选择下面一种标签替换下方 `/kind <label>`，可选标签类型有：\\n'
                    '- /kind bug\\n'
                    '- /kind task\\n'
                    '- /kind feature\\n'
                    '- /kind refactor\\n'
                    '- /kind clean_code\\n'
                    '如PR描述不符合规范，修改PR描述后需要/check-pr重新检查PR规范。\\n'
                    '-->\\n'
                    '/kind bug\\n\\n'
                    '## 概述\\n'
                    '修复 PR 文案来源。\\n\\n'
                    '## 变更内容\\n'
                    '- publish_pr 阶段先生成 PR draft\\n\\n'
                    '## 验证结果\\n'
                    '- pytest passed\\n\\n'
                    '**Self-checklist**:（**请自检，在[ ]内打上x，我们将检视你的完成情况，否则会导致pr无法合入**）\\n\\n'
                    '+ - [ ] **设计**：PR对应的方案是否已经经过Maintainer评审，方案检视意见是否均已答复并完成方案修改\\n'
                    '+ - [x] **测试**：PR中的代码是否已有UT/ST测试用例进行充分的覆盖，新增测试用例是否随本PR一并上库或已经上库\\n'
                    '+ - [x] **验证**：PR描述信息中是否已包含对该PR对应的Feature、Refactor、Bugfix的预期目标达成情况的详细验证结果描述\\n'
                    '+ - [ ] **接口**：是否涉及对外接口变更，相应变更已得到接口评审组织的通过，API对应的注释信息已经刷新正确\\n'
                    '+ - [ ] **文档**：是否涉及官网文档修改，如果涉及请及时提交资料到Doc仓"\n'
                    "}\n```"
                )
            },
        )


class _BrokenDraftAgent:
    async def stream(self, payload):
        del payload
        yield OutputSchema(
            type="llm_output",
            index=0,
            payload={"content": "not-json"},
        )


class _SimplifiedDraftAgent:
    async def stream(self, payload):
        del payload
        yield OutputSchema(
            type="llm_output",
            index=0,
            payload={
                "content": (
                    "```json\n"
                    "{\n"
                    '  "title": "docs(cli): add test line to README",\n'
                    '  "kind": "task",\n'
                    '  "body": "/kind task\\n\\n## 概述\\n简化版 body。\\n\\n'
                    '## 变更内容\\n- 只写简化说明\\n\\n'
                    '## 验证结果\\n- lint/type-check passed\\n\\n'
                    '## Checklist\\n- [x] 测试"\n'
                    "}\n```"
                )
            },
        )


class _RepairingDraftAgent:
    def __init__(self):
        self.calls = 0

    async def stream(self, payload):
        self.calls += 1
        if self.calls == 1:
            assert "上一次 PR draft 校验失败原因" not in payload["query"]
            yield OutputSchema(
                type="llm_output",
                index=0,
                payload={
                    "content": (
                        "```json\n"
                        "{\n"
                        '  "title": "docs(cli): add test line to README",\n'
                        '  "kind": "task",\n'
                        '  "body": "/kind task\\n\\n## 概述\\n简化版 body。\\n\\n'
                        '## 变更内容\\n- 只写简化说明\\n\\n'
                        '## 验证结果\\n- lint/type-check passed\\n\\n'
                        '## Checklist\\n- [x] 测试"\n'
                        "}\n```"
                    )
                },
            )
            return
        assert "上一次 PR draft 校验失败原因" in payload["query"]
        assert "body 未使用完整 GitCode 模板" in payload["query"]
        yield OutputSchema(
            type="llm_output",
            index=0,
            payload={
                "content": (
                    "```json\n"
                    "{\n"
                    '  "title": "docs(cli): 补充 auto-harness 测试说明",\n'
                    '  "kind": "task",\n'
                    '  "body": "<!--  Thanks for sending a pull request!  Here are some tips for you:\\n\\n'
                    '1) If this is your first time, please read our contributor guidelines: https://gitcode.com/openJiuwen/community/blob/master/CONTRIBUTING.md\\n\\n'
                    '2) If you want to contribute your code but don\\u0027t know who will review and merge, please add label `openjiuwen-assistant` to the pull request, we will find and do it as soon as possible.\\n'
                    '-->\\n\\n'
                    '**What type of PR is this?**\\n'
                    '<!--\\n'
                    '选择下面一种标签替换下方 `/kind <label>`，可选标签类型有：\\n'
                    '- /kind bug\\n'
                    '- /kind task\\n'
                    '- /kind feature\\n'
                    '- /kind refactor\\n'
                    '- /kind clean_code\\n'
                    '如PR描述不符合规范，修改PR描述后需要/check-pr重新检查PR规范。\\n'
                    '-->\\n'
                    '/kind task\\n\\n'
                    '## 概述\\n'
                    '补充 auto-harness 测试说明。\\n\\n'
                    '## 变更内容\\n'
                    '- 在 README 中补充测试命令说明\\n\\n'
                    '## 验证结果\\n'
                    '- lint/type-check passed\\n\\n'
                    '**Self-checklist**:（**请自检，在[ ]内打上x，我们将检视你的完成情况，否则会导致pr无法合入**）\\n\\n'
                    '+ - [ ] **设计**：PR对应的方案是否已经经过Maintainer评审，方案检视意见是否均已答复并完成方案修改\\n'
                    '+ - [ ] **测试**：PR中的代码是否已有UT/ST测试用例进行充分的覆盖，新增测试用例是否随本PR一并上库或已经上库\\n'
                    '+ - [x] **验证**：PR描述信息中是否已包含对该PR对应的Feature、Refactor、Bugfix的预期目标达成情况的详细验证结果描述\\n'
                    '+ - [ ] **接口**：是否涉及对外接口变更，相应变更已得到接口评审组织的通过，API对应的注释信息已经刷新正确\\n'
                    '+ - [x] **文档**：是否涉及官网文档修改，如果涉及请及时提交资料到Doc仓"\n'
                    "}\n```"
                )
            },
        )


def _build_ctx(
    *,
    git: _FakeGit,
    config: AutoHarnessConfig,
):
    orchestrator = SimpleNamespace(
        config=config,
        git=git,
        artifacts=ArtifactStore(),
        experience_store=SimpleNamespace(record=AsyncMock()),
    )
    runtime = SimpleNamespace(
        wt_path="/tmp/worktree",
        related=[],
    )
    ctx = TaskContext(
        orchestrator=orchestrator,
        task=OptimizationTask(topic="修复 PR draft"),
        runtime=runtime,
    )
    ctx.put_artifact(
        "verify_report",
        VerifyReportArtifact(
            ci_result={
                "passed": True,
                "gates": [
                    {"name": "lint", "passed": True},
                ],
            }
        ),
    )
    ctx.put_artifact(
        "commit_result",
        CommitArtifact(
            facts=CommitFacts(
                branch_name="auto-harness/topic",
                allowed_files=["openjiuwen/harness/demo.py"],
                edited_files=["openjiuwen/harness/demo.py"],
                diff_stat=" demo.py | 2 +-",
            ),
            branch_name="auto-harness/topic",
            last_commit_stat="commit abc123\n demo.py | 2 +-",
            committed=True,
        ),
    )
    return ctx


@pytest.mark.asyncio
async def test_publish_pr_stage_generates_draft_then_creates_pr():
    git = _FakeGit()
    ctx = _build_ctx(
        git=git,
        config=AutoHarnessConfig(
            git_remote="origin",
            fork_owner="bot",
        ),
    )

    with patch(
        "openjiuwen.auto_harness.stages.publish_pr.create_pr_draft_agent",
        return_value=_FakeDraftAgent(),
    ):
        stage = PublishPRStage()
        events = [event async for event in stage.stream(ctx)]

    assert events[-1].artifacts["pull_request"].pr_url == "https://gitcode.com/pr/1"
    git.push.assert_awaited_once_with(
        branch_name="auto-harness/topic"
    )
    git.create_pr.assert_awaited_once_with(
        title="fix(harness): 补齐 PR draft",
        body="<!--  Thanks for sending a pull request!  Here are some tips for you:\n\n1) If this is your first time, please read our contributor guidelines: https://gitcode.com/openJiuwen/community/blob/master/CONTRIBUTING.md\n\n2) If you want to contribute your code but don't know who will review and merge, please add label `openjiuwen-assistant` to the pull request, we will find and do it as soon as possible.\n-->\n\n**What type of PR is this?**\n<!--\n选择下面一种标签替换下方 `/kind <label>`，可选标签类型有：\n- /kind bug\n- /kind task\n- /kind feature\n- /kind refactor\n- /kind clean_code\n如PR描述不符合规范，修改PR描述后需要/check-pr重新检查PR规范。\n-->\n/kind bug\n\n## 概述\n修复 PR 文案来源。\n\n## 变更内容\n- publish_pr 阶段先生成 PR draft\n\n## 验证结果\n- pytest passed\n\n**Self-checklist**:（**请自检，在[ ]内打上x，我们将检视你的完成情况，否则会导致pr无法合入**）\n\n+ - [ ] **设计**：PR对应的方案是否已经经过Maintainer评审，方案检视意见是否均已答复并完成方案修改\n+ - [x] **测试**：PR中的代码是否已有UT/ST测试用例进行充分的覆盖，新增测试用例是否随本PR一并上库或已经上库\n+ - [x] **验证**：PR描述信息中是否已包含对该PR对应的Feature、Refactor、Bugfix的预期目标达成情况的详细验证结果描述\n+ - [ ] **接口**：是否涉及对外接口变更，相应变更已得到接口评审组织的通过，API对应的注释信息已经刷新正确\n+ - [ ] **文档**：是否涉及官网文档修改，如果涉及请及时提交资料到Doc仓",
        head_branch="auto-harness/topic",
    )


@pytest.mark.asyncio
async def test_publish_pr_stage_fails_when_draft_is_invalid():
    git = _FakeGit()
    ctx = _build_ctx(
        git=git,
        config=AutoHarnessConfig(
            git_remote="origin",
            fork_owner="bot",
        ),
    )

    with patch(
        "openjiuwen.auto_harness.stages.publish_pr.create_pr_draft_agent",
        return_value=_BrokenDraftAgent(),
    ):
        stage = PublishPRStage()
        events = [event async for event in stage.stream(ctx)]

    assert events[-1].status == "failed"
    assert (
        events[-1].error
        == "PR draft generation failed after 2 attempts: 未找到 JSON 对象"
    )
    assert (
        events[-1].artifacts["task_result"].error
        == "PR draft generation failed after 2 attempts: 未找到 JSON 对象"
    )
    git.push.assert_not_awaited()
    git.create_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_pr_stage_accepts_simplified_pr_body():
    git = _FakeGit()
    ctx = _build_ctx(
        git=git,
        config=AutoHarnessConfig(
            git_remote="origin",
            fork_owner="bot",
        ),
    )

    with patch(
        "openjiuwen.auto_harness.stages.publish_pr.create_pr_draft_agent",
        return_value=_SimplifiedDraftAgent(),
    ):
        stage = PublishPRStage()
        events = [event async for event in stage.stream(ctx)]

    expected_body = (
        "/kind task\n\n"
        "## 概述\n简化版 body。\n\n"
        "## 变更内容\n- 只写简化说明\n\n"
        "## 验证结果\n- lint/type-check passed\n\n"
        "## Checklist\n- [x] 测试"
    )
    assert (
        events[-1].artifacts["pull_request"].pr_url
        == "https://gitcode.com/pr/1"
    )
    git.push.assert_awaited_once_with(
        branch_name="auto-harness/topic"
    )
    git.create_pr.assert_awaited_once_with(
        title="docs(cli): add test line to README",
        body=expected_body,
        head_branch="auto-harness/topic",
    )


@pytest.mark.asyncio
async def test_publish_pr_stage_does_not_retry_after_simplified_draft():
    git = _FakeGit()
    ctx = _build_ctx(
        git=git,
        config=AutoHarnessConfig(
            git_remote="origin",
            fork_owner="bot",
        ),
    )
    agent = _RepairingDraftAgent()

    with patch(
        "openjiuwen.auto_harness.stages.publish_pr.create_pr_draft_agent",
        return_value=agent,
    ):
        stage = PublishPRStage()
        events = [event async for event in stage.stream(ctx)]

    assert agent.calls == 1
    assert (
        events[-1].artifacts["pull_request"].pr_url
        == "https://gitcode.com/pr/1"
    )
    git.push.assert_awaited_once_with(
        branch_name="auto-harness/topic"
    )
    git.create_pr.assert_awaited_once()
