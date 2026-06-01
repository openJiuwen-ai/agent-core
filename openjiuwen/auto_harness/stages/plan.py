# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Plan stage hierarchy — base class and concrete implementations."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
)

from openjiuwen.core.common.logging import logger
from openjiuwen.auto_harness.contexts import (
    SessionContext,
)
from openjiuwen.auto_harness.infra.edit_scope import (
    render_edit_scope,
)
from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
    parse_extension_designs,
    parse_tasks,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    ExtensionDesign,
    ExtensionDesignArtifact,
    Gap,
    GapAnalysisArtifact,
    StageResult,
    TaskPlanArtifact,
)
from openjiuwen.auto_harness.stages.base import (
    SessionStage,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.experience.experience_store import (
        ExperienceStore,
    )


# ------------------------------------------------------------------
# Abstract base
# ------------------------------------------------------------------


class PlanStage(SessionStage):
    """Abstract base for all plan-family stages."""

    name = "plan"
    slot = "plan"
    display_name = "制定优化计划"
    description = "Plan optimization tasks."
    produces = ["task_plan"]

    async def stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        raise NotImplementedError


# ------------------------------------------------------------------
# Concrete: meta-plan (original PlanStage logic)
# ------------------------------------------------------------------


class MetaPlanStage(PlanStage):
    """Generate the task plan for the current session."""

    consumes = ["assessment"]

    async def stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        input_tasks = ctx.get_artifact(
            "input_tasks", default=[]
        )
        input_task_list = (
            list(input_tasks)
            if isinstance(input_tasks, list)
            else []
        )
        assessment_artifact = ctx.get_artifact("assessment")
        assessment = getattr(
            assessment_artifact,
            "report",
            "",
        )
        messages: list[str] = []
        plan_text = ""
        async for chunk in run_plan_stream(
            ctx.orchestrator.config,
            assessment,
            ctx.orchestrator.experience_store,
            input_tasks=input_task_list,
            extra_rails=ctx.orchestrator.stream_rails or None,
        ):
            text = extract_text(chunk)
            if text:
                plan_text += text
            yield chunk
        if plan_text.strip():
            path = (
                Path(ctx.orchestrator.paths.runs_dir)
                / "latest_plan.md"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(plan_text, encoding="utf-8")
            messages.append(
                f"规划原始输出已保存: {path}"
            )
        tasks = parse_tasks(plan_text)
        if len(tasks) > 1:
            tasks = tasks[:1]
            messages.append(
                "规划阶段只保留最高优先级的 1 个任务"
            )
        if not tasks:
            if input_task_list:
                tasks = input_task_list[:1]
                messages.append(
                    "规划阶段未生成任务，回退执行最高优先级输入任务"
                )
            else:
                messages.append("规划阶段未生成任务，session 结束")
        yield StageResult(
            artifacts={
                "task_plan": TaskPlanArtifact(
                    tasks=list(tasks),
                    raw_plan=plan_text,
                )
            },
            messages=messages,
        )


# ------------------------------------------------------------------
# Concrete: extension design plan
# ------------------------------------------------------------------


class ExtendPlanStage(PlanStage):
    """Convert gaps into concrete extension designs."""

    name = "plan_ext"
    display_name = "设计扩展方案"
    description = "Design runtime extensions from analyzed gaps."
    consumes = ["gap_analysis"]
    produces = ["extension_design"]

    async def stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        analysis = ctx.require_artifact("gap_analysis")
        if not isinstance(analysis, GapAnalysisArtifact):
            raise TypeError(
                "gap_analysis artifact must be "
                "GapAnalysisArtifact"
            )

        # Stream agent-based design
        designs: list[ExtensionDesign] = []
        package_name_raw: str | None = None
        if analysis.gaps:
            output = ""
            try:
                from openjiuwen.auto_harness.agents import (
                    create_design_ext_agent,
                )
                from openjiuwen.auto_harness.infra.skill_source_manager import (
                    format_community_skill_list,
                )

                # Get available community skills for reuse
                community_skill_list = format_community_skill_list(
                    ctx.orchestrator.config,
                )

                # Run design_ext agent
                agent = create_design_ext_agent(
                    ctx.orchestrator.config,
                    extra_rails=ctx.orchestrator.stream_rails or None,
                )
                query = _build_design_query(
                    analysis,
                    max_designs=(
                        ctx.orchestrator.config
                        .max_tasks_per_session
                    ),
                    community_skill_list=community_skill_list,
                )
                async for chunk in agent.stream(
                    {"query": query}
                ):
                    text = extract_text(chunk)
                    if text:
                        output += text
                    yield chunk
                package_name_raw, designs = parse_extension_designs(output)
            except Exception:
                logger.exception(
                    "Agent extension design failed"
                )

        # Fallback to heuristic if agent fails
        if not designs:
            logger.warning(
                "Agent design returned no results, "
                "falling back to heuristic"
            )
            designs = _build_fallback_designs(
                analysis.gaps,
                max_capabilities=(
                    ctx.orchestrator.config
                    .max_tasks_per_session
                ),
            )

        designs = _cap_extension_designs(
            designs,
            ctx.orchestrator.config.max_tasks_per_session,
        )

        timestamp = int(time.time())
        package_name = ""
        if package_name_raw:
            package_name = f"{package_name_raw}_{timestamp}"
            logger.info(
                "[ExtendPlanStage] using parsed package_name: %s",
                package_name,
            )

        artifact = ExtensionDesignArtifact(
            designs=designs,
            package_name=package_name,
        )
        messages = [
            "Extension design complete: "
            f"{len(designs)} design(s)"
        ]
        if designs:
            messages.append(
                "Designs: "
                + ", ".join(
                    design.extension_name
                    for design in designs
                )
            )
        if designs:
            path = _persist_extension_designs(
                ctx.orchestrator.paths.runs_dir,
                artifact,
            )
            messages.append(
                f"扩展设计已保存: {path}"
            )

        yield StageResult(
            artifacts={
                "extension_design": artifact
            },
            messages=messages,
        )


# Backwards-compat alias
DesignExtStage = ExtendPlanStage


def _persist_extension_designs(
    runs_dir: str,
    artifact: ExtensionDesignArtifact,
) -> str:
    """Persist design_ext output for implement-only/debug replay."""
    payload = {
        "designs": [
            asdict(design)
            for design in artifact.designs
        ],
        "package_name": artifact.package_name,
    }
    runs_path = Path(runs_dir)
    runs_path.mkdir(parents=True, exist_ok=True)
    latest_path = runs_path / "latest_extension_design.json"
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )
    latest_path.write_text(content, encoding="utf-8")
    stamped_path = runs_path / (
        f"extension_design_"
        f"{int(latest_path.stat().st_mtime * 1000)}.json"
    )
    stamped_path.write_text(content, encoding="utf-8")
    return str(latest_path)


# ------------------------------------------------------------------
# Helper functions (MetaPlanStage)
# ------------------------------------------------------------------


async def run_plan_stream(
    config: AutoHarnessConfig,
    assessment: str,
    experience_store: "ExperienceStore",
    *,
    input_tasks: list | None = None,
    extra_rails: list | None = None,
) -> AsyncIterator[Any]:
    """用 DeepAgent 生成任务列表（流式）。

    Args:
        config: Auto Harness 配置。
        assessment: 评估报告文本。
        experience_store: ExperienceStore 实例。

    Yields:
        OutputSchema chunks from plan agent.
    """
    from openjiuwen.auto_harness.agents import (
        create_plan_agent,
    )

    agent = create_plan_agent(
        config, extra_rails=extra_rails,
    )
    query = await _build_plan_query(
        config,
        assessment,
        experience_store,
        input_tasks=input_tasks,
    )

    async for chunk in agent.stream(
        {"query": query},
    ):
        yield chunk


async def _build_plan_query(
    config: AutoHarnessConfig,
    assessment: str,
    experience_store: "ExperienceStore",
    *,
    input_tasks: list | None = None,
) -> str:
    """组装 plan agent 的 query。"""
    recent = await experience_store.list_recent(limit=5)
    experiences_text = "\n".join(
        f"- [{e.type.value}] {e.topic}: "
        f"{e.summary}"
        for e in recent
    ) or "无"
    edit_scope = render_edit_scope(
        "本轮任务规划必须遵守的范围"
    )
    task_focus = _format_input_tasks(input_tasks or [])

    return (
        f"本轮目标:\n"
        f"{config.optimization_goal or '无'}\n\n"
        f"{task_focus}\n\n"
        f"{edit_scope}\n\n"
        f"评估报告:\n{assessment}\n\n"
        f"近期经验:\n{experiences_text}\n\n"
        f"配置任务上限: "
        f"{config.max_tasks_per_session}\n"
        "规划阶段实际输出上限: 1\n"
        f"自驱动槽位: "
        f"{config.self_driven_slots}\n"
        "你本轮只能输出 1 个最高优先级任务，不要输出多个候选。"
        "你输出的每个任务 `files` 都必须只包含上述范围内的路径。"
        "如果某个候选任务需要改动范围外源码目录，直接丢弃该任务，"
        "不要输出到计划里。\n"
    )


def _format_input_tasks(tasks: list) -> str:
    """Render user-provided tasks as plan focus."""
    if not tasks:
        return "显式输入任务: 无"
    lines = [
        "显式输入任务（作为规划焦点；如调研后仍合理，优先产出对应任务）:"
    ]
    for task in tasks:
        topic = getattr(task, "topic", "")
        description = getattr(task, "description", "") or topic
        files = ", ".join(getattr(task, "files", []) or [])
        lines.append(
            f"- {topic}: {description}; files={files or '未指定'}"
        )
    return "\n".join(lines)


# ------------------------------------------------------------------
# Helper functions (ExtendPlanStage)
# ------------------------------------------------------------------


def _build_design_query(
    analysis: GapAnalysisArtifact,
    *,
    max_designs: int = 10,
    community_skill_list: str = "",
) -> str:
    """Build the query prompt for the design_ext agent."""
    gap_lines: list[str] = []
    for gap in analysis.gaps:
        source_tag = (
            f" (来源/参考对象: {gap.competitor})"
            if gap.competitor
            else ""
        )
        gap_lines.append(
            f"- [{gap.id}] {gap.feature}"
            f"{source_tag}: "
            f"{gap.gap_description} "
            f"(impact={gap.impact}, "
            f"feasibility={gap.feasibility})"
        )
    gap_summary = "\n".join(gap_lines)

    query = (
        "根据以下 runtime extension 能力缺口分析结果，"
        "为每个独立 gap 设计一个 harness 运行时扩展方案，"
        f"最多输出 {max(0, max_designs)} 个 ExtensionDesign。\n\n"
        "组件选择规则：按用户目标选择最轻组件组合。"
        "Tool 用于生成文件、调用 API、封装 CLI 或执行明确动作；"
        "Skill 用于承载领域规范、模板原则、生成流程和示例；"
        "设计 Skill 时必须参考 skill-creator 原则，规划准确的"
        "name/description、精简可操作的 SKILL.md，并在需要模板、"
        "品牌素材或详细参考资料时规划 assets/ 或 references/；"
        "Rail 只在需要拦截会话、后台监听、周期触发、审计或"
        "动态注入上下文时使用，不要为了完整性强行添加 Rail。\n"
        "办公/PPT/报告/文件生成类扩展通常优先设计为 "
        '`["tool", "skill"]`，'
        "除非 gap 明确需要生命周期拦截或后台触发。\n\n"
        "真实产物契约：如果扩展承诺生成 PPT、DOCX、PDF、JSON、"
        "图片、报告或其他文件，设计必须明确 Tool 的输入、输出路径、"
        "返回字段和成功条件。成功条件至少包含：目标文件存在、"
        "文件后缀/格式正确、size_bytes > 0、返回 absolute_path/"
        "exists/format/size_bytes 等结构化字段。PPTX/DOCX 必须是合法 "
        "zip 包并包含关键内部结构；PPTX 至少包含 "
        "`ppt/presentation.xml` 和 `ppt/slides/slide*.xml`；"
        "不得设计 JSON/Markdown/纯文本"
        "占位来冒充二进制产物。\n\n"
        "设计拆分标准：每个可独立实现和验证的 gap 输出一个 design。"
        "全局硬约束必须输出为独立 constraint design，普通新增能力"
        "输出为 capability design。必须保留用户目标中的"
        "关键实体和产物类型，例如“PPT”“办公拓展”，"
        "不要泛化成需求收集、需求报告或普通办公扩展。\n\n"
        f"差距列表:\n{gap_summary}\n\n"
        "命名规则：extension_name 必须是能表达用户能力的"
        "snake_case 名称。若 gap 来自明确竞品，可使用竞品名前缀；"
        "若来源是用户需求或领域范式，应按能力命名，例如 "
        "`excel_financial_generator`、`office_ppt_generator`。"
        "不要使用 `user_demand_*` 这类丢失具体产物和场景的泛名。\n\n"
        "首先输出 session 级别的扩展包名称：\n"
        "- package_name: snake_case，表达本轮优化的核心能力\n"
        "- 保留用户目标关键实体（如品牌、产物类型）\n"
        "- 不超过 30 字符（不含 timestamp 后缀）\n"
        "- 不要泛化名称（如 office_tools、user_demand）\n\n"
        "输出 JSON 对象格式：\n"
        '{"package_name": "...", "designs": [...]}\n\n'
        "designs 数组元素包含:\n"
        "- gap_id: 对应的 gap ID\n"
        "- extension_name: 扩展名称 "
        "(snake_case，保留用户目标关键实体)\n"
        "- kind: capability 或 constraint；默认 capability。"
        "全局硬约束、写入前强制检查、所有文件命名约束必须使用 "
        "constraint\n"
        "- depends_on: 依赖的 constraint extension_name 列表；"
        "无依赖时为空数组\n"
        "- applies_to: constraint 适用的能力扩展名称列表；"
        "全局适用时可为空数组\n"
        "- components: 组件列表 "
        "(按需选择 rail/tool/skill；不要强制包含 rail)\n"
        "- skill_source: 空字符串表示从零生成 skill；"
        "'community:<skill_name>' 表示复用社区 skill(匹配时优先使用)\n"
        "- file_plan: 文件规划 "
        '{"root": "...", "manifest": "..."}\n'
        "- harness_config_patch: harness 配置补丁\n"
    )

    # Append community skill list for reuse reference
    if community_skill_list:
        query += "\n\n" + community_skill_list + "\n"

    return query


def _cap_extension_designs(
    designs: list[ExtensionDesign],
    max_designs: int,
) -> list[ExtensionDesign]:
    """Limit extension designs to the configured task budget.

    Constraint extensions run first in the session pipeline, so keep
    them ahead of capabilities when the model returns more designs
    than the configured session task limit.
    """
    limit = max(0, max_designs)
    if limit == 0:
        return []
    constraints = [
        design
        for design in designs
        if design.kind == "constraint"
    ]
    capabilities = [
        design
        for design in designs
        if design.kind != "constraint"
    ]
    return (constraints + capabilities)[:limit]


def _slugify(value: str) -> str:
    """Convert a string to a safe snake_case identifier."""
    slug = re.sub(
        r"[^a-z0-9]+",
        "_",
        value.lower(),
    ).strip("_")
    return slug or "runtime_extension"


def _infer_extension_components(gap: Gap) -> list[str]:
    """Infer a conservative component set for fallback designs."""
    text = " ".join(
        [
            gap.feature or "",
            gap.gap_description or "",
            gap.suggested_approach or "",
        ]
    ).lower()
    components: list[str] = []
    action_keywords = (
        "ppt",
        "powerpoint",
        "报告",
        "文档",
        "文件",
        "生成",
        "导出",
        "api",
        "cli",
        "tool",
    )
    skill_keywords = (
        "风格",
        "模板",
        "规范",
        "领域",
        "指南",
        "skill",
        "最佳实践",
    )
    rail_keywords = (
        "拦截",
        "监听",
        "后台",
        "周期",
        "每 n 次",
        "每n次",
        "审计",
        "提醒",
        "累计",
        "rail",
    )
    if any(keyword in text for keyword in rail_keywords):
        components.append("rail")
    if any(keyword in text for keyword in action_keywords):
        components.append("tool")
    if any(keyword in text for keyword in skill_keywords):
        components.append("skill")
    if not components:
        components.append("tool")
    return components


def _infer_extension_kind(gap: Gap) -> str:
    """Infer whether a fallback gap is a constraint or capability."""
    text = " ".join(
        [
            gap.feature or "",
            gap.gap_description or "",
            gap.suggested_approach or "",
        ]
    ).lower()
    constraint_signals = (
        "constraint",
        "guard",
        "硬约束",
        "硬性约束",
        "强制",
        "必须",
        "不得",
        "禁止",
        "所有文件",
        "写入前",
        "文件名",
        "后缀",
    )
    enforcement_signals = (
        "检查",
        "校验",
        "拦截",
        "阻止",
        "命名",
        "文件名",
        "后缀",
    )
    if any(item in text for item in constraint_signals) and any(
        item in text for item in enforcement_signals
    ):
        return "constraint"
    return "capability"


def _source_name_prefix(source: str) -> str:
    """Return a naming prefix only for explicit product/tool sources."""
    normalized = source.strip().lower()
    generic_sources = (
        "用户需求",
        "领域范式",
        "办公自动化",
        "ppt生成工具",
        "报告生成流程",
        "user demand",
        "user_requirement",
        "domain_pattern",
    )
    if not normalized or any(
        item in normalized for item in generic_sources
    ):
        return ""
    slug = _slugify(source)
    if slug == "runtime_extension":
        return ""
    return slug


def _build_design(gap: Gap) -> ExtensionDesign:
    """Heuristic fallback: convert a gap to an extension design.

    Note: skill_source is intentionally left empty in fallback mode.
    Skill matching is handled by the primary agent path via model-based
    pre-filtering, not by heuristic keyword matching.
    """
    feature_slug = _slugify(gap.feature or "")
    competitor_slug = _source_name_prefix(gap.competitor)
    if competitor_slug and feature_slug:
        extension_name = (
            f"{competitor_slug}_{feature_slug}"
        )
    elif feature_slug:
        extension_name = feature_slug
    else:
        extension_name = _slugify(
            gap.id or "runtime_extension"
        )
    module_base = (
        "openjiuwen.extensions.harness."
        f"{extension_name}"
    )
    kind = _infer_extension_kind(gap)
    components = _infer_extension_components(gap)
    if kind == "constraint" and "rail" not in components:
        components.insert(0, "rail")
    resources: dict[str, Any] = {}
    if "rail" in components:
        resources["rails"] = [
            {
                "type": "package",
                "module": (
                    f"{module_base}.rails."
                    "extension_rail"
                ),
                "class": "ExtensionRail",
            }
        ]
    if "tool" in components:
        resources["tools"] = [
            {
                "type": "package",
                "module": (
                    f"{module_base}.tools."
                    "extension_tool"
                ),
                "class": "ExtensionTool",
            }
        ]
    if "skill" in components:
        resources["skills"] = {"dirs": ["skills/"]}
    return ExtensionDesign(
        gap_id=gap.id,
        extension_name=extension_name,
        kind=kind,
        components=components,
        file_plan={
            "root": (
                "openjiuwen/extensions/harness/"
                f"{extension_name}"
            ),
            "manifest": (
                "openjiuwen/extensions/harness/"
                f"{extension_name}/harness_config.yaml"
            ),
        },
        harness_config_patch={"resources": resources},
    )


def _build_fallback_designs(
    gaps: list[Gap],
    *,
    max_capabilities: int,
) -> list[ExtensionDesign]:
    """Build multiple fallback designs, preserving all constraints."""
    designs = []
    sorted_gaps = sorted(
        gaps,
        key=lambda g: g.priority,
        reverse=True,
    )
    for gap in sorted_gaps:
        designs.append(_build_design(gap))
    constraints = [
        design
        for design in designs
        if design.kind == "constraint"
    ]
    capabilities = [
        design
        for design in designs
        if design.kind != "constraint"
    ][:max(0, max_capabilities)]
    return constraints + capabilities

__all__ = [
    "PlanStage",
    "MetaPlanStage",
    "ExtendPlanStage",
]
