"""Bootstrap OpenJiuwen checkpointer for durable Experiment Design context."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import project_root

_BOOTSTRAPPED = False
_CURRENT_CONFIG: dict[str, Any] | None = None


def reset_checkpoint_bootstrap_state() -> None:
    """Test helper: clear the one-time bootstrap guard."""
    global _BOOTSTRAPPED, _CURRENT_CONFIG
    _BOOTSTRAPPED = False
    _CURRENT_CONFIG = None


def is_checkpoint_bootstrapped() -> bool:
    return _BOOTSTRAPPED


def resolve_checkpointer_db_path(db_path: str | Path, *, root: Path | None = None) -> Path:
    """Resolve a configured checkpointer path under the project root."""
    base = (root or project_root()).resolve()
    raw = Path(db_path)
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        resolved = (base / raw).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"checkpointer db_path must stay under project root: {db_path!r}"
        ) from exc
    return resolved


async def bootstrap_checkpointer(
    config: dict[str, Any] | None = None,
    *,
    force: bool = False,
    root: Path | None = None,
) -> Any:
    """Create and register the configured OpenJiuwen checkpointer once.

    Default MVP backend is the built-in ``persistence`` SQLite checkpointer.
    Pass ``{"type": "in_memory"}`` in tests to avoid touching developer state.
    """
    global _BOOTSTRAPPED, _CURRENT_CONFIG

    from openjiuwen.core.session.checkpointer.checkpointer import (
        CheckpointerConfig,
        CheckpointerFactory,
        default_inmemory_checkpointer,
    )

    conf = dict(config or {})
    store_type = conf.get("type", "persistence")

    if _BOOTSTRAPPED and not force:
        return CheckpointerFactory.get_checkpointer()

    if store_type == "in_memory":
        CheckpointerFactory.set_default_checkpointer(default_inmemory_checkpointer)
        _BOOTSTRAPPED = True
        _CURRENT_CONFIG = {"type": "in_memory"}
        return default_inmemory_checkpointer

    if store_type != "persistence":
        raise ValueError(
            f"unsupported checkpointer type {store_type!r}; "
            "MVP supports 'persistence' or 'in_memory'"
        )

    db_type = conf.get("db_type", "sqlite")
    db_path = conf.get("db_path", "data/interim/checkpoints/experiment_design.db")
    resolved = resolve_checkpointer_db_path(db_path, root=root)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    provider_conf = {
        "db_type": db_type,
        "db_path": str(resolved),
        "db_enable_wal": conf.get("db_enable_wal", True),
    }
    if "db_timeout" in conf:
        provider_conf["db_timeout"] = conf["db_timeout"]

    try:
        checkpointer = await CheckpointerFactory.create(
            CheckpointerConfig(type="persistence", conf=provider_conf)
        )
    except Exception as exc:
        raise RuntimeError(
            f"failed to initialize persistence checkpointer at {resolved}: {exc}"
        ) from exc

    CheckpointerFactory.set_default_checkpointer(checkpointer)
    _BOOTSTRAPPED = True
    _CURRENT_CONFIG = {
        "type": "persistence",
        "db_type": db_type,
        "db_path": str(resolved),
        "db_enable_wal": provider_conf["db_enable_wal"],
    }
    return checkpointer


def current_checkpointer_config() -> dict[str, Any] | None:
    return None if _CURRENT_CONFIG is None else dict(_CURRENT_CONFIG)
