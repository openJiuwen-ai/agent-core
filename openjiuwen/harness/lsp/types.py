"""API types for the LSP subsystem initialization and status."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.harness.lsp.core.types import LspServerStatus


@dataclass
class InitializeOptions:
    """Configuration options for LSP subsystem initialization."""

    cwd: str | None = None
    custom_servers: dict[str, "CustomServerConfig"] | None = None


@dataclass
class CustomServerConfig:
    """User-supplied server configuration to override or extend builtins."""

    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    extensions: list[str] | None = None
    language_id: str | None = None
    initialization_options: dict | None = None
    disabled: bool = False


@dataclass
class InitializeResult:
    """Result of LSP subsystem initialization."""

    success: bool
    servers_loaded: int
    duration_ms: float = 0.0


@dataclass
class LspStatus:
    """Overall LSP subsystem status."""

    initialized: bool
    servers: list["LspServerStatus"] = field(default_factory=list)
