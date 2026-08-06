"""Supplementary UTs to boost coverage for progressive-retrieval modules.

These tests target lines not exercised by the existing
test_progressive_refactor / test_progressive_retriever suites, focusing on:
- subtree/roots.py (choices_cache_key, build_progressive_root, freeze, label/description helpers)
- subtree/default.py (cache & cache_lock paths)
- reduce.py (round-robin overflow, _dedupe_candidates, sequential full-merge)
- contracts.py (Protocol raise NotImplementedError branches)
- expand.py (resolve_branch_top_k edge cases)
- engine.py (parallel-branch & abstain paths)
- select/selection.py (LogitSelectionFragmentSelector fallback modes, min_probability filter)
- flat.py (FlatRetriever.retrieve_top_k)
"""

from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock

from openjiuwen.symphony.retrieval.common.models import (
    RetrieverCandidate,
    RetrieverChoice,
    RetrieverItem,
    RetrieverNode,
    RetrieverTrace,
)
from openjiuwen.symphony.retrieval.llm.base.types import CandidateScore
from openjiuwen.symphony.retrieval.search.runtime.contracts import (
    BranchReducer,
    CurrentSubtreeProvider,
    SubtreeRenderer,
    TargetExpander,
    TopKSelector,
)
from openjiuwen.symphony.retrieval.search.runtime.engine import RecursiveSearchEngine
from openjiuwen.symphony.retrieval.search.runtime.expand import DefaultTargetExpander
from openjiuwen.symphony.retrieval.search.runtime.flat import FlatRetriever
from openjiuwen.symphony.retrieval.search.runtime.reduce import DefaultBranchReducer
from openjiuwen.symphony.retrieval.search.runtime.render.disclosure import (
    ExposedFragment,
    ExposedNode,
    SelectableResolution,
)
from openjiuwen.symphony.retrieval.search.runtime.selector import (
    GenerateFragmentSelector,
    LogitSelectionFragmentSelector,
)
from openjiuwen.symphony.retrieval.search.runtime.subtree import (
    DefaultCurrentSubtreeProvider,
    build_progressive_branch_description,
    build_progressive_item_label,
    build_progressive_root,
    choices_cache_key,
)
from openjiuwen.symphony.retrieval.search.runtime.types import (
    ChildSearchCursor,
    CurrentSubtree,
    ExpansionPlan,
    NodeSearchResult,
    ProgressiveRetrieverConfig,
    ProgressiveRetrieverResult,
    PromptBundle,
    SearchCursor,
    SelectableTarget,
    SelectionProtocol,
    SelectionResult,
)

# ── subtree/roots.py ──────────────────────────────────────────────────────


def _leaf_item(item_id: str, payload: str, label: str) -> RetrieverItem:
    return RetrieverItem(item_id=item_id, payload=payload, label=label, description=f"{label} description")


class ChoicesCacheKeyTests(unittest.TestCase):
    def test_empty_choices_produce_stable_hash(self) -> None:
        key1 = choices_cache_key([])
        key2 = choices_cache_key([])
        self.assertEqual(key1, key2)

    def test_different_choices_produce_different_keys(self) -> None:
        a = choices_cache_key([RetrieverChoice(choice_id="x", payload="p.a", description="desc")])
        b = choices_cache_key([RetrieverChoice(choice_id="y", payload="p.b", description="desc")])
        self.assertNotEqual(a, b)

    def test_attributes_with_defaults_are_handled(self) -> None:
        """Objects without choice_id/payload/description should not crash."""
        key = choices_cache_key([type("Obj", (), {})()])
        self.assertIsInstance(key, str)


class BuildProgressiveRootTests(unittest.TestCase):
    def test_flat_payload_returns_root_with_items(self) -> None:
        choices = [
            RetrieverChoice(choice_id="a", payload="flat_skill", description="flat desc"),
        ]
        root = build_progressive_root(choices, cache={})
        self.assertIsNotNone(root)
        self.assertEqual(len(root.items), 1)
        self.assertEqual(root.items[0].payload, "flat_skill")

    def test_hierarchical_payload_builds_nested_tree(self) -> None:
        choices = [
            RetrieverChoice(choice_id="a", payload="cat.sub.skill_a", description="desc a"),
            RetrieverChoice(choice_id="b", payload="cat.sub.skill_b", description="desc b"),
        ]
        root = build_progressive_root(choices, cache={})
        self.assertIsNotNone(root)
        # root has 1 child (cat), which has 1 child (cat.sub), which has 2 items
        self.assertEqual(len(root.children), 1)
        self.assertEqual(len(root.children[0].children[0].items), 2)

    def test_empty_choices_return_none(self) -> None:
        result = build_progressive_root([], cache={})
        self.assertIsNone(result)

    def test_cache_returns_cached_node_on_second_call(self) -> None:
        choices = [RetrieverChoice(choice_id="a", payload="skill", description="d")]
        cache: dict = {}
        first = build_progressive_root(choices, cache=cache)
        second = build_progressive_root(choices, cache=cache)
        self.assertIs(first, second)

    def test_missing_attributes_are_skipped(self) -> None:
        """choice_id='' or payload='' should be skipped."""
        choices = [RetrieverChoice(choice_id="", payload="", description="")]
        result = build_progressive_root(choices, cache={})
        self.assertIsNone(result)

    def test_deep_hierarchy_properly_builds_branches(self) -> None:
        choices = [
            RetrieverChoice(choice_id="x", payload="a.b.c.d.skill", description="deep"),
        ]
        root = build_progressive_root(choices, cache={})
        self.assertIsNotNone(root)
        self.assertEqual(len(root.children), 1)  # a
        self.assertEqual(len(root.children[0].children), 1)  # a.b
        self.assertEqual(len(root.children[0].children[0].children), 1)  # a.b.c
        self.assertEqual(len(root.children[0].children[0].children[0].children), 1)  # a.b.c.d
        self.assertEqual(len(root.children[0].children[0].children[0].children[0].items), 1)


