# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for openjiuwen.extensions.context_evolver.offline_memory.l2.reflect_pair."""

from __future__ import annotations

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.extensions.context_evolver.offline_memory.l2 import reflect_pair

from tests.unit_tests.fixtures.mock_llm import create_json_response, create_text_response, mock_llm_context


def _model() -> Model:
    return Model(
        ModelClientConfig(client_provider="OpenAI", api_key="mock-api-key", api_base="http://mock", verify_ssl=False),
        ModelRequestConfig(model_name="mock-model"),
    )


class TestReflectPair:
    @pytest.mark.asyncio
    async def test_parses_valid_response(self) -> None:
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([
                create_json_response({
                    "correlation_notes": "Communicates clearly and follows up promptly.",
                    "profile": {"reliability": "high", "strengths": ["clear communicator"]},
                })
            ])
            result = await reflect_pair(
                _model(),
                reflecting_role="researcher",
                partner_role="writer",
                evidence_block="[t1] researcher -> writer: here's the draft data",
                existing_notes="",
            )
        assert result.correlation_notes == "Communicates clearly and follows up promptly."
        assert result.profile == {"reliability": "high", "strengths": ["clear communicator"]}

    @pytest.mark.asyncio
    async def test_missing_fields_default_empty(self) -> None:
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_json_response({})])
            result = await reflect_pair(
                _model(),
                reflecting_role="researcher",
                partner_role="writer",
                evidence_block="(no direct messages found)",
                existing_notes="",
            )
        assert result.correlation_notes == ""
        assert result.profile == {}

    @pytest.mark.asyncio
    async def test_retries_then_raises_on_persistent_malformed_json(self) -> None:
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_text_response("not json") for _ in range(3)])
            with pytest.raises(BaseError):
                await reflect_pair(
                    _model(),
                    reflecting_role="researcher",
                    partner_role="writer",
                    evidence_block="evidence",
                    existing_notes="",
                    retries=3,
                )
        assert mock_llm.call_count == 3

    @pytest.mark.asyncio
    async def test_recovers_after_one_bad_attempt(self) -> None:
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([
                create_text_response("not json"),
                create_json_response({"correlation_notes": "ok", "profile": {}}),
            ])
            result = await reflect_pair(
                _model(),
                reflecting_role="researcher",
                partner_role="writer",
                evidence_block="evidence",
                existing_notes="",
                retries=3,
            )
        assert result.correlation_notes == "ok"
        assert mock_llm.call_count == 2
