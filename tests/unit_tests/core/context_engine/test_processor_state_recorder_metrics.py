# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compression-state metric sources and provider-scale calibration."""

from __future__ import annotations

import pytest

from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.context.processor_state_recorder import (
    ContextProcessorStateInput,
    ContextProcessorStateRecorder,
)
from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata, UserMessage


class _CharCounter:
    """Deterministic token counter: one token per character + len//4."""

    def count_messages(self, messages):
        return sum(len(str(getattr(message, "content", "") or "")) for message in messages)

    def count(self, text, **_kwargs):
        return len(str(text or "")) // 4


class _SequencedCounter:
    """Return pre-seeded ``count_messages`` results in call order."""

    def __init__(self, counts):
        self._counts = list(counts)

    def count_messages(self, _messages):
        return self._counts.pop(0)

    def count(self, _text, **_kwargs):
        return 0


def _recorder() -> ContextProcessorStateRecorder:
    return ContextProcessorStateRecorder(
        session_id="session-1",
        context_id="default_context_id",
        get_session_ref=lambda: None,
        token_counter=_CharCounter(),
    )


def _state_input(
        *,
        before_messages,
        after_messages,
        overhead_tokens=40_000,
        status="completed",
        token_scale_factor=None,
        token_scale_eligible=True,
):
    return ContextProcessorStateInput(
        operation_id="op-1",
        status=status,
        phase="get_context_window",
        trigger="passive",
        processor=None,
        reason="processor_completed",
        before_messages=before_messages,
        after_messages=after_messages,
        started_at=1_700_000_000.0,
        ended_at=1_700_000_001.0,
        error=None,
        messages_to_modify=[],
        force=False,
        context_max=200_000,
        compact_summary="summary",
        overhead_tokens=overhead_tokens,
        token_scale_factor=token_scale_factor,
        token_scale_eligible=token_scale_eligible,
    )


def test_before_uses_usage_baseline_and_after_is_calibrated():
    before_messages = [
        AssistantMessage(
            content="a" * 200_000,
            usage_metadata=UsageMetadata(total_tokens=180_000),
        )
    ]
    state = _recorder().build_state(_state_input(
        before_messages=before_messages,
        after_messages=[UserMessage(content="b" * 60_000)],
    ))

    # before: provider usage 180k, not the 240k local raw count
    assert state.before.tokens == 180_000
    assert state.before.token_source == "usage+tail"
    # after: raw 100k scaled by 180k/240k -> 75k
    assert state.after is not None
    assert state.after.tokens == 75_000
    assert state.after.token_source == "tokenizer-calibrated"
    assert state.saved is not None
    assert state.saved.tokens == 105_000
    assert state.saved.percent == 58.3


def test_before_without_usage_stays_on_tokenizer_scale():
    state = _recorder().build_state(_state_input(
        before_messages=[UserMessage(content="a" * 200_000)],
        after_messages=[UserMessage(content="b" * 60_000)],
    ))

    assert state.before.tokens == 240_000
    assert state.before.token_source == "tokenizer"
    assert state.after is not None
    assert state.after.tokens == 100_000
    assert state.after.token_source == "tokenizer"
    assert state.saved is not None
    assert state.saved.percent == 58.3


def test_real_case_calibration_matches_observed_provider_scale():
    """2026-11-21 奥雅团队 leader 的压缩事件数值回归。"""
    recorder = ContextProcessorStateRecorder(
        session_id="desktop-1",
        context_id="default_context_id",
        get_session_ref=lambda: None,
        token_counter=_SequencedCounter([289_455, 30_482]),
    )
    before_messages = [
        AssistantMessage(
            content="history",
            usage_metadata=UsageMetadata(total_tokens=216_830),
        ),
        UserMessage(content="x" * 1_724),  # tail = 431 tokens via len//4
    ]
    state = recorder.build_state(_state_input(
        before_messages=before_messages,
        after_messages=[UserMessage(content="summary")],
        overhead_tokens=40_112,
    ))

    assert state.before.tokens == 217_261
    assert state.before.token_source == "usage+tail"
    assert state.after is not None
    assert state.after.tokens == 46_538
    assert state.after.context_percent == 23
    assert state.after.token_source == "tokenizer-calibrated"
    assert state.saved is not None
    assert state.saved.percent == 78.6


