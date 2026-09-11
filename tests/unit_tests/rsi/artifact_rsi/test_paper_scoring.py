"""Focused tests for structured paper-scoring completions."""

import pytest
from pydantic import BaseModel

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.llm import (
    StructuredCompletionError,
    StructuredCompleter,
)


class _Review(BaseModel):
    score: int


@pytest.mark.asyncio
async def test_structured_completion_retries_invalid_json_until_valid():
    responses = ["{score: 1}", "not json", '{"score": 5}']
    calls = []

    async def complete_fn(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    completer = StructuredCompleter(
        {"paper_scoring": {"max_validation_retries": 3}},
        complete_fn=complete_fn,
    )

    result = await completer.complete(
        _Review,
        system="Review the paper.",
        user="Return a score.",
    )

    assert result.score == 5
    assert len(calls) == 3
    assert "previous response was invalid" in calls[1]["user"]
    assert "previous response was invalid" in calls[2]["user"]


@pytest.mark.asyncio
async def test_structured_completion_stops_after_three_retries():
    calls = []

    async def complete_fn(**kwargs):
        calls.append(kwargs)
        return "not json"

    completer = StructuredCompleter(
        {"paper_scoring": {"max_validation_retries": 3}},
        complete_fn=complete_fn,
    )

    with pytest.raises(StructuredCompletionError, match="failed to obtain valid _Review"):
        await completer.complete(
            _Review,
            system="Review the paper.",
            user="Return a score.",
        )

    assert len(calls) == 4