class FreezeProgressiveRootTests(unittest.TestCase):
    def test_freeze_sorts_items_by_label(self) -> None:
        choices = [
            RetrieverChoice(choice_id="b", payload="cat.skill_b", description="d b"),
            RetrieverChoice(choice_id="a", payload="cat.skill_a", description="d a"),
        ]
        root = build_progressive_root(choices, cache={})
        self.assertIsNotNone(root)
        # items should be sorted by label
        labels = [item.label for item in root.children[0].items]
        self.assertEqual(labels, sorted(labels))


class BuildProgressiveItemLabelTests(unittest.TestCase):
    def test_choice_id_is_used_as_label(self) -> None:
        label = build_progressive_item_label(choice_id="MySkill", payload="cat.MySkill")
        self.assertEqual(label, "MySkill")

    def test_empty_choice_id_falls_back_to_payload_leaf(self) -> None:
        label = build_progressive_item_label(choice_id="", payload="cat.sub.Leaf")
        self.assertEqual(label, "Leaf")

    def test_empty_both_returns_payload(self) -> None:
        label = build_progressive_item_label(choice_id="", payload="raw_id")
        self.assertEqual(label, "raw_id")


class BuildProgressiveBranchDescriptionTests(unittest.TestCase):
    def test_description_is_empty_string(self) -> None:
        desc = build_progressive_branch_description(label="X", children=[], items=[])
        self.assertEqual(desc, "")


# ── subtree/default.py (cache paths) ──────────────────────────────────────


class SubtreeProviderCacheTests(unittest.TestCase):
    def test_cache_without_lock_returns_cached_subtree(self) -> None:
        root = RetrieverNode(node_id="r", label="R", items=(_leaf_item("i1", "p1", "L1"),))
        cursor = SearchCursor(node=root, depth=0, branch_path=("r",), top_k=1)
        cache: dict = {}
        provider = DefaultCurrentSubtreeProvider(
            config=ProgressiveRetrieverConfig(max_exposure_depth_per_call=1, exposure_threshold=0),
            subtree_item_count=lambda n: len(n.items),
            cache=cache,
        )
        first = provider.get_current_subtree(cursor=cursor)
        second = provider.get_current_subtree(cursor=cursor)
        # Cached result should have same fragment content
        self.assertEqual(first.fragment.rendered_tree, second.fragment.rendered_tree)
        self.assertGreater(len(cache), 0)

    def test_cache_with_lock_returns_cached_subtree(self) -> None:
        root = RetrieverNode(node_id="r", label="R", items=(_leaf_item("i1", "p1", "L1"),))
        cursor = SearchCursor(node=root, depth=0, branch_path=("r",), top_k=1)
        cache: dict = {}
        lock = threading.Lock()
        provider = DefaultCurrentSubtreeProvider(
            config=ProgressiveRetrieverConfig(max_exposure_depth_per_call=1, exposure_threshold=0),
            subtree_item_count=lambda n: len(n.items),
            cache=cache,
            cache_lock=lock,
        )
        first = provider.get_current_subtree(cursor=cursor)
        second = provider.get_current_subtree(cursor=cursor)
        self.assertEqual(first.fragment.rendered_tree, second.fragment.rendered_tree)

    def test_no_cache_builds_fresh_each_time(self) -> None:
        root = RetrieverNode(node_id="r", label="R", items=(_leaf_item("i1", "p1", "L1"),))
        cursor = SearchCursor(node=root, depth=0, branch_path=("r",), top_k=1)
        provider = DefaultCurrentSubtreeProvider(
            config=ProgressiveRetrieverConfig(max_exposure_depth_per_call=1, exposure_threshold=0),
            subtree_item_count=lambda n: len(n.items),
        )
        first = provider.get_current_subtree(cursor=cursor)
        second = provider.get_current_subtree(cursor=cursor)
        # No cache → different fragment objects but same content
        self.assertEqual(first.fragment.rendered_tree, second.fragment.rendered_tree)


