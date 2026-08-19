# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the read_file image-modality probe."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness import image_modality_probe
from openjiuwen.harness.image_modality_probe import (
    get_cached_image_support,
    probe_cache_key,
    probe_image_support,
    reset_image_support_cache,
    schedule_image_support_probe,
)


class _FakeClientConfig:
    def __init__(self, api_base: str) -> None:
        self.api_base = api_base


class _FakeModelConfig:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name


class _FakeResponse:
    def __init__(self, content: Any, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason


def _make_llm(
    *,
    api_base: str = "http://localhost:4000/v1",
    model_name: str = "openai/GLM-5",
    invoke: Any = None,
) -> Any:
    llm = type("_FakeModel", (), {})()
    llm.model_client_config = _FakeClientConfig(api_base)
    llm.model_config = _FakeModelConfig(model_name)
    llm.invoke = invoke if invoke is not None else AsyncMock(return_value=_FakeResponse("red"))
    return llm


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_image_support_cache()
    yield
    reset_image_support_cache()


def test_probe_cache_key_needs_model_name() -> None:
    assert probe_cache_key(_make_llm()) == ("http://localhost:4000/v1", "openai/GLM-5")
    assert probe_cache_key(_make_llm(model_name="  ")) is None


@pytest.mark.asyncio
async def test_probe_uses_small_budget_and_disables_thinking() -> None:
    invoke = AsyncMock(return_value=_FakeResponse("Red"))
    llm = _make_llm(invoke=invoke)

    assert await probe_image_support(llm) is True

    kwargs = invoke.await_args.kwargs
    assert kwargs["max_tokens"] == image_modality_probe._IMAGE_MODALITY_PROBE_MAX_TOKENS
    # Small enough to cap a reasoning model, roomy enough that a short preamble
    # before the color does not truncate the answer.
    assert 8 < kwargs["max_tokens"] <= 64
    assert kwargs["extra_body"] == image_modality_probe._THINKING_DISABLED_EXTRA_BODY


@pytest.mark.asyncio
async def test_probe_result_is_cached_per_endpoint_and_model() -> None:
    invoke = AsyncMock(return_value=_FakeResponse("red"))
    llm = _make_llm(invoke=invoke)

    assert await probe_image_support(llm) is True
    assert await probe_image_support(llm) is True
    assert invoke.await_count == 1
    assert get_cached_image_support(llm) is True

    other = _make_llm(model_name="other-model", invoke=AsyncMock(return_value=_FakeResponse("blue")))
    assert get_cached_image_support(other) is None
    assert await probe_image_support(other) is False


@pytest.mark.asyncio
async def test_probe_retries_without_vendor_switches() -> None:
    invoke = AsyncMock(side_effect=[TypeError("unknown field: thinking"), _FakeResponse("red")])
    llm = _make_llm(invoke=invoke)

    assert await probe_image_support(llm) is True
    assert invoke.await_count == 2
    assert "extra_body" not in invoke.await_args.kwargs


@pytest.mark.asyncio
async def test_probe_without_content_is_inconclusive() -> None:
    llm = _make_llm(invoke=AsyncMock(return_value=_FakeResponse("   ")))

    assert await probe_image_support(llm) is None
    assert get_cached_image_support(llm) is None


@pytest.mark.asyncio
async def test_probe_truncated_before_the_color_is_inconclusive() -> None:
    """A capable model cut off mid-answer must not be cached as image-blind."""
    truncated = _FakeResponse("The image is a solid block of colo", finish_reason="length")
    llm = _make_llm(invoke=AsyncMock(return_value=truncated))

    assert await probe_image_support(llm) is None
    assert get_cached_image_support(llm) is None


@pytest.mark.asyncio
async def test_probe_naming_the_color_wins_over_truncation() -> None:
    """Hitting the budget after naming the color is still a conclusive yes."""
    answered = _FakeResponse("It is red, a solid block of", finish_reason="length")
    llm = _make_llm(invoke=AsyncMock(return_value=answered))

    assert await probe_image_support(llm) is True
    assert get_cached_image_support(llm) is True


@pytest.mark.asyncio
async def test_probe_answering_another_color_is_unsupported() -> None:
    """A complete answer that never names the color is a real negative."""
    llm = _make_llm(invoke=AsyncMock(return_value=_FakeResponse("blue")))

    assert await probe_image_support(llm) is False
    assert get_cached_image_support(llm) is False


def test_schedule_outside_a_running_loop_is_a_no_op() -> None:
    """Configuration can happen off-loop; there is nothing to schedule onto."""
    invoke = AsyncMock(return_value=_FakeResponse("red"))
    llm = _make_llm(invoke=invoke)

    schedule_image_support_probe(llm)

    assert not image_modality_probe._probe_tasks
    invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_rejection_is_cached_as_unsupported() -> None:
    llm = _make_llm(invoke=AsyncMock(side_effect=ValueError("no endpoints found that support image input")))

    assert await probe_image_support(llm) is False
    assert get_cached_image_support(llm) is False


@pytest.mark.asyncio
async def test_probe_recognizes_not_a_multimodal_model_without_retry() -> None:
    invoke = AsyncMock(
        side_effect=ValueError(
            "API returned error 400: Qwen3-235B-A22B-W8A8 "
            "is not a multimodal model"
        )
    )
    llm = _make_llm(invoke=invoke)

    assert await probe_image_support(llm) is False
    assert get_cached_image_support(llm) is False
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_scheduled_probe_runs_once_and_fills_cache() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_invoke(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        started.set()
        await release.wait()
        return _FakeResponse("red")

    invoke = AsyncMock(side_effect=_slow_invoke)
    llm = _make_llm(invoke=invoke)

    schedule_image_support_probe(llm)
    schedule_image_support_probe(llm)
    await started.wait()
    # Nothing is blocked on the verdict while the probe is in flight.
    assert get_cached_image_support(llm) is None

    release.set()
    await asyncio.gather(*image_modality_probe._probe_tasks.values())

    assert invoke.await_count == 1
    assert get_cached_image_support(llm) is True


@pytest.mark.asyncio
async def test_scheduled_probe_skips_cached_endpoint() -> None:
    invoke = AsyncMock(return_value=_FakeResponse("red"))
    llm = _make_llm(invoke=invoke)
    image_modality_probe._probe_results[probe_cache_key(llm)] = False

    schedule_image_support_probe(llm)

    assert not image_modality_probe._probe_tasks
    invoke.assert_not_awaited()
