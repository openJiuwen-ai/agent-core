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

        meta_name = swarmflow_identifier(skill_pack, recipe_id=recipe.recipe_id)
        materials = {
            "recipe": recipe.to_dict(),
            "swarmflow_script": generate_swarmflow_script(
                skill_pack,
                recipe_id=recipe.recipe_id,
                task_description=str(recipe.applicability.get("task_description") or ""),
            ),
            "meta_name": meta_name,
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