# ── reduce.py (overflow & dedup) ──────────────────────────────────────────


class ReduceOverflowTests(unittest.TestCase):
    def test_round_robin_with_more_candidates_than_top_k(self) -> None:
        reducer = DefaultBranchReducer(config=ProgressiveRetrieverConfig(round_robin_branch_reduce=True))
        local = (
            RetrieverCandidate(rank=1, item_id="a1", payload="p.a", branch_path=("a",), label="A1"),
            RetrieverCandidate(rank=2, item_id="a2", payload="p.a2", branch_path=("a",), label="A2"),
            RetrieverCandidate(rank=3, item_id="a3", payload="p.a3", branch_path=("a",), label="A3"),
        )
        child = (
            NodeSearchResult(
                candidates=(
                    RetrieverCandidate(rank=1, item_id="b1", payload="p.b", branch_path=("b",), label="B1"),
                    RetrieverCandidate(rank=2, item_id="b2", payload="p.b2", branch_path=("b",), label="B2"),
                )
            ),
        )
        result = reducer.reduce_branch_results(
            cursor=SearchCursor(node=RetrieverNode(node_id="root", label="R"), depth=0, branch_path=("root",), top_k=2),
            local_leaves=local,
            child_results=child,
        )
        # top_k=2, so only 2 candidates survive even with overflow
        self.assertEqual(len(result.candidates), 2)

    def test_dedup_candidates_removes_duplicates(self) -> None:
        reducer = DefaultBranchReducer(config=ProgressiveRetrieverConfig(round_robin_branch_reduce=False))
        local = (
            RetrieverCandidate(rank=1, item_id="a1", payload="p.shared", branch_path=("a",), label="A"),
            RetrieverCandidate(rank=2, item_id="a2", payload="p.shared", branch_path=("a",), label="A2"),
        )
        child = (
            NodeSearchResult(
                candidates=(
                    RetrieverCandidate(
                        rank=1,
                        item_id="b1",
                        payload="p.shared",
                        branch_path=("b",),
                        label="B",
                    ),
                )
            ),
        )
        result = reducer.reduce_branch_results(
            cursor=SearchCursor(
                node=RetrieverNode(node_id="root", label="R"),
                depth=0,
                branch_path=("root",),
                top_k=10,
            ),
            local_leaves=local,
            child_results=child,
        )
        # Dedup: same payload "p.shared" appears 3 times but only once in result
        payloads = [c.payload for c in result.candidates]
        self.assertEqual(payloads, ["p.shared"])


# ── contracts.py (Protocol raise NotImplementedError) ─────────────────────


class ProtocolRaiseTests(unittest.TestCase):
    def test_current_subtree_provider_protocol_raises(self) -> None:
        class DelegatingProvider(CurrentSubtreeProvider):
            def get_current_subtree(self, *, cursor):
                return super().get_current_subtree(cursor=cursor)

        obj = DelegatingProvider()
        with self.assertRaises(NotImplementedError):
            obj.get_current_subtree(cursor=MagicMock())

    def test_subtree_renderer_protocol_raises(self) -> None:
        class DelegatingRenderer(SubtreeRenderer):
            def render_subtree(self, *, subtree, query_messages, protocol):
                return super().render_subtree(subtree=subtree, query_messages=query_messages, protocol=protocol)

        obj = DelegatingRenderer()
        with self.assertRaises(NotImplementedError):
            obj.render_subtree(subtree=MagicMock(), query_messages=[], protocol=MagicMock())

    def test_topk_selector_build_protocol_raises(self) -> None:
        class DelegatingSelector(TopKSelector):
            @staticmethod
            def build_protocol(*, subtree):
                return TopKSelector.build_protocol(None, subtree=subtree)

            def select_topk(self, *, model, cursor, query_messages, subtree, prompt, trace):
                return super().select_topk(
                    model=model,
                    cursor=cursor,
                    query_messages=query_messages,
                    subtree=subtree,
                    prompt=prompt,
                    trace=trace,
                )

        obj = DelegatingSelector()
        with self.assertRaises(NotImplementedError):
            obj.build_protocol(subtree=MagicMock())

    def test_topk_selector_select_raises(self) -> None:
        class DelegatingSelector(TopKSelector):
            @staticmethod
            def build_protocol(*, subtree):
                return TopKSelector.build_protocol(None, subtree=subtree)

            def select_topk(self, *, model, cursor, query_messages, subtree, prompt, trace):
                return super().select_topk(
                    model=model,
                    cursor=cursor,
                    query_messages=query_messages,
                    subtree=subtree,
                    prompt=prompt,
                    trace=trace,
                )

        obj = DelegatingSelector()
        with self.assertRaises(NotImplementedError):
            obj.select_topk(
                model="",
                cursor=MagicMock(),
                query_messages=[],
                subtree=MagicMock(),
                prompt=MagicMock(),
                trace=MagicMock(),
            )

    def test_target_expander_protocol_raises(self) -> None:
        class DelegatingExpander(TargetExpander):
            def expand_selected_targets(self, *, cursor, selected_targets):
                return super().expand_selected_targets(cursor=cursor, selected_targets=selected_targets)

        obj = DelegatingExpander()
        with self.assertRaises(NotImplementedError):
            obj.expand_selected_targets(cursor=MagicMock(), selected_targets=[])

    def test_branch_reducer_protocol_raises(self) -> None:
        class DelegatingReducer(BranchReducer):
            def reduce_branch_results(self, *, cursor, local_leaves, child_results):
                return super().reduce_branch_results(
                    cursor=cursor,
                    local_leaves=local_leaves,
                    child_results=child_results,
                )

        obj = DelegatingReducer()
        with self.assertRaises(NotImplementedError):
            obj.reduce_branch_results(cursor=MagicMock(), local_leaves=[], child_results=[])


