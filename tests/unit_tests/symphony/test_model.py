from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

import openjiuwen.symphony as symphony
from openjiuwen.symphony.orchestration.model import invoke_json, model_identity, model_usage_context
from openjiuwen.symphony.shared.identity import endpoint_sha256, sanitize_metadata


class _FakeModel:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self.model_config: Any = None
        self.model_client_config: Any = None

    async def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def test_model_identity_is_stable_and_sanitized() -> None:
    model = _FakeModel()
    model.model_config = SimpleNamespace(model_name="model-a", temperature=0.2, top_p=0.8, max_tokens=100, stop=None)
    model.model_client_config = SimpleNamespace(
        client_provider="OpenAI",
        api_base=" HTTPS://PRIVATE.EXAMPLE.TEST/v1/ ",
        api_key="top-secret",
        client_id="volatile-id",
    )
    identity = model_identity(model)
    identity["model"] = "mutated"

    assert model_identity(model)["model"] == "model-a"
    serialized = json.dumps(model_identity(model), sort_keys=True)
    assert model_identity(model)["api_base_sha256"]
    assert "PRIVATE.EXAMPLE.TEST" not in serialized
    assert "top-secret" not in serialized
    assert "volatile-id" not in serialized


def test_model_identity_hashes_complete_routing_config_but_ignores_secrets() -> None:
    def identity(route: str, *, api_key: str, client_id: str, vendor_key: str = "vendor-a") -> dict:
        model = _FakeModel()
        model.model_config = SimpleNamespace(model_name="model-a", temperature=0.2)
        model.model_client_config = SimpleNamespace(
            client_provider="OpenAI",
            api_base="https://private.example.test/v1",
            apiKey=api_key,
            client_id=client_id,
            routing_key=route,
            vendorKey=vendor_key,
            nested=[{"AuthorizationHeader": "routing-secret", "safe_weight": 2}],
        )
        return model_identity(model)

    blue = identity("blue", api_key="secret-a", client_id="id-a")
    equivalent_blue = identity("blue", api_key="secret-b", client_id="id-b")
    green = identity("green", api_key="secret-a", client_id="id-a")
    vendor_changed = identity("blue", api_key="secret-a", client_id="id-a", vendor_key="vendor-b")

    assert blue == equivalent_blue
    assert blue["client_config_sha256"] != green["client_config_sha256"]
    assert blue["client_config_sha256"] != vendor_changed["client_config_sha256"]
    serialized = json.dumps(blue, sort_keys=True)
    assert "secret-a" not in serialized
    assert "routing-secret" not in serialized
    assert "private.example.test" not in serialized
    assert "vendor-a" not in serialized


def test_identity_sanitizer_preserves_semantic_keys_but_removes_credential_keys() -> None:
    sanitized = sanitize_metadata(
        {
            "routing_key": "blue",
            "partition_key": "tenant-a",
            "cache_key": "cache-a",
            "key_type": "RSA",
            "public_key": "public-material",
            "apiKey": "api-secret",
            "apikey": "compact-api-secret",
            "access_key": "access-secret",
            "private_key": "private-secret",
            "signing_key": "signing-secret",
            "subscription_key": "subscription-secret",
            "encryption_key": "encryption-secret",
            "consumer_key": "consumer-secret",
            "vendorKey": "vendor-sensitive-value",
        }
    )

    assert sanitized == {
        "routing_key": "blue",
        "partition_key": "tenant-a",
        "cache_key": "cache-a",
        "key_type": "RSA",
        "public_key": "public-material",
        "vendorKey_sha256": sanitize_metadata({"vendorKey": "vendor-sensitive-value"})["vendorKey_sha256"],
    }
    assert "vendor-sensitive-value" not in json.dumps(sanitized, sort_keys=True)


