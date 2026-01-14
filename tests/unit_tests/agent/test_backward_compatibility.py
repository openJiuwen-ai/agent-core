#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Backward Compatibility Tests

Ensure old interfaces continue working and issue proper deprecation warnings
"""
import warnings
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo
from openjiuwen.core.single_agent import (
    AgentConfig,
    ControllerAgent,
    AgentSession,
    WorkflowFactory,
    workflow_provider,
    create_react_agent_config,
    LLMCallConfig,
    ConstrainConfig,
)
from openjiuwen.core.single_agent import (
    ReActAgent,
    ReActAgentConfig,
    AgentCard,
    BaseAgent
)
from openjiuwen.core.single_agent.legacy import (
    LegacyReActAgent,
    LegacyReActAgentConfig,
)


def _filter_our_warnings(warnings_list):
    """Filter out third-party warnings (e.g., Pydantic), keep only ours."""
    return [
        w for w in warnings_list
        if w.category == DeprecationWarning and "in the future" in str(w.message)
    ]


class TestLegacyImports:
    """Test old import paths work with deprecation warnings"""

    def test_old_imports_issue_warnings(self):
        """Legacy classes issue deprecation warnings on instantiation.

        Note: With explicit imports, warnings are triggered on instantiation,
        not on attribute access. This is the expected behavior for IDE support.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # Instantiate deprecated classes to trigger warnings
            _ = AgentConfig()
            _ = LLMCallConfig()
            _ = ConstrainConfig()

            # Filter out third-party warnings (e.g., Pydantic)
            our_warnings = _filter_our_warnings(w)

            # Each instantiation should trigger our deprecation warning
            assert len(our_warnings) >= 3
            # All our warnings should mention "in the future"
            assert all("in the future" in str(x.message) for x in our_warnings)
    
    def test_new_imports_no_warning(self):
        """New imports (AgentCard) do not issue warnings"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # AgentCard is new, should not trigger warning
            assert len(w) == 0
            assert AgentCard is not None
    
    def test_legacy_module_imports_issue_warnings(self):
        """Imports from legacy module issue warnings on instantiation."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # Instantiate legacy classes to trigger warnings
            _ = LegacyReActAgentConfig()

            # Filter out third-party warnings
            our_warnings = _filter_our_warnings(w)

            # Instantiation should trigger our deprecation warning
            assert len(our_warnings) >= 1


class TestLegacyConstructor:
    """Test old constructor methods"""
    
    def test_react_agent_old_style_construction(self):
        """ReActAgent old construction style still works"""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")

            # Create model config
            model_info = BaseModelInfo(
                model="gpt-4",
                api_key="test-key",
                api_base="https://api.openai.com/v1"
            )
            model_config = ModelConfig(
                model_provider="OpenAI",
                model_info=model_info
            )
            
            # Create agent config
            config = ReActAgentConfig(
                id="test_agent",
                version="1.0",
                description="Test Agent",
                model=model_config
            )
            
            # Create agent - should work without errors
            agent = ReActAgent(agent_config=config)
            
            # Verify agent created successfully
            assert agent.agent_config.id == "test_agent"
            assert agent.agent_config.version == "1.0"
    
    def test_react_agent_with_tools_parameter(self):
        """Support old tools parameter"""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            
            # Create model config
            model_info = BaseModelInfo(
                model="gpt-4",
                api_key="test-key",
                api_base="https://api.openai.com/v1"
            )
            model_config = ModelConfig(
                model_provider="OpenAI",
                model_info=model_info
            )
            
            # Create agent config
            config = ReActAgentConfig(
                id="test_agent",
                version="1.0",
                model=model_config
            )
            
            # Create agent with tools parameter (should not raise error)
            agent = ReActAgent(agent_config=config, tools=[])
            assert agent is not None


