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
import os
from typing import Any

from openjiuwen.core.common.logging import logger

# 可重试的传输层错误 markers。每条 marker 是 ``(pattern, skip_for_runtime_error)``
# 二元组：pattern 用类名/消息文本子串匹配（命中即重试）；第二项标记这条 marker
# 是否对 ``RuntimeError`` 不适用。pattern 在定义时不强制小写——加载时
# 统一 ``.lower()`` 兜底，匹配端也把异常类名/消息转小写，故整体大小写不敏感。
_RETRYABLE_MARKER_DEFS: tuple[tuple[str, bool], ...] = (
    ("session terminated", False),
    ("closedresourceerror", False),
    ("brokenresourceerror", False),
    ("endofstream", False),
    ("stream closed", False),
    ("connection closed", False),
    ("remoteprotocolerror", False),
    ("readerror", False),
    ("writeerror", False),
    ("not connected", True),   # 对 RuntimeError 跳过
    ("broken pipe", False),
)

# 加载期小写化兜底
_RETRYABLE_MARKERS: tuple[tuple[str, bool], ...] = tuple(
    (pattern.lower(), skip) for pattern, skip in _RETRYABLE_MARKER_DEFS
)

# 可重试的异常类型集合（优先于 markers 判定，更稳健）。
_RETRYABLE_TYPES: tuple[type, ...] = ()
try:
    # anyio 的高层资源关闭/损坏异常
    from anyio import ClosedResourceError as _AnyioClosedResourceError  # type: ignore
    from anyio import BrokenResourceError as _AnyioBrokenResourceError  # type: ignore

    _RETRYABLE_TYPES += (_AnyioClosedResourceError, _AnyioBrokenResourceError)
except Exception as _imp_exc:
    logger.debug("[mcp-reconnect] failed to import anyio retryable types: %r", _imp_exc)
try:
    from mcp.shared.exceptions import McpError as _McpError  # type: ignore

    _RETRYABLE_TYPES += (_McpError,)
except Exception as _imp_exc:
    logger.debug("[mcp-reconnect] failed to import MCP retryable type McpError: %r", _imp_exc)


def is_retryable_transport_error(error: Exception) -> bool:
    """Return True if ``error`` looks like a retryable transport-layer failure.

    优先按异常类型判断（更稳健），再退回到字符串 markers 匹配（跨版本兼容）。
    匹配大小写不敏感：异常类名/消息转小写后，与每条 marker 的 pattern（亦
    已小写化）做子串包含。每条 marker 自带"是否对 RuntimeError 不适用"标记，
    避免我们自己的未连接守卫被误判为可重试传输错误。
    """
    try:
        if _RETRYABLE_TYPES and isinstance(error, _RETRYABLE_TYPES):
            return True
    except Exception as type_exc:
        logger.debug("[mcp-reconnect] type check in is_retryable_transport_error failed: %r", type_exc)
    is_runtime_error = isinstance(error, RuntimeError)
    name = error.__class__.__name__.lower()
    text = str(error).lower()
    return any(
        not (skip and is_runtime_error) and (pattern in name or pattern in text)
        for pattern, skip in _RETRYABLE_MARKERS
    )


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


def _get_attempts() -> int:
    """Resolve total attempts (first try + retries) for @with_reconnect.
    """
    env_val = os.getenv("OPENJIUWEN_MCP_RECONNECT_ATTEMPTS", 0)
    try:
        parsed = int(env_val)
        if parsed > 0:
            return parsed
        else:
            logger.debug(
                "[mcp-reconnect] invalid OPENJIUWEN_MCP_RECONNECT_ATTEMPTS=%r; using default 3",
                env_val,
            )
    except Exception as exc:
        logger.debug(
            "[mcp-reconnect] failed to parse OPENJIUWEN_MCP_RECONNECT_ATTEMPTS=%r: %r; using default 3",
            env_val, exc,
        )
    return 3


def with_reconnect(method):
    """Decorator: on a retryable transport error, reconnect and retry up to N times.

    Wraps ``call_tool`` / ``list_tools`` / ``get_tool_info`` /
    ``list_resources`` / ``read_resource``. ``N`` defaults to 3 total attempts
    (2 retries) and can be customized per client instance by defining
    ``self._reconnect_attempts`` (total attempts, including the first try).

    Non-transport errors (e.g. ``ValueError``) propagate immediately without
    reconnecting. The decorated method's timeout (passed as ``timeout`` kwarg)
    is forwarded to ``disconnect``/``connect`` so the reconnect itself respects
    the same deadline as the original call.
    """

    @functools.wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        attempts = _get_attempts()
        if not isinstance(attempts, int) or attempts < 1:
            attempts = 2
        for attempt in range(attempts):
            try:
                return await method(self, *args, **kwargs)
            except Exception as e:
                # Only retry on transport-layer errors and if we have remaining attempts.
                if attempt < attempts - 1 and is_retryable_transport_error(e):
                    logger.warning(
                        "[mcp-reconnect] %s.%s hit transport error (attempt %d/%d), reconnecting: %r",
                        type(self).__name__, method.__name__, attempt + 1, attempts, e,
                    )
                    if await reconnect(self, timeout=kwargs.get("timeout", -1)):
                        logger.info(
                            "[mcp-reconnect] %s.%s reconnect succeeded, retrying (attempt %d/%d)",
                            type(self).__name__, method.__name__, attempt + 2, attempts,
                        )
                        continue
                raise
        return None

    return wrapper


def mark_reconnect_applied(cls: type) -> None:
    """Mark that ``@with_reconnect`` is mounted on ``cls``.

    Lets external monkeypatches (e.g. downstream products' timeout patch that
    also used to inject reconnect) detect this and skip, avoiding duplicate
    reconnect layers.
    """
    setattr(cls, "_reconnect_decorator_applied", True)
