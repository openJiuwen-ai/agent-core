# coding: utf-8

import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm.model_clients import create_model_client
from openjiuwen.core.foundation.llm.model_clients.ascend_affinity_model_client import (
    AscendAffinityModelClient,
)
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.kv_cache import (
    KVC_MANAGEMENT_MAX_ATTEMPTS,
    KVC_SESSION_OFFLOAD_PREFETCH_TIMEOUT_SECONDS,
)


def _client() -> AscendAffinityModelClient:
    return AscendAffinityModelClient(
        model_config=ModelRequestConfig(model="test-model"),
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.AscendAffinity,
            api_key="test-key",
            api_base="https://example.test",
            verify_ssl=False,
        ),
    )


def test_factory_creates_ascend_affinity_client():
    client = create_model_client(
        client_config=ModelClientConfig(
            client_provider="AscendAffinity",
            api_key="test-key",
            api_base="https://example.test",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model"),
    )

    assert isinstance(client, AscendAffinityModelClient)
    assert client.supports_kv_cache_affinity() is True


def test_normal_request_carries_agent_hint():
    params = _client()._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
        session_id="sess-a",
        parent_session_id="parent-a",
    )

    assert params["agent_hint"] == {
        "session_id": "sess-a",
        "parent_session_id": "parent-a",
    }


def test_normal_request_without_session_omits_agent_hint():
    params = _client()._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert "agent_hint" not in params


def test_session_management_request_uses_empty_messages_and_context_management():
    client = _client()
    params = client._build_request_params(
        messages=[],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
        session_id="sess-a",
        parent_session_id="parent-a",
        kv_action="evict",
        target="session",
        manage_request=True,
    )

    assert params["messages"] == []
    assert "tools" not in params
    assert params["agent_hint"] == {
        "session_id": "sess-a",
        "parent_session_id": "parent-a",
        "context_management": {
            "manage_request": True,
            "edits": [{"type": "evict", "target": "session"}],
        },
    }


def test_management_request_defaults_to_session_target():
    params = _client()._build_request_params(
        messages=[],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
        session_id="sess-a",
        kv_action="offload",
        manage_request=True,
    )

    assert params["messages"] == []
    assert params["agent_hint"]["parent_session_id"] == "sess-a"
    assert params["agent_hint"]["context_management"]["edits"] == [
        {"type": "offload", "target": "session"}
    ]


def test_message_and_tools_management_builds_two_edits():
    params = _client()._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
        session_id="sess-a",
        kv_action="evict",
        target="messages",
        manage_request=True,
        msg_start=2,
        msg_end=3,
        include_tools=True,
        tools_start=0,
        tools_end=1,
    )

    edits = params["agent_hint"]["context_management"]["edits"]
    assert edits == [
        {"type": "evict", "target": "messages", "start": 2, "end": 3},
        {"type": "evict", "target": "tools", "start": 0, "end": 1},
    ]


@pytest.mark.parametrize(
    ("target", "msg_start", "msg_end", "tools_start", "tools_end"),
    [
        ("messages", 1, None, None, None),
        ("messages", None, 1, None, None),
        ("tools", None, None, 0, None),
        ("tools", None, None, None, 0),
    ],
)
def test_range_target_requires_both_start_and_end(
        target, msg_start, msg_end, tools_start, tools_end
):
    with pytest.raises(Exception):
        _client()._build_target_edits(
            action="evict",
            target=target,
            msg_start=msg_start,
            msg_end=msg_end,
            tools_start=tools_start,
            tools_end=tools_end,
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 0), (1, -1), (2, 1), (1, 1), (True, 1), (0, False)],
)
def test_range_target_rejects_invalid_half_open_range(start, end):
    with pytest.raises(Exception):
        _client()._build_target_edits(
            action="evict",
            target="messages",
            msg_start=start,
            msg_end=end,
        )


def test_session_management_rejects_ranges():
    with pytest.raises(Exception):
        _client()._build_request_params(
            messages=[],
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
            session_id="sess-a",
            kv_action="evict",
            target="session",
            manage_request=True,
            msg_start=1,
        )


def test_model_reports_affinity_support_and_builds_invoke_kwargs():
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.AscendAffinity,
            api_key="test-key",
            api_base="https://example.test",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model"),
    )

    class Session:
        @staticmethod
        def get_session_id():
            return "sess-a"

    assert model.supports_kv_cache_affinity() is True
    # The generic legacy wrapper must not enable AscendAffinity. New callers use
    # the affinity-specific name below so release and affinity remain separate.
    assert model.build_kv_cache_invoke_kwargs(session=Session()) == {}
    assert model.build_kv_cache_affinity_invoke_kwargs(session=Session()) == {}
    assert model.build_kv_cache_affinity_invoke_kwargs(session=Session(), enable_kv_cache_affinity=True) == {
        "session_id": "sess-a",
        "parent_session_id": "sess-a",
    }


def test_kv_action_methods_have_explicit_parameters():
    expected = {
        "self",
        "session_id",
        "parent_session_id",
        "target",
        "messages",
        "tools",
        "model",
        "msg_start",
        "msg_end",
        "tools_start",
        "tools_end",
        "include_tools",
        "timeout",
    }

    for method_name in ("evict_kvc", "offload_kvc", "prefetch_kvc"):
        params = inspect.signature(getattr(AscendAffinityModelClient, method_name)).parameters
        assert set(params) == expected
        assert not any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())

        model_params = inspect.signature(getattr(Model, method_name)).parameters
        assert set(model_params) == expected
        assert not any(param.kind == inspect.Parameter.VAR_KEYWORD for param in model_params.values())


@pytest.mark.asyncio
async def test_kv_management_uses_shared_total_timeout_and_single_attempt():
    client = _client()
    request = AsyncMock(return_value={"choices": [{"message": {"content": ""}}]})
    client._make_ascend_affinity_request = request

    assert await client.offload_kvc(session_id="sess-a") is True

    assert request.await_args.kwargs["timeout"] == KVC_SESSION_OFFLOAD_PREFETCH_TIMEOUT_SECONDS
    assert request.await_args.kwargs["max_attempts"] == KVC_MANAGEMENT_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_kv_management_total_timeout_cancels_request():
    client = _client()
    cancelled = asyncio.Event()

    async def _slow_request(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client._make_ascend_affinity_request = _slow_request

    with pytest.raises(Exception):
        await client.evict_kvc(session_id="sess-a", timeout=0.01)

    assert cancelled.is_set()
