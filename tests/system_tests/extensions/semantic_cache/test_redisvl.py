# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real Redis Search integration; synthetic vectors test mechanics, not quality."""
import asyncio
import os
import unittest
import uuid
from dataclasses import replace

from openjiuwen.extensions.semantic_cache import CacheScope, GeneratedAnswer, SemanticResponseCache


@unittest.skipUnless(os.getenv("SEMANTIC_CACHE_TEST_REDIS_URL"), "requires dedicated Redis Search service")
class TestRedisVLIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from redisvl.extensions.cache.llm import SemanticCache
        from redisvl.utils.vectorize import CustomVectorizer

        from openjiuwen.extensions.semantic_cache.redisvl import RedisVLBackend

        self.redis_cache = SemanticCache(
            name="jw_test_" + uuid.uuid4().hex,
            redis_url=os.environ["SEMANTIC_CACHE_TEST_REDIS_URL"],
            vectorizer=CustomVectorizer(embed=lambda text: [1.0, 0.0, 0.0]),
            filterable_fields=[{"name": "scope_key", "type": "tag"}],
            distance_threshold=0.1,
            ttl=2,
            refresh_ttl_on_hit=False,
        )
        self.embed_calls = []

        async def embed(text):
            self.embed_calls.append(text)
            return [1.0, 0.0, 0.0]  # Deliberately identical even for contradictory questions.

        self.backend = RedisVLBackend(self.redis_cache, embed, ttl=2)
        self.cache = SemanticResponseCache(self.backend, enabled=True, timeout=2)
        self.scope = CacheScope(
            "tenant-a",
            "reader",
            "faq",
            "provider/model",
            "prompt-v1",
            "kb-v1",
            "text-v1",
            "synthetic-v1",
        )
        self.calls = 0

    async def asyncTearDown(self):
        await self.redis_cache.adelete()
        await self.redis_cache.adisconnect()
        self.redis_cache.disconnect()

    async def invoke(self, prompt, scope=None, accepted=True):
        async def generate(question):
            self.calls += 1
            return GeneratedAnswer("answer:" + question, reusable=True)

        async def validate(question, candidate):
            return accepted

        return await self.cache.invoke(
            prompt,
            scope=scope or self.scope,
            generate=generate,
            can_reuse=validate,
            eligible=True,
        )

    async def test_hit_skips_generation_and_other_tenant_misses(self):
        self.assertFalse((await self.invoke("refund")).cache_hit)
        self.assertTrue((await self.invoke("paraphrased refund")).cache_hit)
        self.assertEqual(self.calls, 1)
        other = replace(self.scope, tenant="tenant-b")
        self.assertFalse((await self.invoke("refund", scope=other)).cache_hit)
        # Same prompt in another partition must not overwrite tenant A's entry.
        self.assertTrue((await self.invoke("refund")).cache_hit)
        self.assertEqual(self.calls, 2)

    async def test_rejected_similarity_calls_generator(self):
        await self.invoke("refund")
        result = await self.invoke("do not refund", accepted=False)
        self.assertFalse(result.cache_hit)
        self.assertEqual(result.text, "answer:do not refund")
        self.assertEqual(self.calls, 2)

    async def test_hit_does_not_extend_ttl(self):
        await self.invoke("refund")
        await asyncio.sleep(1.2)
        self.assertTrue((await self.invoke("refund")).cache_hit)
        await asyncio.sleep(1.1)
        self.assertFalse((await self.invoke("refund")).cache_hit)
        self.assertEqual(self.calls, 2)

    async def test_miss_and_rejected_hit_embed_once_each_and_concurrent_misses_isolate(self):
        await self.invoke("refund")
        await self.invoke("do not refund", accepted=False)
        self.assertEqual(self.embed_calls, ["refund", "do not refund"])

        await asyncio.gather(
            self.invoke("concurrent-alpha", accepted=False),
            self.invoke("concurrent-beta", accepted=False),
        )
        self.assertEqual(sorted(self.embed_calls[2:]), ["concurrent-alpha", "concurrent-beta"])
        self.assertEqual(len(self.embed_calls), 4)

    async def test_concurrent_stampede_coalesces_per_tenant_with_real_redis(self):
        cache = SemanticResponseCache(
            self.backend,
            enabled=True,
            timeout=5.0,
            coalesce_misses=True,
            max_concurrent_backend=8,
        )
        gen_calls: dict[str, int] = {}
        all_leaders = asyncio.Event()
        release = asyncio.Event()

        async def invoke_for(tenant: str, prompt: str):
            scope = replace(self.scope, tenant=tenant)

            async def generate(q: str) -> GeneratedAnswer:
                gen_calls[tenant] = gen_calls.get(tenant, 0) + 1
                if len(gen_calls) == 4:
                    all_leaders.set()
                await asyncio.wait_for(release.wait(), timeout=5.0)
                return GeneratedAnswer(f"{tenant}:{q}", reusable=True)

            async def validate(q: str, candidate) -> bool:
                return True

            return await cache.invoke(prompt, scope=scope, generate=generate, can_reuse=validate, eligible=True)

        async def release_when_queued() -> None:
            await asyncio.wait_for(all_leaders.wait(), timeout=5.0)
            deadline = asyncio.get_running_loop().time() + 5.0
            while cache.stats.lookups < 32 and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.05)
            release.set()

        releaser = asyncio.create_task(release_when_queued())
        try:
            tasks = [invoke_for(f"tenant-{i % 4}", "stampede-question") for i in range(32)]
            results = await asyncio.gather(*tasks)
        finally:
            release.set()
            await releaser
        self.assertEqual(gen_calls, {f"tenant-{t}": 1 for t in range(4)})
        for i, res in enumerate(results):
            self.assertEqual(res.text, f"tenant-{i % 4}:stampede-question")
        self.assertEqual(cache.stats.misses, 4)
        self.assertEqual(cache.stats.hits, 28)
        self.assertEqual(cache.stats.coalesced_hits, 28)
        self.assertEqual(cache.stats.store_errors, 0)

    async def test_circuit_breaker_and_hot_kill_switch_with_real_redis(self):
        from openjiuwen.extensions.semantic_cache.redisvl import RedisVLBackend

        fail_embed = False
        real_calls = 0

        async def guarded_embed(text: str) -> list[float]:
            nonlocal real_calls
            if fail_embed:
                raise ConnectionError("embedder unreachable")
            real_calls += 1
            return [1.0, 0.0, 0.0]

        backend = RedisVLBackend(self.redis_cache, guarded_embed, ttl=2)
        cache = SemanticResponseCache(
            backend,
            enabled=True,
            timeout=2.0,
            circuit_breaker_threshold=4,
            circuit_breaker_cooldown=0.3,
        )

        async def gen(q: str) -> GeneratedAnswer:
            self.calls += 1
            return GeneratedAnswer("live:" + q, reusable=True)

        async def ok(q: str, cand) -> bool:
            return True

        seed_res = await cache.invoke("seed-q", scope=self.scope, generate=gen, can_reuse=ok, eligible=True)
        self.assertFalse(seed_res.cache_hit)
        cache.enabled = False
        calls_before = real_calls
        off_res = await cache.invoke("seed-q", scope=self.scope, generate=gen, can_reuse=ok, eligible=True)
        self.assertFalse(off_res.cache_hit)
        self.assertEqual(real_calls, calls_before)

        cache.enabled = True
        on_res = await cache.invoke("seed-q", scope=self.scope, generate=gen, can_reuse=ok, eligible=True)
        self.assertTrue(on_res.cache_hit)

        fail_embed = True
        calls_before = real_calls
        await cache.invoke("trip-1", scope=self.scope, generate=gen, can_reuse=ok, eligible=True)
        await cache.invoke("trip-2", scope=self.scope, generate=gen, can_reuse=ok, eligible=True)
        self.assertTrue(cache.circuit_open)
        fail_embed = False
        bypass_res = await cache.invoke("bypass-3", scope=self.scope, generate=gen, can_reuse=ok, eligible=True)
        self.assertFalse(bypass_res.cache_hit)
        self.assertEqual(real_calls, calls_before)
        self.assertEqual(cache.stats.circuit_open_bypasses, 1)

        await asyncio.sleep(0.35)
        rec_res = await cache.invoke("seed-q", scope=self.scope, generate=gen, can_reuse=ok, eligible=True)
        self.assertTrue(rec_res.cache_hit)
        self.assertFalse(cache.circuit_open)


if __name__ == "__main__":
    unittest.main()
