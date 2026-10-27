# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Callback argument narrowing tests.

``_narrow_call`` / ``_inject_session_if_needed`` trim trigger arguments to
what each registered callback actually accepts. Their parameter traits are
pre-resolved once per callable (``_get_narrow_spec``); these tests pin the
narrowing contract the framework relies on — including the easy-to-misport
boundaries (POSITIONAL_ONLY counts and its name stays accepted, ``**kwargs``
swallows everything and turns session injection on).
"""


import pytest

from openjiuwen.core.runner.callback.framework import (
    _get_narrow_spec,
    _inject_session_if_needed,
    _narrow_call,
)
from openjiuwen.core.session import _current_session


def _spec_of(func):
    return _get_narrow_spec(func)


# ============================================================================
# _get_narrow_spec
# ============================================================================


def test_spec_counts_positional_params_and_accepts_their_names():
    def callback(a, b=1, *, c):
        return None

    spec = _spec_of(callback)

    assert spec.positional_count == 2
    assert spec.accepted_kwargs == frozenset({"a", "b", "c"})
    assert not spec.has_var_positional and not spec.has_var_keyword
    assert not spec.takes_session


def test_spec_positional_only_name_stays_in_accepted_kwargs():
    """POSITIONAL_ONLY contributes to positional_count and its *name* stays in
    the accepted set (matching the historical set-based behaviour)."""

    def callback(a, /, b):
        return None

    spec = _spec_of(callback)

    assert spec.positional_count == 2
    assert spec.accepted_kwargs == frozenset({"a", "b"})


def test_spec_var_keyword_swallows_and_enables_session():
    def callback(**kwargs):
        return None

    spec = _spec_of(callback)

    assert spec.has_var_keyword
    assert spec.accepted_kwargs is None
    assert spec.takes_session


def test_spec_var_positional_keeps_all_args():
    def callback(*args, config=None):
        return None

    spec = _spec_of(callback)

    assert spec.has_var_positional
    assert spec.positional_count == 0


def test_spec_detects_explicit_session_param():
    def callback(session=None):
        return None

    assert _spec_of(callback).takes_session


def test_spec_is_cached_per_callable():
    def callback(a):
        return None

    assert _spec_of(callback) is _spec_of(callback)


def test_spec_none_for_unresolvable_callable():
    class _Unresolvable:
        def __call__(self):
            raise AssertionError("never invoked")

    unresolvable = _Unresolvable()
    unresolvable.__signature__ = property(lambda self: (_ for _ in ()).throw(ValueError("nope")))  # type: ignore[attr-defined]

    assert _spec_of(unresolvable) is None


# ============================================================================
# _narrow_call
# ============================================================================


def test_narrow_truncates_extra_positional_args():
    def callback(a, b):
        return None

    args, kwargs = _narrow_call(callback, (1, 2, 3), {})

    assert args == (1, 2)
    assert kwargs == {}


def test_narrow_keeps_all_args_for_var_positional():
    def callback(*args):
        return None

    args, _ = _narrow_call(callback, (1, 2, 3), {})

    assert args == (1, 2, 3)


def test_narrow_filters_undeclared_kwargs():
    def callback(a):
        return None

    _, kwargs = _narrow_call(callback, (), {"a": 1, "extra": 2})

    assert kwargs == {"a": 1}


def test_narrow_keeps_all_kwargs_for_var_keyword():
    def callback(**kwargs):
        return None

    _, kwargs = _narrow_call(callback, (), {"a": 1, "extra": 2})

    assert kwargs == {"a": 1, "extra": 2}


def test_narrow_passes_through_when_signature_unresolvable():
    class _Unresolvable:
        def __call__(self):
            raise AssertionError("never invoked")

    unresolvable = _Unresolvable()
    unresolvable.__signature__ = property(lambda self: (_ for _ in ()).throw(ValueError("nope")))  # type: ignore[attr-defined]

    args, kwargs = _narrow_call(unresolvable, (1, 2), {"x": 3})

    assert args == (1, 2)
    assert kwargs == {"x": 3}


# ============================================================================
# _inject_session_if_needed
# ============================================================================


@pytest.mark.asyncio
async def test_session_injected_for_explicit_session_param():
    def callback(session=None):
        return None

    token = _current_session.set("ctx-session")
    try:
        kwargs = {}
        _inject_session_if_needed(callback, (), kwargs)
        assert kwargs["session"] == "ctx-session"
    finally:
        _current_session.reset(token)


@pytest.mark.asyncio
async def test_session_injected_for_var_keyword():
    def callback(**kwargs):
        return None

    token = _current_session.set("ctx-session")
    try:
        kwargs = {"other": 1}
        _inject_session_if_needed(callback, (), kwargs)
        assert kwargs["session"] == "ctx-session"
        assert kwargs["other"] == 1
    finally:
        _current_session.reset(token)


def test_session_not_injected_without_session_param():
    def callback(a):
        return None

    kwargs = {"a": 1}
    _inject_session_if_needed(callback, (), kwargs)

    assert "session" not in kwargs


@pytest.mark.asyncio
async def test_session_not_overridden_when_already_provided():
    def callback(session=None):
        return None

    token = _current_session.set("ctx-session")
    try:
        kwargs = {"session": "explicit-session"}
        _inject_session_if_needed(callback, (), kwargs)
        assert kwargs["session"] == "explicit-session"
    finally:
        _current_session.reset(token)


# ============================================================================
# End-to-end narrowing through the framework trigger
# ============================================================================


@pytest.mark.asyncio
async def test_framework_trigger_delivers_and_injects(framework):
    """trigger delivers declared kwargs and injects the current session.

    Registered handlers are wrapped with a (*args, **kwargs) signature, so
    the framework-level narrowing contract is: kwargs pass through, session
    is injected, and the wrapper strips it again if the target has no
    session parameter. Positional truncation is pinned at the _narrow_call
    level above.
    """
    received = {}

    token = _current_session.set("ctx-session")
    try:

        @framework.on("narrow_event")
        async def handler(a, b, session=None):  # noqa: ARG001 - signature is the point
            received["a"] = a
            received["b"] = b
            received["session"] = session

        await framework.trigger("narrow_event", a=1, b=2)
    finally:
        _current_session.reset(token)

    assert received == {"a": 1, "b": 2, "session": "ctx-session"}


@pytest.mark.asyncio
async def test_framework_trigger_strips_session_from_sessionless_handler(framework):
    """The wrapper removes the injected session when the target has no session param."""
    received = {}

    token = _current_session.set("ctx-session")
    try:

        @framework.on("narrow_event")
        async def handler(a):  # noqa: ARG001 - signature is the point
            received["a"] = a

        await framework.trigger("narrow_event", a=1)
    finally:
        _current_session.reset(token)

    assert received == {"a": 1}
