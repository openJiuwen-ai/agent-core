# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed inputs and outputs of the team extension events.

The events framework types ``EventInput`` / ``EventOutput`` as plain object
aliases, so these models are self-contained frozen dataclasses: the bus hands
them to matchers and handlers as-is, and business entries validate the final
output type after the chain returns.

One event is covered here: ``team.monitor_event`` (public monitor
notifications). The ``team.build_policy`` contracts arrive in a follow-up
change.
"""

from __future__ import annotations

from dataclasses import dataclass

from openjiuwen.agent_teams.monitor.models import MonitorEvent


@dataclass(frozen=True, slots=True)
class TeamMonitorEventInput:
    """One public monitor notification delivered to the extension chain.

    ``member_name`` mirrors ``monitor_event.member_name``: the member the
    notification is *about*, never the receiver and never inferred from a
    sender or recipient field. The internal ``EventMessage`` never crosses
    this boundary.
    """

    team_name: str
    member_name: str | None
    monitor_event: MonitorEvent


@dataclass(frozen=True, slots=True)
class TeamMonitorEventOutput:
    """Marker that the notification chain finished.

    Says nothing about which handlers ran — the chain may short-circuit, so
    completed output does not imply every subscriber observed the event.
    """
