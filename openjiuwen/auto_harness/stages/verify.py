# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Verify stage for auto-harness pipelines."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any, AsyncIterator

from openjiuwen.auto_harness.agents import (
    create_eval_agent,
)
from openjiuwen.auto_harness.contexts import (
    TaskContext,
)
from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
)
from openjiuwen.auto_harness.infra.runtime_extension_loader import (
    load_runtime_rails,
    load_runtime_skill_dirs,
    load_runtime_tools,
)
from openjiuwen.auto_harness.schema import (
    CycleResult,
    Experience,
    ExperienceType,
    ExtensionBuildArtifact,
    RuntimeExtensionArtifact,
    StageResult,
    TaskStatus,
    VerifyReportArtifact,
)
from openjiuwen.auto_harness.stages.base import (
    TaskStage,
    scope_output_event_stage,
)
from openjiuwen.auto_harness.stages.implement import (
    promote_runtime,
)

logger = logging.getLogger(__name__)

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


@dataclass
class _ExtStaticCheckResult:
    """Static verification counts and errors for an extension."""

    errors: list[str] | None = None
    rails_count: int = 0
    tools_count: int = 0
    skills_count: int = 0
    skill_dirs_count: int = 0

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


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
    extra_rails: list | None = None,
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
        eval_agent = create_eval_agent(
            config,
            extra_rails=extra_rails,
        )
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
    """Abstract base for all verify stages."""

    name = "verify"
    slot = "verify"
    display_name = "CI 门禁检查"
    description = "Verify code changes."
    produces = ["verify_report"]

    async def stream(
        self,
        ctx: TaskContext,
    ) -> Any:
        raise NotImplementedError


class MetaVerifyStage(VerifyStage):
    """Run CI and the fix loop for the current task."""

    consumes = ["code_change"]

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        ci_result = await ctx.orchestrator.ci_gate.run("all")
        messages: list[str] = []
        for message in _iter_ci_gate_messages(ci_result):
            messages.append(message)
            yield ctx.message(message)

        fix_errors = ""
        if not ci_result.get("passed"):
            messages.append("CI 未通过，启动修复循环")
            yield ctx.message("CI 未通过，启动修复循环")
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
                extra_rails=ctx.orchestrator.stream_rails or None,
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


