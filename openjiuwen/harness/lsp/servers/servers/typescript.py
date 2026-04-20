"""TypeScript/JavaScript LSP server definition."""

from __future__ import annotations

import shutil
from pathlib import Path

from openjiuwen.harness.lsp.core.types import SpawnHandle
from openjiuwen.harness.lsp.servers.registry import BUILTIN_SERVERS, nearest_root
from openjiuwen.harness.lsp.servers.types import ServerDefinition


def _spawn_ts(root: str) -> SpawnHandle | None:
    cmd = "typescript-language-server"
    if not shutil.which(cmd):
        return None

    args = ["--stdio"]
    has_config = (
        Path(root, "tsconfig.json").exists()
        or Path(root, "jsconfig.json").exists()
    )
    if not has_config:
        args.append("--ignore-node-modules")

    return SpawnHandle(
        command=cmd,
        args=args,
        initialization_options=None,
    )


typescript_server = ServerDefinition(
    id="typescript",
    extensions=[".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"],
    language_id="typescript",
    priority=10,
    find_root=nearest_root(
        include_patterns=[
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
        ],
        exclude_patterns=["deno.json", "deno.jsonc"],
    ),
    spawn=_spawn_ts,
)

BUILTIN_SERVERS[typescript_server.id] = typescript_server
