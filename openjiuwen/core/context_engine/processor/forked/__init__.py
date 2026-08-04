"""Refactored context processors with explicit same-name registration."""
# Names in __all__ are exported lazily via __getattr__, which pylint cannot see.
# pylint: disable=undefined-all-variable

from importlib import import_module

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine import ContextEngine

_EXPORT_MODULES = {
    "CurrentRoundCompressor": ".compressor.current_round_compressor",
    "CurrentRoundCompressorConfig": ".compressor.current_round_compressor",
    "DialogueCompressor": ".compressor.dialogue_compressor",
    "DialogueCompressorConfig": ".compressor.dialogue_compressor",
    "MessageSummaryOffloader": ".offloader.message_offloader",
    "MessageSummaryOffloaderConfig": ".offloader.message_offloader",
    "RoundLevelCompressor": ".compressor.round_level_compressor",
    "RoundLevelCompressorConfig": ".compressor.round_level_compressor",
    "SessionMemoryAgent": ".compressor.session_memory_agent",
    "SessionMemoryAgentConfig": ".compressor.session_memory_agent",
    "SessionMemoryAbilityManager": ".compressor.session_memory_agent",
    "SessionMemoryCompressor": ".compressor.session_memory_compressor",
    "SessionMemoryCompressorConfig": ".compressor.session_memory_compressor",
}

__all__ = list(_EXPORT_MODULES)  # pylint: disable=undefined-all-variable


_ACTIVATED_PROCESSORS = (
    "MessageSummaryOffloader",
    "DialogueCompressor",
    "CurrentRoundCompressor",
    "RoundLevelCompressor",
    "SessionMemoryCompressor",
)
_ACTIVATED = False
_ORIGINAL_PROCESSORS = {}


def activate() -> None:
    """Register the refactored processors under the official processor names."""
    # Designated internal facade for the processor registry.
    # pylint: disable=protected-access
    global _ACTIVATED  # pylint: disable=global-statement
    for class_name in _ACTIVATED_PROCESSORS:
        if class_name not in _ORIGINAL_PROCESSORS:
            _ORIGINAL_PROCESSORS[class_name] = ContextEngine._PROCESSOR_MAP.get(class_name)
        module = import_module(_EXPORT_MODULES[class_name], __name__)
        processor_class = getattr(module, class_name)
        ContextEngine.register_processor()(processor_class)
    if not _ACTIVATED:
        _ACTIVATED = True
        logger.info(
            "forked context processors activated, overriding processor registry: %s",
            ", ".join(_ACTIVATED_PROCESSORS),
        )


def deactivate() -> None:
    """Restore the processor registry entries captured before the first activation."""
    # Designated internal facade for the processor registry.
    # pylint: disable=protected-access
    global _ACTIVATED  # pylint: disable=global-statement
    for class_name, original in _ORIGINAL_PROCESSORS.items():
        if original is None:
            ContextEngine._PROCESSOR_MAP.pop(class_name, None)
        else:
            ContextEngine._PROCESSOR_MAP[class_name] = original
    _ACTIVATED = False


def __getattr__(name: str):
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    activate()
    module = import_module(_EXPORT_MODULES[name], __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