def test_identity_sanitizer_classifies_token_fields_without_losing_behavioral_identity() -> None:
    metadata = {
        "max_tokens": 4096,
        "min_tokens": 32,
        "token_count": 17,
        "reasoning_token_budget": 2048,
        "tokenizer_name": "o200k_base",
        "return_token_ids": True,
        "access_token": "access-secret",
        "refreshToken": "refresh-secret",
        "bearer_token": "bearer-secret",
        "authToken": "auth-secret",
        "id_token": "id-secret",
        "sessionToken": "session-secret",
        "api_token": "api-secret",
        "token": "exact-secret",
        "vendorToken": "vendor-secret-a",
    }

    sanitized = sanitize_metadata(metadata)
    vendor_changed = sanitize_metadata({**metadata, "vendorToken": "vendor-secret-b"})
    credential_changed = sanitize_metadata(
        {
            **metadata,
            "access_token": "access-secret-changed",
            "refreshToken": "refresh-secret-changed",
            "bearer_token": "bearer-secret-changed",
            "authToken": "auth-secret-changed",
            "id_token": "id-secret-changed",
            "sessionToken": "session-secret-changed",
            "api_token": "api-secret-changed",
            "token": "exact-secret-changed",
        }
    )

    assert sanitized == {
        "max_tokens": 4096,
        "min_tokens": 32,
        "token_count": 17,
        "reasoning_token_budget": 2048,
        "tokenizer_name": "o200k_base",
        "return_token_ids": True,
        "vendorToken_sha256": sanitize_metadata({"vendorToken": "vendor-secret-a"})["vendorToken_sha256"],
    }
    assert sanitized["vendorToken_sha256"] != vendor_changed["vendorToken_sha256"]
    assert credential_changed == sanitized
    serialized = json.dumps(sanitized, sort_keys=True)
    for secret in (
        "access-secret",
        "refresh-secret",
        "bearer-secret",
        "auth-secret",
        "id-secret",
        "session-secret",
        "api-secret",
        "exact-secret",
        "vendor-secret-a",
    ):
        assert secret not in serialized


def test_endpoint_identity_sorts_query_pairs_preserves_duplicates_and_ignores_fragment() -> None:
    first = endpoint_sha256(" HTTPS://API.EXAMPLE.TEST/v1/?api-version=2026-01-01&deployment=blue&tag=b&tag=a#first ")
    reordered = endpoint_sha256("https://api.example.test/v1?tag=a&deployment=blue&tag=b&api-version=2026-01-01#second")

    assert first == reordered
    assert first != endpoint_sha256("https://api.example.test/v1?api-version=2026-01-01&deployment=green&tag=a&tag=b")
    assert first != endpoint_sha256("https://api.example.test/v1?api-version=2026-02-01&deployment=blue&tag=a&tag=b")


def test_removed_llm_adapter_contracts_are_not_exported() -> None:
    assert not hasattr(symphony, "LLMClient")
    assert not hasattr(symphony, "LLMResponseObserver")
    assert not hasattr(symphony, "OpenJiuwenLLMClient")


@pytest.mark.asyncio
async def test_invoke_json_uses_model_and_repairs_json() -> None:
    model = _FakeModel(SimpleNamespace(content='```json\n{"accepted": true,}\n```'))

    result = await invoke_json(
        model,
        system_prompt="system",
        user_content="payload",
        timeout=12,
        request_overrides={"extra_body": {"thinking": {"type": "disabled"}}},
    )

    assert result == '{"accepted": true}'
    assert model.calls == [
        (
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "payload"},
            ],
            {
                "extra_body": {"thinking": {"type": "disabled"}},
                "timeout": 12,
            },
        )
    ]


@pytest.mark.asyncio
async def test_invoke_json_reports_context_to_async_observer() -> None:
    response = SimpleNamespace(content=[{"text": '{"ok":'}, {"content": "true}"}])
    observed = []

    async def observer(item, stage, operation):
        observed.append((item, stage, operation))

    model = _FakeModel(response)

    with model_usage_context("orchestration", "beam_rerank"):
        result = await invoke_json(
            model,
            system_prompt="system",
            user_content="payload",
            response_observer=observer,
        )

    assert result == '{"ok": true}'
    assert observed == [(response, "orchestration", "beam_rerank")]


@pytest.mark.asyncio
async def test_invoke_json_observer_failure_does_not_break_completion() -> None:
    def observer(*_args):
        raise RuntimeError("usage sink unavailable")

    model = _FakeModel(SimpleNamespace(content='{"ok": true}'))

    assert (
        await invoke_json(
            model,
            system_prompt="system",
            user_content="payload",
            response_observer=observer,
        )
        == '{"ok": true}'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "message"),
    [
        (_FakeModel(error=TimeoutError("slow")), "graph matching request failed: slow"),
        (_FakeModel(SimpleNamespace(content="")), "graph matching request failed: response content is empty"),
    ],
)
async def test_invoke_json_wraps_failures(model: _FakeModel, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        await invoke_json(
            model,
            system_prompt="system",
            user_content="payload",
            error_context="graph matching",
        )
