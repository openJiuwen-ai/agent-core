from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from openjiuwen.symphony.retrieval.build.tree.context import TreeBuildState
from openjiuwen.symphony.retrieval.build.tree.grouping import TreeGroupingEngine
from openjiuwen.symphony.retrieval.build.tree.llm_runtime import TreeLLMRuntime
from openjiuwen.symphony.retrieval.build.tree.schema import DynamicTreeConfig


class _FakeGroupingBuilder:
    def __init__(self) -> None:
        self.config = DynamicTreeConfig(branching_factor=3)
        self.settings = SimpleNamespace(
            deterministic_prompts=True,
            discovery_seed=123,
            manager_config=SimpleNamespace(build=SimpleNamespace(classify_batch_cap=2)),
        )
        self.max_workers = 4
        self.llm_runtime = self
        self.batch_size = 2
        self.responses: list[dict] = []
        self.prompts: list[str] = []

    def auto_batch_size(self) -> int:
        return self.batch_size

    def call_llm_json(self, prompt: str, **kwargs) -> dict:
        del kwargs
        self.prompts.append(prompt)
        if self.responses:
            return self.responses.pop(0)
        skill_ids = re.findall(r"- ([a-z0-9-]+):", prompt)
        return {"assignments": {skill_id: "dev-tools" for skill_id in skill_ids}}


