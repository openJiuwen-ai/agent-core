# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Verify stage for auto-harness pipelines."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from typing import Any, AsyncIterator

from openjiuwen.auto_harness.agents import (
    create_eval_agent,
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
    CycleResult,
    Experience,
    ExperienceType,
    StageResult,
    TaskStatus,
    VerifyReportArtifact,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.infra.ci_gate_runner import (
        CIGateRunner,
    )
    from openjiuwen.auto_harness.infra.fix_loop import (
        FixLoopController,
    )
    from openjiuwen.auto_harness.infra.git_operations import (
        GitOperations,
    )
    from openjiuwen.auto_harness.schema import (
        AutoHarnessConfig,
        OptimizationTask,
    )
    from openjiuwen.harness.deep_agent import (
        DeepAgent,
    )


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


def _iter_ci_gate_messages(
    ci_result: dict[str, Any],
    *,
    prefix: str = "",
) -> list[str]:
    gates = ci_result.get("gates", [])
    if not gates:
        errors = _summarize_text(
            ci_result.get("errors", "")
        ) or "未匹配到任何 CI 门禁"
        return [f"{prefix}CI 检查未执行: {errors}"]
    summary = ", ".join(
        (
            f"{gate.get('name', 'unknown')}="
            f"{'PASS' if gate.get('passed') else 'FAIL'}"
        )
        for gate in gates
    )
    messages = [f"{prefix}CI 结果: {summary}"]
    for gate in gates:
        if gate.get("passed"):
            continue
        detail = _summarize_text(
            gate.get("output", "")
        ) or "无错误输出"
        messages.append(
            f"{prefix}[{gate.get('name', 'unknown')}] {detail}"
        )
    return messages


def _format_ci_status_for_evaluator(
    ci_result: dict[str, Any],
) -> str:
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


class _CIResult:
    """fix_loop ci_runner 返回值适配。"""

    def __init__(
        self,
        passed: bool,
        errors: str,
    ) -> None:
        self.passed = passed
        self.errors = errors


class _EvalResult:
    """fix_loop evaluator 返回值适配。"""

    def __init__(
        self,
        approved: bool,
        feedback: str = "",
    ) -> None:
        self.approved = approved
        self.feedback = feedback


def _start_fix_loop(
    *,
    config: "AutoHarnessConfig",
    task: "OptimizationTask",
    agent: "DeepAgent | None",
    git: "GitOperations",
    ci_gate: "CIGateRunner",
    fix_loop_ctrl: "FixLoopController",
    msg_factory: Any,
) -> tuple[asyncio.Task[Any], asyncio.Queue[Any], asyncio.Event]:
    """启动 fix loop 任务，并返回流式输出通道。"""
    chunk_queue: asyncio.Queue[Any] = asyncio.Queue()
    attempt_state = {
        "ci": 0,
        "fix": 0,
        "eval": 0,
    }

    async def _emit_message(text: str) -> None:
        await chunk_queue.put(msg_factory(text))

    async def _fixer(errors: str) -> None:
        attempt_state["fix"] += 1
        await _emit_message(
            f"[修复循环] 第 {attempt_state['fix']} 次修复"
        )
        detail = _summarize_text(errors)
        if detail:
            await _emit_message(
                f"[修复循环] 修复目标:\n{detail}"
            )
        if agent is None:
            return
        prompt = (
            "CI 检查失败，请修复以下错误:\n"
            f"{errors[:3000]}"
        )
        async for chunk in agent.stream({"query": prompt}):
            await chunk_queue.put(chunk)

    async def _ci_runner() -> _CIResult:
        attempt_state["ci"] += 1
        await _emit_message(
            f"[修复循环] 第 {attempt_state['ci']} 次重跑 CI"
        )
        result = await ci_gate.run("all")
        for message in _iter_ci_gate_messages(
            result,
            prefix="[修复循环] ",
        ):
            await _emit_message(message)
        return _CIResult(
            passed=result.get("passed", False),
            errors=result.get("errors", ""),
        )

    async def _evaluator() -> _EvalResult:
        attempt_state["eval"] += 1
        await _emit_message(
            f"[修复循环] 进入评审阶段，第 {attempt_state['eval']} 次评审"
        )
        eval_agent = create_eval_agent(config)
        diff = await git.diff_against("HEAD~1")
        ci_result = await ci_gate.run("all")
        query = (
            f"任务描述: {task.description}\n\n"
            f"代码变更:\n{diff[:5000]}\n\n"
            "CI 状态:\n"
            f"{_format_ci_status_for_evaluator(ci_result)}\n\n"
            "请评审这些变更。"
        )
        output = ""
        async for chunk in eval_agent.stream({"query": query}):
            await chunk_queue.put(chunk)
            output += extract_text(chunk)
        approved = "verdict: pass" in output.lower()
        await _emit_message(
            "[修复循环] 评审结果: "
            f"{'PASS' if approved else 'REJECT'}"
        )
        return _EvalResult(
            approved=approved,
            feedback=output,
        )

    fix_done = asyncio.Event()

    async def _run_fix() -> Any:
        try:
            fix_result = await fix_loop_ctrl.run(
                ci_runner=_ci_runner,
                agent_fixer=_fixer,
                evaluator=_evaluator,
            )
            await _emit_message(
                "[修复循环] "
                f"{'修复成功' if fix_result.success else '修复耗尽'}"
            )
            return fix_result.success, fix_result
        finally:
            fix_done.set()

    fix_task = asyncio.create_task(_run_fix())
    return fix_task, chunk_queue, fix_done


class VerifyStage(TaskStage):
    """Run CI and the fix loop for the current task."""

    name = "verify"
    description = "Run CI/fix loop verification."
    consumes = ["code_change"]
    produces = ["verify_report"]

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        ci_result = await ctx.orchestrator.ci_gate.run("all")
        messages: list[str] = []
        yield ctx.message("[2/5] CI 门禁检查")
        for message in _iter_ci_gate_messages(ci_result):
            messages.append(message)
            yield ctx.message(message)

        fix_errors = ""
        if not ci_result.get("passed"):
            messages.append("[3/5] CI 未通过，启动修复循环")
            yield ctx.message("[3/5] CI 未通过，启动修复循环")
            fix_task, chunk_queue, fix_done = _start_fix_loop(
                config=ctx.orchestrator.config,
                task=ctx.task,
                agent=(
                    ctx.runtime.fix_agent
                    or ctx.runtime.task_agent
                ),
                git=ctx.orchestrator.git,
                ci_gate=ctx.orchestrator.ci_gate,
                fix_loop_ctrl=ctx.orchestrator.fix_loop,
                msg_factory=ctx.orchestrator.message_output,
            )
            while not fix_done.is_set() or not chunk_queue.empty():
                try:
                    chunk = await asyncio.wait_for(
                        chunk_queue.get(),
                        timeout=0.5,
                    )
                    yield chunk
                except asyncio.TimeoutError:
                    continue
            fix_ok, fix_res = await fix_task
            if not fix_ok:
                await ctx.orchestrator.git.discard_worktree_changes()
                ctx.task.status = TaskStatus.REVERTED
                error_log = "\n".join(fix_res.error_log)
                await ctx.orchestrator.experience_store.record(
                    Experience(
                        type=ExperienceType.FAILURE,
                        topic=ctx.task.topic,
                        summary="fix loop failed",
                        outcome="reverted",
                        details="\n".join(
                            fix_res.error_log[-3:]
                        ),
                    )
                )
                result = CycleResult(
                    reverted=True,
                    error_log=error_log,
                )
                yield StageResult(
                    status="failed",
                    artifacts={
                        "verify_report": VerifyReportArtifact(
                            ci_result=ci_result,
                            reverted=True,
                            error=error_log,
                        ),
                        "task_result": result,
                    },
                    messages=messages + ["修复失败，回滚变更"],
                    error=error_log,
                )
                return
            fix_errors = "\n".join(fix_res.error_log)
        yield StageResult(
            artifacts={
                "verify_report": VerifyReportArtifact(
                    ci_result=ci_result,
                    fix_errors=fix_errors,
                )
            },
            messages=messages,
        )
