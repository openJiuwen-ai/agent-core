# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Team IntelliRouter E2E — one leader + three teammates on a reliable router.

Verifies the ``intelli_router`` allocation strategy end to end:

1. ``TeamAgentSpec.model_intelli_router`` expands into a flat ``model_pool``
   (one entry per logical model name, each carrying the whole deployment
   list) and forces ``model_pool_strategy="intelli_router"``.
2. The leader, with no ``model_name``, falls back to the router's first
   declared name — ``"*"``, i.e. unified routing across every deployment.
3. Each teammate pins a different logical model name and therefore routes
   only within that model's deployments.
4. The team actually runs: the leader messages all three teammates and
   each answers. Since the three are pinned to three different models, a
   reply from all three means every route resolved and served a request.

Steps 1-3 are asserted offline (no network). Step 4 needs live endpoints.

Note step 4 does not ask members which model they run on: an LLM cannot
introspect the deployment serving it and would simply make one up. The
model-to-member mapping is what steps 1-3 assert.

Because a persistent team stores each member's ``(model_name, model_index)``
reference in the team DB at build time, changing ``model_names`` in the YAML
after a first run leaves stale references behind (a member pinned to a name
the new pool no longer has resolves to no model at all). Drop the team's rows
from ``~/.openjiuwen/.agent_teams/team.db`` when you re-point this config.

Run directly:
    python tests/system_tests/agent_swarm/agent_team_intelli_router_e2e.py

Requires the optional IntelliRouter dependency:
    uv pip install "intelli-router @ git+https://gitcode.com/openJiuwen/agent-protocol.git\
@feature/intelliRouter#subdirectory=intelli_router"

Endpoints come from ``../config_llm_local.yaml`` (see ``llm_config.py``); no
credentials live in this file or in ``config_intelli_router.yaml``. The team
YAML declares only the roster and which model names to offer — the deployments
behind those names are generated from every endpoint in the config that serves
them, so adding a key or a host there widens this test's fleet for free.
Point OPENJIUWEN_E2E_LLM_CONFIG at another file to run elsewhere.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# Ensure _e2e_utils / llm_config are importable regardless of working directory
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from openjiuwen.agent_teams.models import INTELLI_ROUTER_UNIFIED_MODEL
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

from _e2e_utils import consume_stream, load_team_config
from llm_config import DEFAULT_CONFIG_PATH, LlmConfig, load_llm_config

_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config_intelli_router.yaml"

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

_EXPECTED_LEADER_MODEL = INTELLI_ROUTER_UNIFIED_MODEL


# ---------------------------------------------------------------------------
# Endpoint wiring — deployments are generated from config_llm_local.yaml
# ---------------------------------------------------------------------------
def _build_deployments(cfg: LlmConfig, model_names: list[str]) -> list[dict]:
    """Render one deployment per (endpoint, model) pair the config offers.

    Every endpoint declaring one of ``model_names`` contributes a deployment,
    so a model backed by two keys yields two deployments and the router can
    fail over between them. Widening the fleet is then a config edit, not a
    code change.

    ``"*"`` is skipped: it is a routing directive (use every deployment), not
    a model any endpoint serves.

    Raises:
        ValueError: when the config serves none of the requested models —
            better here than as a 404 from an endpoint that never heard of it.
    """
    wanted = [m for m in model_names if m != INTELLI_ROUTER_UNIFIED_MODEL]
    cfg.require(*wanted)
    deployments: list[dict] = []
    for model in wanted:
        for endpoint in cfg.endpoints_for(model):
            deployments.append(endpoint.router_deployment(model, rpm=60, timeout=60.0, tags=[endpoint.name]))
    return deployments


def _require_intelli_router() -> None:
    """Fail fast with an actionable message when the optional dep is absent."""
    try:
        import intelli_router  # noqa: F401
    except ImportError:
        print("[x] The 'intelli_router' package is required for this E2E but is not installed.")
        print("    uv pip install 'intelli-router @ git+https://gitcode.com/openJiuwen/")
        print("    agent-protocol.git@feature/intelliRouter#subdirectory=intelli_router'")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Offline assertions
# ---------------------------------------------------------------------------
def _print_pool_summary(spec: TeamAgentSpec) -> None:
    """Print the pool the router expanded into.

    ``spec.model_pool_strategy`` still holds whatever the YAML declared
    (or its default) — ``build()`` is what forces ``"intelli_router"`` —
    so print the effective value rather than the raw field.
    """
    router = spec.model_intelli_router
    print(f"  strategy    : intelli_router  (forced at build; spec declares {spec.model_pool_strategy!r})")
    print(f"  deployments : {len(router.deployments)}")
    for dep in router.deployments:
        print(f"      - {dep.id:<22} model={dep.model_name:<18} provider={dep.provider:<10} base={dep.api_base}")
    print()
    pool = router.to_pool_entries()
    print(f"  pool entries: {len(pool)}  (one per logical model name)")
    print(f"      {'#':<3}  {'model_name':<20}  {'provider':<16}  deployments")
    for idx, entry in enumerate(pool):
        count = len(entry.metadata["client"]["intelli_router_deployments"])
        print(f"      {idx:<3}  {entry.model_name:<20}  {entry.api_provider:<16}  {count}")
    print()