class TreeGroupingEngineTests(unittest.TestCase):
    def test_deterministic_ordering_formatting_and_sampling_seed(self) -> None:
        builder = _FakeGroupingBuilder()
        engine = TreeGroupingEngine(builder)
        skills = [
            {"id": "beta", "name": "Beta", "description": "b" * 180},
            {"id": "alpha", "name": "Alpha", "description": "short"},
        ]

        formatted = engine.format_skills_list(skills)
        seed_one = engine.sampling_seed({"name": "Parent", "description": "Scope"}, len(skills))
        seed_two = engine.sampling_seed({"name": "Parent", "description": "Scope"}, len(skills))

        self.assertLess(formatted.index("- alpha"), formatted.index("- beta"))
        self.assertIn("...", formatted)
        self.assertEqual(seed_one, seed_two)
        self.assertEqual(list(group_id for group_id, _ in engine.iter_group_items({"z": {}, "a": {}})), ["a", "z"])

    def test_classify_skills_single_cleans_group_and_skill_ids(self) -> None:
        builder = _FakeGroupingBuilder()
        builder.responses.append(
            {
                "assignments": {
                    "alpha": "dev_tools",
                    "beta": "Research",
                    "ghost": "research",
                    "gamma": "unknown",
                }
            }
        )
        engine = TreeGroupingEngine(builder)

        assignments = engine.classify_skills_single(
            [{"id": "alpha"}, {"id": "beta"}, {"id": "gamma"}],
            {"dev-tools": {"name": "Dev Tools"}, "research": {"name": "Research"}},
        )

        self.assertEqual(assignments, {"alpha": "dev-tools", "beta": "research"})

    def test_validate_and_recover_retries_small_missing_set_then_falls_back(self) -> None:
        builder = _FakeGroupingBuilder()
        engine = TreeGroupingEngine(builder)
        groups = {"dev-tools": {"name": "Dev"}, "research": {"name": "Research"}}

        builder.responses.append({"assignments": {"delta": "research"}})
        recovered = engine.validate_and_recover(
            [{"id": "alpha"}, {"id": "beta"}, {"id": "gamma"}, {"id": "delta"}],
            groups,
            {"alpha": "dev-tools", "beta": "dev-tools", "gamma": "research"},
        )
        fallback = engine.validate_and_recover(
            [{"id": "alpha"}, {"id": "beta"}, {"id": "gamma"}],
            groups,
            {"alpha": "dev-tools"},
        )

        self.assertEqual(recovered["delta"], "research")
        self.assertEqual(fallback, {"alpha": "dev-tools", "beta": "dev-tools", "gamma": "dev-tools"})

    def test_split_and_batched_classification_build_group_payloads(self) -> None:
        builder = _FakeGroupingBuilder()
        builder.responses.extend(
            [
                {
                    "groups": {
                        "dev-tools": {"name": "Dev", "description": "Development"},
                        "research": {"name": "Research", "description": "Research work"},
                    }
                },
                {"assignments": {"alpha": "dev-tools", "beta": "research"}},
            ]
        )
        engine = TreeGroupingEngine(builder)

        split = engine.split_skills_single(
            [{"id": "alpha", "name": "Alpha"}, {"id": "beta", "name": "Beta"}],
            {"name": "Parent", "description": "Parent scope"},
        )
        batched = engine.classify_skills(
            [{"id": "alpha"}, {"id": "beta"}, {"id": "gamma"}],
            {"dev-tools": {"name": "Dev"}},
        )

        self.assertEqual(split["dev-tools"]["skill_ids"], ["alpha"])
        self.assertEqual(split["research"]["skill_ids"], ["beta"])
        self.assertEqual(batched, {"alpha": "dev-tools", "beta": "dev-tools", "gamma": "dev-tools"})

    def test_sample_discovery_and_merge_helpers(self) -> None:
        builder = _FakeGroupingBuilder()
        builder.responses.append(
            {
                "canonical_groups": {
                    "dev-tools": {
                        "name": "Development",
                        "description": "Dev work",
                        "select_when": "Use for coding.",
                        "dont_select_when": "Avoid research.",
                    }
                }
            }
        )
        engine = TreeGroupingEngine(builder)
        skills = [{"id": f"skill-{index}", "name": f"Skill {index}"} for index in range(8)]

        samples = getattr(engine, "_sample_batches")(skills, {"name": "Parent"}, batch_size=2)
        merged = engine.merge_group_definitions([{"a": {"name": "A"}}, {"b": {"name": "B"}}], verbose=True)
        context = getattr(engine, "_render_context")({"name": "Parent", "description": "Scope"})
        rendered = getattr(engine, "_render_group_definition_samples")(
            [{"dev": {"name": "Dev", "description": "Code"}}]
        )

        self.assertEqual(len(samples), 4)
        self.assertEqual(merged["dev-tools"]["select_when"], "Use for coding.")
        self.assertIn('under "Parent"', context)
        self.assertIn("Discovery Pass 1", rendered)
        self.assertEqual(getattr(engine, "_normalize_group_id")("DEV_TOOLS", {"dev-tools"}), "dev-tools")
        self.assertEqual(
            getattr(engine, "_largest_group_id")({"a": {}, "b": {}}, {"x": "a", "y": "a", "z": "b"}),
            "a",
        )


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str, *, finish_reason: str = "stop") -> None:
        self.finish_reason = finish_reason
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str, *, finish_reason: str = "stop", metadata: dict | None = None) -> None:
        self.choices = [_FakeChoice(content, finish_reason=finish_reason)]
        self._hidden_params = metadata or {}

    @staticmethod
    def model_dump() -> dict:
        return {"response_metadata": {"cached": "yes"}}


class _FakeCompletions:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeClient:
    def __init__(self, results: list[object]) -> None:
        self.completions = _FakeCompletions(results)
        self.chat = SimpleNamespace(completions=self.completions)


def _runtime_builder(client=None, *, model: str = "gpt-5-mini"):
    build_config = SimpleNamespace(
        context_window=0,
        max_output_tokens=0,
        timeout=30,
        num_retries=1,
    )
    settings = SimpleNamespace(
        manager_config=SimpleNamespace(build=build_config),
        llm_seed=7,
        prompt_fingerprint_version="v1",
        cache_observability=True,
        max_consecutive_failures=3,
    )
    state = TreeBuildState(client=client, max_workers=1)
    return SimpleNamespace(
        PROMPT_OVERHEAD_TOKENS=3000,
        OUTPUT_RESERVE_TOKENS=4000,
        AVG_TOKENS_PER_SKILL=75,
        DEFAULT_CONTEXT_WINDOW=128000,
        DEFAULT_MAX_OUTPUT_TOKENS=32768,
        model=model,
        settings=settings,
        state=state,
    )


