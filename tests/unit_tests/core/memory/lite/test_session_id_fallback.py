# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the session_id contextvar fallback in memory_tool_ops.

Background: ``MemberMemoryToolkit.initialize()`` eagerly evaluates
``_current_session_id()`` and stores the result into ``ctx.session_id`` at
toolkit init time (``agent_teams/memory/member_memory_toolkit.py:119, 130``).
At that point the ``ContextEngineeringRail`` has not yet run ``before_invoke``
to bind the ``agent_teams`` session_id contextvar, so ``ctx.session_id``
freezes to None. The 4 ``daily_memory`` tool entry points in
``core/memory/lite/memory_tool_ops.py`` previously read ``ctx.session_id``
directly, which silently routed writes/reads to the legacy shared
``daily_memory/<basename>`` path — leaking content across sibling sessions
sharing one workspace.

Fix (Plan A): the 4 call sites now read
``session_id=(ctx.session_id if ctx else None) or current_session_id()``
so a None ctx field falls back to the (now-bound) contextvar. These tests
pin that fallback contract.
"""

import os
import sys
import tempfile
import shutil
import types
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock

import pytest

# The agent_teams.context module is light, but importing it through the package
# triggers agent_teams/__init__.py → ... → dashscope_model_client +
# dashscope_embedding, which need the ``dashscope`` distribution and several
# submodules (``dashscope.api_entities`` etc.). These tests never instantiate
# any of those clients; a meta_path finder that stubs the whole ``dashscope``
# package tree on demand lets the import chain resolve so we can exercise the
# real ``current_session_id()`` / ``validate_memory_path()`` code path.


class _DashscopeStubLoader:
    def create_module(self, spec):
        return None

    def exec_module(self, module):
        pass


class _DashscopeStubFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "dashscope" and not fullname.startswith("dashscope."):
            return None
        mod = MagicMock(name=fullname)
        mod.__path__ = []  # package marker so submodule imports resolve
        mod.__name__ = fullname
        sys.modules[fullname] = mod
        from importlib.machinery import ModuleSpec
        return ModuleSpec(fullname, _DashscopeStubLoader(), is_package=True)


if not any(isinstance(f, _DashscopeStubFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _DashscopeStubFinder())


@pytest.fixture
def isolate_session_id():
    """Reset the agent_teams session_id contextvar around each test."""
    from openjiuwen.agent_teams.context import (
        get_session_id, reset_session_id, set_session_id,
    )
    token = set_session_id("")
    yield get_session_id
    reset_session_id(token)


@pytest.fixture
def temp_workspace():
    """A minimal fake workspace exposing get_node_path / get_directory.

    Mirrors the surface ``validate_memory_path`` calls: ``get_node_path("memory")``
    returns a temp directory, ``get_directory("daily_memory")`` returns the
    relative segment. Using a fake keeps these tests off dashscope / Runner.
    """
    dir_path = tempfile.mkdtemp()
    ws = SimpleNamespace(_memory_dir=dir_path, _daily_rel="daily_memory")
    ws.get_node_path = lambda name: ws._memory_dir if name == "memory" else None
    ws.get_directory = lambda name: ws._daily_rel if name == "daily_memory" else None
    try:
        yield ws
    finally:
        shutil.rmtree(dir_path, ignore_errors=True)


def _call_site_session_id(ctx_session_id: Optional[str]) -> Optional[str]:
    """Replicates the memory_tool_ops.py call-site expression:

        session_id=(ctx.session_id if ctx else None) or current_session_id()
    """
    from openjiuwen.core.memory.lite.internal import current_session_id
    return (ctx_session_id or None) or current_session_id()


# ---------------------------------------------------------------------------
# current_session_id() function behavior
# ---------------------------------------------------------------------------

def test_current_session_id_returns_none_when_unbound(isolate_session_id):
    """When the contextvar is empty, current_session_id() returns None."""
    from openjiuwen.core.memory.lite.internal import current_session_id
    # The fixture sets "" which current_session_id normalizes to None.
    assert current_session_id() is None


def test_current_session_id_returns_value_when_bound(isolate_session_id):
    """When the contextvar is bound, current_session_id() returns its value."""
    from openjiuwen.agent_teams.context import set_session_id, reset_session_id
    from openjiuwen.core.memory.lite.internal import current_session_id
    token = set_session_id("sess-fn-direct")
    try:
        assert current_session_id() == "sess-fn-direct"
    finally:
        reset_session_id(token)


# ---------------------------------------------------------------------------
# validate_memory_path with the call-site fallback expression
# ---------------------------------------------------------------------------

def test_fallback_recovers_contextvar_when_ctx_session_id_is_none(
        isolate_session_id):
    """The bug scenario: ctx.session_id frozen to None by eager init, but
    the contextvar is now bound. The call-site expression must recover the
    contextvar value so per-session isolation still works.
    """
    from openjiuwen.agent_teams.context import set_session_id, reset_session_id
    token = set_session_id("sess-recovered-via-contextvar")
    try:
        sid = _call_site_session_id(ctx_session_id=None)
        assert sid == "sess-recovered-via-contextvar"
    finally:
        reset_session_id(token)


def test_fallback_prefers_ctx_session_id_when_set(isolate_session_id):
    """When ctx.session_id is explicitly set, it overrides the contextvar
    (explicit override semantics — preserves any caller that injects a sid).
    """
    from openjiuwen.agent_teams.context import set_session_id, reset_session_id
    token = set_session_id("contextvar-sid")
    try:
        sid = _call_site_session_id(ctx_session_id="explicit-ctx-sid")
        assert sid == "explicit-ctx-sid"
    finally:
        reset_session_id(token)


def test_fallback_returns_none_when_both_empty(isolate_session_id):
    """When both ctx.session_id and the contextvar are empty, the expression
    returns None so the legacy shared path is used (backward compatible).
    """
    sid = _call_site_session_id(ctx_session_id=None)
    assert sid is None


def test_validate_memory_path_uses_contextvar_when_ctx_session_id_is_none(
        temp_workspace, isolate_session_id):
    """End-to-end: ctx.session_id=None + bound contextvar → resolved path
    contains the per-session subdirectory. This is the regression guard for
    the bug fixed by Plan A.
    """
    from openjiuwen.agent_teams.context import set_session_id, reset_session_id
    from openjiuwen.core.memory.lite.memory_tool_ops import validate_memory_path

    token = set_session_id("sess-recovered-via-contextvar")
    try:
        sid = _call_site_session_id(ctx_session_id=None)
        is_valid, resolved = validate_memory_path(
            "2026-08-06.md", temp_workspace, session_id=sid)
        assert is_valid
        assert resolved.endswith(os.path.join(
            "daily_memory", "sess-recovered-via-contextvar", "2026-08-06.md"))
    finally:
        reset_session_id(token)


def test_validate_memory_path_uses_ctx_session_id_when_set(
        temp_workspace, isolate_session_id):
    """End-to-end: explicit ctx.session_id wins; contextvar is ignored."""
    from openjiuwen.agent_teams.context import set_session_id, reset_session_id
    from openjiuwen.core.memory.lite.memory_tool_ops import validate_memory_path

    token = set_session_id("contextvar-sid")
    try:
        sid = _call_site_session_id(ctx_session_id="explicit-ctx-sid")
        is_valid, resolved = validate_memory_path(
            "2026-08-06.md", temp_workspace, session_id=sid)
        assert is_valid
        assert "explicit-ctx-sid" in resolved
        assert "contextvar-sid" not in resolved
    finally:
        reset_session_id(token)


def test_validate_memory_path_legacy_when_no_session(
        temp_workspace, isolate_session_id):
    """End-to-end: no session anywhere → legacy shared daily_memory path."""
    from openjiuwen.core.memory.lite.memory_tool_ops import validate_memory_path

    sid = _call_site_session_id(ctx_session_id=None)
    is_valid, resolved = validate_memory_path(
        "2026-08-06.md", temp_workspace, session_id=sid)
    assert is_valid
    # Legacy path: daily_memory/2026-08-06.md (no session subdirectory).
    assert resolved.endswith(os.path.join("daily_memory", "2026-08-06.md"))
    # And no stray session_id segment leaked in.
    assert "sess-" not in resolved
