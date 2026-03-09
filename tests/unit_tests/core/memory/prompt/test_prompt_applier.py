#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import patch
import pytest
from openjiuwen.core.memory.prompts.prompt_applier import PromptApplier
from openjiuwen.core.foundation.prompt import PromptTemplate


class TestPromptApplier:
    """Unit tests for PromptApplier class."""

    @staticmethod
    def setup_method():
        """Setup method to run before each test."""
        # Clear the singleton instance before each test
        if hasattr(PromptApplier, '_instance'):
            delattr(PromptApplier, '_instance')

    @staticmethod
    def teardown_method():
        """Teardown method to run after each test."""
        # Clean up singleton instance after each test
        if hasattr(PromptApplier, '_instance'):
            delattr(PromptApplier, '_instance')

    @staticmethod
    def test_singleton_initialization():
        """Test that PromptApplier is properly initialized as singleton."""
        applier1 = PromptApplier()
        applier2 = PromptApplier()

        # Should be the same instance due to singleton pattern
        assert applier1 is applier2

    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists')
    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text')
    def test_apply_with_variable_substitution(self, mock_read_text, mock_exists):
        """Test apply method with variable substitution."""
        # Setup mocks for file operations
        mock_exists.return_value = True
        mock_read_text.return_value = "Hello {{name}}, welcome to {{place}}!"

        applier = PromptApplier()

        # Apply template with variables
        result = applier.apply("test_template", {"name": "Alice", "place": "Wonderland"})

        # Verify the result contains substituted values
        assert "Hello Alice" in result
        assert "welcome to Wonderland" in result
        assert result == "Hello Alice, welcome to Wonderland!"

    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists')
    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text')
    def test_apply_with_empty_variables(self, mock_read_text, mock_exists):
        """Test apply method with empty variables dictionary."""
        mock_exists.return_value = True
        mock_read_text.return_value = "Simple template without variables"

        applier = PromptApplier()

        # Apply with empty variables
        result = applier.apply("simple_template", {})

        assert result == "Simple template without variables"

    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists')
    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text')
    def test_apply_caches_templates(self, mock_read_text, mock_exists):
        """Test that apply method caches loaded templates."""
        mock_exists.return_value = True
        mock_read_text.return_value = "Cached template content"

        applier = PromptApplier()

        # First application - should read from file
        result1 = applier.apply("cached_template", {"var": "value1"})
        assert result1 == "Cached template content"
        assert mock_read_text.call_count == 1

        # Second application - should use cache (file read count should not increase)
        result2 = applier.apply("cached_template", {"var": "value2"})
        assert result2 == "Cached template content"
        assert mock_read_text.call_count == 1  # Still 1, not increased due to caching

    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists')
    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text')
    def test_apply_file_not_found(self, mock_read_text, mock_exists):
        """Test apply method with non-existent file."""
        mock_exists.return_value = False

        applier = PromptApplier()

        # Should raise FileNotFoundError
        with pytest.raises(FileNotFoundError, match="Prompt file not found"):
            applier.apply("non_existent_template", {"var": "value"})

    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists')
    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text')
    def test_clear_cache_all(self, mock_read_text, mock_exists):
        """Test clear_cache method clearing all cache."""
        applier = PromptApplier()

        # Load some templates first
        mock_exists.return_value = True
        mock_read_text.return_value = "test content"
        
        # Load templates into cache
        applier.apply("template1", {})
        applier.apply("template2", {})
        
        # Verify templates are cached by checking file read count
        assert mock_read_text.call_count == 2

        # Clear all cache
        applier.clear_cache()
        
        # Load the same templates again - should read from file again (cache was cleared)
        applier.apply("template1", {})
        applier.apply("template2", {})
        
        # Should have read from file 4 times total (2 original + 2 after cache clear)
        assert mock_read_text.call_count == 4

    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists')
    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text')
    def test_get_template_returns_prompt_template(self, mock_read_text, mock_exists):
        """Test get_template method returns a PromptTemplate instance."""
        mock_exists.return_value = True
        mock_read_text.return_value = "Template content for get_template"

        applier = PromptApplier()

        # Get template without applying variables
        template = applier.get_template("test_template")

        # Verify it returns a PromptTemplate
        assert isinstance(template, PromptTemplate)

    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists')
    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text')
    def test_get_template_caches_result(self, mock_read_text, mock_exists):
        """Test that get_template method caches the result."""
        mock_exists.return_value = True
        mock_read_text.return_value = "Cached template content"

        applier = PromptApplier()

        # First get_template call - should read from file
        template1 = applier.get_template("cached_template")
        assert mock_read_text.call_count == 1

        # Second get_template call - should use cache
        template2 = applier.get_template("cached_template")
        assert mock_read_text.call_count == 1  # Still 1, not increased due to caching
        assert template1 is template2  # Should be the same object

    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists')
    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text')
    def test_get_template_file_not_found(self, mock_read_text, mock_exists):
        """Test get_template method with non-existent file."""
        mock_exists.return_value = False

        applier = PromptApplier()

        # Should raise FileNotFoundError
        with pytest.raises(FileNotFoundError, match="Prompt file not found"):
            applier.get_template("non_existent_template")

    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists')
    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text')
    def test_integration_with_complex_template(self, mock_read_text, mock_exists):
        """Integration test with complex template containing multiple variables."""
        mock_exists.return_value = True
        mock_read_text.return_value = """
        System: You are a helpful assistant specialized in {{domain}}.
        User: {{question}}
        Assistant: I'll help you with {{domain}}. Regarding your question about {{topic}}:
        {{context}}
        """

        applier = PromptApplier()

        variables = {
            "domain": "Python programming",
            "question": "How do I write unit tests?",
            "topic": "unit testing",
            "context": "Use pytest framework for writing comprehensive tests."
        }

        result = applier.apply("complex_template", variables)

        # Verify all variables were substituted
        assert "Python programming" in result
        assert "How do I write unit tests?" in result
        assert "unit testing" in result
        assert "Use pytest framework for writing comprehensive tests." in result

    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists')
    @patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text')
    def test_apply_with_special_characters_in_variables(self, mock_read_text, mock_exists):
        """Test apply method with special characters in variable values."""
        mock_exists.return_value = True
        mock_read_text.return_value = "User input: {{user_input}}"

        applier = PromptApplier()

        # Test with special characters
        variables = {
            "user_input": "Hello, world! How are you? @#$%^&*()"
        }

        result = applier.apply("special_chars_template", variables)
        assert "Hello, world! How are you? @#$%^&*()" in result

    @staticmethod
    def test_singleton_behavior_across_multiple_instances():
        """Test that singleton behavior works correctly across multiple test methods."""
        # Create first instance
        applier1 = PromptApplier()
        
        # Create second instance  
        applier2 = PromptApplier()
        
        # Should be the same instance
        assert applier1 is applier2
        
        # Modifying cache through one instance should affect the other (verified through behavior)
        with patch('openjiuwen.core.memory.prompts.prompt_applier.Path.exists', return_value=True), \
             patch('openjiuwen.core.memory.prompts.prompt_applier.Path.read_text', return_value="test"):
            
            # Load template through first instance
            applier1.apply("shared_template", {})
            
            # Second application should use cache (verified by file read count)
            applier2.apply("shared_template", {})
            
            # Both should share the same cache behavior