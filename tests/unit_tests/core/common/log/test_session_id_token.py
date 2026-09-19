# coding: utf-8
"""Tests for ``set_session_id`` / ``reset_session_id`` token lifecycle.

Covers the COM-02 logging context API: ``set_session_id`` returns a
``contextvars.Token`` and ``reset_session_id(token)`` restores the prior
value. Caller-contract violations propagate from the stdlib and are not
caught by the API.
"""

import contextvars

import pytest

from openjiuwen.core.common.logging import (
    get_session_id,
    reset_session_id,
    set_session_id,
)
from openjiuwen.core.common.logging.utils import (
    _trace_id_context,
)
from openjiuwen.core.common.logging.utils import (
    get_session_id as _get_session_id,
)
from openjiuwen.core.common.logging.utils import (
    reset_session_id as _reset_session_id,
)
from openjiuwen.core.common.logging.utils import (
    set_session_id as _set_session_id,
)


@pytest.fixture(autouse=True)
def _isolate_trace_id():
    """Force the trace_id contextvar to its default for each test, then restore."""
    token = _trace_id_context.set("default_trace_id")
    yield
    _trace_id_context.reset(token)


class TestSessionIdTokenLifecycle:
    def test_set_returns_token(self) -> None:
        token = set_session_id("trace-1")
        assert isinstance(token, contextvars.Token)
        assert get_session_id() == "trace-1"

    def test_reset_restores_previous_value(self) -> None:
        token = set_session_id("trace-2")
        reset_session_id(token)
        assert get_session_id() == "default_trace_id"

    def test_reset_restores_outer_value_when_nested_lifo(self) -> None:
        outer = set_session_id("outer")
        inner = set_session_id("inner")
        assert get_session_id() == "inner"
        reset_session_id(inner)
        assert get_session_id() == "outer"
        reset_session_id(outer)
        assert get_session_id() == "default_trace_id"

    def test_three_level_lifo(self) -> None:
        a = set_session_id("a")
        b = set_session_id("b")
        c = set_session_id("c")
        assert get_session_id() == "c"
        reset_session_id(c)
        assert get_session_id() == "b"
        reset_session_id(b)
        assert get_session_id() == "a"
        reset_session_id(a)
        assert get_session_id() == "default_trace_id"

    def test_reuse_token_raises_runtime_error(self) -> None:
        token = set_session_id("once")
        reset_session_id(token)
        with pytest.raises(RuntimeError):
            reset_session_id(token)

    def test_cross_contextvar_token_raises_value_error(self) -> None:
        other: contextvars.ContextVar[str] = contextvars.ContextVar("other_ctx_var")
        other_token = other.set("x")
        try:
            with pytest.raises(ValueError):
                reset_session_id(other_token)
        finally:
            other.reset(other_token)

    def test_old_caller_ignoring_return_value_still_sets(self) -> None:
        # Pre-token callers did ``set_session_id(x)`` and ignored the return.
        # The value must still be applied (back-compat for the None->Token change).
        set_session_id("legacy")
        assert get_session_id() == "legacy"
        # Such callers cannot precisely reset; overwrite to clear.
        set_session_id("default_trace_id")
        assert get_session_id() == "default_trace_id"

    def test_reset_in_different_context_isolates_correctly(self) -> None:
        # Tokens are context-scoped; a reset in a child context does not leak.
        outer = set_session_id("parent")

        def child() -> None:
            inner = set_session_id("child")
            reset_session_id(inner)
            # parent value visible in child after inner reset
            assert get_session_id() == "parent"

        ctx = contextvars.copy_context()
        ctx.run(child)
        assert get_session_id() == "parent"
        reset_session_id(outer)
        assert get_session_id() == "default_trace_id"

    def test_public_re_export_matches_utils(self) -> None:
        assert set_session_id is _set_session_id
        assert reset_session_id is _reset_session_id
        assert get_session_id is _get_session_id

    def test_default_export_is_reachable(self) -> None:
        from openjiuwen.core.common.logging.default import reset_session_id as default_reset

        assert default_reset is reset_session_id

    def test_logging_api_is_distinct_from_agent_teams(self) -> None:
        """D-04: the logging trace_id API is separate from agent_teams' session
        context — different packages, different contextvars, tokens not
        interchangeable. agent_teams has its own set_session_id / reset_session_id
        for team message isolation; it does not replace the logging API."""
        agent_teams_context = pytest.importorskip("openjiuwen.agent_teams.context")
        assert set_session_id is not agent_teams_context.set_session_id
        assert reset_session_id is not agent_teams_context.reset_session_id