class ExtendVerifyStage(VerifyStage):
    """Validate manifest, imports, lint, and constructors."""

    name = "verify_ext"
    display_name = "验证扩展"
    consumes = ["extension_build"]
    produces = ["extension_build", "verify_report"]

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        build = ctx.require_artifact("extension_build")
        if not isinstance(build, ExtensionBuildArtifact):
            raise TypeError(
                "extension_build artifact must be "
                "ExtensionBuildArtifact"
            )

        static_result = _ExtStaticCheckResult()
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            static_result = await _run_ext_static_checks(
                ctx=ctx,
                build=build,
            )
            if not static_result.errors:
                break
            if ctx.runtime.task_agent is None or attempt >= max_attempts:
                break
            error_text = "; ".join(static_result.errors)
            yield ctx.message(
                "[verify_ext] 结构/静态校验失败，修复扩展实现后重试\n"
                f"{_summarize_text(error_text, max_chars=800)}"
            )
            prompt = _build_ext_static_fix_prompt(
                build=build,
                static_errors=error_text,
            )
            async for chunk in _stream_verify_ext_agent_turn(
                ctx.runtime.task_agent,
                prompt,
                session_id_prefix=(
                    f"verify-ext-{build.extension_name}-static-fix"
                ),
            ):
                yield scope_output_event_stage(
                    chunk,
                    "verify_ext",
                )

        if static_result.errors:
            error_text = "; ".join(static_result.errors)
            yield StageResult(
                status="failed",
                artifacts={
                    "verify_report": VerifyReportArtifact(
                        ci_result={
                            "passed": False,
                            "rails": static_result.rails_count,
                            "tools": static_result.tools_count,
                            "skills": static_result.skills_count,
                        },
                        error=error_text,
                    ),
                    "task_result": CycleResult(
                        success=False,
                        error=(
                            "Extension verify failed: "
                            f"{error_text}"
                        ),
                        error_log=error_text,
                    ),
                },
                messages=[
                    "Extension verify failed: "
                    f"{error_text}"
                ],
                error=error_text,
            )
            return

        acceptance_ok = True
        acceptance_error = ""
        async for item in _run_agent_generated_ext_acceptance(
            ctx=ctx,
            build=build,
            rails_count=static_result.rails_count,
            tools_count=static_result.tools_count,
            skills_count=static_result.skills_count,
            skill_dirs_count=static_result.skill_dirs_count,
        ):
            if isinstance(item, _CIResult):
                acceptance_ok = item.passed
                acceptance_error = item.errors
                continue
            yield item
        if not acceptance_ok:
            yield StageResult(
                status="failed",
                artifacts={
                    "verify_report": VerifyReportArtifact(
                        ci_result={
                            "passed": False,
                            "rails": static_result.rails_count,
                            "tools": static_result.tools_count,
                            "skills": static_result.skills_count,
                        },
                        error=acceptance_error,
                    ),
                    "task_result": CycleResult(
                        success=False,
                        error=(
                            "Extension acceptance tests failed: "
                            f"{acceptance_error}"
                        ),
                        error_log=acceptance_error,
                    ),
                },
                messages=[
                    "Extension acceptance tests failed: "
                    f"{_summarize_text(acceptance_error, max_chars=600)}"
                ],
                error=acceptance_error,
            )
            return

        runtime_ext = await promote_runtime(ctx)

        yield StageResult(
            artifacts={
                "extension_build": build,
                "verify_report": VerifyReportArtifact(
                    ci_result={
                        "passed": True,
                        "rails": static_result.rails_count,
                        "tools": static_result.tools_count,
                        "skills": static_result.skills_count,
                    },
                ),
                "runtime_extension": runtime_ext,
            },
            messages=[
                "Verified extension scaffold: "
                f"{build.extension_name}"
            ],
        )


async def _run_ext_static_checks(
    *,
    ctx: TaskContext,
    build: ExtensionBuildArtifact,
) -> _ExtStaticCheckResult:
    """Run structure and lint checks for a runtime extension."""
    result = _ExtStaticCheckResult()
    # Layer 1: Structure check — manifest + class instantiation
    try:
        config_path = Path(build.config_path)
        if not config_path.is_file():
            raise FileNotFoundError(
                "Missing extension manifest: "
                f"{config_path}"
            )

        runtime_ext = RuntimeExtensionArtifact(
            extension_name=build.extension_name,
            runtime_path=build.extension_root,
            config_path=build.config_path,
        )
        verify_session_id = (
            f"verify_"
            f"{ctx.orchestrator.runtime.session_id}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        rails = load_runtime_rails(
            runtime_ext,
            session_id=verify_session_id,
        )
        tools = load_runtime_tools(
            runtime_ext,
            session_id=verify_session_id,
        )
        for rail_cls in rails:
            rail_cls()
        for tool_cls in tools:
            tool_cls()
        result.rails_count = len(rails)
        result.tools_count = len(tools)
        skill_dirs = load_runtime_skill_dirs(
            runtime_ext,
        )
        result.skill_dirs_count = len(skill_dirs)
        for sd in skill_dirs:
            sd_path = Path(sd)
            skill_mds = list(
                sd_path.rglob("SKILL.md")
            )
            result.skills_count += len(skill_mds)
            if not skill_mds:
                result.errors.append(
                    "Skill dir has no SKILL.md: "
                    f"{sd}"
                )
    except Exception as exc:
        result.errors.append(
            f"Structure check failed: {exc}"
        )

    # Layer 2: Import check — skipped for now.
    # Generated code uses absolute imports like
    # ``from openjiuwen.extensions.harness.<ext>…`` which
    # cannot resolve in the worktree environment.  The
    # runtime_extension_loader handles this at load time.
    extension_root = Path(build.extension_root)

    # Layer 3: Lint check — ruff on extension root
    if extension_root.is_dir():
        lint_errors = await _check_ruff(
            extension_root,
        )
        result.errors.extend(lint_errors)

    return result


