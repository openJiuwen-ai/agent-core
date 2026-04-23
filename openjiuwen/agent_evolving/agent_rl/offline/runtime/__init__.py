# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Agent runtime for offline RL rollout generation.

This module provides trajectory collection capabilities for RL training.
The RLRail class (from openjiuwen.agent_evolving.agent_rl.rl_rail) is the
primary implementation based on EvolutionRail, providing:
- Automatic trajectory collection
- Tool result patching
- Task-loop iteration tracking
- RL-specific state management
"""

# Re-export for backward compatibility
from openjiuwen.agent_evolving.agent_rl.rl_rail import RLRail

__all__ = ["RLRail"]
