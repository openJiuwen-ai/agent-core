"""目标产物渲染：统一能力包 → 组合 Skill 目录结构。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from openjiuwen.symphony.flow.models import (
    TARGET_KIND_SKILL,
)


class UnsupportedTargetException(ValueError):
    """target_kind 暂不支持（v1 仅 skill）。"""


class SkillPackNotInstallableError(ValueError):
    """A reviewed package cannot be represented by an SDD-0010 SkillPack."""


@runtime_checkable
class SkillArtifactAdapter(Protocol):
    """Minimal rendering boundary used by Symphony Flow hosts."""

    def render(self, package: dict[str, Any], artifact_dir: str | Path) -> list[Path]:
        """Render one reviewed package into an isolated artifact directory."""

        ...


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


class SkillPackAdapter:
    """Render a reviewed package as one SDD-0010 SkillPack root document."""

    @staticmethod
    def render(package: dict[str, Any], artifact_dir: str | Path) -> list[Path]:
        materials = package.get("materials")
        materials = materials if isinstance(materials, dict) else {}
        recipe = materials.get("recipe")
        recipe = recipe if isinstance(recipe, dict) else {}
        applicability = recipe.get("applicability")
        applicability = applicability if isinstance(applicability, dict) else {}
        structure = recipe.get("combination_structure")
        structure = structure if isinstance(structure, dict) else {}
        members, edges = _skillpack_chain(structure)

        name = str(package.get("meta_name") or "symphony-combination").strip()
        task_description = _compact_text(
            applicability.get("task_description"),
            fallback="由 Symphony 成功执行生成的组合 Skill",
        )
        description = task_description if task_description.startswith("[技能包]") else f"[技能包] {task_description}"
        trigger = _compact_text(applicability.get("trigger_conditions"))
        nodes = structure.get("nodes")
        nodes = nodes if isinstance(nodes, dict) else {}

        frontmatter = yaml.safe_dump(
            {
                "name": name,
                "kind": "skillpack",
                "description": description,
                "skills": members,
            },
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        workflow_graph = {
            "graph": {
                "id": f"{name}-workflow",
                "type": "skillpack_workflow",
                "label": task_description,
                "directed": True,
                "nodes": {member: {"label": member, "metadata": {"skill": member}} for member in members},
                "edges": edges,
            }
        }
        body = "\n".join(
            [
                f"# {name}",
                "",
                "## When to use",
                "",
                task_description,
                *(["", f"触发条件：{trigger}"] if trigger and trigger != task_description else []),
                "",
                "## Do not use",
                "",
                "仅需要其中一个成员 Skill 的单项能力时，不使用本技能包。",
                "",
                "## Required inputs",
                "",
                *_required_input_lines(members, nodes),
                "",
                "## Side effects and confirmation",
                "",
                "遵循各成员 Skill 的权限与确认要求，不扩大其工具权限或副作用范围。",
                "",
                "## Included Skills",
                "",
                *_included_skill_lines(members, nodes),
                "",
                "## Execution Process",
                "",
                *_execution_process_lines(members, nodes),
                "",
                "## Failure handling",
                "",
                "成员执行失败时停止依赖该结果的后续步骤，并返回已有结果、失败原因和未完成步骤。",
                "",
                "## Final output",
                "",
                *_final_output_lines(members[-1], nodes),
                "",
                "## Workflow Graph",
                "",
                "```json",
                json.dumps(workflow_graph, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
        root = Path(artifact_dir)
        root.mkdir(parents=True, exist_ok=True)
        skill_md = root / "SKILL.md"
        skill_md.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
        return [skill_md]


def _skillpack_chain(structure: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    nodes = structure.get("nodes")
    raw_edges = structure.get("edges")
    if not isinstance(nodes, dict) or len(nodes) < 2 or not isinstance(raw_edges, list):
        raise SkillPackNotInstallableError("recipe is not a Skill chain")

    member_ids = {str(node_id) for node_id in nodes}
    indegree = {member: 0 for member in member_ids}
    adjacency: dict[str, str] = {}
    edges: list[dict[str, str]] = []
    for node_id, node in nodes.items():
        metadata = node.get("metadata") if isinstance(node, dict) else None
        capability_type = (
            str(metadata.get("capability_type") or "").strip().casefold() if isinstance(metadata, dict) else ""
        )
        if capability_type != "skill":
            raise SkillPackNotInstallableError(f"capability {node_id!s} is not a Skill")
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, dict):
            raise SkillPackNotInstallableError("recipe edge is invalid")
        source = str(raw_edge.get("source") or "")
        target = str(raw_edge.get("target") or "")
        relation = str(raw_edge.get("relation") or "can_feed")
        if source not in member_ids or target not in member_ids or source == target:
            raise SkillPackNotInstallableError("recipe is not a simple Skill chain")
        if relation != "can_feed" or source in adjacency:
            raise SkillPackNotInstallableError("recipe is not a simple Skill chain")
        adjacency[source] = target
        indegree[target] += 1
        edges.append({"source": source, "target": target, "relation": "can_feed"})

    starts = [member for member, degree in indegree.items() if degree == 0]
    if len(edges) != len(member_ids) - 1 or len(starts) != 1:
        raise SkillPackNotInstallableError("recipe is not a simple Skill chain")
    ordered: list[str] = []
    current = starts[0]
    while current not in ordered:
        ordered.append(current)
        if current not in adjacency:
            break
        current = adjacency[current]
    if len(ordered) != len(member_ids):
        raise SkillPackNotInstallableError("recipe is not a simple Skill chain")
    return ordered, edges


def _node_metadata(nodes: dict[str, Any], member: str) -> dict[str, Any]:
    node = nodes.get(member)
    if not isinstance(node, dict):
        return {}
    metadata = node.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _compact_text(value: Any, *, fallback: str = "", limit: int | None = None) -> str:
    text = " ".join(str(value or "").split()) or fallback
    if limit is not None and len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _included_skill_lines(members: list[str], nodes: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for member in members:
        description = _compact_text(
            _node_metadata(nodes, member).get("description"),
            fallback="具体用途见该成员的 SKILL.md。",
            limit=240,
        )
        lines.append(f"- `{member}`：{description}")
    return lines


def _ports(metadata: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = metadata.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _required_input_lines(members: list[str], nodes: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for member in members:
        for item in _ports(_node_metadata(nodes, member), "inputs"):
            if item.get("required") is not True:
                continue
            name = _compact_text(item.get("name"))
            port_type = _compact_text(item.get("type"))
            description = _compact_text(item.get("description"))
            detail = f"（{port_type}）" if port_type else ""
            suffix = f"：{description}" if description else ""
            lines.append(f"- `{member}.{name}`{detail}{suffix}")
    return lines or ["提供完整的用户目标；各成员未声明额外的必填输入。"]


def _execution_process_lines(members: list[str], nodes: dict[str, Any]) -> list[str]:
    lines = ["当前为线性流程，无可并行步骤。", ""]
    for index, member in enumerate(members, start=1):
        if index == 1:
            input_text = "接收用户原始目标和该成员声明的必要输入"
        else:
            input_text = f"接收用户原始目标，并将 `{members[index - 2]}` 的完整输出作为上下文输入"
        outputs = [
            f"`{_compact_text(item.get('name'))}`"
            for item in _ports(_node_metadata(nodes, member), "outputs")
            if _compact_text(item.get("name"))
        ]
        output_text = "、".join(outputs) if outputs else "本步骤结果"
        lines.append(f"{index}. 调用 `{member}`：{input_text}；产出 {output_text}。")
    lines.extend(["", f"结果汇合：以 `{members[-1]}` 的输出作为最终结果，前序结果作为上下文和依据。"])
    return lines


def _final_output_lines(member: str, nodes: dict[str, Any]) -> list[str]:
    outputs = _ports(_node_metadata(nodes, member), "outputs")
    if not outputs:
        return [f"返回 `{member}` 生成的最终结果，并说明任何失败、跳过或不完整部分。"]
    lines = [f"返回 `{member}` 生成的以下最终产物："]
    for item in outputs:
        name = _compact_text(item.get("name"))
        description = _compact_text(item.get("description"))
        suffix = f"：{description}" if description else ""
        lines.append(f"- `{name}`{suffix}")
    lines.append("并说明任何失败、跳过或不完整部分。")
    return lines


def render_package(
    package: dict[str, Any],
    artifact_dir: str | Path,
) -> list[Path]:
    """按 target_kind 分发渲染；v1 仅支持 skill。"""

    target_kind = str(package.get("target_kind") or TARGET_KIND_SKILL)
    if target_kind == TARGET_KIND_SKILL:
        return SkillAdapter.render(package, artifact_dir)
    raise UnsupportedTargetException(f"target_kind {target_kind!r} is not supported yet (v1: skill only)")
