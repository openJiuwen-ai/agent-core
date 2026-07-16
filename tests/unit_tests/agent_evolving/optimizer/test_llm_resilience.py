# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for shared LLM response normalization."""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen.agent_evolving.optimizer.llm_resilience import response_to_text


class TestResponseToText:
    @staticmethod
    def test_object_content():
        assert response_to_text(SimpleNamespace(content="hello")) == "hello"

    @staticmethod
    def test_dict_content():
        assert response_to_text({"content": "from-dict"}) == "from-dict"

    @staticmethod
    def test_dict_text_fallback():
        assert response_to_text({"text": "plain-text"}) == "plain-text"

    @staticmethod
    def test_list_content_parts():
        response = {
            "content": [
                {"type": "text", "text": "a"},
                {"type": "text", "text": "b"},
            ]
        }
        assert response_to_text(response) == "ab"

    @staticmethod
    def test_reasoning_content_fallback_for_object_and_dict():
        obj = SimpleNamespace(content="", reasoning_content="think")
        assert response_to_text(obj) == "think"
        assert response_to_text({"content": "", "reasoning_content": "think"}) == "think"
