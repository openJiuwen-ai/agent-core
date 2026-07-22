# coding: utf-8
"""Unit tests for recent_tool_results session-state storage layer."""
import unittest

from openjiuwen.core.session.recent_tool_results import (
    get_recent_results,
    record_tool_result,
    clear_recent_results,
    _RECENT_RESULTS_STATE_KEY,
    _WINDOW_SIZE,
    _ENTRY_REQUIRED_FIELDS,
)


class MockSession:
    """Minimal session stub with get_state / update_state."""

    def __init__(self):
        self._state: dict = {}

    def get_state(self, key=None):
        if key is None:
            return dict(self._state)
        return self._state.get(key)

    def update_state(self, data: dict):
        self._state.update(data)


class TestGetRecentResults(unittest.TestCase):
    def test_none_session_returns_empty(self):
        self.assertEqual(get_recent_results(None), [])

    def test_empty_state_returns_empty(self):
        sess = MockSession()
        self.assertEqual(get_recent_results(sess), [])

    def test_returns_stored_list(self):
        sess = MockSession()
        sess.update_state({_RECENT_RESULTS_STATE_KEY: [{"tool": "search"}]})
        result = get_recent_results(sess)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool"], "search")

    def test_non_list_state_returns_empty(self):
        sess = MockSession()
        sess.update_state({_RECENT_RESULTS_STATE_KEY: "not a list"})
        self.assertEqual(get_recent_results(sess), [])


class TestRecordToolResult(unittest.TestCase):
    def test_none_session_noop(self):
        record_tool_result(None, {"tool": "search"})
        # no exception

    def test_empty_entry_noop(self):
        sess = MockSession()
        record_tool_result(sess, {})
        self.assertEqual(get_recent_results(sess), [])

    def test_single_record(self):
        sess = MockSession()
        entry = {"tool": "search", "status": "success"}
        record_tool_result(sess, entry)
        result = get_recent_results(sess)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool"], "search")

    def test_fifo_eviction_at_window_size(self):
        sess = MockSession()
        for i in range(_WINDOW_SIZE + 2):
            record_tool_result(sess, {"tool": f"tool_{i}", "status": "success"})
        result = get_recent_results(sess)
        self.assertEqual(len(result), _WINDOW_SIZE)
        self.assertEqual(result[0]["tool"], "tool_2")
        self.assertEqual(result[-1]["tool"], f"tool_{_WINDOW_SIZE + 1}")

    def test_exact_window_size_kept(self):
        sess = MockSession()
        for i in range(_WINDOW_SIZE):
            record_tool_result(sess, {"tool": f"tool_{i}", "status": "success"})
        result = get_recent_results(sess)
        self.assertEqual(len(result), _WINDOW_SIZE)
        self.assertEqual(result[0]["tool"], "tool_0")
        self.assertEqual(result[-1]["tool"], f"tool_{_WINDOW_SIZE - 1}")


class TestClearRecentResults(unittest.TestCase):
    def test_none_session_noop(self):
        clear_recent_results(None)

    def test_clears_existing(self):
        sess = MockSession()
        record_tool_result(sess, {"tool": "search", "status": "success"})
        self.assertEqual(len(get_recent_results(sess)), 1)
        clear_recent_results(sess)
        self.assertEqual(get_recent_results(sess), [])

    def test_clear_when_empty(self):
        sess = MockSession()
        clear_recent_results(sess)
        self.assertEqual(get_recent_results(sess), [])


class TestWindowIndependentSessions(unittest.TestCase):
    def test_sessions_isolated(self):
        parent = MockSession()
        child = MockSession()
        record_tool_result(parent, {"tool": "search", "status": "success"})
        self.assertEqual(len(get_recent_results(parent)), 1)
        self.assertEqual(len(get_recent_results(child)), 0)


class TestEntrySchemaValidation(unittest.TestCase):
    """Covers #4: record_tool_result does not reject, but logs, malformed entries."""

    def test_non_dict_entry_is_skipped(self):
        sess = MockSession()
        # A list is non-falsy but not a dict; should be skipped without raising.
        record_tool_result(sess, ["tool", "bash"])  # type: ignore[arg-type]
        self.assertEqual(get_recent_results(sess), [])

    def test_entry_missing_required_fields_is_stored(self):
        sess = MockSession()
        # Missing `tool`/`status`/`timestamp` — entry still stored; caller owns schema.
        entry = {"args": {"command": "ls"}}
        record_tool_result(sess, entry)
        results = get_recent_results(sess)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], entry)

    def test_full_entry_is_stored(self):
        sess = MockSession()
        entry = {
            "tool": "bash",
            "args": {"command": "ls"},
            "result": "file1\nfile2",
            "status": "success",
            "error": None,
            "timestamp": "2026-07-21T14:50:00+08:00",
        }
        record_tool_result(sess, entry)
        results = get_recent_results(sess)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["tool"], "bash")

    def test_required_fields_constant_matches_docstring(self):
        # Guards against accidentally narrowing the documented schema.
        self.assertEqual(set(_ENTRY_REQUIRED_FIELDS), {"tool", "status", "timestamp"})


