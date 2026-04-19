# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Commit stage for auto-harness pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
)

from openjiuwen.auto_harness.infra.commit_scope import (
    derive_legacy_related_test_files,
    extract_verify_related_files,
    is_allowed_documentation_file,
    is_derived_test_file,
    is_documentation_file,
)
from openjiuwen.auto_harness.stages.base import (
    TaskStage,
)
from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
)
from openjiuwen.auto_harness.contexts import (
    TaskContext,
)
from openjiuwen.auto_harness.schema import (
    CommitArtifact,
    CommitFacts,
    CycleResult,
    Experience,
    ExperienceType,
    StageResult,
    TaskStatus,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.infra.git_operations import (
        GitOperations,
    )
    from openjiuwen.auto_harness.rails.edit_safety_rail import (
        EditSafetyRail,
    )
    from openjiuwen.auto_harness.schema import (
        OptimizationTask,
    )


@dataclass
class CommitRoundResult:
    """Structured outcome for one commit attempt."""

    ok: bool
    reason: str
    status_text: str
    last_commit_stat: str


def _derive_allowed_files(
    facts: "CommitFacts",
) -> list[str]:
    edited_set = set(facts.edited_files)
    if not edited_set:
        return []
    allowed = set()
    for path in facts.task_declared_files:
        if path not in edited_set:
            continue
        if is_documentation_file(path):
            if is_allowed_documentation_file(path):
                allowed.add(path)
            continue
        allowed.add(path)
    for path in facts.derived_test_files:
        if path not in edited_set:
            continue
        if is_derived_test_file(
            facts.task_declared_files,
            path,
        ):
            allowed.add(path)
    for path in facts.legacy_related_test_files:
        if path in edited_set:
            allowed.add(path)
    if not facts.task_declared_files:
        allowed = set()
        for path in edited_set:
            if not is_documentation_file(path):
                allowed.add(path)
                continue
            if is_allowed_documentation_file(path):
                allowed.add(path)
    return sorted(allowed)


def _build_commit_prompt(
    task: "OptimizationTask",
    facts: "CommitFacts",
    *,
    retry_reason: str = "",
    retry_status: str = "",
    last_commit_stat: str = "",
) -> str:
    retry_text = (
        f"\n上一次提交尝试失败原因:\n{retry_reason}\n"
        if retry_reason
        else ""
    )
    status_text = (
        "\n上一次提交尝试后的 git status --porcelain:\n"
        f"{retry_status or '无'}\n"
        if retry_reason
        else ""
    )
    commit_stat_text = (
        f"\n最近一次提交摘要:\n{last_commit_stat}\n"
        if last_commit_stat
        else ""
    )
    return (
        f"任务: {task.topic}\n"
        f"描述: {task.description}\n"
        f"声明文件: {', '.join(facts.task_declared_files) or '无'}\n"
        f"当前脏文件: {', '.join(facts.current_dirty_files) or '无'}\n"
        f"本轮实际修改: {', '.join(facts.edited_files) or '无'}\n"
        f"允许提交文件: {', '.join(facts.allowed_files) or '无'}\n"
        f"派生测试文件: {', '.join(facts.derived_test_files) or '无'}\n"
        f"验证关联老测试: {', '.join(facts.legacy_related_test_files) or '无'}\n"
        f"禁止混入旧脏文件: {', '.join(facts.preexisting_dirty_files) or '无'}\n"
        f"diff 统计:\n{facts.diff_stat or '无'}\n"
        f"{retry_text}"
        f"{status_text}"
        f"{commit_stat_text}\n"
        "请遵循 commit skill，通过 bash 执行 git status、git add 明确文件路径、git commit，并在提交后自检。"
    )


async def _collect_commit_facts(
    task: "OptimizationTask",
    git: "GitOperations",
    edit_safety_rail: "EditSafetyRail",
    *,
    preexisting_dirty_files: list[str],
    ci_result: dict[str, Any] | None,
    fix_errors: str,
) -> "CommitFacts":
    status = await git.collect_status()
    edited_files = edit_safety_rail.edited_files()
    verify_related_files = extract_verify_related_files(
        ci_result,
        fix_errors,
    )
    derived_test_files: list[str] = []
    for path in edited_files:
        if path not in status["dirty_files"]:
            continue
        if is_derived_test_file(task.files, path):
            derived_test_files.append(path)
    legacy_related_test_files = derive_legacy_related_test_files(
        edited_files,
        verify_related_files,
    )
    facts = CommitFacts(
        branch_name=await git.current_branch(),
        task_declared_files=list(task.files),
        preexisting_dirty_files=list(preexisting_dirty_files),
        current_dirty_files=status["dirty_files"],
        tracked_modified_files=status["tracked_modified_files"],
        untracked_files=status["untracked_files"],
        edited_files=edited_files,
        derived_test_files=derived_test_files,
        legacy_related_test_files=legacy_related_test_files,
        verify_related_files=verify_related_files,
        diff_stat=await git.diff_stat(),
    )
    facts.allowed_files = _derive_allowed_files(facts)
    return facts


def _format_commit_failure(
    reason: str,
    *,
    status_text: str = "",
    last_commit_stat: str = "",
) -> str:
    details = [reason]
    if status_text:
        details.append(
            "当前 git status --porcelain:\n"
            f"{status_text}"
        )
    if last_commit_stat:
        details.append(
            "最近一次提交摘要:\n"
            f"{last_commit_stat}"
        )
    return "\n".join(details)


async def _run_commit_round_stream(
    *,
    commit_agent: "DeepAgent | None",
    task: "OptimizationTask",
    git: "GitOperations",
    facts: "CommitFacts",
    retry_reason: str = "",
    retry_status: str = "",
    last_commit_stat: str = "",
) -> AsyncIterator[Any]:
    """Stream one commit attempt and end with a CommitRoundResult."""
    if commit_agent is None:
        yield CommitRoundResult(
            ok=False,
            reason="No agent available for commit phase.",
            status_text="",
            last_commit_stat="",
        )
        return
    before_head = await git.current_head()
    async for chunk in commit_agent.stream(
        {
            "query": _build_commit_prompt(
                task,
                facts,
                retry_reason=retry_reason,
                retry_status=retry_status,
                last_commit_stat=last_commit_stat,
            )
        }
    ):
        yield chunk
    after_head = await git.current_head()
    status_text = await git.status_porcelain()
    latest_commit = ""
    if after_head != before_head:
        latest_commit = await git.show_last_commit_stat()
        yield CommitRoundResult(
            ok=True,
            reason="",
            status_text=status_text,
            last_commit_stat=latest_commit,
        )
        return
    yield CommitRoundResult(
        ok=False,
        reason="Agent did not create a git commit during commit phase.",
        status_text=status_text,
        last_commit_stat=latest_commit,
    )


class CommitStage(TaskStage):
    """Create a git commit for the current task."""

    name = "commit"
    description = "Create a git commit for the task."
    consumes = ["verify_report"]
    produces = ["commit_result"]

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        verify_report = ctx.require_artifact(
            "verify_report"
        )
        if verify_report.reverted:
            yield StageResult(
                status="failed",
                error=verify_report.error,
            )
            return
        facts = await _collect_commit_facts(
            ctx.task,
            ctx.orchestrator.git,
            ctx.runtime.edit_safety_rail,
            preexisting_dirty_files=ctx.runtime.preexisting_dirty_files,
            ci_result=verify_report.ci_result,
            fix_errors=verify_report.fix_errors,
        )
        messages = [
            "[4/5] 检查提交范围",
            "[5/5] 提交变更",
        ]
        commit_ok = False
        reason = ""
        status_text = ""
        last_commit_stat = ""
        async for event in _run_commit_round_stream(
                commit_agent=ctx.runtime.commit_agent
                or ctx.runtime.task_agent,
                task=ctx.task,
                git=ctx.orchestrator.git,
                facts=facts,
        ):
            if isinstance(event, CommitRoundResult):
                commit_ok = event.ok
                reason = event.reason
                status_text = event.status_text
                last_commit_stat = event.last_commit_stat
            else:
                yield event
        if not commit_ok:
            messages.append(
                "首次提交未成功:\n"
                + _format_commit_failure(
                    reason,
                    status_text=status_text,
                    last_commit_stat=last_commit_stat,
                )
            )
            refreshed_facts = await _collect_commit_facts(
                ctx.task,
                ctx.orchestrator.git,
                ctx.runtime.edit_safety_rail,
                preexisting_dirty_files=ctx.runtime.preexisting_dirty_files,
                ci_result=verify_report.ci_result,
                fix_errors=verify_report.fix_errors,
            )
            async for event in _run_commit_round_stream(
                    commit_agent=ctx.runtime.commit_agent
                    or ctx.runtime.task_agent,
                    task=ctx.task,
                    git=ctx.orchestrator.git,
                    facts=refreshed_facts,
                    retry_reason=reason,
                    retry_status=status_text,
                    last_commit_stat=last_commit_stat,
            ):
                if isinstance(event, CommitRoundResult):
                    commit_ok = event.ok
                    reason = event.reason
                    status_text = event.status_text
                    last_commit_stat = event.last_commit_stat
                else:
                    yield event
            facts = refreshed_facts
        if not commit_ok:
            formatted_error = _format_commit_failure(
                reason,
                status_text=status_text,
                last_commit_stat=last_commit_stat,
            )
            ctx.task.status = TaskStatus.FAILED
            await ctx.orchestrator.experience_store.record(
                Experience(
                    type=ExperienceType.FAILURE,
                    topic=ctx.task.topic,
                    summary="commit failed",
                    outcome="failed",
                    details=formatted_error,
                    files_changed=facts.allowed_files,
                )
            )
            result = CycleResult(
                success=False,
                error=formatted_error,
            )
            yield StageResult(
                status="failed",
                artifacts={
                    "commit_result": CommitArtifact(
                        facts=facts,
                        status_text=status_text,
                        last_commit_stat=last_commit_stat,
                        branch_name=facts.branch_name,
                        committed=False,
                        error=formatted_error,
                    ),
                    "task_result": result,
                },
                messages=messages
                + [f"提交失败: {formatted_error}"],
                error=formatted_error,
            )
            return
        yield StageResult(
            artifacts={
                "commit_result": CommitArtifact(
                    facts=facts,
                    status_text=status_text,
                    last_commit_stat=last_commit_stat,
                    branch_name=facts.branch_name,
                    committed=True,
                )
            },
            messages=messages,
        )
