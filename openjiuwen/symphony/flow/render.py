"""目标产物渲染：统一能力包 → 组合 Skill 目录结构。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.symphony.flow.models import (
    TARGET_KIND_SKILL,
)


class UnsupportedTargetException(ValueError):
    """target_kind 暂不支持（v1 仅 skill）。"""


class SkillAdapter:
    """渲染为 Skill：SKILL.md + swarmflow/run.py + dependencies.yaml。"""

    @staticmethod
    def render(
        package: dict[str, Any],
        artifact_dir: str | Path,
    ) -> list[Path]:
        materials = package.get("materials") or {}
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        skill_md = SkillAdapter.render_skill_markdown(package)
        run_py = str(materials.get("swarmflow_script") or "")
        dependencies_yaml = SkillAdapter.render_dependencies_yaml(package)

        outputs = [
            artifact_dir / "SKILL.md",
            artifact_dir / "dependencies.yaml",
        ]
        swarmflow_dir = artifact_dir / "swarmflow"
        swarmflow_dir.mkdir(parents=True, exist_ok=True)
        run_path = swarmflow_dir / "run.py"
        run_path.write_text(run_py, encoding="utf-8")
        (artifact_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        (artifact_dir / "dependencies.yaml").write_text(dependencies_yaml, encoding="utf-8")
        return outputs + [run_path]

    @staticmethod
    def render_skill_markdown(package: dict[str, Any]) -> str:
        materials = package.get("materials") or {}
        recipe = materials.get("recipe") or {}
        applicability = recipe.get("applicability") or {}
        narrative = str(recipe.get("execution_narrative") or "")
        task_description = str(applicability.get("task_description") or "")
        trigger = str(applicability.get("trigger_conditions") or "")
        examples = [str(item) for item in (applicability.get("example_requests") or [])]
        members = sorted((recipe.get("combination_structure") or {}).get("nodes", {}).keys())
        lines: list[str] = [
            f"# {package.get('meta_name') or 'capability-combo'}",
            "",
        ]
        if task_description:
            lines += ["## 任务描述", "", task_description, ""]
        if trigger:
            lines += ["## 适用场景", "", trigger, ""]
        if members:
            lines += [
                "## 能力组合",
                "",
                *[f"- {member}" for member in members],
                "",
            ]
        if narrative:
            lines += ["## 执行过程", "", narrative, ""]
        if examples:
            lines += ["## 示例请求", "", *[f"- {example}" for example in examples], ""]
        lines += [
            "## 执行入口",
            "",
            "见 `swarmflow/run.py`（由 skill pack 生成的 SwarmFlow 编排脚本）。",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def render_dependencies_yaml(package: dict[str, Any]) -> str:
        materials = package.get("materials") or {}
        recipe = materials.get("recipe") or {}
        nodes = (recipe.get("combination_structure") or {}).get("nodes") or {}
        lines = ["# 依赖能力清单（由能力包生成）", "capabilities:"]
        for member_id in sorted(nodes):
            metadata = (nodes.get(member_id) or {}).get("metadata") or {}
            version = str(metadata.get("version") or "unknown")
            content_hash = str(metadata.get("content_hash") or "")
            lines.append(f"  - id: {member_id}")
            lines.append(f"    version: {version}")
            if content_hash:
                lines.append(f"    content_hash: {content_hash}")
        return "\n".join(lines) + "\n"


def render_package(
    package: dict[str, Any],
    artifact_dir: str | Path,
) -> list[Path]:
    """按 target_kind 分发渲染；v1 仅支持 skill。"""

    target_kind = str(package.get("target_kind") or TARGET_KIND_SKILL)
    if target_kind == TARGET_KIND_SKILL:
        return SkillAdapter.render(package, artifact_dir)
    raise UnsupportedTargetException(f"target_kind {target_kind!r} is not supported yet (v1: skill only)")
