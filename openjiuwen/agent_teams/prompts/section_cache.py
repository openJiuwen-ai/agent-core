# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


"""Generic mtime-keyed cache primitive.

Generic refresh primitive reused across team code paths (team rails,
workspace template loading, ...) to avoid re-fetching slow data on every
model call. The cache is unaware of teams, files or databases -- callers
inject:

  - ``probe``: an awaitable returning a monotonic integer that
    increases whenever the underlying data changes (a one-row SELECT /
    MAX aggregate, or a file mtime, or ``-1`` when the file is absent).
  - ``fetch_and_build``: an awaitable that performs the full data
    fetch and returns the rebuilt value (or ``None`` when the value
    should be omitted / callers fall back).

The cache only re-runs ``fetch_and_build`` when ``probe`` returns a
value different from the last cached probe, so the steady-state cost
per call is one cheap probe + one dict lookup.

``MtimeSectionCache`` is kept as a typed alias of
``MtimeValueCache[PromptSection]`` for the team rails that inject
DB-backed probes; the workspace template loader injects a file-mtime
probe into the same primitive.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Generic, Optional, TypeVar

from openjiuwen.core.single_agent.prompts.builder import PromptSection

T = TypeVar("T")


class MtimeValueCache(Generic[T]):
    """Cache one value of type ``T``, refresh only when a probe changes.

    The cache treats the very first call as a miss regardless of the
    probe value, then only re-runs ``fetch_and_build`` when the probe
    output differs from the cached value.
    """

    def __init__(
        self,
        probe: Callable[[], Awaitable[int]],
        fetch_and_build: Callable[[], Awaitable[Optional[T]]],
    ) -> None:
        """Initialize the cache.

        Args:
            probe: Async callable returning a monotonic integer that
                increases whenever the underlying data changes.
            fetch_and_build: Async callable that performs the full
                data fetch and returns the rebuilt value or ``None``.
        """
        self._probe = probe
        self._fetch_and_build = fetch_and_build
        self._cached: Optional[T] = None
        self._cached_mtime: int = 0
        self._initialized: bool = False

    async def refresh(self) -> Optional[T]:
        """Return the current value, refetching only if mtime changed.

        Returns:
            The cached value (possibly ``None`` when the backing data
            is empty), reflecting the latest probe.
        """
        mtime = await self._probe()
        if self._initialized and mtime == self._cached_mtime:
            return self._cached
        self._cached = await self._fetch_and_build()
        self._cached_mtime = mtime
        self._initialized = True
        return self._cached

    def invalidate(self) -> None:
        """Force the next ``refresh`` to refetch regardless of mtime."""
        self._cached = None
        self._cached_mtime = 0
        self._initialized = False


# Typed alias for team rails that build PromptSections from DB probes.
MtimeSectionCache = MtimeValueCache[PromptSection]

__all__ = ["MtimeValueCache", "MtimeSectionCache"]
