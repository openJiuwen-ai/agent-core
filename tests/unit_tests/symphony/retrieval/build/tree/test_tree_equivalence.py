from __future__ import annotations

import unittest
from types import SimpleNamespace

from openjiuwen.symphony.retrieval.build.tree.equivalence import TreeBuilderEquivalenceMixin
from openjiuwen.symphony.retrieval.build.tree.schema import TreeNode
from openjiuwen.symphony.retrieval.build.tree.shared import console


class _FakeEquivalenceBuilder:
    def __init__(self, responses: list, max_candidate_pairs: int = 0) -> None:
        self.settings = SimpleNamespace(
            equiv_max_groups_per_parent=6,
            equiv_max_candidate_pairs=max_candidate_pairs,
            equiv_allow_singleton_groups=True,
            equiv_min_lexical_similarity=0.0,
            deterministic_prompts=False,
        )
        self.responses = list(responses)
        self.prompts: list[str] = []

    def _call_llm_json(self, prompt: str, is_retry: bool = False) -> dict:
        del is_retry
        self.prompts.append(prompt)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @staticmethod
    def _iter_group_items(groups: dict):
        return sorted(groups.items())


class _Harness(TreeBuilderEquivalenceMixin, _FakeEquivalenceBuilder):
    pass


def _leaf(leaf_id: str, description: str = "") -> TreeNode:
    return TreeNode(id=leaf_id, name=leaf_id.title(), description=description)


def _candidate_groups(leaf_ids: list[str]) -> dict:
    return {"g1": {"leaf_ids": list(leaf_ids)}}


def _candidate_payload(leaf_ids: list[str]) -> dict:
    return {"groups": _candidate_groups(leaf_ids)}


class TreeEquivalenceDegradationTests(unittest.TestCase):
    def test_degrades_to_empty_groups_when_candidate_retries_fail(self) -> None:
        harness = _Harness(
            [
                {"groups": {"g1": {"leaf_ids": ["alpha", "unknown"]}}},
                {"groups": {}},
            ]
        )
        leaves = [_leaf("alpha"), _leaf("beta"), _leaf("gamma")]

        result = harness._discover_equivalence_groups(TreeNode(id="scope", name="Scope"), leaves, verbose=True)

        self.assertEqual(result, {})
        self.assertEqual(len(harness.prompts), 2)

    def test_degrades_to_empty_groups_when_pairwise_retries_fail(self) -> None:
        harness = _Harness(
            [
                _candidate_payload(["alpha", "beta", "gamma"]),
                {"decisions": [{"pair_id": "p99999", "similar": False}]},
                {"decisions": []},
            ]
        )
        leaves = [_leaf("alpha"), _leaf("beta"), _leaf("gamma")]

        result = harness._discover_equivalence_groups(TreeNode(id="scope", name="Scope"), leaves)

        self.assertEqual(result, {})
        self.assertEqual(len(harness.prompts), 3)

    def test_valid_flow_still_returns_groups(self) -> None:
        harness = _Harness(
            [
                _candidate_payload(["alpha", "beta", "gamma"]),
                {
                    "decisions": [
                        {"pair_id": "p00001", "similar": True, "shared_capability": "shared cap"},
                        {"pair_id": "p00002", "similar": True, "shared_capability": "shared cap"},
                        {"pair_id": "p00003", "similar": True, "shared_capability": "shared cap"},
                    ]
                },
            ]
        )
        leaves = [_leaf("alpha"), _leaf("beta"), _leaf("gamma")]

        result = harness._discover_equivalence_groups(TreeNode(id="scope", name="Scope"), leaves)

        self.assertEqual(len(result), 1)
        self.assertEqual(result["g1"]["leaf_ids"], ["alpha", "beta", "gamma"])
        self.assertEqual(result["g1"]["name"], "shared cap")


class TreeEquivalencePairCapTests(unittest.TestCase):
    def test_prescreen_caps_pairs_and_keeps_most_similar(self) -> None:
        leaves = {
            "alpha-one": _leaf("alpha-one", "alpha shared capability"),
            "alpha-two": _leaf("alpha-two", "alpha shared capability"),
            "beta-one": _leaf("beta-one", "beta workflow"),
            "beta-two": _leaf("beta-two", "beta workflow"),
            "gamma-one": _leaf("gamma-one", "gamma unrelated"),
        }
        harness = _Harness([], max_candidate_pairs=3)
        groups = _candidate_groups(list(leaves))

        first = harness._candidate_pairs_from_groups(leaves, groups)
        second = harness._candidate_pairs_from_groups(leaves, groups)

        self.assertEqual(len(first), 3)
        self.assertEqual(first, second)
        self.assertIn(("alpha-one", "alpha-two"), first)

    def test_nonpositive_cap_keeps_all_pairs(self) -> None:
        leaves = {
            "alpha": _leaf("alpha"),
            "beta": _leaf("beta"),
            "gamma": _leaf("gamma"),
        }
        harness = _Harness([], max_candidate_pairs=0)

        pairs = harness._candidate_pairs_from_groups(leaves, _candidate_groups(list(leaves)))

        self.assertEqual(pairs, [("alpha", "beta"), ("alpha", "gamma"), ("beta", "gamma")])

    def test_verbose_reports_pair_and_batch_counts(self) -> None:
        leaves = {
            "alpha": _leaf("alpha"),
            "beta": _leaf("beta"),
            "gamma": _leaf("gamma"),
        }
        harness = _Harness(
            [
                {
                    "decisions": [
                        {"pair_id": "p00001", "similar": False},
                        {"pair_id": "p00002", "similar": False},
                        {"pair_id": "p00003", "similar": False},
                    ]
                }
            ],
            max_candidate_pairs=0,
        )
        groups = _candidate_groups(list(leaves))

        with console.capture() as capture:
            harness._candidate_pairs_from_groups(leaves, groups, verbose=True)
            harness._judge_candidate_pairs(
                TreeNode(id="scope", name="Scope"),
                leaves,
                harness._candidate_pairs_from_groups(leaves, groups),
                verbose=True,
            )

        output = capture.get()
        self.assertIn("3 pairs in 1 batches", output)


if __name__ == "__main__":
    unittest.main()
