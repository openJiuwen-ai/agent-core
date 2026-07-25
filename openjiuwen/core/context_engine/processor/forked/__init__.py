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
}

__all__ = list(_EXPORT_MODULES)  # pylint: disable=undefined-all-variable


_ACTIVATED_PROCESSORS = (
    "MessageSummaryOffloader",
    "DialogueCompressor",
    "CurrentRoundCompressor",
    "RoundLevelCompressor",
)
_ACTIVATED = False


def activate() -> None:
    """Register the refactored processors under the official processor names."""
    global _ACTIVATED  # pylint: disable=global-statement
    for class_name in _ACTIVATED_PROCESSORS:
        module = import_module(_EXPORT_MODULES[class_name], __name__)
        processor_class = getattr(module, class_name)
        ContextEngine.register_processor()(processor_class)
    if not _ACTIVATED:
        _ACTIVATED = True
        logger.info(
            "forked context processors activated, overriding processor registry: %s",
            ", ".join(_ACTIVATED_PROCESSORS),
        )


def __getattr__(name: str):
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    activate()
    module = import_module(_EXPORT_MODULES[name], __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
