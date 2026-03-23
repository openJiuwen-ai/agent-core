# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_generator import (
    DL_GENERATE_SYSTEM_TEMPLATE,
    DL_REFINE_USER_TEMPLATE,
    DLGenerator,
)


class TestDLGeneratorInit:
    """Test DLGenerator initialization."""

    @staticmethod
    def test_init_success(mock_model):
        """Test successful initialization."""
        generator = DLGenerator(mock_model)
        
        assert generator.llm == mock_model

    @staticmethod
    def test_init_with_none_llm():
        """Test initialization with None LLM."""
        generator = DLGenerator(None)
        
        assert generator.llm is None


class TestDLGeneratorTemplates:
    """Test DLGenerator templates."""

    @staticmethod
    def test_generate_system_template_exists():
        """Test generate system template exists."""
        assert DL_GENERATE_SYSTEM_TEMPLATE is not None

    @staticmethod
    def test_refine_user_template_exists():
        """Test refine user template exists."""
        assert DL_REFINE_USER_TEMPLATE is not None

    @staticmethod
    def test_generate_system_template_format():
        """Test generate system template can be formatted."""
        messages = DL_GENERATE_SYSTEM_TEMPLATE.format({
            "components": "test components",
            "schema": "test schema",
            "plugins": "test plugins",
            "examples": "test examples"
        }).to_messages()
        
        assert len(messages) > 0

    @staticmethod
    def test_refine_user_template_format():
        """Test refine user template can be formatted."""
        messages = DL_REFINE_USER_TEMPLATE.format({
            "user_input": "test input",
            "exist_mermaid": "test mermaid",
            "exist_dl": "test dl"
        }).to_messages()
        
        assert len(messages) > 0