class TestRealStateDeepCopy(unittest.TestCase):
    """Integration test: real StateCollection + InMemoryStateLike (deepcopy semantics).

    MockSession returns a live reference from get_state, so it cannot verify the
    production code path where state access is backed by ``deepcopy`` (see
    ``openjiuwen/core/session/state/base.py:113``).  These tests construct a
    minimal session-like wrapper around a real ``StateCollection`` to confirm
    that:

    - ``get_recent_results`` returns a deep copy (in-place mutations on the
      returned list/dict do NOT write back to session state);
    - ``record_tool_result`` persists through the real ``update_dict`` write
      path (which deepcopies the inbound data);
    - FIFO eviction still works through the real state layer.
    """

    @staticmethod
    def _make_real_session():
        """Build a minimal session backed by a real StateCollection.

        Mirrors ``StateSession.get_state`` / ``StateSession.update_state`` in
        ``openjiuwen/core/session/internal/wrapper.py``: get_state → state().get(key),
        update_state → state().update(data).  StateCollection uses
        ``InMemoryStateLike`` underneath, which deepcopies on both read and write.
        """
        from openjiuwen.core.session.state.agent_state import StateCollection

        state = StateCollection()

        class RealSessionLike:
            def get_state(self, key=None):
                return state.get(key)

            def update_state(self, data: dict):
                state.update(data)

        return RealSessionLike()

    def test_get_recent_results_returns_deepcopy_list(self):
        sess = self._make_real_session()
        record_tool_result(sess, {"tool": "bash", "status": "success"})
        results = get_recent_results(sess)
        self.assertEqual(len(results), 1)

        # In-place mutation on the returned list must NOT write back to state.
        results.append({"tool": "tampered", "status": "success"})
        results_after = get_recent_results(sess)
        self.assertEqual(
            len(results_after), 1,
            "in-place append on get_recent_results return value must not "
            "persist to session state (deepcopy contract)",
        )
        self.assertEqual(results_after[0]["tool"], "bash")

    def test_get_recent_results_returns_deepcopy_entry(self):
        sess = self._make_real_session()
        record_tool_result(sess, {"tool": "bash", "status": "success"})
        results = get_recent_results(sess)
        self.assertEqual(len(results), 1)

        # In-place mutation on the returned entry must NOT write back to state.
        results[0]["tool"] = "tampered"
        results_after = get_recent_results(sess)
        self.assertEqual(
            results_after[0]["tool"], "bash",
            "in-place mutation on returned entry dict must not persist (deepcopy contract)",
        )

    def test_record_tool_result_persists_through_real_state(self):
        sess = self._make_real_session()
        entry = {"tool": "bash", "status": "success", "result": "ok"}
        record_tool_result(sess, entry)

        # Read back through the real state layer (deepcopy path).
        results = get_recent_results(sess)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["tool"], "bash")
        self.assertEqual(results[0]["result"], "ok")

    def test_fifo_eviction_through_real_state(self):
        sess = self._make_real_session()
        for i in range(_WINDOW_SIZE + 2):
            record_tool_result(sess, {
                "tool": f"bash_{i}",
                "status": "success",
                "timestamp": f"2026-07-21T14:50:0{i}+08:00",
            })
        results = get_recent_results(sess)
        self.assertEqual(len(results), _WINDOW_SIZE)
        self.assertEqual(results[0]["tool"], "bash_2")
        self.assertEqual(results[-1]["tool"], f"bash_{_WINDOW_SIZE + 1}")

    def test_clear_through_real_state(self):
        sess = self._make_real_session()
        record_tool_result(sess, {"tool": "bash", "status": "success"})
        self.assertEqual(len(get_recent_results(sess)), 1)
        clear_recent_results(sess)
        self.assertEqual(get_recent_results(sess), [])

    def test_empty_state_returns_empty_list(self):
        sess = self._make_real_session()
        # StateCollection.get returns None for an unknown key → fall through to [].
        self.assertEqual(get_recent_results(sess), [])


if __name__ == "__main__":
    unittest.main()
