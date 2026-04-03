# coding: utf-8
"""In-process spawn and process-global shared resources for agent teams."""

from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
from openjiuwen.agent_teams.spawn.inprocess_spawn import inprocess_spawn
from openjiuwen.agent_teams.spawn.shared_resources import (
    get_shared_db,
    get_shared_runtime,
    cleanup_shared_resources,
)

__all__ = [
    "InProcessSpawnHandle",
    "inprocess_spawn",
    "get_shared_db",
    "get_shared_runtime",
    "cleanup_shared_resources",
]
