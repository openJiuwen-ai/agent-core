"""Unit tests for the refactored retrieval search API."""

from __future__ import annotations

import unittest
from typing import Any, Sequence
from unittest.mock import MagicMock, patch

from openjiuwen.symphony.retrieval.common.models import RetrieverChoice, RetrieverNode, RetrieverTraceEvent
from openjiuwen.symphony.retrieval.llm import (
    LLMClientCapabilities,
    OpenAIClientConfig,
    ProgressiveLLMClient,
    TransformersClientConfig,
    VLLMClientConfig,
)
from openjiuwen.symphony.retrieval.search.artifacts.loading import CatalogRecord, LoadedRetrieverIndex
from openjiuwen.symphony.retrieval.search.runtime.types import ProgressiveRetrieverConfig
from openjiuwen.symphony.retrieval.search.service.defaults import serialize_hit_summary, serialize_trace_event
from openjiuwen.symphony.retrieval.search.service.models import (
    GenerationConfig,
    RenderConfig,
    RequestConfig,
    RetrieverConfig,
    SearchResult,
    TraversalConfig,
    _RuntimeRetrieverConfig,
    runtime_retriever_config_from_config,
)
from openjiuwen.symphony.retrieval.search.service.retriever import (
    Retriever,
    _coerce_llm_client,
    _coerce_retriever_config,
    _progressive_fixed_prefix_cache_requested,
    _progressive_model_name,
    _progressive_runtime_log_identity,
    _progressive_search_backend_name,
    _resolve_request_top_k,
    _validate_search_request_config,
)


def _make_loaded_index(
    *,
    catalog_records: Sequence[CatalogRecord] = (),
    tree_root: RetrieverNode | None = None,
) -> LoadedRetrieverIndex:
    return LoadedRetrieverIndex(
        index_dir="/tmp/fake",
        tree_root=tree_root or RetrieverNode(node_id="ROOT", label="ROOT"),
        choices=tuple(
            RetrieverChoice(choice_id=r.choice_id, payload=r.payload, description=r.description)
            for r in catalog_records
        ),
        catalog_records=tuple(catalog_records),
    )


def _make_llm_client(*, completion: bool = True) -> MagicMock:
    client = MagicMock(spec=ProgressiveLLMClient)
    client.capabilities = LLMClientCapabilities(
        completion=completion,
        streaming=False,
        candidate_scoring=False,
    )
    client.name = "mock"
    return client


def _private_attr(obj: Any, name: str) -> Any:
    return getattr(obj, name)


