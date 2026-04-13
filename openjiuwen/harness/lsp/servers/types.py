"""Types for the builtin server registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from openjiuwen.harness.lsp.core.types import SpawnHandle


@dataclass
class ServerDefinition:
    """
    Definition for a single builtin LSP server.

    Attributes:
        id: Unique server identifier.
        extensions: Supported file extensions (including dot).
        language_id: LSP languageId.
        priority: Priority (lower value = higher priority).
        global_server: Whether this is a global server.
        find_root: Given a file path, returns the workspace root (sync or async).
        spawn: Given a root, returns SpawnHandle or None (sync or async).
    """

    id: str
    extensions: list[str]
    language_id: str
    priority: int = 100
    global_server: bool = False
    find_root: Callable[[str], str | None] = ...
    spawn: Callable[[str], SpawnHandle | None] = ...
