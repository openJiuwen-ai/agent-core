# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.prompts import (
    EMPTY_RESOURCE_CONTENT,
    INITIAL_INTENTION_SYSTEM_PROMPT,
    INITIAL_INTENTION_USER_TEMPLATE,
    REFINE_INTENTION_SYSTEM_PROMPT,
    REFINE_INTENTION_USER_TEMPLATE,
)


class TestInitialIntentionSystemPrompt:
    """Test INITIAL_INTENTION_SYSTEM_PROMPT constant."""

    @staticmethod
    def test_is_string():
        """Test is string."""
        assert isinstance(INITIAL_INTENTION_SYSTEM_PROMPT, str)
        assert len(INITIAL_INTENTION_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_contains_role():
        """Test contains role definition."""
        assert "角色" in INITIAL_INTENTION_SYSTEM_PROMPT

    @staticmethod
    def test_contains_true_condition():
        """Test contains true condition."""
        assert "true" in INITIAL_INTENTION_SYSTEM_PROMPT.lower()

    @staticmethod
    def test_contains_false_condition():
        """Test contains false condition."""
        assert "false" in INITIAL_INTENTION_SYSTEM_PROMPT.lower()

    @staticmethod
    def test_contains_provide_process():
        """Test contains provide_process key."""
        assert "provide_process" in INITIAL_INTENTION_SYSTEM_PROMPT


class TestInitialIntentionUserTemplate:
    """Test INITIAL_INTENTION_USER_TEMPLATE."""

    @staticmethod
    def test_template_exists():
        """Test template exists."""
        assert INITIAL_INTENTION_USER_TEMPLATE is not None

    @staticmethod
    def test_template_has_content():
        """Test template has content."""
        assert hasattr(INITIAL_INTENTION_USER_TEMPLATE, 'content')

    @staticmethod
    def test_template_format():
        """Test template can be formatted."""
        messages = INITIAL_INTENTION_USER_TEMPLATE.format({
            "dialog_history": "test history"
        }).to_messages()
        
        assert len(messages) > 0


class TestRefineIntentionSystemPrompt:
    """Test REFINE_INTENTION_SYSTEM_PROMPT constant."""

    @staticmethod
    def test_is_string():
        """Test is string."""
        assert isinstance(REFINE_INTENTION_SYSTEM_PROMPT, str)
        assert len(REFINE_INTENTION_SYSTEM_PROMPT) > 0

    @staticmethod
    def test_contains_role():
        """Test contains role definition."""
        assert "角色" in REFINE_INTENTION_SYSTEM_PROMPT

    @staticmethod
    def test_contains_need_refined():
        """Test contains need_refined key."""
        assert "need_refined" in REFINE_INTENTION_SYSTEM_PROMPT


class TestRefineIntentionUserTemplate:
    """Test REFINE_INTENTION_USER_TEMPLATE."""

    @staticmethod
    def test_template_exists():
        """Test template exists."""
        assert REFINE_INTENTION_USER_TEMPLATE is not None

    @staticmethod
    def test_template_has_content():
        """Test template has content."""
        assert hasattr(REFINE_INTENTION_USER_TEMPLATE, 'content')

    @staticmethod
    def test_template_format():
        """Test template can be formatted."""
        messages = REFINE_INTENTION_USER_TEMPLATE.format({
            "mermaid_code": "graph TD",
            "dialog_history": "test history"
        }).to_messages()
        
        assert len(messages) > 0


class TestEmptyResourceContent:
    """Test EMPTY_RESOURCE_CONTENT constant."""

    @staticmethod
    def test_is_string():
        """Test is string."""
        assert isinstance(EMPTY_RESOURCE_CONTENT, str)

    @staticmethod
    def test_indicates_empty():
        """Test indicates empty resources."""
        assert "无" in EMPTY_RESOURCE_CONTENT or (
            "empty" in EMPTY_RESOURCE_CONTENT.lower() or len(EMPTY_RESOURCE_CONTENT) == 0)
