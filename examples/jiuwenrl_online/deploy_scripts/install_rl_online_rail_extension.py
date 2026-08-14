#!/usr/bin/env python3
# coding: utf-8

"""Install the online-RL Rail as a JiuwenClaw user Rail extension.

This keeps JiuwenClaw source close to its develop branch. JiuwenClaw's existing
RailManager loads enabled extensions from:

    ${JIUWENSWARM_DATA_DIR:-~/.jiuwenswarm}/agent/workspace/extensions

The installed wrapper is intentionally no-arg because RailManager instantiates
extension classes with ``rail_class()``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


EXTENSION_NAME = "rl_online"
CLASS_NAME = "RLOnlineExtensionRail"

RAIL_PY = '''# coding: utf-8
"""JiuwenClaw extension wrapper for agent-core RLOnlineRail."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.agent_evolving.agent_rl.online.core.rail_factory import (
    build_rl_online_rail_from_env,
)


def _patch_rail_manager_for_session_scoped_agents() -> None:
    """Keep extension registration per active DeepAgent instance.

    JiuwenSwarm develop uses session-scoped DeepAgent instances, while its
    RailManager tracks registered extension names globally.  Without this
    patch, the first session registers rl_online and later sessions are skipped
    as "already registered" even though their DeepAgent has no RLOnlineRail.
    """
    try:
        from jiuwenswarm.agents.harness.common.plugins import rail_manager as manager_module
    except Exception:
        return

    manager_cls = getattr(manager_module, "RailManager", None)
    if manager_cls is None or getattr(manager_cls, "_rl_online_agent_core_patch", False):
        return

    original_set_agent_instance = manager_cls.set_agent_instance
    original_hot_reload_rail = manager_cls.hot_reload_rail

    def patched_set_agent_instance(self, agent_instance: Any) -> None:
        previous_agent = getattr(self, "_agent_instance", None)
        original_set_agent_instance(self, agent_instance)
        if previous_agent is not None and previous_agent is not agent_instance:
            getattr(self, "_registered_rails", set()).discard("rl_online")
            getattr(self, "_rail_instances", {}).pop("rl_online", None)

    async def patched_hot_reload_rail(self, name: str, enabled: bool) -> None:
        if name == "rl_online" and enabled and name in getattr(self, "_registered_rails", set()):
            rail_instance = getattr(self, "_rail_instances", {}).get(name)
            agent_instance = getattr(self, "_agent_instance", None)
            agent_rails = []
            if agent_instance is not None:
                agent_rails.extend(getattr(agent_instance, "_pending_rails", []) or [])
                agent_rails.extend(getattr(agent_instance, "_registered_rails", []) or [])
            if rail_instance is None or rail_instance not in agent_rails:
                getattr(self, "_registered_rails", set()).discard(name)
                getattr(self, "_rail_instances", {}).pop(name, None)
        await original_hot_reload_rail(self, name, enabled)

    manager_cls.set_agent_instance = patched_set_agent_instance
    manager_cls.hot_reload_rail = patched_hot_reload_rail
    manager_cls._rl_online_agent_core_patch = True


_patch_rail_manager_for_session_scoped_agents()


class RLOnlineExtensionRail(AgentRail):
    """No-arg wrapper loaded by JiuwenClaw RailManager."""

    priority: int = 100

    def __init__(self) -> None:
        _patch_rail_manager_for_session_scoped_agents()
        self._inner = build_rl_online_rail_from_env()

    def __getattr__(self, name: str) -> Any:
        if self._inner is None:
            raise AttributeError(name)
        return getattr(self._inner, name)

    def init(self, agent: Any) -> None:
        if self._inner is not None:
            self._inner.init(agent)

    def uninit(self, agent: Any) -> None:
        if self._inner is not None:
            self._inner.uninit(agent)

    def get_callbacks(self) -> dict[Any, Any]:
        if self._inner is None:
            return {}
        return self._inner.get_callbacks()
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent-workspace",
        default=os.getenv("JIUWENCLAW_AGENT_WORKSPACE", ""),
        help="JiuwenClaw agent workspace path; defaults from env or JIUWENSWARM_DATA_DIR.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing rl_online extension rail.py.",
    )
    return parser.parse_args()


def resolve_agent_workspace(raw: str) -> Path:
    if raw.strip():
        return Path(raw).expanduser().resolve()
    data_dir = os.getenv("JIUWENSWARM_DATA_DIR", "").strip()
    if data_dir:
        return (Path(data_dir).expanduser() / "agent" / "workspace").resolve()
    return (Path.home() / ".jiuwenswarm" / "agent" / "workspace").resolve()


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid extensions config JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Extensions config must be a JSON object: {path}")
    return payload


def main() -> None:
    args = parse_args()
    agent_workspace = resolve_agent_workspace(args.agent_workspace)
    extensions_dir = agent_workspace / "extensions"
    extension_dir = extensions_dir / EXTENSION_NAME
    config_path = extensions_dir / "extensions_config.json"
    rail_path = extension_dir / "rail.py"

    if rail_path.exists() and not args.force:
        raise FileExistsError(f"{rail_path} already exists; use --force to overwrite it")

    extension_dir.mkdir(parents=True, exist_ok=True)
    (extension_dir / "__init__.py").write_text("", encoding="utf-8")
    rail_path.write_text(RAIL_PY, encoding="utf-8")

    config = load_config(config_path)
    config[EXTENSION_NAME] = {
        "name": EXTENSION_NAME,
        "class_name": CLASS_NAME,
        "enabled": True,
        "description": "agent-core online RL trajectory collection and upload Rail",
        "priority": 100,
    }
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"installed={extension_dir}")
    print(f"config={config_path}")
    print("enable with USE_RL_ONLINE_RAIL=1 and TRAJECTORY_GATEWAY_URL=http://host:port")


if __name__ == "__main__":
    main()
