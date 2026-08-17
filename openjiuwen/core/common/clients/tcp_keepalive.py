# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TCP SO_KEEPALIVE socket options for HTTP transports.

``keepalive_expiry`` (httpx) / ``keepalive_timeout`` (aiohttp) only govern
HTTP-layer connection-pool reuse; they do NOT enable OS-level TCP keepalive
probes on the underlying socket. Without ``SO_KEEPALIVE`` a long-lived
connection to an LLM gateway can be silently dropped when the NIC enters a
low-power state (Windows display-off) or a stateful middlebox reaps idle
flows — the failure mode behind IR-2026-0813-001.

This module builds a cross-platform ``socket_options`` list consumable by
``httpcore.AsyncConnectionPool(socket_options=...)`` and
``httpx.AsyncHTTPTransport(socket_options=...)`` (httpx >= 0.25):

* Linux / macOS: ``SO_KEEPALIVE`` + ``TCP_KEEPIDLE`` / ``TCP_KEEPINTVL`` /
  ``TCP_KEEPCNT`` via standard ``socket`` constants.
* Windows: ``SO_KEEPALIVE`` only — per-flow interval tuning requires
  ``WSAIoctl(SIO_KEEPALIVE_VALS)`` which ``socket_options`` tuples cannot
  express. Pair with system-level ``powercfg`` for full coverage on Windows
  desktop clients.

All option setup is best-effort: any ``AttributeError`` / ``OSError`` is
swallowed so a missing/unavailable option never breaks transport creation.
"""

import socket
from typing import List, Tuple

from openjiuwen.core.common.logging import logger

_DEFAULT_KEEPALIVE_IDLE_SECONDS = 30
_DEFAULT_KEEPALIVE_INTERVAL_SECONDS = 10
_DEFAULT_KEEPALIVE_PROBE_COUNT = 3


def build_tcp_keepalive_socket_options(
    *,
    idle_seconds: int = _DEFAULT_KEEPALIVE_IDLE_SECONDS,
    interval_seconds: int = _DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
    probe_count: int = _DEFAULT_KEEPALIVE_PROBE_COUNT,
) -> List[Tuple[int, int, int]]:
    """Build a cross-platform TCP keepalive ``socket_options`` list.

    Returns ``(level, optname, value)`` tuples consumable by
    ``httpcore.AsyncConnectionPool(socket_options=...)`` and
    ``httpx.AsyncHTTPTransport(socket_options=...)``.

    Args:
        idle_seconds: TCP_KEEPIDLE — idle seconds before the first probe.
        interval_seconds: TCP_KEEPINTVL — seconds between successive probes.
        probe_count: TCP_KEEPCNT — probes before declaring the connection dead.

    Returns:
        List of socket-option tuples; may be empty (e.g. if ``SO_KEEPALIVE``
        itself is unavailable on the platform).
    """
    options: List[Tuple[int, int, int]] = []

    try:
        options.append((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1))
    except (AttributeError, OSError) as exc:
        logger.debug(
            "SO_KEEPALIVE unavailable on this platform; skipping TCP keepalive: %s", exc
        )
        return options

    try:
        options.append((socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, int(idle_seconds)))
    except AttributeError:
        pass
    try:
        options.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, int(interval_seconds)))
    except AttributeError:
        pass
    try:
        options.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, int(probe_count)))
    except AttributeError:
        pass

    return options


_DEFAULT_SOCKET_OPTIONS: List[Tuple[int, int, int]] = build_tcp_keepalive_socket_options()


def get_default_tcp_keepalive_socket_options() -> List[Tuple[int, int, int]]:
    """Return a cached, platform-constant default TCP keepalive option list."""
    return list(_DEFAULT_SOCKET_OPTIONS)
