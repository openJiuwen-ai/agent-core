# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Transport-layer reconnect decorators for MCP clients.

SSE 的 mcp SDK 后台 ``post_writer`` 协程在
TCP 连接被重置时自爆并 ``write_stream.aclose()``，而 client 侧
``call_tool`` / ``list_tools`` 撞上已关闭的流即报错且不自愈，导致会话
半死、上层退化为只发 keepalive。

此处提供 ``@with_reconnect`` 装饰器，对 ``call_tool`` / ``list_tools`` /
``get_tool_info`` / ``list_resources`` / ``read_resource`` 这类用户调用方法
做横切重连：撞可重试传输层错误时，自动 ``disconnect + connect`` 后重试一次。
装饰器自包含（用 ``getattr`` 懒初始化每实例重连锁，不改基类）。
"""
from __future__ import annotations

import asyncio
import functools
from typing import Any

from openjiuwen.core.common.logging import logger

# 可重试的传输层错误 markers（类名或消息文本命中其一即重试）。
_RETRYABLE_MARKERS: tuple[str, ...] = (
    "session terminated",
    "closedresourceerror",
    "brokenresourceerror",
    "endofstream",
    "stream closed",
    "connection closed",
    "remoteprotocolerror",
    "readerror",
    "writeerror",
    "not connected",
    "broken pipe",
)


def is_retryable_transport_error(error: Exception) -> bool:
    """Return True if ``error`` looks like a retryable transport-layer failure."""
    name = error.__class__.__name__.lower()
    text = str(error).lower()
    return any(m in name or m in text for m in _RETRYABLE_MARKERS)


def _get_reconnect_lock(client: Any) -> asyncio.Lock:
    """Per-instance reconnect lock, lazily initialized.

    ``McpClient`` has no ``_reconnect_lock`` field, so direct attribute
    assignment would be an [attr-defined] error in strict typing;
    ``getattr`` + ``setattr`` keeps it dynamic and lint-clean while still
    giving each client instance its own lock (concurrent ``call_tool`` on
    the same instance won't trigger duplicate reconnects).
    """
    lock = getattr(client, "_reconnect_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(client, "_reconnect_lock", lock)
    return lock


async def reconnect(client: Any, *, timeout: float = -1) -> bool:
    """Tear down and re-establish the transport, serialized per client instance.

    If ``client`` exposes its own ``reconnect()`` method (e.g. ``SseClient``
    with an owner-task queue) we delegate to it so the transport-specific
    concurrency guards are honoured.  Otherwise we fall back to the generic
    ``disconnect + connect`` sequence guarded by ``_reconnect_lock``.
    """
    # Prefer a transport-native reconnect (may have owner-task / event
    # serialization) over the generic disconnect+connect fallback.
    native = getattr(client, "reconnect", None)
    if native is not None and asyncio.iscoroutinefunction(native):
        try:
            return await native(timeout=timeout)
        except Exception as exc:
            logger.warning(
                "[mcp-reconnect] %s.native_reconnect failed: %r; falling back to disconnect+connect",
                type(client).__name__, exc,
            )

    async with _get_reconnect_lock(client):
        try:
            await client.disconnect(timeout=timeout)
            logger.info(
                "[mcp-reconnect] %s disconnected to %s",
                type(client).__name__, getattr(client, "_server_path", "?"),
            )
        except Exception as exc:
            logger.warning(
                "[mcp-reconnect] %s disconnect before reconnect failed: %r",
                type(client).__name__, exc,
            )
        connected = await client.connect(timeout=timeout)
        if connected:
            logger.info(
                "[mcp-reconnect] %s reconnected to %s",
                type(client).__name__, getattr(client, "_server_path", "?"),
            )
        return connected


def with_reconnect(method):
    """Decorator: on a retryable transport error, reconnect and retry once.

    Wraps ``call_tool`` / ``list_tools`` / ``get_tool_info`` /
    ``list_resources`` / ``read_resource``. Two attempts max
    (``for attempt in range(2)``); only the first failure triggers a
    reconnect, the second failure propagates. Non-transport errors (e.g.
    ``ValueError``) propagate immediately without reconnecting.

    The decorated method's timeout (passed as ``timeout`` kwarg) is forwarded
    to ``disconnect``/``connect`` so the reconnect itself respects the same
    deadline as the original call.
    """

    @functools.wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        for attempt in range(2):
            try:
                return await method(self, *args, **kwargs)
            except Exception as e:
                if attempt == 0 and is_retryable_transport_error(e):
                    logger.warning(
                        "[mcp-reconnect] %s.%s hit transport error, reconnecting: %r",
                        type(self).__name__, method.__name__, e,
                    )
                    if await reconnect(self, timeout=kwargs.get("timeout", -1)):
                        continue
                raise

    return wrapper


def mark_reconnect_applied(cls: type) -> None:
    """Mark that ``@with_reconnect`` is mounted on ``cls``.

    Lets external monkeypatches (e.g. downstream products' timeout patch that
    also used to inject reconnect) detect this and skip, avoiding duplicate
    reconnect layers.
    """
    setattr(cls, "_reconnect_decorator_applied", True)
