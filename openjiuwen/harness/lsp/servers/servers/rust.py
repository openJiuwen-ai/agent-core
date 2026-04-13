"""Rust LSP server definition (rust-analyzer)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from openjiuwen.harness.lsp.core.types import SpawnHandle
from openjiuwen.harness.lsp.servers.registry import BUILTIN_SERVERS
from openjiuwen.harness.lsp.servers.types import ServerDefinition

logger = logging.getLogger(__name__)


async def rust_root(file_path: str) -> str | None:
    """
    Find Rust project root, supporting Cargo workspaces.

    1. Find the nearest Cargo.toml
    2. Read it; if it contains [workspace], that directory is the workspace root
    3. If not, continue upward to find a parent with [workspace]
    """
    def _sync_find() -> str | None:
        start = Path(file_path).parent.resolve()
        root: str | None = None

        current = start
        stop = Path(file_path).parent
        while current != stop and current.parent != current:
            if (current / "Cargo.toml").exists():
                root = str(current)
                break
            current = current.parent

        if not root:
            return None

        cargo_toml_path = Path(root) / "Cargo.toml"
        try:
            content = cargo_toml_path.read_text()
        except OSError:
            return root

        if "[workspace]" in content:
            return root

        current = Path(root)
        while current.parent != current:
            parent_cargo = current.parent / "Cargo.toml"
            if parent_cargo.exists():
                try:
                    parent_content = parent_cargo.read_text()
                    if "[workspace]" in parent_content:
                        return str(current.parent)
                except OSError:
                    logger.debug("Could not read parent Cargo.toml at %s", parent_cargo)
            current = current.parent

        return root

    return await asyncio.to_thread(_sync_find)


def _spawn_rust(root: str) -> SpawnHandle | None:
    cmd = "rust-analyzer"
    if not shutil.which(cmd):
        return None

    return SpawnHandle(
        command=cmd,
        args=[],
    )


rust_server = ServerDefinition(
    id="rust",
    extensions=[".rs"],
    language_id="rust",
    priority=10,
    find_root=rust_root,
    spawn=_spawn_rust,
)

BUILTIN_SERVERS[rust_server.id] = rust_server
