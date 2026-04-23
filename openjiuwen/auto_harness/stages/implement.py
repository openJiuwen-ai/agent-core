# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Implement stage for task-scoped code changes."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.auto_harness.contexts import (
    TaskContext,
)
from openjiuwen.auto_harness.infra.edit_scope import (
    is_allowed_repo_edit_path,
    normalize_repo_path,
    render_edit_scope,
)
from openjiuwen.auto_harness.schema import (
    CodeChangeArtifact,
    CycleResult,
    Experience,
    OptimizationTask,
    StageResult,
    TaskStatus,
)
from openjiuwen.auto_harness.stages.base import (
    TaskStage,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import (
        DeepAgent,
    )
    from openjiuwen.core.session.agent import (
        Session,
    )

logger = logging.getLogger(__name__)


def _build_implement_prompt(
    task: OptimizationTask,
    related: list[Experience],
) -> str:
    """Build the implement-stage prompt."""
    context_parts: list[str] = []
    for exp in related:
        context_parts.append(
            f"- [{exp.type.value}] {exp.topic}: {exp.summary}"
        )
    context = "\n".join(context_parts) or "无"
    edit_scope = render_edit_scope(
        "本轮实现阶段允许改动的路径"
    )
    return (
        f"任务: {task.topic}\n"
        f"描述: {task.description}\n"
        f"目标文件: {', '.join(task.files) or '自行判断'}\n"
        f"\n相关经验:\n{context}\n"
        f"\n{edit_scope}\n"
        "\n本阶段只允许完成代码修改与局部验证。"
        "\n默认直接开始实施修改，不要等待人工确认。"
        "\n禁止输出“是否需要我开始实现”“如果需要请指示”“是否继续”之类的回问；"
        "除非存在明确范围冲突、缺少关键输入或必须越界编辑，否则必须直接动手修改代码。"
        "\n如果 `task.files` 包含范围外路径，或你判断必须修改范围外文件才能完成任务，"
        "立即停止并明确报告，不要尝试越界编辑。"
        "\n严禁执行 git add、git commit 或其他提交动作；"
        "提交只允许在后续独立 commit phase 中进行。"
    )


def _build_prompt_debug_stats(
    prompt: str,
) -> dict[str, int]:
    """Return lightweight prompt size stats for timeout diagnosis."""
    return {
        "chars": len(prompt),
        "lines": prompt.count("\n") + 1,
        "bytes": len(prompt.encode("utf-8")),
    }


def _extract_repo_edit_candidates(
    *,
    status_text: str,
    diff_files: list[str],
    preexisting_dirty_files: list[str] | None = None,
) -> list[str]:
    """Extract actual repo edits from git status/diff outputs."""
    files: list[str] = []
    preexisting: set[str] = set()
    for path in preexisting_dirty_files or []:
        normalized = normalize_repo_path(path)
        if normalized:
            preexisting.add(normalized)
    for line in status_text.splitlines():
        raw = line.rstrip()
        if len(raw) < 3:
            continue
        path = ""
        if raw.startswith("?? "):
            path = raw[3:].strip()
        elif len(raw) >= 4 and raw[2] == " ":
            path = raw[3:].strip()
        elif len(raw) >= 3 and raw[1] == " ":
            # Compat: some callers may strip the leading space from
            # the first porcelain line (" M foo" -> "M foo").
            path = raw[2:].strip()
        if not path:
            continue
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        normalized = normalize_repo_path(path)
        if normalized:
            files.append(normalized)
    for path in diff_files:
        normalized = normalize_repo_path(path)
        if normalized:
            files.append(normalized)
    filtered_files: list[str] = []
    for path in dict.fromkeys(files):
        if not is_allowed_repo_edit_path(path):
            continue
        if path in preexisting:
            continue
        filtered_files.append(path)
    return filtered_files


def _summarize_text(
    text: str,
    *,
    max_lines: int = 6,
    max_chars: int = 400,
) -> str:
    if not text:
        return ""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]
    summary = "\n".join(lines[:max_lines]).strip()
    if len(summary) > max_chars:
        return f"{summary[: max_chars - 3].rstrip()}..."
    if len(lines) > max_lines:
        return f"{summary}\n..."
    return summary


def _extract_controller_task_failed_error(
    chunk: Any,
) -> str:
    """Return task-loop failure text from a controller_output chunk."""
    if getattr(chunk, "type", "") != "controller_output":
        return ""

    payload = getattr(chunk, "payload", None)
    if payload is None:
        return ""

    if isinstance(payload, dict):
        payload_type = payload.get("type", "")
        payload_data = payload.get("data", [])
    else:
        payload_type = getattr(payload, "type", "")
        payload_data = getattr(payload, "data", [])

    if str(payload_type).lower() != "task_failed":
        return ""

    texts: list[str] = []
    if isinstance(payload_data, list):
        for item in payload_data:
            if isinstance(item, dict):
                text = item.get("text", "")
            else:
                text = getattr(item, "text", "")
            text = str(text).strip()
            if text:
                texts.append(text)

    if texts:
        return "\n".join(texts)
    return str(payload).strip()


