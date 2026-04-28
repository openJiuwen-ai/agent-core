# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for evolution LLM resilience helper."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError


class TestInvokeTextWithRetry:
    @pytest.mark.asyncio
    async def test_retries_with_retry_prompt_after_timeout_then_succeeds(self):
        llm = Mock()
        llm.invoke = AsyncMock(
            side_effect=[
                asyncio.TimeoutError("request timed out"),
                SimpleNamespace(content='{"ok": true}'),
            ]
        )

        result = await invoke_text_with_retry(
            llm=llm,
            model="test-model",
            prompt="full prompt",
            retry_prompt="short prompt",
            policy=LLMInvokePolicy(
                attempt_timeout_secs=5,
                total_budget_secs=10,
                max_attempts=2,
                backoff_base_secs=0,
            ),
        )

        assert result == '{"ok": true}'
        assert llm.invoke.await_count == 2
        assert llm.invoke.await_args_list[0].kwargs["messages"][0]["content"] == "full prompt"
        assert llm.invoke.await_args_list[1].kwargs["messages"][0]["content"] == "short prompt"

    @pytest.mark.asyncio
    async def test_does_not_use_retry_prompt_for_non_timeout_error(self):
        llm = Mock()
        llm.invoke = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(BaseError) as exc_info:
            await invoke_text_with_retry(
                llm=llm,
                model="test-model",
                prompt="full prompt",
                retry_prompt="short prompt",
                policy=LLMInvokePolicy(
                    attempt_timeout_secs=5,
                    total_budget_secs=10,
                    max_attempts=2,
                    backoff_base_secs=0,
                ),
            )

        assert exc_info.value.status == StatusCode.TOOLCHAIN_EVOLVING_TOOL_CALL_LLM_CALL_EXECUTION_ERROR
        assert llm.invoke.await_count == 1
        assert llm.invoke.await_args_list[0].kwargs["messages"][0]["content"] == "full prompt"

    @pytest.mark.asyncio
    async def test_retries_on_empty_response_then_succeeds(self):
        llm = Mock()
        llm.invoke = AsyncMock(
            side_effect=[
                SimpleNamespace(content="   "),
                SimpleNamespace(content='{"ok": true}'),
            ]
        )

        result = await invoke_text_with_retry(
            llm=llm,
            model="test-model",
            prompt="hello",
            policy=LLMInvokePolicy(
                attempt_timeout_secs=5,
                total_budget_secs=10,
                max_attempts=2,
                backoff_base_secs=0,
            ),
        )

        assert result == '{"ok": true}'
        assert llm.invoke.await_count == 2
        assert llm.invoke.await_args_list[0].kwargs["timeout"] == 5

    @pytest.mark.asyncio
    async def test_retries_on_unusable_response_then_succeeds(self):
        llm = Mock()
        llm.invoke = AsyncMock(
            side_effect=[
                SimpleNamespace(content="not json"),
                SimpleNamespace(content='{"ok": true}'),
            ]
        )

        result = await invoke_text_with_retry(
            llm=llm,
            model="test-model",
            prompt="hello",
            policy=LLMInvokePolicy(
                attempt_timeout_secs=5,
                total_budget_secs=10,
                max_attempts=2,
                backoff_base_secs=0,
            ),
            is_result_usable=lambda raw: raw.startswith("{"),
        )

        assert result == '{"ok": true}'
        assert llm.invoke.await_count == 2

    @pytest.mark.asyncio
    async def test_raises_when_total_budget_exceeded(self):
        async def slow_empty_response(**_: object) -> SimpleNamespace:
            await asyncio.sleep(0.05)
            return SimpleNamespace(content="")

        llm = Mock()
        llm.invoke = AsyncMock(side_effect=slow_empty_response)

        started_at = time.monotonic()
        with pytest.raises(BaseError) as exc_info:
            await invoke_text_with_retry(
                llm=llm,
                model="test-model",
                prompt="hello",
                policy=LLMInvokePolicy(
                    attempt_timeout_secs=1,
                    total_budget_secs=0.01,
                    max_attempts=2,
                    backoff_base_secs=0,
                ),
            )
        elapsed = time.monotonic() - started_at

        assert exc_info.value.status == StatusCode.TOOLCHAIN_EVOLVING_TOOL_CALL_LLM_CALL_EXECUTION_ERROR
        assert exc_info.value.details["reason"] == "total_budget_exceeded"
        assert exc_info.value.details["attempts"] == 1
        assert llm.invoke.await_count == 1
        assert elapsed < 0.04