# ── expand.py (edge cases) ────────────────────────────────────────────────


class ExpandEdgeCaseTests(unittest.TestCase):
    def test_resolve_branch_top_k_with_zero_branch_count(self) -> None:
        expander = DefaultTargetExpander(config=ProgressiveRetrieverConfig(branch_candidate_slack=0))
        result = getattr(expander, "_resolve_branch_top_k")(top_k=5, branch_count=0)
        self.assertEqual(result, 5)

    def test_resolve_branch_top_k_with_slack(self) -> None:
        expander = DefaultTargetExpander(config=ProgressiveRetrieverConfig(branch_candidate_slack=3))
        result = getattr(expander, "_resolve_branch_top_k")(top_k=5, branch_count=2)
        # ceil(5/2) + 3 = 3 + 3 = 6, capped at top_k=5
        self.assertEqual(result, 5)

    def test_expand_skips_target_with_null_node_and_non_terminal(self) -> None:
        """A non-terminal target with node=None should be skipped."""
        resolution = SelectableResolution(
            code="X",
            canonical_id="x",
            display_name="X",
            label="X",
            description="",
            is_terminal=False,
            branch_path=("root", "x"),
            item=None,
            node=None,
        )
        target = SelectableTarget(resolution=resolution)
        expander = DefaultTargetExpander(config=ProgressiveRetrieverConfig())
        plan = expander.expand_selected_targets(
            cursor=SearchCursor(node=RetrieverNode(node_id="root", label="R"), depth=0, branch_path=("root",), top_k=3),
            selected_targets=(target,),
        )
        self.assertEqual(len(plan.leaf_results), 0)
        self.assertEqual(len(plan.child_cursors), 0)


# ── engine.py (parallel branches & abstain) ───────────────────────────────


def _make_fragment(code: str, canonical_id: str, item: RetrieverItem | None = None) -> ExposedFragment:
    resolution = SelectableResolution(
        code=code,
        canonical_id=canonical_id,
        display_name=code,
        label=item.label if item else canonical_id,
        description=item.description if item else "",
        is_terminal=item is not None,
        branch_path=("root", canonical_id),
        item=item,
        node=None,
    )
    return ExposedFragment(
        root=ExposedNode(canonical_id="root", label="Root", description="", is_selectable=False, children=()),
        rendered_tree=f"- Candidate {resolution.label}",
        compact_codes_enabled=False,
        code_width=0,
        candidate_codes=(code,),
        selectable_nodes=(),
        code_to_resolution={code: resolution},
    )


class _StubProvider:
    def __init__(self, subtrees: dict) -> None:
        self.subtrees = dict(subtrees)
        self.calls: list[str] = []

    def get_current_subtree(self, *, cursor) -> CurrentSubtree:
        self.calls.append(cursor.node.node_id)
        return self.subtrees[cursor.node.node_id]


class _StubRenderer:
    calls: list[str] = []

    def render_subtree(self, *, subtree, query_messages, protocol) -> PromptBundle:
        self.calls.append(subtree.cursor.node.node_id)
        return PromptBundle(
            fragment=subtree.fragment,
            protocol=protocol,
            messages=({"role": "system", "content": "x"},),
        )


class _StubSelector:
    def __init__(self, outputs: dict, abstain_node: str | None = None) -> None:
        self.outputs = dict(outputs)
        self.abstain_node = abstain_node
        self.calls: list[str] = []

    @staticmethod
    def build_protocol(*, subtree) -> SelectionProtocol:
        return SelectionProtocol(
            compact_codes_enabled=False,
            candidate_codes=tuple(subtree.fragment.candidate_codes),
            code_width=0,
        )

    def select_topk(self, *, model, cursor, query_messages, subtree, prompt, trace) -> SelectionResult:
        self.calls.append(cursor.node.node_id)
        if self.abstain_node and cursor.node.node_id == self.abstain_node:
            return SelectionResult(raw_output="0", selected_targets=(), is_abstain=True)
        return self.outputs[cursor.node.node_id]


