from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from .schema import TreeManagerConfig, TreeNode


@dataclass
class TreeBuildSettings:
    """Resolved build settings shared by the tree engines."""

    manager_config: TreeManagerConfig
    llm_seed: int | None
    postprocess_enabled: bool
    postprocess_max_passes: int
    postprocess_min_skills: int
    equiv_grouping_enabled: bool
    equiv_max_groups_per_parent: int
    equiv_allow_singleton_groups: bool
    equiv_min_lexical_similarity: float
    deterministic_prompts: bool
    discovery_seed: int
    prompt_fingerprint_version: str
    cache_observability: bool
    skill_profiles_enabled: bool
    skill_profile_select_rules_enabled: bool
    skill_profile_batch_size: int
    skill_profile_description_limit: int
    skill_profile_rule_limit: int
    max_consecutive_failures: int = 5

    @classmethod
    def from_manager_config(
        cls,
        manager_config: TreeManagerConfig,
        *,
        llm_seed: int | None,
    ) -> TreeBuildSettings:
        build_config = manager_config.build
        return cls(
            manager_config=manager_config,
            llm_seed=llm_seed,
            postprocess_enabled=bool(build_config.postprocess_enabled),
            postprocess_max_passes=max(0, int(build_config.postprocess_max_passes)),
            postprocess_min_skills=max(2, int(build_config.postprocess_min_skills)),
            equiv_grouping_enabled=bool(build_config.equiv_grouping_enabled),
            equiv_max_groups_per_parent=max(2, int(build_config.equiv_max_groups_per_parent)),
            equiv_allow_singleton_groups=bool(build_config.equiv_allow_singleton_groups),
            equiv_min_lexical_similarity=max(
                0.0,
                min(1.0, float(build_config.equiv_min_lexical_similarity)),
            ),
            deterministic_prompts=build_config.deterministic_prompts,
            discovery_seed=build_config.discovery_seed,
            prompt_fingerprint_version=build_config.prompt_fingerprint_version,
            cache_observability=build_config.cache_observability,
            skill_profiles_enabled=bool(build_config.skill_profiles_enabled),
            skill_profile_select_rules_enabled=bool(build_config.skill_profile_select_rules_enabled),
            skill_profile_batch_size=max(1, int(build_config.skill_profile_batch_size)),
            skill_profile_description_limit=max(40, int(build_config.skill_profile_description_limit)),
            skill_profile_rule_limit=max(40, int(build_config.skill_profile_rule_limit)),
        )


@dataclass
class TreeBuildState:
    """Mutable runtime state shared by concurrent tree-build workers."""

    client: Any
    max_workers: int
    llm_calls: int = 0
    retry_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_unknown: int = 0
    prompt_fingerprints: set[str] = field(default_factory=set)
    leaf_skills: int = 0
    progress: Any = None
    progress_task: Any = None
    batch_size_cache: int | None = None
    max_output_tokens_cache: int | None = None
    executor: ThreadPoolExecutor | None = None
    consecutive_failures: int = 0
    counter_lock: Any = field(default_factory=threading.Lock)
    thread_local: threading.local = field(default_factory=threading.local)
    llm_semaphore: threading.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.llm_semaphore = threading.Semaphore(self.max_workers)


@dataclass(frozen=True)
class TreeBuilderOperations:
    """Explicit callbacks used when an engine needs builder-owned behavior."""

    assign_skills_to_leaf: Callable[[TreeNode, list[dict]], None]
    insert_skill_into_subtree: Callable[[TreeNode, dict], None]
    prune_empty_children: Callable[[TreeNode], int]
    repair_small_leaf_children: Callable[[TreeNode], int]
    discover_equivalence_groups: Callable[..., dict]
    normalize_equivalence_groups: Callable[[list[TreeNode], dict], list[dict]]
    build_equivalence_group_id: Callable[..., str]
