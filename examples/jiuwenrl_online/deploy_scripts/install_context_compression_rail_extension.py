#!/usr/bin/env python3
# coding: utf-8

"""Install a JiuwenClaw/JiuwenSwarm context-compression Rail extension.

The extension is installed into the same user-rail extension directory as the
online RL rail.  Keeping it as an extension lets agent-core enable context
compression for the online-RL stack without patching jiuwenswarm source.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


EXTENSION_NAME = "context_compression"
CLASS_NAME = "ContextCompressionExtensionRail"

RAIL_PY = '''# coding: utf-8
"""JiuwenClaw extension wrapper for agent-core context compression."""

from __future__ import annotations

import os
from typing import Any

from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
    RoundLevelCompressorConfig,
)
from openjiuwen.harness.rails.context_engineer import ContextProcessorRail
from openjiuwen.core.single_agent.rail.base import AgentRail


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


class ContextCompressionExtensionRail(AgentRail):
    """No-arg wrapper loaded by JiuwenClaw RailManager."""

    priority: int = 85

    def __init__(self) -> None:
        self._inner = None
        if not _env_bool("USE_CONTEXT_COMPRESSION_RAIL", "0"):
            return

        context_window_tokens = _env_int("JIUWENSWARM_CONTEXT_WINDOW_TOKENS", 16384)
        trigger_tokens = _env_int(
            "JIUWENSWARM_CONTEXT_COMPRESSION_TRIGGER_TOKENS",
            max(4096, context_window_tokens - 2048),
        )
        target_tokens = _env_int(
            "JIUWENSWARM_CONTEXT_COMPRESSION_TARGET_TOKENS",
            max(3072, context_window_tokens - 4096),
        )
        keep_recent = _env_int("JIUWENSWARM_CONTEXT_COMPRESSION_KEEP_RECENT_MESSAGES", 6)
        compression_call_max = _env_int(
            "JIUWENSWARM_CONTEXT_COMPRESSION_CALL_MAX_TOKENS",
            max(4096, context_window_tokens - 2048),
        )
        first_pass_target = _env_int("JIUWENSWARM_CONTEXT_COMPRESSION_FIRST_PASS_TARGET_TOKENS", 1800)
        second_pass_target = _env_int("JIUWENSWARM_CONTEXT_COMPRESSION_SECOND_PASS_TARGET_TOKENS", 1200)
        third_pass_target = _env_int("JIUWENSWARM_CONTEXT_COMPRESSION_THIRD_PASS_TARGET_TOKENS", 800)

        if target_tokens >= trigger_tokens:
            target_tokens = max(1024, trigger_tokens - 1024)

        self._inner = ContextProcessorRail(
            preset=False,
            processors=[
                (
                    "RoundLevelCompressor",
                    RoundLevelCompressorConfig(
                        trigger_total_tokens=trigger_tokens,
                        target_total_tokens=target_tokens,
                        keep_recent_messages=keep_recent,
                        compression_call_max_tokens=compression_call_max,
                        first_pass_target_tokens=first_pass_target,
                        second_pass_target_tokens=second_pass_target,
                        third_pass_target_tokens=third_pass_target,
                    ),
                )
            ],
        )

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
        help="Overwrite an existing context_compression extension rail.py.",
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
        "description": "agent-core context compression Rail for long JiuwenSwarm sessions",
        "priority": 85,
    }
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"installed={extension_dir}")
    print(f"config={config_path}")
    print("enable with USE_CONTEXT_COMPRESSION_RAIL=1")


if __name__ == "__main__":
    main()
