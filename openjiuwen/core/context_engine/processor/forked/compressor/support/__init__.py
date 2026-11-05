from typing import TYPE_CHECKING

from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
    CompressionExecutor,
    CompressionResult,
)

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.processor.forked.compressor.support.forked_agent import ForkedAgent

__all__ = [
    "CompressionExecutor",
    "CompressionResult",
    "ForkedAgent",
]


def __getattr__(name: str):
    # ReActAgent imports compression_executor while single_agent is initializing.
    # Loading ForkedAgent here eagerly would import that partially built package.
    if name == "ForkedAgent":
        from openjiuwen.core.context_engine.processor.forked.compressor.support.forked_agent import ForkedAgent

        globals()[name] = ForkedAgent
        return ForkedAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
