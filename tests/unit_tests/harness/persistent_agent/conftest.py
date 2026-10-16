# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Controlled token counts for formal-entry tests using local model doubles."""

import pytest

from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.token import TokenCounter


class LocalModelCounter(TokenCounter):
    """The local test model uses one character per token; no provider tokenizer is downloaded."""

    measurement_source = "native_tokenizer"
    measurement_estimated = False

    def count(self, text, *, model="", **kwargs):
        return len(text or "")

    def count_messages(self, messages, *, model="", **kwargs):
        return sum(self.count(message.model_dump_json()) for message in messages)

    def count_tools(self, tools, *, model="", **kwargs):
        return sum(self.count(tool.model_dump_json()) for tool in tools)


@pytest.fixture
def capacity_counter(monkeypatch):
    counter = LocalModelCounter()
    monkeypatch.setattr(SessionModelContext, "token_counter", lambda self: counter)
    return counter
