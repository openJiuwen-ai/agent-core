"""LSP subsystem package — Language Server Protocol support for AI agents."""

from __future__ import annotations

from typing import Any

__version__ = "0.1.10"

from openjiuwen.harness.lsp.types import CustomServerConfig, InitializeOptions, InitializeResult, LspStatus
from openjiuwen.harness.tools.lsp_tool._schemas import LspOperation
from openjiuwen.harness.lsp.core.manager import LSPServerManager
from openjiuwen.harness.lsp.core.utils.constants import MAX_LSP_FILE_SIZE_BYTES
from openjiuwen.harness.lsp.core.utils.git_ignore import filter_git_ignored_locations


async def initialize_lsp(
    options: InitializeOptions | None = None,
) -> InitializeResult:
    """
    Initialize the LSP subsystem (idempotent, lazy-loading hybrid mode).

    Call this once at application startup. Initialization only builds
    the configuration mapping; servers start lazily on first LSP request.
    """
    return await LSPServerManager.initialize(options)


async def shutdown_lsp() -> None:
    """
    Shutdown the LSP subsystem, stopping all server processes.

    Call this on application exit.
    """
    await LSPServerManager.shutdown()


def get_lsp_tool() -> dict[str, Any]:
    """
    Get the LSP Tool definition for AI Agent registration.

    Returns a dict with name, description, and input_schema suitable
    for passing to the agent framework's tool registration interface.
    """
    from openjiuwen.harness.tools.lsp_tool._tool import build_lsp_tool

    return build_lsp_tool()


def get_lsp_status() -> LspStatus:
    """Get the current LSP subsystem status."""
    instance = LSPServerManager.get_instance()
    if instance is None:
        return LspStatus(initialized=False, servers=[])
    servers = instance.get_status()
    return LspStatus(initialized=True, servers=servers)


__all__ = [
    "__version__",
    "get_lsp_status",
    "get_lsp_tool",
    "initialize_lsp",
    "shutdown_lsp",
    "CustomServerConfig",
    "InitializeOptions",
    "InitializeResult",
    "LspStatus",
    "LspOperation",
    "LSPServerManager",
    "MAX_LSP_FILE_SIZE_BYTES",
    "filter_git_ignored_locations",
]
