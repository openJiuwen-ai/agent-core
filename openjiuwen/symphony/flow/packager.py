"""CapabilityPackager：把 active+verified recipe 冻结为统一能力包。"""

from __future__ import annotations

from typing import Any

from openjiuwen.symphony.flow.codegen import (
    generate_swarmflow_script,
    swarmflow_identifier,
)
from openjiuwen.symphony.flow.models import (
    PACKAGE_SCHEMA_VERSION,
    RECIPE_GRADE_VERIFIED,
    RECIPE_STATUS_ACTIVE,
    TARGET_KIND_SKILL,
    ExperienceRecipe,
    content_hash,
    utc_now_iso,
)
from openjiuwen.symphony.flow.privacy import sanitize_distilled_text


class RecipeNotPackableError(ValueError):
    """recipe 不满足打包条件（非 active+verified 或结构缺失）。"""


class CapabilityPackager:
    """把 recipe 冻结为不可变、target 无关的统一能力包。"""

    @staticmethod
    def build_package(
        recipe: ExperienceRecipe,
        *,
        target_kind: str = TARGET_KIND_SKILL,
    ) -> dict[str, Any]:
        if target_kind != TARGET_KIND_SKILL:
            raise RecipeNotPackableError(f"target_kind {target_kind!r} is not installable (v1: skill only)")
        if recipe.status != RECIPE_STATUS_ACTIVE:
            raise RecipeNotPackableError(
                f"recipe {recipe.recipe_id} status is {recipe.status}, only active recipes can be packaged"
            )
        if recipe.grade != RECIPE_GRADE_VERIFIED:
            raise RecipeNotPackableError(
                f"recipe {recipe.recipe_id} grade is {recipe.grade}, only verified recipes can be packaged"
            )
        skill_pack = recipe.combination_structure
        if not isinstance(skill_pack, dict) or not skill_pack.get("nodes"):
            raise RecipeNotPackableError(f"recipe {recipe.recipe_id} has no combination structure")
        _validate_simple_chain(skill_pack, recipe.recipe_id)

        meta_name = swarmflow_identifier(skill_pack, recipe_id=recipe.recipe_id)
        package_recipe = _package_recipe(recipe)
        materials = {
            "recipe": package_recipe,
            "swarmflow_script": generate_swarmflow_script(
                package_recipe["combination_structure"],
                recipe_id=recipe.recipe_id,
                task_description=str(package_recipe["applicability"].get("task_description") or ""),
            ),
            "meta_name": meta_name,
            "permissions": [],
            "license": "Proprietary",
        }
        package_id = f"cap-{content_hash(materials)[len('sha256:') :][:12]}"
        return {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "package_id": package_id,
            "recipe_id": recipe.recipe_id,
            "recipe_version": recipe.version,
            "target_kind": target_kind,
            "meta_name": meta_name,
            "materials": materials,
            "integrity": content_hash(materials),
            "created_at": utc_now_iso(),
        }

    @staticmethod
    def verify_package_integrity(package: dict[str, Any]) -> bool:
        """重算 materials 哈希与 integrity 比对（防篡改）。"""

        materials = package.get("materials")
        if not isinstance(materials, dict):
            return False
        return content_hash(materials) == str(package.get("integrity") or "")


def _package_recipe(recipe: ExperienceRecipe) -> dict[str, Any]:
    """Build the minimum redacted recipe view needed by review and rendering."""

    source_queries = [str(item) for item in (recipe.provenance.get("sample_queries") or ())]
    structure = recipe.combination_structure
    nodes = structure.get("nodes") if isinstance(structure, dict) else {}
    edges = structure.get("edges") if isinstance(structure, dict) else []
    safe_nodes: dict[str, Any] = {}
    for node_id, node in (nodes or {}).items():
        metadata = node.get("metadata") if isinstance(node, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        safe_nodes[str(node_id)] = {
            "label": "capability",
            "metadata": {
                key: sanitize_distilled_text(metadata.get(key))
                for key in ("version", "content_hash", "capability_type")
                if metadata.get(key) is not None
            },
        }
    safe_structure = {
        "type": str(structure.get("type") or "skill_pack"),
        "direction": str(structure.get("direction") or "directed"),
        "nodes": safe_nodes,
        "edges": [
            {
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
                "relation": str(edge.get("relation") or "can_feed"),
            }
            for edge in edges or ()
            if isinstance(edge, dict)
        ],
        "loop_guards": [],
    }
    quality_keys = (
        "execution_count",
        "success_count",
        "failure_count",
        "pack_success_rate",
        "qualified_edge_count",
    )
    provenance_keys = ("source", "structure_signature", "evidence_count", "narrative_source")
    return {
        "schema_version": recipe.schema_version,
        "recipe_id": recipe.recipe_id,
        "version": recipe.version,
        "status": recipe.status,
        "grade": recipe.grade,
        "applicability": {
            "task_description": sanitize_distilled_text(
                recipe.applicability.get("task_description"),
                source_queries=source_queries,
            ),
            "trigger_conditions": sanitize_distilled_text(
                recipe.applicability.get("trigger_conditions"),
                source_queries=source_queries,
            ),
        },
        "combination_structure": safe_structure,
        "execution_narrative": sanitize_distilled_text(
            recipe.execution_narrative,
            source_queries=source_queries,
        ),
        "quality": {key: recipe.quality[key] for key in quality_keys if key in recipe.quality},
        "provenance": {key: recipe.provenance[key] for key in provenance_keys if key in recipe.provenance},
    }


def _validate_simple_chain(skill_pack: dict[str, Any], recipe_id: str) -> None:
    """V1 packages only linear, fully connected skill chains."""

    nodes = skill_pack.get("nodes")
    edges = skill_pack.get("edges")
    if not isinstance(nodes, dict) or len(nodes) < 2 or not isinstance(edges, list):
        raise RecipeNotPackableError(f"recipe {recipe_id} is not a simple skill chain")
    indegree = {str(node_id): 0 for node_id in nodes}
    outdegree = {str(node_id): 0 for node_id in nodes}
    adjacency: dict[str, str] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            raise RecipeNotPackableError(f"recipe {recipe_id} is not a simple skill chain")
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in nodes or target not in nodes or source == target:
            raise RecipeNotPackableError(f"recipe {recipe_id} is not a simple skill chain")
        indegree[target] += 1
        outdegree[source] += 1
        adjacency[source] = target
    starts = [node_id for node_id, degree in indegree.items() if degree == 0]
    if len(edges) != len(nodes) - 1 or len(starts) != 1 or max(indegree.values()) > 1 or max(outdegree.values()) > 1:
        raise RecipeNotPackableError(f"recipe {recipe_id} is not a simple skill chain")
    visited: set[str] = set()
    current = starts[0]
    while current not in visited:
        visited.add(current)
        if current not in adjacency:
            break
        current = adjacency[current]
    if visited != set(nodes):
        raise RecipeNotPackableError(f"recipe {recipe_id} is not a simple skill chain")
