# coding: utf-8
"""Interactive CLI for the agent_teams runtime.

Public surface:

* :func:`run_team_cli` — bring up the prompt-driven CLI against a set
  of YAML / in-memory ``TeamAgentSpec`` entries.
* :class:`TeamCli` — embeddable driver class for callers that manage
  the ``Runner`` lifecycle themselves.
* :class:`SpecRegistry` — name-keyed spec store backing the CLI.
"""

from openjiuwen.agent_teams.cli.app import run_team_cli
from openjiuwen.agent_teams.cli.spec_loader import (
    SpecEntry,
    SpecRegistry,
    load_spec_yaml,
)
from openjiuwen.agent_teams.cli.tui import TeamCli

__all__ = [
    "SpecEntry",
    "SpecRegistry",
    "TeamCli",
    "load_spec_yaml",
    "run_team_cli",
]
