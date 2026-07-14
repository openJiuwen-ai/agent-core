# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Verify stage for auto-harness pipelines."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
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
from openjiuwen.auto_harness.infra.ci_gate_runner import (
    decode_stdout,
)
from openjiuwen.auto_harness.infra.runtime_extension_static_checks import (
    ExtStaticCheckResult,
    check_ruff,
    run_static_checks_against_runtime,
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


async def _install_extension_dependencies(
    extension_root: Path,
) -> tuple[bool, str]:
    """Install dependencies from requirements.txt if present.

    Returns:
        (success, error_message) - error_message is empty on success.
    """
    req_file = extension_root / "requirements.txt"
    if not req_file.exists():
        logger.debug(
            "[verify_ext] no requirements.txt found: %s",
            extension_root,
        )
        return True, ""

    logger.info(
        "[verify_ext] installing dependencies from: %s",
        req_file,
    )

    env = _build_install_env()

    # Ensure pip is available, bootstrap via ensurepip if not
    pip_available = await _check_pip_available(env)
    if not pip_available:
        logger.info("[verify_ext] pip not available, bootstrapping via ensurepip")
        bootstrap_ok, bootstrap_error = await _bootstrap_pip(env)
        if not bootstrap_ok:
            return False, bootstrap_error

    return await _run_pip_install(req_file, env)


def _build_install_env() -> dict[str, str]:
    """Build env for pip install, ensuring correct Python environment."""
    env = {**os.environ, "CI": "1"}
    # Remove VIRTUAL_ENV to prevent targeting wrong environment
    env.pop("VIRTUAL_ENV", None)
    # Set AUTO_HARNESS_PYTHON and prepend bin dir to PATH
    python_path = Path(sys.executable)
    env["AUTO_HARNESS_PYTHON"] = sys.executable
    if python_path.name.startswith("python"):
        bin_dir = str(python_path.parent)
        existing_path = env.get("PATH", "")
        pathsep = os.pathsep
        env["PATH"] = (
            f"{bin_dir}{pathsep}{existing_path}"
            if existing_path
            else bin_dir
        )
    return env


async def _check_pip_available(env: dict[str, str]) -> bool:
    """Check if pip module is available in current Python environment."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception:
        return False


async def _run_pip_install(
    req_file: Path,
    env: dict[str, str],
) -> tuple[bool, str]:
    """Run pip install -r requirements.txt."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(req_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info("[verify_ext] pip install succeeded")
            return True, ""

        error = stderr.decode("utf-8", errors="replace").strip()
        if not error:
            error = stdout.decode("utf-8", errors="replace").strip()
        return False, f"pip_install_failed: {error[:500]}"
    except Exception as e:
        return False, f"pip_install_exception: {e}"


async def _bootstrap_pip(env: dict[str, str]) -> tuple[bool, str]:
    """Bootstrap pip via ensurepip if not available.

    Returns:
        (success, error_message)
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "ensurepip",
            "--upgrade",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info("[verify_ext] pip bootstrapped successfully")
            return True, ""

        error = stderr.decode("utf-8", errors="replace").strip()
        if not error:
            error = stdout.decode("utf-8", errors="replace").strip()
        return False, f"pip_bootstrap_failed: {error[:500]}"
    except Exception as e:
        return False, f"pip_bootstrap_exception: {e}"


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

        # L0: Install dependencies from requirements.txt
        extension_root = Path(build.extension_root)
        dep_ok, dep_error = await _install_extension_dependencies(
            extension_root,
        )
        if not dep_ok:
            logger.error(f"[verify_ext] 依赖安装失败: {dep_error}")

        static_result = ExtStaticCheckResult()
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            static_result = await run_static_checks_against_runtime(
                runtime_ext=RuntimeExtensionArtifact(
                    extension_name=build.extension_name,
                    runtime_path=build.extension_root,
                    config_path=build.config_path,
                ),
                session_id_prefix=(
                    f"verify_{ctx.orchestrator.runtime.session_id}_{uuid.uuid4().hex[:8]}"
                ),
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


async def _run_agent_generated_ext_acceptance(
    *,
    ctx: TaskContext,
    build: ExtensionBuildArtifact,
    rails_count: int,
    tools_count: int,
    skills_count: int,
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
    previous_error: str,
) -> str:
    """Build prompt for agent-generated extension acceptance tests."""
    previous = (
        f"\n\n上一次测试/生成失败信息:\n{previous_error[:4000]}"
        if previous_error
        else ""
    )

    return (
        "你正在执行 verify_ext 阶段。请严格遵循 verify_ext skill 规范生成 pytest 验收测试。\n\n"

        # ===== 强制约束 =====
        "【强制约束 - 违反将导致测试无法执行】\n\n"

        "1. 路径动态解析:\n"
        "   禁止硬编码任何绝对路径。必须从 __file__ 或环境变量动态推断:\n"
        "   ```python\n"
        "   test_file = Path(__file__).resolve()\n"
        "   wt_root = test_file.parent.parent.parent  # .auto_harness_verify/<ext>/test.py\n"
        "   ext_root = wt_root / 'openjiuwen/extensions/harness/<extension_name>'\n"
        "   ```\n\n"

        "2. sys.path 操作:\n"
        "   只在模块顶部一次性添加路径，禁止在测试方法内修改:\n"
        "   ```python\n"
        "   for p in [str(wt_root), str(agent_core_root)]:\n"
        "       if p not in sys.path:\n"
        "           sys.path.insert(0, p)\n"
        "   ```\n\n"

        "3. 异步测试规范:\n"
        "   async 测试方法必须加 @pytest.mark.asyncio 装饰器。\n"
        "   禁止在非 async 测试中使用 asyncio.run()。\n\n"

        "4. 动态导入:\n"
        "   必须从 harness_config.yaml 实际声明的 module/class 获取:\n"
        "   ```python\n"
        "   import importlib\n"
        "   with open(config_path) as f:\n"
        "       config = yaml.safe_load(f)\n"
        "   for entry in config['resources']['tools']:\n"
        "       module = importlib.import_module(entry['module'])\n"
        "       tool_cls = getattr(module, entry['class'])\n"
        "   ```\n"
        "   禁止假设特定 module path。\n\n"

        "5. 失败归因格式:\n"
        "   所有 assert 失败必须以 `failure_id=<ID>: ` 开头，ID 来自 verify_ext skill:\n"
        "   L1: manifest_invalid, entry_point_not_allowed, module_import_failed,\n"
        "       class_init_failed, skill_manifest_invalid\n"
        "   L2: harness_load_failed, rail_not_registered, tool_not_registered,\n"
        "       skill_not_loaded\n"
        "   L3: tool_not_called, tool_result_failed, tool_result_schema_missing,\n"
        "       artifact_not_created, artifact_format_invalid, artifact_placeholder_output,\n"
        "       rail_hook_not_observed, rail_tool_state_not_shared\n\n"

        "6. API 安全调用:\n"
        "   调用方法前用 hasattr 检查:\n"
        "   ```python\n"
        "   if hasattr(agent, 'shutdown'):\n"
        "       await agent.shutdown()\n"
        "   ```\n\n"

        "7. 跨平台兼容性:\n"
        "   测试代码必须在 Windows 和 Linux 环境下都能执行:\n"
        "   - 路径操作: 使用 pathlib.Path，禁止字符串拼接路径分隔符\n"
        "   - 路径比较: 使用 Path.resolve() 规范化后再比较\n"
        "   - 环境变量: 使用 os.getenv()，不假设特定路径前缀 (C:/ vs /)\n"
        "   - 换行符: 文件读写指定 encoding='utf-8'，不假设换行符\n"
        "   - subprocess: 使用 asyncio.create_subprocess_exec 而非 shell=True\n"
        "   - 临时目录: 使用 pytest tmp_path 或 tempfile.gettempdir()\n"
        "   ```python\n"
        "   # 正确: 使用 Path 进行跨平台路径操作\n"
        "   ext_root = wt_root / 'openjiuwen' / 'extensions' / 'harness' / ext_name\n"
        "   config_path = ext_root / 'harness_config.yaml'\n\n"
        "   # 错误: 字符串拼接路径分隔符\n"
        "   ext_root = wt_root + '/openjiuwen/extensions/harness/' + ext_name  # 禁止\n"
        "   ```\n\n"

        # ===== 验证分层 (来自 SKILL.md) =====
        "【验证分层 - 验收测试必须覆盖 L1/L2/L3】\n\n"

        "L1 结构校验:\n"
        "  必须检查:\n"
        "  - harness_config.yaml 存在且 schema_version: harness_config.v0.1\n"
        "  - rail/tool 条目必须是 type: package，包含 module 和 class\n"
        "  - module 必须以 openjiuwen.extensions.harness.<extension_name> 开头\n"
        "  - module 能映射到扩展根目录内真实 .py 文件\n"
        "  - __init__.py 不得包含 re-export\n"
        "  - Tool class 必须无参构造，且自己创建 ToolCard\n"
        "  - ToolCard.id 和 ToolCard.name 必须显式设置\n"
        "  - skill 目录必须包含合法 SKILL.md (frontmatter 有 name/description)\n"
        "  建议测试: test_harness_config_schema, test_entry_points_valid, test_skill_manifests\n\n"

        "L2 临时热加载:\n"
        "  必须创建临时 DeepAgent，调用真实加载路径:\n"
        "  ```python\n"
        "  from openjiuwen.harness.deep_agent import DeepAgent\n"
        "  from openjiuwen.harness.schema.config import DeepAgentConfig\n"
        "  from openjiuwen.core.single_agent.schema.agent_card import AgentCard\n"
        "  agent = DeepAgent(AgentCard(name='test_agent', description='test')).configure(\n"
        "    DeepAgentConfig(enable_task_loop=False))\n"
        "  loaded = await agent.load_harness_config(config_path)\n"
        "  ```\n"
        "  断言:\n"
        "  - 每个 rail 返回 rail:<ClassName>\n"
        "  - 每个 tool 返回 tool:<ClassName>\n"
        "  - ToolCard 出现在 agent.ability_manager.list()\n"
        "  - skill 目录被追加到 SkillUseRail\n"
        "  建议测试: test_load_harness_config, test_tools_registered\n\n"

        "L3 运行时验收:\n"
        "  Tool 验收:\n"
        "  - 导入：from openjiuwen.core.runner.runner import Runner"
        "  - 调用 Runner.resource_mgr.get_tool(tool_id).invoke(...) 验证输出\n"
        "  - ToolOutput.success 必须为 true\n"
        "  - 输出必须包含设计声明的字段\n\n"

        "  Rail 验收:\n"
        "  - 检查可观测副作用: 状态文件、prompt section 注入、tool gating\n"
        "  - 状态文件路径: <extension_root>/.state/<session_id>.json\n"
        "  - 或检查 session stream 中的 OutputSchema/steering message\n\n"

        "  Skill 验收:\n"
        "  - SKILL.md frontmatter 合法\n"
        "  - skill 目录能被 SkillUseRail 加载\n"
        "  (skill 验证已在静态检查完成大部分，验收测试确认加载生效即可)\n\n"

        "  文件产物验收 (仅文件生成类 Tool):\n"
        "  - 传入 pytest tmp_path 下的 output_path\n"
        "  - 断言返回: success=true, path/absolute_path 存在, exists=true, size_bytes>0\n"
        "  - PPTX: zipfile 校验 [Content_Types].xml + ppt/presentation.xml + slide*.xml\n"
        "  - DOCX: zipfile 校验 [Content_Types].xml + word/document.xml\n"
        "  - PDF: 文件头 %PDF\n"
        "  - JSON: json.load 重解析 + 关键字段\n"
        "  - 禁止 JSON/Markdown 冒充 PPTX/DOCX/PDF\n\n"

        # ===== 测试范围限制 =====
        "【测试范围限制】\n"
        "  - L1: 最多 3 个测试方法\n"
        "  - L2: 最多 2 个测试方法\n"
        "  - L3: 每类组件最多 1 个测试方法\n"
        "  - 禁止测试异常输入拒绝、父目录自动创建等边界情况\n"
        "  - 禁止测试 DeepAgent 完整生命周期\n\n"

        # ===== 扩展信息 =====
        f"扩展名称: {build.extension_name}\n"
        f"扩展根目录: {build.extension_root}\n"
        f"harness_config: {build.config_path}\n"
        f"测试文件必须写入: {test_file}\n"
        f"pytest 解释器: {python_executable}\n\n"

        f"组件数量: rails={rails_count}, tools={tools_count}, skills={skills_count}\n\n"

        # ===== 禁止事项 =====
        "【禁止事项】\n"
        "- 不要修改扩展实现代码\n"
        "- 不要为绕过失败修改测试\n"
        "- 不要使用 bare except\n"
        "- 不要使用网络请求\n\n"

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

        "【常见修复要求】\n"
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
        "- 依赖缺失：若报错 `No module named 'xxx'`，在扩展根目录 "
        "创建或者追加 `requirements.txt` 文件，添加所需依赖（如 `python-pptx`、"
        "`python-docx`、`openpyxl` 等）；verify_ext 会自动安装。\n"
        "- 只允许修改扩展根目录内文件，不要修改测试或 auto-harness 主代码。\n\n"

        "【跨平台兼容性】\n"
        "- 路径操作: 使用 pathlib.Path，禁止字符串拼接路径分隔符\n"
        "- 状态文件路径: 使用 Path 跨平台构建，不假设 / 或 \\\\ 分隔符\n"
        "- 环境变量: 使用 os.getenv() 获取，不假设 Windows 盘符路径\n\n"

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
    # 解析失败归因 ID，帮助 agent 精准定位
    failure_ids = _extract_failure_ids_from_pytest_output(pytest_output)
    failure_summary = (
        f"\n检测到的 failure_id:\n{failure_ids}\n"
        if failure_ids
        else ""
    )

    return (
        "verify_ext 验收测试失败。请根据 failure_id 精准定位并修复扩展实现。\n\n"

        # ===== 失败信息 =====
        f"扩展根目录: {build.extension_root}\n"
        f"harness_config: {build.config_path}\n"
        f"测试文件: {test_file}\n"
        f"pytest 解释器: {python_executable}\n\n"
        f"{failure_summary}"

        # ===== 修复约束 =====
        "【修复约束】\n"
        "1. 只允许修改扩展根目录内的实现文件。\n"
        "2. 不要修改测试文件来绕过失败。\n"
        "3. 不要修改 harness_config.yaml 中的 module/class 声明。\n"
        "4. 跨平台兼容: 使用 pathlib.Path 操作路径，禁止字符串拼接路径分隔符。\n\n"

        # ===== 按 failure_id 修复建议 (来自 SKILL.md) =====
        "【按 failure_id 修复建议】\n\n"

        "L1 结构类:\n"
        "  - manifest_invalid: 检查 harness_config.yaml schema_version/name/resources 格式\n"
        "  - entry_point_not_allowed: 确保 type=package + module/class 字段\n"
        "  - module_import_failed: 检查 module 路径以 openjiuwen.extensions.harness.<ext> 开头\n"
        "  - class_init_failed: 检查 Tool class 无参构造 + ToolCard 创建\n"
        "  - skill_manifest_invalid: 检查 SKILL.md frontmatter name/description\n\n"

        "L2 热加载类:\n"
        "  - harness_load_failed: 检查 harness_config.yaml 格式 + DeepAgent.load_harness_config 路径\n"
        "  - rail_not_registered: 检查 rail 返回 rail:<ClassName>\n"
        "  - tool_not_registered: 检查 ToolCard 在 ability_manager.list() 返回的Ability中\n"
        "  - skill_not_loaded: 检查 skills.dirs 被追加到 SkillUseRail\n\n"

        "L3 运行时类:\n"
        "  - tool_not_called: 检查 agent 是否实际调用工具\n"
        "  - tool_result_failed: 检查 ToolOutput.success + invoke 内部异常\n"
        "  - tool_result_schema_missing: 检查返回结构包含设计声明字段\n"
        "  - artifact_not_created: 检查文件产物真实存在\n"
        "  - artifact_format_invalid: 检查 PPTX/DOCX/PDF/JSON 格式结构\n"
        "  - artifact_placeholder_output: 禁止 JSON/Markdown 冒充 PPTX/DOCX/PDF\n"
        "  - rail_hook_not_observed: 检查状态文件/steering message 等副作用\n"
        "  - rail_tool_state_not_shared: 检查 Rail/Tool 按 session_id 隔离的状态文件\n\n"

        # ===== 文件产物修复 =====
        "【文件产物修复 - artifact_* 类 failure_id】\n"
        "- PPTX: 使用 python-pptx 或直接写入 ZIP，校验 [Content_Types].xml + ppt/presentation.xml\n"
        "- DOCX: 使用 python-docx，校验 [Content_Types].xml + word/document.xml\n"
        "- PDF: 必须生成 %PDF 开头的真实文件\n"
        "- JSON: 必须可 json.loads 解析\n"
        "- 路径处理: 使用 Path 跨平台构建 output_path，不假设 / 或 \\\\ 分隔符\n"
        "禁止返回 JSON/Markdown 占位并标记 success=true\n\n"

        # ===== pytest 输出 =====
        f"pytest 输出 (前 5000 字符):\n{pytest_output[:5000]}"
    )


def _extract_failure_ids_from_pytest_output(pytest_output: str) -> str:
    """从 pytest 输出中提取所有 failure_id。

    Returns:
        格式化的 failure_id 列表字符串，按 L1/L2/L3 分类。
    """
    import re

    # 匹配 failure_id=<ID>: 格式
    pattern = r"failure_id=([a-zA-Z_]+):"
    matches = re.findall(pattern, pytest_output)

    if not matches:
        return ""

    # 去重并排序
    unique_ids = sorted(set(matches))

    # 按 SKILL.md 定义分类
    l1_ids = [
        "manifest_invalid", "entry_point_not_allowed",
        "module_import_failed", "class_init_failed", "skill_manifest_invalid",
    ]
    l2_ids = [
        "harness_load_failed", "rail_not_registered",
        "tool_not_registered", "skill_not_loaded",
    ]
    l3_ids = [
        "tool_not_called", "tool_result_failed", "tool_result_schema_missing",
        "artifact_not_created", "artifact_format_invalid", "artifact_placeholder_output",
        "rail_hook_not_observed", "rail_tool_state_not_shared",
    ]

    found_l1 = [id for id in unique_ids if id in l1_ids]
    found_l2 = [id for id in unique_ids if id in l2_ids]
    found_l3 = [id for id in unique_ids if id in l3_ids]
    other_ids = [id for id in unique_ids if id not in l1_ids + l2_ids + l3_ids]

    lines: list[str] = []
    if found_l1:
        lines.append("L1 结构类:")
        lines.extend([f"  - {id}" for id in found_l1])
    if found_l2:
        lines.append("L2 热加载类:")
        lines.extend([f"  - {id}" for id in found_l2])
    if found_l3:
        lines.append("L3 运行时类:")
        lines.extend([f"  - {id}" for id in found_l3])
    if other_ids:
        lines.append("其他 (未识别):")
        lines.extend([f"  - {id}" for id in other_ids])

    return "\n".join(lines)


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
        pathsep = os.pathsep
        env["PATH"] = (
            f"{bin_dir}{pathsep}{env.get('PATH', '')}"
            if env.get("PATH")
            else bin_dir
        )
    proc = await asyncio.create_subprocess_exec(
        python_executable,
        "-m",
        "pytest",
        str(test_file),
        "-q",
        "-o",
        "addopts=",
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    stdout, _ = await proc.communicate()
    output = decode_stdout(stdout)
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


# Backward-compat alias — real definition lives in runtime_extension_static_checks
_check_ruff = check_ruff


# Backwards-compat alias
VerifyExtStage = ExtendVerifyStage

__all__ = [
    "VerifyStage",
    "MetaVerifyStage",
    "ExtendVerifyStage",
    "VerifyExtStage",
]