def _verify_allocation(spec: TeamAgentSpec) -> None:
    """Assert the pool expansion and per-member allocation, offline.

    Builds the allocator straight off the expanded ``TeamSpec`` rather
    than driving ``spec.build()``, so this stays a pure check of the
    allocation contract with no runtime or network involved.
    """
    from openjiuwen.agent_teams.models import build_model_allocator
    from openjiuwen.agent_teams.models.pool import INTELLI_ROUTER_PROVIDER
    from openjiuwen.agent_teams.schema.team import TeamSpec

    pool = spec.model_intelli_router.to_pool_entries()
    team_spec = TeamSpec(
        team_name=spec.team_name,
        display_name=spec.team_name,
        model_pool=pool,
        model_pool_strategy="intelli_router",
    )

    providers = {entry.api_provider for entry in pool}
    assert providers == {INTELLI_ROUTER_PROVIDER}, f"unexpected providers: {providers}"

    allocator = build_model_allocator(spec, team_spec)
    assert type(allocator).__name__ == "IntelliRouterAllocator", f"got {type(allocator).__name__}"

    # Leader has no model_name -> first declared name (unified routing).
    leader_alloc = allocator.allocate(model_name=spec.leader.model_name)
    leader_model = leader_alloc.entry.model_name
    assert leader_model == _EXPECTED_LEADER_MODEL, f"leader got {leader_model!r}"
    print(f"  leader  {'team_leader':<8} -> {leader_model:<20} (unified routing across all deployments)")

    pinned = {m.model_name for m in spec.predefined_members}
    assert len(pinned) == len(spec.predefined_members), f"teammates must pin distinct models, got {pinned}"

    for member in spec.predefined_members:
        alloc = allocator.allocate(model_name=member.model_name)
        assert alloc is not None, f"{member.member_name} allocated nothing"
        assert alloc.entry.model_name == member.model_name, f"{member.member_name} got {alloc.entry.model_name!r}"
        deployments = alloc.entry.metadata["client"]["intelli_router_deployments"]
        peers = [d["id"] for d in deployments if d["model_name"] == member.model_name]
        assert peers, f"{member.member_name} pinned {member.model_name!r} with no deployment behind it"
        print(
            f"  member  {member.member_name:<8} -> {alloc.entry.model_name:<20} "
            f"(routes within {len(peers)} deployment(s): {', '.join(peers)})"
        )

    # Every member's client is handed the full deployment list regardless of
    # which name it pinned — failover is the router's job, not the allocator's.
    for entry in pool:
        count = len(entry.metadata["client"]["intelli_router_deployments"])
        assert count == len(spec.model_intelli_router.deployments), f"{entry.model_name} sees {count} deployments"

    # An unknown name must not silently route somewhere else.
    assert allocator.allocate(model_name="no-such-model") is None
    print("  unknown model_name -> None (falls back to the per-agent model)")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    _require_intelli_router()

    cfg = load_team_config(_TEAM_CONFIG_PATH)
    runtime_cfg: dict[str, Any] = cfg.pop("runtime", {})

    # The team YAML declares which model names to offer; the deployments behind
    # them come from the endpoint config, so credentials never live in the team
    # file and a new key there widens this run's fleet on its own.
    llm_cfg = load_llm_config()
    router_cfg = cfg["model_intelli_router"]
    router_cfg["deployments"] = _build_deployments(llm_cfg, router_cfg["model_names"])
    spec = TeamAgentSpec.model_validate(cfg)

    print("=" * 74)
    print("Agent Team IntelliRouter E2E — 1 leader + 3 teammates")
    print("=" * 74)
    print()
    print(f"  team_name : {spec.team_name}")
    print(f"  roster    : team_leader + {[m.member_name for m in spec.predefined_members]}")
    print(f"  endpoints : {[e.name for e in llm_cfg.endpoints]}  (from {DEFAULT_CONFIG_PATH.name})")
    print()
    print("IntelliRouter:")
    _print_pool_summary(spec)
    print("Allocation:")
    _verify_allocation(spec)
    print("[v] Offline allocation checks passed.")
    print()

    await Runner.start()
    print("=" * 74)
    print("Running the team against live endpoints...")
    print("=" * 74)
    # consume_stream writes to fd 1 directly, so flush the buffered prints
    # above or they would surface after the stream output.
    sys.stdout.flush()
    try:
        await consume_stream(
            spec,
            runtime_cfg.get("initial_query", "hello"),
            runtime_cfg.get("session_id", "intelli_router_session"),
        )
    finally:
        await Runner.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