class _StubExpander:
    def __init__(self, plans: dict) -> None:
        self.plans = dict(plans)
        self.calls: list[str] = []

    def expand_selected_targets(self, *, cursor, selected_targets) -> ExpansionPlan:
        self.calls.append(cursor.node.node_id)
        return self.plans[cursor.node.node_id]


class _StubReducer:
    calls: list[tuple[str, int, int]] = []

    def reduce_branch_results(self, *, cursor, local_leaves, child_results) -> NodeSearchResult:
        self.calls.append((cursor.node.node_id, len(local_leaves), len(child_results)))
        merged = list(local_leaves)
        for result in child_results:
            merged.extend(result.candidates)
        return NodeSearchResult(candidates=tuple(merged[: cursor.top_k]))


class EngineParallelBranchTests(unittest.TestCase):
    def test_engine_parallel_branches_with_multiple_children(self) -> None:
        """Two child branches should be processed in parallel when enabled."""
        child_a_node = RetrieverNode(node_id="child_a", label="A", items=(_leaf_item("ia", "p.a", "A"),))
        child_b_node = RetrieverNode(node_id="child_b", label="B", items=(_leaf_item("ib", "p.b", "B"),))
        leaf_item = _leaf_item("alpha.item", "worker.alpha", "Alpha")
        root_cursor = SearchCursor(
            node=RetrieverNode(node_id="root", label="Root"),
            depth=0,
            branch_path=("root",),
            top_k=2,
        )

        root_resolution = SelectableResolution(
            code="Alpha",
            canonical_id="alpha",
            display_name="Alpha",
            label="Alpha",
            description="",
            is_terminal=True,
            branch_path=("root", "alpha"),
            item=leaf_item,
            node=None,
        )
        branch_a_resolution = SelectableResolution(
            code="ChildA",
            canonical_id="child_a",
            display_name="ChildA",
            label="ChildA",
            description="",
            is_terminal=False,
            branch_path=("root", "child_a"),
            item=None,
            node=child_a_node,
        )
        branch_b_resolution = SelectableResolution(
            code="ChildB",
            canonical_id="child_b",
            display_name="ChildB",
            label="ChildB",
            description="",
            is_terminal=False,
            branch_path=("root", "child_b"),
            item=None,
            node=child_b_node,
        )

        root_fragment = ExposedFragment(
            root=ExposedNode(canonical_id="root", label="Root", description="", is_selectable=False, children=()),
            rendered_tree="- Alpha\n- ChildA\n- ChildB",
            compact_codes_enabled=False,
            code_width=0,
            candidate_codes=("Alpha", "ChildA", "ChildB"),
            selectable_nodes=(),
            code_to_resolution={"Alpha": root_resolution, "ChildA": branch_a_resolution, "ChildB": branch_b_resolution},
        )

        child_a_fragment = _make_fragment("LeafA", "worker.a", item=_leaf_item("ia", "p.a", "A"))
        child_b_fragment = _make_fragment("LeafB", "worker.b", item=_leaf_item("ib", "p.b", "B"))

        provider = _StubProvider(
            {
                "root": CurrentSubtree(
                    cursor=root_cursor,
                    fragment=root_fragment,
                    selectable_targets=(
                        SelectableTarget(root_resolution),
                        SelectableTarget(branch_a_resolution),
                        SelectableTarget(branch_b_resolution),
                    ),
                ),
                "child_a": CurrentSubtree(
                    cursor=SearchCursor(node=child_a_node, depth=1, branch_path=("root", "child_a"), top_k=1),
                    fragment=child_a_fragment,
                    selectable_targets=(SelectableTarget(child_a_fragment.code_to_resolution["LeafA"]),),
                ),
                "child_b": CurrentSubtree(
                    cursor=SearchCursor(node=child_b_node, depth=1, branch_path=("root", "child_b"), top_k=1),
                    fragment=child_b_fragment,
                    selectable_targets=(SelectableTarget(child_b_fragment.code_to_resolution["LeafB"]),),
                ),
            }
        )
        renderer = _StubRenderer()
        selector = _StubSelector(
            outputs={
                "root": SelectionResult(
                    raw_output="ChildA\nChildB",
                    selected_targets=(
                        provider.subtrees["root"].selectable_targets[1],
                        provider.subtrees["root"].selectable_targets[2],
                    ),
                ),
            }
        )
        expander = _StubExpander(
            {
                "root": ExpansionPlan(
                    leaf_results=(),
                    child_cursors=(
                        ChildSearchCursor(
                            cursor=provider.subtrees["child_a"].cursor,
                            target=provider.subtrees["root"].selectable_targets[1],
                        ),
                        ChildSearchCursor(
                            cursor=provider.subtrees["child_b"].cursor,
                            target=provider.subtrees["root"].selectable_targets[2],
                        ),
                    ),
                ),
                "child_a": ExpansionPlan(
                    leaf_results=(
                        RetrieverCandidate(
                            rank=1, item_id="ia", payload="p.a", branch_path=("root", "child_a"), label="A"
                        ),
                    ),
                    child_cursors=(),
                ),
                "child_b": ExpansionPlan(
                    leaf_results=(
                        RetrieverCandidate(
                            rank=1, item_id="ib", payload="p.b", branch_path=("root", "child_b"), label="B"
                        ),
                    ),
                    child_cursors=(),
                ),
            }
        )
        reducer = _StubReducer()
        engine = RecursiveSearchEngine(
            subtree_provider=provider,
            renderer=renderer,
            selector=selector,
            expander=expander,
            reducer=reducer,
            enable_parallel_branches=True,
        )

        result = engine.search(
            model="m",
            query_messages=[{"role": "user", "content": "q"}],
            root_cursor=root_cursor,
            trace=RetrieverTrace(),
        )
        # Both child branches should be explored
        self.assertIn("child_a", provider.calls)
        self.assertIn("child_b", provider.calls)
        self.assertEqual({candidate.payload for candidate in result}, {"p.a", "p.b"})


