"""Focused tests for structured paper-scoring completions."""

import pytest
from pydantic import BaseModel

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.llm import (
    StructuredCompleter,
    StructuredCompletionError,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.schemas import (
    FigureAsset,
)


class _Review(BaseModel):
    score: int


def _figure() -> FigureAsset:
    return FigureAsset(
        figure_id="fig-1",
        section_id="sec-019",
        canonical_section="results",
        source_path="figures/results.pdf",
        mime_type="image/png",
        sha256="0" * 64,
        width=10,
        height=10,
        png_bytes=b"fake-png-bytes",
    )


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


class _VisionRejectingModel:
    """Rejects any call whose user content carries image parts, like an
    OpenRouter model/route with no vision-capable endpoint (404 'No
    endpoints found that support image input')."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    def invoke(self, messages):
        self.calls.append(messages)
        if isinstance(messages[-1]["content"], list):
            raise RuntimeError("No endpoints found that support image input")
        return '{"score": 7}'


@pytest.mark.asyncio
async def test_structured_completion_falls_back_to_text_when_model_rejects_images():
    model = _VisionRejectingModel()
    completer = StructuredCompleter(
        {"paper_scoring": {"max_validation_retries": 1}},
        model=model,
    )

    result = await completer.complete(
        _Review,
        system="Review the paper.",
        user="Return a score.",
        images=[_figure()],
    )

    assert result.score == 7
    assert len(model.calls) == 2
    assert isinstance(model.calls[0][-1]["content"], list), "first call still attempted images"
    assert model.calls[1][-1]["content"] == "Return a score.", "fallback call must drop images"
    assert completer._vision_unsupported is True


@pytest.mark.asyncio
async def test_structured_completion_skips_images_after_first_rejection():
    model = _VisionRejectingModel()
    completer = StructuredCompleter(
        {"paper_scoring": {"max_validation_retries": 1}},
        model=model,
    )

    await completer.complete(_Review, system="Review the paper.", user="First.", images=[_figure()])
    await completer.complete(_Review, system="Review the paper.", user="Second.", images=[_figure()])

    # 2 calls for the first request (image attempt + text fallback), then a
    # single text-only call for the second request -- no repeat 404.
    assert len(model.calls) == 3
    assert all(not isinstance(call[-1]["content"], list) for call in model.calls[1:])
