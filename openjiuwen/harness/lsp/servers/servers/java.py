"""Java LSP server definition (Eclipse JDT Language Server)."""

from __future__ import annotations

import shutil

from openjiuwen.harness.lsp.core.types import SpawnHandle
from openjiuwen.harness.lsp.servers.registry import BUILTIN_SERVERS, nearest_root
from openjiuwen.harness.lsp.servers.types import ServerDefinition


def _spawn_java(root: str) -> SpawnHandle | None:
    cmd = shutil.which("jdtls")
    if not cmd:
        return None

    return SpawnHandle(
        command=cmd,
        args=[],
        initialization_options=None,
    )


java_server = ServerDefinition(
    id="jdtls",
    extensions=[".java"],
    language_id="java",
    priority=10,
    find_root=nearest_root(
        include_patterns=[
            "pom.xml",  # Maven
            "build.gradle",  # Gradle (Groovy DSL)
            "build.gradle.kts",  # Gradle (Kotlin DSL)
            ".project",  # Eclipse
        ],
        exclude_patterns=[".git"],
    ),
    spawn=_spawn_java,
)

BUILTIN_SERVERS[java_server.id] = java_server