class EngineAbstainTests(unittest.TestCase):
    def test_engine_abstain_selection_returns_empty_candidates(self) -> None:
        """When selector abstains, engine should return empty candidates."""
        leaf_item = _leaf_item("alpha.item", "worker.alpha", "Alpha")
        fragment = _make_fragment("Alpha", "worker.alpha", item=leaf_item)
        # Need 2+ targets so engine calls selector (not shortcut)
        alt_resolution = SelectableResolution(
            code="Alt",
            canonical_id="alt",
            display_name="Alt",
            label="Alt",
            description="",
            is_terminal=True,
            branch_path=("root", "alt"),
            item=_leaf_item("alt.item", "worker.alt", "Alt"),
            node=None,
        )
        alt_fragment = ExposedFragment(
            root=ExposedNode(canonical_id="root", label="Root", description="", is_selectable=False, children=()),
            rendered_tree="- Alpha\n- Alt",
            compact_codes_enabled=False,
            code_width=0,
            candidate_codes=("Alpha", "Alt"),
            selectable_nodes=(),
            code_to_resolution={"Alpha": fragment.code_to_resolution["Alpha"], "Alt": alt_resolution},
        )
        root_cursor = SearchCursor(
            node=RetrieverNode(node_id="root", label="Root"),
            depth=0,
            branch_path=("root",),
            top_k=2,
        )
        provider = _StubProvider(
            {
                "root": CurrentSubtree(
                    cursor=root_cursor,
                    fragment=alt_fragment,
                    selectable_targets=(
                        SelectableTarget(fragment.code_to_resolution["Alpha"]),
                        SelectableTarget(alt_resolution),
                    ),
                ),
            }
        )
        renderer = _StubRenderer()
        selector = _StubSelector(outputs={}, abstain_node="root")
        expander = _StubExpander({})
        reducer = _StubReducer()
        engine = RecursiveSearchEngine(
            subtree_provider=provider,
            renderer=renderer,
            selector=selector,
            expander=expander,
            reducer=reducer,
            enable_parallel_branches=False,
        )

        result = engine.search(
            model="m",
            query_messages=[{"role": "user", "content": "q"}],
            root_cursor=root_cursor,
            trace=RetrieverTrace(),
        )
        self.assertEqual(result, [])


class EngineEmptySelectableTests(unittest.TestCase):
    def test_engine_empty_selectable_targets_returns_empty(self) -> None:
        """No selectable targets → engine returns empty candidates."""
        root_cursor = SearchCursor(
            node=RetrieverNode(node_id="root", label="Root"),
            depth=0,
            branch_path=("root",),
            top_k=2,
        )
        empty_fragment = ExposedFragment(
            root=ExposedNode(canonical_id="root", label="Root", description="", is_selectable=False, children=()),
            rendered_tree="",
            compact_codes_enabled=False,
            code_width=0,
            candidate_codes=(),
            selectable_nodes=(),
            code_to_resolution={},
        )
        provider = _StubProvider(
            {
                "root": CurrentSubtree(cursor=root_cursor, fragment=empty_fragment, selectable_targets=()),
            }
        )
        renderer = _StubRenderer()
        selector = _StubSelector(outputs={})
        expander = _StubExpander({})
        reducer = _StubReducer()
        engine = RecursiveSearchEngine(
            subtree_provider=provider,
            renderer=renderer,
            selector=selector,
            expander=expander,
            reducer=reducer,
            enable_parallel_branches=False,
        )

        result = engine.search(
            model="m",
            query_messages=[{"role": "user", "content": "q"}],
            root_cursor=root_cursor,
            trace=RetrieverTrace(),
        )
        self.assertEqual(result, [])


