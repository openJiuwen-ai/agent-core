# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Publish PR stage for auto-harness pipelines."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from openjiuwen.auto_harness.agents import (
    create_pr_draft_agent,
)
from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
    parse_pr_draft_with_error,
)
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
    PullRequestDraft,
    PullRequestArtifact,
    StageResult,
    TaskStatus,
)

_PR_DRAFT_MAX_ATTEMPTS = 2


@dataclass
class _DraftGenerationResult:
    draft: PullRequestDraft | None
    error: str
    output: str


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


def _build_pr_draft_query(
    ctx: TaskContext,
    *,
    facts: CommitFacts,
    ci_result: dict[str, Any],
    last_commit_stat: str,
    validation_error: str = "",
    previous_output: str = "",
) -> str:
    related_text = "\n".join(
        f"- [{item.type.value}] {item.topic}: {item.summary}"
        for item in ctx.runtime.related[:5]
    ) or "无"
    repair_text = ""
    if validation_error:
        repair_text = (
            "\n\n上一次 PR draft 校验失败原因:\n"
            f"{validation_error}\n\n"
            "上一次输出如下，请修正为合法的完整 GitCode 模板，"
            "不要重复输出简化版格式，也不要省略 HTML 注释和标准 checklist：\n"
            f"{previous_output or '无'}\n"
        )
    return (
        f"任务主题: {ctx.task.topic}\n"
        f"任务描述: {ctx.task.description or '无'}\n"
        f"预期效果: {ctx.task.expected_effect or '无'}\n"
        f"关联 issue: {ctx.task.issue_ref or '无'}\n"
        f"允许提交文件: {', '.join(facts.allowed_files) or '无'}\n"
        f"本轮实际修改: {', '.join(facts.edited_files) or '无'}\n"
        f"diff 统计:\n{facts.diff_stat or '无'}\n\n"
        f"最近一次提交摘要:\n{last_commit_stat or '无'}\n\n"
        f"验证结果(JSON):\n"
        f"{json.dumps(ci_result, ensure_ascii=False, indent=2)}\n\n"
        f"相关经验:\n{related_text}\n\n"
        f"{repair_text}"
        "请基于这些事实，生成可直接提交到 GitCode 的 PR draft。"
    )


async def _generate_pr_draft_attempt(
    ctx: TaskContext,
    *,
    facts: CommitFacts,
    ci_result: dict[str, Any],
    last_commit_stat: str,
    validation_error: str = "",
    previous_output: str = "",
) -> AsyncIterator[Any]:
    agent = create_pr_draft_agent(
        ctx.orchestrator.config,
        workspace_override=ctx.runtime.wt_path,
    )
    output = ""
    async for chunk in agent.stream(
        {
            "query": _build_pr_draft_query(
                ctx,
                facts=facts,
                ci_result=ci_result,
                last_commit_stat=last_commit_stat,
                validation_error=validation_error,
                previous_output=previous_output,
            )
        }
    ):
        yield chunk
        output += extract_text(chunk)
    draft, error = parse_pr_draft_with_error(output)
    yield _DraftGenerationResult(
        draft=draft,
        error=error,
        output=output,
    )


def _should_create_pr(ctx: TaskContext) -> bool:
    return bool(
        ctx.orchestrator.config.git_remote
        and ctx.orchestrator.config.fork_owner
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
            error = (
                commit_result.error
                or "commit result missing"
            )
            ctx.task.status = TaskStatus.FAILED
            yield StageResult(
                status="failed",
                artifacts={
                    "task_result": CycleResult(
                        success=False,
                        error=error,
                    )
                },
                messages=[f"发布 PR 失败: {error}"],
                error=error,
            )
            return
        branch_name = commit_result.branch_name
        pr_url = ""
        messages: list[str] = []
        pr_draft = PullRequestDraft()
        if _should_create_pr(ctx):
            draft_error = ""
            previous_output = ""
            for attempt in range(1, _PR_DRAFT_MAX_ATTEMPTS + 1):
                if attempt == 1:
                    yield ctx.message("[后置] 生成 PR draft")
                else:
                    yield ctx.message(
                        f"[后置] 修正 PR draft ({attempt}/{_PR_DRAFT_MAX_ATTEMPTS})"
                    )
                async for event in _generate_pr_draft_attempt(
                    ctx,
                    facts=commit_result.facts,
                    ci_result=verify_report.ci_result,
                    last_commit_stat=commit_result.last_commit_stat,
                    validation_error=draft_error,
                    previous_output=previous_output,
                ):
                    if isinstance(event, _DraftGenerationResult):
                        pr_draft = event.draft or PullRequestDraft()
                        draft_error = event.error
                        previous_output = event.output
                    else:
                        yield event
                if pr_draft.title and pr_draft.body:
                    break
            if not pr_draft.title or not pr_draft.body:
                error = (
                    "PR draft generation failed after "
                    f"{_PR_DRAFT_MAX_ATTEMPTS} attempts: "
                    f"{draft_error or 'communicate agent did not return a valid JSON draft.'}"
                )
                ctx.task.status = TaskStatus.FAILED
                yield StageResult(
                    status="failed",
                    artifacts={
                        "task_result": CycleResult(
                            success=False,
                            error=error,
                        )
                    },
                    messages=[f"发布 PR 失败: {error}"],
                    error=error,
                )
                return
            messages.append(f"PR draft 已生成: {pr_draft.title}")
        if ctx.orchestrator.config.git_remote:
            yield ctx.message("[后置] 推送分支")
            await ctx.orchestrator.git.push(
                branch_name=branch_name
            )
            if _should_create_pr(ctx):
                yield ctx.message("[后置] 创建 PR")
                pr_result = await ctx.orchestrator.git.create_pr(
                    title=pr_draft.title,
                    body=pr_draft.body,
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
