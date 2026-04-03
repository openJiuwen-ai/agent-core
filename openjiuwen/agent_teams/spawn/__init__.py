# coding: utf-8
"""In-process spawn support for single-process agent teams."""

from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
from openjiuwen.agent_teams.spawn.inprocess_spawn import inprocess_spawn
from openjiuwen.agent_teams.spawn.shared_resources import (
    get_or_create_memory_db,
    get_or_create_runtime,
    cleanup_shared_resources,
)

__all__ = [
    "InProcessSpawnHandle",
    "inprocess_spawn",
    "get_or_create_memory_db",
    "get_or_create_runtime",
    "cleanup_shared_resources",
]
