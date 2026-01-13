# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Legacy Interface Compatibility Layer

This package contains all deprecated interfaces for backward compatibility.
All interfaces in this package will be removed in v1.0.0.

For migration guide, see: docs/AGENT_MIGRATION_GUIDE.md

Created on: 2025-01-04
"""
import warnings
from typing import Any

# Mapping of deprecated names to their modules and new alternatives
_DEPRECATED_NAMES = {
    # Legacy agents
    "LegacyReActAgent": (
        "openjiuwen.core.single_agent.legacy.react_agent",
        "LegacyReActAgent",
        "openjiuwen.core.single_agent.agents.react_agent.ReActAgent"
    ),
    "create_react_agent_config": (
        "openjiuwen.core.single_agent.legacy.react_agent",
        "create_react_agent_config",
        "ReActAgentConfig() constructor"
    ),
    # Legacy base classes
    "LegacyBaseAgent": (
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
    # Legacy configs
    "AgentConfig": (
        "openjiuwen.core.single_agent.legacy.config",
        "AgentConfig",
        "AgentCard + ReActAgentConfig"
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
    "MemoryConfig": (
        "openjiuwen.core.single_agent.legacy.config",
        "MemoryConfig",
        "MemoryScopeConfig"
    ),
    "LegacyReActAgentConfig": (
        "openjiuwen.core.single_agent.legacy.config",
        "LegacyReActAgentConfig",
        "openjiuwen.core.single_agent.agents.react_agent.ReActAgentConfig"
    ),
}

# Cache for loaded modules
_loaded_modules = {}


def clear_module_cache() -> None:
    """Clear the module cache for testing purposes.

    Provided to support tests that need to force re-imports and re-trigger
    deprecation warnings without touching internal cache variables.
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
    return list(_DEPRECATED_NAMES.keys())


__all__ = list(_DEPRECATED_NAMES.keys())
