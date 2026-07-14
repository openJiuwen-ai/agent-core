# coding: utf-8
"""Swarmflow concurrency E2E — concurrent nested workflows via parallel().

Drives ``resources/concurrent_invites.py`` from the team entry point. That script
fans out N invitation-card SUB-workflows concurrently through ``parallel()``; this
test proves every one of them actually runs (none skipped by the nesting-depth
guard) by counting the invitation-card writer's ``agent_completed`` events.

It reuses the swarmflow E2E harness (`agent_team_swarmflow_e2e`): importing that
module configures logging / openjiuwen_home / model env, and its ``_wire_model_env``
/ ``_run_team`` / ``_SwarmflowProbe`` drive the leader stream and progress probe.

Run directly (needs a real model endpoint, see config_llm_local.yaml):
    python tests/system_tests/agent_swarm/agent_team_swarmflow_concurrent_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.runner.runner import Runner

import agent_team_swarmflow_e2e as base
from _e2e_utils import load_team_config
from tests.test_logger import logger as test_logger

_TEAM_CONFIG_PATH = _HERE / "config_swarmflow.yaml"
# cwd is base._WORKDIR (a scratch dir under agent_swarm), so reach the script via ``..``.
_SCRIPT_REL = "../resources/concurrent_invites.py"
_INVITE_WRITER_LABEL = "写邀请函"   # invitation_card.py's agent label
_EXPECTED_INVITES = 3               # len(INVITES) in concurrent_invites.py


def _verify(probe: base._SwarmflowProbe) -> None:
    """Assert all N sub-workflows ran concurrently (none skipped)."""
    assert probe.started, "workflow never emitted workflow_started"
    assert probe.workflow_name == "concurrent-invites", (
        f"unexpected workflow name: {probe.workflow_name!r}"
    )
    assert probe.completed, "workflow did not complete (failed or timed out)"
    assert not probe.failed, "workflow reported a failure"
    ran = probe.agent_completed.get(_INVITE_WRITER_LABEL, 0)
    assert ran == _EXPECTED_INVITES, (
        f"expected {_EXPECTED_INVITES} concurrent sub-workflows to complete, got "
        f"{ran} (label={_INVITE_WRITER_LABEL!r}) — a regression would skip "
        "all-but-one concurrent workflow()"
    )
    test_logger.info("[swarmflow] verified: %d concurrent sub-workflows ran", ran)


async def main() -> int:
    base._wire_model_env()
    # Same scratch-dir discipline as the base E2E: keep runtime scaffolding out of
    # the test tree, and hand the leader a short relative script path.
    base._WORKDIR.mkdir(parents=True, exist_ok=True)
    os.chdir(base._WORKDIR)

    cfg = load_team_config(_TEAM_CONFIG_PATH)
    cfg.pop("runtime", {})
    spec = TeamAgentSpec.model_validate(cfg)

    # Unique session id every run so swarmflow never silently resumes a prior
    # journal (keyed by team + session_id + workflow name) — forces a live run.
    session_id = f"swarmflow_concurrent_{uuid.uuid4().hex[:8]}"

    query = (
        "请立即调用 swarmflow 工具来运行一个多 agent 工作流。"
        f"参数:script_path 必须填 \"{_SCRIPT_REL}\",args 留空。"
        "只需调用这一个工具,然后等待工作流结果即可,不要自己拆解或执行其它步骤。"
    )

    await Runner.start()
    test_logger.info("=" * 60)
    test_logger.info("Swarmflow concurrency E2E — %s", _SCRIPT_REL)
    test_logger.info("=" * 60)

    probe = await base._run_team(spec, query, session_id)

    await Runner.stop()

    try:
        _verify(probe)
    except AssertionError as e:
        test_logger.error("[swarmflow] VERIFICATION FAILED: %s", e)
        return 1
    test_logger.info("[swarmflow] CONCURRENCY E2E PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
