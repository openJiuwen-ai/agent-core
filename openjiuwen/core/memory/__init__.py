"""Memory module for managing agent memory and vector stores.

This module provides memory-related configurations and implementations,
including long-term memory management and vector store backends.
"""

from typing import TYPE_CHECKING

from openjiuwen.core.memory.config import (
    AgentMemoryConfig,
    MemoryEngineConfig,
    MemoryScopeConfig,
)
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.memory.store.impl.memory_milvus_vector_store import MemoryMilvusVectorStore

if TYPE_CHECKING:
    from openjiuwen.core.memory.store.impl.memory_chroma_vector_store import MemoryChromaVectorStore

# Lazy import mapping for optional dependencies
_LAZY_IMPORTS = {
    "MemoryChromaVectorStore": ("openjiuwen.core.memory.store.impl.memory_chroma_vector_store",
                                "MemoryChromaVectorStore"),
}

# Installation hints for missing optional dependencies
_INSTALL_HINTS = {
    "MemoryChromaVectorStore": "uv sync --extra chromadb"
}


def __getattr__(name: str):
    """Lazy loading for module-level attributes.

    Implements lazy import for optional dependencies to avoid import errors
    when optional packages are not installed.

    Args:
        name: Name of the attribute to load.

    Returns:
        The requested attribute from the lazy import.

    Raises:
        ImportError: If the required module cannot be imported and provides
            installation hints.
        AttributeError: If the attribute is not found in lazy imports.
    """
    if name in _LAZY_IMPORTS:
        module_name, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib
            module = importlib.import_module(module_name, __package__)
            attr = getattr(module, attr_name)
            # Cache to module namespace for direct access on subsequent calls
            globals()[name] = attr
            return attr
        except ImportError as e:
            hint = _INSTALL_HINTS.get(name, "")
            raise ImportError(
                f"'{name}' requires additional dependencies. {hint}"
            ) from e

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """Support dir() and IDE autocompletion.

    Returns:
        List of all available module attributes, including lazy-loaded ones.
    """
    return list(globals().keys()) + list(_LAZY_IMPORTS.keys())


__all__ = [
    'MemoryEngineConfig',
    'MemoryScopeConfig',
    'AgentMemoryConfig',
    'MemoryMilvusVectorStore',
    'MemoryChromaVectorStore',
    'LongTermMemory'
]
