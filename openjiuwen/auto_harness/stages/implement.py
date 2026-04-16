# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Implement 阶段 — worktree 中执行代码修改 + CI + fix loop。

从 orchestrator 提取的最复杂阶段，包含：
- agent 代码修改
- CI 门禁检查
- fix loop 流式桥接
- commit / push / PR 创建
"""

from __future__ import annotations

import asyncio
import logging
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    List,
)

from openjiuwen.auto_harness.infra.commit_guard import (
    derive_allowed_files,
)
from openjiuwen.auto_harness.infra.commit_scope import (
    derive_legacy_related_test_files,
    extract_verify_related_files,
    is_derived_test_file,
)
from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    CommitFacts,
    CycleResult,
    Experience,
    ExperienceType,
    OptimizationTask,
    TaskStatus,
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
    from openjiuwen.auto_harness.experience.experience_store import (
        ExperienceStore,
    )
    from openjiuwen.auto_harness.rails.edit_safety_rail import (
        EditSafetyRail,
    )
    from openjiuwen.auto_harness.tools.commit_tool import (
        CommitTool,
    )
    from openjiuwen.harness.deep_agent import DeepAgent

logger = logging.getLogger(__name__)


def _summarize_text(
    text: str,
    *,
    max_lines: int = 6,
    max_chars: int = 400,
) -> str:
    """压缩长文本，便于在流式 UI 中展示。"""
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
    """格式化 CI 检查结果为用户可见摘要。"""
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
    """将 CI 结果压缩为 evaluator 可消费的状态摘要。"""
    gates = ci_result.get("gates", [])
    if not gates:
        errors = _summarize_text(
            ci_result.get("errors", "")
        ) or "未执行任何门禁"
        return (
            "结论: blocking failure\n"
            f"详情: {errors}"
        )

    lines = [
        (
            "结论: pass"
            if ci_result.get("passed")
            else "结论: blocking failure"
        )
    ]
    for gate in gates:
        status = (
            "PASS" if gate.get("passed") else "FAIL"
        )
        detail = _summarize_text(
            gate.get("output", "")
        )
        line = (
            f"- {gate.get('name', 'unknown')}: {status}"
        )
        if detail and not gate.get("passed"):
            line = f"{line} | {detail}"
        lines.append(line)
    return "\n".join(lines)


class _CIResult:
    """fix_loop ci_runner 返回值适配。"""

    def __init__(
        self, passed: bool, errors: str,
    ) -> None:
        self.passed = passed
        self.errors = errors


class _EvalResult:
    """fix_loop evaluator 返回值适配。"""

    def __init__(
        self, approved: bool, feedback: str = "",
    ) -> None:
        self.approved = approved
        self.feedback = feedback


async def run_implement_stream(
    agent: "DeepAgent | None",
    task: OptimizationTask,
    related: List[Experience],
) -> AsyncIterator[Any]:
    """调用 agent.stream() 执行代码修改。

    Args:
        agent: DeepAgent 实例，为 None 时跳过。
        task: 当前任务。
        related: 相关经验记录。

    Yields:
        OutputSchema chunks from agent.
    """
    if agent is None:
        logger.warning("No agent, skipping implement")
        return

    context_parts: List[str] = []
    for exp in related:
        context_parts.append(
            f"- [{exp.type.value}] {exp.topic}: "
            f"{exp.summary}"
        )
    context = "\n".join(context_parts) or "无"

    prompt = (
        f"任务: {task.topic}\n"
        f"描述: {task.description}\n"
        f"目标文件: "
        f"{', '.join(task.files) or '自行判断'}\n"
        f"\n相关经验:\n{context}\n"
        f"\n请直接修改代码完成任务。"
    )

    async for chunk in agent.stream(
        {"query": prompt},
    ):
        yield chunk


def _build_commit_prompt(
    task: OptimizationTask,
    facts: CommitFacts,
    *,
    retry_reason: str = "",
) -> str:
    """Build the second-round commit prompt for the agent."""
    retry_text = (
        f"\n上一次提交尝试失败原因:\n{retry_reason}\n"
        if retry_reason else ""
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
        f"{retry_text}\n"
        "请基于 implement skill 生成提交计划，并且只能通过 commit_tool 执行提交。"
    )


async def _collect_commit_facts(
    task: OptimizationTask,
    git: "GitOperations",
    edit_safety_rail: "EditSafetyRail",
    *,
    preexisting_dirty_files: List[str],
    ci_result: dict[str, Any] | None,
    fix_errors: str,
) -> CommitFacts:
    """Collect commit facts after verify/fix has completed."""
    status = await git.collect_status()
    edited_files = edit_safety_rail.edited_files()
    verify_related_files = extract_verify_related_files(
        ci_result,
        fix_errors,
    )
    derived_test_files = [
        path
        for path in edited_files
        if path in status["dirty_files"]
        and is_derived_test_file(task.files, path)
    ]
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
    facts.allowed_files = derive_allowed_files(facts)
    return facts


async def _run_commit_round(
    *,
    agent: "DeepAgent | None",
    task: OptimizationTask,
    git: "GitOperations",
    facts: CommitFacts,
    commit_tool: "CommitTool",
    msg_factory: Any,
    retry_reason: str = "",
) -> tuple[bool, str, list[Any]]:
    """Ask the agent to produce and execute a guarded commit."""
    if agent is None:
        return False, "No agent available for commit phase.", []

    before_head = await git.current_head()
    commit_tool.last_output = None
    chunks: list[Any] = []
    async for chunk in agent.stream(
        {"query": _build_commit_prompt(
            task,
            facts,
            retry_reason=retry_reason,
        )}
    ):
        chunks.append(chunk)

    after_head = await git.current_head()
    if after_head != before_head:
        return True, "", chunks

    reason = "Agent did not create a commit via commit_tool."
    if commit_tool.last_output is not None:
        reason = (
            commit_tool.last_output.error
            or reason
        )
    return False, reason, chunks


async def run_in_worktree_stream(
    config: AutoHarnessConfig,
    task: OptimizationTask,
    related: List[Experience],
    *,
    agent: "DeepAgent | None",
    git: "GitOperations",
    ci_gate: "CIGateRunner",
    fix_loop: "FixLoopController",
    experience_store: "ExperienceStore",
    edit_safety_rail: "EditSafetyRail",
    preexisting_dirty_files: List[str],
    commit_facts_ref: List[CommitFacts],
    commit_tool: "CommitTool",
    msg_factory: Any,
    result_holder: List[CycleResult],
) -> AsyncIterator[Any]:
    """在 worktree 中执行全流程。

    implement -> CI -> fix -> commit -> push -> PR。

    Args:
        config: Auto Harness 配置。
        task: 当前任务。
        related: 相关经验记录。
        agent: DeepAgent 实例。
        git: GitOperations 实例。
        ci_gate: CIGateRunner 实例。
        fix_loop: FixLoopController 实例。
        experience_store: ExperienceStore 实例。
        msg_factory: 构造 OutputSchema message 的可调用对象。
        result_holder: 单元素列表，用于传出 CycleResult。

    Yields:
        OutputSchema chunks。
    """
    fix_errors = ""

    # [1/5] 执行代码修改
    yield msg_factory("[1/5] 执行代码修改")
    async for chunk in run_implement_stream(
        agent, task, related,
    ):
        yield chunk

    # [2/5] CI 门禁检查
    yield msg_factory("[2/5] CI 门禁检查")
    ci_result = await ci_gate.run("all")
    for message in _iter_ci_gate_messages(ci_result):
        yield msg_factory(message)

    if not ci_result.get("passed"):
        # [3/5] 修复循环
        yield msg_factory(
            "[3/5] CI 未通过，启动修复循环"
        )
        fix_task, chunk_queue, fix_done = _start_fix_loop(
            config=config,
            task=task,
            agent=agent,
            git=git,
            ci_gate=ci_gate,
            fix_loop_ctrl=fix_loop,
            msg_factory=msg_factory,
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
            yield msg_factory("修复失败，回滚变更")
            await git._git("checkout", ".")
            task.status = TaskStatus.REVERTED
            await experience_store.record(Experience(
                type=ExperienceType.FAILURE,
                topic=task.topic,
                summary="fix loop failed",
                outcome="reverted",
                details="\n".join(
                    fix_res.error_log[-3:]
                ),
            ))
            result_holder.append(CycleResult(
                reverted=True,
                error_log="\n".join(
                    fix_res.error_log
                ),
            ))
            return
        fix_errors = "\n".join(
            fix_res.error_log
        )

    # [4/5] 收集提交事实并提交
    yield msg_factory("[4/5] 收集提交事实")
    facts = await _collect_commit_facts(
        task,
        git,
        edit_safety_rail,
        preexisting_dirty_files=preexisting_dirty_files,
        ci_result=ci_result,
        fix_errors=fix_errors,
    )
    commit_facts_ref[0] = facts

    yield msg_factory("[5/5] 生成提交计划并提交")
    commit_ok, reason, commit_chunks = await _run_commit_round(
        agent=agent,
        task=task,
        git=git,
        facts=facts,
        commit_tool=commit_tool,
        msg_factory=msg_factory,
    )
    for chunk in commit_chunks:
        yield chunk
    if not commit_ok:
        yield msg_factory(
            f"提交失败，重试一次: {reason}"
        )
        commit_ok, reason, commit_chunks = await _run_commit_round(
            agent=agent,
            task=task,
            git=git,
            facts=facts,
            commit_tool=commit_tool,
            msg_factory=msg_factory,
            retry_reason=reason,
        )
        for chunk in commit_chunks:
            yield chunk

    if not commit_ok:
        yield msg_factory(f"提交失败: {reason}")
        task.status = TaskStatus.FAILED
        await experience_store.record(Experience(
            type=ExperienceType.FAILURE,
            topic=task.topic,
            summary="commit failed",
            outcome="failed",
            details=reason,
            files_changed=(
                commit_tool.last_output.data.get(
                    "committed_files", []
                )
                if (
                    commit_tool.last_output
                    and isinstance(
                        commit_tool.last_output.data,
                        dict,
                    )
                )
                else []
            ),
        ))
        result_holder.append(CycleResult(
            success=False,
            error=reason,
        ))
        return

    branch_name = facts.branch_name
    pr_url = ""
    if config.git_remote:
        await git.push(branch_name=branch_name)

        # push 后创建 PR
        if config.fork_owner:
            yield msg_factory("[后置] 创建 PR")
            pr_result = await git.create_pr(
                title=(
                    f"auto-harness: {task.topic}"
                ),
                body=(
                    "Auto-harness 自动优化\n\n"
                    f"任务: {task.topic}\n"
                    f"{task.description}"
                ),
                head_branch=branch_name,
            )
            pr_url = pr_result.get("pr_url", "")

    task.status = TaskStatus.SUCCESS
    await experience_store.record(Experience(
        type=ExperienceType.OPTIMIZATION,
        topic=task.topic,
        summary=f"completed: {task.topic}",
        outcome="success",
        pr_url=pr_url,
    ))
    result_holder.append(CycleResult(
        success=True, pr_url=pr_url,
    ))
    yield msg_factory(
        f"任务完成: {pr_url}" if pr_url
        else "任务完成（本地提交）"
    )


def _start_fix_loop(
    *,
    config: AutoHarnessConfig,
    task: OptimizationTask,
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
        async for c in agent.stream(
            {"query": prompt},
        ):
            await chunk_queue.put(c)

    async def _ci_runner() -> _CIResult:
        attempt_state["ci"] += 1
        await _emit_message(
            f"[修复循环] 第 {attempt_state['ci']} 次重跑 CI"
        )
        r = await ci_gate.run("all")
        for message in _iter_ci_gate_messages(
            r, prefix="[修复循环] "
        ):
            await _emit_message(message)
        return _CIResult(
            passed=r.get("passed", False),
            errors=r.get("errors", ""),
        )

    async def _evaluator() -> _EvalResult:
        attempt_state["eval"] += 1
        await _emit_message(
            f"[修复循环] 进入评审阶段，第 "
            f"{attempt_state['eval']} 次评审"
        )
        from openjiuwen.auto_harness.agent import (
            create_eval_agent,
        )

        eval_agent = create_eval_agent(config)
        _, diff = await git._git("diff", "HEAD~1")
        ci_result = await ci_gate.run("all")
        query = (
            f"任务描述: {task.description}\n\n"
            f"代码变更:\n{diff[:5000]}\n\n"
            "CI 状态:\n"
            f"{_format_ci_status_for_evaluator(ci_result)}\n\n"
            "请评审这些变更。"
        )
        output = ""
        async for c in eval_agent.stream(
            {"query": query},
        ):
            await chunk_queue.put(c)
            output += extract_text(c)
        approved = (
            "verdict: pass" in output.lower()
        )
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