class TestLegacyMethods:
    """Test legacy methods work without errors"""
    
    def test_add_tools_method_works(self):
        """add_tools() method works in legacy agent"""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            
            # Create agent
            model_info = BaseModelInfo(
                model="gpt-4",
                api_key="test-key",
                api_base="https://api.openai.com/v1"
            )
            model_config = ModelConfig(
                model_provider="OpenAI",
                model_info=model_info
            )
            config = ReActAgentConfig(
                id="test_agent",
                version="1.0",
                model=model_config
            )
            agent = ReActAgent(agent_config=config)
            
            # Call add_tools - should work without errors
            agent.add_tools([])
            assert agent is not None
    
    def test_add_workflows_method_works(self):
        """add_workflows() method works in legacy agent"""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            
            # Create agent
            model_info = BaseModelInfo(
                model="gpt-4",
                api_key="test-key",
                api_base="https://api.openai.com/v1"
            )
            model_config = ModelConfig(
                model_provider="OpenAI",
                model_info=model_info
            )
            config = ReActAgentConfig(
                id="test_agent",
                version="1.0",
                model=model_config
            )
            agent = ReActAgent(agent_config=config)
            
            # Call add_workflows - should work without errors
            agent.add_workflows([])
            assert agent is not None


class TestWarningMessages:
    """Test warning messages correctness"""

    def test_deprecation_warning_contains_migration_info(self):
        """Deprecation warnings contain migration information."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # Instantiate a deprecated class to trigger warning
            _ = ReActAgentConfig()

            our_warnings = _filter_our_warnings(w)
            assert len(our_warnings) > 0
            warning_msg = str(our_warnings[0].message)
            # Verify contains "in the future"
            assert "in the future" in warning_msg
            # Verify warning message mentions deprecated
            assert "deprecated" in warning_msg.lower()
            # Verify suggests alternative
            assert "ReActAgent" in warning_msg or "react_agent" in warning_msg.lower()


class TestCreateReactAgentConfig:
    """Test create_react_agent_config factory function"""

    def test_create_react_agent_config_issues_warning(self):
        """create_react_agent_config() issues deprecation warning on call."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            model_info = BaseModelInfo(
                model="gpt-4",
                api_key="test-key",
                api_base="https://api.openai.com/v1"
            )
            model_config = ModelConfig(
                model_provider="OpenAI",
                model_info=model_info
            )

            # Call create_react_agent_config to trigger warning
            _ = create_react_agent_config(
                agent_id="test",
                agent_version="1.0",
                description="test",
                model=model_config,
                prompt_template=[]
            )

            our_warnings = _filter_our_warnings(w)
            # Verify deprecation warning on call
            assert len(our_warnings) > 0
            assert any("deprecated" in str(x.message).lower() for x in our_warnings)
    
    def test_create_react_agent_config_works(self):
        """create_react_agent_config() still works"""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            
            model_info = BaseModelInfo(
                model="gpt-4",
                api_key="test-key",
                api_base="https://api.openai.com/v1"
            )
            model_config = ModelConfig(
                model_provider="OpenAI",
                model_info=model_info
            )
            
            # Should work and return config
            config = create_react_agent_config(
                agent_id="test",
                agent_version="1.0",
                description="test",
                model=model_config,
                prompt_template=[]
            )
            
            # Verify config created successfully
            assert config.id == "test"
            assert config.version == "1.0"


class TestLegacyCompatibilityIntegration:
    """Integration tests for legacy compatibility"""
    
    def test_old_and_new_apis_coexist(self):
        """Old and new APIs can coexist"""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")

            # Create model config
            model_info = BaseModelInfo(
                model="gpt-4",
                api_key="test-key",
                api_base="https://api.openai.com/v1"
            )
            model_config = ModelConfig(
                model_provider="OpenAI",
                model_info=model_info
            )
            
            # Old style agent
            old_config = ReActAgentConfig(
                id="old_agent",
                version="1.0",
                model=model_config
            )
            old_agent = ReActAgent(agent_config=old_config)
            
            # Verify old agent is created successfully
            assert old_agent is not None
            # Old agent uses legacy BaseAgent
            assert isinstance(old_agent, BaseAgent)
