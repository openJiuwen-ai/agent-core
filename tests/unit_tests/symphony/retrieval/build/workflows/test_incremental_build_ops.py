from __future__ import annotations

import unittest
from types import SimpleNamespace

from openjiuwen.symphony.retrieval.build.workflows.incremental_build import (
    branch_and_ancestors,
    parent_branches_for_workers,
)
from openjiuwen.symphony.retrieval.build.workflows.subtree_rebuild import (
    build_subtree_from_llm_groups,
    normalize_llm_subtree_groups,
)


class IncrementalBuildOperatorTests(unittest.TestCase):
    def test_branch_and_ancestors_returns_all_non_empty_prefixes(self) -> None:
        self.assertEqual(
            branch_and_ancestors("Skills.weather.forecast"),
            {"Skills", "Skills.weather", "Skills.weather.forecast"},
        )
        self.assertEqual(branch_and_ancestors(""), set())
        self.assertEqual(branch_and_ancestors("Skills..weather"), {"Skills", "Skills.weather"})

    def test_parent_branches_for_workers_maps_removed_leaf_to_parent_ancestors(self) -> None:
        nodes = [
            {"cid": "Skills", "type": "branch"},
            {"cid": "Skills.weather", "type": "branch"},
            {"cid": "Skills.weather.forecast", "type": "leaf", "worker_id": "weather"},
            {"cid": "Skills.finance.stock", "type": "leaf", "worker_id": "stock"},
        ]

        self.assertEqual(parent_branches_for_workers(nodes, {"weather"}), {"Skills", "Skills.weather"})


class IncrementalSubtreeRebuilderOperatorTests(unittest.TestCase):
    def test_normalize_llm_subtree_groups_deduplicates_unknowns_and_recovers_missing(self) -> None:
        groups = normalize_llm_subtree_groups(
            {
                "groups": [
                    {"id": "forecast", "name": "Forecast", "skill_ids": ["weather", "paper"]},
                    {"id": "invalid", "name": "Invalid", "skill_ids": ["unknown"]},
                ]
            },
            valid_worker_ids={"weather", "paper", "stock"},
        )

        self.assertEqual([group["id"] for group in groups], ["forecast"])
        self.assertEqual(groups[0]["skill_ids"], ["weather", "paper", "stock"])

    def test_build_subtree_from_llm_groups_retains_root_and_moves_leaves_under_new_branch(self) -> None:
        nodes = [
            {"cid": "Skills", "type": "branch", "description": "root"},
            {"cid": "Skills.weather", "type": "leaf", "worker_id": "weather"},
            {"cid": "Skills.paper", "type": "leaf", "worker_id": "paper"},
            {"cid": "Other.stock", "type": "leaf", "worker_id": "stock"},
        ]
        leaves = [node for node in nodes if str(node.get("cid", "")).startswith("Skills.")]
        records_by_worker = {
            "weather": SimpleNamespace(description="Weather forecast"),
            "paper": SimpleNamespace(description="Paper search"),
        }

        rebuilt = build_subtree_from_llm_groups(
            nodes=nodes,
            root_cid="Skills",
            leaves=leaves,
            groups=[
                {
                    "id": "research",
                    "name": "Research",
                    "description": "Research branch",
                    "skill_ids": ["weather", "paper"],
                }
            ],
            records_by_worker=records_by_worker,
            max_direct_leaf_children=3,
        )

        by_worker = {str(node.get("worker_id")): node for node in rebuilt if node.get("worker_id")}
        cids = {str(node.get("cid")) for node in rebuilt}

        self.assertIn("Skills", cids)
        self.assertIn("Other.stock", cids)
        self.assertIn("Skills.research", cids)
        self.assertEqual(by_worker["weather"]["cid"], "Skills.research.weather")
        self.assertEqual(by_worker["weather"]["description"], "Weather forecast")
        self.assertEqual(by_worker["paper"]["cid"], "Skills.research.paper")


if __name__ == "__main__":
    unittest.main()
