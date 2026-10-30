# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Transport-layer reconnect decorators for MCP clients.

Long-lived MCP sessions break when the peer restarts: SSE streams close and
streamable HTTP answers HTTP 404 / JSON-RPC 32600 ("Session terminated") for
every call carrying the stale ``Mcp-Session-Id``. ``@with_reconnect`` rebuilds
the transport once and retries the failed call.

MCP business errors are never retried; only transport failures and
session-invalidating ``McpError`` responses qualify.
"""
from __future__ import annotations

import asyncio
import functools
import os
import random
import time
from typing import Any

from openjiuwen.core.common.logging import logger

_SESSION_ERROR_MARKERS = (
    "session terminated",
    "session not found",
    "invalid session",
    "no valid session",
)
_TRANSPORT_ERROR_MARKERS = (
    "connection closed",
    "stream closed",
)

# 可重试的传输层错误 markers。每条 marker 是 ``(pattern, skip_for_runtime_error)``
# 二元组：pattern 用类名/消息文本子串匹配（命中即重试）；第二项标记这条 marker
# 是否对 ``RuntimeError`` 不适用。pattern 在定义时不强制小写——加载时
# 统一 ``.lower()`` 兜底，匹配端也把异常类名/消息转小写，故整体大小写不敏感。
_RETRYABLE_MARKER_DEFS: tuple[tuple[str, bool], ...] = tuple(
    (pattern, False) for pattern in _SESSION_ERROR_MARKERS + _TRANSPORT_ERROR_MARKERS
) + (
    ("closedresourceerror", False),
    ("brokenresourceerror", False),
    ("endofstream", False),
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
    from anyio import ClosedResourceError as _AnyioClosedResourceError  # type: ignore
    from anyio import BrokenResourceError as _AnyioBrokenResourceError  # type: ignore

    _RETRYABLE_TYPES += (_AnyioClosedResourceError, _AnyioBrokenResourceError)
except Exception as _imp_exc:
    logger.debug("[mcp-reconnect] failed to import anyio retryable types: %r", _imp_exc)

_McpError: type | None = None
try:
    from mcp.shared.exceptions import McpError as _McpError  # type: ignore
except Exception as _imp_exc:
    logger.debug("[mcp-reconnect] failed to import McpError: %r", _imp_exc)

#: JSON-RPC codes the MCP SDK uses for session and transport failures.
_SESSION_TERMINATED_CODE = 32600
_CONNECTION_CLOSED_CODE = -32000


def _is_retryable_mcp_error(error: Exception) -> bool:
    """Return True for MCP errors caused by a session or transport failure."""
    if _McpError is None or not isinstance(error, _McpError):
        return False
    error_data = getattr(error, "error", None)
    code = getattr(error_data, "code", None)
    message = str(getattr(error_data, "message", "") or error).lower()
    if code == _CONNECTION_CLOSED_CODE:
        return True
    if code == _SESSION_TERMINATED_CODE and "session" in message:
        return True
    return any(
        marker in message
        for marker in _SESSION_ERROR_MARKERS + _TRANSPORT_ERROR_MARKERS
    )


def is_retryable_transport_error(error: Exception) -> bool:
    """Return True if ``error`` looks like a retryable transport-layer failure.

    优先按异常类型判断（更稳健），再退回到字符串 markers 匹配（跨版本兼容）。
    匹配大小写不敏感：异常类名/消息转小写后，与每条 marker 的 pattern（亦
    已小写化）做子串包含。每条 marker 自带"是否对 RuntimeError 不适用"标记，
    避免我们自己的未连接守卫被误判为可重试传输错误。
    """
    if _McpError is not None and isinstance(error, _McpError):
        return _is_retryable_mcp_error(error)
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


def _is_not_connected_guard(error: Exception) -> bool:
    return isinstance(error, RuntimeError) and "not connected" in str(error).lower()


def _resolve_reconnect_timeout(client: Any, explicit: Any) -> float:
    """Use the call deadline when available; ``-1`` falls back to the client default."""
    if isinstance(explicit, (int, float)) and explicit > 0:
        return float(explicit)
    stamped = getattr(client, "_jws_call_timeout", None)
    if isinstance(stamped, (int, float)) and stamped > 0:
        return float(stamped)
    return -1


def should_attempt_reconnect(client: Any, error: Exception) -> bool:
    """Decide whether a failed call should trigger one reconnect attempt.

    ``Not connected`` is the client's own guard. It is retryable only when the
    client was torn down by a transport failure (``_lazy_reconnect``), never
    after an explicit user disconnect.
    """
    if _is_not_connected_guard(error):
        return bool(getattr(client, "_lazy_reconnect", False))
    return is_retryable_transport_error(error)


_RECONNECT_BASE_DELAY = 5.0
_RECONNECT_MAX_DELAY = 120.0
_RECONNECT_JITTER = 0.2


def _cooldown_remaining(client: Any) -> float:
    """Seconds left before the next *automatic* reconnect attempt."""
    try:
        until = float(getattr(client, "_reconnect_cooldown_until", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, until - time.monotonic())


def _record_reconnect_failure(client: Any) -> None:
    """Exponential backoff with jitter: 5s, 10s, ... capped at 120s."""
    count = getattr(client, "_reconnect_failure_count", 0)
    count = count + 1 if isinstance(count, int) and count >= 0 else 1
    delay = min(_RECONNECT_MAX_DELAY, _RECONNECT_BASE_DELAY * (2 ** min(count - 1, 5)))
    delay *= 1.0 - _RECONNECT_JITTER + random.random() * (2 * _RECONNECT_JITTER)
    setattr(client, "_reconnect_failure_count", count)
    setattr(client, "_reconnect_cooldown_until", time.monotonic() + delay)
    logger.warning(
        "[mcp-reconnect] %s reconnect failed %d time(s); next auto attempt in %.1fs",
        type(client).__name__, count, delay,
    )


def record_reconnect_success(client: Any) -> None:
    """Reset automatic reconnect backoff after a successful connection."""
    setattr(client, "_reconnect_failure_count", 0)
    setattr(client, "_reconnect_cooldown_until", 0.0)


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
    (2 retries). ``StreamableHttpClient`` overrides it to 2; other clients can
    set ``self._reconnect_attempts`` (total attempts, including the first try).

    Non-transport errors (e.g. ``ValueError``) propagate immediately without
    reconnecting. The decorated method's timeout (passed as ``timeout`` kwarg)
    is forwarded to ``disconnect``/``connect`` so the reconnect itself respects
    the same deadline as the original call.

    Automatic reconnects are rate-limited by an exponential cooldown
    (5s..120s, jittered). The client's explicit ``reconnect()`` API bypasses
    the cooldown so an operator can force recovery.
    """

    @functools.wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        attempts = getattr(self, "_reconnect_attempts", None)
        if not isinstance(attempts, int) or attempts < 1:
            attempts = _get_attempts()
        task = asyncio.current_task()
        call_token = object()
        active_calls = getattr(self, "_active_calls", None)
        reconnect_cancelled = getattr(self, "_reconnect_cancelled_calls", None)
        try:
            for attempt in range(attempts):
                generation = getattr(self, "_generation", None)
                if isinstance(active_calls, dict) and task is not None:
                    active_calls[task] = call_token
                try:
                    return await method(self, *args, **kwargs)
                except asyncio.CancelledError:
                    # Only cancellation issued by the reconnect teardown is
                    # retried; caller cancellation must propagate unchanged.
                    if not isinstance(reconnect_cancelled, dict) or task is None:
                        raise
                    marker = reconnect_cancelled.get(task)
                    if marker is not call_token:
                        raise
                    cancelling = getattr(task, "cancelling", None)
                    if cancelling is not None and cancelling() > 1:
                        raise
                    reconnect_cancelled.pop(task, None)
                    uncancel = getattr(task, "uncancel", None)
                    if uncancel is not None:
                        uncancel()
                    error: Exception = RuntimeError(
                        "Not connected to streamable-http server"
                    )
                except Exception as exc:
                    error = exc
                finally:
                    if isinstance(active_calls, dict) and task is not None:
                        if active_calls.get(task) is call_token:
                            active_calls.pop(task, None)

                # Only retry on transport-layer errors and if attempts remain.
                if attempt < attempts - 1 and should_attempt_reconnect(self, error):
                    # Another caller already rebuilt the transport while this
                    # call was in flight; retry without tearing it down again.
                    current = getattr(self, "_generation", None)
                    if generation is not None and current != generation:
                        logger.info(
                            "[mcp-reconnect] %s.%s retrying on generation %s "
                            "(was %s) without reconnecting",
                            type(self).__name__, method.__name__, current, generation,
                        )
                        continue
                    remaining = _cooldown_remaining(self)
                    if remaining > 0:
                        logger.warning(
                            "[mcp-reconnect] %s.%s skipping auto reconnect: "
                            "cooldown has %.1fs remaining",
                            type(self).__name__, method.__name__, remaining,
                        )
                        raise error
                    if _is_not_connected_guard(error):
                        setattr(self, "_lazy_reconnect", True)
                    logger.warning(
                        "[mcp-reconnect] %s.%s hit transport error (attempt %d/%d), reconnecting: %r",
                        type(self).__name__, method.__name__, attempt + 1, attempts, error,
                    )
                    try:
                        reconnected = await reconnect(
                            self,
                            timeout=_resolve_reconnect_timeout(self, kwargs.get("timeout")),
                        )
                    except Exception:
                        if not _cooldown_remaining(self):
                            _record_reconnect_failure(self)
                        raise
                    if reconnected:
                        record_reconnect_success(self)
                        logger.info(
                            "[mcp-reconnect] %s.%s reconnect succeeded, retrying (attempt %d/%d)",
                            type(self).__name__, method.__name__, attempt + 2, attempts,
                        )
                        continue
                    if not _cooldown_remaining(self):
                        _record_reconnect_failure(self)
                raise error
            return None
        finally:
            if isinstance(reconnect_cancelled, dict) and task is not None:
                if reconnect_cancelled.get(task) is call_token:
                    reconnect_cancelled.pop(task, None)

    return wrapper


def mark_reconnect_applied(cls: type) -> None:
    """Mark that ``@with_reconnect`` is mounted on ``cls``.

    Lets external monkeypatches (e.g. downstream products' timeout patch that
    also used to inject reconnect) detect this and skip, avoiding duplicate
    reconnect layers.
    """
    setattr(cls, "_reconnect_decorator_applied", True)
