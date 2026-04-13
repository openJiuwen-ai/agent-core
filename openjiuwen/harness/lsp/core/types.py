"""Core types for the LSP subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpawnHandle:
    """LSP server process spawn parameters."""

    command: str
    """Executable command path."""
    args: list[str] = field(default_factory=list)
    """Command-line arguments."""
    env: dict[str, str] = field(default_factory=dict)
    """Additional environment variables."""
    initialization_options: dict[str, Any] | None = None
    """Options passed to the server in the initialize request."""
    startup_timeout: int = 45_000
    """Startup timeout in milliseconds."""


@dataclass
class ScopedLspServerConfig:
    """Complete configuration for a single LSP server instance."""

    server_id: str
    """Unique server identifier."""
    command: str
    """Executable command path."""
    workspace_folder: str
    """Workspace root directory for this server."""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    initialization_options: dict[str, Any] | None = None
    startup_timeout: int = 45_000
    extension_to_language: dict[str, str] = field(default_factory=dict)
    """File extension -> languageId mapping."""


@dataclass
class LspServerStatus:
    """Snapshot of a single server's running state."""

    server_id: str
    """Server unique identifier."""
    name: str
    """Human-readable server name."""
    running: bool
    """Whether the server is currently running."""
    root: str | None = None
    """Workspace root directory."""
