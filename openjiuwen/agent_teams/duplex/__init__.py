# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Duplex delivery routing for agent teams (F_117).

A per-member supervisor decides HOW a new mailbox message reaches a busy (or
idle) member: interrupt and replan, steer, append, deliver now, or record only.
It never changes what the message says or what the member does with it.

``DuplexConfig`` is the user-facing entry point (hang it off
``TeamAgentSpec.duplex``). The router, scheduler and handlers are wired by the
coordination dispatcher at assembly time.
"""

from openjiuwen.agent_teams.duplex.config import DuplexConfig

__all__ = ["DuplexConfig"]