def _private_call(obj: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(obj, name)(*args, **kwargs)


def _sample_result(
    *,
    method: str = "progressive",
    n: int = 3,
    elapsed_ms: float = 10.0,
) -> SearchResult:
    records = [
        {
            "rank": i,
            "raw_output": f"choice_{i}",
            "resolved_payload": f"payload_{i}",
            "valid": True,
            "selected": i == 1,
            "choice_id": f"choice_{i}",
            "description": f"desc_{i}",
            "score": 1.0 - i * 0.1,
            "source": "progressive",
        }
        for i in range(1, n + 1)
    ]
    return SearchResult(
        method=method,
        payloads=[str(r["resolved_payload"]) for r in records],
        candidate_records=records,
        summary_lines=[f"{i}. payload_{i}" for i in range(1, n + 1)],
        selected_payload="payload_1" if records else None,
        selected_rank=1 if records else -1,
        elapsed_ms=elapsed_ms,
    )


class DefaultsTests(unittest.TestCase):
    def test_serialize_trace_event(self) -> None:
        event = RetrieverTraceEvent(event_type="select", node_id="node.1", depth=2, detail={"key": "val"})
        result = serialize_trace_event(event)
        self.assertEqual(result["event_type"], "select")
        self.assertEqual(result["node_id"], "node.1")
        self.assertEqual(result["depth"], 2)
        self.assertEqual(result["detail"], {"key": "val"})

    def test_serialize_hit_summary_coerces_types(self) -> None:
        result = serialize_hit_summary(42, 99, "5", "0.1")
        self.assertEqual(result, {"choice_id": "42", "payload": "99", "rank": 5, "score": 0.1})


class ModelsTests(unittest.TestCase):
    def test_public_config_defaults(self) -> None:
        cfg = RetrieverConfig()
        self.assertEqual(cfg.top_k, 10)
        self.assertIsInstance(cfg.llm_client_config, OpenAIClientConfig)
        self.assertIsInstance(cfg.traversal_config, TraversalConfig)
        self.assertIsInstance(cfg.render_config, RenderConfig)
        self.assertIsInstance(cfg.generation_config, GenerationConfig)

    def test_request_config_defaults(self) -> None:
        self.assertIsNone(RequestConfig().top_k)

    def test_search_result_defaults(self) -> None:
        result = SearchResult(
            method="progressive",
            payloads=["a"],
            candidate_records=[{"rank": 1}],
            summary_lines=["1. a"],
            selected_payload="a",
            selected_rank=1,
        )
        self.assertEqual(result.elapsed_ms, 0.0)
        self.assertEqual(result.trace_events, [])

    def test_runtime_config_maps_refactored_sub_configs(self) -> None:
        cfg = RetrieverConfig(
            top_k=5,
            llm_client_config=TransformersClientConfig(
                backend="transformers",
                model_path="/models/score",
                tokenizer_path="/models/tokenizer",
            ),
            traversal_config=TraversalConfig(
                max_branch_choices=8,
                max_parallel_branches=2,
                enable_parallel_branches=False,
                branch_choice_slack=4,
                branch_candidate_slack=3,
                round_robin_branch_reduce=False,
            ),
            render_config=RenderConfig(
                compact_codes_enabled=False,
                flatten_tree=False,
                max_exposure_depth=3,
                exposure_threshold=7,
            ),
            generation_config=GenerationConfig(
                mode="logit_selection",
                max_tokens=64,
                request_timeout_seconds=9.5,
                trie_constrained_decoding_enabled=True,
                logit_require_single_token_codes=False,
                logit_return_probabilities=False,
                logit_fallback_mode="generate",
                logit_max_candidates=12,
                logit_min_probability=0.2,
                logit_trace_top_n=4,
            ),
        )

        runtime = runtime_retriever_config_from_config(cfg)

        self.assertIsInstance(runtime, _RuntimeRetrieverConfig)
        self.assertEqual(runtime.top_k, 5)
        progressive = runtime.progressive
        self.assertEqual(progressive.top_k, 5)
        self.assertEqual(progressive.llm_client_config, cfg.llm_client_config)
        self.assertEqual(progressive.max_tokens, 64)
        self.assertTrue(progressive.trie_constrained_decoding_enabled)
        self.assertEqual(progressive.max_branch_choices, 8)
        self.assertEqual(progressive.max_parallel_branches, 2)
        self.assertFalse(progressive.enable_parallel_branches)
        self.assertEqual(progressive.branch_choice_slack, 4)
        self.assertEqual(progressive.branch_candidate_slack, 3)
        self.assertFalse(progressive.round_robin_branch_reduce)
        self.assertFalse(progressive.compact_boundary_codes_enabled)
        self.assertFalse(progressive.flatten_full_tree_in_prompt)
        self.assertEqual(progressive.max_exposure_depth_per_call, 3)
        self.assertEqual(progressive.exposure_threshold, 7)
        self.assertEqual(progressive.selection_mode, "logit_selection")
        self.assertFalse(progressive.scoring_require_single_token_codes)
        self.assertFalse(progressive.scoring_return_probabilities)
        self.assertEqual(progressive.scoring_fallback_mode, "generate")
        self.assertEqual(progressive.scoring_max_candidates, 12)
        self.assertEqual(progressive.scoring_min_probability, 0.2)
        self.assertEqual(progressive.scoring_trace_top_n, 4)

    def test_runtime_config_clamps_numeric_boundaries(self) -> None:
        runtime = runtime_retriever_config_from_config(
            RetrieverConfig(
                top_k=0,
                traversal_config=TraversalConfig(
                    max_branch_choices=0,
                    max_parallel_branches=0,
                    branch_choice_slack=-3,
                    branch_candidate_slack=-2,
                ),
                render_config=RenderConfig(max_exposure_depth=-1, exposure_threshold=-5),
                generation_config=GenerationConfig(max_tokens=0, logit_max_candidates=0, logit_trace_top_n=0),
            )
        )

        self.assertEqual(runtime.top_k, 1)
        self.assertEqual(runtime.progressive.max_tokens, 1)
        self.assertEqual(runtime.progressive.max_branch_choices, 1)
        self.assertEqual(runtime.progressive.max_parallel_branches, 1)
        self.assertEqual(runtime.progressive.branch_choice_slack, 0)
        self.assertEqual(runtime.progressive.branch_candidate_slack, 0)
        self.assertEqual(runtime.progressive.max_exposure_depth_per_call, 0)
        self.assertEqual(runtime.progressive.exposure_threshold, 0)
        self.assertEqual(runtime.progressive.scoring_max_candidates, 1)
        self.assertEqual(runtime.progressive.scoring_trace_top_n, 1)

    def test_empty_generation_mode_defaults_to_generate(self) -> None:
        runtime = runtime_retriever_config_from_config(RetrieverConfig(generation_config=GenerationConfig(mode="")))
        self.assertEqual(runtime.progressive.selection_mode, "generate")


class RetrieverHelperTests(unittest.TestCase):
    def test_coerce_retriever_config_none_uses_defaults(self) -> None:
        runtime = _coerce_retriever_config(None)
        self.assertIsInstance(runtime, _RuntimeRetrieverConfig)
        self.assertEqual(runtime.top_k, 10)

    def test_coerce_retriever_config_converts_public_config(self) -> None:
        runtime = _coerce_retriever_config(RetrieverConfig(top_k=3))
        self.assertIsInstance(runtime, _RuntimeRetrieverConfig)
        self.assertEqual(runtime.top_k, 3)

    def test_coerce_retriever_config_invalid_type_raises(self) -> None:
        with self.assertRaises(TypeError):
            _coerce_retriever_config("invalid")

    def test_resolve_request_top_k(self) -> None:
        runtime = runtime_retriever_config_from_config(RetrieverConfig(top_k=10))
        self.assertEqual(_resolve_request_top_k(runtime_config=runtime, search_config=None), 10)
        self.assertEqual(_resolve_request_top_k(runtime_config=runtime, search_config=RequestConfig(top_k=3)), 3)
        self.assertEqual(_resolve_request_top_k(runtime_config=runtime, search_config=RequestConfig(top_k=0)), 1)
        with self.assertRaises(TypeError):
            _resolve_request_top_k(runtime_config=runtime, search_config="bad")

    def test_validate_top_k_rejects_prefix_cache_override(self) -> None:
        runtime = _RuntimeRetrieverConfig(
            top_k=10,
            progressive=ProgressiveRetrieverConfig(
                top_k=10,
                llm_client_config=TransformersClientConfig(
                    backend="transformers_prefix_cached",
                    model_path="/models/gen",
                ),
            ),
        )
        with self.assertRaises(ValueError):
            _validate_search_request_config(runtime_config=runtime, request_top_k=5)

    def test_validate_top_k_allows_same_value_and_non_prefix_backend(self) -> None:
        runtime = runtime_retriever_config_from_config(RetrieverConfig(top_k=10))
        _validate_search_request_config(runtime_config=runtime, request_top_k=10)
        _validate_search_request_config(runtime_config=runtime, request_top_k=5)

    def test_progressive_backend_helpers_use_llm_client_config(self) -> None:
        transformers = ProgressiveRetrieverConfig(
            llm_client_config=TransformersClientConfig(
                backend="transformers_prefix_cached",
                model_path="/models/gen",
                tokenizer_path="/models/tok",
            )
        )
        vllm = ProgressiveRetrieverConfig(
            llm_client_config=VLLMClientConfig(
                model_path="/models/vllm",
                tokenizer_path="/models/vllm-tokenizer",
            )
        )
        openai_logit = ProgressiveRetrieverConfig(selection_mode="logit_selection")

        self.assertTrue(_progressive_fixed_prefix_cache_requested(transformers))
        self.assertEqual(_progressive_model_name("fallback", transformers), "/models/gen")
        self.assertEqual(
            _progressive_runtime_log_identity(transformers),
            ("transformers_prefix_cached", "/models/gen", "/models/tok"),
        )
        self.assertEqual(_progressive_search_backend_name(vllm), "vllm")
        self.assertEqual(_progressive_search_backend_name(openai_logit), "openai")
        self.assertEqual(_progressive_model_name("fallback", ProgressiveRetrieverConfig()), "fallback")


class RetrieverInstanceTests(unittest.TestCase):
    def test_catalog_records_build_public_lookup_maps(self) -> None:
        records = [
            CatalogRecord(
                choice_id="c1",
                payload="p1",
                name="Skill A",
                description="desc A",
                worker_id="w1",
            ),
        ]
        r = Retriever(loaded_index=_make_loaded_index(catalog_records=records), config=RetrieverConfig())

        self.assertEqual(_private_attr(r, "_public_name_by_payload")["p1"], "Skill A")
        self.assertEqual(_private_attr(r, "_public_name_by_choice_id")["c1"], "Skill A")
        self.assertEqual(_private_attr(r, "_worker_id_by_payload")["p1"], "w1")
        self.assertEqual(_private_attr(r, "_description_by_payload")["p1"], "desc A")

    def test_catalog_record_worker_id_falls_back_to_metadata(self) -> None:
        records = [
            CatalogRecord(choice_id="c1", payload="p1", name="S", worker_id="", metadata={"worker_id": "wm"}),
        ]
        r = Retriever(loaded_index=_make_loaded_index(catalog_records=records), config=RetrieverConfig())
        self.assertEqual(_private_attr(r, "_worker_id_by_payload")["p1"], "wm")

    def test_progressive_unavailable_reason_paths(self) -> None:
        r = Retriever(loaded_index=_make_loaded_index(), config=RetrieverConfig(), llm=None, llm_model="")
        self.assertEqual(_private_call(r, "_progressive_unavailable_reason", None), "llm client is unavailable")

        runtime = runtime_retriever_config_from_config(RetrieverConfig())
        self.assertIn("logit selection is disabled", _private_call(r, "_progressive_unavailable_reason", runtime))

        logit_runtime = _RuntimeRetrieverConfig(
            top_k=5,
            progressive=ProgressiveRetrieverConfig(
                selection_mode="logit_selection",
                compact_boundary_codes_enabled=True,
                llm_client_config=TransformersClientConfig(backend="transformers", model_path="/models/score"),
            ),
        )
        self.assertIsNone(_private_call(r, "_progressive_unavailable_reason", logit_runtime))

    def test_llm_available_makes_progressive_available(self) -> None:
        llm = _make_llm_client(completion=True)
        r = Retriever(loaded_index=_make_loaded_index(), config=RetrieverConfig(), llm=llm, llm_model="gpt-4")
        runtime = runtime_retriever_config_from_config(RetrieverConfig())
        self.assertIsNone(_private_call(r, "_progressive_unavailable_reason", runtime))
        self.assertTrue(_private_call(r, "_can_run_progressive", runtime))

    def test_candidate_record_normalization_and_publicize(self) -> None:
        records = [CatalogRecord(choice_id="c1", payload="p1", name="Skill A", worker_id="w1", description="desc")]
        r = Retriever(loaded_index=_make_loaded_index(catalog_records=records), config=RetrieverConfig())
        normalized = _private_call(
            Retriever,
            "_normalize_candidate_records",
            [{"raw_output": "c1", "resolved_payload": "p1"}],
            source="unit",
        )
        public = _private_call(r, "_publicize_candidate_record", normalized[0])

        self.assertEqual(normalized[0]["rank"], 1)
        self.assertEqual(normalized[0]["source"], "unit")
        self.assertEqual(public["resolved_cid"], "p1")
        self.assertEqual(public["resolved_payload"], "w1")
        self.assertEqual(public["skill_name"], "Skill A")
        self.assertEqual(public["description"], "desc")

    def test_dedupe_trim_and_summary(self) -> None:
        records = [
            {"resolved_payload": "a", "worker_id": "w1", "skill_name": "S1", "raw_output": "a", "score": 0.9},
            {"resolved_payload": "a", "worker_id": "w1", "skill_name": "S1", "raw_output": "a", "score": 0.8},
            {"resolved_payload": "b", "worker_id": "w2", "skill_name": "S2", "raw_output": "b", "score": None},
        ]
        deduped = _private_call(Retriever, "_dedupe_public_candidate_records", records)
        trimmed = _private_call(
            Retriever,
            "_trim_public_search_result",
            SearchResult(
                method="progressive",
                payloads=["w1", "w2"],
                candidate_records=deduped,
                summary_lines=[],
                selected_payload="w1",
                selected_rank=1,
            ),
            top_k=1,
        )

        self.assertEqual(len(deduped), 2)
        self.assertEqual(len(trimmed.candidate_records), 1)
        self.assertEqual(trimmed.payloads, ["a"])
        self.assertIn("score=0.9000", trimmed.summary_lines[0])

    def test_search_delegates_to_search_details(self) -> None:
        r = Retriever(loaded_index=_make_loaded_index(), config=RetrieverConfig())
        with patch.object(r, "search_details", return_value=_sample_result(n=2)) as search_details:
            payloads = r.search("query")
        search_details.assert_called_once_with("query", search_config=None)
        self.assertEqual(payloads, ["payload_1", "payload_2"])

    def test_search_details_uses_progressive_path_and_publicizes(self) -> None:
        records = [CatalogRecord(choice_id="choice_1", payload="payload_1", name="Skill A", worker_id="worker.a")]
        r = Retriever(
            loaded_index=_make_loaded_index(catalog_records=records),
            config=RetrieverConfig(top_k=3),
            llm=_make_llm_client(completion=True),
            llm_model="gpt-4",
        )
        with patch.object(r, "_search_progressive", return_value=_sample_result(n=3)) as search_progressive:
            result = r.search_details("query", search_config=RequestConfig(top_k=1))

        search_progressive.assert_called_once()
        self.assertIsInstance(result, SearchResult)
        self.assertEqual(result.payloads, ["worker.a"])
        self.assertEqual(result.selected_payload, "worker.a")

    def test_close_closes_unique_clients_and_clears_caches(self) -> None:
        llm = _make_llm_client()
        r = Retriever(loaded_index=_make_loaded_index(), config=RetrieverConfig(), llm=llm)
        _private_attr(r, "_progressive_runtime_cache")[("same",)] = llm
        _private_attr(r, "_progressive_retriever_cache")[("retriever",)] = MagicMock()

        r.close()

        self.assertEqual(llm.close.call_count, 1)
        self.assertEqual(_private_attr(r, "_progressive_runtime_cache"), {})
        self.assertEqual(_private_attr(r, "_progressive_retriever_cache"), {})

    def test_from_index_loads_index_and_coerces_openai_client(self) -> None:
        fake_index = _make_loaded_index()
        llm = _make_llm_client()
        with patch(
            "openjiuwen.symphony.retrieval.search.service.retriever.load_retriever_index", return_value=fake_index
        ):
            with patch(
                "openjiuwen.symphony.retrieval.search.service.retriever._coerce_llm_client", return_value=llm
            ) as coerce:
                r = Retriever.from_index("/tmp/fake_index", llm_openai_client=llm)

        coerce.assert_called_once_with(llm)
        self.assertIsInstance(r, Retriever)

    def test_debug_event_helpers(self) -> None:
        hook = MagicMock()
        r = Retriever(loaded_index=_make_loaded_index(), config=RetrieverConfig(), debug_event_hook=hook)

        _private_call(r, "_record_debug_event", {"type": "unit"})
        _private_call(r, "_emit_runtime_event", phase="ready")
        _private_call(
            r,
            "_emit_fallback_event",
            requested_method="progressive",
            fallback_method="generate",
            reason="no llm",
        )

        self.assertEqual(hook.call_count, 3)
        self.assertEqual(hook.call_args_list[1].args[0]["type"], "progressive_runtime")
        self.assertEqual(hook.call_args_list[2].args[0]["type"], "retriever_fallback")


class CoerceLlmClientTests(unittest.TestCase):
    def test_coerce_llm_client_delegates_without_rejecting_none(self) -> None:
        self.assertIsNone(_coerce_llm_client(None))


if __name__ == "__main__":
    unittest.main()
