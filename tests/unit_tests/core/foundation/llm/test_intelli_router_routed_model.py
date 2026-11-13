# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A model-group request names, in its own prompt, the model it is sent to."""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import (
    ROUTED_MODEL_PLACEHOLDER,
    IntelliRouterClientConfig,
    IntelliRouterModelClient,
    _fill_routed_model,
)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("intelli_router") is None,
    reason="requires the optional intelli_router package",
)


def _deployment(route_id: str, model_name: str, fallback_tag: str | None) -> dict[str, Any]:
    return {
        "route_id": route_id,
        "model_name": model_name,
        "fallback_tag": fallback_tag,
        "provider": "openai",
        "api_key": "mock-api-key",
        "api_base": f"https://{route_id}.example.invalid",
    }


def _router(*deployments: dict[str, Any]) -> Any:
    """A real ordered-failover router, as the model-group client builds it."""
    config = IntelliRouterClientConfig(
        model_group_id="mgp_1",
        deployments=list(deployments),
        strategy="ordered-failover",
        num_retries=len(deployments) - 1,
    )
    return IntelliRouterModelClient._create_router(config)


def _prompt() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are a teammate."},
        {"role": "user", "content": f"# Runtime State\n\n- Current model: {ROUTED_MODEL_PLACEHOLDER}\n"},
    ]


