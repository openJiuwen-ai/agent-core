"""Minimal prompt bank for Demo's tree indexer."""

from __future__ import annotations

from openjiuwen.symphony.retrieval.common.prompts import INDEXING_YAML, get_prompt

GROUP_DISCOVERY_PROMPT = get_prompt(INDEXING_YAML, "group_discovery")
SKILL_ASSIGNMENT_PROMPT = get_prompt(INDEXING_YAML, "skill_assignment")
SKILL_PROFILE_PROMPT = get_prompt(INDEXING_YAML, "skill_profile")
NODE_LABEL_REWRITE_PROMPT = get_prompt(INDEXING_YAML, "node_label_rewrite")
GROUP_MERGE_PROMPT = get_prompt(INDEXING_YAML, "group_merge")
EQUIVALENCE_GROUPING_PROMPT = get_prompt(INDEXING_YAML, "equivalence_grouping")
EQUIVALENCE_PAIRWISE_PROMPT = get_prompt(INDEXING_YAML, "equivalence_pairwise")
SUBTREE_REBUILD_PROMPT = get_prompt(INDEXING_YAML, "subtree_rebuild")
