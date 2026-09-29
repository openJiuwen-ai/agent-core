from __future__ import annotations

import time
import unittest

from openjiuwen.symphony.retrieval.common.models import RetrieverCandidate, RetrieverItem, RetrieverNode, RetrieverTrace
from openjiuwen.symphony.retrieval.search.runtime.engine import RecursiveSearchEngine
from openjiuwen.symphony.retrieval.search.runtime.expand import DefaultTargetExpander
from openjiuwen.symphony.retrieval.search.runtime.reduce import DefaultBranchReducer
from openjiuwen.symphony.retrieval.search.runtime.render import DefaultSubtreeRenderer
from openjiuwen.symphony.retrieval.search.runtime.render.disclosure import (
    ExposedFragment,
    ExposedNode,
    SelectableResolution,
)
from openjiuwen.symphony.retrieval.search.runtime.selector import DefaultTopKSelector
from openjiuwen.symphony.retrieval.search.runtime.subtree import DefaultCurrentSubtreeProvider
from openjiuwen.symphony.retrieval.search.runtime.types import (
    ChildSearchCursor,
    CurrentSubtree,
    ExpansionPlan,
    NodeSearchResult,
    ProgressiveRetrieverConfig,
    PromptBundle,
    SearchCursor,
    SelectableTarget,
    SelectionProtocol,
    SelectionResult,
)


def _leaf_item(item_id: str, payload: str, label: str) -> RetrieverItem:
    return RetrieverItem(item_id=item_id, payload=payload, label=label, description=f"{label} description")


def _single_fragment(
    *,
    code: str,
    canonical_id: str,
    item: RetrieverItem | None = None,
    node: RetrieverNode | None = None,
) -> ExposedFragment:
    resolution = SelectableResolution(
        code=code,
        canonical_id=canonical_id,
        display_name=code,
        label=item.label if item is not None else (node.label if node is not None else canonical_id),
        description=item.description if item is not None else (node.description if node is not None else ""),
        is_terminal=item is not None,
        branch_path=("root", canonical_id),
        node=node,
        item=item,
    )
    return ExposedFragment(
        root=ExposedNode(
            canonical_id="root",
            label="Root",
            description="",
            is_selectable=False,
            children=(),
        ),
        rendered_tree=f"- Candidate {resolution.label}",
        compact_codes_enabled=False,
        code_width=0,
        candidate_codes=(code,),
        selectable_nodes=(),
        code_to_resolution={code: resolution},
    )


class ProgressiveSubtreeProviderTests(unittest.TestCase):
    def test_provider_wraps_fragment_resolutions_as_selectable_targets(self) -> None:
        root = RetrieverNode(node_id="root", label="Root", items=(_leaf_item("alpha.item", "worker.alpha", "Alpha"),))
        cursor = SearchCursor(node=root, depth=0, branch_path=("root",), top_k=1)
        provider = DefaultCurrentSubtreeProvider(
            config=ProgressiveRetrieverConfig(max_exposure_depth_per_call=1, exposure_threshold=5),
            subtree_item_count=lambda current: 1,
        )

        subtree = provider.get_current_subtree(cursor=cursor)

        self.assertEqual(len(subtree.selectable_targets), 1)
        self.assertEqual(subtree.selectable_targets[0].resolution.item.payload, "worker.alpha")
        self.assertIn("Alpha", subtree.fragment.rendered_tree)