# ── select/selection.py (LogitSelectionFragmentSelector) ───────────────────


class LogitSelectionFallbackTests(unittest.TestCase):
    def test_abstain_fallback_returns_zero_and_empty_list(self) -> None:
        mock_client = MagicMock()
        mock_client.capabilities = MagicMock(candidate_scoring=False)
        mock_client.name = "mock"
        gen_selector = GenerateFragmentSelector(generate_fn=MagicMock(return_value=("Q1", [])))
        selector = LogitSelectionFragmentSelector(
            client=mock_client,
            require_single_token_codes=True,
            fallback_mode="abstain",
            generate_selector=gen_selector,
        )
        node = RetrieverNode(node_id="n", label="N")
        fragment = _make_fragment("Q1", "x", item=_leaf_item("i", "p", "L"))

        output, selected = selector.select(
            model="m",
            query_messages=[{"role": "user", "content": "q"}],
            node=node,
            depth=0,
            top_k=1,
            trace=RetrieverTrace(),
            fragment=fragment,
        )
        self.assertEqual(output, "0")
        self.assertEqual(selected, [])

    def test_error_fallback_raises_runtime_error(self) -> None:
        mock_client = MagicMock()
        mock_client.capabilities = MagicMock(candidate_scoring=False)
        mock_client.name = "mock"
        gen_selector = GenerateFragmentSelector(generate_fn=MagicMock())
        selector = LogitSelectionFragmentSelector(
            client=mock_client,
            require_single_token_codes=True,
            fallback_mode="error",
            generate_selector=gen_selector,
        )
        node = RetrieverNode(node_id="n", label="N")
        fragment = _make_fragment("Q1", "x", item=_leaf_item("i", "p", "L"))

        with self.assertRaises(RuntimeError):
            selector.select(
                model="m",
                query_messages=[{"role": "user", "content": "q"}],
                node=node,
                depth=0,
                top_k=1,
                trace=RetrieverTrace(),
                fragment=fragment,
            )

    def test_generate_fallback_delegates_to_generate_selector(self) -> None:
        mock_client = MagicMock()
        mock_client.capabilities = MagicMock(candidate_scoring=False)
        mock_client.name = "mock"
        gen_fn = MagicMock(return_value=("Q1", [MagicMock()]))
        gen_selector = GenerateFragmentSelector(generate_fn=gen_fn)
        selector = LogitSelectionFragmentSelector(
            client=mock_client,
            require_single_token_codes=True,
            fallback_mode="generate",
            generate_selector=gen_selector,
        )
        node = RetrieverNode(node_id="n", label="N")
        fragment = _make_fragment("Q1", "x", item=_leaf_item("i", "p", "L"))

        output, selected = selector.select(
            model="m",
            query_messages=[{"role": "user", "content": "q"}],
            node=node,
            depth=0,
            top_k=1,
            trace=RetrieverTrace(),
            fragment=fragment,
        )
        self.assertEqual(output, "Q1")
        self.assertEqual(len(selected), 1)
        gen_fn.assert_called_once()

    def test_candidate_count_exceeds_limit_triggers_fallback(self) -> None:
        mock_client = MagicMock()
        mock_client.capabilities = MagicMock(candidate_scoring=True)
        mock_client.name = "mock"
        gen_fn = MagicMock(return_value=("Q1", [MagicMock()]))
        gen_selector = GenerateFragmentSelector(generate_fn=gen_fn)
        selector = LogitSelectionFragmentSelector(
            client=mock_client,
            require_single_token_codes=True,
            fallback_mode="generate",
            generate_selector=gen_selector,
            max_candidates=1,
        )
        # Create a fragment with 2 candidate codes (exceeds max_candidates=1)
        r1 = SelectableResolution(
            code="Q1",
            canonical_id="x1",
            display_name="Q1",
            label="Q1",
            description="",
            is_terminal=True,
            branch_path=("r", "x1"),
            item=_leaf_item("i1", "p1", "L1"),
            node=None,
        )
        r2 = SelectableResolution(
            code="Q2",
            canonical_id="x2",
            display_name="Q2",
            label="Q2",
            description="",
            is_terminal=True,
            branch_path=("r", "x2"),
            item=_leaf_item("i2", "p2", "L2"),
            node=None,
        )
        fragment = ExposedFragment(
            root=ExposedNode(canonical_id="root", label="Root", description="", is_selectable=False, children=()),
            rendered_tree="- Q1\n- Q2",
            compact_codes_enabled=True,
            code_width=2,
            candidate_codes=("Q1", "Q2"),
            selectable_nodes=(),
            code_to_resolution={"Q1": r1, "Q2": r2},
        )
        node = RetrieverNode(node_id="n", label="N")

        output, selected = selector.select(
            model="m",
            query_messages=[{"role": "user", "content": "q"}],
            node=node,
            depth=0,
            top_k=1,
            trace=RetrieverTrace(),
            fragment=fragment,
        )
        # Should fallback to generate because candidate_count > max_candidates
        gen_fn.assert_called_once()

    def test_min_probability_filters_low_probability_scores(self) -> None:
        """When min_probability is set, scores below threshold should be filtered out."""

        mock_client = MagicMock()
        mock_client.name = "mock"
        mock_client.capabilities = MagicMock(candidate_scoring=True)
        # Build a scoring result with low-probability candidates
        scores = [
            CandidateScore(code="Q1", canonical_id="x1", token_id=101, logit=10.0, probability=0.9, rank=1),
            CandidateScore(code="Q2", canonical_id="x2", token_id=102, logit=-5.0, probability=0.01, rank=2),
        ]
        scoring_result = MagicMock()
        scoring_result.scores = scores
        scoring_result.latency_breakdown = {"total_ms": 1.0}
        mock_client.score_candidate_codes.return_value = scoring_result

        gen_selector = GenerateFragmentSelector(generate_fn=MagicMock())
        selector = LogitSelectionFragmentSelector(
            client=mock_client,
            require_single_token_codes=True,
            fallback_mode="generate",
            generate_selector=gen_selector,
            min_probability=0.5,
        )
        r1 = SelectableResolution(
            code="Q1",
            canonical_id="x1",
            display_name="Q1",
            label="Q1",
            description="",
            is_terminal=True,
            branch_path=("r", "x1"),
            item=_leaf_item("i1", "p1", "L1"),
            node=None,
        )
        r2 = SelectableResolution(
            code="Q2",
            canonical_id="x2",
            display_name="Q2",
            label="Q2",
            description="",
            is_terminal=True,
            branch_path=("r", "x2"),
            item=_leaf_item("i2", "p2", "L2"),
            node=None,
        )
        fragment = ExposedFragment(
            root=ExposedNode(canonical_id="root", label="Root", description="", is_selectable=False, children=()),
            rendered_tree="- Q1\n- Q2",
            compact_codes_enabled=True,
            code_width=2,
            candidate_codes=("Q1", "Q2"),
            selectable_nodes=(),
            code_to_resolution={"Q1": r1, "Q2": r2},
        )
        node = RetrieverNode(node_id="n", label="N")

        output, selected = selector.select(
            model="m",
            query_messages=[{"role": "user", "content": "q"}],
            node=node,
            depth=0,
            top_k=2,
            trace=RetrieverTrace(),
            fragment=fragment,
        )
        # Only Q1 (probability 0.9 >= 0.5) should be selected
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].code, "Q1")
        self.assertEqual(output, "Q1")


