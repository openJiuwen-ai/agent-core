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

Endpoint configuration is read from the sibling model configs — no
credentials live in this file:
    ../config_llm_local.yaml    — DeepSeek endpoint, key #1
    ../config_llm_local_2.yaml  — DeepSeek endpoint, key #2 (failover peer)
    ../config_llm_local_3.yaml  — OpenAI-compatible multi-model gateway

Override any of them by exporting the variables the YAML interpolates:
    IR_DEEPSEEK_BASE / IR_DEEPSEEK_KEY_1 / IR_DEEPSEEK_KEY_2
    IR_GATEWAY_BASE  / IR_GATEWAY_KEY
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import yaml

# Ensure _e2e_utils is importable regardless of working directory
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from openjiuwen.agent_teams.models import INTELLI_ROUTER_UNIFIED_MODEL
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.runner.runner import Runner

from _e2e_utils import consume_stream, load_team_config

_LOG_CONFIG_PATH = _HERE / "logging.yaml"
_TEAM_CONFIG_PATH = _HERE / "config_intelli_router.yaml"

# Sibling model configs supplying real endpoints (see module docstring).
_DEEPSEEK_CONFIG_1 = _HERE.parent / "config_llm_local.yaml"
_DEEPSEEK_CONFIG_2 = _HERE.parent / "config_llm_local_2.yaml"
_GATEWAY_CONFIG = _HERE.parent / "config_llm_local_3.yaml"

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

_EXPECTED_LEADER_MODEL = INTELLI_ROUTER_UNIFIED_MODEL
_EXPECTED_TEAMMATE_MODELS = {
    "alice": "deepseek-v4-flash",
    "bob": "Qwen3.7-Plus",
    "carol": "GLM-5.2",
}


# ---------------------------------------------------------------------------
# Endpoint wiring
# ---------------------------------------------------------------------------
def _read_llm_config(path: Path) -> dict[str, Any]:
    """Load one ``config_llm_local*.yaml``, or return {} when absent."""
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _to_router_base(api_base: str) -> str:
    """Drop the ``/v1`` suffix an openjiuwen ``api_base`` carries.

    The sibling configs are written for openjiuwen's own clients, whose
    ``api_base`` points at the OpenAI-compatible API root and so ends in
    ``/v1``. IntelliRouter's adapters append that path themselves, so the
    same value would produce ``/v1/v1/chat/completions``. See
    ``IntelliRouterDeployment.api_base``.
    """
    return api_base.rstrip("/").removesuffix("/v1")


def _export_endpoints() -> None:
    """Seed the ``IR_*`` variables the team YAML interpolates.

    Values already present in the environment win, so a caller can point
    the run at different endpoints without editing any YAML.
    """
    deepseek_1 = _read_llm_config(_DEEPSEEK_CONFIG_1)
    deepseek_2 = _read_llm_config(_DEEPSEEK_CONFIG_2)
    gateway = _read_llm_config(_GATEWAY_CONFIG)

    defaults = {
        "IR_DEEPSEEK_BASE": _to_router_base(deepseek_1.get("api_base", "")),
        "IR_DEEPSEEK_KEY_1": deepseek_1.get("api_key", ""),
        # Falls back to key #1 when the second config is missing; the router
        # then has one deployment for that model instead of two.
        "IR_DEEPSEEK_KEY_2": deepseek_2.get("api_key") or deepseek_1.get("api_key", ""),
        "IR_GATEWAY_BASE": _to_router_base(gateway.get("api_base", "")),
        "IR_GATEWAY_KEY": gateway.get("api_key", ""),
    }
    missing = [name for name, value in defaults.items() if not value and not os.environ.get(name)]
    if missing:
        print(f"[!] No endpoint value for: {', '.join(missing)}")
        print(f"    Populate {_DEEPSEEK_CONFIG_1.name} / {_GATEWAY_CONFIG.name} or export the variables.")
        sys.exit(1)
    for name, value in defaults.items():
        if value:
            os.environ.setdefault(name, value)


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

    for member in spec.predefined_members:
        alloc = allocator.allocate(model_name=member.model_name)
        assert alloc is not None, f"{member.member_name} allocated nothing"
        expected = _EXPECTED_TEAMMATE_MODELS[member.member_name]
        assert alloc.entry.model_name == expected, f"{member.member_name} got {alloc.entry.model_name!r}"
        deployments = alloc.entry.metadata["client"]["intelli_router_deployments"]
        peers = [d["id"] for d in deployments if d["model_name"] == expected]
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
    _export_endpoints()

    cfg = load_team_config(_TEAM_CONFIG_PATH)
    runtime_cfg: dict[str, Any] = cfg.pop("runtime", {})
    spec = TeamAgentSpec.model_validate(cfg)

    print("=" * 74)
    print("Agent Team IntelliRouter E2E — 1 leader + 3 teammates")
    print("=" * 74)
    print()
    print(f"  team_name : {spec.team_name}")
    print(f"  roster    : team_leader + {[m.member_name for m in spec.predefined_members]}")
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
