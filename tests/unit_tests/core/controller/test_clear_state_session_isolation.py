# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TaskManager.clear_state session-scoped isolation (fixes #1520).

When Controller.stream() is called concurrently across multiple sessions,
_restore_task_manager_state() invokes clear_state(). The original clear_state()
takes no arguments and wipes all sessions' tasks — a race that breaks concurrent
pipelines. These tests verify the session-scoped clear_state(session_id=...)
fix and its backward compatibility.
"""

import unittest

from openjiuwen.core.controller import TaskManager, Task


class TestClearStateSessionIsolation(unittest.IsolatedAsyncioTestCase):
    """Tests for session-scoped clear_state and backward compatibility."""

    def _mk(self, session_id, task_id, priority=1, parent_task_id=None):
        return Task(
            session_id=session_id,
            task_id=task_id,
            task_type="test_task",
            priority=priority,
            parent_task_id=parent_task_id,
        )

    def _prio_consistent(self, tm: TaskManager) -> bool:
        """Task set and priority index are bidirectionally consistent, no empty buckets."""
        reverse_ok = all(
            tid in tm.tasks for bucket in tm._priority_index.values() for tid in bucket
        )
        forward_ok = all(
            any(tid in bucket for bucket in tm._priority_index.values())
            for tid in tm.tasks
        )
        no_empty = all(bucket for bucket in tm._priority_index.values())
        return reverse_ok and forward_ok and no_empty

    async def test_clear_state_session_isolation(self):
        """clear_state(session_id="B") removes only B; A's tasks and indexes intact."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task([
            self._mk("A", "a0"), self._mk("A", "a1"),
            self._mk("B", "b0"), self._mk("B", "b1"),
        ])
        await tm.clear_state(session_id="B")
        self.assertEqual(set(tm.tasks), {"a0", "a1"})
        self.assertTrue(all(t.session_id == "A" for t in tm.tasks.values()))
        self.assertTrue(self._prio_consistent(tm))

    async def test_clear_state_drops_empty_priority_bucket(self):
        """Removing the last task in a priority bucket deletes the empty bucket."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task([
            self._mk("A", "a0", priority=1),
            self._mk("B", "b0", priority=2),
        ])
        await tm.clear_state(session_id="A")
        self.assertNotIn(1, tm._priority_index)
        self.assertIn(2, tm._priority_index)
        self.assertIn("b0", tm._priority_index[2])

    async def test_clear_state_is_backward_compatible(self):
        """No-arg clear_state() preserves the "clear all" behavior."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task([self._mk("A", "a0"), self._mk("B", "b0")])
        await tm.clear_state()
        self.assertEqual(len(tm.tasks), 0)
        self.assertTrue(self._prio_consistent(tm))

    async def test_clear_state_promotes_survived_child_to_root(self):
        """Cross-session parent-child: deleting parent promotes survived child to root."""
        tm = TaskManager(config={"default_task_priority": 1})
        await tm.add_task(self._mk("A", "p1", priority=1))
        await tm.add_task(self._mk("B", "c1", priority=2, parent_task_id="p1"))
        await tm.clear_state(session_id="A")
        self.assertEqual(list(tm.tasks), ["c1"])
        self.assertIsNone(tm.tasks["c1"].parent_task_id)
        self.assertIn("c1", tm._root_tasks)
        self.assertNotIn("p1", tm._parent_to_children)
        self.assertNotIn("c1", tm._child_to_parent)
