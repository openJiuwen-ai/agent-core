# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Callable reflection trait caching tests.

The Pregel execution path used to re-run ``inspect.signature`` and
``iscoroutinefunction`` on every task execution and every conditional
dispatch. Those results are constants of the callable — and for callable
*instances* (fresh per-compile Vertex shells, component objects) they are
constants of the **class**, since reflection resolves through
``type(instance).__call__``.

These tests pin the trait-cache contract:
1. instances map onto their class as the reflection key, so traits are
   shared across fresh shells of the same class (no per-compile misses);
2. bound methods map onto ``__func__``; plain callables map onto themselves;
   explicit ``__signature__`` callables stay instance-keyed;
3. routers resolve the traits once at build time and behave identically to
   the per-dispatch reflection they replaced.
"""

import asyncio
import inspect

from openjiuwen.core.graph.pregel.router import ConditionalRouter
from openjiuwen.core.graph.pregel.task import _callable_traits, _reflection_key


class _FakeVertex:
    """Stands in for fresh per-compile shells: distinct instances, one class."""

    def __call__(self, config=None, state=None):
        return None

    def method(self, state=None):
        return None


def _sync_takes_config(config=None):
    return None


async def _async_takes_state(state=None):
    return "async-target"


# ============================================================================
# _reflection_key
# ============================================================================


def test_instances_map_onto_their_class():
    """Two fresh shells of one class must share one reflection key."""
    assert _reflection_key(_FakeVertex()) is _FakeVertex
    assert _reflection_key(_FakeVertex()) is _reflection_key(_FakeVertex())


def test_classes_functions_and_builtins_map_onto_themselves():
    assert _reflection_key(_FakeVertex) is _FakeVertex
    assert _reflection_key(_sync_takes_config) is _sync_takes_config


def test_bound_methods_map_onto_their_underlying_function():
    instance = _FakeVertex()
    assert _reflection_key(instance.method) is _FakeVertex.method


def test_explicit_signature_callables_stay_instance_keyed():
    """``__signature__`` is per-instance data and must not collapse to the class."""

    class _Sig1:
        def __call__(self, config=None):
            return None

    class _Sig2:
        def __call__(self, config=None):
            return None

    one, two = _Sig1(), _Sig2()
    one.__signature__ = inspect.Signature()  # type: ignore[attr-defined]
    two.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters=[inspect.Parameter("state", inspect.Parameter.KEYWORD_ONLY)]
    )

    assert _reflection_key(one) is one
    assert _reflection_key(two) is two


# ============================================================================
# _callable_traits
# ============================================================================


def test_traits_are_shared_across_fresh_instances_of_one_class():
    """The fresh-shell drift fix: distinct instances hit one cached entry."""
    first = _callable_traits(_FakeVertex())
    second = _callable_traits(_FakeVertex())

    assert first is second
    assert first.has_config and first.has_state
    assert not first.is_coroutine


def test_traits_reflect_signature_and_coroutine_ness():
    sync_traits = _callable_traits(_sync_takes_config)
    assert sync_traits.has_config and not sync_traits.has_state
    assert not sync_traits.is_coroutine

    async_traits = _callable_traits(_async_takes_state)
    assert not async_traits.has_config and async_traits.has_state
    assert async_traits.is_coroutine


# ============================================================================
# ConditionalRouter build-time resolution
# ============================================================================


def test_router_passes_state_only_to_state_takers():
    router = ConditionalRouter(_async_takes_state)

    assert router._takes_state is True
    assert router._is_coroutine is True
    messages = asyncio.run(router.dispatch("src"))

    assert [m.target for m in messages] == ["async-target"]


def test_router_sync_selector_without_state():
    def selector():
        return "plain-target"

    router = ConditionalRouter(selector)

    assert router._takes_state is False
    assert router._is_coroutine is False
    messages = asyncio.run(router.dispatch("src"))

    assert [m.target for m in messages] == ["plain-target"]
