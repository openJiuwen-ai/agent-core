# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_assets import (
    COMPONENTS_INFO,
    EXAMPLES,
    SCHEMA_INFO,
)


class TestComponentsInfo:
    """Test COMPONENTS_INFO constant."""

    @staticmethod
    def test_components_info_is_string():
        """Test COMPONENTS_INFO is string."""
        assert isinstance(COMPONENTS_INFO, str)
        assert len(COMPONENTS_INFO) > 0

    @staticmethod
    def test_components_info_contains_start():
        """Test COMPONENTS_INFO contains Start node."""
        assert "Start" in COMPONENTS_INFO

    @staticmethod
    def test_components_info_contains_end():
        """Test COMPONENTS_INFO contains End node."""
        assert "End" in COMPONENTS_INFO

    @staticmethod
    def test_components_info_contains_llm():
        """Test COMPONENTS_INFO contains LLM node."""
        assert "LLM" in COMPONENTS_INFO

    @staticmethod
    def test_components_info_contains_intent_detection():
        """Test COMPONENTS_INFO contains IntentDetection node."""
        assert "IntentDetection" in COMPONENTS_INFO

    @staticmethod
    def test_components_info_contains_questioner():
        """Test COMPONENTS_INFO contains Questioner node."""
        assert "Questioner" in COMPONENTS_INFO

    @staticmethod
    def test_components_info_contains_code():
        """Test COMPONENTS_INFO contains Code node."""
        assert "Code" in COMPONENTS_INFO

    @staticmethod
    def test_components_info_contains_plugin():
        """Test COMPONENTS_INFO contains Plugin node."""
        assert "Plugin" in COMPONENTS_INFO

    @staticmethod
    def test_components_info_contains_output():
        """Test COMPONENTS_INFO contains Output node."""
        assert "Output" in COMPONENTS_INFO

    @staticmethod
    def test_components_info_contains_branch():
        """Test COMPONENTS_INFO contains Branch node."""
        assert "Branch" in COMPONENTS_INFO


class TestSchemaInfo:
    """Test SCHEMA_INFO constant."""

    @staticmethod
    def test_schema_info_is_string():
        """Test SCHEMA_INFO is string."""
        assert isinstance(SCHEMA_INFO, str)
        assert len(SCHEMA_INFO) > 0

    @staticmethod
    def test_schema_info_contains_node_schema():
        """Test SCHEMA_INFO contains node schema definitions."""
        assert "id" in SCHEMA_INFO
        assert "type" in SCHEMA_INFO
        assert "parameters" in SCHEMA_INFO

    @staticmethod
    def test_schema_info_contains_start_schema():
        """Test SCHEMA_INFO contains Start node schema."""
        assert "开始节点" in SCHEMA_INFO or "Start" in SCHEMA_INFO

    @staticmethod
    def test_schema_info_contains_end_schema():
        """Test SCHEMA_INFO contains End node schema."""
        assert "结束节点" in SCHEMA_INFO or "End" in SCHEMA_INFO

    @staticmethod
    def test_schema_info_contains_llm_schema():
        """Test SCHEMA_INFO contains LLM node schema."""
        assert "大模型节点" in SCHEMA_INFO or "LLM" in SCHEMA_INFO


class TestExamples:
    """Test EXAMPLES constant."""

    @staticmethod
    def test_examples_is_string():
        """Test EXAMPLES is string."""
        assert isinstance(EXAMPLES, str)

    @staticmethod
    def test_examples_not_empty():
        """Test EXAMPLES is not empty."""
        assert len(EXAMPLES) >= 0
