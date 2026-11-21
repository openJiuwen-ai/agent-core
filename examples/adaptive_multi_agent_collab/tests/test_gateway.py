"""Actual public Model + registered gateway client, with an offline HTTP transport."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm.output_parsers.json_output_parser import JsonOutputParser
from openjiuwen.core.foundation.llm.schema.message import SystemMessage, UserMessage

from examples.adaptive_multi_agent_collab.providers.gateway import (
    COLLABORATION_MODELS,
    DISABLED_PLUGINS,
    OPENROUTER_BASE_URL,
    OpenRouterBinding,
    OpenRouterError,
    build_openrouter_model,
)
from examples.adaptive_multi_agent_collab.providers.model_io import invoke_model


def identity_evidence(tmp_path, route):
    canonical = route.model + "-fixture-canonical"
    models = tmp_path / "models.json"
    models.write_text(json.dumps({"data": [{"id": route.model, "canonical_slug": canonical}]}))
    endpoints = tmp_path / "endpoints.json"
    endpoints.write_text(
        json.dumps(
            {
                "data": {
                    "id": route.model,
                    "endpoints": [
                        {
                            "model_id": route.model,
                            "provider_name": route.served_provider,
                            "tag": route.provider_endpoint,
                        }
                    ],
                }
            }
        )
    )
    return {
        "protocol": "documented_accounting_model_identity_1",
        "requested_model": route.model,
        "metadata_model": canonical,
        "served_provider": route.served_provider,
        "provider_endpoint": route.provider_endpoint,
        "model_catalog": {"path": str(models), "sha256": hashlib.sha256(models.read_bytes()).hexdigest()},
        "endpoint_catalog": {"path": str(endpoints), "sha256": hashlib.sha256(endpoints.read_bytes()).hexdigest()},
    }


def test_verified_alias_canonical_receipt_keeps_wire_binding_and_raw_models(tmp_path):
    route = binding()
    evidence = identity_evidence(tmp_path, route)
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, text=sse([frame("yes", finish="stop", native="stop")]))
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "generation-fixture",
                    "model": evidence["metadata_model"],
                    "provider_name": route.served_provider,
                    "native_tokens_prompt": 20,
                    "native_tokens_completion": 4,
                    "native_tokens_cached": 0,
                    "native_tokens_reasoning": 0,
                    "total_cost": 0.0001,
                    "is_byok": False,
                    "finish_reason": "stop",
                    "native_finish_reason": "completed",
                }
            },
        )

    model = build_openrouter_model(
        route,
        "fixture-key",
        temperature=0.7,
        transport=httpx.MockTransport(handle),
        response_identity_evidence=evidence,
    )
    result = invoke(model, seed=7)
    assert [request.method for request in requests] == ["POST", "GET"]
    body = json.loads(requests[0].content)
    assert body["model"] == route.model and body["provider"]["only"] == [route.provider_endpoint]
    assert not any("identity" in key for key in body)
    assert result.usage_metadata["binding"] == route.metadata()
    assert result.usage_metadata["response_identity_verified"] is True
    assert result.served_model == evidence["metadata_model"]
    assert result.usage_metadata["raw_gateway_frames"][0]["model"] == route.model
    assert result.usage_metadata["raw_generation_metadata"]["model"] == evidence["metadata_model"]
    assert result.usage_metadata["response_identity_evidence"] == evidence
    assert len(result.usage_metadata["response_identity_evidence_sha256"]) == 64


@pytest.mark.parametrize("changed", ["model", "provider"])
def test_catalog_identity_does_not_allow_unlisted_snapshot_or_provider(tmp_path, changed):
    route = binding()
    evidence = identity_evidence(tmp_path, route)
    raw = frame("yes", finish="stop", native="stop", model=evidence["metadata_model"])
    raw[changed] = "unapproved"

    def handle(request):
        return httpx.Response(404) if request.method == "GET" else httpx.Response(200, text=sse([raw]))

    model = build_openrouter_model(
        route,
        "fixture-key",
        temperature=0.7,
        transport=httpx.MockTransport(handle),
        response_identity_evidence=evidence,
    )
    with pytest.raises(OpenRouterError, match="differs|changed"):
        invoke(model)


def test_catalog_identity_is_revalidated_before_dispatch_if_source_changes(tmp_path):
    route = binding()
    evidence = identity_evidence(tmp_path, route)
    requests = []

    def handle(request):
        requests.append(request)
        raise AssertionError("changed evidence must not dispatch")

    model = build_openrouter_model(
        route,
        "fixture-key",
        temperature=0.7,
        transport=httpx.MockTransport(handle),
        response_identity_evidence=evidence,
    )
    (tmp_path / "models.json").write_text('{"data":[]}')
    with pytest.raises(ValueError, match="catalog changed"):
        invoke(model)
    assert not requests


def binding(model: str = COLLABORATION_MODELS[0], **changes: Any) -> OpenRouterBinding:
    """All labels/evidence below are synthetic test attestations, never routes."""

    return replace(
        OpenRouterBinding(
            model=model,
            provider_endpoint="fixture/exact-endpoint",
            revision_binding="fixture service binding; no immutable snapshot",
            binding_evidence="offline fixture endpoint evidence",
            account_policy_evidence="offline fixture; no account/workspace plugins or request rewriting",
            approved=True,
            supports_seed=model != COLLABORATION_MODELS[2],
            seed_support_evidence="offline fixture seed support",
            thinking_disabled_evidence="offline fixture supports reasoning.enabled=false",
            limited_reproducibility_approved=True,
            served_provider="Fixture Provider",
        ),
        **changes,
    )


def frame(content: str = "", *, finish: str | None = None, native: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": "generation-fixture",
        "provider": "Fixture Provider",
        "model": COLLABORATION_MODELS[0],
        "choices": [
            {"index": 0, "delta": {"content": content}, "finish_reason": finish, "native_finish_reason": native}
        ],
        **extra,
    }


def sse(frames: list[dict[str, Any]], *, done: bool = True) -> str:
    return (
        ": OPENROUTER PROCESSING\n\n"
        + "".join("data: " + json.dumps(value) + "\n\n" for value in frames)
        + ("data: [DONE]\n\n" if done else "")
    )


def gateway_model(
    frames: list[dict[str, Any]],
    *,
    route: OpenRouterBinding | None = None,
    temperature: float = 0.7,
    done: bool = True,
) -> tuple[Model, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={"error": "fixture metadata unavailable"})
        requests.append(request)
        return httpx.Response(
            200,
            text=sse(frames, done=done),
            headers={"Content-Type": "text/event-stream", "X-Generation-Id": "generation-fixture"},
        )

    return (
        build_openrouter_model(
            route or binding(), "offline-test-key", temperature=temperature, transport=httpx.MockTransport(handle)
        ),
        requests,
    )


def invoke(model: Model, /, **settings: Any) -> Any:
    return asyncio.run(
        invoke_model(
            model,
            messages=[SystemMessage(content="fixed instructions"), UserMessage(content="complete query")],
            max_tokens=256,
            **settings,
        )
    )


@pytest.mark.parametrize("model_name", COLLABORATION_MODELS)
def test_public_model_transmits_only_frozen_gateway_parameters(model_name: str) -> None:
    route = binding(model_name)
    model, requests = gateway_model([frame("yes", finish="stop", native="end_turn", model=model_name)], route=route)
    settings = {"seed": 29} if route.supports_seed else {}
    result = invoke(model, **settings)
    assert isinstance(model, Model) and len(requests) == 1
    request = requests[0]
    assert str(request.url) == OPENROUTER_BASE_URL + "/chat/completions"
    assert request.headers["Authorization"] == "Bearer offline-test-key"
    body = json.loads(request.content)
    assert body["model"] == model_name and body["temperature"] == 0.7 and body["max_tokens"] == 256
    assert body["messages"] == [
        {"role": "system", "content": "fixed instructions"},
        {"role": "user", "content": "complete query"},
    ]
    assert body["provider"] == {
        "only": [route.provider_endpoint],
        "order": [route.provider_endpoint],
        "allow_fallbacks": False,
        "require_parameters": True,
    }
    assert body["plugins"] == [{"id": name, "enabled": False} for name in DISABLED_PLUGINS]
    assert body["transforms"] == []
    assert not ({"tools", "top_p", "top_k", "stop", "response_format", "stream_options", "usage"} & set(body))
    if model_name == COLLABORATION_MODELS[0]:
        assert "reasoning" not in body
    else:
        assert body["reasoning"] == {"enabled": False} and "exclude" not in body["reasoning"]
    assert (body.get("seed") == 29) == route.supports_seed
    assert result.finish_reason == "stop" and result.native_finish_reason == "end_turn"
    assert result.sdk_usage_limited is False
    assert result.usage_metadata["binding"] == route.metadata()
    assert model.model_client_config.max_retries == 0


def test_empty_choice_usage_tail_preserves_native_categories_actual_zero_and_generation_metadata() -> None:
    native_usage = {
        "prompt_tokens": 40,
        "completion_tokens": 12,
        "total_tokens": 52,
        "cost": 0.0,
        "prompt_tokens_details": {"cached_tokens": 15, "cache_write_tokens": 3},
        "completion_tokens_details": {"reasoning_tokens": 0},
        "cost_details": {"upstream_inference_cost": 0.001},
    }
    tail = {"choices": [], "usage": native_usage}
    model, _ = gateway_model(
        [frame('{"answer":"', finish=None), frame('yes"}', finish="length", native="max_tokens"), tail]
    )
    result = invoke(model, output_parser=JsonOutputParser())
    assert result.content == '{"answer":"yes"}' and result.parser_content == {"answer": "yes"}
    assert result.finish_reason == "length" and result.native_finish_reason == "max_tokens"
    assert result.served_model == COLLABORATION_MODELS[0] and result.served_provider == "Fixture Provider"
    # A tail without an id must not erase the authoritative payload generation id.
    assert result.generation_id == "generation-fixture"
    usage = result.usage_metadata
    assert {
        key: usage[key]
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "total_cost",
        )
    } == {
        "input_tokens": 40,
        "output_tokens": 12,
        "total_tokens": 52,
        "cache_tokens": 15,
        "cache_write_tokens": 3,
        "reasoning_tokens": 0,
        "total_cost": 0.0,
    }
    assert usage["raw_gateway_usage"] == native_usage and usage["raw_gateway_frames"][-1] == tail


def test_missing_native_accounting_stays_unknown_and_partial_snapshots_are_not_summed() -> None:
    model, _ = gateway_model(
        [
            frame("answer", usage={"prompt_tokens": 30}),
            frame(finish="stop", usage={"completion_tokens": 4}),
            {"choices": [], "usage": {"prompt_tokens_details": {"cached_tokens": 0}}},
        ]
    )
    result = invoke(model)
    assert result.usage_metadata["input_tokens"] == 30 and result.usage_metadata["output_tokens"] == 4
    assert result.usage_metadata["cache_tokens"] == 0
    assert "total_cost" not in result.usage_metadata and "reasoning_tokens" not in result.usage_metadata
    unknown, _ = gateway_model([frame("answer", finish="stop")])
    assert "input_tokens" not in invoke(unknown).usage_metadata


def test_later_stop_snapshot_cannot_erase_an_observed_native_truncation() -> None:
    model, _ = gateway_model(
        [
            frame("partial", finish="stop", native="MAX_TOKENS"),
            frame(finish="stop", native="stop", usage={"cost": 0.001}),
        ]
    )
    result = invoke(model)
    assert result.finish_reason == "length" and result.native_finish_reason == "stop"
    assert result.usage_metadata["raw_gateway_frames"][0]["choices"][0]["native_finish_reason"] == "MAX_TOKENS"


@pytest.mark.parametrize(
    "change,match",
    [
        ({"approved": False}, "not been approved"),
        ({"provider_endpoint": None}, "provider_endpoint"),
        ({"binding_evidence": None}, "binding_evidence"),
        ({"limited_reproducibility_approved": False}, "limited-reproducibility"),
        ({"seed_support_evidence": None}, "seed forwarding"),
        ({"model": COLLABORATION_MODELS[1], "thinking_disabled_evidence": None}, "thinking"),
        ({"model": "openrouter/auto"}, "fixed collaboration pool"),
    ],
)
def test_binding_proposals_never_dispatch_until_exact_capabilities_are_approved(
    change: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        build_openrouter_model(binding(**change), "offline-key", temperature=0.7)


def test_endpoint_binding_has_no_regional_prerequisite() -> None:
    route = binding()
    metadata = route.metadata()
    assert "region" not in metadata
    restored = OpenRouterBinding.from_dict(metadata)
    restored.validate()
    assert restored.provider_endpoint == route.provider_endpoint
    assert restored.revision_binding == route.revision_binding


@pytest.mark.parametrize("evidence", [None, "", "historical optional evidence"])
def test_account_policy_evidence_is_optional_and_does_not_gate_execution(evidence: str | None) -> None:
    route = binding(account_policy_evidence=evidence)
    restored = OpenRouterBinding.from_dict(route.metadata())
    restored.validate()
    assert restored.account_policy_evidence == evidence
    model, calls = gateway_model([frame("answer", finish="stop")], route=restored)
    assert invoke(model).content == "answer" and len(calls) == 1
    without_legacy_field = route.metadata()
    without_legacy_field.pop("account_policy_evidence")
    OpenRouterBinding.from_dict(without_legacy_field).validate()


@pytest.mark.parametrize(
    "settings",
    [
        {"top_p": 0.9},
        {"tools": []},
        {"extra_body": {"provider": {"allow_fallbacks": True}}},
        {"plugins": []},
        {"reasoning": {"exclude": True}},
        {"model": "another-model"},
        {"temperature": 0.0},
    ],
)
def test_no_call_can_override_frozen_decoding_or_enable_extra_facilities(settings: dict[str, Any]) -> None:
    model, requests = gateway_model([frame("answer", finish="stop")])
    with pytest.raises(ValueError):
        invoke(model, **settings)
    assert not requests


def test_claude_unsupported_seed_fails_without_dispatch_and_textgrad_keeps_temperature_zero() -> None:
    claude, requests = gateway_model([frame("answer", finish="stop")], route=binding(COLLABORATION_MODELS[2]))
    with pytest.raises(ValueError, match="does not support"):
        invoke(claude, seed=4)
    assert not requests
    backward, calls = gateway_model([frame("feedback", finish="stop")], temperature=0.0)
    invoke(backward, seed=4, temperature=0.0)
    assert json.loads(calls[0].content)["temperature"] == 0.0
    assert "reasoning" not in json.loads(calls[0].content)


@pytest.mark.parametrize(
    "ending",
    [
        {"error": {"code": 502, "message": "upstream failure"}, "choices": []},
        frame(finish="error", native="error"),
    ],
)
def test_streaming_errors_keep_partial_usage_and_never_retry(ending: dict[str, Any]) -> None:
    ending["usage"] = {"prompt_tokens": 9, "completion_tokens": 2, "cost": 0.003}
    model, requests = gateway_model([frame("partial"), ending])
    with pytest.raises(OpenRouterError, match="streaming generation error") as caught:
        invoke(model)
    partial = getattr(caught.value, "usage")
    assert partial.content == "partial" and partial.usage_metadata["total_cost"] == 0.003
    assert partial.usage_metadata["input_tokens"] == 9 and len(requests) == 1


@pytest.mark.parametrize("status", [302, 429, 500])
def test_http_failure_does_not_follow_redirects_or_retry(status: int) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://unused.invalid"}, json={"error": {"code": status}})

    model = build_openrouter_model(binding(), "offline-key", temperature=0.7, transport=httpx.MockTransport(handle))
    with pytest.raises(OpenRouterError, match=f"HTTP {status}") as caught:
        invoke(model)
    partial = getattr(caught.value, "usage")
    assert len(calls) == 1 and "total_cost" not in partial.usage_metadata
    assert partial.usage_metadata["raw_gateway_frames"][0]["http_status"] == status


def test_missing_done_and_missing_finish_reason_are_explicit_failures() -> None:
    incomplete, _ = gateway_model([frame("partial", finish="stop", usage={"prompt_tokens": 5})], done=False)
    with pytest.raises(OpenRouterError, match=r"final \[DONE\]") as caught:
        invoke(incomplete)
    assert getattr(caught.value, "usage").usage_metadata["input_tokens"] == 5
    no_reason, _ = gateway_model([frame("content")])
    with pytest.raises(RuntimeError, match="completion cannot be verified"):
        invoke(no_reason)


def test_disabled_thinking_violation_retains_full_native_usage_for_accounting() -> None:
    model, requests = gateway_model(
        [
            frame("answer", finish="stop", model=COLLABORATION_MODELS[1]),
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 7,
                    "completion_tokens_details": {"reasoning_tokens": 5},
                    "cost": 0.004,
                },
            },
        ],
        route=binding(COLLABORATION_MODELS[1]),
    )
    with pytest.raises(OpenRouterError, match="disabled-thinking") as caught:
        invoke(model)
    result = getattr(caught.value, "usage")
    assert result.usage_metadata["output_tokens"] == 7 and result.usage_metadata["reasoning_tokens"] == 5
    assert result.usage_metadata["total_cost"] == 0.004 and len(requests) == 1


@pytest.mark.parametrize("first_finish,first_native", [("length", "max_tokens"), ("stop", "MAX_TOKENS")])
def test_real_public_gateway_adapter_repairs_truncation_and_counts_each_physical_cost(
    first_finish: str, first_native: str
) -> None:
    from examples.adaptive_multi_agent_collab.providers.adapter import GenerationRequest, OpenJiuwenAgent
    from examples.adaptive_multi_agent_collab.infrastructure.randomness import derive_seed
    from examples.adaptive_multi_agent_collab.tests.test_resources import controller

    calls: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={"error": "fixture metadata unavailable"})
        calls.append(json.loads(request.content))
        reason = first_finish if len(calls) == 1 else "stop"
        return httpx.Response(
            200,
            text=sse(
                [
                    frame(
                        '{"answer":"yes","explanation":""}',
                        finish=reason,
                        native=first_native if len(calls) == 1 else "stop",
                    ),
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 12,
                            "prompt_tokens_details": {"cached_tokens": 0},
                            "cost": 0.001,
                        },
                    },
                ]
            ),
        )

    model = build_openrouter_model(binding(), "offline-key", temperature=0.7, transport=httpx.MockTransport(handle))
    resources = controller()
    agent = OpenJiuwenAgent(model, resources=resources, supports_seed=True)
    result = asyncio.run(agent.generate(GenerationRequest("initial", "Is this a query?", 0, {"generation_seed": 7})))
    assert result.data == {"answer": "yes", "explanation": ""} and result.attempts == 2
    assert len(calls) == len(resources.ledger.entries) == 2
    assert calls[0]["seed"] == 7 and calls[1]["seed"] == derive_seed(7, "adapter-repair", 1) % 2**31
    assert resources.ledger.entries[1]["usage"]["logical_seed"] == derive_seed(7, "adapter-repair", 1)
    for call, entry in zip(calls, resources.ledger.entries):
        assert call["max_tokens"] == 256 and call["messages"][0]["content"].count("Fixed protocol:") == 1
        assert entry["charged"] == pytest.approx(0.001)
        assert entry["usage"]["generation_id"] == "generation-fixture"
        assert entry["usage"]["input_tokens"] == 100 and entry["usage"]["output_tokens"] == 12
    assert resources.ledger.entries[0]["usage"]["native_finish_reason"] == first_native
    assert resources.ledger.entries[0]["usage"]["finish_reason"] == "length"
    assert resources.ledger.entries[0]["usage"]["gateway_finish_reason"] == first_finish


@pytest.mark.parametrize(
    "field,changed", [("id", "different-generation"), ("provider", "Different Provider"), ("model", "different-model")]
)
def test_one_generation_cannot_change_identity_even_if_usage_is_in_the_conflicting_frame(
    field: str, changed: str
) -> None:
    later = frame(finish="stop", usage={"prompt_tokens": 12, "completion_tokens": 2, "cost": 0.003})
    later[field] = changed
    model, calls = gateway_model([frame("answer"), later])
    with pytest.raises(OpenRouterError, match="identity") as caught:
        invoke(model)
    result = getattr(caught.value, "usage")
    assert result.usage_metadata["total_cost"] == 0.003 and len(calls) == 1
    assert result.usage_metadata["raw_gateway_frames"][-1][field] == changed


def test_served_model_alias_requires_an_explicit_approved_binding() -> None:
    alias = "fixture-canonical-model-revision"
    different, _ = gateway_model([frame("answer", finish="stop", model=alias, usage={"cost": 0.003})])
    with pytest.raises(OpenRouterError, match="served model") as caught:
        invoke(different)
    assert getattr(caught.value, "usage").usage_metadata["total_cost"] == 0.003
    approved, _ = gateway_model([frame("answer", finish="stop", model=alias)], route=binding(served_model=alias))
    assert invoke(approved).served_model == alias


def test_utf8_network_fragments_comments_and_multiline_sse_are_decoded_without_content_loss() -> None:
    payload = (
        ': keepalive\n\ndata: {"choices": [\n'
        'data: {"delta":{"content":"café"},"finish_reason":"stop"}\n'
        "data: ]}\n\ndata: [DONE]\n\n"
    ).encode()

    class Fragments(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(payload), 3):
                yield payload[offset : offset + 3]

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Fragments()))
    model = build_openrouter_model(binding(), "offline-key", temperature=0.7, transport=transport)
    assert invoke(model).content == "café"


def test_midstream_disconnect_keeps_known_counts_and_does_not_invent_actual_cost() -> None:
    class Disconnect(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse([frame("partial", usage={"prompt_tokens": 15})], done=False).encode()
            raise httpx.ReadError("offline fixture disconnect")

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Disconnect()))
    model = build_openrouter_model(binding(), "offline-key", temperature=0.7, transport=transport)
    with pytest.raises(httpx.ReadError) as caught:
        invoke(model)
    result = getattr(caught.value, "usage")
    assert result.content == "partial" and result.usage_metadata["input_tokens"] == 15
    assert "total_cost" not in result.usage_metadata and "output_tokens" not in result.usage_metadata


def test_unexpected_nontext_output_is_rejected_after_preserving_known_usage() -> None:
    invalid = frame(finish="stop", usage={"prompt_tokens": 8, "completion_tokens": 1, "cost": 0.001})
    invalid["choices"][0]["delta"]["content"] = [{"type": "image_url", "image_url": "unused"}]
    model, _ = gateway_model([invalid])
    with pytest.raises(OpenRouterError, match="nontext") as caught:
        invoke(model)
    assert getattr(caught.value, "usage").usage_metadata["total_cost"] == 0.001


def generation_metadata(**changes: Any) -> dict[str, Any]:
    return {
        "id": "generation-fixture",
        "model": COLLABORATION_MODELS[0],
        "provider_name": "Fixture Provider",
        "native_tokens_prompt": 30,
        "native_tokens_completion": 7,
        "native_tokens_cached": 0,
        "native_tokens_reasoning": 0,
        "tokens_prompt": 99,
        "tokens_completion": 55,
        "total_cost": 0.001,
        "upstream_inference_cost": 0.009,
        "finish_reason": "stop",
        "native_finish_reason": "stop",
        "is_byok": False,
        "service_tier": "default",
        **changes,
    }


def lookup_model(
    payloads: list[dict[str, Any] | int],
    *,
    frames: list[dict[str, Any]] | None = None,
    done: bool = True,
) -> tuple[Model, list[httpx.Request]]:
    calls: list[httpx.Request] = []
    lookups = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal lookups
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(
                200, text=sse(frames if frames is not None else [frame("complete", finish="stop")], done=done)
            )
        assert str(request.url).startswith(OPENROUTER_BASE_URL + "/generation?")
        assert request.url.params["id"] == "generation-fixture"
        payload = payloads[min(lookups, len(payloads) - 1)]
        lookups += 1
        return (
            httpx.Response(payload, json={"error": "unavailable"})
            if isinstance(payload, int)
            else httpx.Response(200, json={"data": payload})
        )

    return (
        build_openrouter_model(binding(), "offline-key", temperature=0.7, transport=httpx.MockTransport(handle)),
        calls,
    )


def test_generation_lookup_separates_native_normalized_tokens_and_actual_paid_cost() -> None:
    payload = generation_metadata()
    model, calls = lookup_model([404, payload])
    result = invoke(model, seed=2**62 + 31)
    usage = result.usage_metadata
    assert [call.method for call in calls] == ["POST", "GET", "GET"]
    assert result.content == "complete" and result.finish_reason == "stop"
    assert usage["input_tokens"] == 30 and usage["output_tokens"] == 7
    assert usage["normalized_input_tokens"] == 99 and usage["normalized_output_tokens"] == 55
    assert usage["total_cost"] == 0.001 and usage["is_byok"] is False
    assert usage["cache_tokens"] == usage["reasoning_tokens"] == 0
    assert usage["raw_generation_metadata"] == payload and usage["response_identity_verified"] is True
    assert usage["usage_sources"]["input_tokens"] == "generation_native"
    assert usage["usage_sources"]["normalized_input_tokens"] == "generation_normalized"
    assert usage["native_count_source"] == "generation_native"
    assert len(usage["metadata_lookup_attempts"]) == 2
    assert usage["logical_seed"] == 2**62 + 31 and usage["wire_seed"] == 31
    assert usage["seed_mapping_id"] == "nonnegative_int32_modulo"
    assert json.loads(calls[0].content)["seed"] == 31
    assert "usage" not in json.loads(calls[0].content) and "stream_options" not in json.loads(calls[0].content)
    assert len(usage["raw_gateway_frames"]) == 1


def test_normalized_counts_cannot_replace_missing_native_counts_and_three_gets_are_the_limit() -> None:
    payload = generation_metadata(
        native_tokens_prompt=None, native_tokens_completion=None, native_tokens_reasoning=None
    )
    model, calls = lookup_model([payload])
    usage = invoke(model).usage_metadata
    assert [call.method for call in calls] == ["POST", "GET", "GET", "GET"]
    assert "input_tokens" not in usage and "output_tokens" not in usage and "reasoning_tokens" not in usage
    assert usage["normalized_input_tokens"] == 99 and usage["total_cost"] == 0.001
    assert len(usage["metadata_lookup_attempts"]) == 3


def test_unavailable_lookup_keeps_native_reasoning_unknown_and_does_not_regenerate() -> None:
    model, calls = lookup_model([404])
    result = invoke(model)
    assert [call.method for call in calls] == ["POST", "GET", "GET", "GET"]
    assert "reasoning_tokens" not in result.usage_metadata and "total_cost" not in result.usage_metadata
    assert all(entry["http_status"] == 404 for entry in result.usage_metadata["metadata_lookup_attempts"])


def test_incomplete_generation_can_recover_its_charge_but_cannot_be_revived() -> None:
    model, calls = lookup_model([generation_metadata()], frames=[frame("partial")], done=False)
    with pytest.raises(OpenRouterError, match="final") as caught:
        invoke(model)
    result = getattr(caught.value, "usage")
    assert result.content == "partial" and result.usage_metadata["total_cost"] == 0.001
    assert result.usage_metadata["response_identity_verified"] is False
    assert [call.method for call in calls] == ["POST", "GET"]


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"id": "different-generation"}, "generation id"),
        ({"model": "different-model"}, "identity"),
        ({"provider_name": "Different Provider"}, "identity"),
        ({"native_tokens_reasoning": 2}, "disabled-thinking"),
        ({"service_tier": "priority"}, "non-default"),
    ],
)
def test_generation_metadata_identity_capability_failures_stop_without_another_generation(
    changes: dict[str, Any],
    match: str,
) -> None:
    model, calls = lookup_model([generation_metadata(**changes)])
    with pytest.raises(OpenRouterError, match=match) as caught:
        invoke(model)
    usage = getattr(caught.value, "usage").usage_metadata
    assert usage["response_identity_verified"] is False
    assert [call.method for call in calls] == ["POST", "GET"]
    if "id" in changes:
        assert "total_cost" not in usage
    else:
        assert usage["total_cost"] == 0.001


def test_lookup_native_truncation_cannot_be_hidden_by_normalized_stop() -> None:
    model, _ = lookup_model([generation_metadata(native_finish_reason="MAX_TOKENS")])
    result = invoke(model)
    assert result.finish_reason == "length" and result.native_finish_reason == "MAX_TOKENS"
    assert result.usage_metadata["gateway_finish_reason"] == "stop"


def test_complete_native_accounting_needs_no_metadata_get() -> None:
    model, calls = lookup_model(
        [404],
        frames=[
            frame(
                "complete",
                finish="stop",
                native="stop",
                usage={
                    "prompt_tokens": 30,
                    "completion_tokens": 7,
                    "cost": 0.001,
                    "is_byok": False,
                    "prompt_tokens_details": {"cached_tokens": 0},
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
            )
        ],
    )
    result = invoke(model)
    assert [call.method for call in calls] == ["POST"]
    assert result.usage_metadata["response_identity_verified"] is True
    assert "metadata_lookup_attempts" not in result.usage_metadata


def test_metadata_timeout_is_bounded_by_original_call_and_never_replays_generation() -> None:
    calls: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, text=sse([frame("complete", finish="stop")]))
        await asyncio.sleep(10)
        raise AssertionError("lookup should have been cancelled")

    model = build_openrouter_model(binding(), "offline-key", temperature=0.7, transport=httpx.MockTransport(handle))
    result = invoke(model, timeout=0.03)
    assert calls == ["POST", "GET"]
    assert result.content == "complete" and "total_cost" not in result.usage_metadata
    assert result.usage_metadata["metadata_lookup_attempts"][0]["error_type"] == "TimeoutError"


def test_partial_native_stream_and_lookup_merge_coordinate_provenance_without_erasing_fingerprint() -> None:
    metadata = generation_metadata(
        native_tokens_prompt=None, system_fingerprint=None, upstream_id="fixture-upstream-id"
    )
    model, calls = lookup_model(
        [metadata],
        frames=[
            frame(
                "complete",
                finish="stop",
                native="stop",
                system_fingerprint="fixture-fingerprint",
                usage={"prompt_tokens": 30, "prompt_tokens_details": {"cache_write_tokens": 0}},
            )
        ],
    )
    result = invoke(model)
    usage = result.usage_metadata
    assert [request.method for request in calls] == ["POST", "GET"]
    assert usage["input_tokens"] == 30 and usage["output_tokens"] == 7
    assert usage["usage_sources"]["input_tokens"] == "stream_native"
    assert usage["usage_sources"]["output_tokens"] == "generation_native"
    assert usage["cache_write_tokens"] == 0
    assert usage["system_fingerprint"] == "fixture-fingerprint"
    assert usage["upstream_id"] == "fixture-upstream-id"
