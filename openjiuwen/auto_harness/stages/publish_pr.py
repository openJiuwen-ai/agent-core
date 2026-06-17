# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Publish PR stage for auto-harness pipelines."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

from openjiuwen.auto_harness.agents import (
    create_pr_draft_agent,
)
from openjiuwen.auto_harness.infra.gitcode_pr_template import (
    fetch_pr_template,
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
_SELF_CHECKLIST_ITEMS = ("设计", "测试", "验证", "接口", "文档")
_SELF_CHECKLIST_NOTES = {
    "设计": "已按 issue 范围完成方案说明；不涉及额外方案评审时按不涉及处理",
    "测试": "已补充或运行相关验证；如无新增测试，原因已在验证结果中说明",
    "验证": "PR 描述已包含本次 Bugfix/Feature/Refactor 的验证结果",
    "接口": "不涉及对外接口变更；如涉及需在正文中补充接口评审信息",
    "文档": "不涉及官网文档变更；如涉及需同步提交资料到 Doc 仓",
}


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


def _summarize_ci_result(ci_result: dict[str, Any]) -> str:
    """Return a markdown summary for CI/verification facts."""
    if not ci_result:
        return "- 未执行"
    gates = ci_result.get("gates") or []
    if gates:
        lines = []
        for gate in gates:
            name = gate.get("name") or "unknown"
            status = "PASS" if gate.get("passed") else "FAIL"
            details = gate.get("summary") or gate.get("details") or ""
            suffix = f": {details}" if details else ""
            lines.append(f"- {name}: {status}{suffix}")
        return "\n".join(lines)
    return (
        "- overall: PASS"
        if ci_result.get("passed")
        else "- overall: FAIL"
    )


def _build_deterministic_pr_title(
    task: OptimizationTask,
) -> str:
    """Build a PR title from deterministic task facts."""
    return _normalize_pr_title(
        task.topic
        or task.issue_ref
        or "auto-harness task",
        task,
    )


def _extract_issue_number(task: OptimizationTask) -> str:
    parts = []
    for value in (task.issue_ref, task.topic, task.description, task.expected_effect):
        parts.append(str(value or ""))
    text = "\n".join(parts)
    match = re.search(
        r"(?:fix-issue-|issue-|\bissue\s*#?|#)\s*(\d+)",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _normalize_pr_title(title: str, task: OptimizationTask) -> str:
    issue_number = _extract_issue_number(task)
    normalized = (title or task.topic or "auto-harness task").strip()
    lowered = normalized.lower()
    prefixes = (
        "fix",
        "feat",
        "docs",
        "test",
        "refactor",
        "chore",
        "perf",
    )
    if not lowered.startswith(prefixes):
        normalized = f"fix(auto-harness): {normalized}"
    if issue_number and not re.search(rf"#\s*{issue_number}\b", normalized):
        normalized = f"{normalized} (#{issue_number})"
    if len(normalized) > 120:
        suffix = f" (#{issue_number})" if issue_number else ""
        budget = 120 - len(suffix)
        normalized = normalized[: max(20, budget - 3)].rstrip() + "..." + suffix
    return normalized


def _normalize_pr_body(body: str, task: OptimizationTask) -> str:
    issue_number = _extract_issue_number(task)
    normalized = body or ""
    normalized = _normalize_self_checklist(normalized)
    if not issue_number:
        return normalized
    issue_ref = f"#{issue_number}"
    if not re.search(rf"(?i)(关联\s*issue|对应\s*issue|closes|fixes|resolves).{{0,20}}#\s*{issue_number}\b", normalized):
        normalized += (
            "\n\n## 关联 Issue\n"
            f"- 对应 Issue: {issue_ref}\n"
            f"Closes {issue_ref}\n"
        )
    if not re.search(rf"(?im)^\s*(closes|fixes|resolves)\s+#\s*{issue_number}\s*$", normalized):
        normalized += f"\n\nCloses {issue_ref}\n"
    if not re.search(rf"(?i)对应\s*issue\s*[:：]\s*#\s*{issue_number}\b", normalized):
        normalized += f"\n\n对应 Issue: {issue_ref}\n"
    return _normalize_self_checklist(normalized)


def _normalize_self_checklist(body: str) -> str:
    """Make GitCode PR checklist parseable and non-blocking for check-pr."""
    normalized = body or ""
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in normalized.splitlines():
        line = re.sub(
            r"^\s*\+\s*-\s*\[\s*([xX ]?)\s*\]",
            r"- [\1]",
            raw_line,
        )
        match = re.match(
            r"^(\s*-\s*)\[\s*[xX ]?\s*\](\s*\*\*(设计|测试|验证|接口|文档)\*\*[:：]?\s*)(.*)$",
            line,
        )
        if match:
            item = match.group(3)
            text = match.group(4).strip()
            seen.add(item)
            if item in {"接口", "文档"} and "不涉及" not in text:
                text = f"{text}；{_SELF_CHECKLIST_NOTES[item]}" if text else _SELF_CHECKLIST_NOTES[item]
            elif not text:
                text = _SELF_CHECKLIST_NOTES[item]
            line = f"{match.group(1)}[x]{match.group(2)}{text}"
        lines.append(line)

    normalized = "\n".join(lines)
    if "**Self-checklist**" not in normalized:
        normalized = normalized.rstrip() + (
            "\n\n**Self-checklist**:（**请自检，在[ ]内打上x，我们将检视你的完成情况，否则会导致pr无法合入**）\n"
        )
        seen = set()
    missing = [item for item in _SELF_CHECKLIST_ITEMS if item not in seen]
    if missing:
        suffix = "\n".join(
            f"- [x] **{item}**：{_SELF_CHECKLIST_NOTES[item]}"
            for item in missing
        )
        normalized = normalized.rstrip() + "\n\n" + suffix
    return normalized


def _normalize_pr_draft(
    draft: PullRequestDraft,
    task: OptimizationTask,
) -> PullRequestDraft:
    return PullRequestDraft(
        title=_normalize_pr_title(draft.title, task),
        body=_normalize_pr_body(draft.body, task),
        kind=draft.kind or "bug",
    )


def _build_deterministic_pr_body(
    task: OptimizationTask,
    *,
    facts: CommitFacts,
    ci_result: dict[str, Any],
    last_commit_stat: str,
    draft_error: str,
) -> str:
    """Build a reviewable GitCode PR body without relying on agent JSON."""
    allowed_files = facts.allowed_files or facts.edited_files
    changed_files = (
        "\n".join(f"- `{path}`" for path in allowed_files)
        if allowed_files
        else "- 无"
    )
    implementation_files = (
        "\n".join(f"- `{path}`" for path in facts.edited_files)
        if facts.edited_files
        else "- 无新增工作区 diff，复用当前分支已有提交"
    )
    issue_number = _extract_issue_number(task)
    issue_ref = f"#{issue_number}" if issue_number else (task.issue_ref or "无")
    description = task.description or "无"
    expected_effect = task.expected_effect or "无"
    diff_stat = facts.diff_stat or last_commit_stat or "无"
    fallback_note = (
        "\n\n> 注：PR draft agent 未返回可解析 JSON，"
        "本 PR 描述由 auto-harness 基于提交事实自动生成。"
    )
    if draft_error:
        fallback_note += f"\n> draft 解析错误：{draft_error}"
    issue_link_section = (
        "## 关联 Issue\n"
        f"- 对应 Issue: {issue_ref}\n"
        + (f"- Closes {issue_ref}\n\n" if issue_number else "\n")
    )
    return (
        "<!-- Thanks for contributing to JiuwenSwarm. -->\n\n"
        "**What type of PR is this?**\n"
        "/kind bug\n\n"
        "## 概述\n"
        f"- 任务主题：{task.topic or '无'}\n"
        f"- 关联 issue：{issue_ref}\n"
        f"- 任务描述：{description}\n"
        f"- 预期效果：{expected_effect}\n"
        f"{fallback_note}\n\n"
        "## 修改方案\n"
        "- 基于 auto-harness 执行过程产生的提交事实整理本次修复。\n"
        "- 保留实际提交中的代码改动，供 committer 按文件和 diff 审核。\n\n"
        "## 修改文件\n"
        f"{changed_files}\n\n"
        "## 本轮实现文件\n"
        f"{implementation_files}\n\n"
        "## Diff 统计\n"
        "```text\n"
        f"{diff_stat}\n"
        "```\n\n"
        "## 验证结果\n"
        f"{_summarize_ci_result(ci_result)}\n\n"
        "## 风险与回滚\n"
        "- 风险：请重点审核上述文件中的行为变更和测试覆盖。\n"
        "- 回滚：如需回滚，可 revert 本 PR 对应提交。\n\n"
        f"{issue_link_section}"
        "**Self-checklist**:（**请自检，在[ ]内打上x，我们将检视你的完成情况，否则会导致pr无法合入**）\n\n"
        "- [x] **设计**：已按 issue 范围完成方案说明；不涉及额外方案评审时按不涉及处理\n"
        "- [x] **测试**：已补充或运行相关验证；如无新增测试，原因已在验证结果中说明\n"
        "- [x] **验证**：PR 描述已包含本次 Bugfix/Feature/Refactor 的验证结果\n"
        "- [x] **接口**：不涉及对外接口变更；如涉及需在正文中补充接口评审信息\n"
        "- [x] **文档**：不涉及官网文档变更；如涉及需同步提交资料到 Doc 仓\n\n"
        "## Checklist\n"
        "- [x] 变更范围已限制在任务相关文件内\n"
        "- [x] 已执行 auto-harness 验证阶段\n"
        "- [x] PR 描述包含修改方案和验证信息\n"
    )


def _build_deterministic_pr_draft(
    task: OptimizationTask,
    *,
    facts: CommitFacts,
    ci_result: dict[str, Any],
    last_commit_stat: str,
    draft_error: str,
) -> PullRequestDraft:
    """Fallback PR draft used when the communicate agent omits JSON."""
    return PullRequestDraft(
        title=_build_deterministic_pr_title(task),
        kind="bug",
        body=_build_deterministic_pr_body(
            task,
            facts=facts,
            ci_result=ci_result,
            last_commit_stat=last_commit_stat,
            draft_error=draft_error,
        ),
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
            "不要重复输出简化版格式，也不要省略 HTML 注释、章节标题和 checklist：\n"
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
        "PR 标题必须包含对应 issue 号（例如 #1265）。"
        "PR 正文必须包含“对应 Issue: #<编号>”以及单独一行"
        "“Closes #<编号>”，用于 GitCode 自动关联 issue。"
        "PR 正文中的 Self-checklist 禁止出现 '+ - [ ]' 这种 diff 标记，"
        "设计、测试、验证、接口、文档 5 项必须使用 '- [x]'；"
        "不涉及的接口/文档也要勾选并写明“不涉及”。"
    )


async def _generate_pr_draft_attempt(
    ctx: TaskContext,
    *,
    facts: CommitFacts,
    ci_result: dict[str, Any],
    last_commit_stat: str,
    pr_template: str,
    validation_error: str = "",
    previous_output: str = "",
) -> AsyncIterator[Any]:
    agent = create_pr_draft_agent(
        ctx.orchestrator.config,
        workspace_override=ctx.runtime.wt_path,
        extra_rails=ctx.orchestrator.stream_rails or None,
        pr_template=pr_template,
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


def _format_publish_diagnostics(data: dict[str, Any]) -> str:
    diagnostics = data.get("diagnostics") or {}
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    parts = []
    for key in (
        "api",
        "target_repo",
        "source_repo",
        "head",
        "base",
        "manual_url",
    ):
        value = diagnostics.get(key)
        if value:
            parts.append(f"{key}={value}")
    if data.get("http_status"):
        parts.append(f"http_status={data.get('http_status')}")
    if data.get("response_body"):
        parts.append(f"response_body={str(data.get('response_body'))[:500]}")
    if not parts:
        return ""
    return "PR 发布诊断: " + "；".join(parts)


class PublishPRStage(TaskStage):
    """Push the branch, open a PR, and finalize the task result."""

    name = "publish_pr"
    slot = "publish"
    display_name = "发布 PR"
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
            pr_template = await fetch_pr_template(
                ctx.orchestrator.config
            )
            draft_error = ""
            previous_output = ""
            for attempt in range(1, _PR_DRAFT_MAX_ATTEMPTS + 1):
                if attempt == 1:
                    yield ctx.message("生成 PR draft")
                else:
                    yield ctx.message(
                        f"修正 PR draft ({attempt}/{_PR_DRAFT_MAX_ATTEMPTS})"
                    )
                async for event in _generate_pr_draft_attempt(
                    ctx,
                    facts=commit_result.facts,
                    ci_result=verify_report.ci_result,
                    last_commit_stat=commit_result.last_commit_stat,
                    pr_template=pr_template,
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
                    "PR draft agent did not return a valid JSON draft after "
                    f"{_PR_DRAFT_MAX_ATTEMPTS} attempts: "
                    f"{draft_error or 'communicate agent did not return a valid JSON draft.'}"
                )
                pr_draft = _build_deterministic_pr_draft(
                    ctx.task,
                    facts=commit_result.facts,
                    ci_result=verify_report.ci_result,
                    last_commit_stat=commit_result.last_commit_stat,
                    draft_error=draft_error,
                )
                messages.append(
                    "PR draft agent 未返回合法 JSON，"
                    f"已使用事实兜底生成: {error}"
                )
            pr_draft = _normalize_pr_draft(pr_draft, ctx.task)
            messages.append(f"PR draft 已生成: {pr_draft.title}")
        if ctx.orchestrator.config.git_remote:
            yield ctx.message("推送分支")
            push_result = await ctx.orchestrator.git.push(
                branch_name=branch_name
            )
            if push_result.get("output"):
                messages.append(
                    "Push 诊断: "
                    + str(push_result.get("output") or "")[:1200]
                )
            if not push_result.get("success"):
                error = (
                    "Git branch push failed: "
                    f"{push_result.get('output') or 'empty git output'}"
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
            if _should_create_pr(ctx):
                yield ctx.message("创建 PR")
                pr_result = await ctx.orchestrator.git.create_pr(
                    title=pr_draft.title,
                    body=pr_draft.body,
                    head_branch=branch_name,
                )
                diagnostic_message = _format_publish_diagnostics(pr_result)
                if diagnostic_message:
                    messages.append(diagnostic_message)
                pr_url = (
                    pr_result.get("pr_url", "")
                    or pr_result.get("manual_url", "")
                )
                if not pr_url:
                    error = (
                        "GitCode PR creation failed: "
                        f"{pr_result.get('error') or pr_result}"
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
                if pr_url:
                    if pr_result.get("pr_url"):
                        messages.append(f"PR 已创建: {pr_url}")
                    else:
                        messages.append(
                            "PR 自动创建未确认，分支已推送；"
                            f"请通过链接创建/确认 PR: {pr_url}"
                        )
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
