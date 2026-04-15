"""Core types for the LSP subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LspServerState(Enum):
    """Lifecycle states of a single LSP server instance.

    State transitions::

        STOPPED ──start()──▶ STARTING ──success──▶ RUNNING
           ^                      │                    │
           │                      └──timeout/error──▶ ERROR ──┤
           │                              [crash]             │
           │                                           start()│ (crash recovery)
           │                                                  │
      stop <──────────────────────────────────────────────────┘

    Notes:
    - ERROR → STARTING only occurs when start() is called again with crash_count
      below the recovery threshold (MAX_CRASH_RECOVERY_ATTEMPTS).
    - RUNNING → ERROR is triggered internally when _read_loop detects the
      subprocess has exited (server crash), not by an explicit transition.
    - All transitions ultimately lead back to STOPPED via stop().
    """

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


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
    """Whether the server is currently running (kept for backward compatibility)."""
    state: LspServerState = LspServerState.STOPPED
    """Current lifecycle state of the server."""
    root: str | None = None
    """Workspace root directory."""
    crash_count: int = 0
    """Number of times this server has crashed (for crash-recovery decisions)."""
    last_error: str | None = None
    """Last error message if the server is in ERROR state."""
