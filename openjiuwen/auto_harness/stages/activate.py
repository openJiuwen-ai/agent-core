# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Activate stage for auto-harness extend pipeline."""

from __future__ import annotations

# import logging
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.auto_harness.schema import (
    ActivateDecision,
    RuntimeExtensionArtifact,
    StageResult,
    StageSlot,
    VerifyReportArtifact,
)
from openjiuwen.auto_harness.stages.base import (
    TaskStage,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.contexts import (
        TaskContext,
    )

from openjiuwen.core.common.logging import logger


@dataclass
class LoadedComponents:
    """Hot-loaded extension components."""

    rails: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)


class ExtendActivateStage(TaskStage):
    """Activate stage: preview, confirm, then hot-load."""

    name = "activate_ext"
    display_name = "激活扩展"
    slot = StageSlot.ACTIVATE
    consumes = ["runtime_extension", "verify_report"]
    produces = ["activate_decision"]

    async def stream(
        self,
        ctx: "TaskContext",
    ) -> AsyncIterator[Any]:
        runtime_ext = ctx.require_artifact(
            "runtime_extension"
        )
        verify_report = ctx.get_artifact(
            "verify_report", default={}
        )
        session_runtime_path = str(
            ctx.orchestrator.ensure_session_runtime_dir()
        )
        runtime_extensions = _runtime_extensions_snapshot(
            session_runtime_path
        )

        yield OutputSchema(
            type="extension_ready",
            index=0,
            payload={
                "extension_name": (
                    runtime_ext.extension_name
                ),
                "runtime_path": session_runtime_path,
                "session_runtime_path": session_runtime_path,
                "extension_runtime_path": (
                    runtime_ext.runtime_path
                ),
                "config_path": (
                    runtime_ext.config_path
                ),
                "runtime_extensions": runtime_extensions,
                "verify_report": _safe_verify_report(
                    verify_report
                ),
                "components_summary": (
                    _components_summary(verify_report)
                ),
            },
        )

        interaction_id = (
            f"activate:{runtime_ext.extension_name}"
        )
        fut = ctx.orchestrator.create_interaction(
            interaction_id
        )
        yield OutputSchema(
            type="__interaction__",
            index=0,
            payload={
                "interaction_type": "activate_confirm",
                "interaction_id": interaction_id,
                "extension_name": (
                    runtime_ext.extension_name
                ),
                "runtime_path": (
                    runtime_ext.runtime_path
                ),
                "session_runtime_path": session_runtime_path,
                "extension_runtime_path": (
                    runtime_ext.runtime_path
                ),
                "options": ["accept", "reject"],
            },
        )
        decision = await _parse_decision(fut)

        if decision.action == "reject":
            _cleanup_runtime(runtime_ext)
            yield StageResult(
                status="failed",
                error="用户拒绝扩展",
                artifacts={
                    "activate_decision": decision,
                },
            )
            return

        agent = ctx.orchestrator.agent
        if agent is None:
            logger.warning(
                "No DeepAgent on orchestrator, "
                "skipping hot-load enqueue"
            )
            yield StageResult(
                status="success",
                artifacts={
                    "activate_decision": decision,
                },
            )
            return

        agent.enqueue_harness_config(
            runtime_ext.config_path
        )
        loaded = _preview_extension_components(
            runtime_ext
        )
        yield ctx.message(
            f"扩展 {runtime_ext.extension_name} "
            f"已排队热加载: "
            f"{len(loaded.rails)} rails, "
            f"{len(loaded.tools)} tools, "
            f"{len(loaded.skills)} skills\n"
            f"下次普通 query 时生效。"
        )

        guide_parts: list[str] = []
        async for chunk in _stream_testing_guide(
            ctx, runtime_ext, loaded, verify_report
        ):
            payload = chunk.payload
            if isinstance(payload, dict):
                guide_parts.append(
                    payload.get("content", "")
                )
            yield chunk

        guide_text = "".join(guide_parts).strip()
        if guide_text:
            yield OutputSchema(
                type="activate_testing_guide",
                index=0,
                payload={
                    "extension_name": (
                        runtime_ext.extension_name
                    ),
                    "text": guide_text,
                },
            )

        yield StageResult(
            status="success",
            artifacts={
                "activate_decision": decision,
            },
        )



async def _parse_decision(
    fut: Any,
) -> ActivateDecision:
    """Await interaction future and parse response."""
    response = await fut
    if isinstance(response, dict):
        action = response.get("action", "accept")
        feedback = response.get("feedback", "")
        return ActivateDecision(
            action=action, feedback=feedback
        )
    return ActivateDecision(action="accept")


def _safe_verify_report(report: Any) -> dict:
    """Extract serializable fields from verify report."""
    if isinstance(report, VerifyReportArtifact):
        return report.ci_result
    if isinstance(report, dict):
        serializable = {}
        for k, v in report.items():
            if isinstance(v, (str, int, float, bool, list)):
                serializable[k] = v
        return serializable
    return {}


def _components_summary(report: Any) -> dict:
    """Extract component counts from verify report."""
    ci = {}
    if isinstance(report, VerifyReportArtifact):
        ci = report.ci_result
    elif isinstance(report, dict):
        ci = report
    return {
        "rails": ci.get("rails", 0),
        "tools": ci.get("tools", 0),
        "skills": ci.get("skills", 0),
    }


