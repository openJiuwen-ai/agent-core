# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Commit stage for auto-harness pipelines."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
)

from openjiuwen.auto_harness.infra.commit_scope import (
    derive_legacy_related_test_files,
    extract_verify_related_files,
    is_allowed_documentation_file,
    is_allowed_repo_edit_path,
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
from openjiuwen.harness.deep_agent import DeepAgent

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
        if not is_allowed_repo_edit_path(path):
            continue
        if is_documentation_file(path):
            if is_allowed_documentation_file(path):
                allowed.add(path)
            continue
        allowed.add(path)
    for path in facts.derived_test_files:
        if path not in edited_set:
            continue
        if not is_allowed_repo_edit_path(path):
            continue
        if is_derived_test_file(
            facts.task_declared_files,
            path,
        ):
            allowed.add(path)
    for path in facts.legacy_related_test_files:
        if (
            path in edited_set
            and is_allowed_repo_edit_path(path)
        ):
            allowed.add(path)
    if not facts.task_declared_files:
        allowed = set()
        for path in edited_set:
            if not is_allowed_repo_edit_path(path):
                continue
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
    # Fallback: when edit_safety_rail tracked nothing
    # (e.g. extension pipeline where the task agent writes
    # files directly), treat all dirty files minus
    # preexisting ones as edited.
    if not edited_files:
        pre_set = set(preexisting_dirty_files)
        edited_files = [
            f
            for f in status["dirty_files"]
            if f not in pre_set
        ]
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


async def _collect_existing_branch_commit_facts(
    task: "OptimizationTask",
    git: "GitOperations",
    *,
    preexisting_dirty_files: list[str],
) -> "CommitFacts":
    """Collect commit facts from current branch commits vs base."""
    branch_files = await git.diff_name_only_against_base()
    edited_files = [
        path
        for path in branch_files
        if is_allowed_repo_edit_path(path)
    ]
    facts = CommitFacts(
        branch_name=await git.current_branch(),
        task_declared_files=list(task.files),
        preexisting_dirty_files=list(preexisting_dirty_files),
        current_dirty_files=[],
        tracked_modified_files=[],
        untracked_files=[],
        edited_files=edited_files,
        derived_test_files=[
            path
            for path in edited_files
            if is_derived_test_file(task.files, path)
        ],
        legacy_related_test_files=[],
        verify_related_files=[],
        diff_stat=await git.diff_stat_against_base(),
    )
    facts.allowed_files = _derive_allowed_files(facts)
    if not facts.allowed_files and edited_files:
        facts.allowed_files = edited_files
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


def _is_explicit_issue_fix(task: "OptimizationTask") -> bool:
    text = "\n".join(
        str(value or "")
        for value in (
            task.issue_ref,
            task.topic,
            task.description,
        )
    )
    return bool(
        re.search(
            r"(?:fix-issue-|issue-|\bissue\s*#?|#)\s*\d+",
            text,
            flags=re.IGNORECASE,
        )
    )


def _extract_issue_target_symbols(task: "OptimizationTask") -> list[str]:
    """Extract function/class names that an explicit issue calls out."""
    if not _is_explicit_issue_fix(task):
        return []
    text = "\n".join(
        str(value or "")
        for value in (
            task.topic,
            task.description,
            task.expected_effect,
        )
    )
    symbols: list[str] = []
    patterns = (
        r"`([A-Za-z_][A-Za-z0-9_]{2,})\s*\(\s*\)`",
        r"`([A-Za-z_][A-Za-z0-9_]{2,})`",
        r"(?:函数|方法|接口|class|function|method)\s*[`“\"]?([A-Za-z_][A-Za-z0-9_]{2,})",
    )
    ignored = {
        "issue",
        "gitcode",
        "auto",
        "harness",
        "jiuwenswarm",
        "true",
        "false",
        "none",
        "null",
    }
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            symbol = match.group(1)
            if symbol.lower() in ignored:
                continue
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols[:8]


async def _validate_issue_target_alignment(
    *,
    task: "OptimizationTask",
    git: "GitOperations",
    against_base: bool,
) -> str:
    """Ensure explicit issue-fix diffs hit symbols named by the issue."""
    symbols = _extract_issue_target_symbols(task)
    if not symbols:
        return ""
    diff_text = (
        await git.diff_against_base()
        if against_base
        else await git.diff_against("HEAD")
    )
    if not diff_text.strip():
        return ""
    matched = [
        symbol
        for symbol in symbols
        if re.search(rf"\b{re.escape(symbol)}\b", diff_text)
    ]
    if matched:
        return ""
    return (
        "Issue target validation failed: final diff did not touch symbols "
        f"explicitly named by the issue: {', '.join(symbols)}. "
        "Do not reuse a similar branch/commit unless it changes the exact "
        "function or method requested by the issue."
    )


def _build_deterministic_commit_message(
    task: "OptimizationTask",
    facts: "CommitFacts",
) -> str:
    """Build a stable commit message without asking the agent to run git."""
    topic = (task.topic or "auto-harness task").strip()
    title = topic
    if not title.lower().startswith(("fix", "feat", "docs", "test", "refactor")):
        title = f"fix(auto-harness): {title}"
    title = " ".join(title.split())
    if len(title) > 120:
        title = title[:117].rstrip() + "..."

    body_lines = [
        "",
        "Auto-Harness deterministic commit.",
        "",
        f"Task: {task.topic}",
    ]
    description = (task.description or "").strip()
    if description:
        body_lines.extend([
            "",
            "Description:",
            description,
        ])
    if facts.allowed_files:
        body_lines.extend([
            "",
            "Files:",
            *[f"- {path}" for path in facts.allowed_files],
        ])
    return title + "\n".join(body_lines)


async def _run_deterministic_commit(
    *,
    task: "OptimizationTask",
    git: "GitOperations",
    facts: "CommitFacts",
) -> CommitRoundResult:
    """Create the commit directly in the task worktree."""
    if not facts.allowed_files:
        status_text = await git.status_porcelain()
        return CommitRoundResult(
            ok=False,
            reason="No allowed files to commit.",
            status_text=status_text,
            last_commit_stat="",
        )

    before_head = await git.current_head()
    add_result = await git.add_paths(facts.allowed_files)
    if not add_result.get("success"):
        status_text = await git.status_porcelain()
        return CommitRoundResult(
            ok=False,
            reason=(
                "Failed to stage allowed files:\n"
                f"{add_result.get('output', '')}"
            ),
            status_text=status_text,
            last_commit_stat="",
        )

    message = _build_deterministic_commit_message(task, facts)
    commit_result = await git.commit(message)
    after_head = await git.current_head()
    status_text = await git.status_porcelain()
    latest_commit = ""
    if commit_result.get("success") and after_head != before_head:
        latest_commit = await git.show_last_commit_stat()
        return CommitRoundResult(
            ok=True,
            reason="",
            status_text=status_text,
            last_commit_stat=latest_commit,
        )

    return CommitRoundResult(
        ok=False,
        reason=(
            "Deterministic git commit did not create a new commit:\n"
            f"{commit_result.get('output', '')}"
        ),
        status_text=status_text,
        last_commit_stat="",
    )


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
    slot = "commit"
    display_name = "提交变更"
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
            "检查提交范围",
            "提交变更",
        ]
        messages.append(
            "自动提交文件: "
            + (", ".join(facts.allowed_files) or "无")
        )
        alignment_error = await _validate_issue_target_alignment(
            task=ctx.task,
            git=ctx.orchestrator.git,
            against_base=False,
        )
        if alignment_error:
            ctx.task.status = TaskStatus.FAILED
            result = CycleResult(
                success=False,
                error=alignment_error,
            )
            yield StageResult(
                status="failed",
                artifacts={
                    "commit_result": CommitArtifact(
                        facts=facts,
                        status_text=await ctx.orchestrator.git.status_porcelain(),
                        branch_name=facts.branch_name,
                        committed=False,
                        error=alignment_error,
                    ),
                    "task_result": result,
                },
                messages=messages + [f"提交失败: {alignment_error}"],
                error=alignment_error,
            )
            return
        if not facts.allowed_files and await ctx.orchestrator.git.has_commits_against_base():
            facts = await _collect_existing_branch_commit_facts(
                ctx.task,
                ctx.orchestrator.git,
                preexisting_dirty_files=ctx.runtime.preexisting_dirty_files,
            )
            alignment_error = await _validate_issue_target_alignment(
                task=ctx.task,
                git=ctx.orchestrator.git,
                against_base=True,
            )
            if alignment_error:
                ctx.task.status = TaskStatus.FAILED
                result = CycleResult(
                    success=False,
                    error=alignment_error,
                )
                yield StageResult(
                    status="failed",
                    artifacts={
                        "commit_result": CommitArtifact(
                            facts=facts,
                            status_text=await ctx.orchestrator.git.status_porcelain(),
                            branch_name=facts.branch_name,
                            committed=False,
                            error=alignment_error,
                        ),
                        "task_result": result,
                    },
                    messages=messages + [f"提交失败: {alignment_error}"],
                    error=alignment_error,
                )
                return
            status_text = await ctx.orchestrator.git.status_porcelain()
            last_commit_stat = await ctx.orchestrator.git.show_last_commit_stat()
            messages = [
                "检查提交范围",
                "未发现新的工作区 diff，复用当前分支已有提交",
                "分支已有提交文件: "
                + (", ".join(facts.allowed_files) or "无"),
            ]
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
            return
        commit_result = await _run_deterministic_commit(
            task=ctx.task,
            git=ctx.orchestrator.git,
            facts=facts,
        )
        commit_ok = commit_result.ok
        reason = commit_result.reason
        status_text = commit_result.status_text
        last_commit_stat = commit_result.last_commit_stat
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