async def _run_agent_generated_ext_acceptance(
    *,
    ctx: TaskContext,
    build: ExtensionBuildArtifact,
    rails_count: int,
    tools_count: int,
    skills_count: int,
    skill_dirs_count: int,
) -> AsyncIterator[Any]:
    """Generate acceptance tests once, then repair code and rerun them."""
    agent = ctx.runtime.task_agent
    if agent is None:
        yield _CIResult(
            passed=False,
            errors="acceptance_test_agent_missing",
        )
        return

    test_dir = (
        Path(ctx.runtime.wt_path)
        / ".auto_harness_verify"
        / build.extension_name
    )
    test_file = test_dir / "test_runtime_extension_acceptance.py"
    python_executable = (
        ctx.orchestrator.config.resolve_ci_gate_python_executable()
    )
    last_error = ""
    max_attempts = 3
    test_generated = False
    for attempt in range(1, max_attempts + 1):
        test_dir.mkdir(parents=True, exist_ok=True)
        if not test_generated:
            yield ctx.message(
                "[verify_ext] 生成 runtime extension 验收测试 "
                f"(attempt {attempt}/{max_attempts})"
            )
            prompt = _build_ext_acceptance_test_prompt(
                build=build,
                test_file=test_file,
                python_executable=python_executable,
                rails_count=rails_count,
                tools_count=tools_count,
                skills_count=skills_count,
                skill_dirs_count=skill_dirs_count,
                previous_error=last_error,
            )
            async for chunk in _stream_verify_ext_agent_turn(
                agent,
                prompt,
                session_id_prefix=(
                    f"verify-ext-{build.extension_name}-generate"
                ),
            ):
                yield scope_output_event_stage(
                    chunk,
                    "verify_ext",
                )
            if not test_file.is_file():
                last_error = (
                    "verify_ext_test_not_generated: "
                    f"expected test file at {test_file}"
                )
            else:
                test_generated = True

        if not test_generated:
            if attempt >= max_attempts:
                break
            continue

        result = await _run_pytest_file(
            python_executable=python_executable,
            test_file=test_file,
            cwd=Path(ctx.runtime.wt_path),
        )
        if result.passed:
            yield ctx.message(
                "[verify_ext] runtime extension 验收测试通过"
            )
            yield result
            return
        last_error = result.errors

        if attempt >= max_attempts:
            break

        yield ctx.message(
            "[verify_ext] 验收测试失败，修复扩展实现后复跑同一测试\n"
            f"{_summarize_text(last_error, max_chars=800)}"
        )
        fix_prompt = _build_ext_acceptance_fix_prompt(
            build=build,
            test_file=test_file,
            pytest_output=last_error,
            python_executable=python_executable,
        )
        async for chunk in _stream_verify_ext_agent_turn(
            agent,
            fix_prompt,
            session_id_prefix=(
                f"verify-ext-{build.extension_name}-fix"
            ),
        ):
            yield scope_output_event_stage(
                chunk,
                "verify_ext",
            )

    yield _CIResult(
        passed=False,
        errors=last_error or "verify_ext_acceptance_failed",
    )


async def _stream_verify_ext_agent_turn(
    agent: "DeepAgent",
    prompt: str,
    *,
    session_id_prefix: str,
) -> AsyncIterator[Any]:
    """Stream one verify_ext agent turn with a fresh stream session.

    DeepAgent task-loop streaming closes the session emitter when a turn
    completes.  verify_ext runs after implement, whose task session has
    already been consumed, so reusing it would silently discard chunks.
    """
    from openjiuwen.core.session.agent import (
        create_agent_session,
    )

    session = create_agent_session(
        session_id=(
            f"{session_id_prefix}-"
            f"{uuid.uuid4().hex[:8]}"
        ),
        card=getattr(agent, "card", None),
        close_stream_on_post_run=False,
    )
    await session.pre_run(inputs={"query": prompt})
    try:
        async for chunk in agent.stream(
            {"query": prompt},
            session=session,
        ):
            yield chunk
    finally:
        await session.post_run()


