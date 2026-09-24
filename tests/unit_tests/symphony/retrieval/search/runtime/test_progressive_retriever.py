from __future__ import annotations

import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from openjiuwen.symphony.retrieval.common.models import RetrieverItem, RetrieverNode
from openjiuwen.symphony.retrieval.llm import (
    GenerationConfig,
    LLMClientCapabilities,
    ProgressiveLLMClient,
    PromptCacheHint,
)
from openjiuwen.symphony.retrieval.llm.base.scoring import build_candidate_scoring_result, prepare_candidate_token_ids
from openjiuwen.symphony.retrieval.llm.base.tokenization import CandidateCodeTokenizer
from openjiuwen.symphony.retrieval.llm.transformers_logit_selection import TransformersLogitSelectionClient
from openjiuwen.symphony.retrieval.llm.vllm import LocalVLLMClient
from openjiuwen.symphony.retrieval.search.runtime.progressive import ProgressiveRetriever
from openjiuwen.symphony.retrieval.search.runtime.render.disclosure import (
    DisclosureConfig,
    build_disclosure_messages,
    build_disclosure_prompt_parts,
    build_exposed_fragment,
    parse_selected_codes,
)
from openjiuwen.symphony.retrieval.search.runtime.types import ProgressiveRetrieverConfig


class _StaticScoringClient(ProgressiveLLMClient):
    name = "static"

    def __init__(
        self,
        token_map: dict[str, int | None],
        scores: dict[int, float],
        outputs: list[str] | None = None,
    ) -> None:
        self._token_map = dict(token_map)
        self._scores = dict(scores)
        self.calls: list[dict[str, object]] = []
        self.complete_calls: list[dict[str, object]] = []
        self._outputs = list(outputs or [])

    @property
    def capabilities(self) -> LLMClientCapabilities:
        return LLMClientCapabilities(
            completion=bool(self._outputs),
            streaming=False,
            candidate_scoring=True,
            trie_constrained_decoding=True,
        )

    def complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        stop_sequences=None,
        generation_config: GenerationConfig | None = None,
        n: int = 1,
        request_timeout: float | None = None,
    ):
        self.complete_calls.append(
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "stop_sequences": stop_sequences,
                "generation_config": generation_config,
                "n": n,
                "request_timeout": request_timeout,
            }
        )
        index = len(self.complete_calls) - 1
        if index < len(self._outputs):
            return [self._outputs[index]]
        return [""]

    def score_candidate_codes(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        candidate_codes,
        code_to_canonical_id,
        top_k: int | None = None,
        require_single_token_codes: bool = True,
        request_timeout: float | None = None,
    ):
        encoded = {str(code): self._token_map.get(str(code)) for code in candidate_codes}
        tokenization = prepare_candidate_token_ids(
            candidate_codes=tuple(str(code) for code in candidate_codes),
            encoded_codes=encoded,
            require_single_token_codes=require_single_token_codes,
        )
        candidate_token_ids = list(tokenization.candidate_token_ids)
        self.calls.append(
            {
                "model": model,
                "messages": list(messages),
                "candidate_token_ids": list(candidate_token_ids),
                "top_k": top_k or len(candidate_token_ids),
                "request_timeout": request_timeout,
            }
        )
        ranked = [(token_id, float(self._scores[token_id])) for token_id in candidate_token_ids]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return build_candidate_scoring_result(
            tokenization=tokenization,
            scored_pairs=ranked,
            code_to_canonical_id=code_to_canonical_id,
            latency_breakdown={"encode_ms": 0.0, "backend_ms": 0.0, "total_ms": 0.0},
        )


class _QueuedLLM(ProgressiveLLMClient):
    name = "queued_test"

    def __init__(self, outputs: list[str]) -> None:
        self._outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    @property
    def capabilities(self) -> LLMClientCapabilities:
        return LLMClientCapabilities(completion=True, streaming=False, trie_constrained_decoding=True)

    def complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        stop_sequences=None,
        generation_config: GenerationConfig | None = None,
        n: int = 1,
        request_timeout: float | None = None,
    ):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "stop_sequences": stop_sequences,
                "generation_config": generation_config,
                "n": n,
                "request_timeout": request_timeout,
            }
        )
        index = len(self.calls) - 1
        if index < len(self._outputs):
            return [self._outputs[index]]
        return [""]


class _StreamingLLM(ProgressiveLLMClient):
    name = "streaming_test"

    def __init__(self, chunks: list[object]) -> None:
        self._chunks = list(chunks)
        self.calls: list[dict[str, object]] = []

    @property
    def capabilities(self) -> LLMClientCapabilities:
        return LLMClientCapabilities(completion=True, streaming=True, trie_constrained_decoding=True)

    def complete(self, *args, **kwargs):
        raise AssertionError("streaming test should not call complete")

    def stream_complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        stop_sequences=None,
        generation_config: GenerationConfig | None = None,
        request_timeout: float | None = None,
        early_stop=None,
    ):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "stop_sequences": stop_sequences,
                "generation_config": generation_config,
                "request_timeout": request_timeout,
                "early_stop": early_stop,
            }
        )
        for chunk in self._chunks:
            yield chunk


class _UsageChunk(str):
    def __new__(cls, content: str, usage: dict[str, int]):
        obj = str.__new__(cls, content)
        obj.usage = dict(usage)
        return obj


