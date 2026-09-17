# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``with_session`` must not raise when ContextVar tokens cross Contexts."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.session import get_current_session, with_session


@pytest.mark.asyncio
async def test_with_session_async_gen_aclose_from_other_task() -> None:
    """Cross-Context aclose must not crash; producer Context may still leak."""

    @with_session()
    async def stream(session: object):
        yield "chunk"
        await asyncio.sleep(0)

    session = object()
    agen = stream(session)
    assert await agen.__anext__() == "chunk"
    # Token was minted in this Context; value stays until this Context ends.
    assert get_current_session() is session

    async def close_elsewhere() -> None:
        await agen.aclose()
        # Closer Context never held ``session``, so conditional fallback is a
        # no-op (must not invent a restore of ``previous`` here).
        assert get_current_session() is None

    await asyncio.create_task(close_elsewhere())
    # Producer Context leak is an inherent ContextVar limit across Contexts.
    assert get_current_session() is session


@pytest.mark.asyncio
async def test_aclose_from_other_task_does_not_clobber_closer_session() -> None:
    """Fallback must not overwrite a closer that already has another session."""
    stream_session = object()
    closer_session = object()

    @with_session()
    async def stream(session: object):
        yield "chunk"
        await asyncio.sleep(0)

    agen = stream(stream_session)
    assert await agen.__anext__() == "chunk"

    @with_session()
    async def close_under_other_session(session: object) -> None:
        assert get_current_session() is session
        await agen.aclose()
        assert get_current_session() is session

    # Run closer in a separate task so aclose cleanup uses another Context
    # (the real disconnect / create_task(stream_process) shape).
    await asyncio.create_task(close_under_other_session(closer_session))
    assert get_current_session() is stream_session


@pytest.mark.asyncio
async def test_with_session_async_coro_restores_previous() -> None:
    outer = object()
    inner = object()

    @with_session()
    async def outer_bound(session: object) -> object:
        assert get_current_session() is session

        @with_session()
        async def inner_bound(session: object) -> object:
            assert get_current_session() is session
            return session

        assert await inner_bound(inner) is inner
        assert get_current_session() is session
        return session

    assert get_current_session() is None
    assert await outer_bound(outer) is outer
    assert get_current_session() is None