# ── flat.py ────────────────────────────────────────────────────────────────


class FlatRetrieverTests(unittest.TestCase):
    def _make_flat_retriever(self, llm_output: str) -> FlatRetriever:
        """Build a FlatRetriever with a mock LLM that returns the given output."""
        from openjiuwen.symphony.retrieval.llm import LLMClientCapabilities, ProgressiveLLMClient

        class _StubLLM(ProgressiveLLMClient):
            name = "stub"

            @property
            def capabilities(self):
                return LLMClientCapabilities(completion=True, streaming=False, trie_constrained_decoding=True)

            def complete(
                self,
                model,
                messages,
                *,
                max_tokens=None,
                stop_sequences=None,
                generation_config=None,
                n=1,
                request_timeout=None,
            ):
                self.calls.append({"model": model, "messages": messages, "max_tokens": max_tokens})
                return [llm_output]

        llm = _StubLLM()
        llm.calls: list = []
        config = ProgressiveRetrieverConfig(
            top_k=2,
            max_exposure_depth_per_call=0,
            exposure_threshold=0,
            compact_boundary_codes_enabled=True,
            compact_boundary_codebook=("X1", "X2"),
        )
        retriever = FlatRetriever(llm=llm, config=config)
        setattr(retriever, "_llm", llm)
        return retriever

    def test_flat_retriever_returns_candidates(self) -> None:
        retriever = self._make_flat_retriever("X1\nX2")
        choices = (
            RetrieverChoice(choice_id="skill_a", payload="worker.a", description="Skill A"),
            RetrieverChoice(choice_id="skill_b", payload="worker.b", description="Skill B"),
        )
        result = retriever.retrieve_top_k(
            model="m",
            query="need a and b",
            choices=choices,
            resolve_candidate=lambda cid, msg: f"resolved:{cid}",
            system_prompt="system",
            top_k=2,
        )
        self.assertIsInstance(result, ProgressiveRetrieverResult)
        self.assertIsNotNone(result.trace)
        self.assertIsNotNone(result.request_messages)


if __name__ == "__main__":
    unittest.main()
