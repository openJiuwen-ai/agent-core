"""Builtin server registry and configuration builder."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from openjiuwen.harness.lsp.core.types import ScopedLspServerConfig
from openjiuwen.harness.lsp.servers.types import ServerDefinition

if TYPE_CHECKING:
    from openjiuwen.harness.lsp.types import InitializeOptions

# Global registry of all builtin servers
BUILTIN_SERVERS: dict[str, ServerDefinition] = {}


def nearest_root(
    include_patterns: list[str],
    exclude_patterns: list[str] | None = None,
    stop_dir: str | None = None,
) -> Callable[[str], Awaitable[str | None] | str | None]:
    """
    Generate a NearestRoot function.

    Returns a callable that, given a file path, traverses upward from its
    parent directory looking for the nearest directory containing any of the
    include_patterns (file or directory names). Stops at stop_dir.
    Uses asyncio.to_thread() to avoid blocking the event loop.
    """

    async def find_root(file_path: str) -> str | None:
        def _sync_find() -> str | None:
            try:
                start_dir = Path(file_path).parent.resolve()
            except Exception:
                return None
            stop = (Path(stop_dir) if stop_dir else Path.cwd()).resolve()

            # Always check the starting directory first, regardless of stop_dir.
            # This ensures we find project roots even when:
            # 1. The file is directly in the CWD (where start_dir == stop)
            # 2. The starting directory contains exclude_patterns like .git
            #    (project roots typically ARE in .git directories)
            for pattern in include_patterns:
                if (start_dir / pattern).exists():
                    return str(start_dir)

            # Then traverse upward, applying exclude_patterns as intended
            # (stop before entering directories with .git, etc.)
            current = start_dir.parent
            while current.parent != current:
                for pattern in include_patterns:
                    if (current / pattern).exists():
                        return str(current)

                if current == stop:
                    return None

                if exclude_patterns:
                    for pattern in exclude_patterns:
                        if (current / pattern).exists():
                            return None

                current = current.parent

            return None

        return await asyncio.to_thread(_sync_find)

    return find_root


async def build_configs_async(
    options: "InitializeOptions",
    cwd: str,
) -> list[ScopedLspServerConfig]:
    """
    Build the initial server configuration list (lazy-loading semantics).

    Does NOT pre-scan root directories; actual roots are determined dynamically
    by get_or_start_server on each request. This ensures every sub-project
    in a monorepo finds its own root correctly.
    """
    configs: list[ScopedLspServerConfig] = []

    for server_id, server_def in BUILTIN_SERVERS.items():
        spawn_result = server_def.spawn(cwd)
        if asyncio.iscoroutine(spawn_result):
            spawn_handle = await spawn_result
        else:
            spawn_handle = spawn_result

        if spawn_handle is None:
            # Server binary not found — keep placeholder config
            configs.append(
                ScopedLspServerConfig(
                    server_id=server_id,
                    command="",
                    args=[],
                    env={},
                    workspace_folder=cwd,
                    initialization_options=None,
                    startup_timeout=45_000,
                    extension_to_language={
                        ext: server_def.language_id for ext in server_def.extensions
                    },
                )
            )
            continue

        configs.append(
            ScopedLspServerConfig(
                server_id=server_id,
                command=spawn_handle.command,
                args=spawn_handle.args,
                env=spawn_handle.env,
                workspace_folder=cwd,
                initialization_options=spawn_handle.initialization_options,
                startup_timeout=spawn_handle.startup_timeout,
                extension_to_language={
                    ext: server_def.language_id for ext in server_def.extensions
                },
            )
        )

    if options.custom_servers:
        for server_id, custom in options.custom_servers.items():
            if custom.disabled:
                configs = [c for c in configs if c.server_id != server_id]
                continue

            existing = next((c for c in configs if c.server_id == server_id), None)
            if existing:
                if custom.command:
                    existing.command = custom.command
                if custom.args is not None:
                    existing.args = custom.args
                if custom.initialization_options is not None:
                    existing.initialization_options = custom.initialization_options
            else:
                configs.append(
                    ScopedLspServerConfig(
                        server_id=server_id,
                        command=custom.command or "",
                        args=custom.args or [],
                        env=custom.env or {},
                        workspace_folder=cwd,
                        initialization_options=custom.initialization_options,
                        extension_to_language={
                            ext: custom.language_id or server_id
                            for ext in (custom.extensions or [])
                        },
                    )
                )

    return configs


def build_configs(
    options: "InitializeOptions",
    cwd: str,
) -> list[ScopedLspServerConfig]:
    """Synchronous wrapper for build_configs_async."""
    return asyncio.run(build_configs_async(options, cwd))
