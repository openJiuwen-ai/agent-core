# -*- coding: utf-8 -*-
"""issue #1756：流式 chunk 列表也要落 gen_ai.response.finish_reasons。"""
from unittest.mock import MagicMock

from openjiuwen.extensions.tracer_otel.handler import _set_llm_response_attrs
from openjiuwen.extensions.tracer_otel.semconv import GEN_AI_RESPONSE_FINISH_REASONS


def _span():
    span = MagicMock()
    span.attributes = {}
    return span


def _reasons(span):
    for call in span.set_attribute.call_args_list:
        if call.args[0] == GEN_AI_RESPONSE_FINISH_REASONS:
            return call.args[1]
    return None


def test_chunk_list_takes_terminal_finish_reason():
    span = _span()
    _set_llm_response_attrs(span, {"outputs": [{"content": "a"}, {"finish_reason": "length"}]})
    assert _reasons(span) == ["length"]


def test_chunk_list_skips_null_and_uses_last_non_null():
    span = _span()
    _set_llm_response_attrs(span, {"outputs": [
        {"finish_reason": "content_filter"}, {"finish_reason": "null"}, {"content": "x"},
    ]})
    assert _reasons(span) == ["content_filter"]


def test_chunk_list_without_finish_reason_writes_nothing():
    span = _span()
    _set_llm_response_attrs(span, {"outputs": [{"content": "a"}, {"content": "b"}]})
    assert _reasons(span) is None


def test_non_stream_message_still_works():
    span = _span()
    _set_llm_response_attrs(span, {"finish_reason": "stop"})
    assert _reasons(span) == ["stop"]
