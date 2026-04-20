"""Agent construction: configuration and factory."""

from openjiuwen.harness.cli.agent.config import (
    CLIConfig,
    load_config,
)

# Factory imports are lazy to avoid heavy SDK imports at
# module load time.  They are re-exported here for convenience
# (``from openjiuwen.harness.cli.agent import create_backend``).


def __getattr__(name: str):  # noqa: ANN204
    """Lazy-load factory symbols on first access."""
    _factory_names = {
        "AgentBackend",
        "LocalBackend",
        "create_agent",
        "create_backend",
    }
    if name in _factory_names:
        from openjiuwen.harness.cli.agent import factory as _f

        return getattr(_f, name)
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )


__all__ = [
    "CLIConfig",
    "load_config",
    "AgentBackend",
    "LocalBackend",
    "create_agent",
    "create_backend",
]
