# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for DL Generator module.

Tests DL Generator integration with LLM.
"""
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_generator import (
    DL_GENERATE_SYSTEM_TEMPLATE,
    DL_REFINE_USER_TEMPLATE,
    DLGenerator,
)


class TestDLGeneratorIntegration:
    """Test DLGenerator integration."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def dl_generator(self, mock_llm):
        return DLGenerator(mock_llm)

    @staticmethod
    def test_dl_generator_initialization(dl_generator, mock_llm):
        """Test DLGenerator initialization."""
        assert dl_generator.llm == mock_llm

    @staticmethod
    def test_generate_system_template_content():
        """Test DL_GENERATE_SYSTEM_TEMPLATE content."""
        messages = DL_GENERATE_SYSTEM_TEMPLATE.format({
            "components": "test components",
            "schema": "test schema",
            "plugins": "test plugins",
            "examples": "test examples"
        }).to_messages()
        
        assert len(messages) > 0
        assert "test components" in messages[0].content
        assert "test schema" in messages[0].content

    @staticmethod
    def test_refine_user_template_content():
        """Test DL_REFINE_USER_TEMPLATE content."""
        messages = DL_REFINE_USER_TEMPLATE.format({
            "user_input": "test input",
            "exist_mermaid": "test mermaid",
            "exist_dl": "test dl"
        }).to_messages()
        
        assert len(messages) > 0
        assert "test input" in messages[0].content
        assert "test mermaid" in messages[0].content
        assert "test dl" in messages[0].content


class TestDLGeneratorGenerate:
    """Test DLGenerator generate method."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def dl_generator(self, mock_llm):
        return DLGenerator(mock_llm)

    @staticmethod
    def test_generate_basic(dl_generator, mock_llm):
        """Test basic generate."""
        mock_llm.invoke.return_value = Mock(content='[{"id": "node_1", "type": "Start"}]')
        
        result = dl_generator.generate(
            query="create workflow",
            resource={}
        )
        
        assert result is not None
        mock_llm.invoke.assert_called()

    @staticmethod
    def test_generate_with_plugins(dl_generator, mock_llm):
        """Test generate with plugins."""
        mock_llm.invoke.return_value = Mock(content='[{"id": "node_1", "type": "Start"}]')
        
        result = dl_generator.generate(
            query="create workflow",
            resource={"plugins": [{"tool_id": "tool_1"}]}
        )
        
        assert result is not None


class TestDLGeneratorRefine:
    """Test DLGenerator refine method."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def dl_generator(self, mock_llm):
        return DLGenerator(mock_llm)

    @staticmethod
    def test_refine_basic(dl_generator, mock_llm):
        """Test basic refine."""
        mock_llm.invoke.return_value = Mock(content='[{"id": "node_1", "type": "Start"}]')
        
        result = dl_generator.refine(
            query="modify workflow",
            resource={},
            exist_dl='[{"id": "node_1", "type": "Start"}]',
            exist_mermaid="graph TD\n  A --> B"
        )
        
        assert result is not None
        mock_llm.invoke.assert_called()


class TestDLGeneratorLoadSchemaAndExamples:
    """Test DLGenerator load_schema_and_examples method."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @staticmethod
    def test_load_schema_and_examples(mock_llm):
        """Test load_schema_and_examples."""
        components, schema, examples = DLGenerator.load_schema_and_examples()
        
        assert isinstance(components, str)
        assert isinstance(schema, str)
        assert isinstance(examples, str)
        assert len(components) > 0
        assert len(schema) > 0