def _ok_response(model: str) -> dict[str, Any]:
    return {
        "id": "resp-1",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def test_each_attempt_names_its_own_model() -> None:
    """The adapter of each deployment fills that deployment's model name."""
    router = _router(_deployment("rte_a", "glm-a", "GLM-A"), _deployment("rte_b", "glm-b", "GLM-B"))
    messages = _prompt()

    bodies = {
        dep.id: router._get_adapter(dep).transform_request(model=dep.model_name, messages=messages, deployment=dep)
        for dep in router.deployments
    }

    assert bodies["rte_a"]["messages"][1]["content"] == "# Runtime State\n\n- Current model: GLM-A\n"
    assert bodies["rte_b"]["messages"][1]["content"] == "# Runtime State\n\n- Current model: GLM-B\n"
    assert ROUTED_MODEL_PLACEHOLDER in messages[1]["content"], "the caller's messages must stay untouched"


@pytest.mark.asyncio
async def test_failover_sends_the_backup_model_its_own_name() -> None:
    """When A fails, the request B actually answers names B, not A."""
    from intelli_router.utils.exceptions import DeploymentServerError

    router = _router(_deployment("rte_a", "glm-a", "GLM-A"), _deployment("rte_b", "glm-b", "GLM-B"))
    sent: list[tuple[str, str]] = []

    async def _make_request(deployment: Any, request_body: dict[str, Any]) -> dict[str, Any]:
        sent.append((deployment.id, request_body["messages"][1]["content"]))
        if deployment.id == "rte_a":
            raise DeploymentServerError(deployment.id, 503, "down")
        return _ok_response(deployment.model_name)

    router._make_request = _make_request
    await router.invoke(messages=_prompt(), model="*")

    assert sent == [
        ("rte_a", "# Runtime State\n\n- Current model: GLM-A\n"),
        ("rte_b", "# Runtime State\n\n- Current model: GLM-B\n"),
    ]


def test_route_events_carry_the_same_name() -> None:
    """The name a route event reports is the name the prompt received."""
    router = _router(_deployment("rte_a", "glm-a", "GLM-A"), _deployment("rte_b", "glm-b", None))

    assert router._route_metadata(router.deployments[0])["routed_model"] == "GLM-A"
    assert router._route_metadata(router.deployments[1])["routed_model"] == "glm-b"


def test_multipart_content_is_filled() -> None:
    """Text parts of multi-part content are filled; other parts are kept."""
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    messages = [{"role": "user", "content": [{"type": "text", "text": f"model: {ROUTED_MODEL_PLACEHOLDER}"}, image]}]

    filled = _fill_routed_model(messages, "GLM-A")

    assert filled[0]["content"] == [{"type": "text", "text": "model: GLM-A"}, image]
    assert messages[0]["content"][0]["text"] == f"model: {ROUTED_MODEL_PLACEHOLDER}"


def test_prompt_without_placeholder_is_sent_as_is() -> None:
    """A request with no placeholder keeps the very same message list."""
    messages = [{"role": "user", "content": "hello"}]

    assert _fill_routed_model(messages, "GLM-A") is messages


@pytest.mark.asyncio
async def test_each_attempt_is_announced_when_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every attempt is announced as it is sent, so a failed one still names its model.

    The HTTP layer is faked on the base router so the routed-model mixin's own
    send hook runs.
    """
    from intelli_router.router.base_router import BaseRouter
    from intelli_router.utils.exceptions import DeploymentServerError

    from openjiuwen.core.foundation.llm.model_clients import intelli_router_model_client as client_module

    router = _router(_deployment("rte_a", "glm-a", "GLM-A"), _deployment("rte_b", "glm-b", "GLM-B"))
    sent: list[tuple[str, str]] = []
    announced: list[dict[str, Any]] = []

    async def _make_request(self, deployment: Any, request_body: dict[str, Any]) -> dict[str, Any]:
        sent.append((deployment.id, request_body["messages"][1]["content"]))
        if deployment.id == "rte_a":
            raise DeploymentServerError(deployment.id, 503, "down")
        return _ok_response(deployment.model_name)

    async def _trigger(event: Any, **kwargs: Any) -> None:
        if event == client_module.LLMCallEvents.LLM_ROUTE_ATTEMPT:
            announced.append(kwargs["route_metadata"])

    monkeypatch.setattr(BaseRouter, "_make_request", _make_request)
    monkeypatch.setattr(client_module, "trigger", _trigger)
    await router.invoke(messages=_prompt(), model="*")

    assert [content for _, content in sent] == [
        "# Runtime State\n\n- Current model: GLM-A\n",
        "# Runtime State\n\n- Current model: GLM-B\n",
    ]
    assert [(item["route_id"], item["status"], item["routed_model"]) for item in announced] == [
        ("rte_a", "sending", "GLM-A"),
        ("rte_b", "sending", "GLM-B"),
    ]

@pytest.mark.asyncio
async def test_failed_invoke_names_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed model-group call fires the terminal LLM error event, like other clients."""
    from intelli_router.utils.exceptions import DeploymentAuthError

    from openjiuwen.core.common.exception.errors import FrameworkError
    from openjiuwen.core.foundation.llm.model_clients import intelli_router_model_client as client_module
    from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

    fired: list[tuple[Any, dict[str, Any]]] = []

    async def _trigger(event: Any, **kwargs: Any) -> None:
        fired.append((event, kwargs))

    monkeypatch.setattr(client_module, "trigger", _trigger)
    router = _router(_deployment("rte_a", "glm-a", "GLM-A"))

    async def _invoke(**_kwargs: Any) -> Any:
        raise DeploymentAuthError("rte_a", 401)

    router.invoke = _invoke
    client = IntelliRouterModelClient(
        ModelRequestConfig(model="*"),
        ModelClientConfig(client_provider="intelli_router"),
        router=router,
    )

    with pytest.raises(FrameworkError):
        await client.invoke([{"role": "user", "content": "hi"}])

    errors = [kwargs for event, kwargs in fired if event == client_module.LLMCallEvents.LLM_CALL_ERROR]
    assert len(errors) == 1
    assert errors[0]["is_stream"] is False
    assert "401" in str(errors[0]["error"])


@pytest.mark.asyncio
async def test_failed_stream_names_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stream that fails before any chunk fires the terminal LLM error event too."""
    from intelli_router.utils.exceptions import DeploymentAuthError

    from openjiuwen.core.common.exception.errors import FrameworkError
    from openjiuwen.core.foundation.llm.model_clients import intelli_router_model_client as client_module
    from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

    fired: list[tuple[Any, dict[str, Any]]] = []

    async def _trigger(event: Any, **kwargs: Any) -> None:
        fired.append((event, kwargs))

    monkeypatch.setattr(client_module, "trigger", _trigger)
    router = _router(_deployment("rte_a", "glm-a", "GLM-A"))

    async def _stream(**_kwargs: Any) -> Any:
        raise DeploymentAuthError("rte_a", 401)
        yield None  # pragma: no cover - makes this an async generator

    router.stream = _stream
    client = IntelliRouterModelClient(
        ModelRequestConfig(model="*"),
        ModelClientConfig(client_provider="intelli_router"),
        router=router,
    )

    with pytest.raises(FrameworkError):
        async for _chunk in client.stream([{"role": "user", "content": "hi"}]):
            pass

    errors = [kwargs for event, kwargs in fired if event == client_module.LLMCallEvents.LLM_CALL_ERROR]
    assert len(errors) == 1
    assert errors[0]["is_stream"] is True
    assert "401" in str(errors[0]["error"])

@pytest.mark.asyncio
async def test_route_selection_is_logged_with_cooldown_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every attempt logs its route and what cooldown skipped, traced or not."""
    from openjiuwen.core.foundation.llm.model_clients import intelli_router_model_client as client_module

    logged: list[str] = []

    class _Log:
        def info(self, message: str, *args: Any) -> None:
            logged.append(message % args if args else message)

    async def _trigger(event: Any, **_kwargs: Any) -> None:
        pass

    monkeypatch.setattr(client_module, "llm_logger", _Log())
    monkeypatch.setattr(client_module, "trigger", _trigger)
    router = _router(_deployment("rte_a", "glm-a", "GLM-A"), _deployment("rte_b", "glm-b", "GLM-B"))
    router.state.on_failure("rte_a", RuntimeError("down"), cooldown_time=60)

    await router._announce_route_attempt(router.deployments[1])

    assert len(logged) == 1
    assert "model=GLM-B" in logged[0]
    assert "cooldown_skipped=" in logged[0]
    assert "GLM-A(" in logged[0], "the cooled-down model is named with its remaining time"


def test_router_scope_isolates_router_state() -> None:
    """A scoped config gets its own router; cooldowns never cross the scope."""
    from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import (
        IntelliRouterClientConfig,
        IntelliRouterModelClient,
        cooldown_remaining,
    )
    import time as _time

    deployments = [_deployment("rte_a", "glm-a", "GLM-A")]
    base = dict(
        model_group_id="mgp_scope",
        deployments=deployments,
        strategy="ordered-failover",
        num_retries=0,
    )
    shared = IntelliRouterClientConfig(**base)
    scoped = IntelliRouterClientConfig(**base, router_scope="probe")

    router_shared = IntelliRouterModelClient._get_or_create_router(shared)
    router_scoped = IntelliRouterModelClient._get_or_create_router(scoped)

    assert router_shared is not router_scoped
    router_shared.state.on_failure("rte_a", RuntimeError("down"), cooldown_time=60)
    now = _time.time()
    assert cooldown_remaining(router_shared, "rte_a", now) is not None
    assert cooldown_remaining(router_scoped, "rte_a", now) is None, "the scoped router keeps its own state"


@pytest.mark.asyncio
async def test_route_log_survives_a_failing_observer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The selection log is written even when the trigger raises."""
    from openjiuwen.core.foundation.llm.model_clients import intelli_router_model_client as client_module

    logged: list[str] = []

    class _Log:
        def info(self, message: str, *args: Any) -> None:
            logged.append(message % args if args else message)

        def debug(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    async def _trigger(event: Any, **_kwargs: Any) -> None:
        raise RuntimeError("observer down")

    monkeypatch.setattr(client_module, "llm_logger", _Log())
    monkeypatch.setattr(client_module, "trigger", _trigger)
    router = _router(_deployment("rte_a", "glm-a", "GLM-A"))

    await router._announce_route_attempt(router.deployments[0])

    assert len(logged) == 1
    assert "model=GLM-A" in logged[0]


@pytest.mark.asyncio
async def test_model_error_survives_a_failing_error_observer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The converted model error is raised even when the error observer aborts."""
    from intelli_router.utils.exceptions import DeploymentServerError

    from openjiuwen.core.common.exception.errors import FrameworkError
    from openjiuwen.core.foundation.llm.model_clients import intelli_router_model_client as client_module

    async def _trigger(event: Any, **_kwargs: Any) -> None:
        raise RuntimeError("observer down")

    monkeypatch.setattr(client_module, "trigger", _trigger)
    router = _router(_deployment("rte_a", "glm-a", "GLM-A"))

    async def _invoke(**_kwargs: Any) -> Any:
        raise DeploymentServerError("rte_a", 503, "down")

    router.invoke = _invoke
    from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

    client = client_module.IntelliRouterModelClient(
        ModelRequestConfig(model="*"),
        ModelClientConfig(client_provider="intelli_router"),
        router=router,
    )

    with pytest.raises(FrameworkError):
        await client.invoke([{"role": "user", "content": "hi"}])
