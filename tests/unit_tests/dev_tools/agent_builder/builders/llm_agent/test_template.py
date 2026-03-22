# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.template import LLM_AGENT_TEMPLATE


class TestLlmAgentTemplate:
    @staticmethod
    def test_template_is_dict():
        assert isinstance(LLM_AGENT_TEMPLATE, dict)

    @staticmethod
    def test_template_has_required_keys():
        required_keys = [
            "agent_id",
            "agent_type",
            "name",
            "description",
            "configs",
            "opening_remarks",
            "plugins",
            "knowledge",
            "workflows",
            "create_time",
            "update_time"
        ]
        
        for key in required_keys:
            assert key in LLM_AGENT_TEMPLATE, f"Missing key: {key}"

    @staticmethod
    def test_template_has_memory_config():
        assert "memory" in LLM_AGENT_TEMPLATE
        assert "max_tokens" in LLM_AGENT_TEMPLATE["memory"]

    @staticmethod
    def test_template_has_model_config():
        assert "model" in LLM_AGENT_TEMPLATE
        assert "model_info" in LLM_AGENT_TEMPLATE["model"]

    @staticmethod
    def test_template_has_constraints():
        assert "constraints" in LLM_AGENT_TEMPLATE
        assert "max_iterations" in LLM_AGENT_TEMPLATE["constraints"]

    @staticmethod
    def test_template_create_time_is_none():
        assert LLM_AGENT_TEMPLATE["create_time"] is None
        assert LLM_AGENT_TEMPLATE["update_time"] is None

    @staticmethod
    def test_template_is_copied_not_referenced():
        import copy
        template_copy = copy.deepcopy(LLM_AGENT_TEMPLATE)
        template_copy["name"] = "Modified"
        
        assert LLM_AGENT_TEMPLATE["name"] == ""
        assert template_copy["name"] == "Modified"
