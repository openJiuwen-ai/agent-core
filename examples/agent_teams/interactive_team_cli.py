# coding: utf-8
"""Interactive Team CLI launcher.

Drops the user into the ``agent_teams.cli`` interactive prompt with one
or more YAML team specs pre-registered. Defaults to
``examples/agent_teams/config.yaml`` (the runner-owner spec); pass other
YAML paths as positional arguments to load alternates instead.

Run from repo root::

    source .venv/bin/activate
    export PYTHONPATH=.:$PYTHONPATH
    # default runner-owner spec
    python examples/agent_teams/interactive_team_cli.py
    # HITT blast-furnace e2e flow
    python examples/agent_teams/interactive_team_cli.py \
        examples/agent_teams/config_hitt_blast_furnace.yaml
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from _e2e_utils import load_team_config  # noqa: E402

from openjiuwen.agent_teams.cli import run_team_cli  # noqa: E402
from openjiuwen.agent_teams.paths import configure_openjiuwen_home  # noqa: E402
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec  # noqa: E402
from openjiuwen.core.common.logging.log_config import (  # noqa: E402
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG  # noqa: E402

_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_DEFAULT_TEAM_CONFIG_PATH = _HERE / "config.yaml"

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")
# Bind any stdio MCP subprocess (e.g. the HITT blast-furnace example)
# to the same interpreter the CLI is running under, so it picks up the
# venv that has the `mcp` package installed. Mirrors main.py.
os.environ.setdefault("PYTHON_BIN", sys.executable)

configure_openjiuwen_home(str(Path("./openjiuwen_home").resolve()))


def _resolve_yaml_paths(argv: list[str]) -> list[Path]:
    """Resolve CLI argv into one or more YAML paths to register."""
    if not argv:
        return [_DEFAULT_TEAM_CONFIG_PATH]
    return [Path(arg).expanduser().resolve() for arg in argv]


async def main() -> None:
    """Build specs from CLI argv (or the default yaml) and run the CLI."""
    yaml_paths = _resolve_yaml_paths(sys.argv[1:])
    specs: dict[str, TeamAgentSpec] = {}
    for path in yaml_paths:
        raw = load_team_config(path)
        raw.pop("runtime", None)
        spec = TeamAgentSpec.model_validate(raw)
        specs[spec.team_name] = spec
    await run_team_cli(specs=specs)


if __name__ == "__main__":
    asyncio.run(main())
