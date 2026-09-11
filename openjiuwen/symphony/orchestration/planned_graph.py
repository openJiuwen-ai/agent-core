"""Minimal JGF v2 projection for public orchestration plans."""

from __future__ import annotations

from typing import Any

from openjiuwen.symphony.orchestration.artifacts import GraphArtifacts


def build_planned_graph(
    planning_result: dict[str, Any],
    artifacts: GraphArtifacts,
) -> dict[str, Any]:
    """Project the selected internal plan into one minimal JGF v2 graph."""

    primary_plan = select_primary_plan(planning_result)
    status = _plan_status(primary_plan, planning_result)
    metadata: dict[str, Any] = {"status": status}
    reason = _plan_text(primary_plan, planning_result, "reason")
    if reason:
        metadata["reason"] = reason
    missing_inputs = _missing_input_names(primary_plan.get("missing_inputs"))
    if missing_inputs:
        metadata["missing_inputs"] = missing_inputs

    nodes = _planned_nodes(primary_plan, artifacts)
    edges = _planned_edges(primary_plan, nodes)
    return {
        "graph": {
            "id": str(planning_result.get("plan_id") or ""),
            "type": "planned_graph",
            "directed": True,
            "metadata": metadata,
            "nodes": nodes,
            "edges": edges,
        }
    }


def select_primary_plan(planning_result: dict[str, Any]) -> dict[str, Any]:
    """Return the planner's selected plan without synthesizing a fallback."""

    for key in ("recommended_plans", "plans"):
        plans = planning_result.get(key)
        if isinstance(plans, list):
            for plan in plans:
                if isinstance(plan, dict):
                    return plan
    return {}


def _plan_status(primary_plan: dict[str, Any], planning_result: dict[str, Any]) -> str:
    status = _plan_text(primary_plan, planning_result, "status").lower()
    return status if status in {"ready", "needs_input", "no_plan"} else "no_plan"


def _plan_text(primary_plan: dict[str, Any], planning_result: dict[str, Any], key: str) -> str:
    value = primary_plan.get(key)
    if value in (None, ""):
        value = planning_result.get(key)
    return str(value or "").strip()


def _planned_nodes(primary_plan: dict[str, Any], artifacts: GraphArtifacts) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for step in primary_plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        capability_id = _capability_id(
            step.get("capability_id") or step.get("skill_id") or step.get("id")
        )
        if not capability_id or capability_id in nodes:
            continue
        capability = artifacts.skill_by_id.get(capability_id)
        if capability is None:
            raise ValueError(f"Planned graph references unknown capability: {capability_id!r}.")
        nodes[capability_id] = {
            "label": str(capability.get("name") or capability_id),
            "metadata": {
                "type": str(capability.get("type") or capability.get("capability_type") or "skill"),
            },
        }
    return nodes


def _planned_edges(primary_plan: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for edge in primary_plan.get("can_feed_edges") or []:
        if not isinstance(edge, dict):
            continue
        source = _capability_id(edge.get("source") or edge.get("source_id"))
        target = _capability_id(edge.get("target") or edge.get("target_id"))
        if source not in nodes or target not in nodes:
            raise ValueError(
                "Planned graph edge endpoints must be selected capability nodes: "
                f"source={source!r}, target={target!r}."
            )
        edges.append({"source": source, "target": target, "relation": "can_feed"})
    return edges


def _capability_id(value: Any) -> str:
    return str(value or "").strip().removeprefix("skill:").removeprefix("capability:")


def _missing_input_names(raw_items: Any) -> list[str]:
    if not isinstance(raw_items, list):
        return []
    names: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            value = item.get("name") or item.get("description") or item.get("reason")
        else:
            value = item
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)
    return names
