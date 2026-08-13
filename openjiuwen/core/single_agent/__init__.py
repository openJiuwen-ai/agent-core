# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Single Agent Module

This module provides exports for single agent functionality.
Legacy implementations are in the legacy/ directory and should be
imported from openjiuwen.core.single_agent.legacy explicitly.
"""

from importlib import import_module

_EXPORTS = {
    "Session": "openjiuwen.core.session.agent",
    "create_agent_session": "openjiuwen.core.session.agent",
    "BaseAgent": "openjiuwen.core.single_agent.base",
    "AbilityManager": "openjiuwen.core.single_agent.ability_manager",
    "AddAbilityResult": "openjiuwen.core.single_agent.ability_manager",
    "ReActAgent": "openjiuwen.core.single_agent.agents.react_agent",
    "ReActAgentConfig": "openjiuwen.core.single_agent.agents.react_agent",
    "ReActAgentEvolve": "openjiuwen.core.single_agent.agents.react_agent_evolve",
    "AgentCard": "openjiuwen.core.single_agent.schema.agent_card",
    "LegacyBaseAgent": "openjiuwen.core.single_agent.legacy",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name in _EXPORTS:
        module = import_module(_EXPORTS[name])
        return getattr(module, name)
    return import_module("." + name, __name__)
