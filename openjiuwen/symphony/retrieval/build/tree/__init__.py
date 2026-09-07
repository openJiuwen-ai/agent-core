from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .builder import TreeBuilder as TreeBuilder
    from .builder import build_tree as build_tree
    from .one_shot import OneShotLeaf as OneShotLeaf
    from .one_shot import OneShotSkill as OneShotSkill
    from .one_shot import OneShotSkillTreeBuilder as OneShotSkillTreeBuilder
    from .one_shot import OneShotTreeBuildConfig as OneShotTreeBuildConfig
    from .one_shot import OneShotTreeBuildError as OneShotTreeBuildError
    from .one_shot import OneShotTreeBuildResult as OneShotTreeBuildResult
    from .schema import DynamicTreeConfig as DynamicTreeConfig
    from .schema import Skill as Skill
    from .schema import SkillStatus as SkillStatus
    from .schema import TreeBuildConfig as TreeBuildConfig
    from .schema import TreeManagerConfig as TreeManagerConfig
    from .schema import TreeNode as TreeNode

__all__ = [
    "DynamicTreeConfig",
    "OneShotLeaf",
    "OneShotSkill",
    "OneShotSkillTreeBuilder",
    "OneShotTreeBuildConfig",
    "OneShotTreeBuildError",
    "OneShotTreeBuildResult",
    "Skill",
    "SkillStatus",
    "TreeBuildConfig",
    "TreeBuilder",
    "TreeManagerConfig",
    "TreeNode",
    "build_tree",
]


def __getattr__(name: str):
    if name in {"DynamicTreeConfig", "Skill", "SkillStatus", "TreeNode", "TreeBuildConfig", "TreeManagerConfig"}:
        from .schema import DynamicTreeConfig, Skill, SkillStatus, TreeBuildConfig, TreeManagerConfig, TreeNode

        exports: dict[str, Any] = {
            "DynamicTreeConfig": DynamicTreeConfig,
            "Skill": Skill,
            "SkillStatus": SkillStatus,
            "TreeBuildConfig": TreeBuildConfig,
            "TreeManagerConfig": TreeManagerConfig,
            "TreeNode": TreeNode,
        }
        return exports[name]
    if name in {"TreeBuilder", "build_tree"}:
        from .builder import TreeBuilder, build_tree

        builder_exports: dict[str, Any] = {"TreeBuilder": TreeBuilder, "build_tree": build_tree}
        return builder_exports[name]
    if name in {
        "OneShotLeaf",
        "OneShotSkill",
        "OneShotSkillTreeBuilder",
        "OneShotTreeBuildConfig",
        "OneShotTreeBuildError",
        "OneShotTreeBuildResult",
    }:
        from .one_shot import (
            OneShotLeaf,
            OneShotSkill,
            OneShotSkillTreeBuilder,
            OneShotTreeBuildConfig,
            OneShotTreeBuildError,
            OneShotTreeBuildResult,
        )

        one_shot_exports: dict[str, Any] = {
            "OneShotLeaf": OneShotLeaf,
            "OneShotSkill": OneShotSkill,
            "OneShotSkillTreeBuilder": OneShotSkillTreeBuilder,
            "OneShotTreeBuildConfig": OneShotTreeBuildConfig,
            "OneShotTreeBuildError": OneShotTreeBuildError,
            "OneShotTreeBuildResult": OneShotTreeBuildResult,
        }
        return one_shot_exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