def _format_ci_status_for_evaluator(
    ci_result: dict[str, Any],
) -> str:
    """Build the evaluator-facing CI summary.

    Keep this helper in implement.py for backwards-compatible imports
    used by older tests and downstream callers.
    """
    gates = ci_result.get("gates", [])
    if not gates:
        errors = _summarize_text(
            ci_result.get("errors", "")
        ) or "未执行任何门禁"
        return f"结论: blocking failure\n详情: {errors}"
    lines = [
        "结论: pass"
        if ci_result.get("passed")
        else "结论: blocking failure"
    ]
    for gate in gates:
        status = "PASS" if gate.get("passed") else "FAIL"
        detail = _summarize_text(
            gate.get("output", "")
        )
        line = f"- {gate.get('name', 'unknown')}: {status}"
        if detail and not gate.get("passed"):
            line = f"{line} | {detail}"
        lines.append(line)
    return "\n".join(lines)


async def run_implement_stream(
    agent: "DeepAgent | None",
    task: OptimizationTask,
    related: list[Experience],
    session: "Session | None" = None,
    prompt: str | None = None,
) -> AsyncIterator[Any]:
    """Stream task implementation through the task agent."""
    if agent is None:
        logger.warning("No agent, skipping implement")
        return
    effective_prompt = prompt or _build_implement_prompt(
        task,
        related,
    )
    if session is None:
        async for chunk in agent.stream(
            {"query": effective_prompt}
        ):
            yield chunk
        return

    await session.pre_run(
        inputs={"query": effective_prompt}
    )
    try:
        async for chunk in agent.stream(
            {"query": effective_prompt},
            session=session,
        ):
            yield chunk
    finally:
        await session.post_run()


class ImplementStage(TaskStage):
    """Execute code changes for the current task."""

    name = "implement"
    description = "Run the implement stage for PR pipeline."
    produces = ["code_change"]

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        implement_error = ""
        prompt = _build_implement_prompt(
            ctx.task,
            ctx.runtime.related,
        )
        prompt_stats = _build_prompt_debug_stats(prompt)
        started_at = datetime.now(
            timezone.utc
        ).isoformat()
        start_monotonic = time.monotonic()
        model_timeout_secs = float(
            getattr(
                getattr(
                    ctx.orchestrator,
                    "config",
                    None,
                ),
                "model_timeout_secs",
                0.0,
            )
            or 0.0
        )
        logger.info(
            "Implement LLM call starting: task=%s, started_at=%s, "
            "prompt_chars=%d, prompt_lines=%d, prompt_bytes=%d, "
            "model_timeout_secs=%.1f",
            ctx.task.topic,
            started_at,
            prompt_stats["chars"],
            prompt_stats["lines"],
            prompt_stats["bytes"],
            model_timeout_secs,
        )
        yield ctx.message(
            f"任务准备就绪: {ctx.task.topic}"
        )
        yield ctx.message("[1/5] 执行代码修改")
        async for chunk in run_implement_stream(
            ctx.runtime.task_agent,
            ctx.task,
            ctx.runtime.related,
            session=ctx.runtime.task_session,
            prompt=prompt,
        ):
            yield chunk
            implement_error = (
                _extract_controller_task_failed_error(
                    chunk
                )
            )
            if implement_error:
                break
        if implement_error:
            elapsed_secs = time.monotonic() - start_monotonic
            implement_error = (
                "Implement model call failed after "
                f"{elapsed_secs:.1f}s "
                f"(started_at={started_at}, "
                f"prompt_chars={prompt_stats['chars']}, "
                f"prompt_lines={prompt_stats['lines']}, "
                f"prompt_bytes={prompt_stats['bytes']}, "
                f"model_timeout_secs={model_timeout_secs:.1f}).\n"
                f"{implement_error}"
            )
            ctx.task.status = TaskStatus.FAILED
            yield StageResult(
                status="failed",
                artifacts={
                    "code_change": CodeChangeArtifact(
                        related=ctx.runtime.related,
                        edited_files=[],
                    ),
                    "task_result": CycleResult(
                        success=False,
                        error=implement_error,
                    ),
                },
                messages=[implement_error],
                error=implement_error,
            )
            return
        logger.info(
            "Implement LLM call finished: task=%s, elapsed_secs=%.1f, "
            "prompt_chars=%d, prompt_lines=%d, prompt_bytes=%d",
            ctx.task.topic,
            time.monotonic() - start_monotonic,
            prompt_stats["chars"],
            prompt_stats["lines"],
            prompt_stats["bytes"],
        )
        status_text = (
            await ctx.orchestrator.git.status_porcelain()
        )
        diff_files = await ctx.orchestrator.git.diff_name_only(
            "HEAD"
        )
        edited_files = _extract_repo_edit_candidates(
            status_text=status_text,
            diff_files=diff_files,
            preexisting_dirty_files=(
                ctx.runtime.preexisting_dirty_files
            ),
        )
        if not edited_files:
            error = (
                "Implement phase finished without any code edits. "
                "No allowed repo file was changed according to git status/diff."
            )
            ctx.task.status = TaskStatus.FAILED
            yield StageResult(
                status="failed",
                artifacts={
                    "code_change": CodeChangeArtifact(
                        related=ctx.runtime.related,
                        edited_files=[],
                    ),
                    "task_result": CycleResult(
                        success=False,
                        error=error,
                    ),
                },
                messages=[error],
                error=error,
            )
            return
        yield StageResult(
            artifacts={
                "code_change": CodeChangeArtifact(
                    related=ctx.runtime.related,
                    edited_files=edited_files,
                )
            }
        )
