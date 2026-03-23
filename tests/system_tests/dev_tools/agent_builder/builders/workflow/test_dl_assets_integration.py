# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for DL Assets module.

Tests DL assets constants integration.
"""
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_assets import (
    COMPONENTS_INFO,
    EXAMPLES,
    SCHEMA_INFO,
)


class TestDLAssetsIntegration:
    """Test DL assets integration."""

    @staticmethod
    def test_components_info_is_string():
        """Test COMPONENTS_INFO is string."""
        assert isinstance(COMPONENTS_INFO, str)
        assert len(COMPONENTS_INFO) > 0

    @staticmethod
    def test_components_info_contains_all_nodes():
        """Test COMPONENTS_INFO contains all node types."""
        node_types = [
            "Start", "End", "LLM", "IntentDetection",
            "Questioner", "Code", "Plugin", "Output", "Branch"
        ]
        
        for node_type in node_types:
            assert node_type in COMPONENTS_INFO, f"Missing {node_type} in COMPONENTS_INFO"

    @staticmethod
    def test_schema_info_is_string():
        """Test SCHEMA_INFO is string."""
        assert isinstance(SCHEMA_INFO, str)
        assert len(SCHEMA_INFO) > 0

    @staticmethod
    def test_schema_info_contains_node_fields():
        """Test SCHEMA_INFO contains node fields."""
        required_fields = ["id", "type", "parameters"]
        
        for field in required_fields:
            assert field in SCHEMA_INFO, f"Missing {field} in SCHEMA_INFO"

    @staticmethod
    def test_examples_is_string():
        """Test EXAMPLES is string."""
        assert isinstance(EXAMPLES, str)


class TestComponentsInfoContent:
    """Test COMPONENTS_INFO content."""

    @staticmethod
    def test_contains_start_description():
        """Test contains Start node description."""
        assert "开始节点" in COMPONENTS_INFO or "Start" in COMPONENTS_INFO

    @staticmethod
    def test_contains_end_description():
        """Test contains End node description."""
        assert "结束节点" in COMPONENTS_INFO or "End" in COMPONENTS_INFO

    @staticmethod
    def test_contains_llm_description():
        """Test contains LLM node description."""
        assert "大模型" in COMPONENTS_INFO or "LLM" in COMPONENTS_INFO

    @staticmethod
    def test_contains_plugin_description():
        """Test contains Plugin node description."""
        assert "插件" in COMPONENTS_INFO or "Plugin" in COMPONENTS_INFO

    @staticmethod
    def test_contains_code_description():
        """Test contains Code node description."""
        assert "代码" in COMPONENTS_INFO or "Code" in COMPONENTS_INFO

    @staticmethod
    def test_contains_questioner_description():
        """Test contains Questioner node description."""
        assert "提问" in COMPONENTS_INFO or "Questioner" in COMPONENTS_INFO

    @staticmethod
    def test_contains_intent_detection_description():
        """Test contains IntentDetection node description."""
        assert "意图" in COMPONENTS_INFO or "IntentDetection" in COMPONENTS_INFO

    @staticmethod
    def test_contains_branch_description():
        """Test contains Branch node description."""
        assert "分支" in COMPONENTS_INFO or "Branch" in COMPONENTS_INFO

    @staticmethod
    def test_contains_output_description():
        """Test contains Output node description."""
        assert "输出" in COMPONENTS_INFO or "Output" in COMPONENTS_INFO


class TestSchemaInfoContent:
    """Test SCHEMA_INFO content."""

    @staticmethod
    def test_contains_start_schema():
        """Test contains Start node schema."""
        assert "Start" in SCHEMA_INFO or "开始" in SCHEMA_INFO

    @staticmethod
    def test_contains_end_schema():
        """Test contains End node schema."""
        assert "End" in SCHEMA_INFO or "结束" in SCHEMA_INFO

    @staticmethod
    def test_contains_llm_schema():
        """Test contains LLM node schema."""
        assert "LLM" in SCHEMA_INFO or "大模型" in SCHEMA_INFO
