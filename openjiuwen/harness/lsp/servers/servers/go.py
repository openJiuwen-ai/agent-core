"""Go LSP server definition (gopls)."""

from __future__ import annotations

import shutil

from openjiuwen.harness.lsp.core.types import SpawnHandle
from openjiuwen.harness.lsp.servers.registry import BUILTIN_SERVERS, nearest_root
from openjiuwen.harness.lsp.servers.types import ServerDefinition


async def go_root(file_path: str) -> str | None:
    """Find Go project root, supporting go.work multi-module workspaces."""
    work_result = await nearest_root(["go.work"])(file_path)
    if work_result:
        return work_result
    return await nearest_root(["go.mod", "go.sum"])(file_path)


def _spawn_go(root: str) -> SpawnHandle | None:
    cmd = "gopls"
    if not shutil.which(cmd):
        return None

    return SpawnHandle(
        command=cmd,
        args=[],
        initialization_options={"staticcheck": True},
        startup_timeout=60_000,
    )


go_server = ServerDefinition(
    id="gopls",
    extensions=[".go"],
    language_id="go",
    priority=10,
    find_root=go_root,
    spawn=_spawn_go,
)

BUILTIN_SERVERS[go_server.id] = go_server
