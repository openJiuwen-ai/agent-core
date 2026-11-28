# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from openjiuwen.extensions.semantic_cache import (
    CacheCandidate, CacheScope, GeneratedAnswer, SemanticResponseCache,
)


class _FakeTag:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other: object) -> tuple[str, object]:  # type: ignore[override]
        return (self.name, other)


def _fake_redisvl_modules() -> dict[str, ModuleType]:
    redisvl_mod = ModuleType("redisvl")
    query_mod = ModuleType("redisvl.query")
    filter_mod = ModuleType("redisvl.query.filter")
    filter_mod.Tag = _FakeTag  # type: ignore[attr-defined]
    return {
        "redisvl": redisvl_mod,
        "redisvl.query": query_mod,
        "redisvl.query.filter": filter_mod,
    }


SCOPE = CacheScope(
    "tenant", "reader", "faq", "provider/model",
    "template-v1", "kb-v1", "text-v1", "embed-v1",
)


class TestRedisVLBackend(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patcher = patch.dict(sys.modules, _fake_redisvl_modules())
        self.patcher.start()
        from openjiuwen.extensions.semantic_cache.redisvl import RedisVLBackend

        self.backend_cls = RedisVLBackend
        self.embed_calls: list[str] = []
        self.redis_cache = SimpleNamespace(
            acheck=AsyncMock(return_value=[]),
            astore=AsyncMock(),
        )

    def tearDown(self):
        self.patcher.stop()

    async def _embed(self, text: str) -> list[float]:
        self.embed_calls.append(text)
        return [float(len(text)), float(sum(ord(ch) for ch in text))]

    async def test_miss_embeds_once_and_reuses_vector_on_store(self):
        backend = self.backend_cls(self.redis_cache, self._embed, ttl=60)
        cache = SemanticResponseCache(backend, enabled=True, timeout=1.0)
        generate = AsyncMock(return_value=GeneratedAnswer("answer", reusable=True))
        can_reuse = AsyncMock(return_value=True)

        result = await cache.invoke(
            "refund policy?", scope=SCOPE, generate=generate, can_reuse=can_reuse, eligible=True
        )

        self.assertFalse(result.cache_hit)
        self.assertEqual(self.embed_calls, ["refund policy?"])
        expected_vec = [float(len("refund policy?")), float(sum(ord(ch) for ch in "refund policy?"))]
        self.redis_cache.acheck.assert_awaited_once()
        self.redis_cache.astore.assert_awaited_once()
        self.assertEqual(self.redis_cache.astore.await_args.kwargs["vector"], expected_vec)

    async def test_rejected_candidate_miss_embeds_once(self):
        self.redis_cache.acheck.return_value = [
            {"prompt": "opened return", "response": "not allowed", "scope_key": SCOPE.key},
        ]
        backend = self.backend_cls(self.redis_cache, self._embed, ttl=60)
        cache = SemanticResponseCache(backend, enabled=True, timeout=1.0)
        generate = AsyncMock(return_value=GeneratedAnswer("allowed within 30 days", reusable=True))
        can_reuse = AsyncMock(return_value=False)

        result = await cache.invoke(
            "unopened return", scope=SCOPE, generate=generate, can_reuse=can_reuse, eligible=True
        )

        self.assertFalse(result.cache_hit)
        self.assertEqual(result.text, "allowed within 30 days")
        self.assertEqual(self.embed_calls, ["unopened return"])
        self.redis_cache.astore.assert_awaited_once()

    async def test_direct_store_without_lookup_embeds_normally(self):
        backend = self.backend_cls(self.redis_cache, self._embed, ttl=60)
        await backend.store(CacheCandidate("direct prompt", "direct answer", SCOPE.key))
        self.assertEqual(self.embed_calls, ["direct prompt"])
        self.redis_cache.astore.assert_awaited_once()

    async def test_concurrent_misses_do_not_cross_contaminate(self):
        async def slow_embed(text: str) -> list[float]:
            self.embed_calls.append(text)
            await asyncio.sleep(0.01 if text == "first" else 0.002)
            return [1.0, 0.0] if text == "first" else [0.0, 1.0]

        backend = self.backend_cls(self.redis_cache, slow_embed, ttl=60)
        cache = SemanticResponseCache(backend, enabled=True, timeout=1.0)

        async def run_one(prompt: str):
            return await cache.invoke(
                prompt,
                scope=SCOPE,
                generate=AsyncMock(return_value=GeneratedAnswer("ans:" + prompt, reusable=True)),
                can_reuse=AsyncMock(return_value=False),
                eligible=True,
            )

        await asyncio.gather(run_one("first"), run_one("second"))

        self.assertEqual(len(self.embed_calls), 2)
        stored = {call.kwargs["prompt"]: call.kwargs["vector"] for call in self.redis_cache.astore.await_args_list}
        self.assertEqual(stored["first"], [1.0, 0.0])
        self.assertEqual(stored["second"], [0.0, 1.0])

    async def test_separate_backend_instances_are_isolated(self):
        cache_a = SimpleNamespace(acheck=AsyncMock(return_value=[]), astore=AsyncMock())
        cache_b = SimpleNamespace(acheck=AsyncMock(return_value=[]), astore=AsyncMock())
        calls_b: list[str] = []

        async def embed_b(text: str) -> list[float]:
            calls_b.append(text)
            return [9.0, 9.0]

        backend_a = self.backend_cls(cache_a, self._embed, ttl=60)
        backend_b = self.backend_cls(cache_b, embed_b, ttl=60)

        await backend_a.lookup("same prompt", SCOPE.key)
        await backend_b.store(CacheCandidate("same prompt", "answer", SCOPE.key))

        self.assertEqual(self.embed_calls, ["same prompt"])
        self.assertEqual(calls_b, ["same prompt"])
        self.assertEqual(cache_b.astore.await_args.kwargs["vector"], [9.0, 9.0])

    async def test_mismatched_prompt_or_scope_does_not_reuse_pending_vector(self):
        backend = self.backend_cls(self.redis_cache, self._embed, ttl=60)
        await backend.lookup("prompt-a", SCOPE.key)
        await backend.store(CacheCandidate("prompt-b", "answer", SCOPE.key))
        await backend.lookup("prompt-a", SCOPE.key)
        await backend.store(CacheCandidate("prompt-a", "answer", "other-scope"))
        # Calling store again with original pair must NOT find stale vector after mismatch cleared it.
        await backend.store(CacheCandidate("prompt-a", "answer", SCOPE.key))

        self.assertEqual(self.embed_calls, ["prompt-a", "prompt-b", "prompt-a", "prompt-a", "prompt-a"])

    async def test_invalid_vectors_fail_open_without_calling_redis(self):
        for bad_vec in ([], [float("nan")], [float("inf")]):
            async def bad_embed(_text: str, vec=bad_vec) -> list[float]:
                return vec

            backend = self.backend_cls(self.redis_cache, bad_embed, ttl=60)
            cache = SemanticResponseCache(backend, enabled=True, timeout=1.0)
            generate = AsyncMock(return_value=GeneratedAnswer("fallback-ok", reusable=True))
            res = await cache.invoke("q", scope=SCOPE, generate=generate, can_reuse=AsyncMock(return_value=True),
                                     eligible=True)
            self.assertFalse(res.cache_hit)
            self.assertEqual(res.text, "fallback-ok")
            self.assertEqual(cache.stats.backend_errors, 1)
            self.assertEqual(cache.stats.store_errors, 1)
        self.redis_cache.acheck.assert_not_called()
        self.redis_cache.astore.assert_not_called()


if __name__ == "__main__":
    unittest.main()
