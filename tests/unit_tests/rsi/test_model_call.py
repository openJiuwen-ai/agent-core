# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for model-call retry guards."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.rsi.harness_rsi.model_call import (
    DEFAULT_MODEL_CALL_MAX_RETRIES,
    RetryableModelOutputError,
    model_output_has_mojibake,
    run_model_call_with_retries,
)


def test_default_model_call_retry_budget_is_twenty() -> None:
    assert DEFAULT_MODEL_CALL_MAX_RETRIES == 20


@pytest.mark.asyncio
async def test_retries_when_model_output_contains_mojibake() -> None:
    attempts = 0

    async def call_model() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return "璇蜂负鏂拌兘婧愪紒涓氬埗浣滅綉椤"
        return '{"ok": true}'

    result = await run_model_call_with_retries(
        call_model,
        operation_name="dataset generation",
        max_retries=2,
    )

    assert result == '{"ok": true}'
    assert attempts == 2


@pytest.mark.asyncio
async def test_retries_when_model_call_times_out() -> None:
    attempts = 0

    async def call_model() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncio.TimeoutError("model request timed out")
        return '{"ok": true}'

    result = await run_model_call_with_retries(
        call_model,
        operation_name="judge",
        max_retries=2,
        initial_retry_delay_seconds=0,
    )

    assert result == '{"ok": true}'
    assert attempts == 2


@pytest.mark.asyncio
async def test_retries_when_model_gateway_returns_502() -> None:
    attempts = 0

    async def call_model() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError(
                "openAI API async invoke error: <html><head><title>502 Bad Gateway</title></head></html>"
            )
        return '{"ok": true}'

    result = await run_model_call_with_retries(
        call_model,
        operation_name="diagnosis agent",
        max_retries=2,
        initial_retry_delay_seconds=0,
    )

    assert result == '{"ok": true}'
    assert attempts == 2


@pytest.mark.asyncio
async def test_retries_rate_limit_but_not_exhausted_budget() -> None:
    attempts = 0

    async def rate_limited_call() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Error code: 429 - rate limit exceeded")
        return '{"ok": true}'

    result = await run_model_call_with_retries(
        rate_limited_call,
        operation_name="diagnosis agent",
        max_retries=2,
        initial_retry_delay_seconds=0,
    )
    assert result == '{"ok": true}'
    assert attempts == 2

    async def exhausted_budget_call() -> str:
        raise RuntimeError("Error code: 429 - Budget has been exceeded; code=budget_exceeded")

    with pytest.raises(RuntimeError, match="Budget has been exceeded"):
        await run_model_call_with_retries(
            exhausted_budget_call,
            operation_name="diagnosis agent",
            max_retries=2,
            initial_retry_delay_seconds=0,
        )


@pytest.mark.asyncio
async def test_retries_transient_connector_database_recovery_reported_as_401() -> None:
    attempts = 0

    async def call_model() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError(
                "Error code: 401 - Authentication Error, Error in connector: "
                "FATAL: the database system is not yet accepting connections; "
                "DETAIL: Consistent recovery state has not been yet reached."
            )
        return '{"ok": true}'

    result = await run_model_call_with_retries(
        call_model,
        operation_name="diagnosis agent",
        max_retries=2,
        initial_retry_delay_seconds=0,
    )

    assert result == '{"ok": true}'
    assert attempts == 2


@pytest.mark.asyncio
async def test_does_not_retry_genuine_authentication_failure() -> None:
    attempts = 0

    async def call_model() -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("Error code: 401 - Authentication failed: invalid API key")

    with pytest.raises(RuntimeError, match="invalid API key"):
        await run_model_call_with_retries(
            call_model,
            operation_name="diagnosis agent",
            max_retries=2,
            initial_retry_delay_seconds=0,
        )

    assert attempts == 1


@pytest.mark.asyncio
async def test_retries_when_model_output_is_empty() -> None:
    attempts = 0

    async def call_model() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return ""
        return '{"ok": true}'

    result = await run_model_call_with_retries(
        call_model,
        operation_name="dataset generation",
        max_retries=2,
    )

    assert result == '{"ok": true}'
    assert attempts == 2


