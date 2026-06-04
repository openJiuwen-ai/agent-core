# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.graph.pregel.base import BarrierMessage
from openjiuwen.core.graph.pregel.channels import BarrierChannel, ChannelManager


class TestBarrierChannelCNF:
    """Tests for BarrierChannel CNF (AND-of-OR) expression support."""

    def test_backward_compatible_all_and(self):
        """Single-element groups: ALL must complete (backward compatible)."""
        ch = BarrierChannel("end", expected_groups=[{"A"}, {"B"}, {"C"}])
        assert ch.is_ready() is False

        ch.accept(BarrierMessage(sender="A", target=ch.key))
        assert ch.is_ready() is False

        ch.accept(BarrierMessage(sender="B", target=ch.key))
        assert ch.is_ready() is False

        ch.accept(BarrierMessage(sender="C", target=ch.key))
        assert ch.is_ready() is True

    def test_or_group_any_one_suffices(self):
        """OR-group: any one sender completing is enough for that group."""
        ch = BarrierChannel("end", expected_groups=[{"LLM_1", "LLM_2"}])
        assert ch.is_ready() is False

        # Only LLM_1 completes (LLM_2 never executes)
        ch.accept(BarrierMessage(sender="LLM_1", target=ch.key))
        assert ch.is_ready() is True

    def test_cnf_mixed_or_and_and(self):
        """CNF expression: (LLM_1 OR LLM_2) AND LLM_3."""
        ch = BarrierChannel("end", expected_groups=[{"LLM_1", "LLM_2"}, {"LLM_3"}])
        assert ch.is_ready() is False

        # Only LLM_1 completes, LLM_3 still pending
        ch.accept(BarrierMessage(sender="LLM_1", target=ch.key))
        assert ch.is_ready() is False

        # LLM_3 also completes
        ch.accept(BarrierMessage(sender="LLM_3", target=ch.key))
        assert ch.is_ready() is True

    def test_cnf_or_group_alternative_path(self):
        """CNF: LLM_2 completes instead of LLM_1 (alternative branch path)."""
        ch = BarrierChannel("end", expected_groups=[{"LLM_1", "LLM_2"}, {"LLM_3"}])

        # LLM_2 completes (not LLM_1)
        ch.accept(BarrierMessage(sender="LLM_2", target=ch.key))
        assert ch.is_ready() is False

        ch.accept(BarrierMessage(sender="LLM_3", target=ch.key))
        assert ch.is_ready() is True

    def test_multiple_independent_branches(self):
        """Multiple independent branches: (A OR B) AND (C OR D)."""
        ch = BarrierChannel("end", expected_groups=[{"A", "B"}, {"C", "D"}])

        ch.accept(BarrierMessage(sender="A", target=ch.key))
        assert ch.is_ready() is False

        ch.accept(BarrierMessage(sender="D", target=ch.key))
        assert ch.is_ready() is True

    def test_empty_received_not_ready(self):
        """No messages received -> not ready."""
        ch = BarrierChannel("end", expected_groups=[{"A", "B"}])
        assert ch.is_ready() is False

    def test_all_senders_in_or_group_complete(self):
        """All senders in an OR-group complete -> still ready."""
        ch = BarrierChannel("end", expected_groups=[{"A", "B"}, {"C"}])

        ch.accept(BarrierMessage(sender="A", target=ch.key))
        ch.accept(BarrierMessage(sender="B", target=ch.key))
        assert ch.is_ready() is False

        ch.accept(BarrierMessage(sender="C", target=ch.key))
        assert ch.is_ready() is True

    def test_consume_resets_state(self):
        """After consume(), channel is no longer ready."""
        ch = BarrierChannel("end", expected_groups=[{"A", "B"}])

        ch.accept(BarrierMessage(sender="A", target=ch.key))
        assert ch.is_ready() is True

        ch.consume()
        assert ch.is_ready() is False
        assert len(ch.received) == 0

    def test_snapshot_restore_cnf(self):
        """Snapshot and restore work correctly with CNF groups."""
        ch = BarrierChannel("end", expected_groups=[{"A", "B"}, {"C"}])

        ch.accept(BarrierMessage(sender="A", target=ch.key))
        ch.accept(BarrierMessage(sender="C", target=ch.key))
        assert ch.is_ready() is True

        snap = ch.snapshot()
        assert set(snap) == {"A", "C"}

        ch.consume()
        assert ch.is_ready() is False

        ch.restore(snap)
        assert ch.is_ready() is True

    def test_router_key_format_single_groups(self):
        """Router key format with single-element groups (backward compatible)."""
        ch = BarrierChannel("end", expected_groups=[{"A"}, {"B"}, {"C"}])
        assert ch.key == "barrier:A&B&C->end"

    def test_router_key_format_or_groups(self):
        """Router key format with OR-groups."""
        ch = BarrierChannel("end", expected_groups=[{"A", "B"}, {"C"}])
        assert ch.key == "barrier:(A|B)&C->end"

    def test_router_key_format_multiple_or_groups(self):
        """Router key format with multiple OR-groups."""
        ch = BarrierChannel("end", expected_groups=[{"A", "B"}, {"C", "D"}])
        assert ch.key == "barrier:(A|B)&(C|D)->end"

    def test_channel_manager_with_cnf_barrier(self):
        """ChannelManager correctly handles CNF barrier in full lifecycle."""
        barrier = BarrierChannel("end", expected_groups=[{"LLM_1", "LLM_2"}])
        manager = ChannelManager([barrier])

        # Simulate branch: only LLM_1 executes
        manager.buffer_message(BarrierMessage(sender="LLM_1", target=barrier.key))
        manager.flush()

        assert barrier.is_ready() is True
        assert "end" in manager.get_ready_nodes()

    def test_checkpoint_recovery_deadlock_fix(self):
        """Simulate checkpoint recovery: old checkpoint + new CNF code auto-fixes deadlock.

        Old code would deadlock: received = {"LLM_1", "LLM_3"}, expected = {"LLM_1", "LLM_2", "LLM_3"}
        New code: expected_groups = [{"LLM_3"}, {"LLM_1", "LLM_2"}] -> is_ready() = True
        """
        ch = BarrierChannel("end", expected_groups=[{"LLM_3"}, {"LLM_1", "LLM_2"}])

        # Restore from old checkpoint
        ch.restore(["LLM_1", "LLM_3"])

        # LLM_2 was never going to arrive, but:
        # group0 = {"LLM_3"} ∩ {"LLM_1", "LLM_3"} = {"LLM_3"} ≠ ∅ ✓
        # group1 = {"LLM_1", "LLM_2"} ∩ {"LLM_1", "LLM_3"} = {"LLM_1"} ≠ ∅ ✓
        assert ch.is_ready() is True


