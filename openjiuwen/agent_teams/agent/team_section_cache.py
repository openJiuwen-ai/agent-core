# coding: utf-8

"""mtime-based PromptSection cache.

Generic refresh primitive used by ``TeamRail`` (and any future rail)
to avoid re-fetching slow data on every model call.  The cache is
unaware of teams or databases -- callers inject:

  - ``probe``: an awaitable returning a monotonic integer that
    increases whenever the underlying data changes (typically a one
    row SELECT or MAX aggregate).
  - ``fetch_and_build``: an awaitable that performs the full data
    fetch and returns the rebuilt :class:`PromptSection` (or ``None``
    when the section should be omitted).

The cache only re-runs ``fetch_and_build`` when ``probe`` returns a
value different from the last cached probe, so the steady-state cost
per call is one cheap probe + one dict lookup.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional

from openjiuwen.core.single_agent.prompts.builder import PromptSection


class MtimeSectionCache:
    """Cache one PromptSection, refresh only when an mtime probe changes.

    The cache treats the very first call as a miss regardless of the
    probe value, then only re-runs ``fetch_and_build`` when the probe
    output differs from the cached value.
    """

    def __init__(
        self,
        probe: Callable[[], Awaitable[int]],
        fetch_and_build: Callable[[], Awaitable[Optional[PromptSection]]],
    ) -> None:
        """Initialize the cache.

        Args:
            probe: Async callable returning a monotonic integer that
                increases whenever the underlying data changes.
            fetch_and_build: Async callable that performs the full
                data fetch and returns the rebuilt PromptSection or
                ``None``.
        """
        self._probe = probe
        self._fetch_and_build = fetch_and_build
        self._cached_section: Optional[PromptSection] = None
        self._cached_mtime: int = 0
        self._initialized: bool = False

    async def refresh(self) -> Optional[PromptSection]:
        """Return the current section, refetching only if mtime changed.

        Returns:
            The cached PromptSection (possibly ``None`` when the
            backing data is empty), reflecting the latest probe.
        """
        mtime = await self._probe()
        if self._initialized and mtime == self._cached_mtime:
            return self._cached_section
        self._cached_section = await self._fetch_and_build()
        self._cached_mtime = mtime
        self._initialized = True
        return self._cached_section

    def invalidate(self) -> None:
        """Force the next ``refresh`` to refetch regardless of mtime."""
        self._cached_section = None
        self._cached_mtime = 0
        self._initialized = False


__all__ = ["MtimeSectionCache"]