@pytest.mark.asyncio
async def test_retries_when_model_output_is_whitespace() -> None:
    attempts = 0

    async def call_model() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return " \n\t "
        return '{"ok": true}'

    result = await run_model_call_with_retries(
        call_model,
        operation_name="dataset generation",
        max_retries=2,
    )

    assert result == '{"ok": true}'
    assert attempts == 2


@pytest.mark.asyncio
async def test_raises_after_retry_budget_is_exhausted() -> None:
    async def call_model() -> str:
        return "\ufffd\ufffd\ufffd\ufffd"

    with pytest.raises(RetryableModelOutputError, match="mojibake"):
        await run_model_call_with_retries(
            call_model,
            operation_name="dataset generation",
            max_retries=1,
        )


@pytest.mark.asyncio
async def test_returns_repaired_output_after_retry_budget_is_exhausted() -> None:
    readable = "\u8bf7\u4e3a\u65b0\u80fd\u6e90\u50a8\u80fd\u4f01\u4e1a\u5236\u4f5c\u878d\u8d44\u7f51\u9875"
    repairable = readable.encode("utf-8").decode("gbk", errors="ignore")
    attempts = 0

    async def call_model() -> str:
        nonlocal attempts
        attempts += 1
        return repairable

    result = await run_model_call_with_retries(
        call_model,
        operation_name="dataset generation",
        max_retries=1,
    )

    assert result == repairable.encode("gbk", errors="ignore").decode("utf-8", errors="ignore")
    assert result.startswith("请为新能源储能企业制作融资网")
    assert attempts == 2


def test_mojibake_detector_does_not_flag_normal_chinese() -> None:
    assert not model_output_has_mojibake(
        "\u8bf7\u4e3a\u65b0\u80fd\u6e90\u50a8\u80fd\u4f01\u4e1a\u5236\u4f5c\u878d\u8d44\u7f51\u9875\u3002"
    )


def test_mojibake_detector_does_not_flag_normal_financing_artifact_json() -> None:
    text = (
        "  {\n"
        '      "case_id": "energy_storage_pitch_deck_slide_alloc_002",\n'
        '      "dataset_id": "dataset_001",\n'
        '      "schema_version": "1.0",\n'
        '      "source": "llm_synthetic_evaluation_dataset",\n'
        '      "task_type": "pitch_deck_creation",\n'
        '      "input": {\n'
        '        "user_message": "\u4e3a\u300c\u9502\u661f\u50a8\u80fd\u300d\u5236'
        "\u4f5c\u4e00\u4efd\u9762\u5411\u4ea7\u4e1a\u6295\u8d44\u4eba\u548c"
        "\u4f01\u4e1aCFO\u76848\u9875\u878d\u8d44\u8def\u6f14PPT\u3002"
        "\u9502\u661f\u50a8\u80fd\u662f\u4e00\u5bb6\u4e13\u6ce8\u5de5\u5546"
        "\u4e1a\u4fa7\u9502\u7535\u50a8\u80fd\u7cfb\u7edf\u7684\u516c\u53f8"
        "\uff0c\u5df2\u4ea4\u4ed812\u4e2a\u9879\u76ee\u3001\u7d2f\u8ba1"
        "\u88c5\u673a45MWh\u3001\u5ba2\u6237\u590d\u8d2d\u738768%\u3002"
        "\u672c\u8f6e\u5bfb\u6c42B\u8f6e\u878d\u8d448000\u4e07\u5143\u3002"
        "PPT\u5fc5\u987b\u8986\u76d67\u4e2a\u89c4\u5b9a\u8bae\u9898\uff1a"
        "\u5e02\u573a\u673a\u4f1a\u3001\u4ea7\u54c1\u65b9\u6848\u3001"
        "\u6280\u672f\u58c1\u5792\u3001\u5546\u4e1a"
    )

    assert not model_output_has_mojibake(text)


def test_mojibake_detector_does_not_flag_normal_non_cjk_languages() -> None:
    assert not model_output_has_mojibake(
        "Use \u03b1, \u03b2, \u03b3 as coefficients in a formula. "
        "\u042d\u0442\u043e \u043d\u043e\u0440\u043c\u0430\u043b\u044c\u043d\u044b\u0439 "
        "\u0440\u0443\u0441\u0441\u043a\u0438\u0439 \u0442\u0435\u043a\u0441\u0442."
    )
