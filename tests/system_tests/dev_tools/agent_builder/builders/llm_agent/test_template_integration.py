# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for LLM Agent template module.

Tests template structure and validation.
"""
import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.template import LLM_AGENT_TEMPLATE


class TestLlmAgentTemplateIntegration:
    @staticmethod
    def test_template_structure():
        assert "agent_id" in LLM_AGENT_TEMPLATE
        assert "agent_type" in LLM_AGENT_TEMPLATE
        assert "name" in LLM_AGENT_TEMPLATE
        assert "description" in LLM_AGENT_TEMPLATE
        assert "configs" in LLM_AGENT_TEMPLATE
        assert "model" in LLM_AGENT_TEMPLATE
        assert "plugins" in LLM_AGENT_TEMPLATE

    @staticmethod
    def test_template_default_values():
        assert LLM_AGENT_TEMPLATE["agent_id"] == ""
        assert LLM_AGENT_TEMPLATE["agent_type"] == "react"
        assert LLM_AGENT_TEMPLATE["edit_mode"] == "manual"

    @staticmethod
    def test_template_configs_structure():
        configs = LLM_AGENT_TEMPLATE["configs"]
        assert "system_prompt" in configs

    @staticmethod
    def test_template_constraints_structure():
        constraints = LLM_AGENT_TEMPLATE["constraints"]
        assert "max_iterations" in constraints
        assert "reserved_max_chat_rounds" in constraints
        assert constraints["max_iterations"] == 5

    @staticmethod
    def test_template_memory_structure():
        memory = LLM_AGENT_TEMPLATE["memory"]
        assert "max_tokens" in memory
        assert memory["max_tokens"] == 1000

    @staticmethod
    def test_template_model_structure():
        model = LLM_AGENT_TEMPLATE["model"]
        assert "model_info" in model
        assert "model_provider" in model
        
        model_info = model["model_info"]
        assert "api_base" in model_info
        assert "api_key" in model_info
        assert "model_name" in model_info
        assert "temperature" in model_info
        assert "top_p" in model_info
        assert "max_tokens" in model_info
        assert "streaming" in model_info

    @staticmethod
    def test_template_model_defaults():
        model_info = LLM_AGENT_TEMPLATE["model"]["model_info"]
        assert model_info["streaming"] is True
        assert model_info["max_tokens"] == 2048
        assert model_info["timeout"] == 1000

    @staticmethod
    def test_template_empty_collections():
        assert LLM_AGENT_TEMPLATE["plugins"] == []
        assert LLM_AGENT_TEMPLATE["knowledge"] == []
        assert LLM_AGENT_TEMPLATE["workflows"] == []
        assert LLM_AGENT_TEMPLATE["triggers"] == []
        assert LLM_AGENT_TEMPLATE["prompt_template"] == []

    @staticmethod
    def test_template_nullable_fields():
        assert LLM_AGENT_TEMPLATE["create_time"] is None
        assert LLM_AGENT_TEMPLATE["update_time"] is None
        assert LLM_AGENT_TEMPLATE["latest_publish_time"] is None
        assert LLM_AGENT_TEMPLATE["latest_publish_version"] is None

    @staticmethod
    def test_template_can_be_copied():
        import copy
        
        template_copy = copy.deepcopy(LLM_AGENT_TEMPLATE)
        
        assert template_copy is not LLM_AGENT_TEMPLATE
        assert template_copy == LLM_AGENT_TEMPLATE

    @staticmethod
    def test_template_modification_does_not_affect_original():
        import copy
        
        template_copy = copy.deepcopy(LLM_AGENT_TEMPLATE)
        template_copy["name"] = "Test Agent"
        template_copy["model"]["model_info"]["temperature"] = 0.5
        
        assert LLM_AGENT_TEMPLATE["name"] == ""
        assert LLM_AGENT_TEMPLATE["model"]["model_info"]["temperature"] == 2
