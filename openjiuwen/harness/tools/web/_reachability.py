# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""In-process endpoint reachability cache for the web tools package.

The web tools call a small, fixed set of network endpoints (the free-search
engines and the jina.ai reader proxy). When one of them is unreachable at the
network layer (a connect-phase failure), every call that touches it pays the
full connect timeout before learning it is dead. This module remembers that
verdict for a bounded time so later calls skip a dead endpoint immediately.

Only a *real* request failing to connect marks a host dead; there is no active
probing. A host is considered reachable again once its backoff deadline has
passed, at which point the next real request acts as the half-open probe
(success clears the mark, failure extends the backoff).

The cache is process-local and lock-free: the web tools run on a single asyncio
event loop, so the state is only ever mutated from that one thread.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

# Backoff ladder applied after consecutive connect-phase failures. The first
# level is short so a transient blip self-heals quickly; each repeated failure
# pushes the next retry further out, capped at six hours.
_BACKOFF_SECONDS = (300, 900, 3600, 21600)

# host -> monotonic deadline after which the host may be retried again.
_dead_until: dict[str, float] = {}
# host -> number of consecutive connect-phase failures seen so far.
_attempts: dict[str, int] = {}


def host_from(url: str) -> str:
    """Return the lower-cased host of ``url``, or ``""`` when it has none."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_dead(host: str) -> bool:
    """Whether ``host`` is currently marked unreachable."""
    return time.monotonic() < _dead_until.get((host or "").lower(), 0.0)


def mark_dead(host: str) -> None:
    """Record a connect-phase failure for ``host`` and extend its backoff."""
    host = (host or "").lower()
    if not host:
        return
    level = _attempts.get(host, 0)
    _attempts[host] = min(level + 1, len(_BACKOFF_SECONDS) - 1)
    _dead_until[host] = time.monotonic() + _BACKOFF_SECONDS[level]


def mark_alive(host: str) -> None:
    """Clear any dead marking for ``host`` after it responded successfully."""
    host = (host or "").lower()
    _dead_until.pop(host, None)
    _attempts.pop(host, None)