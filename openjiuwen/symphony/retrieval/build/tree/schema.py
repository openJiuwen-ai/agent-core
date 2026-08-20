"""Core schema and config objects for Symphony capability-tree indexing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, TypedDict, Unpack

from .json_parser import parse_json_from_response as _parse_json_from_response
from .root_categories import FIXED_ROOT_CATEGORIES as _FIXED_ROOT_CATEGORIES
from .search_models import MultiLevelSearchResult as _MultiLevelSearchResult
from .search_models import SearchStep as _SearchStep

parse_json_from_response = _parse_json_from_response
FIXED_ROOT_CATEGORIES = _FIXED_ROOT_CATEGORIES
MultiLevelSearchResult = _MultiLevelSearchResult
SearchStep = _SearchStep

_Identifier = TypedDict("_Identifier", {"id": str})


class SkillStatus(str, Enum):
    ACTIVE = "active"
    DORMANT = "dormant"
    PINNED = "pinned"

    @classmethod
    def default(cls) -> "SkillStatus":
        return cls.ACTIVE


SKILL_DESCRIPTION_MAX_LENGTH = 150

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = PROJECT_ROOT / "data" / "skills"
DEFAULT_TREE_OUTPUT_PATH = PROJECT_ROOT / "data" / "capability_trees" / "tree.yaml"

BRANCHING_FACTOR = 8
MAX_DEPTH = 6

TREE_BUILD_MAX_WORKERS = 1
TREE_BUILD_CACHING = False
TREE_BUILD_NUM_RETRIES = 2
TREE_BUILD_TIMEOUT = 180.0
TREE_BUILD_CONTEXT_WINDOW = 0
TREE_BUILD_MAX_OUTPUT_TOKENS = 0
TREE_BUILD_POSTPROCESS_ENABLED = True
TREE_BUILD_POSTPROCESS_MAX_PASSES = 1
TREE_BUILD_POSTPROCESS_MIN_SKILLS = 6
TREE_BUILD_EQUIV_GROUPING_ENABLED = False
TREE_BUILD_EQUIV_MAX_GROUPS_PER_PARENT = 6
TREE_BUILD_EQUIV_ALLOW_SINGLETON_GROUPS = True
TREE_BUILD_EQUIV_MIN_LEXICAL_SIMILARITY = 0.0
TREE_BUILD_SKILL_PROFILES_ENABLED = False
TREE_BUILD_SKILL_PROFILE_SELECT_RULES_ENABLED = True
TREE_BUILD_SKILL_PROFILE_BATCH_SIZE = 48
TREE_BUILD_SKILL_PROFILE_DESCRIPTION_LIMIT = 140
TREE_BUILD_SKILL_PROFILE_RULE_LIMIT = 120

MAX_SKILLS_PER_NODE_MULTIPLIER = 1.5
EXPAND_THRESHOLD_MULTIPLIER = 0.7
EARLY_STOP_MULTIPLIER = 1.7
LAZY_SPLIT_MULTIPLIER = 1.3
CLASSIFICATION_BATCH_MULTIPLIER = 6
STRUCTURE_SAMPLE_MULTIPLIER = 12
FALLBACK_CATEGORY_ID_HASH_LENGTH = 12


def _slug_term(value: str, fallback: str = "category") -> str:
    source = str(value or "")
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "-", source).replace("_", "-").lower()
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-") or fallback
    if normalized == fallback and source.strip():
        # Non-ASCII names may not produce a readable slug; append a stable short hash.
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:FALLBACK_CATEGORY_ID_HASH_LENGTH]
        normalized = f"{fallback}-{digest}"
    return normalized if normalized[0].isalpha() else f"n-{normalized}"


def normalize_root_categories(raw_categories) -> Optional[dict]:
    if raw_categories in (None, [], ()):
        return None
    if not isinstance(raw_categories, list):
        raise ValueError("TREE_BUILDER_ROOT_CATEGORIES must be a list.")

    def normalize_entries(entries: list, *, label_path: str) -> dict:
        items: list[tuple[str, dict]] = []
        for entry in entries:
            if isinstance(entry, str):
                label = entry.strip()
                if not label:
                    continue
                category_id = _slug_term(label)
                payload: dict[str, object] = {
                    "name": label,
                    "description": f"Skills related to {label.lower()}.",
                }
            elif isinstance(entry, dict):
                label = str(entry.get("name") or entry.get("id") or "").strip()
                if not label:
                    raise ValueError(f"Each {label_path} item must include 'name' or 'id'.")
                category_id = _slug_term(str(entry.get("id") or label))
                payload = {
                    "name": label,
                    "description": str(entry.get("description") or f"Skills related to {label.lower()}.").strip(),
                }
                select_when = str(entry.get("select_when") or "").strip()
                dont_select_when = str(entry.get("dont_select_when") or "").strip()
                if select_when:
                    payload["select_when"] = select_when
                if dont_select_when:
                    payload["dont_select_when"] = dont_select_when
                raw_children = entry.get("children")
                if raw_children in (None, [], ()):
                    raw_children = None
                if raw_children is not None:
                    if isinstance(raw_children, dict):
                        child_entries = [
                            {"id": child_id, **dict(child_payload)}
                            for child_id, child_payload in raw_children.items()
                            if isinstance(child_payload, dict)
                        ]
                        if len(child_entries) != len(raw_children):
                            raise ValueError(f"{label_path} item '{category_id}' children dict values must be objects.")
                    elif isinstance(raw_children, list):
                        child_entries = raw_children
                    else:
                        raise ValueError(f"{label_path} item '{category_id}' children must be a list or dict.")
                    children = normalize_entries(child_entries, label_path=f"{label_path}.{category_id}.children")
                    if children:
                        payload["children"] = children
            else:
                raise ValueError(f"{label_path} items must be strings or dicts.")
            items.append((category_id, payload))

        normalized: dict[str, dict] = {}
        for category_id, payload in items:
            if category_id in normalized:
                raise ValueError(f"Duplicate {label_path} id: {category_id}")
            normalized[category_id] = payload
        return normalized

    normalized = normalize_entries(raw_categories, label_path="TREE_BUILDER_ROOT_CATEGORIES")
    return normalized or None


@dataclass(frozen=True)
class TreeBuildConfig:
    max_workers: int = TREE_BUILD_MAX_WORKERS
    caching: bool = TREE_BUILD_CACHING
    num_retries: int = TREE_BUILD_NUM_RETRIES
    timeout: float = TREE_BUILD_TIMEOUT
    context_window: int = TREE_BUILD_CONTEXT_WINDOW
    max_output_tokens: int = TREE_BUILD_MAX_OUTPUT_TOKENS
    postprocess_enabled: bool = TREE_BUILD_POSTPROCESS_ENABLED
    postprocess_max_passes: int = TREE_BUILD_POSTPROCESS_MAX_PASSES
    postprocess_min_skills: int = TREE_BUILD_POSTPROCESS_MIN_SKILLS
    equiv_grouping_enabled: bool = TREE_BUILD_EQUIV_GROUPING_ENABLED
    equiv_max_groups_per_parent: int = TREE_BUILD_EQUIV_MAX_GROUPS_PER_PARENT
    equiv_allow_singleton_groups: bool = TREE_BUILD_EQUIV_ALLOW_SINGLETON_GROUPS
    equiv_min_lexical_similarity: float = TREE_BUILD_EQUIV_MIN_LEXICAL_SIMILARITY
    deterministic_prompts: bool = True
    discovery_seed: int = 42
    prompt_fingerprint_version: str = "v1"
    cache_observability: bool = True
    skill_profiles_enabled: bool = TREE_BUILD_SKILL_PROFILES_ENABLED
    skill_profile_select_rules_enabled: bool = TREE_BUILD_SKILL_PROFILE_SELECT_RULES_ENABLED
    skill_profile_batch_size: int = TREE_BUILD_SKILL_PROFILE_BATCH_SIZE
    skill_profile_description_limit: int = TREE_BUILD_SKILL_PROFILE_DESCRIPTION_LIMIT
    skill_profile_rule_limit: int = TREE_BUILD_SKILL_PROFILE_RULE_LIMIT
    classify_batch_cap: int = 20


@dataclass(frozen=True)
class TreeManagerConfig:
    branching_factor: int = BRANCHING_FACTOR
    max_depth: int = MAX_DEPTH
    root_categories: Optional[dict] = None
    build: TreeBuildConfig = field(default_factory=TreeBuildConfig)


@dataclass
class DynamicTreeConfig:
    branching_factor: int = BRANCHING_FACTOR
    max_depth: int = MAX_DEPTH
    root_categories: Optional[dict] = None
    rebalance_interval: int = 50

    def _scaled(self, multiplier: float, seed: Optional[int] = None) -> int:
        anchor = self.branching_factor if seed is None else seed
        return int(anchor * multiplier)

    def _derived_value(self, key: str) -> int:
        if key == "max_skills_per_node":
            return self._scaled(MAX_SKILLS_PER_NODE_MULTIPLIER)
        if key == "expand_threshold":
            return self._scaled(EXPAND_THRESHOLD_MULTIPLIER)
        if key == "early_stop_skill_count":
            return self._scaled(EARLY_STOP_MULTIPLIER)
        if key == "lazy_split_threshold":
            return self._scaled(LAZY_SPLIT_MULTIPLIER, self.max_skills_per_node)
        if key == "classification_batch_size":
            return self._scaled(CLASSIFICATION_BATCH_MULTIPLIER)
        if key == "structure_sample_size":
            return self._scaled(STRUCTURE_SAMPLE_MULTIPLIER)
        raise KeyError(key)

    @property
    def max_skills_per_node(self) -> int:
        return self._derived_value("max_skills_per_node")

    @property
    def expand_threshold(self) -> int:
        return self._derived_value("expand_threshold")

    @property
    def early_stop_skill_count(self) -> int:
        return self._derived_value("early_stop_skill_count")

    @property
    def lazy_split_threshold(self) -> int:
        return self._derived_value("lazy_split_threshold")

    @property
    def classification_batch_size(self) -> int:
        return self._derived_value("classification_batch_size")

    @property
    def structure_sample_size(self) -> int:
        return self._derived_value("structure_sample_size")


class Skill:
    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        path: str = "",
        skill_path: str = "",
        content: str = "",
        selection_reason: str = "",
        select_when: str = "",
        dont_select_when: str = "",
        source_description: str = "",
        github_url: str = "",
        stars: int = 0,
        is_official: bool = False,
        author: str = "",
        status: SkillStatus = SkillStatus.ACTIVE,
        installs_count: int = 0,
        pinned_at: Optional[str] = None,
        last_used: Optional[str] = None,
        **identifier: Unpack[_Identifier],
    ) -> None:
        self.id = identifier["id"]
        self.name = name
        self.description = description
        self.path = path
        self.skill_path = skill_path
        self.content = content
        self.selection_reason = selection_reason
        self.select_when = select_when
        self.dont_select_when = dont_select_when
        self.source_description = source_description
        self.github_url = github_url
        self.stars = stars
        self.is_official = is_official
        self.author = author
        self.status = status
        self.installs_count = installs_count
        self.pinned_at = pinned_at
        self.last_used = last_used

    def to_dict(self, include_content: bool = True) -> dict:
        keys = (
            "id",
            "name",
            "description",
            "skill_path",
            "select_when",
            "dont_select_when",
            "source_description",
            "github_url",
            "stars",
            "is_official",
            "author",
        )
        values = (
            self.id,
            self.name,
            self.description,
            self.skill_path,
            self.select_when,
            self.dont_select_when,
            self.source_description,
            self.github_url,
            self.stars,
            self.is_official,
            self.author,
        )
        payload = dict(zip(keys, values))
        if include_content:
            payload["content"] = self.content
        return payload


class TreeNode:
    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        select_when: str = "",
        dont_select_when: str = "",
        children: Optional[list["TreeNode"]] = None,
        skills: Optional[list[Skill]] = None,
        depth: int = 0,
        parent_id: Optional[str] = None,
        pending_split: bool = False,
        **identifier: Unpack[_Identifier],
    ) -> None:
        self.id = identifier["id"]
        self.name = name
        self.description = description
        self.select_when = select_when
        self.dont_select_when = dont_select_when
        self.children = list(children or [])
        self.skills = list(skills or [])
        self.depth = depth
        self.parent_id = parent_id
        self.pending_split = pending_split

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    @property
    def is_intermediate(self) -> bool:
        return not self.is_leaf

    def count_all_skills(self) -> int:
        total = 0
        stack = [self]
        while stack:
            current = stack.pop()
            if current.children:
                stack.extend(current.children)
            else:
                total += len(current.skills)
        return total

    def collect_all_skills(self) -> list[Skill]:
        gathered: list[Skill] = []
        agenda = [self]
        while agenda:
            current = agenda.pop()
            if current.children:
                agenda.extend(current.children)
                continue
            if current.skills:
                gathered.extend(current.skills)
        return gathered

    def get_leaf_nodes(self) -> list["TreeNode"]:
        result: list[TreeNode] = []
        frontier = [self]
        while frontier:
            current = frontier.pop()
            if current.children:
                frontier.extend(current.children)
            else:
                result.append(current)
        return result

    def get_pending_split_nodes(self) -> list["TreeNode"]:
        flagged: list[TreeNode] = []
        queue = [self]
        while queue:
            current = queue.pop()
            if current.pending_split:
                flagged.append(current)
            queue.extend(current.children)
        return flagged

    def clear_pending_splits(self) -> None:
        for current in [self, *self._walk_descendants()]:
            current.pending_split = False

    def get_path(self) -> str:
        return self.id

    def to_dict(self) -> dict:
        payload: dict = {}
        payload.update(id=self.id, name=self.name, description=self.description)
        if self.select_when:
            payload["select_when"] = self.select_when
        if self.dont_select_when:
            payload["dont_select_when"] = self.dont_select_when
        child_items = list(self.children)
        skill_items = list(self.skills)
        if child_items:
            serialized_children: list[dict] = []
            for child in child_items:
                serialized_children.append(child.to_dict())
            payload["children"] = serialized_children
        if skill_items:
            payload["skills"] = [item.to_dict() for item in skill_items]
        return payload

    def _walk_descendants(self):
        stack = list(self.children)
        while stack:
            current = stack.pop()
            yield current
            stack.extend(current.children)

    @classmethod
    def from_recursive_tree(
        cls,
        tree_dict: dict,
        depth: int = 0,
        parent_id: Optional[str] = None,
    ) -> "TreeNode":
        node = cls(
            id=tree_dict.get("id", "unknown"),
            name=tree_dict.get("name", ""),
            description=tree_dict.get("description", ""),
            select_when=tree_dict.get("select_when", ""),
            dont_select_when=tree_dict.get("dont_select_when", ""),
            depth=depth,
            parent_id=parent_id,
        )
        for child_payload in list(tree_dict.get("children", []) or []):
            node.children.append(cls.from_recursive_tree(child_payload, depth + 1, node.id))
        for skill_payload in list(tree_dict.get("skills", []) or []):
            node.skills.append(
                Skill(
                    id=skill_payload.get("id", ""),
                    name=skill_payload.get("name", ""),
                    description=skill_payload.get("description", ""),
                    path=node.id,
                    skill_path=skill_payload.get("skill_path", ""),
                    content=skill_payload.get("content", ""),
                    select_when=skill_payload.get("select_when", ""),
                    dont_select_when=skill_payload.get("dont_select_when", ""),
                    source_description=skill_payload.get("source_description", ""),
                    github_url=skill_payload.get("github_url", ""),
                    stars=skill_payload.get("stars", 0),
                    is_official=skill_payload.get("is_official", False),
                    author=skill_payload.get("author", ""),
                )
            )
        return node

    @classmethod
    def from_capability_tree(cls, tree_dict: dict) -> "TreeNode":
        root = cls(id="root", name="Root", description="Skill Tree Root")
        domains = tree_dict.get("domains", {}) or {}
        for domain_id, domain_payload in domains.items():
            domain_node = cls(
                id=domain_id,
                name=domain_payload.get("name", domain_id),
                description=domain_payload.get("description", ""),
                select_when=domain_payload.get("select_when", ""),
                dont_select_when=domain_payload.get("dont_select_when", ""),
                depth=1,
                parent_id=root.id,
            )
            for type_id, type_payload in (domain_payload.get("types", {}) or {}).items():
                type_node = cls(
                    id=type_id,
                    name=type_payload.get("name", type_id),
                    description=type_payload.get("description", ""),
                    select_when=type_payload.get("select_when", ""),
                    dont_select_when=type_payload.get("dont_select_when", ""),
                    depth=2,
                    parent_id=domain_id,
                )
                for skill_payload in list(type_payload.get("skills", []) or []):
                    type_node.skills.append(
                        Skill(
                            id=skill_payload.get("id", ""),
                            name=skill_payload.get("name", ""),
                            description=skill_payload.get("description", ""),
                            path="/".join([domain_id, type_id]),
                            select_when=skill_payload.get("select_when", ""),
                            dont_select_when=skill_payload.get("dont_select_when", ""),
                            source_description=skill_payload.get("source_description", ""),
                            github_url=skill_payload.get("github_url", ""),
                            stars=skill_payload.get("stars", 0),
                            is_official=skill_payload.get("is_official", False),
                            author=skill_payload.get("author", ""),
                        )
                    )
                domain_node.children.append(type_node)
            root.children.append(domain_node)
        return root
