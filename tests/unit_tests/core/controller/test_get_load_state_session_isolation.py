# coding: utf-8
"""Unit tests for TaskManager get_state / load_state session-scoped isolation.

get_state() and load_state() operate globally in the original implementation.
When Controller.stream() runs concurrently across sessions,
_save_task_manager_state() leaks other sessions' in-flight tasks into this
session's snapshot via get_state(), and _restore_task_manager_state()
destroys other sessions' in-memory tasks via load_state(). These tests
verify the session-scoped get_state(session_id=...) and
load_state(state, session_id=...) fix and their backward compatibility.
"""

import unittest

from openjiuwen.core.controller import Task, TaskManager, TaskManagerState


class TestGetLoadStateSessionIsolation(unittest.IsolatedAsyncioTestCase):
    """Tests for session-scoped get_state / load_state and backward compat."""

    def _mk(self, session_id, task_id, priority=1, parent_task_id=None):
        return Task(
            session_id=session_id,
            task_id=task_id,
            task_type="test_task",
            priority=priority,
            parent_task_id=parent_task_id,
        )

    def _snapshot(self, tasks, priority_index, parent_to_children,
                  children_to_parent, root_tasks):
        return TaskManagerState(
            tasks=tasks,
            priority_index=priority_index,
            parent_to_children=parent_to_children,
            children_to_parent=children_to_parent,
            root_tasks=root_tasks,
        )

    def _prio_consistent(self, tm: TaskManager) -> bool:
        """Task set and priority index are bidirectionally consistent,
        with no empty buckets."""
        reverse_ok = all(
            tid in tm.tasks
            for bucket in tm._priority_index.values()
            for tid in bucket
        )
        forward_ok = all(
            any(tid in bucket for bucket in tm._priority_index.values())
            for tid in tm.tasks
        )
        no_empty = all(bucket for bucket in tm._priority_index.values())
        return reverse_ok and forward_ok and no_empty

    # ---- get_state ----

    async def test_get_state_session_isolation(self):
        """get_state(session_id="B") exports only B's tasks, indexes derived
        from the filtered set, snapshot isolated from live memory."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task([
            self._mk("A", "a0", priority=1),
            self._mk("A", "a1", priority=2),
            self._mk("B", "b0", priority=2),
        ])
        st = await tm.get_state(session_id="B")
        self.assertEqual(set(st.tasks), {"b0"})
        self.assertEqual(st.tasks["b0"].session_id, "B")
        self.assertEqual(st.priority_index, {2: ["b0"]})
        self.assertEqual(st.root_tasks, {"b0"})
        self.assertEqual(st.parent_to_children, {})
        self.assertEqual(st.children_to_parent, {})
        st.tasks["b0"].priority = 9
        self.assertEqual(tm.tasks["b0"].priority, 2)
        self.assertEqual(set(tm.tasks), {"a0", "a1", "b0"})

    async def test_get_state_cuts_cross_session_parent(self):
        """A task whose parent belongs to another session is exported as
        a root."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task(self._mk("A", "p1", priority=1))
        await tm.add_task(self._mk("B", "c1", priority=2, parent_task_id="p1"))
        st_b = await tm.get_state(session_id="B")
        self.assertEqual(set(st_b.tasks), {"c1"})
        self.assertIsNone(st_b.tasks["c1"].parent_task_id)
        self.assertEqual(st_b.root_tasks, {"c1"})
        st_a = await tm.get_state(session_id="A")
        self.assertEqual(set(st_a.tasks), {"p1"})
        self.assertEqual(st_a.root_tasks, {"p1"})

    async def test_get_state_backward_compatible(self):
        """No-arg get_state() exports everything."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task([self._mk("A", "a0"), self._mk("B", "b0")])
        st = await tm.get_state()
        self.assertEqual(set(st.tasks), {"a0", "b0"})
        self.assertEqual(set(st.root_tasks), {"a0", "b0"})

    # ---- load_state ----

    async def test_load_state_session_isolation(self):
        """load_state(state, session_id="B") merges B's snapshot; A's
        in-flight tasks, hierarchy and indexes survive."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task(self._mk("A", "a_root", priority=1))
        await tm.add_task(self._mk("A", "a_child", priority=2,
                                   parent_task_id="a_root"))

        snapshot = self._snapshot(
            tasks={"b_new": self._mk("B", "b_new", priority=3)},
            priority_index={3: ["b_new"]},
            parent_to_children={},
            children_to_parent={},
            root_tasks={"b_new"},
        )
        await tm.load_state(snapshot, session_id="B")

        self.assertEqual(set(tm.tasks), {"a_root", "a_child", "b_new"})
        self.assertEqual(tm.tasks["b_new"].session_id, "B")
        self.assertEqual(tm._parent_to_children["a_root"], {"a_child"})
        self.assertEqual(tm._child_to_parent["a_child"], "a_root")
        self.assertIn("b_new", tm._root_tasks)
        self.assertTrue(self._prio_consistent(tm))

    async def test_load_state_replaces_same_session_tasks(self):
        """load_state replaces that session's in-memory tasks that are not
        in the snapshot (same-session replace semantics)."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task(self._mk("A", "a0", priority=1))
        await tm.add_task(self._mk("B", "b_old", priority=1))
        await tm.add_task(self._mk("B", "b_keep", priority=2))

        snapshot = self._snapshot(
            tasks={"b_new": self._mk("B", "b_new", priority=3)},
            priority_index={3: ["b_new"]},
            parent_to_children={},
            children_to_parent={},
            root_tasks={"b_new"},
        )
        await tm.load_state(snapshot, session_id="B")

        self.assertEqual(set(tm.tasks), {"a0", "b_new"})
        self.assertNotIn("b_old", tm._priority_index.get(1, []))
        self.assertTrue(self._prio_consistent(tm))

    async def test_load_state_skips_foreign_session_tasks(self):
        """Legacy full-state snapshots: only the target session's slice is
        merged."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task(self._mk("A", "a0", priority=1))

        snapshot = self._snapshot(
            tasks={
                "a_legacy": self._mk("A", "a_legacy", priority=1),
                "b_new": self._mk("B", "b_new", priority=2),
            },
            priority_index={1: ["a_legacy"], 2: ["b_new"]},
            parent_to_children={},
            children_to_parent={},
            root_tasks={"a_legacy", "b_new"},
        )
        await tm.load_state(snapshot, session_id="B")

        self.assertEqual(set(tm.tasks), {"a0", "b_new"})
        self.assertTrue(self._prio_consistent(tm))

    async def test_load_state_promotes_orphan_to_root(self):
        """A snapshot task whose parent is missing is promoted to a root."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task(self._mk("A", "a0", priority=1))

        snapshot = self._snapshot(
            tasks={"b_orphan": self._mk("B", "b_orphan", priority=2,
                                          parent_task_id="ghost")},
            priority_index={2: ["b_orphan"]},
            parent_to_children={"ghost": {"b_orphan"}},
            children_to_parent={"b_orphan": "ghost"},
            root_tasks=set(),
        )
        await tm.load_state(snapshot, session_id="B")

        self.assertIsNone(tm.tasks["b_orphan"].parent_task_id)
        self.assertIn("b_orphan", tm._root_tasks)
        self.assertNotIn("b_orphan", tm._child_to_parent)
        self.assertNotIn("ghost", tm._parent_to_children)
        self.assertTrue(self._prio_consistent(tm))

    async def test_load_state_cuts_cross_session_parent(self):
        """A snapshot task whose parent belongs to another session (and is
        still in memory) is promoted to a root instead of keeping the
        cross-session link."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task(self._mk("A", "a1", priority=1))

        snapshot = self._snapshot(
            tasks={
                "a1": self._mk("A", "a1", priority=1),
                "b1": self._mk("B", "b1", priority=2, parent_task_id="a1"),
            },
            priority_index={1: ["a1"], 2: ["b1"]},
            parent_to_children={"a1": {"b1"}},
            children_to_parent={"b1": "a1"},
            root_tasks={"a1"},
        )
        await tm.load_state(snapshot, session_id="B")

        self.assertIn("b1", tm.tasks)
        self.assertIsNone(tm.tasks["b1"].parent_task_id)
        self.assertIn("b1", tm._root_tasks)
        self.assertIn("a1", tm.tasks)
        self.assertEqual(tm.tasks["a1"].session_id, "A")
        self.assertTrue(self._prio_consistent(tm))

    async def test_load_state_backward_compatible(self):
        """No-arg load_state(state) replaces the whole state."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task(self._mk("A", "a_old", priority=1))
        snapshot = self._snapshot(
            tasks={"b0": self._mk("B", "b0", priority=2)},
            priority_index={2: ["b0"]},
            parent_to_children={},
            children_to_parent={},
            root_tasks={"b0"},
        )
        await tm.load_state(snapshot)
        self.assertEqual(set(tm.tasks), {"b0"})
        self.assertEqual(tm._priority_index[2], ["b0"])

    # ---- concurrent roundtrip ----

    async def test_concurrent_save_restore_roundtrip(self):
        """Interleaved save/restore/save across two sessions leaks nothing."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task(self._mk("A", "a1", priority=1))
        await tm.add_task(self._mk("B", "b1", priority=2))

        st_a = await tm.get_state(session_id="A")
        self.assertEqual(set(st_a.tasks), {"a1"})

        st_b = self._snapshot(
            tasks={"b0": self._mk("B", "b0", priority=3)},
            priority_index={3: ["b0"]},
            parent_to_children={},
            children_to_parent={},
            root_tasks={"b0"},
        )
        await tm.load_state(st_b, session_id="B")
        self.assertEqual(set(tm.tasks), {"a1", "b0"})
        self.assertTrue(self._prio_consistent(tm))

        st_a2 = await tm.get_state(session_id="A")
        self.assertEqual(set(st_a2.tasks), {"a1"})
        st_b2 = await tm.get_state(session_id="B")
        self.assertEqual(set(st_b2.tasks), {"b0"})


if __name__ == "__main__":
    unittest.main()