def _build_ext_acceptance_test_prompt(
    *,
    build: ExtensionBuildArtifact,
    test_file: Path,
    python_executable: str,
    rails_count: int,
    tools_count: int,
    skills_count: int,
    skill_dirs_count: int,
    previous_error: str,
) -> str:
    """Build prompt for agent-generated extension acceptance tests."""
    previous = (
        f"\n\n上一次测试/生成失败信息:\n{previous_error[:4000]}"
        if previous_error
        else ""
    )
    return (
        "你正在执行 verify_ext 阶段。请根据已加载的 verify_ext skill，"
        "为 runtime extension 生成可执行 pytest 验收测试。\n\n"
        f"扩展名称: {build.extension_name}\n"
        f"扩展根目录: {build.extension_root}\n"
        f"harness_config: {build.config_path}\n"
        f"测试文件必须写入: {test_file}\n"
        f"pytest 必须使用的解释器: {python_executable}\n"
        "这个解释器路径是关键执行环境信息，测试代码和说明不得假设其他 Python 环境。\n\n"
        "组件数量:\n"
        f"- rails: {rails_count}\n"
        f"- tools: {tools_count}\n"
        f"- skill files: {skills_count}\n"
        f"- skill dirs: {skill_dirs_count}\n\n"
        "测试要求:\n"
        "1. 必须覆盖 DeepAgent.load_harness_config(config_path) 真实加载路径。\n"
        "2. 必须检查 agent.ability_manager 和 Runner.resource_mgr 中的工具注册。\n"
        "   测试代码需要导入或实例化 rail/tool 时，必须先读取 "
        "harness_config.yaml 中实际声明的 module/class；不要手写或猜测 "
        "module path。module 必须以 "
        "`openjiuwen.extensions.harness.<extension_name>.` 开头，并映射到"
        "扩展根目录内真实存在的 .py 文件。\n"
        "3. Rail 必须通过 agent-core/harness 可观测副作用验证，例如状态文件、"
        "ToolCallInputs 记录、session OutputSchema 或 steering message。\n"
        "4. Tool 必须验证 ToolOutput 或 invoke 输出结构，不依赖任何 web/UI 事件。\n"
        "5. 文件/产物生成类 Tool 必须验证真实产物，而不是只验证 success 文本："
        "调用 Tool 时传入 pytest tmp_path 下的 output_path，断言返回的 path/"
        "absolute_path 指向真实存在文件，exists=true，size_bytes > 0，"
        "format/后缀匹配。PPTX/DOCX 必须用 zipfile 校验关键内部结构；"
        "PPTX 至少包含 `[Content_Types].xml`、`ppt/presentation.xml` 和 "
        "`ppt/slides/slide*.xml`。PDF 必须校验 `%PDF` 文件头；JSON 必须"
        "用 json parser 重新解析并检查关键字段。不得接受 JSON/Markdown/"
        "纯文本占位冒充 PPTX/DOCX/PDF。\n"
        "6. Skill 必须验证 SKILL.md frontmatter 和加载路径。\n"
        "7. 测试失败信息要具体，便于 implement_ext 修复扩展代码。\n"
        "8. 只写测试文件，不要修改扩展实现代码。\n"
        f"{previous}"
    )


