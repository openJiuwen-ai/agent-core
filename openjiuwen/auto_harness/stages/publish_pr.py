# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Publish PR stage for auto-harness pipelines."""

from __future__ import annotations

from typing import Any, AsyncIterator

from openjiuwen.auto_harness.stages.base import (
    TaskStage,
)
from openjiuwen.auto_harness.contexts import (
    TaskContext,
)
from openjiuwen.auto_harness.schema import (
    CommitFacts,
    CycleResult,
    Experience,
    ExperienceType,
    OptimizationTask,
    PullRequestArtifact,
    StageResult,
    TaskStatus,
)


def _build_completion_summary(
    task: OptimizationTask,
    *,
    facts: CommitFacts,
    ci_result: dict[str, Any],
    pr_url: str = "",
) -> str:
    """Build a compact user-facing completion summary."""
    ci_summary = ", ".join(
        (
            f"{gate.get('name', 'unknown')}="
            f"{'PASS' if gate.get('passed') else 'FAIL'}"
        )
        for gate in ci_result.get("gates", [])
    ) or (
        "未执行"
        if not ci_result
        else ("PASS" if ci_result.get("passed") else "FAIL")
    )
    changed_files = ", ".join(facts.allowed_files[:5]) or "无"
    suffix = ""
    if len(facts.allowed_files) > 5:
        suffix = f" 等 {len(facts.allowed_files)} 个文件"
    location = pr_url or "本地提交"
    return (
        f"{task.topic}: 已完成；CI={ci_summary}；"
        f"变更文件={changed_files}{suffix}；"
        f"交付={location}"
    )


class PublishPRStage(TaskStage):
    """Push the branch, open a PR, and finalize the task result."""

    name = "publish_pr"
    description = "Push branch and create PR when configured."
    consumes = ["verify_report", "commit_result"]
    produces = ["pull_request", "task_result"]

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[StageResult]:
        verify_report = ctx.require_artifact(
            "verify_report"
        )
        commit_result = ctx.require_artifact(
            "commit_result"
        )
        if (
            not commit_result.committed
            or commit_result.facts is None
        ):
            yield StageResult(
                status="failed",
                error=commit_result.error
                or "commit result missing",
            )
            return
        branch_name = commit_result.branch_name
        pr_url = ""
        messages: list[str] = []
        if ctx.orchestrator.config.git_remote:
            yield ctx.message("[后置] 推送分支")
            await ctx.orchestrator.git.push(
                branch_name=branch_name
            )
            if ctx.orchestrator.config.fork_owner:
                yield ctx.message("[后置] 创建 PR")
                pr_result = await ctx.orchestrator.git.create_pr(
                    title=f"auto-harness: {ctx.task.topic}",
                    body=(
                        "Auto-harness 自动优化\n\n"
                        f"任务: {ctx.task.topic}\n"
                        f"{ctx.task.description}"
                    ),
                    head_branch=branch_name,
                )
                pr_url = pr_result.get("pr_url", "")
                if pr_url:
                    messages.append(f"PR 已创建: {pr_url}")
        completion_summary = _build_completion_summary(
            ctx.task,
            facts=commit_result.facts,
            ci_result=verify_report.ci_result,
            pr_url=pr_url,
        )
        ctx.task.status = TaskStatus.SUCCESS
        await ctx.orchestrator.experience_store.record(
            Experience(
                type=ExperienceType.OPTIMIZATION,
                topic=ctx.task.topic,
                summary=f"completed: {ctx.task.topic}",
                outcome="success",
                pr_url=pr_url,
            )
        )
        result = CycleResult(
            success=True,
            summary=completion_summary,
            pr_url=pr_url,
        )
        yield StageResult(
            artifacts={
                "pull_request": PullRequestArtifact(
                    pr_url=pr_url,
                    summary=completion_summary,
                ),
                "task_result": result,
            },
            messages=messages
            + [
                f"任务总结: {completion_summary}",
                (
                    f"任务完成: {pr_url}"
                    if pr_url
                    else "任务完成（本地提交）"
                ),
            ],
        )
