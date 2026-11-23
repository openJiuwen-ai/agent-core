# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from openjiuwen.extensions.semantic_cache import (
    CacheCandidate, CacheScope, CacheStats, GeneratedAnswer, SemanticResponseCache,
)


SCOPE = CacheScope("tenant", "reader", "faq", "provider/model/revision",
                   "template-v1", "knowledge-v1", "text-temperature0", "embed-v1")


class TestSemanticResponseCache(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.backend = AsyncMock()
        self.backend.lookup.return_value = None
        self.generate = AsyncMock(return_value=GeneratedAnswer("answer", reusable=True))
        self.validate = AsyncMock(return_value=True)
        self.cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.02)

    async def invoke(self, **kwargs):
        args = dict(scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True)
        args.update(kwargs)
        return await self.cache.invoke("question", **args)

    async def test_default_disabled(self):
        self.cache = SemanticResponseCache(self.backend)
        result = await self.invoke()
        self.assertFalse(result.cache_hit)
        self.backend.lookup.assert_not_called()
        self.backend.store.assert_not_called()
        self.generate.assert_awaited_once_with("question")

    async def test_ineligible_bypasses(self):
        await self.invoke(eligible=False)
        self.backend.lookup.assert_not_called()
        self.backend.store.assert_not_called()

    async def test_miss_stores_final_answer(self):
        result = await self.invoke()
        self.assertEqual(result.text, "answer")
        self.assertFalse(result.cache_hit)
        self.backend.store.assert_awaited_once_with(CacheCandidate("question", "answer", SCOPE.key))

    async def test_valid_hit_never_calls_model(self):
        self.backend.lookup.return_value = CacheCandidate("paraphrase", "saved", SCOPE.key)
        result = await self.invoke()
        self.assertEqual(result.text, "saved")
        self.assertTrue(result.cache_hit)
        self.generate.assert_not_called()
        self.backend.store.assert_not_called()

    async def test_negation_candidate_rejected_by_application(self):
        self.backend.lookup.return_value = CacheCandidate("do not refund", "keep payment", SCOPE.key)
        self.validate.return_value = False
        result = await self.invoke()
        self.assertFalse(result.cache_hit)
        self.generate.assert_awaited_once()

    async def test_scope_mismatch_cannot_reuse(self):
        for field in SCOPE.__dataclass_fields__:
            with self.subTest(field=field):
                other = replace(SCOPE, **{field: "different"})
                self.backend.lookup.return_value = CacheCandidate("question", "private", other.key)
                self.assertFalse((await self.invoke()).cache_hit)
        self.validate.assert_not_called()
        self.assertEqual(self.generate.await_count, 8)

    async def test_policy_error_does_not_fall_back(self):
        self.backend.lookup.return_value = CacheCandidate("question", "saved", SCOPE.key)
        self.validate.side_effect = PermissionError("access denied")
        with self.assertRaises(PermissionError):
            await self.invoke()
        self.generate.assert_not_called()

    async def test_lookup_failure_falls_back(self):
        self.backend.lookup.side_effect = ConnectionError("unavailable")
        self.assertFalse((await self.invoke()).cache_hit)
        self.generate.assert_awaited_once()

    async def test_lookup_timeout_falls_back(self):
        async def slow(*args):
            await asyncio.sleep(10)
        self.backend.lookup.side_effect = slow
        self.assertFalse((await self.invoke()).cache_hit)
        self.generate.assert_awaited_once()

    async def test_store_failure_preserves_answer(self):
        self.backend.store.side_effect = ConnectionError("unavailable")
        self.assertEqual((await self.invoke()).text, "answer")

    async def test_incomplete_or_unapproved_answer_not_stored(self):
        for answer in (GeneratedAnswer("partial"), GeneratedAnswer("", True)):
            self.generate.return_value = answer
            await self.invoke()
        self.backend.store.assert_not_called()

    async def test_generation_failure_not_stored(self):
        self.generate.side_effect = RuntimeError("provider failed")
        with self.assertRaises(RuntimeError):
            await self.invoke()
        self.backend.store.assert_not_called()

    async def test_lookup_cancellation_does_not_generate(self):
        self.backend.lookup.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.invoke()
        self.generate.assert_not_called()

    async def test_store_cancellation_propagates(self):
        self.backend.store.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.invoke()

    async def test_validator_timeout_fails_closed(self):
        self.backend.lookup.return_value = CacheCandidate("question", "saved", SCOPE.key)
        async def slow(*args):
            await asyncio.sleep(10)
        self.validate.side_effect = slow
        with self.assertRaises(TimeoutError):
            await self.invoke()
        self.generate.assert_not_called()

    def test_scope_validation_and_encoding(self):
        with self.assertRaises(ValueError):
            replace(SCOPE, access="")
        self.assertNotEqual(replace(SCOPE, tenant="a|b", access="c").key,
                            replace(SCOPE, tenant="a", access="b|c").key)
        self.assertEqual(SCOPE.key, replace(SCOPE).key)

    def test_timeout_validation(self):
        for timeout in (0, -1, True, False, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                SemanticResponseCache(self.backend, timeout=timeout)

    def test_circuit_breaker_validation(self):
        for invalid_threshold in (0, -1, True, False):
            with self.assertRaises(ValueError):
                SemanticResponseCache(self.backend, circuit_breaker_threshold=invalid_threshold)
        for invalid_cooldown in (0, -1, True, False, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                SemanticResponseCache(self.backend, circuit_breaker_cooldown=invalid_cooldown)

    async def test_enabled_runtime_toggle_and_bool_validation(self):
        with self.assertRaises(TypeError):
            SemanticResponseCache(self.backend, enabled="true")  # type: ignore[arg-type]
        cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.2)
        with self.assertRaises(TypeError):
            cache.enabled = 1  # type: ignore[assignment]
        with self.assertRaises(TypeError):
            await cache.invoke(
                "q", scope="bad", generate=self.generate,  # type: ignore[arg-type]
                can_reuse=self.validate, eligible=True,
            )
        with self.assertRaises(TypeError):
            await cache.invoke(
                "q", scope=SCOPE, generate=self.generate,
                can_reuse=self.validate, eligible="yes",  # type: ignore[arg-type]
            )

        cache.enabled = False
        self.assertFalse(cache.enabled)
        res_off = await cache.invoke("q", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True)
        self.assertFalse(res_off.cache_hit)
        self.backend.lookup.assert_not_called()
        self.backend.store.assert_not_called()

        cache.enabled = True
        self.assertTrue(cache.enabled)
        self.backend.lookup.return_value = CacheCandidate("q", "cached-after-enable", SCOPE.key)
        res_on = await cache.invoke("q", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True)
        self.assertTrue(res_on.cache_hit)
        self.assertEqual(res_on.text, "cached-after-enable")

    def test_max_concurrent_backend_validation(self):
        for invalid in (0, -2, True, False):
            with self.assertRaises(ValueError):
                SemanticResponseCache(self.backend, max_concurrent_backend=invalid)

    async def test_coalesce_misses_deduplicates_stampede(self):
        calls = 0

        async def slow_generate(prompt: str) -> GeneratedAnswer:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return GeneratedAnswer("shared:" + prompt, reusable=True)

        cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.5, coalesce_misses=True)
        results = await asyncio.gather(*[
            cache.invoke("hot-question", scope=SCOPE, generate=slow_generate, can_reuse=self.validate, eligible=True)
            for _ in range(32)
        ])
        self.assertEqual(calls, 1)
        self.assertEqual(sum(1 for r in results if not r.cache_hit), 1)
        self.assertEqual(sum(1 for r in results if r.cache_hit), 31)
        self.assertTrue(all(r.text == "shared:hot-question" for r in results))
        self.assertEqual(len(cache._in_flight), 0)
        self.assertEqual(cache.stats.coalesced_hits, 31)
        self.assertEqual(cache.stats.misses, 1)
        self.assertEqual(cache.stats.lookups, 32)
        self.backend.store.assert_awaited_once()

    async def test_coalesce_misses_isolates_scopes(self):
        calls = 0

        async def slow_generate(prompt: str) -> GeneratedAnswer:
            nonlocal calls
            calls += 1
            call_id = calls
            await asyncio.sleep(0.02)
            return GeneratedAnswer(f"ans:{call_id}:{prompt}", reusable=True)

        other_scope = replace(SCOPE, tenant="tenant-b")
        cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.5, coalesce_misses=True)
        r_a, r_b = await asyncio.gather(
            cache.invoke("same-prompt", scope=SCOPE, generate=slow_generate, can_reuse=self.validate, eligible=True),
            cache.invoke(
                "same-prompt", scope=other_scope, generate=slow_generate, can_reuse=self.validate, eligible=True
            ),
        )
        self.assertEqual(calls, 2)
        self.assertNotEqual(r_a.text, r_b.text)
        self.assertEqual(len(cache._in_flight), 0)

    async def test_waiter_cancellation_does_not_cancel_leader(self):
        leader_entered = asyncio.Event()
        release_leader = asyncio.Event()
        calls = 0

        async def slow_generate(prompt: str) -> GeneratedAnswer:
            nonlocal calls
            calls += 1
            leader_entered.set()
            await release_leader.wait()
            return GeneratedAnswer("leader-ok", reusable=True)

        cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.5, coalesce_misses=True)
        leader = asyncio.create_task(
            cache.invoke("q", scope=SCOPE, generate=slow_generate, can_reuse=self.validate, eligible=True)
        )
        await leader_entered.wait()
        waiter = asyncio.create_task(
            cache.invoke("q", scope=SCOPE, generate=slow_generate, can_reuse=self.validate, eligible=True)
        )
        await asyncio.sleep(0.005)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        release_leader.set()
        res = await leader
        self.assertEqual(res.text, "leader-ok")
        self.assertEqual(calls, 1)
        self.assertEqual(len(cache._in_flight), 0)

    async def test_leader_failure_or_cancellation_wakes_waiters_cleanly(self):
        for failure in (RuntimeError("boom"), asyncio.CancelledError()):
            leader_entered = asyncio.Event()
            release_leader = asyncio.Event()
            calls = 0

            async def flaky_generate(prompt: str, exc=failure) -> GeneratedAnswer:
                nonlocal calls
                calls += 1
                if calls == 1:
                    leader_entered.set()
                    await release_leader.wait()
                    raise exc
                return GeneratedAnswer("fallback-ok", reusable=True)

            cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.5, coalesce_misses=True)
            leader = asyncio.create_task(
                cache.invoke("q", scope=SCOPE, generate=flaky_generate, can_reuse=self.validate, eligible=True)
            )
            await leader_entered.wait()
            waiter = asyncio.create_task(
                cache.invoke("q", scope=SCOPE, generate=flaky_generate, can_reuse=self.validate, eligible=True)
            )
            await asyncio.sleep(0.005)
            release_leader.set()
            with self.assertRaises(type(failure)):
                await leader
            waiter_res = await waiter
            self.assertEqual(waiter_res.text, "fallback-ok")
            self.assertFalse(waiter_res.cache_hit)
            self.assertEqual(calls, 2)
            self.assertEqual(len(cache._in_flight), 0)

    async def test_leader_non_reusable_answer_is_not_shared_with_waiters(self):
        leader_entered = asyncio.Event()
        release_leader = asyncio.Event()
        calls = 0

        async def gen(prompt: str) -> GeneratedAnswer:
            nonlocal calls
            calls += 1
            if calls == 1:
                leader_entered.set()
                await release_leader.wait()
                return GeneratedAnswer("refusal", reusable=False)
            return GeneratedAnswer("valid", reusable=True)

        cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.5, coalesce_misses=True)
        leader = asyncio.create_task(
            cache.invoke("q", scope=SCOPE, generate=gen, can_reuse=self.validate, eligible=True)
        )
        await leader_entered.wait()
        waiter = asyncio.create_task(
            cache.invoke("q", scope=SCOPE, generate=gen, can_reuse=self.validate, eligible=True)
        )
        await asyncio.sleep(0.005)
        release_leader.set()
        r1, r2 = await asyncio.gather(leader, waiter)
        self.assertEqual((r1.text, r2.text), ("refusal", "valid"))
        self.assertEqual(calls, 2)
        self.assertEqual(cache.stats.coalesced_hits, 0)

    async def test_coalesce_waiter_validation_rejection_and_error(self):
        leader_entered = asyncio.Event()
        release_leader = asyncio.Event()
        calls = 0

        async def gen(prompt: str) -> GeneratedAnswer:
            nonlocal calls
            calls += 1
            if calls == 1:
                leader_entered.set()
                await release_leader.wait()
                return GeneratedAnswer("leader-answer", reusable=True)
            return GeneratedAnswer("waiter-fallback", reusable=True)

        cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.5, coalesce_misses=True)
        leader = asyncio.create_task(
            cache.invoke("q", scope=SCOPE, generate=gen, can_reuse=self.validate, eligible=True)
        )
        await leader_entered.wait()
        reject_waiter = asyncio.create_task(
            cache.invoke("q", scope=SCOPE, generate=gen, can_reuse=AsyncMock(return_value=False), eligible=True)
        )
        err_waiter = asyncio.create_task(
            cache.invoke(
                "q", scope=SCOPE, generate=gen,
                can_reuse=AsyncMock(side_effect=PermissionError("denied")), eligible=True,
            )
        )
        await asyncio.sleep(0.005)
        release_leader.set()
        leader_res = await leader
        reject_res = await reject_waiter
        with self.assertRaises(PermissionError):
            await err_waiter
        self.assertEqual(leader_res.text, "leader-answer")
        self.assertEqual(reject_res.text, "waiter-fallback")
        self.assertFalse(reject_res.cache_hit)
        self.assertEqual(calls, 2)
        self.assertEqual(cache.stats.validator_rejections, 1)

    async def test_max_concurrent_backend_limits_lookup_and_drops_overload_store(self):
        active_lookups = 0
        max_seen_lookups = 0
        hold_lookups = asyncio.Event()
        two_entered = asyncio.Event()

        async def slow_lookup(prompt: str, scope_key: str):
            nonlocal active_lookups, max_seen_lookups
            active_lookups += 1
            max_seen_lookups = max(max_seen_lookups, active_lookups)
            if active_lookups == 2:
                two_entered.set()
            try:
                await hold_lookups.wait()
                return None
            finally:
                active_lookups -= 1

        self.backend.lookup.side_effect = slow_lookup
        no_store_gen = AsyncMock(return_value=GeneratedAnswer("answer", reusable=False))
        cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.5, max_concurrent_backend=2)
        lookup_tasks = [
            asyncio.create_task(
                cache.invoke(f"q{i}", scope=SCOPE, generate=no_store_gen, can_reuse=self.validate, eligible=True)
            )
            for i in range(4)
        ]
        await two_entered.wait()
        await asyncio.sleep(0.005)
        self.assertEqual(max_seen_lookups, 2)
        await cache._store_best_effort(CacheCandidate("during-lookup-sat", "ans", SCOPE.key))
        self.assertEqual(cache.stats.store_successes, 1)
        self.assertEqual(cache.stats.store_skipped_overload, 0)
        hold_lookups.set()
        await asyncio.gather(*lookup_tasks)
        self.assertEqual(max_seen_lookups, 2)

        active_stores = 0
        hold_stores = asyncio.Event()
        two_stores_entered = asyncio.Event()

        async def slow_store(candidate: CacheCandidate):
            nonlocal active_stores
            active_stores += 1
            if active_stores == 2:
                two_stores_entered.set()
            try:
                await hold_stores.wait()
            finally:
                active_stores -= 1

        self.backend.store.side_effect = slow_store
        store_tasks = [
            asyncio.create_task(cache._store_best_effort(CacheCandidate(f"s{i}", "ans", SCOPE.key)))
            for i in range(2)
        ]
        await two_stores_entered.wait()
        await cache._store_best_effort(CacheCandidate("overflow-store", "ans", SCOPE.key))
        self.assertEqual(cache.stats.store_skipped_overload, 1)
        hold_stores.set()
        await asyncio.gather(*store_tasks)
        self.assertEqual(cache.stats.store_successes, 3)

        self.backend.lookup.side_effect = None
        self.backend.store.side_effect = None
        await cache.invoke(
            "after-saturation", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True
        )
        self.assertEqual(cache.stats.store_successes, 4)

    async def test_stats_counters_accurate(self):
        cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.02)
        await cache.invoke("bypass", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=False)
        self.backend.lookup.side_effect = ConnectionError("down")
        self.backend.store.side_effect = ConnectionError("down")
        await cache.invoke("err", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True)
        self.backend.lookup.side_effect = None
        self.backend.store.side_effect = None
        self.backend.lookup.return_value = CacheCandidate("q", "bad", SCOPE.key)
        await cache.invoke(
            "rej", scope=SCOPE, generate=self.generate, can_reuse=AsyncMock(return_value=False), eligible=True
        )
        await cache.invoke("hit", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True)

        self.assertEqual(
            cache.stats,
            CacheStats(
                lookups=3,
                hits=1,
                coalesced_hits=0,
                misses=2,
                bypasses=1,
                validator_rejections=1,
                backend_errors=1,
                store_successes=1,
                store_errors=1,
                store_skipped_overload=0,
                circuit_open_bypasses=0,
            ),
        )

    async def test_circuit_breaker_opens_bypasses_and_recovers_via_single_probe(self):
        now = [100.0]

        def monotonic_now() -> float:
            return now[0]

        with (
            patch("openjiuwen.extensions.semantic_cache.cache._monotonic", side_effect=monotonic_now),
            patch("openjiuwen.extensions.semantic_cache.cache.llm_logger") as mock_logger,
        ):
            cache = SemanticResponseCache(
                self.backend,
                enabled=True,
                timeout=0.5,
                circuit_breaker_threshold=2,
                circuit_breaker_cooldown=30.0,
            )
            self.backend.lookup.side_effect = ConnectionError("redis down")
            for _ in range(2):
                res = await cache.invoke(
                    "q", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True
                )
                self.assertFalse(res.cache_hit)
            self.assertTrue(cache.circuit_open)
            self.assertEqual(self.backend.lookup.await_count, 2)
            self.assertEqual(self.backend.store.await_count, 1)
            warning_msgs = [call.args[0] % call.args[1:] for call in mock_logger.warning.call_args_list]
            self.assertTrue(any("circuit breaker opened" in msg for msg in warning_msgs))
            self.assertFalse(any("secret" in msg or "\nq\n" in msg for msg in warning_msgs))

            res_bypass = await cache.invoke(
                "q", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True
            )
            self.assertFalse(res_bypass.cache_hit)
            self.assertEqual(cache.stats.circuit_open_bypasses, 1)
            self.assertEqual(self.backend.lookup.await_count, 2)
            self.assertEqual(self.backend.store.await_count, 1)

            now[0] = 131.0
            probe_entered = asyncio.Event()
            release_probe = asyncio.Event()

            async def probe_lookup(prompt: str, scope_key: str):
                probe_entered.set()
                await release_probe.wait()
                return CacheCandidate(prompt, "recovered-hit", scope_key)

            self.backend.lookup.side_effect = probe_lookup
            probe_task = asyncio.create_task(
                cache.invoke("q", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True)
            )
            await probe_entered.wait()
            peer_res = await cache.invoke(
                "q-peer", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True
            )
            self.assertFalse(peer_res.cache_hit)
            self.assertEqual(cache.stats.circuit_open_bypasses, 2)
            self.assertEqual(self.backend.lookup.await_count, 3)

            release_probe.set()
            probe_res = await probe_task
            self.assertTrue(probe_res.cache_hit)
            self.assertEqual(probe_res.text, "recovered-hit")
            self.assertFalse(cache.circuit_open)
            mock_logger.info.assert_called_once()
            recovery_msg = mock_logger.info.call_args.args[0] % mock_logger.info.call_args.args[1:]
            self.assertIn("circuit breaker closed after backend recovery", recovery_msg)
            self.assertNotIn("recovered-hit", recovery_msg)

    async def test_circuit_breaker_reopens_on_probe_failure_and_recovers_on_probe_cancel(self):
        now = [100.0]

        def monotonic_now() -> float:
            return now[0]

        with (
            patch("openjiuwen.extensions.semantic_cache.cache._monotonic", side_effect=monotonic_now),
            patch("openjiuwen.extensions.semantic_cache.cache.llm_logger"),
        ):
            cache = SemanticResponseCache(
                self.backend,
                enabled=True,
                timeout=0.5,
                circuit_breaker_threshold=2,
                circuit_breaker_cooldown=20.0,
            )
            self.backend.lookup.side_effect = ConnectionError("down")
            await cache.invoke("q1", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True)
            await cache.invoke("q2", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True)
            self.assertTrue(cache.circuit_open)

            now[0] = 121.0
            await cache.invoke(
                "probe-fail", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True
            )
            self.assertTrue(cache.circuit_open)
            await cache.invoke("bypass-2", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True)
            self.assertEqual(cache.stats.circuit_open_bypasses, 1)

            now[0] = 142.0
            probe_entered = asyncio.Event()

            async def hang_lookup(prompt: str, scope_key: str):
                probe_entered.set()
                await asyncio.sleep(10)
                return None

            self.backend.lookup.side_effect = hang_lookup
            cancelled_probe = asyncio.create_task(
                cache.invoke(
                    "probe-cancel", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True
                )
            )
            await probe_entered.wait()
            cancelled_probe.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled_probe
            self.assertFalse(cache._circuit_probe_in_flight)

            self.backend.lookup.side_effect = None
            self.backend.lookup.return_value = CacheCandidate("q-ok", "ok-after-cancel", SCOPE.key)
            recovered = await cache.invoke(
                "q-ok", scope=SCOPE, generate=self.generate, can_reuse=self.validate, eligible=True
            )
            self.assertTrue(recovered.cache_hit)
            self.assertFalse(cache.circuit_open)

    async def test_malformed_backend_candidate_fails_open_and_logs_without_pii(self):
        cache = SemanticResponseCache(self.backend, enabled=True, timeout=0.5)
        for bad_candidate in ("not-a-candidate", CacheCandidate("secret-user-prompt-xyz", "   ", SCOPE.key)):
            self.backend.lookup.return_value = bad_candidate
            with patch("openjiuwen.extensions.semantic_cache.cache.llm_logger") as mock_logger:
                res = await cache.invoke(
                    "secret-user-prompt-xyz",
                    scope=SCOPE,
                    generate=AsyncMock(return_value=GeneratedAnswer("secret-response-abc", reusable=False)),
                    can_reuse=self.validate,
                    eligible=True,
                )
            self.assertFalse(res.cache_hit)
            self.assertEqual(res.text, "secret-response-abc")
            self.assertEqual(mock_logger.warning.call_count, 1)
            args = mock_logger.warning.call_args.args
            rendered = args[0] % args[1:]
            self.assertIn("TypeError", rendered)
            self.assertIn(SCOPE.key[:12], rendered)
            self.assertNotIn("secret-user-prompt-xyz", rendered)
            self.assertNotIn("secret-response-abc", rendered)
        self.assertEqual(cache.stats.backend_errors, 2)

    async def test_coalesce_skips_queued_lookups_and_keeps_in_flight_until_store_finishes(self):
        gen_entered = asyncio.Event()
        release_gen = asyncio.Event()
        store_entered = asyncio.Event()
        release_store = asyncio.Event()
        gen_calls = 0

        async def controlled_generate(prompt: str) -> GeneratedAnswer:
            nonlocal gen_calls
            gen_calls += 1
            gen_entered.set()
            await release_gen.wait()
            return GeneratedAnswer(f"ans:{prompt}", reusable=True)

        async def controlled_store(_candidate: CacheCandidate) -> None:
            store_entered.set()
            await release_store.wait()

        self.backend.store.side_effect = controlled_store
        cache = SemanticResponseCache(
            self.backend,
            enabled=True,
            timeout=1.0,
            coalesce_misses=True,
            max_concurrent_backend=1,
        )
        leader = asyncio.create_task(
            cache.invoke("hot-q", scope=SCOPE, generate=controlled_generate, can_reuse=self.validate, eligible=True)
        )
        await gen_entered.wait()
        self.assertEqual(self.backend.lookup.await_count, 1)

        during_gen = asyncio.create_task(
            cache.invoke("hot-q", scope=SCOPE, generate=controlled_generate, can_reuse=self.validate, eligible=True)
        )
        await asyncio.sleep(0.005)
        self.assertEqual(self.backend.lookup.await_count, 1)

        release_gen.set()
        await store_entered.wait()
        during_gen_res = await during_gen
        self.assertTrue(during_gen_res.cache_hit)
        self.assertEqual(during_gen_res.text, "ans:hot-q")
        self.assertFalse(leader.done())

        during_store_res = await cache.invoke(
            "hot-q", scope=SCOPE, generate=controlled_generate, can_reuse=self.validate, eligible=True
        )
        self.assertTrue(during_store_res.cache_hit)
        self.assertEqual(during_store_res.text, "ans:hot-q")
        self.assertEqual(self.backend.lookup.await_count, 1)

        release_store.set()
        leader_res = await leader
        self.assertFalse(leader_res.cache_hit)
        self.assertEqual(leader_res.text, "ans:hot-q")
        self.assertEqual(gen_calls, 1)
        self.assertEqual(cache.stats.coalesced_hits, 2)
        self.assertEqual(len(cache._in_flight), 0)


if __name__ == "__main__":
    unittest.main()
