# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Scenario-scoped coordination event handlers.

Each handler class owns one business domain: agent lifecycle, member
events, messages, task board, stale-task sweep, or team completion.
They share the ``DispatcherHost`` contract and register through
:class:`BaseCoordinationHandler.get_callbacks`.
"""

from __future__ import annotations

from openjiuwen.agent_teams.agent.coordination.handlers.agent_lifecycle import AgentLifecycleHandler
from openjiuwen.agent_teams.agent.coordination.handlers.base import (
    BaseCoordinationHandler,
    EventCallback,
)
from openjiuwen.agent_teams.agent.coordination.handlers.member import MemberHandler
from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.agent.coordination.handlers.stale_task import StaleTaskHandler
from openjiuwen.agent_teams.agent.coordination.handlers.task_board import TaskBoardHandler
from openjiuwen.agent_teams.agent.coordination.handlers.team_completion import TeamCompletionHandler

__all__ = [
    "AgentLifecycleHandler",
    "BaseCoordinationHandler",
    "EventCallback",
    "MemberHandler",
    "MessageHandler",
    "StaleTaskHandler",
    "TaskBoardHandler",
    "TeamCompletionHandler",
]
