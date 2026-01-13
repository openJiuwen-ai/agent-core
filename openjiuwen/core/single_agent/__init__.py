# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Single Agent Module

This module provides backward compatible exports for legacy code.
All legacy implementations are in the legacy/ directory.

For migration guide, see: docs/AGENT_MIGRATION_GUIDE.md
"""
import warnings
from typing import Any

# New classes (no deprecation warning)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

# Mapping of deprecated names to their modules and alternatives
_DEPRECATED_NAMES = {
    # Legacy base classes
    "BaseAgent": (
        "openjiuwen.core.single_agent.legacy.agent",
        "BaseAgent",
        "openjiuwen.core.single_agent.agent.BaseAgent"
    ),
    "ControllerAgent": (
        "openjiuwen.core.single_agent.legacy.agent",
        "ControllerAgent",
        "openjiuwen.core.single_agent.agent.BaseAgent"
    ),
    "AgentSession": (
        "openjiuwen.core.single_agent.legacy.agent",
        "AgentSession",
        "openjiuwen.core.session.Session"
    ),
    "WorkflowFactory": (
        "openjiuwen.core.single_agent.legacy.agent",
        "WorkflowFactory",
        "Workflow class directly"
    ),
    "workflow_provider": (
        "openjiuwen.core.single_agent.legacy.agent",
        "workflow_provider",
        "Workflow class directly"
    ),
    "PluginSchema": (
        "openjiuwen.core.single_agent.legacy.schema",
        "PluginSchema",
        "Tool class directly"
    ),
    # Legacy configs
    "AgentConfig": (
        "openjiuwen.core.single_agent.legacy.config",
        "AgentConfig",
        "AgentCard + ReActAgentConfig"
    ),
    "ReActAgentConfig": (
        "openjiuwen.core.single_agent.legacy.config",
        "LegacyReActAgentConfig",
        "openjiuwen.core.single_agent.agents.react_agent.ReActAgentConfig"
    ),
    "LLMCallConfig": (
        "openjiuwen.core.single_agent.legacy.config",
        "LLMCallConfig",
        "ReActAgentConfig"
    ),
    "IntentDetectionConfig": (
        "openjiuwen.core.single_agent.legacy.config",
        "IntentDetectionConfig",
        "new config classes"
    ),
    "ConstrainConfig": (
        "openjiuwen.core.single_agent.legacy.config",
        "ConstrainConfig",
        "ReActAgentConfig"
    ),
    "DefaultResponse": (
        "openjiuwen.core.single_agent.legacy.config",
        "DefaultResponse",
        "new config classes"
    ),
    "WorkflowAgentConfig": (
        "openjiuwen.core.single_agent.legacy.config",
        "WorkflowAgentConfig",
        "WorkflowAgentConfig from workflow module"
    ),
    # Schema classes
    "WorkflowSchema": (
        "openjiuwen.core.single_agent.legacy.schema",
        "WorkflowSchema",
        "WorkflowCard"
    ),
    # Legacy ReAct agent
    "ReActAgent": (
        "openjiuwen.core.single_agent.legacy.react_agent",
        "LegacyReActAgent",
        "openjiuwen.core.single_agent.agents.react_agent.ReActAgent"
    ),
    "create_react_agent_config": (
        "openjiuwen.core.single_agent.legacy.react_agent",
        "create_react_agent_config",
        "ReActAgentConfig() constructor"
    ),
}

# Cache for loaded modules
_loaded_modules = {}


def clear_module_cache() -> None:
    """Clear the module cache for testing purposes.
    
    This function is provided for testing scenarios where you need to
    re-trigger deprecation warnings by forcing module re-imports.
    """
    _loaded_modules.clear()


def _import_deprecated(name: str) -> Any:
    """Import a deprecated name and issue warning.
    
    Args:
        name: Name to import
        
    Returns:
        The imported object
    """
    if name not in _DEPRECATED_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    
    module_path, attr_name, alternative = _DEPRECATED_NAMES[name]
    
    # Issue deprecation warning
    warnings.warn(
        f"{name} is deprecated and will be removed in the future. "
        f"Please use {alternative} instead.",
        DeprecationWarning,
        stacklevel=3
    )
    
    # Import and cache
    if module_path not in _loaded_modules:
        import importlib
        _loaded_modules[module_path] = importlib.import_module(module_path)
    
    return getattr(_loaded_modules[module_path], attr_name)


def __getattr__(name: str) -> Any:
    """Lazy loading with deprecation warnings.
    
    This is called when an attribute is not found in the module's namespace.
    We use it to issue deprecation warnings when legacy names are accessed.
    """
    if name in _DEPRECATED_NAMES:
        return _import_deprecated(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """Return list of available attributes."""
    return ["AgentCard"] + list(_DEPRECATED_NAMES.keys())


__all__ = ["AgentCard"] + list(_DEPRECATED_NAMES.keys())