class ProgressiveRenderTests(unittest.TestCase):
    def test_renderer_builds_prompt_bundle_from_subtree_and_query(self) -> None:
        item = _leaf_item("alpha.item", "worker.alpha", "Alpha")
        fragment = _single_fragment(code="Alpha", canonical_id="worker.alpha", item=item)
        subtree = CurrentSubtree(
            cursor=SearchCursor(
                node=RetrieverNode(node_id="root", label="Root"),
                depth=0,
                branch_path=("root",),
                top_k=2,
            ),
            fragment=fragment,
            selectable_targets=(SelectableTarget(resolution=fragment.code_to_resolution["Alpha"]),),
        )

        bundle = DefaultSubtreeRenderer().render_subtree(
            subtree=subtree,
            query_messages=[{"role": "user", "content": "need alpha"}],
            protocol=SelectionProtocol(compact_codes_enabled=False, candidate_codes=("Alpha",), code_width=0),
        )

        self.assertIsInstance(bundle, PromptBundle)
        self.assertEqual(bundle.messages[0]["role"], "system")
        self.assertIn("# 候选列表", bundle.messages[0]["content"])
        self.assertIn("<CANDIDATE_TREE>", bundle.messages[0]["content"])
        self.assertIn(fragment.rendered_tree, bundle.messages[0]["content"])
        self.assertNotIn("<CANDIDATE_TREE>", bundle.messages[1]["content"])
        self.assertNotIn(fragment.rendered_tree, bundle.messages[1]["content"])
        self.assertIn("<USER_REQUEST>", bundle.messages[1]["content"])
        self.assertIn("need alpha", bundle.messages[1]["content"])