class _BoundarySensitiveTokenizer:
    def __init__(self) -> None:
        self.apply_chat_template_calls: list[dict[str, object]] = []

    def apply_chat_template(self, messages, *, add_generation_prompt=False, tokenize=False, **kwargs):
        self.apply_chat_template_calls.append(
            {
                "messages": list(messages),
                "add_generation_prompt": add_generation_prompt,
                "tokenize": tokenize,
                "kwargs": dict(kwargs),
            }
        )
        if tokenize:
            raise AssertionError("tokenize=True is not expected in this test")
        return "PROMPT:"

    @staticmethod
    def encode(text, add_special_tokens=False):
        del add_special_tokens
        mapping = {
            "Q1": [101],
            "PROMPT:": [11],
            "": [],
            "\n": [12],
            "\nAA": [12, 13],
            "PROMPT:Q1": [21, 22],
            "PROMPT:Q1\n": [21, 22, 12],
            "PROMPT:Q1\nAA": [21, 22, 12, 13],
        }
        return mapping.get(text, [999])


class _ContextAwareTokenizer:
    def __init__(self, token_map: dict[str, int | None]) -> None:
        self.token_map = dict(token_map)
        self.messages: list[dict[str, str]] | None = None

    def encode_many(self, codes, *, messages=None):
        self.messages = list(messages) if messages is not None else None
        return {str(code): self.token_map.get(str(code)) for code in codes}


