# coding: utf-8
"""HITT Blast-Furnace Operation Team — interactive E2E.

Drives a real LLM-backed leader through the ``blast-furnace-operation-team``
SKILL with HITT enabled. The leader builds a team via ``build_team(enable_hitt=true)``,
serially dispatches a worker, and the worker hands off to the reserved
``human_agent`` so the human user (this CLI) can confirm or reject the
proposed operation before it is recorded by ``confirm_cli``.

Run directly via main.py from the repository root:
    source .venv/bin/activate
    export PYTHONPATH=.:$PYTHONPATH
    python examples/agent_teams/main.py

The furnace MCP server (``mcp_server/server.py``) is launched automatically
as a stdio subprocess by the framework — see the ``mcps`` block in
``config_hitt_blast_furnace.yaml``. The interpreter used for that subprocess
is taken from the ``PYTHON_BIN`` env var which we default to
``sys.executable`` below so it inherits the active venv. Make sure the
``mcp`` package is installed there (``pip install -r examples/agent_teams/
blast-furnace-operation-team/mcp_server/requirements.txt``).

stdin routing (parsed by ``parse_interact_str``; the leading prefix
must be followed by a space):
    <body>                            God-view → leader (default when no prefix).
    # <body>                          God-view → leader (explicit form).
    $human_agent <body>               Drive the human-agent avatar's LLM.
    $human_agent @<worker> <body>     Direct-reply as the human agent to a worker.
    @<member> <body>                  User-side direct message to a team member.
    exit / quit                       Stop the loop.

Configuration:
    config_hitt_blast_furnace.yaml — team spec, model, transport, storage,
        skill_use rail (skills_dir=examples/agent_teams,
        enabled_skills=[blast-furnace-operation-team]).
    logging.yaml — loguru logging sinks / routes (shared with other e2e scripts).

Environment variable overrides (take precedence over the YAML):
    API_BASE, LEADER_API_KEY, TEAMMATE_API_KEY, MODEL_NAME
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Awaitable, Callable

# Ensure _e2e_utils is importable regardless of working directory.
# The sys.path tweak forces the imports below to be flagged E402; we silence
# it locally because there is no clean sibling-module import without it.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from _e2e_utils import (  # noqa: E402
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_YELLOW,
    _write,
    load_team_config,
    run_interactive,
)

from openjiuwen.agent_teams.constants import HUMAN_AGENT_MEMBER_NAME  # noqa: E402
from openjiuwen.agent_teams.interaction import HumanAgentInboundEvent  # noqa: E402
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec  # noqa: E402
from openjiuwen.core.common.logging import team_logger  # noqa: E402
from openjiuwen.core.common.logging.log_config import (  # noqa: E402
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG  # noqa: E402
from openjiuwen.core.runner.runner import Runner  # noqa: E402

# ---------------------------------------------------------------------------
# Logging — must be configured before any logger is used
# ---------------------------------------------------------------------------
_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config_hitt_blast_furnace.yaml"

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")
# Bind the stdio MCP subprocess to the same interpreter the e2e is running
# under so it picks up the venv that has the `mcp` package installed.
os.environ.setdefault("PYTHON_BIN", sys.executable)


_BANNER = """
============================================================
HITT Blast-Furnace Operation Team — Interactive CLI

Prefix routing for stdin (a space MUST follow the prefix):
  <body>                          → leader (god-view, default)
  # <body>                        → leader (explicit god-view)
  $human_agent <body>             → drive the human_agent avatar
  $human_agent @<worker> <body>   → reply as human_agent to <worker>
  @<member> <body>                → direct user-side message to <member>
  exit / quit                     → stop

The furnace MCP server is launched automatically by the framework.
============================================================
"""


def _make_inbound_handler() -> Callable[[HumanAgentInboundEvent], Awaitable[None]]:
    """Return an ``on_inbound`` callback that surfaces team→user messages on stdout.

    The dispatcher fires this whenever a teammate (typically a worker) sends a
    message addressed to ``human_agent``. We render the message inline with the
    streaming output so the user can decide how to reply.
    """

    async def _handler(evt: HumanAgentInboundEvent) -> None:
        kind = "broadcast" if evt.broadcast else "direct"
        _write(
            f"\n{_COLOR_YELLOW}[Inbound to {evt.member_name} / {kind}] "
            f"from <{evt.sender}>: {evt.body}{_COLOR_RESET}\n"
            f"{_COLOR_DIM}  Reply with: $human_agent @{evt.sender} <your reply>"
            f"{_COLOR_RESET}\n"
        )

    return _handler


def _make_arm_inbound_on_ready(
    member_name: str,
    callback: Callable[[HumanAgentInboundEvent], Awaitable[None]],
) -> Callable[[str, str], Awaitable[None]]:
    """Build an ``on_runtime_ready`` hook that arms the inbound callback.

    The hook is fired by ``run_interactive`` after the first
    ``team.runtime_ready`` chunk lands — by then the leader's pool entry
    is in place, so ``Runner.register_human_agent_inbound`` can resolve
    it and reach the team backend without us holding a ``TeamAgent``
    reference here.
    """

    async def _on_ready(team_name: str, session_id: str) -> None:
        ok = await Runner.register_human_agent_inbound(
            team_name=team_name,
            session_id=session_id,
            member_name=member_name,
            callback=callback,
        )
        if ok:
            team_logger.info("[hitt-e2e] human-agent inbound callback armed for %s", member_name)
        else:
            team_logger.warning(
                "[hitt-e2e] failed to arm inbound callback for %s "
                "(no active runtime for team=%s session=%s)",
                member_name,
                team_name,
                session_id,
            )

    return _on_ready


async def main() -> None:
    cfg = load_team_config(_TEAM_CONFIG_PATH)
    runtime_cfg = cfg.pop("runtime", {})

    spec = TeamAgentSpec.model_validate(cfg)

    await Runner.start()

    session_id = runtime_cfg.get("session_id", "hitt_blast_furnace_session")

    print(_BANNER)

    try:
        await run_interactive(
            spec,
            runtime_cfg,
            default_session_id=session_id,
            on_runtime_ready=_make_arm_inbound_on_ready(
                member_name=HUMAN_AGENT_MEMBER_NAME,
                callback=_make_inbound_handler(),
            ),
        )
    finally:
        await Runner.stop()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
