# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Implement stage hierarchy for task-scoped code changes."""

from __future__ import annotations

import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.core.common.logging import logger
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
    ExtensionBuildArtifact,
    ExtensionDesign,
    OptimizationTask,
    RuntimeExtensionArtifact,
    StageResult,
    TaskStatus,
)
from openjiuwen.auto_harness.stages.base import (
    TaskStage,
    scope_output_event_stage,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import (
        DeepAgent,
    )
    from openjiuwen.core.session.agent import (
        Session,
    )


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


def _artifact_contract_hint(name: str) -> str:
    """Return artifact validation guidance for generated extensions."""
    lowered = name.lower()
    common = (
        "文件/产物生成类 Tool 必须在返回 success=true 前完成自校验："
        "输出路径存在、size_bytes > 0、format 与文件后缀一致，并返回 "
        "success/path 或 absolute_path/format/exists/size_bytes 等结构化字段。"
        "如果依赖缺失、写入失败或格式校验失败，必须返回 success=false "
        "和明确错误；不得用成功文本掩盖失败。"
    )
    if any(token in lowered for token in ("ppt", "pptx", "powerpoint")):
        return (
            common +
            " PPT/PPTX 生成必须产出真实 .pptx 文件；不得用 JSON、Markdown、"
            "纯文本或“待下游转换”的中间结构冒充 PPTX。"
            "成功前必须用 zipfile 校验文件是合法 zip 包，并包含 "
            "`[Content_Types].xml`、`ppt/presentation.xml` 和至少一个 "
            "`ppt/slides/slide*.xml`。可用 python-pptx 时应优先生成真实 PPTX。"
        )
    if any(token in lowered for token in ("docx", "word")):
        return (
            common +
            " DOCX 生成必须产出真实 .docx 文件，并用 zipfile 校验 "
            "`[Content_Types].xml` 和 `word/document.xml`。"
        )
    if "pdf" in lowered:
        return (
            common +
            " PDF 生成必须产出真实 .pdf 文件，并校验文件头以 `%PDF` 开始。"
        )
    if "json" in lowered:
        return (
            common +
            " JSON 生成必须写出可被 json parser 解析的文件，并校验关键字段存在。"
        )
    return common


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
    """Abstract base for all implement-slot stages."""

    name = "implement"
    slot = "implement"
    display_name = "执行代码修改"
    description = "Implement code changes."
    produces = ["code_change"]

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        """Subclasses must override this method."""
        raise NotImplementedError


class MetaImplementStage(ImplementStage):
    """Execute code changes for the current task."""

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


# ------------------------------------------------------------------
# ExtendImplementStage — extension implementation (from implement_ext)
# ------------------------------------------------------------------


def _build_implement_ext_prompt(
    design: ExtensionDesign,
    *,
    extension_root: Path,
    config_path: Path,
) -> str:
    """Build the prompt for the implement_ext agent."""
    components = list(design.components or ["tool", "skill"])
    components_text = ", ".join(components)
    file_plan_lines: list[str] = []
    for key, value in design.file_plan.items():
        file_plan_lines.append(f"  - {key}: {value}")
    file_plan_text = (
        "\n".join(file_plan_lines)
        if file_plan_lines
        else "  (无)"
    )

    # Build component-specific requirements
    requirements: list[str] = [
        "1. 在扩展根目录下创建完整的 Python 包结构",
        "2. 严格按 ExtensionDesign.components 实现组件；"
        "不要为了完整性自动补充未声明的 Rail、Tool 或 Skill。",
        "3. 实现必须贴合 extension_name 和 gap 语义，"
        "保留用户目标中的关键实体与产物类型；不要把 PPT/"
        "文档/办公生成类需求泛化成需求收集或结构化需求报告。",
        f"4. 真实产物契约：{_artifact_contract_hint(design.extension_name)}",
    ]
    step = 5
    if "rail" in components:
        requirements.append(
            f"{step}. 实现 rail 组件 "
            "(继承 DeepAgentRail)。Rail 只负责设计中要求的"
            "生命周期拦截、后台监听、周期触发、审计或上下文增强；"
            "不得替代 Tool 执行文件生成等主动动作。"
        )
        step += 1
    if "tool" in components:
        tool_req = (
            f"{step}. 实现 tool 组件 "
            "(继承 Tool，包含 ToolCard)。ToolCard.id 和 "
            "ToolCard.name 必须稳定、snake_case、语义明确，"
            "优先与 extension_name 或核心动作一致。"
        )
        if "skill" in components:
            tool_req += (
                " Tool + Skill 协作模式：Tool 不应独立完成复杂任务，"
                "应作为 Skill 的执行层；ToolCard.description 必须明确说明"
                "\"需配合对应 Skill 使用\"，禁止在单个 invoke 中完成"
                "品牌适配、模板选择、内容生成、格式校验等全部环节。"
            )
        requirements.append(tool_req)
        step += 1
    if "skill" in components:
        if design.skill_source:
            skill_name = design.skill_source.removeprefix(
                "community:"
            )
            requirements.append(
                f"{step}. Skill 部分已从社区 skill "
                f"'{skill_name}' 复用，skill 目录已存在于 "
                f"skills/<skill_name>/ 下；不要重新创建或修改 "
                f"SKILL.md，只需确保 harness_config.yaml "
                f"正确声明 skills.dirs"
            )
        else:
            requirements.append(
                f"{step}. 创建 skills/<skill_name>/SKILL.md，"
                "包含 frontmatter (name, description) 和正文；"
                "必须参考 skill-creator 规范：name/description 要准确描述"
                "触发场景，正文保持精简且可操作，只写 agent 真正需要的"
            "领域规范、品牌风格、模板原则、生成流程、示例和验收标准；"
            "如需 PPT 模板、品牌素材或详细参考资料，可放在 skill "
            "目录下的 assets/ 或 references/，并在 SKILL.md 中说明何时使用"
        )
        step += 1
    if "rail" in components and "tool" in components:
        requirements.append(
            f"{step}. 如果 Rail 与 Tool 需要共享状态，"
            "必须使用按 session_id 隔离的显式文件状态；"
            "不得通过 Tool 构造函数注入 Rail 实例、agent、"
            "session 或其他运行时对象"
        )
        step += 1
    requirements.append(
        f"{step}. 生成 harness_config.yaml manifest"
        "（只声明实际包含的组件类型）"
    )
    step += 1
    requirements.append(
        f"{step}. 确保所有模块可正常 import；自测 import 和类实例化"
        "必须从 `harness_config.yaml` 中读取实际声明的 `module` 和 "
        "`class`，不要手写或猜测 module path；所有 rail/tool module "
        "必须以 `openjiuwen.extensions.harness.<extension_name>.` 开头，"
        "并指向扩展目录内真实存在的 Python 文件"
    )
    requirements_text = "\n".join(requirements)

    return (
        f"实现运行时扩展: {design.extension_name}\n\n"
        f"Gap ID: {design.gap_id}\n"
        f"组件: {components_text}\n"
        f"文件规划:\n{file_plan_text}\n\n"
        f"扩展根目录: {extension_root}\n"
        f"Manifest 路径: {config_path}\n\n"
        f"要求:\n{requirements_text}\n\n"
        "直接开始实现，不要等待确认。\n"
        "严禁执行 git add、git commit 或其他提交动作。"
    )


def _extract_task_failed_error(
    chunk: Any,
) -> str:
    """Return task-loop failure text from a chunk."""
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


def _resolve_extension_root(
    wt_path: str,
    design: ExtensionDesign,
) -> Path:
    """Resolve the extension root directory in the worktree."""
    root = design.file_plan.get(
        "root",
        (
            "openjiuwen/extensions/harness/"
            f"{design.extension_name}"
        ),
    )
    return Path(wt_path) / root


def _resolve_config_path(
    wt_path: str,
    design: ExtensionDesign,
) -> Path:
    """Resolve the manifest config path in the worktree."""
    manifest = design.file_plan.get(
        "manifest",
        (
            "openjiuwen/extensions/harness/"
            f"{design.extension_name}/harness_config.yaml"
        ),
    )
    return Path(wt_path) / manifest


class ExtendImplementStage(ImplementStage):
    """Materialize one extension design into the task worktree."""

    name = "implement_ext"
    display_name = "实现扩展"
    produces = ["extension_build"]
    consumes = ["extension_target"]

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        design = ctx.require_artifact("extension_target")
        if not isinstance(design, ExtensionDesign):
            raise TypeError(
                "extension_target artifact must be "
                "ExtensionDesign"
            )

        agent = ctx.runtime.task_agent
        if agent is None:
            error = (
                "No task_agent available for "
                "implement_ext stage"
            )
            logger.error(error)
            ctx.task.status = TaskStatus.FAILED
            yield StageResult(
                status="failed",
                artifacts={
                    "task_result": CycleResult(
                        success=False,
                        error=error,
                    ),
                },
                messages=[error],
                error=error,
            )
            return

        extension_root = _resolve_extension_root(
            ctx.runtime.wt_path,
            design,
        )
        config_path = _resolve_config_path(
            ctx.runtime.wt_path,
            design,
        )

        extension_root.mkdir(parents=True, exist_ok=True)

        # Copy community skill if skill_source is set
        if design.skill_source:
            skill_name = design.skill_source.removeprefix(
                "community:"
            )
            from openjiuwen.auto_harness.infra.skill_source_manager import (
                copy_skill_to_extension,
            )
            copied = copy_skill_to_extension(
                skill_name,
                extension_root,
                ctx.orchestrator.config,
            )
            if not copied:
                logger.warning(
                    "Community skill '%s' not found in cache, "
                    "falling back to agent-generated skill",
                    skill_name,
                )
                design.skill_source = ""

        prompt = _build_implement_ext_prompt(
            design,
            extension_root=extension_root,
            config_path=config_path,
        )

        yield ctx.message(
            f"开始实现扩展: {design.extension_name}"
        )

        session = ctx.runtime.task_session
        if session is not None:
            await session.pre_run(
                inputs={"query": prompt}
            )

        implement_error = ""
        async for chunk in agent.stream(
            {"query": prompt},
            **(
                {"session": session}
                if session is not None
                else {}
            ),
        ):
            yield scope_output_event_stage(
                chunk,
                self.name,
            )
            implement_error = (
                _extract_task_failed_error(chunk)
            )
            if implement_error:
                break

        if implement_error:
            ctx.task.status = TaskStatus.FAILED
            yield StageResult(
                status="failed",
                artifacts={
                    "task_result": CycleResult(
                        success=False,
                        error=implement_error,
                    ),
                },
                messages=[implement_error],
                error=implement_error,
            )
            return

        # Verify agent produced actual files
        if not extension_root.exists():
            error = (
                "Agent did not create extension root: "
                f"{extension_root}"
            )
            ctx.task.status = TaskStatus.FAILED
            yield StageResult(
                status="failed",
                artifacts={
                    "task_result": CycleResult(
                        success=False,
                        error=error,
                    ),
                },
                messages=[error],
                error=error,
            )
            return

        artifact = ExtensionBuildArtifact(
            extension_name=design.extension_name,
            extension_root=str(
                extension_root.resolve()
            ),
            config_path=str(config_path.resolve()),
        )
        yield StageResult(
            artifacts={"extension_build": artifact},
            messages=[
                "Implemented extension: "
                f"{design.extension_name}"
            ],
        )


# ------------------------------------------------------------------
# promote_runtime — plain async function (from promote_runtime.py)
# ------------------------------------------------------------------


async def promote_runtime(
    ctx: "TaskContext",
) -> "RuntimeExtensionArtifact":
    """Promote a verified extension build into the session runtime dir.

    Converts the former ``PromoteRuntimeStage.stream()`` logic into a
    plain async helper that returns the artifact directly.
    """
    build = ctx.require_artifact("extension_build")
    if not isinstance(build, ExtensionBuildArtifact):
        raise TypeError(
            "extension_build artifact must be "
            "ExtensionBuildArtifact"
        )

    session_root = (
        ctx.orchestrator.ensure_session_runtime_dir()
    )
    destination = session_root / build.extension_name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        build.extension_root,
        destination,
    )
    config_path = (
        destination / "harness_config.yaml"
    ).resolve()
    return RuntimeExtensionArtifact(
        extension_name=build.extension_name,
        runtime_path=str(destination.resolve()),
        config_path=str(config_path),
    )


# Backwards-compatible alias
ImplementExtStage = ExtendImplementStage


__all__ = [
    "ImplementStage",
    "MetaImplementStage",
    "ExtendImplementStage",
    "ImplementExtStage",
    "promote_runtime",
    "run_implement_stream",
]