class _RecordingTemplateTokenizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def apply_chat_template(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": dict(kwargs)})
        raise RuntimeError("stop after recording kwargs")


class _FakeVLLMPromptTokenizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def apply_chat_template(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": dict(kwargs)})
        if kwargs.get("tokenize"):
            return [1]
        return "PROMPT::VISIBLE"

    @staticmethod
    def encode(text, add_special_tokens=False):
        del add_special_tokens
        mapping = {
            "Q1": [101],
            "Q2": [102],
            "Q3": [103],
            "PROMPT::VISIBLE": [1],
            "": [],
            "\n": [2],
            "\nAA": [2, 3],
        }
        for code, token_id in {"Q1": 101, "Q2": 102, "Q3": 103}.items():
            mapping[f"PROMPT::VISIBLE{code}"] = [1, token_id]
            mapping[f"PROMPT::VISIBLE{code}\n"] = [1, token_id, 2]
            mapping[f"PROMPT::VISIBLE{code}\nAA"] = [1, token_id, 2, 3]
        return mapping.get(str(text), [999])


class _FakeSamplingParams:
    def __init__(self, **kwargs) -> None:
        self.kwargs = dict(kwargs)


class _FakeVLLMCompletion:
    text = "Q2"


class _FakeVLLMRequestOutput:
    outputs = [_FakeVLLMCompletion()]


class _FakeLocalVLLMEngine:
    def __init__(self, tokenizer: object | None = None) -> None:
        self.tokenizer = tokenizer or _FakeVLLMPromptTokenizer()
        self.calls: list[dict[str, object]] = []

    def get_tokenizer(self):
        return self.tokenizer

    def generate(self, prompt, sampling_params, prompt_token_ids=None, **kwargs):
        self.calls.append(
            {
                "prompt": prompt,
                "prompt_token_ids": prompt_token_ids,
                "sampling_params": sampling_params,
                "kwargs": dict(kwargs),
            }
        )

        # dispatch 版本期望 async generator，返回 async iterator

        async def _async_gen():
            yield _FakeVLLMRequestOutput()

        return _async_gen()


def _sample_tree() -> RetrieverNode:
    return RetrieverNode(
        node_id="root",
        label="Root",
        children=(
            RetrieverNode(
                node_id="research",
                label="SearchResearch",
                description="Research and search tools",
                children=(
                    RetrieverNode(
                        node_id="literature",
                        label="Literature",
                        items=(
                            RetrieverItem(
                                item_id="arxiv",
                                payload="worker.arxiv",
                                label="arxiv",
                                description="Search arXiv papers",
                            ),
                            RetrieverItem(
                                item_id="semantic-scholar",
                                payload="worker.semantic-scholar",
                                label="semantic-scholar",
                                description="Search papers and citations",
                            ),
                        ),
                    ),
                ),
            ),
            RetrieverNode(
                node_id="writing",
                label="WritingEditing",
                description="Writing and editing tools",
                items=(
                    RetrieverItem(
                        item_id="summary",
                        payload="worker.summary",
                        label="summary",
                        description="Summarize long documents",
                    ),
                ),
            ),
        ),
    )


class DisclosureModuleTests(unittest.TestCase):
    def test_fragment_can_expand_full_tree_when_limits_are_large(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=99,
                exposure_threshold=999,
                compact_boundary_codes_enabled=False,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        self.assertIn("SearchResearch", fragment.rendered_tree)
        self.assertIn("Literature", fragment.rendered_tree)
        self.assertIn("Arxiv", fragment.rendered_tree)
        self.assertIn("SemanticScholar", fragment.rendered_tree)
        self.assertIn("WritingEditing", fragment.rendered_tree)
        self.assertIn("Summary", fragment.rendered_tree)

    def test_flatten_full_tree_prompt_flattens_current_exposed_subtree_only(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=1,
                exposure_threshold=0,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2"),
                flatten_full_tree_in_prompt=True,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        self.assertTrue(fragment.flat_list_mode)
        self.assertNotIn("Category ", fragment.rendered_tree)
        self.assertNotIn("Candidate Q1 | Research", fragment.rendered_tree)
        self.assertNotIn("Candidate Q1 | Literature", fragment.rendered_tree)
        self.assertNotIn("Candidate Q1 | Writing", fragment.rendered_tree)
        rendered_options = json.loads(fragment.rendered_tree)
        self.assertEqual([item["id"] for item in rendered_options], ["Q1", "Q2"])
        self.assertEqual([item["name"] for item in rendered_options], ["Research", "Writing"])
        self.assertTrue(all("category" in item for item in rendered_options))
        self.assertNotIn("Summary", fragment.rendered_tree)
        self.assertNotIn("Arxiv", fragment.rendered_tree)
        self.assertNotIn("SemanticScholar", fragment.rendered_tree)
        self.assertNotIn("Candidate Q1 |", fragment.rendered_tree)
        self.assertIn("Available options 是 JSON 数组", fragment.system_prompt)
        self.assertIn('"id": "ID"', fragment.system_prompt)
        self.assertIn('"category": "CATEGORY"', fragment.system_prompt)
        self.assertIn("CATEGORY", fragment.system_prompt)
        self.assertNotIn("CANDIDATE_TREE", fragment.system_prompt)

    def test_flat_compact_json_uses_skill_name_and_localized_category(self) -> None:
        root = RetrieverNode(
            node_id="ROOT",
            label="ROOT",
            children=(
                RetrieverNode(
                    node_id="LifeServices",
                    label="LifeServices",
                    children=(
                        RetrieverNode(
                            node_id="MapsWeather",
                            label="MapsWeather",
                            items=(
                                RetrieverItem(
                                    item_id="weather",
                                    payload="LifeServices.MapsWeather.Weather",
                                    label="天气查询",
                                    description="查询天气、温度和预报。",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=99,
                exposure_threshold=999,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("AA",),
                flatten_full_tree_in_prompt=True,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        rendered_options = json.loads(fragment.rendered_tree)
        self.assertEqual(rendered_options[0]["name"], "天气查询")
        self.assertEqual(rendered_options[0]["category"], "生活服务 > 地图天气")
        self.assertNotIn("ROOT", rendered_options[0]["category"])

    def test_flat_compact_json_field_order_can_be_overridden(self) -> None:
        root = _sample_tree()
        with patch.dict(
            os.environ,
            {"DEMO_PROGRESSIVE_FLAT_COMPACT_FIELD_ORDER": "id,name,category,description"},
        ):
            fragment = build_exposed_fragment(
                root=root,
                branch_path=(root.node_id,),
                config=DisclosureConfig(
                    max_exposure_depth_per_call=99,
                    exposure_threshold=999,
                    compact_boundary_codes_enabled=True,
                    compact_boundary_codebook=("Q1", "Q2", "Q3"),
                    flatten_full_tree_in_prompt=True,
                ),
                subtree_item_count=lambda node: _count_items(node),
            )

        rendered_options = json.loads(fragment.rendered_tree)
        # dispatch 版本包含 raw_name 字段且默认字段顺序不同
        self.assertIn("id", rendered_options[0])
        self.assertIn("name", rendered_options[0])
        self.assertIn("category", rendered_options[0])
        self.assertIn("description", rendered_options[0])

    def test_flat_candidate_block_can_move_to_user_prefix_with_extra_rules(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=99,
                exposure_threshold=999,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2", "Q3"),
                flatten_full_tree_in_prompt=True,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        with patch.dict(
            os.environ,
            {
                "DEMO_PROGRESSIVE_CANDIDATE_PLACEMENT": "user",
                "DEMO_PROGRESSIVE_PROMPT_EXTRA_RULES": "- Prefer concrete execution skills.",
                "DEMO_PROGRESSIVE_COMPACT_OUTPUT_CONCISE": "1",
            },
        ):
            parts = build_disclosure_prompt_parts(
                fragment=fragment,
                query_messages=[{"role": "user", "content": "find paper tools"}],
                top_k=2,
            )

        # dispatch 版本: prompt 中包含候选列表和输出规则
        all_content = str(parts.full_messages[0]["content"]) + str(parts.full_messages[1]["content"])
        self.assertIn(fragment.rendered_tree, all_content)
        self.assertIn("Available options", all_content)
        self.assertIn("find paper tools", str(parts.full_messages[1]["content"]))

    def test_flat_compact_defaults_use_selected_top2_experiment(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=99,
                exposure_threshold=999,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2", "Q3"),
                flatten_full_tree_in_prompt=True,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        with patch.dict(
            os.environ,
            {
                "DEMO_PROGRESSIVE_CANDIDATE_PLACEMENT": "",
                "DEMO_PROGRESSIVE_COMPACT_OUTPUT_CONCISE": "",
            },
        ):
            parts = build_disclosure_prompt_parts(
                fragment=fragment,
                query_messages=[{"role": "user", "content": "find paper tools"}],
                top_k=2,
            )

        self.assertIn("# 候选列表", parts.full_messages[0]["content"])
        self.assertNotIn("# 候选列表", parts.full_messages[1]["content"])
        self.assertIn("只输出候选", parts.full_messages[0]["content"])

    def test_disclosure_messages_put_query_after_tree_prefix(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=2,
                exposure_threshold=1,
                compact_boundary_codes_enabled=False,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        messages_a = build_disclosure_messages(
            fragment=fragment,
            query_messages=[{"role": "user", "content": "From User: first query"}],
        )
        messages_b = build_disclosure_messages(
            fragment=fragment,
            query_messages=[{"role": "user", "content": "From User: second query"}],
        )

        self.assertEqual(messages_a[0]["content"], messages_b[0]["content"])
        self.assertIn("# 候选列表", str(messages_a[0]["content"]))
        self.assertIn("<CANDIDATE_TREE>", str(messages_a[0]["content"]))
        self.assertIn(fragment.rendered_tree, str(messages_a[0]["content"]))
        self.assertNotIn("<CANDIDATE_TREE>", str(messages_a[1]["content"]))
        self.assertNotIn(fragment.rendered_tree, str(messages_a[1]["content"]))
        self.assertRegex(
            str(messages_a[1]["content"]),
            r"<USER_REQUEST>\n(?:\[req:[0-9a-f]+\])?first query\n</USER_REQUEST>\Z",
        )
        self.assertRegex(
            str(messages_b[1]["content"]),
            r"<USER_REQUEST>\n(?:\[req:[0-9a-f]+\])?second query\n</USER_REQUEST>\Z",
        )
        self.assertEqual(
            str(messages_a[0]["content"]),
            str(messages_b[0]["content"]),
        )
        self.assertNotIn("From User:", str(messages_a[1]["content"]))
        self.assertNotIn("Return only codes", str(messages_a[1]["content"]))
        self.assertNotIn("Only lines marked with [ID: ...] are selectable options.", str(messages_a[1]["content"]))

    def test_flattened_disclosure_messages_request_all_results_in_single_response(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=0,
                exposure_threshold=0,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2", "Q3"),
                flatten_full_tree_in_prompt=True,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        messages = build_disclosure_messages(
            fragment=fragment,
            query_messages=[{"role": "user", "content": "find paper tools"}],
            top_k=3,
        )

        self.assertIn("候选", str(messages[0]["content"]))
        self.assertIn("Available options", str(messages[0]["content"]))
        self.assertNotIn("Available options", str(messages[1]["content"]))
        self.assertIn("find paper tools", str(messages[1]["content"]))

    def test_compact_user_prefix_only_contains_tree_and_request(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=2,
                exposure_threshold=1,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2"),
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        self.assertTrue(fragment.user_prefix.startswith("<CANDIDATE_TREE>\n"))
        self.assertIn(fragment.rendered_tree, fragment.user_prefix)
        self.assertTrue(fragment.user_prefix.endswith("\n\n<USER_REQUEST>\n"))
        self.assertNotIn("Response format example:", fragment.user_prefix)
        self.assertNotIn("Return only IDs", fragment.user_prefix)

    def test_non_compact_system_prompt_restores_name_mode(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=2,
                exposure_threshold=1,
                compact_boundary_codes_enabled=False,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        # dispatch 版本 non-compact prompt 包含更新的规则文本
        self.assertIn("你只做“候选筛选”", fragment.system_prompt)
        self.assertIn("Candidate NAME", fragment.system_prompt)
        self.assertIn("用户的显式约束优先级最高", fragment.system_prompt)
        self.assertIn("务必识别真实隐含意图", fragment.system_prompt)
        self.assertNotIn("如果没有合适的候选", fragment.system_prompt)

    def test_non_compact_rendering_restores_original_inline_format(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=2,
                exposure_threshold=1,
                compact_boundary_codes_enabled=False,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        self.assertIn("- Category Root", fragment.rendered_tree)
        self.assertIn("- Candidate Research: Research and search tools", fragment.rendered_tree)
        self.assertNotIn("Q1 |", fragment.rendered_tree)

    def test_candidate_rendering_strips_representative_descendants_suffix(self) -> None:
        root = RetrieverNode(
            node_id="root",
            label="Root",
            items=(
                RetrieverItem(
                    item_id="general-writing",
                    payload="worker.general-writing",
                    label="general-writing",
                    description="Long-form writing helper.\n\nRepresentative descendants: foo; bar",
                ),
            ),
        )
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=1,
                exposure_threshold=0,
                compact_boundary_codes_enabled=False,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        self.assertIn("Long-form writing helper.", fragment.rendered_tree)
        self.assertNotIn("Representative descendants:", fragment.rendered_tree)

    def test_compact_codebook_assigns_codes_in_given_order(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=2,
                exposure_threshold=1,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2"),
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        self.assertEqual(list(fragment.code_to_resolution.keys()), ["Q1", "Q2"])
        selected = parse_selected_codes(fragment=fragment, output="Q2\nQ1")
        self.assertEqual([item.code for item in selected], ["Q2", "Q1"])
        self.assertEqual(fragment.code_width, 2)
        self.assertIn("<CANDIDATE_TREE> 中的 NAME [id: X] 表示候选 skill，可选择", fragment.system_prompt)
        self.assertIn("默认输出 2 个 Candidate skill 节点 id", fragment.system_prompt)
        self.assertIn("每个非空行必须只包含一个候选 id", fragment.system_prompt)
        self.assertIn("正确输出示例：CC", fragment.system_prompt)
        self.assertIn("用户的显式约束优先级最高", fragment.system_prompt)
        self.assertIn("不能选择 PPT", fragment.system_prompt)
        self.assertNotIn("如果没有合适的候选", fragment.system_prompt)
        self.assertNotIn("Response format example:", fragment.system_prompt)

    def test_compact_code_parser_accepts_copied_tree_lines(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=2,
                exposure_threshold=1,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2"),
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        selected = parse_selected_codes(
            fragment=fragment,
            output="Candidate Q1 | Arxiv",
        )

        self.assertEqual([item.code for item in selected], ["Q1"])

    def test_compact_codebook_requires_enough_codes(self) -> None:
        root = _sample_tree()
        with self.assertRaisesRegex(ValueError, "compact codebook provides 1 codes"):
            build_exposed_fragment(
                root=root,
                branch_path=(root.node_id,),
                config=DisclosureConfig(
                    max_exposure_depth_per_call=2,
                    exposure_threshold=1,
                    compact_boundary_codes_enabled=True,
                    compact_boundary_codebook=("ONLY",),
                ),
                subtree_item_count=lambda node: _count_items(node),
            )

    def test_parse_selected_codes_accepts_boundary_names_when_compact_disabled(self) -> None:
        root = _sample_tree()
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=2,
                exposure_threshold=1,
                compact_boundary_codes_enabled=False,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        selected = parse_selected_codes(fragment=fragment, output="Research")

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].code, "Research")

    def test_non_compact_uses_english_pascal_ids_instead_of_labels(self) -> None:
        root = RetrieverNode(
            node_id="ROOT",
            label="根节点",
            children=(
                RetrieverNode(
                    node_id="market-deep-insight",
                    label="市场深度洞察",
                    description="深度市场分析",
                ),
            ),
        )
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=0,
                exposure_threshold=0,
                compact_boundary_codes_enabled=False,
            ),
            subtree_item_count=lambda node: _count_items(node),
        )

        self.assertIn("MarketDeepInsight", fragment.rendered_tree)
        self.assertNotIn("市场深度洞察 | 市场深度洞察", fragment.rendered_tree)
        selected = parse_selected_codes(fragment=fragment, output="MarketDeepInsight")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].code, "MarketDeepInsight")

    def test_score_fragment_normalizes_over_all_visible_candidates(self) -> None:
        root = RetrieverNode(
            node_id="root",
            label="Root",
            items=(
                RetrieverItem(item_id="a", payload="worker.a", label="A"),
                RetrieverItem(item_id="b", payload="worker.b", label="B"),
                RetrieverItem(item_id="c", payload="worker.c", label="C"),
            ),
        )
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=1,
                exposure_threshold=0,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2", "Q3"),
            ),
            subtree_item_count=lambda node: _count_items(node),
        )
        client = _StaticScoringClient({"Q1": 101, "Q2": 102, "Q3": 103}, {101: 0.0, 102: 1.0, 103: 2.0})
        messages = build_disclosure_messages(fragment=fragment, query_messages=[{"role": "user", "content": "pick c"}])

        result = client.score_candidate_codes(
            model="retriever-model",
            messages=messages,
            candidate_codes=fragment.candidate_codes,
            code_to_canonical_id={
                code: resolution.canonical_id for code, resolution in fragment.code_to_resolution.items()
            },
            top_k=3,
        )
        scores = list(result.scores)

        self.assertEqual(len(scores), 3)
        self.assertEqual(scores[0].code, "Q3")
        self.assertLess(scores[0].probability, 1.0)
        self.assertEqual(client.calls[0]["top_k"], 3)

    def test_score_fragment_rejects_single_token_collisions(self) -> None:
        root = RetrieverNode(
            node_id="root",
            label="Root",
            items=(
                RetrieverItem(item_id="a", payload="worker.a", label="A"),
                RetrieverItem(item_id="b", payload="worker.b", label="B"),
            ),
        )
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=1,
                exposure_threshold=0,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2"),
            ),
            subtree_item_count=lambda node: _count_items(node),
        )
        client = _StaticScoringClient({"Q1": 101, "Q2": 101}, {101: 1.0})

        with self.assertRaisesRegex(Exception, "collide under the scoring tokenizer"):
            client.score_candidate_codes(
                model="retriever-model",
                messages=build_disclosure_messages(
                    fragment=fragment,
                    query_messages=[{"role": "user", "content": "pick a"}],
                ),
                candidate_codes=fragment.candidate_codes,
                code_to_canonical_id={
                    code: resolution.canonical_id for code, resolution in fragment.code_to_resolution.items()
                },
            )


class ProgressiveRetrieverTests(unittest.TestCase):
    def test_transformers_candidate_tokenizer_requires_output_boundary_stability(self) -> None:
        tokenizer = CandidateCodeTokenizer(tokenizer=_BoundarySensitiveTokenizer())

        bare = tokenizer.encode_single_token("Q1")
        contextual = tokenizer.encode_single_token("Q1", messages=[{"role": "user", "content": "pick one"}])

        self.assertEqual(bare, 101)
        self.assertIsNone(contextual)
        self.assertEqual(len(tokenizer.tokenizer.apply_chat_template_calls), 1)
        self.assertEqual(
            tokenizer.tokenizer.apply_chat_template_calls[0]["kwargs"],
            {
                "enable_thinking": False,
                "preserve_thinking": False,
                "add_vision_id": False,
            },
        )

    def test_score_fragment_passes_disclosure_messages_into_candidate_tokenizer(self) -> None:
        root = RetrieverNode(
            node_id="root",
            label="Root",
            items=(
                RetrieverItem(item_id="a", payload="worker.a", label="A"),
                RetrieverItem(item_id="b", payload="worker.b", label="B"),
            ),
        )
        fragment = build_exposed_fragment(
            root=root,
            branch_path=(root.node_id,),
            config=DisclosureConfig(
                max_exposure_depth_per_call=1,
                exposure_threshold=0,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2"),
            ),
            subtree_item_count=lambda node: _count_items(node),
        )
        client = _StaticScoringClient({"Q1": 101, "Q2": 102}, {101: 1.0, 102: 2.0})
        query_messages = [{"role": "user", "content": "pick b"}]
        messages = build_disclosure_messages(fragment=fragment, query_messages=query_messages)

        result = client.score_candidate_codes(
            model="retriever-model",
            messages=messages,
            candidate_codes=fragment.candidate_codes,
            code_to_canonical_id={
                code: resolution.canonical_id for code, resolution in fragment.code_to_resolution.items()
            },
        )

        self.assertEqual(result.scores[0].code, "Q2")
        self.assertEqual(
            client.calls[0]["messages"],
            messages,
        )

    def test_transformers_scoring_backend_disables_thinking_in_chat_template(self) -> None:
        client = TransformersLogitSelectionClient(
            model_obj=object(),
            candidate_tokenizer=CandidateCodeTokenizer(tokenizer=_RecordingTemplateTokenizer()),
        )

        with patch.dict(sys.modules, {"torch": types.SimpleNamespace()}):
            with self.assertRaisesRegex(RuntimeError, "stop after recording kwargs"):
                getattr(client, "_score_token_ids")(
                    messages=[{"role": "user", "content": "pick one"}],
                    candidate_token_ids=[101],
                )

        self.assertEqual(len(client.candidate_tokenizer.tokenizer.calls), 1)
        self.assertEqual(
            client.candidate_tokenizer.tokenizer.calls[0]["kwargs"],
            {
                "add_generation_prompt": True,
                "return_tensors": "pt",
                "tokenize": True,
                "enable_thinking": False,
                "preserve_thinking": False,
                "add_vision_id": False,
            },
        )

    def test_local_vllm_generation_from_pretrained_builds_in_process_engine(self) -> None:
        engine_calls: list[dict[str, object]] = []

        class _FakeAutoTokenizer:
            @staticmethod
            def from_pretrained(path, **kwargs):
                engine_calls.append({"tokenizer_load_path": path, "tokenizer_load_kwargs": dict(kwargs)})
                return _FakeVLLMPromptTokenizer()

        class _FakeAsyncEngineArgs:
            def __init__(self, **kwargs) -> None:
                self.kwargs = dict(kwargs)

        class _FakeAsyncLLMEngine:
            @staticmethod
            def from_engine_args(engine_args):
                engine_calls.append(dict(engine_args.kwargs))
                return _FakeLocalVLLMEngine()

        class _FakeTokensPrompt(dict):
            def __init__(self, *, prompt_token_ids):
                super().__init__(prompt_token_ids=prompt_token_ids)

        with patch.dict(
            sys.modules,
            {
                "transformers": types.SimpleNamespace(AutoTokenizer=_FakeAutoTokenizer),
                "vllm": types.SimpleNamespace(
                    AsyncEngineArgs=_FakeAsyncEngineArgs,
                    AsyncLLMEngine=_FakeAsyncLLMEngine,
                    SamplingParams=_FakeSamplingParams,
                ),
                "vllm.engine.arg_utils": types.SimpleNamespace(AsyncEngineArgs=_FakeAsyncEngineArgs),
                "vllm.engine.async_llm_engine": types.SimpleNamespace(AsyncLLMEngine=_FakeAsyncLLMEngine),
                "vllm.inputs": types.SimpleNamespace(TokensPrompt=_FakeTokensPrompt),
                "vllm.sampling_params": types.SimpleNamespace(SamplingParams=_FakeSamplingParams),
                "vllm.global_consts": types.SimpleNamespace(EngineRole=types.SimpleNamespace(M="M")),
            },
        ):
            client = LocalVLLMClient.from_pretrained(
                model_path="/tmp/mock-model",
                tokenizer_path="/tmp/mock-tokenizer",
                dtype="bfloat16",
                vllm_kwargs={
                    "request_model": "served-model-name",
                    "tensor_parallel_size": 2,
                },
            )

        self.assertIsInstance(client, LocalVLLMClient)
        self.assertEqual(len(engine_calls), 2)
        self.assertEqual(engine_calls[0]["tokenizer_load_path"], "/tmp/mock-tokenizer")
        self.assertEqual(engine_calls[1]["model"], "/tmp/mock-model")
        self.assertEqual(engine_calls[1]["tokenizer"], "/tmp/mock-tokenizer")
        self.assertEqual(engine_calls[1]["dtype"], "bfloat16")
        self.assertEqual(engine_calls[1]["tensor_parallel_size"], 2)
        self.assertEqual(client.model_name, "served-model-name")

    def test_local_vllm_prefix_cache_warmup_and_completion_use_token_ids(self) -> None:
        tokenizer = _FakeVLLMPromptTokenizer()
        engine = _FakeLocalVLLMEngine(tokenizer=tokenizer)
        client = LocalVLLMClient(
            engine=engine,
            model_name="served-model",
            model_path="/tmp/mock-model",
            tokenizer_path="/tmp/mock-tokenizer",
            chat_template_tokenizer=tokenizer,
            sampling_params_cls=_FakeSamplingParams,
            max_new_tokens=3,
        )

        handle = client.prepare_prefix_cache(
            cache_id="cache-1",
            prefix_messages=({"role": "system", "content": "prefix"},),
            prefix_token_hash="hash-1",
        )
        outputs = client.complete(
            model="ignored",
            messages=[],
            max_tokens=2,
            generation_config=GenerationConfig(
                prompt_cache=PromptCacheHint(
                    handle=handle,
                    suffix_text="Q1",
                    suffix_token_ids=None,
                    expected_prefix_len=1,
                )
            ),
        )

        self.assertEqual(outputs, ["Q2"])
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(engine.calls[0]["prompt_token_ids"], [1])
        self.assertEqual(engine.calls[1]["prompt_token_ids"], [1, 101])

    def test_search_recurses_after_branch_selection(self) -> None:
        llm = _QueuedLLM(["IU", "IU\nBK"])
        root = _sample_tree()
        retriever = ProgressiveRetriever(
            llm=llm,
            config=ProgressiveRetrieverConfig(
                top_k=2,
                max_exposure_depth_per_call=1,
                exposure_threshold=0,
                enable_parallel_branches=False,
            ),
        )

        result = retriever.search(model="retriever-model", query="find paper tools", root=root, top_k=2)

        self.assertEqual([item.payload for item in result.candidates], ["worker.arxiv", "worker.semantic-scholar"])
        event_types = [event.event_type for event in result.trace.events]
        self.assertIn("fragment_built", event_types)
        self.assertIn("fragment_selected", event_types)
        self.assertIn("fragment_continue", event_types)
        self.assertEqual(len(llm.calls), 2)

    def test_single_selectable_branch_shortcuts_without_llm(self) -> None:
        llm = _QueuedLLM([])
        root = RetrieverNode(
            node_id="root",
            label="Root",
            children=(
                RetrieverNode(
                    node_id="only",
                    label="Only",
                    items=(RetrieverItem(item_id="only.item", payload="worker.only", label="only"),),
                ),
            ),
        )
        retriever = ProgressiveRetriever(
            llm=llm,
            config=ProgressiveRetrieverConfig(
                top_k=1,
                max_exposure_depth_per_call=0,
                exposure_threshold=0,
            ),
        )

        result = retriever.search(model="retriever-model", query="only", root=root, top_k=1)

        self.assertEqual([item.payload for item in result.candidates], ["worker.only"])
        self.assertEqual(len(llm.calls), 0)

    def test_search_uses_compact_codebook_for_fragment_selection(self) -> None:
        llm = _QueuedLLM(["Y2\nX1"])
        root = RetrieverNode(
            node_id="root",
            label="Root",
            items=(
                RetrieverItem(
                    item_id="arxiv",
                    payload="worker.arxiv",
                    label="arxiv",
                    description="Search arXiv papers",
                ),
                RetrieverItem(
                    item_id="semantic-scholar",
                    payload="worker.semantic-scholar",
                    label="semantic-scholar",
                    description="Search papers and citations",
                ),
            ),
        )
        retriever = ProgressiveRetriever(
            llm=llm,
            config=ProgressiveRetrieverConfig(
                top_k=2,
                max_exposure_depth_per_call=1,
                exposure_threshold=0,
                enable_parallel_branches=False,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("X1", "Y2"),
            ),
        )

        result = retriever.search(model="retriever-model", query="find paper tools", root=root, top_k=2)

        self.assertEqual([item.payload for item in result.candidates], ["worker.semantic-scholar", "worker.arxiv"])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0]["max_tokens"], 3)
        system_content = str(llm.calls[0]["messages"][0]["content"])
        self.assertIn('"raw_name": "Arxiv"', system_content)
        self.assertIn('"id": "X1"', system_content)
        self.assertIn('"raw_name": "SemanticScholar"', system_content)
        self.assertIn('"id": "Y2"', system_content)
        self.assertNotIn('"raw_name": "Arxiv"', str(llm.calls[0]["messages"][1]["content"]))
        self.assertRegex(
            str(llm.calls[0]["messages"][1]["content"]),
            r"User request:\n(?:\[req:[0-9a-f]+\])?find paper tools",
        )

    def test_progressive_streaming_llm_records_ttft_latency(self) -> None:
        llm = _StreamingLLM(
            [
                "Y2",
                "\n",
                "X1",
                _UsageChunk("", {"prompt_tokens": 21, "completion_tokens": 3, "total_tokens": 24}),
            ]
        )
        debug_events: list[dict[str, object]] = []
        root = RetrieverNode(
            node_id="root",
            label="Root",
            items=(
                RetrieverItem(
                    item_id="arxiv",
                    payload="worker.arxiv",
                    label="arxiv",
                    description="Search arXiv papers",
                ),
                RetrieverItem(
                    item_id="semantic-scholar",
                    payload="worker.semantic-scholar",
                    label="semantic-scholar",
                    description="Search papers and citations",
                ),
            ),
        )
        retriever = ProgressiveRetriever(
            llm=llm,
            config=ProgressiveRetrieverConfig(
                top_k=2,
                max_exposure_depth_per_call=1,
                exposure_threshold=0,
                enable_parallel_branches=False,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("X1", "Y2"),
            ),
            debug_event_hook=lambda event: debug_events.append(dict(event)),
        )

        result = retriever.search(model="retriever-model", query="find paper tools", root=root, top_k=2)

        self.assertEqual([item.payload for item in result.candidates], ["worker.semantic-scholar", "worker.arxiv"])
        response_event = next(
            event
            for event in debug_events
            if event.get("type") in {"retriever_io", "finder_io"} and event.get("phase") == "response"
        )
        latency = response_event.get("latency")
        self.assertIsInstance(latency, dict)
        self.assertTrue(latency["stream"])
        self.assertIsInstance(latency["ttft_ms"], float)
        self.assertIsInstance(latency["elapsed_ms"], float)
        self.assertGreaterEqual(latency["elapsed_ms"], latency["ttft_ms"])
        self.assertNotIn("output_chars", latency)
        self.assertEqual(response_event["usage"]["completion_tokens"], 3)

    def test_single_forward_logit_selection_selects_top_visible_candidate_when_enabled(self) -> None:
        client = _StaticScoringClient({"Q1": 101, "Q2": 102}, {101: 1.0, 102: 5.0})
        root = RetrieverNode(
            node_id="root",
            label="Root",
            items=(
                RetrieverItem(item_id="item.a", payload="worker.a", label="A"),
                RetrieverItem(item_id="item.b", payload="worker.b", label="B"),
            ),
        )
        retriever = ProgressiveRetriever(
            llm=client,
            config=ProgressiveRetrieverConfig(
                top_k=1,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2"),
                selection_mode="logit_selection",
            ),
        )

        result = retriever.search(model="retriever-model", query="pick b", root=root, top_k=1)

        self.assertEqual([item.payload for item in result.candidates], ["worker.b"])
        self.assertEqual(len(client.complete_calls), 0)
        self.assertEqual(len(client.calls), 1)
        event_types = [event.event_type for event in result.trace.events]
        self.assertIn("fragment_logit_selection_requested", event_types)
        self.assertIn("fragment_logit_selection_completed", event_types)
        scoring_requested = next(
            event for event in result.trace.events if event.event_type == "fragment_logit_selection_requested"
        )
        scoring_completed = next(
            event for event in result.trace.events if event.event_type == "fragment_logit_selection_completed"
        )
        self.assertEqual(scoring_requested.detail["candidate_codes"], ["Q1", "Q2"])
        self.assertEqual(scoring_requested.detail["candidate_canonical_ids"], ["worker.a", "worker.b"])
        self.assertIn("latency_breakdown", scoring_completed.detail)
        self.assertIn("backend_ms", scoring_completed.detail["latency_breakdown"])

    def test_generate_mode_preserves_generation_flow(self) -> None:
        llm = _QueuedLLM(["Q2"])
        root = RetrieverNode(
            node_id="root",
            label="Root",
            items=(
                RetrieverItem(item_id="item.a", payload="worker.a", label="A"),
                RetrieverItem(item_id="item.b", payload="worker.b", label="B"),
            ),
        )
        retriever = ProgressiveRetriever(
            llm=llm,
            config=ProgressiveRetrieverConfig(
                top_k=1,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2"),
                selection_mode="generate",
            ),
        )

        result = retriever.search(model="retriever-model", query="pick b", root=root, top_k=1)

        self.assertEqual([item.payload for item in result.candidates], ["worker.b"])
        self.assertEqual(len(llm.calls), 1)

    def test_single_forward_logit_selection_falls_back_when_candidate_code_is_not_single_token(self) -> None:
        client = _StaticScoringClient({"Q1": None, "Q2": 102}, {101: 1.0, 102: 5.0}, outputs=["Q2"])
        root = RetrieverNode(
            node_id="root",
            label="Root",
            items=(
                RetrieverItem(item_id="item.a", payload="worker.a", label="A"),
                RetrieverItem(item_id="item.b", payload="worker.b", label="B"),
            ),
        )
        retriever = ProgressiveRetriever(
            llm=client,
            config=ProgressiveRetrieverConfig(
                top_k=1,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2"),
                selection_mode="logit_selection",
                scoring_fallback_mode="generate",
            ),
        )

        result = retriever.search(model="retriever-model", query="pick b", root=root, top_k=1)

        self.assertEqual([item.payload for item in result.candidates], ["worker.b"])
        self.assertEqual(len(client.complete_calls), 1)
        self.assertEqual(len(client.calls), 0)
        self.assertIn("fragment_logit_selection_fallback", [event.event_type for event in result.trace.events])

    def test_single_forward_logit_selection_default_fallback_raises(self) -> None:
        client = _StaticScoringClient({"Q1": None, "Q2": 102}, {101: 1.0, 102: 5.0}, outputs=["Q2"])
        root = RetrieverNode(
            node_id="root",
            label="Root",
            items=(
                RetrieverItem(item_id="item.a", payload="worker.a", label="A"),
                RetrieverItem(item_id="item.b", payload="worker.b", label="B"),
            ),
        )
        retriever = ProgressiveRetriever(
            llm=client,
            config=ProgressiveRetrieverConfig(
                top_k=1,
                compact_boundary_codes_enabled=True,
                compact_boundary_codebook=("Q1", "Q2"),
                selection_mode="logit_selection",
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "single-token"):
            retriever.search(model="retriever-model", query="pick b", root=root, top_k=1)

        self.assertEqual(len(client.complete_calls), 0)


def _count_items(node: RetrieverNode) -> int:
    return len(node.items) + sum(_count_items(child) for child in node.children)


if __name__ == "__main__":
    unittest.main()
