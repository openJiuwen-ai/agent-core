"""Builtin server definitions."""
from openjiuwen.harness.lsp.servers.registry import BUILTIN_SERVERS

# Import all builtin servers to register them
from openjiuwen.harness.lsp.servers.servers import rust, typescript, java, python
from openjiuwen.harness.lsp.servers.servers import go

__all__ = ["BUILTIN_SERVERS"]
