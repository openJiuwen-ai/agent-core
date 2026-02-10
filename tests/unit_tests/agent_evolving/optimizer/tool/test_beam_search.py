# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.agent_evolving.optimizer.tool.utils.beam_search import BeamSearch, TreeNode


class DummyMethod:
    @staticmethod
    def step(tool, examples=None, prev_outputs=None, it=0):
        if it == 0:
            return {"it": 0}, "root", 1.0
        return {"it": it}, f"node-{it}", float(it + 1)


def test_tree_node_depth_and_repr():
    root = TreeNode(data="r", score=1.0, results={"x": 1})
    child = TreeNode(data="c", score=2.0, results={"x": 2}, history=root.history)
    child.parent = root
    root.children.append(child)

    assert root.get_depth() == 0
    assert child.get_depth() == 1
    assert 'it=0 score=1.0 data="r"' in repr(root)


def test_beam_search_search_and_prune():
    bs = BeamSearch(
        method=DummyMethod(),
        beam_width=1,
        expand_num=2,
        max_depth=2,
        num_workers=1,
        early_stop=False,
        check_valid=False,
        top_k=1,
    )
    result = bs.search({"name": "tool"})
    assert len(result) == 1
    assert result[0][0] == {"it": 0}
    assert result[0][-1] == {"it": 2}

    nodes = [TreeNode("a", 1, {}), TreeNode("b", 3, {}), TreeNode("c", 2, {})]
    pruned = bs.prune(nodes)
    assert [n.score for n in pruned] == [3]


def test_beam_search_timeout_and_early_stop():
    bs = BeamSearch(
        method=DummyMethod(),
        beam_width=1,
        expand_num=1,
        max_depth=3,
        num_workers=1,
        early_stop=True,
        check_valid=False,
        max_score=1.0,
        top_k=1,
    )
    bs.timeout = -1
    out = bs.search({"name": "tool"})
    assert len(out) == 1
    assert out[0][0] == {"it": 0}

    node = TreeNode("x", 2.0, {})
    assert bs.check_early_stop([node], max_score=1.0, k=1) is True
    assert bs.check_early_stop([], max_score=1.0, k=1) is False


def test_beam_search_invalid_root_and_expand_error():
    class InvalidMethod:
        @staticmethod
        def step(tool, examples=None, prev_outputs=None, it=0):
            return {"bad": True}, "x", -1.0

    bs = BeamSearch(
        method=InvalidMethod(),
        beam_width=1,
        expand_num=1,
        max_depth=1,
        num_workers=1,
        check_valid=True,
    )
    with pytest.raises(RuntimeError):
        bs.search({"name": "tool"})

    root = TreeNode("r", 1.0, {"ok": 1})
    with pytest.raises(RuntimeError):
        bs.expand([root], {"name": "tool"}, examples=None, depth=1)
