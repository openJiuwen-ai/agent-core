import pytest

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.compressor.support.compression_executor import (
    CompressionExecutor,
)
from openjiuwen.core.context_engine.processor.compressor.current_round_compressor import (
    CurrentRoundCompressor,
)
from openjiuwen.core.context_engine.processor.legacy.compressor import (
    FullCompactProcessor,
    LegacyDialogueCompressor,
    MicroCompactProcessor,
)
from openjiuwen.core.context_engine.processor.legacy.offloader import (
    MessageSummaryOffloader,
    ToolResultBudgetProcessor,
)
from openjiuwen.core.context_engine.processor.legacy.offloader.message_offloader import (
    MessageOffloader as LegacyMessageOffloader,
    MessageOffloaderConfig as LegacyMessageOffloaderConfig,
)
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloader as CurrentMessageOffloader,
)
from openjiuwen.core.context_engine.processor.offloader.rule_compression.compressors.diff_compressor import (
    DiffCompressor,
)
from openjiuwen.core.context_engine.processor.offloader.rule_compression.pipeline import (
    RuleCompressionPipeline,
)
from openjiuwen.core.foundation.llm import ToolMessage


class _LegacyOffloadContext:
    def __init__(self):
        self.saved_messages = {}

    def session_id(self):
        return "legacy-test"

    def workspace_dir(self):
        return ""

    def offload_messages(self, handle, messages):
        self.saved_messages[handle] = messages


def test_processor_canonical_imports_are_available():
    assert CurrentRoundCompressor.__name__ == "CurrentRoundCompressor"
    assert CompressionExecutor.__name__ == "CompressionExecutor"
    assert RuleCompressionPipeline.__name__ == "RuleCompressionPipeline"
    assert DiffCompressor.__name__ == "DiffCompressor"


def test_legacy_processor_imports_are_available_from_new_and_old_paths():
    assert FullCompactProcessor.__name__ == "FullCompactProcessor"
    assert MicroCompactProcessor.__name__ == "MicroCompactProcessor"
    assert MessageSummaryOffloader.__name__ == "MessageSummaryOffloader"
    assert ToolResultBudgetProcessor.__name__ == "ToolResultBudgetProcessor"
    assert LegacyDialogueCompressor.__name__ == "LegacyDialogueCompressor"


@pytest.mark.asyncio
async def test_legacy_message_offloader_stays_unregistered_and_keeps_old_memory_fallback():
    assert ContextEngine._PROCESSOR_MAP["MessageOffloader"] is CurrentMessageOffloader
    assert LegacyMessageOffloader is not CurrentMessageOffloader

    context = _LegacyOffloadContext()
    message = ToolMessage(content="legacy payload", tool_call_id="legacy-call")
    result = await LegacyMessageOffloader(LegacyMessageOffloaderConfig()).offload_messages(
        role="tool",
        content="legacy preview",
        messages=[message],
        context=context,
        tool_call_id=message.tool_call_id,
    )

    assert result is not None
    assert result.offload_type == "in_memory"
    assert result.content.endswith("[[OFFLOAD: handle=" + result.offload_handle + ", type=in_memory]]")
    assert not result.content.startswith("\n[[OFFLOAD")
    assert context.saved_messages[result.offload_handle] == [message]
