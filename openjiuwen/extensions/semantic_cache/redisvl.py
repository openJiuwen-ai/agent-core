# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RedisVL adapter. Importing the base wrapper does not import RedisVL."""

import math
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING

from openjiuwen.extensions.semantic_cache.cache import CacheCandidate

if TYPE_CHECKING:
    from redisvl.extensions.cache.llm import SemanticCache


def _validate_vector(vector: object) -> list[float]:
    """Validate that an embedder returned a non-empty sequence of finite floats."""
    if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence) or not vector:
        raise ValueError("embed must return a non-empty sequence of finite floats")
    result: list[float] = []
    for item in vector:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValueError("embed must return a non-empty sequence of finite floats")
        result.append(float(item))
    return result


class RedisVLBackend:
    """Wrap a caller-owned SemanticCache with a ``scope_key`` TAG field.

    Supply an asynchronous embedder for the same model, dimension, dtype and
    preprocessing as the cache index. Passing vectors explicitly avoids running
    a synchronous local embedder on the event loop. Construct/close the RedisVL
    cache at application startup/shutdown, outside request handling.
    """

    def __init__(
        self,
        cache: "SemanticCache",
        embed: Callable[[str], Awaitable[Sequence[float]]],
        *,
        ttl: int,
    ):
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise ValueError("ttl must be a positive integer")
        from redisvl.query.filter import Tag

        self._cache = cache
        self._embed = embed
        self._ttl = ttl
        self._tag = Tag("scope_key")
        self._pending_vector: ContextVar[tuple[str, str, list[float]] | None] = ContextVar(
            f"redisvl_pending_vector_{id(self)}",
            default=None,
        )

    async def lookup(self, prompt: str, scope_key: str) -> CacheCandidate | None:
        """Embed the prompt, query RedisVL inside ``scope_key``, and cache the vector for this task."""
        self._pending_vector.set(None)
        vector = _validate_vector(await self._embed(prompt))
        # Reuse this task's query vector if the immediately following miss/rejection calls store().
        self._pending_vector.set((scope_key, prompt, vector))
        hits = await self._cache.acheck(
            vector=vector,
            filter_expression=self._tag == scope_key,
            return_fields=["prompt", "response", "scope_key"],
            num_results=1,
            refresh_ttl=False,
        )
        if not hits:
            return None
        hit = hits[0]
        if hit.get("scope_key") != scope_key:
            return None
        if not all(isinstance(hit.get(field), str) and hit[field].strip() for field in ("prompt", "response")):
            return None
        return CacheCandidate(hit["prompt"], hit["response"], scope_key)

    async def store(self, candidate: CacheCandidate) -> None:
        """Persist ``candidate`` into RedisVL using the pending lookup vector when matching."""
        pending = self._pending_vector.get()
        self._pending_vector.set(None)
        if pending is not None and pending[0] == candidate.scope_key and pending[1] == candidate.prompt:
            vector = pending[2]
        else:
            vector = _validate_vector(await self._embed(candidate.prompt))
        await self._cache.astore(
            prompt=candidate.prompt,
            response=candidate.response,
            vector=vector,
            filters={"scope_key": candidate.scope_key},
            ttl=self._ttl,
        )
