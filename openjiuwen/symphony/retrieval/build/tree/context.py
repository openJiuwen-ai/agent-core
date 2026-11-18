from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from openjiuwen.symphony.config import RetrievalBuildConfig

from .schema import TreeNode


@dataclass
class TreeBuildSettings:
    """Resolved build settings shared by the tree engines."""

    config: RetrievalBuildConfig
    postprocess_enabled: bool
    postprocess_max_passes: int
    postprocess_min_skills: int
    equiv_grouping_enabled: bool
    equiv_max_groups_per_parent: int
    equiv_allow_singleton_groups: bool
    equiv_min_lexical_similarity: float
    deterministic_prompts: bool
    discovery_seed: int
    cache_observability: bool
    skill_profiles_enabled: bool
    skill_profile_select_rules_enabled: bool
    skill_profile_batch_size: int
    skill_profile_description_limit: int
    skill_profile_rule_limit: int
    max_consecutive_failures: int = 5

    @classmethod
    def from_config(cls, config: RetrievalBuildConfig) -> TreeBuildSettings:
        return cls(
            config=config,
            postprocess_enabled=config.postprocess.enabled,
            postprocess_max_passes=config.postprocess.max_passes,
            postprocess_min_skills=config.postprocess.min_capabilities,
            equiv_grouping_enabled=config.equivalence.enabled,
            equiv_max_groups_per_parent=config.equivalence.max_groups_per_parent,
            equiv_allow_singleton_groups=config.equivalence.allow_singleton_groups,
            equiv_min_lexical_similarity=config.equivalence.min_lexical_similarity,
            deterministic_prompts=config.prompt.deterministic,
            discovery_seed=config.prompt.discovery_seed,
            cache_observability=config.output.cache_metrics_enabled,
            skill_profiles_enabled=config.profiles.enabled,
            skill_profile_select_rules_enabled=config.profiles.select_rules_enabled,
            skill_profile_batch_size=config.profiles.batch_size,
            skill_profile_description_limit=config.profiles.max_description_chars,
            skill_profile_rule_limit=config.profiles.max_rule_chars,
        )


@dataclass
class TreeBuildState:
    """Mutable runtime state shared by concurrent tree-build workers."""

    model: Any
    max_workers: int
    llm_calls: int = 0
    retry_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
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