class TreeLLMRuntimeTests(unittest.TestCase):
    def test_model_limits_batch_size_output_tokens_and_fingerprint(self) -> None:
        builder = _runtime_builder(model="gpt-4o")
        runtime = TreeLLMRuntime(builder)

        self.assertEqual(runtime.model_limits(), (128000, 32768))
        self.assertEqual(runtime.auto_batch_size(), 1000)
        self.assertEqual(runtime.get_max_output_tokens(), 4096)
        self.assertEqual(runtime.merged_extra_body()["seed"], 7)
        self.assertEqual(runtime.normalize_prompt_for_fingerprint(" a \r\nb  "), "a\nb")
        self.assertEqual(len(runtime.prompt_fingerprint("prompt")), 16)

        build_config = builder.settings.manager_config.build
        build_config.context_window = 4096
        build_config.max_output_tokens = 512
        builder.state.batch_size_cache = None
        builder.state.max_output_tokens_cache = None
        self.assertEqual(runtime.model_limits(), (4096, 512))
        self.assertEqual(runtime.get_max_output_tokens(), 512)

    def test_cache_metadata_extraction_and_recording(self) -> None:
        builder = _runtime_builder()
        runtime = TreeLLMRuntime(builder)

        self.assertTrue(runtime.extract_cache_hit(_FakeResponse("{}", metadata={"nested": {"cache_hit": "hit"}})))
        self.assertTrue(runtime.extract_cache_hit_from_mapping({"x-litellm-cache-hit": "true"}))
        self.assertFalse(runtime.extract_cache_hit_from_mapping({"cached": "no"}))
        self.assertIsNone(runtime.extract_cache_hit_from_mapping({"cached": "maybe"}))
        runtime.record_cache_observation(True)
        runtime.record_cache_observation(False)
        runtime.record_cache_observation(None)

        self.assertEqual(
            (
                builder.state.cache_hits,
                builder.state.cache_misses,
                builder.state.cache_unknown,
            ),
            (1, 1, 1),
        )

    def test_call_llm_success_retry_json_and_truncation(self) -> None:
        client = _FakeClient(
            [
                _FakeResponse("[]"),
                _FakeResponse('{"ok": true}'),
                _FakeResponse("[]", finish_reason="length"),
            ]
        )
        builder = _runtime_builder(client=client)
        runtime = TreeLLMRuntime(builder)

        parsed = runtime.call_llm_json("json please", max_retries=2)
        truncated = runtime.call_llm("long please")

        self.assertEqual(parsed, {"ok": True})
        self.assertEqual(truncated, "[]")
        self.assertTrue(builder.state.thread_local.truncated)
        self.assertEqual(builder.state.llm_calls, 3)
        self.assertEqual(builder.state.retry_calls, 1)
        self.assertEqual(client.completions.calls[0]["extra_body"]["seed"], 7)

    def test_call_llm_handles_missing_client_context_and_timeout_retry(self) -> None:
        missing_client_builder = _runtime_builder(client=None)
        with self.assertRaisesRegex(RuntimeError, "openai is required"):
            TreeLLMRuntime(missing_client_builder).call_llm("prompt")

        context_builder = _runtime_builder(client=_FakeClient([ValueError("maximum context length exceeded")]))
        context_builder.state.batch_size_cache = 100
        self.assertEqual(TreeLLMRuntime(context_builder).call_llm("prompt"), "{}")
        self.assertEqual(context_builder.state.batch_size_cache, 50)

        retry_client = _FakeClient([RuntimeError("timeout while calling model"), _FakeResponse('{"after": "retry"}')])
        retry_builder = _runtime_builder(client=retry_client)
        retry_result = TreeLLMRuntime(retry_builder).call_llm("prompt", retry_left=1)
        self.assertEqual(retry_result, '{"after": "retry"}')
        self.assertEqual(retry_builder.state.retry_calls, 1)


if __name__ == "__main__":
    unittest.main()
