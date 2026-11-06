# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the shared [CompressionApplied] compression event helper.

Uses a recording stub logger instead of ``caplog`` so the assertions stay
immune to logging reconfiguration performed by other test modules imported
during collection.
"""

from __future__ import annotations

import json

import openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.pipeline as pipeline_module
from openjiuwen.core.context_engine.processor.compression_events import (
    content_chars,
    count_messages_tokens_if_available,
    est_tokens,
    log_compression_applied,
    messages_total_chars,
    resolve_session_id,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.pipeline import (
    RuleCompressionPipeline,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleCompressionResult,
)
from openjiuwen.core.foundation.llm import ToolMessage


class _RecordingLogger:
    def __init__(self):
        self.lines: list[str] = []

    def info(self, message, *args):
        self.lines.append(message % args if args else message)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class _Context:
    def __init__(self, session_id="session-a"):
        self._session_id = session_id
        self.token_counter = None

    def context_window_tokens(self) -> int:
        return 100

    def get_messages(self):
        return []

    def session_id(self) -> str:
        if isinstance(self._session_id, Exception):
            raise self._session_id
        return self._session_id

    def context_id(self) -> str:
        return "context-a"

    def workspace_dir(self) -> str:
        return ""


class _CounterContext(_Context):
    def __init__(self, counter):
        super().__init__()
        self.token_counter = lambda: counter


class _Counter:
    def __init__(self, value):
        self._value = value
        self.calls = 0

    def count_messages(self, messages):
        self.calls += 1
        return self._value * len(list(messages))


class _RaisingCounter:
    def count_messages(self, _messages):
        raise RuntimeError("counter unavailable")


def test_log_compression_applied_emits_structured_line_with_real_tokens():
    recorder = _RecordingLogger()
    log_compression_applied(
        recorder,
        session_id="s-1",
        processor="RuleCompressionPipeline",
        strategy="rule:PLAIN_TEXT",
        trigger="add",
        before_chars=30,
        after_chars=12,
        before_tokens=10,
        after_tokens=4,
        tool_call_id="tc-1",
    )

    assert "[CompressionApplied]" in recorder.text
    assert "session_id=s-1" in recorder.text
    assert "processor=RuleCompressionPipeline" in recorder.text
    assert "strategy=rule:PLAIN_TEXT" in recorder.text
    assert "trigger=add" in recorder.text
    assert "chars=30->12" in recorder.text
    assert "tokens=10->4" in recorder.text
    assert "est_tokens=n/a" in recorder.text
    assert "tool_call_id=tc-1" in recorder.text


def test_log_compression_applied_annotates_est_tokens_without_tokenizer():
    recorder = _RecordingLogger()
    log_compression_applied(
        recorder,
        session_id="s-2",
        processor="DialogueCompressor",
        strategy="llm_summary",
        trigger="threshold",
        before_chars=30,
        after_chars=6,
    )

    assert "session_id=s-2" in recorder.text
    assert "chars=30->6" in recorder.text
    assert "tokens=n/a" in recorder.text
    assert "est_tokens=10->2" in recorder.text
    assert "tool_call_id=n/a" in recorder.text


def test_est_tokens_uses_shared_three_chars_per_token_heuristic():
    assert est_tokens(0) == 0
    assert est_tokens(2) == 0
    assert est_tokens(30) == 10
    assert est_tokens(31) == 10


def test_content_chars_and_messages_total_chars_handle_mixed_content():
    class _Message:
        def __init__(self, content):
            self.content = content

    assert content_chars("abc") == 3
    assert content_chars(None) == 0
    assert content_chars({"k": "v"}) == len(json.dumps({"k": "v"}, ensure_ascii=False))

    messages = [_Message("abcd"), _Message({"a": 1}), _Message(None), _Message("xy")]
    assert messages_total_chars(messages) == 4 + content_chars({"a": 1}) + 0 + 2


def test_resolve_session_id_variants():
    assert resolve_session_id(_Context("s-9")) == "s-9"
    assert resolve_session_id(_Context(None)) == "unknown_session"
    assert resolve_session_id(object()) == "unknown_session"
    assert resolve_session_id(_Context(RuntimeError("boom"))) == "unknown_session"


def test_count_messages_tokens_if_available_counts_with_context_tokenizer():
    counter = _Counter(7)
    assert count_messages_tokens_if_available(_CounterContext(counter), ["m", "m"]) == 14
    assert counter.calls == 1


def test_count_messages_tokens_if_available_returns_none_when_unavailable():
    assert count_messages_tokens_if_available(_Context(), ["m"]) is None
    assert count_messages_tokens_if_available(_CounterContext(_RaisingCounter()), ["m"]) is None
    assert count_messages_tokens_if_available(object(), ["m"]) is None


def test_rule_compression_pipeline_logs_legacy_and_structured_events(monkeypatch):
    recorder = _RecordingLogger()
    monkeypatch.setattr(pipeline_module, "logger", recorder)
    pipeline = RuleCompressionPipeline(router=_ShrinkingRouter())

    pipeline.compress(
        ToolMessage(content="x" * 80, tool_call_id="tc-obs"),
        _Context("session-a"),
        pass_name="add",
        max_chars=80,
        force=True,
        context_messages=[],
    )

    assert (
        "[RuleCompression] applied tool_call_id=tc-obs pass=add type=PLAIN_TEXT chars=80->40"
        in recorder.text
    )
    assert "[CompressionApplied]" in recorder.text
    assert "session_id=session-a" in recorder.text
    assert "processor=RuleCompressionPipeline" in recorder.text
    assert "strategy=rule:PLAIN_TEXT" in recorder.text
    assert "trigger=add" in recorder.text
    assert "chars=80->40" in recorder.text
    assert "est_tokens=26->13" in recorder.text
    assert "tool_call_id=tc-obs" in recorder.text
    assert sum("[RuleCompression] applied" in line for line in recorder.lines) == 1
    assert sum("[CompressionApplied]" in line for line in recorder.lines) == 1


class _ShrinkingRouter:
    def compress(self, content: str, _context) -> RuleCompressionResult:
        return RuleCompressionResult(
            content=content[: len(content) // 2],
            content_type=ContentType.PLAIN_TEXT,
            modified=True,
        )
