# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for intelli_router failure translation (issue #5489)."""
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

intelli_router = pytest.importorskip("intelli_router")

from intelli_router import DeploymentStatus  # noqa: E402
from intelli_router.utils.exceptions import (  # noqa: E402
    AllDeploymentsFailed,
    DeploymentAuthError,
    DeploymentError,
    DeploymentNetworkError,
    NoDeploymentAvailable,
)

from openjiuwen.core.common.exception.codes import StatusCode  # noqa: E402
from openjiuwen.core.common.exception.errors import BaseError  # noqa: E402
from openjiuwen.core.foundation.llm.routing import intelli_router_errors as errors  # noqa: E402
from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import (  # noqa: E402
    IntelliRouterModelClient,
    _CoreRoutingEventBridge,
)
from openjiuwen.core.foundation.llm.schema.config import (  # noqa: E402
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.schema.message import UserMessage  # noqa: E402

# Internal identifiers and jargon must never reach the user-facing text.
_FORBIDDEN_TEXT = (
    "'*'", "dpl_", "deployment", "Deployment", "route", "pinned", "cooldown",
    "LLM stream timeout", "stream frame timeout",
)


@pytest.fixture(autouse=True)
def _clear_failure_records():
    errors.reset_failure_records()
    yield
    errors.reset_failure_records()


def _deployment(dep_id: str, model_name: str, *, api_base: str = "https://api.a.com/v1", provider: str = "openai",
                tag: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=dep_id, model_name=model_name, provider=provider, api_base=api_base, fallback_tag=tag or model_name,
    )


class _FakeState:
    """Mirror of the ``LocalRouterState`` fields the translator touches."""

    def __init__(self) -> None:
        self.deployment_status: dict = {}
        self.cooldown_until: dict = {}
        self.consecutive_failures: dict = {}

    def reset_deployment(self, dep_id: str) -> None:
        self.deployment_status[dep_id] = DeploymentStatus.HEALTHY
        self.cooldown_until[dep_id] = None


class _FakeRouter:
    """Router stand-in exposing the public attributes the translator reads."""

    def __init__(self, deployments: list, *, pinned_tag: str | None = None, group_id: str = "g1") -> None:
        self.deployments = deployments
        self.model_group_id = group_id
        self.strategy = SimpleNamespace(fallback_tag=pinned_tag) if pinned_tag else SimpleNamespace()
        self.state = _FakeState()

    def fail(self, dep_id: str, *, cooldown: float = 60.0, consecutive: int = 1) -> None:
        """Mirror ``LocalRouterState.on_failure``: cooldown + failure streak."""
        self.state.deployment_status[dep_id] = DeploymentStatus.COOLDOWN
        self.state.cooldown_until[dep_id] = time.time() + cooldown
        self.state.consecutive_failures[dep_id] = consecutive


def _translate(exc, router, *, output_emitted: bool = False, model_name: str = "*") -> BaseError:
    return errors.to_model_error(exc, router=router, model_name=model_name, output_emitted=output_emitted)


def _assert_readable(error: BaseError) -> None:
    text = str(error)
    for forbidden in _FORBIDDEN_TEXT:
        assert forbidden not in text, f"{forbidden!r} leaked into {text!r}"


def _bad_request(dep_id: str, status: int = 400, message: str = "maximum context length is 32768 tokens"):
    body = json.dumps({"error": {"message": message, "type": "invalid_request_error"}})
    return DeploymentError(
        message=f"Deployment '{dep_id}' returned HTTP {status}",
        details={"deployment_id": dep_id, "status_code": status, "response_body": body},
    )


class TestSwitchable:
    def test_auth_error_with_usable_deployment_asks_for_immediate_retry(self):
        router = _FakeRouter([_deployment("dpl_c", "C"), _deployment("dpl_d", "D")])
        router.fail("dpl_c")

        error = _translate(DeploymentAuthError(deployment_id="dpl_c", status_code=401), router)

        assert error.status == StatusCode.MODEL_CALL_FAILED
        assert error.details["route_case"] == "switchable"
        assert error.details["reason"] == "deterministic"
        assert error.details["should_retry"] is True
        assert error.details["retry_after_seconds"] == errors.SWITCH_RETRY_DELAY_SECONDS
        assert error.details["user_action_required"] is False
        assert error.details["usable"] == [
            {"model_name": "D", "provider": "openai", "endpoint": "api.a.com", "position": 2},
        ]
        assert error.details["failures"][0]["position"] == 1
        assert str(error) == (
            "[181001] The model 'C' (openai, api.a.com) rejected the API key (HTTP 401). "
            "Switching to another model in the group."
        )
        _assert_readable(error)

    def test_pinned_model_switches_between_same_name_deployments(self):
        router = _FakeRouter([_deployment("dpl_c1", "C"), _deployment("dpl_c2", "C", api_base="https://b.com")],
                             pinned_tag="C")
        router.fail("dpl_c1")

        error = _translate(DeploymentAuthError(deployment_id="dpl_c1", status_code=401), router)

        assert error.details["route_case"] == "switchable"
        assert error.details["pinned_model"] == "C"

    def test_rejected_request_tries_another_model(self):
        router = _FakeRouter([_deployment("dpl_c", "C"), _deployment("dpl_d", "D")])
        router.fail("dpl_c")

        error = _translate(_bad_request("dpl_c"), router)

        assert error.details["route_case"] == "switchable"
        assert error.details["reason"] == "request_rejected"
        assert error.details["should_retry"] is True
        assert "rejected the request (HTTP 400): maximum context length is 32768 tokens" in str(error)
        _assert_readable(error)

    def test_interrupted_answer_with_usable_deployment_reruns_round(self):
        router = _FakeRouter([_deployment("dpl_c", "C"), _deployment("dpl_d", "D")])
        router.fail("dpl_c")

        error = _translate(DeploymentNetworkError(deployment_id="dpl_c", reason="closed"), router,
                           output_emitted=True)

        assert error.status == StatusCode.MODEL_CALL_FAILED
        assert error.details["reason"] == "stream_interrupted"
        assert error.details["should_retry"] is False
        assert "stopped in the middle of its answer (could not be reached)" in str(error)
        assert "Retrying with another model in the group." in str(error)
        _assert_readable(error)


class TestExhausted:
    def test_pinned_single_deployment_auth_error_requires_user(self):
        router = _FakeRouter([_deployment("dpl_c", "C")], pinned_tag="C")
        router.fail("dpl_c")

        error = _translate(DeploymentAuthError(deployment_id="dpl_c", status_code=401), router)

        assert error.status == StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE
        assert error.details["route_case"] == "exhausted_user_action"
        assert error.details["reason"] == "all_deterministic"
        assert error.details["user_action_required"] is True
        assert error.details["should_retry"] is False
        assert str(error) == (
            "[181010] The model 'C' (openai, api.a.com) rejected the API key (HTTP 401). "
            "Please check the API key configured for this model. "
            "This member is set to use 'C' only, so no other model was tried."
        )
        _assert_readable(error)

    def test_cooldown_error_reports_the_original_reason(self):
        router = _FakeRouter([_deployment("dpl_c", "C")], pinned_tag="C")
        router.fail("dpl_c")
        _translate(DeploymentAuthError(deployment_id="dpl_c", status_code=401), router)

        error = _translate(NoDeploymentAvailable("*", "No deployment matched routing strategy"), router)

        assert error.details["reason"] == "all_deterministic"
        assert "rejected the API key (HTTP 401)" in str(error)
        _assert_readable(error)

    def test_all_same_name_deployments_listed(self):
        router = _FakeRouter([_deployment("dpl_c1", "C"), _deployment("dpl_c2", "C", api_base="https://b.com")],
                             pinned_tag="C")
        router.fail("dpl_c1")
        _translate(DeploymentAuthError(deployment_id="dpl_c1", status_code=401), router)
        router.fail("dpl_c2")

        error = _translate(DeploymentAuthError(deployment_id="dpl_c2", status_code=403), router)

        text = str(error)
        assert text.startswith("[181010] No model 'C' is available: ")
        assert "'C' (openai, api.a.com) rejected the API key (HTTP 401)" in text
        assert "'C' (openai, b.com) rejected the API key (HTTP 403)" in text
        _assert_readable(error)

    def test_mixed_reasons_wait_for_the_transient_deployment(self):
        router = _FakeRouter([_deployment("dpl_c1", "C"), _deployment("dpl_c2", "C")], pinned_tag="C")
        router.fail("dpl_c1")
        _translate(DeploymentAuthError(deployment_id="dpl_c1", status_code=401), router)
        router.fail("dpl_c2", cooldown=30.0)

        error = _translate(
            AllDeploymentsFailed(
                model="*", errors=[("dpl_c2", "Deployment 'dpl_c2' returned HTTP 502 (server error)")],
            ),
            router,
        )

        assert error.details["route_case"] == "exhausted_retryable"
        assert error.details["should_retry"] is True
        assert 29.0 < error.details["retry_after_seconds"] <= 31.0
        assert len(error.details["failures"]) == 2
        assert "Retrying automatically in 30 seconds." in str(error)

    def test_first_transient_failure_retries_after_cooldown(self):
        router = _FakeRouter([_deployment("dpl_c", "C")], pinned_tag="C")
        router.fail("dpl_c", cooldown=60.0, consecutive=1)

        error = _translate(
            AllDeploymentsFailed(model="*", errors=[("dpl_c", "Deployment 'dpl_c' returned HTTP 502 (server error)")]),
            router,
        )

        assert error.details["route_case"] == "exhausted_retryable"
        assert error.details["retry_after_seconds"] == 60.0
        assert "returned a server error (HTTP 502)" in str(error)
        _assert_readable(error)

    def test_repeated_transient_failure_requires_user(self):
        router = _FakeRouter([_deployment("dpl_c", "C")], pinned_tag="C")
        router.fail("dpl_c", cooldown=120.0, consecutive=2)

        error = _translate(
            AllDeploymentsFailed(model="*", errors=[("dpl_c", "Deployment 'dpl_c' returned HTTP 502 (server error)")]),
            router,
        )

        assert error.details["reason"] == "transient_repeated"
        assert error.details["user_action_required"] is True
        assert "Still unavailable after retrying." in str(error)

    def test_interrupted_answer_without_alternative_requires_user(self):
        router = _FakeRouter([_deployment("dpl_c", "C")], pinned_tag="C")
        router.fail("dpl_c")

        error = _translate(DeploymentNetworkError(deployment_id="dpl_c", reason="closed"), router,
                           output_emitted=True)

        assert error.details["route_case"] == "exhausted_user_action"
        assert error.details["reason"] == "stream_interrupted"
        assert "This member is set to use 'C' only" in str(error)
        _assert_readable(error)

    def test_model_missing_from_group(self):
        router = _FakeRouter([_deployment("dpl_a", "A")], pinned_tag="C")

        error = _translate(NoDeploymentAvailable("*", "No deployment matched routing strategy"), router)

        assert error.details["reason"] == "tag_not_found"
        assert str(error) == (
            "[181010] The model 'C' is not in the selected model group. "
            "Please choose another model or add it back to the model group."
        )

    def test_unrecorded_cooling_deployment_counts_as_transient(self):
        router = _FakeRouter([_deployment("dpl_c", "C")], pinned_tag="C")
        router.fail("dpl_c")

        error = _translate(NoDeploymentAvailable("*", "No deployment matched routing strategy"), router)

        assert error.details["route_case"] == "exhausted_retryable"
        assert "is temporarily unavailable" in str(error)


class TestRequestRejected:
    def test_every_model_rejecting_the_request_is_not_escalated(self):
        router = _FakeRouter([_deployment("dpl_c", "C")])
        router.fail("dpl_c")

        error = _translate(_bad_request("dpl_c"), router)

        assert error.details["route_case"] == "exhausted_request_rejected"
        assert error.details["user_action_required"] is False
        assert error.details["should_retry"] is False
        assert str(error) == (
            "[181010] The model 'C' (openai, api.a.com) rejected the request (HTTP 400): "
            "maximum context length is 32768 tokens."
        )

    def test_rejecting_models_are_released_for_a_smaller_retry(self):
        router = _FakeRouter([_deployment("dpl_c", "C")])
        router.fail("dpl_c")

        _translate(_bad_request("dpl_c"), router)

        assert router.state.deployment_status["dpl_c"] == DeploymentStatus.HEALTHY

    def test_context_overflow_text_stays_detectable(self):
        from openjiuwen.core.context_engine.context_engine import ContextEngine

        router = _FakeRouter([_deployment("dpl_c", "C")])
        router.fail("dpl_c")

        error = _translate(_bad_request("dpl_c"), router)

        assert ContextEngine.is_context_overflow_error(error)


class TestFailureOrder:
    def test_triggering_deployment_is_reported_first(self):
        router = _FakeRouter([_deployment("dpl_a", "A"), _deployment("dpl_c", "C"), _deployment("dpl_d", "D")])
        router.fail("dpl_a")
        router.fail("dpl_c")

        error = _translate(DeploymentAuthError(deployment_id="dpl_c", status_code=401), router)

        assert error.details["failures"][0]["deployment_id"] == "dpl_c"
        assert [f["position"] for f in error.details["failures"]] == [2, 1]


class TestRoutingEvents:
    @pytest.mark.asyncio
    async def test_bridge_records_reason_of_router_internal_failover(self):
        router = _FakeRouter([_deployment("dpl_c", "C")], pinned_tag="C")
        router.fail("dpl_c", consecutive=2)
        event = SimpleNamespace(
            event_type=SimpleNamespace(value="request_retried"),
            deployment_id="dpl_c",
            error_type="DeploymentServerError",
            error_message="Deployment 'dpl_c' returned HTTP 503 (server error)",
            extra={"model_group_id": "g1", "route_id": "dpl_c"},
        )

        await _CoreRoutingEventBridge().handle_event(event)
        error = _translate(NoDeploymentAvailable("*", "No deployment matched routing strategy"), router)

        assert error.details["failures"][0]["status_code"] == 503
        assert error.details["failures"][0]["category"] == "transient"

    @pytest.mark.asyncio
    async def test_success_event_clears_the_old_reason(self):
        router = _FakeRouter([_deployment("dpl_c", "C")], pinned_tag="C")
        router.fail("dpl_c")
        _translate(DeploymentAuthError(deployment_id="dpl_c", status_code=401), router)
        success = SimpleNamespace(
            event_type=SimpleNamespace(value="request_succeeded"),
            deployment_id="dpl_c",
            error_message=None,
            extra={"model_group_id": "g1", "route_id": "dpl_c"},
        )

        await _CoreRoutingEventBridge().handle_event(success)
        error = _translate(NoDeploymentAvailable("*", "No deployment matched routing strategy"), router)

        assert error.details["route_case"] == "exhausted_retryable"
        assert "HTTP 401" not in str(error)


def _client(router) -> IntelliRouterModelClient:
    config = ModelClientConfig(client_provider=ProviderType.IntelliRouter, verify_ssl=False)
    return IntelliRouterModelClient(ModelRequestConfig(model="*"), config, router=router)


def _router_mock(fake: _FakeRouter) -> MagicMock:
    mock = MagicMock()
    mock.deployments = fake.deployments
    mock.model_group_id = fake.model_group_id
    mock.strategy = fake.strategy
    mock.state = fake.state
    return mock


class TestClientTranslation:
    @pytest.mark.asyncio
    async def test_invoke_raises_framework_error(self):
        fake = _FakeRouter([_deployment("dpl_c", "C")], pinned_tag="C")
        fake.fail("dpl_c")
        router = _router_mock(fake)
        router.invoke = AsyncMock(side_effect=DeploymentAuthError(deployment_id="dpl_c", status_code=401))

        with pytest.raises(BaseError) as caught:
            await _client(router).invoke([UserMessage(content="hi")])

        assert caught.value.status == StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE
        assert isinstance(caught.value.__cause__, DeploymentAuthError)

    @pytest.mark.asyncio
    async def test_stream_marks_output_emitted_after_visible_chunk(self):
        fake = _FakeRouter([_deployment("dpl_c", "C"), _deployment("dpl_d", "D")])
        fake.fail("dpl_c")
        router = _router_mock(fake)

        async def failing_stream(**_kwargs):
            yield SimpleNamespace(content="partial", finish_reason="null", tool_calls=None, reasoning_content=None)
            raise DeploymentNetworkError(deployment_id="dpl_c", reason="closed")

        router.stream = MagicMock(side_effect=failing_stream)
        chunks = []
        with pytest.raises(BaseError) as caught:
            async for chunk in _client(router).stream([UserMessage(content="hi")]):
                chunks.append(chunk)

        assert [chunk.content for chunk in chunks] == ["partial"]
        assert caught.value.details["route_case"] == "switchable"
        assert caught.value.details["output_emitted"] is True

    @pytest.mark.asyncio
    async def test_rejected_request_is_translated(self):
        fake = _FakeRouter([_deployment("dpl_c", "C")])
        fake.fail("dpl_c")
        router = _router_mock(fake)
        router.invoke = AsyncMock(side_effect=_bad_request("dpl_c"))

        with pytest.raises(BaseError) as caught:
            await _client(router).invoke([UserMessage(content="hi")])

        assert caught.value.details["route_case"] == "exhausted_request_rejected"
        assert "maximum context length" in str(caught.value)
