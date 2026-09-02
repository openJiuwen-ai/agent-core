# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``with_session`` must not raise when ContextVar tokens cross Contexts."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.session import get_current_session, with_session


@pytest.mark.asyncio
async def test_with_session_async_gen_aclose_from_other_task() -> None:
    """ReAct streams spawn work in another task; aclose can run there."""

    @with_session()
    async def stream(session: object):
        yield "chunk"
        await asyncio.sleep(0)

    session = object()
    agen = stream(session)
    assert await agen.__anext__() == "chunk"

    async def close_elsewhere() -> None:
        await agen.aclose()

    await asyncio.create_task(close_elsewhere())
    assert get_current_session() in (None, session)


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