def _preview_extension_components(
    runtime_ext: RuntimeExtensionArtifact,
) -> LoadedComponents:
    """Parse config YAML to preview components without importing."""
    from openjiuwen.harness.harness_config.loader import (
        HarnessConfigLoader,
    )

    resolved = HarnessConfigLoader.load(
        runtime_ext.config_path
    )
    resources = resolved.config.resources
    rails: list[str] = []
    tools: list[str] = []
    skills: list[str] = []
    if resources is not None:
        for r in resources.rails:
            rails.append(
                r.class_name
                or r.name
                or r.module
                or "unknown"
            )
        for t in resources.tools:
            tools.append(
                t.class_name
                or t.name
                or t.module
                or "unknown"
            )
        if resources.skills and resources.skills.dirs:
            root = Path(runtime_ext.runtime_path).resolve()
            for skill_dir in resources.skills.dirs:
                skill_root = root / skill_dir
                if not skill_root.is_dir():
                    continue
                for child in sorted(
                    skill_root.iterdir(),
                    key=lambda p: p.name,
                ):
                    if child.is_dir() and (
                        child / "SKILL.md"
                    ).is_file():
                        skills.append(child.name)
    return LoadedComponents(
        rails=rails, tools=tools, skills=skills,
    )


def _runtime_extensions_snapshot(
    session_runtime_path: str,
) -> list[dict[str, str]]:
    """Return runtime extension packages under the session root."""
    root = Path(session_runtime_path)
    if not root.is_dir():
        return []
    items: list[dict[str, str]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        config_path = child / "harness_config.yaml"
        items.append(
            {
                "extension_name": child.name,
                "runtime_path": str(child.resolve()),
                "config_path": (
                    str(config_path.resolve())
                    if config_path.exists()
                    else ""
                ),
            }
        )
    return items


def _cleanup_runtime(
    runtime_ext: RuntimeExtensionArtifact,
) -> None:
    """Remove runtime directory (no sys.modules cleanup needed)."""
    runtime_path = Path(runtime_ext.runtime_path)
    if runtime_path.exists():
        shutil.rmtree(runtime_path)


def unload_extension(
    runtime_ext: RuntimeExtensionArtifact,
    session_id: str,
) -> None:
    """Unload a hot-loaded extension: clean sys.modules + remove runtime dir."""
    prefix_runtime = (
        f"openjiuwen_runtime_extensions"
        f".{session_id}"
        f".{runtime_ext.extension_name}"
    )
    prefix_official = (
        f"openjiuwen.extensions.harness"
        f".{runtime_ext.extension_name}"
    )
    to_remove = []
    for k in sys.modules:
        if k.startswith(prefix_runtime):
            to_remove.append(k)
            continue
        if k.startswith(prefix_official):
            to_remove.append(k)
    for k in to_remove:
        del sys.modules[k]
    _cleanup_runtime(runtime_ext)


async def _stream_testing_guide(
    ctx: "TaskContext",
    runtime_ext: RuntimeExtensionArtifact,
    loaded: LoadedComponents,
    verify_report: Any,
) -> AsyncIterator[Any]:
    """Stream testing guide as llm_output chunks."""
    from openjiuwen.auto_harness.agents.factory import (
        create_activate_guide_agent,
    )

    design = ctx.get_artifact("extension_target")
    agent = create_activate_guide_agent(
        ctx.orchestrator.config
    )

    query = _build_testing_guide_query(
        runtime_ext, loaded, verify_report, design
    )
    async for chunk in agent.stream({"query": query}):
        chunk_type = getattr(chunk, "type", "")
        if chunk_type == "llm_output":
            yield chunk


def _build_testing_guide_query(
    runtime_ext: RuntimeExtensionArtifact,
    loaded: LoadedComponents,
    verify_report: Any,
    design: Any,
) -> str:
    """Build prompt for the testing guide agent."""
    components = {
        "rails": loaded.rails,
        "tools": loaded.tools,
        "skills": loaded.skills,
    }
    design_info = ""
    if design and hasattr(design, "gap_id"):
        design_info = (
            f"扩展设计目标: gap_id={design.gap_id}, "
            f"components={design.components}, "
            f"file_plan={design.file_plan}"
        )
    verify_info = ""
    if isinstance(verify_report, VerifyReportArtifact):
        verify_info = str(verify_report.ci_result)
    elif isinstance(verify_report, dict):
        verify_info = str(verify_report)

    ext_name = runtime_ext.extension_name
    return (
        f"扩展 {ext_name} 已热加载到 deep agent。\n\n"
        f"已加载组件:\n"
        f"- Rails: {components['rails'] or '无'}\n"
        f"- Tools: {components['tools'] or '无'}\n"
        f"- Skills: {components['skills'] or '无'}\n\n"
        f"{design_info}\n\n"
        f"验证报告: {verify_info}\n\n"
        f"请生成一份简洁的测试引导，包含:\n"
        f"1. 任务总结 - 这个扩展做了什么（1-2 句话）\n"
        f"2. 推荐测试 case - 3-5 个具体的测试场景，"
        f"每个包含输入示例和预期行为\n"
        f"3. 预期效果 - 用户应该观察到什么变化\n"
        f"4. 注意事项 - 可能的边界情况或已知限制\n\n"
        f"用户将退出 auto-harness，"
        f"在普通 query 模式下测试。\n"
        f"如需卸载扩展: "
        f"/auto-harness deactivate {ext_name}\n\n"
        f"用 markdown 格式输出，保持简洁。"
    )