def test_started_event_uses_usage_baseline_without_after():
    before_messages = [
        AssistantMessage(
            content="a" * 200_000,
            usage_metadata=UsageMetadata(total_tokens=180_000),
        )
    ]
    state = _recorder().build_state(_state_input(
        before_messages=before_messages,
        after_messages=None,
        status="started",
    ))

    assert state.before.tokens == 180_000
    assert state.before.token_source == "usage+tail"
    assert state.after is None
    assert state.saved is None


def test_completed_reuses_frozen_before_after_usage_invalidation():
    """set_messages 污染 before_messages 的 usage 后，completed 仍复用冻结值。"""
    recorder = _recorder()
    before_messages = [
        AssistantMessage(
            content="a" * 200_000,
            usage_metadata=UsageMetadata(total_tokens=180_000),
        ),
        UserMessage(content="x" * 4_000),  # tail = 1_000
    ]

    started = recorder.build_state(_state_input(
        before_messages=before_messages,
        after_messages=None,
        status="started",
    ))
    assert started.before.tokens == 181_000

    # Simulate context.set_messages(new_messages) retaining this assistant.
    ContextUtils.invalidate_usage_metadata(before_messages)

    completed = recorder.build_state(_state_input(
        before_messages=before_messages,
        after_messages=[UserMessage(content="b" * 60_000)],
    ))

    assert completed.before.tokens == 181_000
    assert completed.before.token_source == "usage+tail"
    # after raw 100k, frozen ratio 181k / 244k
    assert completed.after is not None
    assert completed.after.tokens == 74_180
    assert completed.after.token_source == "tokenizer-calibrated"


def test_before_without_usage_uses_context_scale_factor():
    """无有效 usage 时，before/after 使用上下文级校准因子。"""
    state = _recorder().build_state(_state_input(
        before_messages=[UserMessage(content="a" * 200_000)],
        after_messages=[UserMessage(content="b" * 60_000)],
        token_scale_factor=0.5,
    ))

    assert state.before.tokens == 120_000
    assert state.before.token_source == "tokenizer-calibrated"
    assert state.after is not None
    assert state.after.tokens == 50_000
    assert state.after.token_source == "tokenizer-calibrated"
    assert state.saved is not None
    assert state.saved.percent == 58.3


def test_trimmed_window_snapshot_does_not_emit_scale_factor():
    """usage 覆盖整段对话、raw 只覆盖窗口时，不能从二者推导校准因子。"""
    state = _recorder().build_state(_state_input(
        before_messages=[
            AssistantMessage(
                content="a" * 200_000,
                usage_metadata=UsageMetadata(total_tokens=180_000),
            )
        ],
        after_messages=[UserMessage(content="b" * 60_000)],
        token_scale_eligible=False,
    ))

    assert state.before.tokens == 240_000
    assert state.before.token_source == "tokenizer"
    assert state.scale_factor is None
    assert state.after is not None
    assert state.after.tokens == 100_000
    assert state.after.token_source == "tokenizer"


def test_zhou_shi_case_calibrates_from_frozen_start_scale():
    """周实案例：before 冻结在 234,331，after 用同一比例得到 186,715。"""
    recorder = ContextProcessorStateRecorder(
        session_id="desktop-1",
        context_id="default_context_id",
        get_session_ref=lambda: None,
        token_counter=_SequencedCounter([337_365, 268_812]),
    )
    before_messages = [
        AssistantMessage(
            content="history",
            usage_metadata=UsageMetadata(total_tokens=234_331),
        )
    ]
    state = recorder.build_state(_state_input(
        before_messages=before_messages,
        after_messages=[UserMessage(content="summary")],
        overhead_tokens=0,
    ))

    assert state.before.tokens == 234_331
    assert state.before.token_source == "usage+tail"
    assert state.scale_factor == pytest.approx(234_331 / 337_365)
    assert state.after is not None
    assert state.after.tokens == 186_715
    assert state.after.token_source == "tokenizer-calibrated"
    assert state.saved is not None
    assert state.saved.percent == 20.3
