"""skill pack（JGF）→ SwarmFlow 代码生成。"""

from __future__ import annotations

import re
from typing import Any

from openjiuwen.symphony.flow.distill import topological_order
from openjiuwen.symphony.flow.models import content_hash

_META_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_ALLOWED_OPERATORS = {"agent"}
_AGENT_MODULE = "swarmflow"


def normalize_meta_name(raw: str, *, fallback: str = "combo") -> str:
    """把任意名称规整为 meta.name 允许的 [a-z0-9][a-z0-9-]{0,63}。"""

    text = str(raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9-]+", "-", text).strip("-")
    if not text:
        text = fallback
    if not text[0].isdigit() and not ("a" <= text[0] <= "z"):
        text = f"{fallback}-{text}"
    return text[:64]


def swarmflow_identifier(skill_pack: dict[str, Any], *, recipe_id: str) -> str:
    """meta.name 派生：recipe 前缀 + 组合内容指纹，保证稳定且合法。"""

    return normalize_meta_name(
        f"combo-{content_hash(skill_pack)[len('sha256:') :][:12]}",
        fallback=recipe_id.removeprefix("recipe_") or "combo",
    )


def _step_prompt(
    capability_id: str,
    member_description: str,
    *,
    input_refs: list[str],
) -> str:
    upstream = "。上一步产出：" + " + ".join(input_refs) if input_refs else "。输入来自任务参数"
    return f"使用能力 {capability_id}（{member_description}）完成本阶段任务{upstream}。"


def generate_swarmflow_script(
    skill_pack: dict[str, Any],
    *,
    recipe_id: str,
    task_description: str = "",
    member_descriptions: dict[str, str] | None = None,
) -> str:
    """把 skill pack 机械翻译为 SwarmFlow Python 脚本。

    仅允许白名单算子 from swarmflow import agent；分支边在 v1 中
    按拓扑序线性化（多前驱用字符串拼接传递）。
    """

    order = topological_order(skill_pack)
    descriptions = member_descriptions or {}
    upstream: dict[str, list[str]] = {}
    for edge in skill_pack.get("edges") or []:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source in order and target in order:
            upstream.setdefault(target, []).append(source)

    meta_name = swarmflow_identifier(skill_pack, recipe_id=recipe_id)
    phases = [f"step_{index}" for index in range(1, len(order) + 1)]
    lines: list[str] = []
    lines.append(f'"""SwarmFlow script generated from recipe {recipe_id}."""')
    lines.append("")
    lines.append(f"from {_AGENT_MODULE} import agent")
    lines.append("")
    lines.append(f"META = {dict(name=meta_name, phases=phases)!r}")
    lines.append("")
    if task_description:
        lines.append(f"TASK = {task_description!r}")
        lines.append("")
    lines.append("async def run(args):")
    if not order:
        lines.append("    return None")
        lines.append("")
        return "\n".join(lines)
    lines.append('    """按 skill pack 拓扑序执行能力组合。"""')
    for index, capability_id in enumerate(order, start=1):
        prompt = _step_prompt(
            capability_id,
            descriptions.get(capability_id, "协作成员能力"),
            input_refs=upstream.get(capability_id, []),
        )
        lines.append(f"    step_{index} = await agent({prompt!r}, label={capability_id!r}, phase='step_{index}')")
    lines.append(f"    return step_{len(order)}")
    lines.append("")
    return "\n".join(lines)


def validate_meta_name(name: str) -> bool:
    return bool(_META_NAME_PATTERN.match(name or ""))


def validate_generated_script(source: str) -> list[str]:
    """静态校验生成脚本：语法、导入与调用白名单。"""

    import ast

    problems: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"script has syntax error: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module != _AGENT_MODULE:
                problems.append(f"disallowed import from: {module}")
            for alias in node.names:
                if alias.name not in _ALLOWED_OPERATORS:
                    problems.append(f"disallowed operator import: {alias.name}")
        elif isinstance(node, ast.Import):
            problems.append("plain import statements are not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in _ALLOWED_OPERATORS:
                problems.append(f"disallowed call: {node.func.id}")
    return problems