def _build_ext_static_fix_prompt(
    *,
    build: ExtensionBuildArtifact,
    static_errors: str,
) -> str:
    """Build prompt for fixing extension structure/static failures."""
    return (
        "verify_ext 的结构/静态校验失败。请只修改扩展 package 内的"
        "实现文件和 harness_config.yaml，直到结构校验、manifest schema、"
        "组件加载和 ruff 检查能通过。\n\n"
        f"扩展名称: {build.extension_name}\n"
        f"扩展根目录: {build.extension_root}\n"
        f"harness_config: {build.config_path}\n\n"
        "常见修复要求:\n"
        "- harness_config.yaml 必须符合 HarnessConfig schema；"
        "`description` 必须是字符串，不能是多语言 dict。\n"
        "- resources 中只声明实际生成的 rails/tools/skills；"
        "不得声明不存在或无法 import 的 module/class。\n"
        "- rail/tool 条目必须使用 `type: package`，并同时包含 "
        "`module` 和 `class`。\n"
        "- rail/tool 的 module 必须以 "
        "`openjiuwen.extensions.harness.<extension_name>.` 开头，"
        "并指向扩展根目录内真实存在的 Python 文件。\n"
        "- 所有自测 import 和实例化都必须以 harness_config.yaml 中"
        "实际声明的 module/class 为唯一来源，不要手写或猜测路径。\n"
        "- Tool class 必须可无参构造，并在 __init__ 内创建 ToolCard。\n"
        "- SKILL.md 必须有合法 frontmatter。\n"
        "- 只允许修改扩展根目录内文件，不要修改测试或 auto-harness 主代码。\n\n"
        f"失败信息:\n{static_errors[:6000]}"
    )


def _build_ext_acceptance_fix_prompt(
    *,
    build: ExtensionBuildArtifact,
    test_file: Path,
    pytest_output: str,
    python_executable: str,
) -> str:
    """Build prompt for fixing extension package after acceptance failure."""
    return (
        "verify_ext 生成的 runtime extension 验收测试失败。"
        "请只修改扩展 package 内的实现文件，直到测试能通过。\n\n"
        f"扩展根目录: {build.extension_root}\n"
        f"harness_config: {build.config_path}\n"
        f"测试文件: {test_file}\n"
        f"pytest 解释器: {python_executable}\n\n"
        "约束:\n"
        "- 只允许修改扩展根目录内文件。\n"
        "- 不要修改测试来绕过失败；verify_ext 会复跑同一个测试文件，"
        "不会因为实现修复而重新生成测试。\n"
        "- Tool/Rail 共享状态必须使用按 session_id 隔离的文件状态。\n\n"
        "- 如果失败来自文件产物验收，必须修复 Tool 生成真实目标格式；"
        "不得用 JSON/Markdown/纯文本占位，也不得在文件不存在或格式无效时"
        "返回 success=true。\n\n"
        f"pytest 输出:\n{pytest_output[:6000]}"
    )


