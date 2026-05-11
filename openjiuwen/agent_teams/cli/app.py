# coding: utf-8
"""Public entry point for the Team CLI.

:func:`run_team_cli` is the single async entry the SDK exposes for
embedding the interactive CLI inside another application. It builds a
:class:`SpecRegistry` from the supplied YAML paths and / or in-memory
``TeamAgentSpec`` dict, brings up :class:`Runner`, drives
:class:`TeamCli` to completion, and tears everything down on exit.

Callers who already manage ``Runner.start()`` / ``Runner.stop()``
themselves (e.g. inside a larger framework) should instantiate
``TeamCli`` directly — this helper exists for the ``examples/``
launcher and small standalone tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import (
    AsyncIterator,
    Iterable,
)

from openjiuwen.agent_teams.cli.spec_loader import SpecRegistry
from openjiuwen.agent_teams.cli.tui import TeamCli
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.runner.runner import Runner


async def run_team_cli(
    *,
    specs: dict[str, TeamAgentSpec] | None = None,
    yaml_paths: Iterable[str | Path] | None = None,
    input_iter: AsyncIterator[str] | None = None,
    manage_runner: bool = True,
) -> None:
    """Bring up the Team CLI against a pre-built spec set.

    Args:
        specs: Mapping of ``team_name → TeamAgentSpec`` to register as
            in-memory entries. The dict key is informational; the
            registered name comes from ``spec.team_name``.
        yaml_paths: Iterable of YAML files to load through
            :meth:`SpecRegistry.add_yaml`.
        input_iter: Optional pre-canned async iterator of input lines
            used by tests; the prompt UI is bypassed when supplied.
        manage_runner: When ``True`` (default), call ``Runner.start()``
            before the loop and ``Runner.stop()`` after teardown. Set
            to ``False`` when the embedding application already owns
            the runner lifecycle.
    """
    registry = SpecRegistry()
    if yaml_paths:
        registry.bulk_load_yaml(yaml_paths)
    if specs:
        registry.bulk_register(specs)
    cli = TeamCli(registry)
    if manage_runner:
        await Runner.start()
    try:
        await cli.run(input_iter=input_iter)
    finally:
        await cli.shutdown()
        if manage_runner:
            await Runner.stop()


__all__ = ["run_team_cli"]