class _FakeGenerateSelector:
    def __init__(self, output: str, selected: list[SelectableResolution]) -> None:
        self.output = output
        self.selected = list(selected)
        self.calls: list[dict[str, object]] = []

    def select(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.output, list(self.selected)


class ProgressiveSelectTests(unittest.TestCase):
    def test_build_protocol_uses_fragment_codes(self) -> None:
        item = _leaf_item("alpha.item", "worker.alpha", "Alpha")
        fragment = _single_fragment(code="Q1", canonical_id="worker.alpha", item=item)
        subtree = CurrentSubtree(
            cursor=SearchCursor(
                node=RetrieverNode(node_id="root", label="Root"),
                depth=0,
                branch_path=("root",),
                top_k=1,
            ),
            fragment=fragment,
            selectable_targets=(SelectableTarget(resolution=fragment.code_to_resolution["Q1"]),),
        )
        selector = DefaultTopKSelector(
            config=ProgressiveRetrieverConfig(),
            build_generate_selector=lambda: _FakeGenerateSelector("Q1", [fragment.code_to_resolution["Q1"]]),
        )

        protocol = selector.build_protocol(subtree=subtree)

        self.assertTrue(protocol.candidate_codes)
        self.assertEqual(protocol.candidate_codes, ("Q1",))

    def test_select_topk_marks_abstain_when_output_is_zero(self) -> None:
        item = _leaf_item("alpha.item", "worker.alpha", "Alpha")
        fragment = _single_fragment(code="Q1", canonical_id="worker.alpha", item=item)
        subtree = CurrentSubtree(
            cursor=SearchCursor(
                node=RetrieverNode(node_id="root", label="Root"),
                depth=0,
                branch_path=("root",),
                top_k=1,
            ),
            fragment=fragment,
            selectable_targets=(SelectableTarget(resolution=fragment.code_to_resolution["Q1"]),),
        )
        selector = DefaultTopKSelector(
            config=ProgressiveRetrieverConfig(),
            build_generate_selector=lambda: _FakeGenerateSelector("0", []),
        )

        result = selector.select_topk(
            model="demo-model",
            cursor=subtree.cursor,
            query_messages=[{"role": "user", "content": "need alpha"}],
            subtree=subtree,
            prompt=PromptBundle(
                fragment=fragment,
                protocol=SelectionProtocol(compact_codes_enabled=False, candidate_codes=("Q1",), code_width=0),
                messages=(),
            ),
            trace=RetrieverTrace(),
        )

        self.assertTrue(result.is_abstain)
        self.assertEqual(result.selected_targets, ())


class ProgressiveExpandTests(unittest.TestCase):
    def test_expander_splits_leafs_and_child_cursors(self) -> None:
        leaf = _leaf_item("alpha.item", "worker.alpha", "Alpha")
        branch = RetrieverNode(node_id="beta", label="Beta")
        selected_targets = (
            SelectableTarget(
                resolution=SelectableResolution(
                    code="Alpha",
                    canonical_id="worker.alpha",
                    display_name="Alpha",
                    label="Alpha",
                    description="Alpha description",
                    is_terminal=True,
                    branch_path=("root", "alpha"),
                    item=leaf,
                    node=None,
                )
            ),
            SelectableTarget(
                resolution=SelectableResolution(
                    code="Beta",
                    canonical_id="beta",
                    display_name="Beta",
                    label="Beta",
                    description="Beta description",
                    is_terminal=False,
                    branch_path=("root", "beta"),
                    item=None,
                    node=branch,
                )
            ),
        )
        plan = DefaultTargetExpander(
            config=ProgressiveRetrieverConfig(branch_candidate_slack=1)
        ).expand_selected_targets(
            cursor=SearchCursor(
                node=RetrieverNode(node_id="root", label="Root"),
                depth=0,
                branch_path=("root",),
                top_k=5,
            ),
            selected_targets=selected_targets,
        )

        self.assertEqual([item.payload for item in plan.leaf_results], ["worker.alpha"])
        self.assertEqual(len(plan.child_cursors), 1)
        self.assertEqual(plan.child_cursors[0].cursor.node.node_id, "beta")
        self.assertEqual(plan.child_cursors[0].cursor.top_k, 4)


class ProgressiveReduceTests(unittest.TestCase):
    def test_round_robin_reduce_interleaves_and_dedupes(self) -> None:
        reducer = DefaultBranchReducer(config=ProgressiveRetrieverConfig(round_robin_branch_reduce=True))
        local = (
            RetrieverCandidate(rank=1, item_id="a1", payload="worker.a", branch_path=("a",), label="A1"),
            RetrieverCandidate(rank=1, item_id="a2", payload="worker.shared", branch_path=("a",), label="A2"),
        )
        child_results = (
            NodeSearchResult(
                candidates=(
                    RetrieverCandidate(rank=1, item_id="b1", payload="worker.b", branch_path=("b",), label="B1"),
                    RetrieverCandidate(rank=2, item_id="b2", payload="worker.shared", branch_path=("b",), label="B2"),
                )
            ),
        )

        result = reducer.reduce_branch_results(
            cursor=SearchCursor(
                node=RetrieverNode(node_id="root", label="Root"),
                depth=0,
                branch_path=("root",),
                top_k=3,
            ),
            local_leaves=local,
            child_results=child_results,
        )

        self.assertEqual([item.payload for item in result.candidates], ["worker.a", "worker.b", "worker.shared"])

    def test_sequential_reduce_preserves_branch_order(self) -> None:
        reducer = DefaultBranchReducer(config=ProgressiveRetrieverConfig(round_robin_branch_reduce=False))
        result = reducer.reduce_branch_results(
            cursor=SearchCursor(
                node=RetrieverNode(node_id="root", label="Root"),
                depth=0,
                branch_path=("root",),
                top_k=3,
            ),
            local_leaves=(RetrieverCandidate(rank=1, item_id="a", payload="worker.a", branch_path=("a",), label="A"),),
            child_results=(
                NodeSearchResult(
                    candidates=(
                        RetrieverCandidate(
                            rank=1,
                            item_id="b",
                            payload="worker.b",
                            branch_path=("b",),
                            label="B",
                        ),
                    )
                ),
                NodeSearchResult(
                    candidates=(
                        RetrieverCandidate(
                            rank=1,
                            item_id="c",
                            payload="worker.c",
                            branch_path=("c",),
                            label="C",
                        ),
                    )
                ),
            ),
        )

        self.assertEqual([item.payload for item in result.candidates], ["worker.a", "worker.b", "worker.c"])


class _StubSubtreeProvider:
    def __init__(self, subtrees: dict[str, CurrentSubtree]) -> None:
        self.subtrees = dict(subtrees)
        self.calls: list[str] = []

    def get_current_subtree(self, *, cursor: SearchCursor) -> CurrentSubtree:
        self.calls.append(cursor.node.node_id)
        return self.subtrees[cursor.node.node_id]


class _StubRenderer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def render_subtree(self, *, subtree: CurrentSubtree, query_messages, protocol) -> PromptBundle:
        self.calls.append(subtree.cursor.node.node_id)
        return PromptBundle(
            fragment=subtree.fragment,
            protocol=protocol,
            messages=({"role": "system", "content": "x"},),
        )


class _StubSelector:
    def __init__(self, outputs: dict[str, SelectionResult], sleep_by_node: dict[str, float] | None = None) -> None:
        self.outputs = dict(outputs)
        self.sleep_by_node = dict(sleep_by_node or {})
        self.calls: list[str] = []

    @staticmethod
    def build_protocol(*, subtree: CurrentSubtree) -> SelectionProtocol:
        return SelectionProtocol(
            compact_codes_enabled=False,
            candidate_codes=tuple(subtree.fragment.candidate_codes),
            code_width=0,
        )

    def select_topk(
        self,
        *,
        model: str,
        cursor: SearchCursor,
        query_messages,
        subtree: CurrentSubtree,
        prompt: PromptBundle,
        trace: RetrieverTrace,
    ) -> SelectionResult:
        self.calls.append(cursor.node.node_id)
        if cursor.node.node_id in self.sleep_by_node:
            time.sleep(self.sleep_by_node[cursor.node.node_id])
        return self.outputs[cursor.node.node_id]


class _StubExpander:
    def __init__(self, plans: dict[str, ExpansionPlan]) -> None:
        self.plans = dict(plans)
        self.calls: list[str] = []

    def expand_selected_targets(self, *, cursor: SearchCursor, selected_targets) -> ExpansionPlan:
        self.calls.append(cursor.node.node_id)
        return self.plans[cursor.node.node_id]


class _StubReducer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def reduce_branch_results(self, *, cursor: SearchCursor, local_leaves, child_results) -> NodeSearchResult:
        self.calls.append((cursor.node.node_id, len(local_leaves), len(child_results)))
        merged = list(local_leaves)
        for result in child_results:
            merged.extend(result.candidates)
        return NodeSearchResult(candidates=tuple(merged[: cursor.top_k]))


class ProgressiveEngineTests(unittest.TestCase):
    def test_engine_uses_single_selectable_shortcut_without_render_or_select(self) -> None:
        leaf = _leaf_item("alpha.item", "worker.alpha", "Alpha")
        fragment = _single_fragment(code="Alpha", canonical_id="worker.alpha", item=leaf)
        root_cursor = SearchCursor(
            node=RetrieverNode(node_id="root", label="Root"),
            depth=0,
            branch_path=("root",),
            top_k=1,
        )
        subtree = CurrentSubtree(
            cursor=root_cursor,
            fragment=fragment,
            selectable_targets=(SelectableTarget(resolution=fragment.code_to_resolution["Alpha"]),),
        )
        provider = _StubSubtreeProvider({"root": subtree})
        renderer = _StubRenderer()
        selector = _StubSelector(outputs={})
        expander = _StubExpander(
            {
                "root": ExpansionPlan(
                    leaf_results=(
                        RetrieverCandidate(
                            rank=1,
                            item_id="alpha.item",
                            payload="worker.alpha",
                            branch_path=("root", "alpha"),
                            label="Alpha",
                        ),
                    ),
                    child_cursors=(),
                )
            }
        )
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
            query_messages=[{"role": "user", "content": "need alpha"}],
            root_cursor=root_cursor,
            trace=RetrieverTrace(),
        )

        self.assertEqual([item.payload for item in result], ["worker.alpha"])
        self.assertEqual(renderer.calls, [])
        self.assertEqual(selector.calls, [])
        self.assertEqual(expander.calls, ["root"])
        self.assertEqual(reducer.calls, [("root", 1, 0)])

    def test_engine_recurses_into_child_branch_and_reduces_results(self) -> None:
        child_node = RetrieverNode(node_id="child", label="Child")
        root_cursor = SearchCursor(
            node=RetrieverNode(node_id="root", label="Root"),
            depth=0,
            branch_path=("root",),
            top_k=2,
        )
        child_cursor = SearchCursor(node=child_node, depth=1, branch_path=("root", "child"), top_k=1)
        root_resolution = SelectableResolution(
            code="Child",
            canonical_id="child",
            display_name="Child",
            label="Child",
            description="Child description",
            is_terminal=False,
            branch_path=("root", "child"),
            item=None,
            node=child_node,
        )
        child_resolution = SelectableResolution(
            code="Leaf",
            canonical_id="worker.leaf",
            display_name="Leaf",
            label="Leaf",
            description="Leaf description",
            is_terminal=True,
            branch_path=("root", "child", "leaf"),
            item=_leaf_item("leaf.item", "worker.leaf", "Leaf"),
            node=None,
        )
        root_fragment = ExposedFragment(
            root=ExposedNode(canonical_id="root", label="Root", description="", is_selectable=False, children=()),
            rendered_tree="- Candidate Child",
            compact_codes_enabled=False,
            code_width=0,
            candidate_codes=("Child",),
            selectable_nodes=(),
            code_to_resolution={"Child": root_resolution},
        )
        child_fragment = ExposedFragment(
            root=ExposedNode(canonical_id="child", label="Child", description="", is_selectable=False, children=()),
            rendered_tree="- Candidate Leaf",
            compact_codes_enabled=False,
            code_width=0,
            candidate_codes=("Leaf",),
            selectable_nodes=(),
            code_to_resolution={"Leaf": child_resolution},
        )
        provider = _StubSubtreeProvider(
            {
                "root": CurrentSubtree(
                    cursor=root_cursor,
                    fragment=root_fragment,
                    selectable_targets=(
                        SelectableTarget(root_resolution),
                        SelectableTarget(
                            SelectableResolution(
                                code="Alt",
                                canonical_id="alt",
                                display_name="Alt",
                                label="Alt",
                                description="Alt description",
                                is_terminal=False,
                                branch_path=("root", "alt"),
                                item=None,
                                node=RetrieverNode(node_id="alt", label="Alt"),
                            )
                        ),
                    ),
                ),
                "child": CurrentSubtree(
                    cursor=child_cursor,
                    fragment=child_fragment,
                    selectable_targets=(SelectableTarget(child_resolution),),
                ),
                "alt": CurrentSubtree(
                    cursor=SearchCursor(
                        node=RetrieverNode(node_id="alt", label="Alt"),
                        depth=1,
                        branch_path=("root", "alt"),
                        top_k=1,
                    ),
                    fragment=child_fragment,
                    selectable_targets=(SelectableTarget(child_resolution),),
                ),
            }
        )
        renderer = _StubRenderer()
        selector = _StubSelector(
            outputs={
                "root": SelectionResult(
                    raw_output="Child\nAlt",
                    selected_targets=provider.subtrees["root"].selectable_targets,
                ),
            }
        )
        expander = _StubExpander(
            {
                "root": ExpansionPlan(
                    leaf_results=(),
                    child_cursors=(
                        ChildSearchCursor(
                            cursor=provider.subtrees["child"].cursor,
                            target=provider.subtrees["root"].selectable_targets[0],
                        ),
                        ChildSearchCursor(
                            cursor=provider.subtrees["alt"].cursor,
                            target=provider.subtrees["root"].selectable_targets[1],
                        ),
                    ),
                ),
                "child": ExpansionPlan(
                    leaf_results=(
                        RetrieverCandidate(
                            rank=1,
                            item_id="leaf.item",
                            payload="worker.leaf",
                            branch_path=("root", "child", "leaf"),
                            label="Leaf",
                        ),
                    ),
                    child_cursors=(),
                ),
                "alt": ExpansionPlan(
                    leaf_results=(
                        RetrieverCandidate(
                            rank=1,
                            item_id="alt.item",
                            payload="worker.alt",
                            branch_path=("root", "alt", "leaf"),
                            label="AltLeaf",
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
            enable_parallel_branches=False,
        )

        result = engine.search(
            model="m",
            query_messages=[{"role": "user", "content": "need leaf"}],
            root_cursor=root_cursor,
            trace=RetrieverTrace(),
        )

        self.assertEqual([item.payload for item in result], ["worker.leaf", "worker.alt"])
        self.assertEqual(renderer.calls[0], "root")
        self.assertEqual(selector.calls, ["root"])
        self.assertEqual(expander.calls, ["root", "child", "alt"])


if __name__ == "__main__":
    unittest.main()
