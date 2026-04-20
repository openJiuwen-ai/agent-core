"""Unit tests for openjiuwen.harness.cli.rails.token_tracker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from openjiuwen.harness.cli.rails.token_tracker import TokenTrackingRail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class FakeResponse:
    usage: Optional[FakeUsage] = None


@dataclass
class FakeInputs:
    response: Optional[FakeResponse] = None


def _make_ctx(
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
) -> Any:
    """Build a minimal AgentCallbackContext mock."""
    ctx = MagicMock()
    if prompt_tokens is None and completion_tokens is None:
        ctx.inputs = FakeInputs(
            response=FakeResponse(usage=None)
        )
    else:
        ctx.inputs = FakeInputs(
            response=FakeResponse(
                usage=FakeUsage(
                    prompt_tokens=prompt_tokens or 0,
                    completion_tokens=completion_tokens or 0,
                )
            )
        )
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTokenTrackingRail:
    """Tests for token usage tracking."""

    @pytest.mark.asyncio
    async def test_tracks_usage(self) -> None:
        """Single call usage is recorded."""
        tracker = TokenTrackingRail()
        ctx = _make_ctx(
            prompt_tokens=100, completion_tokens=50
        )
        await tracker.after_model_call(ctx)
        assert tracker.total_input_tokens == 100
        assert tracker.total_output_tokens == 50
        assert tracker.call_count == 1

    @pytest.mark.asyncio
    async def test_accumulates_across_calls(self) -> None:
        """Usage accumulates over multiple calls."""
        tracker = TokenTrackingRail()
        await tracker.after_model_call(
            _make_ctx(prompt_tokens=100, completion_tokens=50)
        )
        await tracker.after_model_call(
            _make_ctx(prompt_tokens=200, completion_tokens=80)
        )
        summary = tracker.get_summary()
        assert summary["input_tokens"] == 300
        assert summary["output_tokens"] == 130
        assert summary["total_tokens"] == 430
        assert summary["model_calls"] == 2

    @pytest.mark.asyncio
    async def test_handles_missing_usage(self) -> None:
        """No crash when response has no usage attribute."""
        tracker = TokenTrackingRail()
        ctx = _make_ctx()
        await tracker.after_model_call(ctx)
        assert tracker.total_input_tokens == 0
        assert tracker.total_output_tokens == 0
        assert tracker.call_count == 1

    @pytest.mark.asyncio
    async def test_handles_missing_response(self) -> None:
        """No crash when ctx.inputs has no response."""
        tracker = TokenTrackingRail()
        ctx = MagicMock()
        ctx.inputs = MagicMock(spec=[])  # no 'response' attr
        await tracker.after_model_call(ctx)
        assert tracker.call_count == 1
        assert tracker.total_input_tokens == 0

    def test_get_summary_initial(self) -> None:
        """Summary is all zeros before any calls."""
        tracker = TokenTrackingRail()
        s = tracker.get_summary()
        assert s["input_tokens"] == 0
        assert s["output_tokens"] == 0
        assert s["total_tokens"] == 0
        assert s["model_calls"] == 0