class TestPregelGraphResolveBarrierGroups:
    """Tests for the _resolve_barrier_groups logic using a lightweight mock."""

    @staticmethod
    def _forward_reachable(edges, start_node: str) -> set[str]:
        """BFS forward search (mirrors PregelGraph._forward_reachable)."""
        visited = set()
        queue = [start_node]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            for (src, tgt) in edges:
                if src == node and isinstance(tgt, str) and tgt not in visited:
                    queue.append(tgt)
        return visited

    @staticmethod
    def _resolve_barrier_groups(edges, branch_targets, target_id, source_list):
        """Mirrors PregelGraph._resolve_barrier_groups logic for isolated testing."""
        if not branch_targets or not source_list:
            return source_list

        all_predecessors = set()
        for g in source_list:
            all_predecessors |= g

        reachable = {}
        for branch_id, targets in branch_targets.items():
            for target in targets:
                reachable[(branch_id, target)] = TestPregelGraphResolveBarrierGroups._forward_reachable(
                    edges, target
                )

        pred_info = {}
        for p in all_predecessors:
            pred_info[p] = set()
            for (bid, tgt), nodes in reachable.items():
                if p in nodes:
                    pred_info[p].add((bid, tgt))

        from collections import defaultdict as _defaultdict
        branch_groups = _defaultdict(set)
        standalone = []

        for p in all_predecessors:
            branches = pred_info[p]
            if len(branches) == 1:
                bid = next(iter(branches))[0]
                branch_groups[bid].add(p)
            else:
                standalone.append({p})

        result = []
        for group in branch_groups.values():
            result.append(group)
        for s in standalone:
            result.append(s)

        return result if result else source_list

    def test_no_branch_targets_passthrough(self):
        """No branch_targets -> source_list returned unchanged."""
        result = self._resolve_barrier_groups([], {}, "end", [{"A"}, {"B"}, {"C"}])
        assert result == [{"A"}, {"B"}, {"C"}]

    def test_branch_targets_merge_or_group(self):
        """Two predecessors from same branch -> merged into OR-group."""
        edges = [
            ("branch_1", "LLM_1"),
            ("branch_1", "LLM_2"),
            ("LLM_1", "end"),
            ("LLM_2", "end"),
        ]
        branch_targets = {"branch_1": {"LLM_1", "LLM_2"}}
        result = self._resolve_barrier_groups(edges, branch_targets, "end", [{"LLM_1"}, {"LLM_2"}])
        assert len(result) == 1
        assert result[0] == {"LLM_1", "LLM_2"}

    def test_branch_with_standalone_node(self):
        """Branch nodes + standalone node -> OR-group + standalone group."""
        edges = [
            ("branch_1", "LLM_1"),
            ("branch_1", "LLM_2"),
            ("LLM_1", "end"),
            ("LLM_2", "end"),
            ("LLM_3", "end"),
        ]
        branch_targets = {"branch_1": {"LLM_1", "LLM_2"}}
        result = self._resolve_barrier_groups(edges, branch_targets, "end",
                                              [{"LLM_1"}, {"LLM_2"}, {"LLM_3"}])
        assert len(result) == 2

        or_groups = [g for g in result if len(g) > 1]
        standalone_groups = [g for g in result if len(g) == 1]

        assert len(or_groups) == 1
        assert or_groups[0] == {"LLM_1", "LLM_2"}
        assert len(standalone_groups) == 1
        assert standalone_groups[0] == {"LLM_3"}

    def test_multiple_independent_branches(self):
        """Two independent branches converging -> two OR-groups."""
        edges = [
            ("branch_1", "A"),
            ("branch_1", "B"),
            ("branch_2", "C"),
            ("branch_2", "D"),
            ("A", "end"),
            ("B", "end"),
            ("C", "end"),
            ("D", "end"),
        ]
        branch_targets = {
            "branch_1": {"A", "B"},
            "branch_2": {"C", "D"},
        }
        result = self._resolve_barrier_groups(edges, branch_targets, "end",
                                              [{"A"}, {"B"}, {"C"}, {"D"}])
        assert len(result) == 2

        groups_sorted = sorted(result, key=lambda x: sorted(x))
        assert groups_sorted[0] == {"A", "B"}
        assert groups_sorted[1] == {"C", "D"}

    def test_partial_branch_targets_to_end(self):
        """Only some branch targets connect to End -> others ignored."""
        edges = [
            ("branch", "A"),
            ("branch", "B"),
            ("branch", "C"),
            ("A", "end"),
            ("B", "end"),
            ("C", "X"),
        ]
        branch_targets = {"branch": {"A", "B", "C"}}
        result = self._resolve_barrier_groups(edges, branch_targets, "end", [{"A"}, {"B"}])
        assert len(result) == 1
        assert result[0] == {"A", "B"}

    def test_empty_source_list(self):
        """Empty source_list -> returned unchanged."""
        result = self._resolve_barrier_groups([], {"b": {"A", "B"}}, "end", [])
        assert result == []

    def test_forward_reachable(self):
        """Test BFS forward reachability."""
        edges = [
            ("start", "A"),
            ("A", "B"),
            ("B", "end"),
            ("start", "C"),
        ]
        assert self._forward_reachable(edges, "A") == {"A", "B", "end"}
        assert self._forward_reachable(edges, "C") == {"C"}
        assert self._forward_reachable(edges, "start") == {"start", "A", "B", "C", "end"}


class TestBranchRouterAllTargets:
    """Tests for BranchRouter.all_targets property."""

    def test_all_targets_single_targets(self):
        from openjiuwen.core.workflow.components.flow.branch_router import BranchRouter

        router = BranchRouter()
        router.add_branch("True", "LLM_1", branch_id="default")
        router.add_branch("False", "LLM_2", branch_id="if")

        targets = router.all_targets
        assert targets == {"LLM_1", "LLM_2"}

    def test_all_targets_multi_targets(self):
        from openjiuwen.core.workflow.components.flow.branch_router import BranchRouter

        router = BranchRouter()
        router.add_branch("True", ["A", "B"], branch_id="path1")
        router.add_branch("False", ["C"], branch_id="path2")

        targets = router.all_targets
        assert targets == {"A", "B", "C"}

    def test_all_targets_single_branch(self):
        """Single branch with one target -> len == 1 -> not registered as branch_targets."""
        from openjiuwen.core.workflow.components.flow.branch_router import BranchRouter

        router = BranchRouter()
        router.add_branch("True", "A", branch_id="only")

        targets = router.all_targets
        assert targets == {"A"}