async def _run_pytest_file(
    *,
    python_executable: str,
    test_file: Path,
    cwd: Path,
) -> _CIResult:
    """Run one pytest file with the configured CI Python executable."""
    env = {
        **os.environ,
        "CI": "1",
        "AUTO_HARNESS_PYTHON": python_executable,
    }
    python_path = Path(python_executable)
    if not python_path.is_file():
        return _CIResult(
            passed=False,
            errors=(
                "verify_ext_python_executable_not_found: "
                f"{python_executable}"
            ),
        )
    if python_path.is_file():
        bin_dir = str(python_path.parent)
        env["VIRTUAL_ENV"] = str(python_path.parent.parent)
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    proc = await asyncio.create_subprocess_exec(
        python_executable,
        "-m",
        "pytest",
        str(test_file),
        "-q",
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace")
    return _CIResult(
        passed=proc.returncode == 0,
        errors=output[-6000:],
    )


def _check_imports(
    extension_root: Path,
) -> list[str]:
    """Try importing each .py file under extension_root.

    Uses ``importlib.util.spec_from_file_location`` with a
    temporary package hierarchy registered in ``sys.modules``
    so that absolute imports like
    ``from openjiuwen.extensions.harness.<ext>.xxx`` resolve
    correctly even when the ``harness`` sub-package does not
    exist in the installed tree.

    Returns:
        List of import error descriptions.
    """
    import importlib.util
    import sys
    import types

    errors: list[str] = []
    py_files = sorted(extension_root.rglob("*.py"))
    if not py_files:
        return errors

    # Build the expected package prefix from the
    # extension_root path.  For a root like
    # ``…/openjiuwen/extensions/harness/demo_ext`` we need
    # ``openjiuwen.extensions.harness.demo_ext``.
    ext_name = extension_root.name

    # Walk up to find the ``openjiuwen`` ancestor so we can
    # derive the dotted prefix.
    parts: list[str] = [ext_name]
    cur = extension_root.parent
    while cur.name and cur.name != cur.root:
        parts.append(cur.name)
        if cur.name == "openjiuwen":
            break
        cur = cur.parent
    parts.reverse()
    pkg_prefix = ".".join(parts)

    # Temporarily register synthetic namespace packages so
    # that ``from openjiuwen.extensions.harness.<ext>…``
    # resolves.
    injected: list[str] = []
    accumulated = ""
    for part in parts:
        accumulated = (
            f"{accumulated}.{part}" if accumulated else part
        )
        if accumulated not in sys.modules:
            mod = types.ModuleType(accumulated)
            mod.__path__ = []  # type: ignore[attr-defined]
            sys.modules[accumulated] = mod
            injected.append(accumulated)

    # Point the extension package __path__ at the real dir
    ext_mod = sys.modules.get(pkg_prefix)
    if ext_mod is not None:
        ext_mod.__path__ = [  # type: ignore[attr-defined]
            str(extension_root)
        ]

    try:
        for py_file in py_files:
            if py_file.name == "__init__.py":
                continue
            relative = py_file.relative_to(extension_root)
            module_suffix = (
                str(relative.with_suffix(""))
                .replace("/", ".")
                .replace("\\", ".")
            )
            full_module = f"{pkg_prefix}.{module_suffix}"
            try:
                spec = (
                    importlib.util.spec_from_file_location(
                        full_module,
                        py_file,
                    )
                )
                if spec is None or spec.loader is None:
                    errors.append(
                        "Import failed for "
                        f"{py_file.name}: "
                        "cannot create import spec"
                    )
                    continue
                mod = (
                    importlib.util.module_from_spec(spec)
                )
                sys.modules[full_module] = mod
                injected.append(full_module)
                spec.loader.exec_module(mod)
            except Exception as exc:
                errors.append(
                    f"Import failed for "
                    f"{py_file.name}: {exc}"
                )
    finally:
        # Clean up injected modules
        for name in reversed(injected):
            sys.modules.pop(name, None)

    return errors


async def _check_ruff(
    extension_root: Path,
) -> list[str]:
    """Auto-fix formatting, then lint-check on extension_root.

    Runs ``ruff format`` (auto-fix) and ``ruff check --fix``
    first so that agent-generated code gets cleaned up before
    we report real errors.

    Returns:
        List of lint error descriptions.
    """
    errors: list[str] = []
    root_str = str(extension_root)

    # Step 1: auto-fix formatting and lint issues
    for fix_cmd in (
        ["ruff", "format", root_str],
        ["ruff", "check", "--fix", root_str],
    ):
        try:
            proc = await asyncio.create_subprocess_exec(
                *fix_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except FileNotFoundError:
            logger.debug(
                "ruff not available, skipping auto-fix"
            )
            return errors

    # Step 2: check remaining lint errors
    try:
        proc = await asyncio.create_subprocess_exec(
            "ruff",
            "check",
            root_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            output = (
                stdout.decode("utf-8", errors="replace")
                .strip()
            )
            if not output:
                output = (
                    stderr.decode(
                        "utf-8", errors="replace"
                    ).strip()
                )
            errors.append(
                f"ruff check failed: {output[:500]}"
            )
    except FileNotFoundError:
        logger.debug("ruff not available, skipping lint")

    return errors


# Backwards-compat alias
VerifyExtStage = ExtendVerifyStage

__all__ = [
    "VerifyStage",
    "MetaVerifyStage",
    "ExtendVerifyStage",
]
