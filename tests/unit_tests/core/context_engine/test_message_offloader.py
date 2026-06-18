# coding: utf-8

import glob
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloaderConfig,
)
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


async def create_context(
    config: MessageOffloaderConfig | None = None,
    *,
    context_window_tokens: int = 100,
    default_window_message_num: int = 100,
    workspace=None,
):
    engine = ContextEngine(
        ContextEngineConfig(
            context_window_tokens=context_window_tokens,
            default_window_message_num=default_window_message_num,
        ),
        workspace=workspace,
    )
    return await engine.create_context(
        "test_ctx",
        processors=[("MessageOffloader", config or MessageOffloaderConfig())],
    )


def _tool_call(call_id: str, name: str, arguments: str = "{}") -> AssistantMessage:
    return AssistantMessage(
        content="calling tool",
        tool_calls=[
            ToolCall(
                id=call_id,
                name=name,
                type="function",
                arguments=arguments,
            )
        ],
    )


class _Workspace:
    def __init__(self, root_path: str):
        self.root_path = root_path


def _large_diff() -> str:
    chunks = [
        "diff --git a/auth.py b/auth.py",
        "--- a/auth.py",
        "+++ b/auth.py",
        "@@ -1,80 +1,80 @@",
    ]
    for index in range(80):
        if index == 40:
            chunks.append("-old_token = get_legacy_token()")
            chunks.append("+new_token = get_secure_token()")
        else:
            chunks.append(f" context line {index} with enough padding to exceed thresholds")
    return "\n".join(chunks)


class TestMessageOffloaderConfig:
    def test_only_exposes_ttl_and_protected_tools(self):
        assert set(MessageOffloaderConfig.model_fields) == {
            "enable_rule_compression",
            "add_message_threshold_ratio",
            "ttl_context_occupancy_ratio",
            "ttl_message_threshold_ratio",
            "offload_preview_head_tail_chars",
            "ttl_seconds",
            "protected_tool_names",
        }

    @pytest.mark.parametrize(
        "removed_field",
        [
            "messages_threshold",
            "tokens_threshold",
            "large_message_threshold",
            "trim_size",
            "messages_to_keep",
            "keep_last_round",
            "offload_message_type",
            "rule_compression_ratio",
            "rule_compression_expired_ratio",
            "rule_compression_context_window_tokens",
            "rule_compression_ttl_keep_recent_messages",
            "rule_truncate_head_tokens",
            "rule_truncate_tail_tokens",
        ],
    )
    def test_rejects_removed_configuration(self, removed_field):
        with pytest.raises(ValidationError):
            MessageOffloaderConfig(**{removed_field: 1})


class TestMessageOffloaderAddTrigger:
    @pytest.mark.asyncio
    async def test_does_not_trigger_at_exactly_twenty_percent_character_capacity(self):
        context = await create_context(context_window_tokens=100)

        await context.add_messages(ToolMessage(content="x" * 60, tool_call_id="tc-boundary"))

        message = context.get_messages()[0]
        assert message.content == "x" * 60
        assert not isinstance(message, OffloadMixin)

    @pytest.mark.asyncio
    async def test_triggers_above_twenty_percent_character_capacity(self):
        context = await create_context(context_window_tokens=100)

        await context.add_messages(ToolMessage(content="x" * 61, tool_call_id="tc-large"))

        message = context.get_messages()[0]
        assert isinstance(message, OffloadMixin)
        reloaded = await context.reloader_tool().invoke(
            {
                "offload_handle": message.offload_handle,
                "offload_type": message.offload_type,
            }
        )
        assert "x" * 61 in reloaded

    @pytest.mark.asyncio
    async def test_rule_compression_offloads_original_message(self):
        context = await create_context(context_window_tokens=100)
        content = "\n".join(["same line"] * 20)

        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-repeat"))

        message = context.get_messages()[0]
        assert isinstance(message, OffloadMixin)
        assert "[[OFFLOAD:" in message.content
        assert message.content.rstrip().endswith("]]")
        assert message.metadata["rule_compression_pass"] == "add"
        reloaded = await context.reloader_tool().invoke(
            {
                "offload_handle": message.offload_handle,
                "offload_type": message.offload_type,
            }
        )
        assert "same line" in reloaded
        assert context.save_state()["offload_messages"][message.offload_handle][0].content == content

    @pytest.mark.asyncio
    async def test_rule_compression_writes_original_to_filesystem_and_preserves_path(
        self,
        tmp_path,
    ):
        workspace = SimpleNamespace(root_path=str(tmp_path))
        context = await create_context(context_window_tokens=500, workspace=workspace)
        content = "\n".join(["same line"] * 100)

        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-repeat"))

        message = context.get_messages()[0]
        assert isinstance(message, OffloadMixin)
        assert message.offload_type == "filesystem"
        assert "same line" in message.content
        assert "[[OFFLOAD: type=filesystem, path=" in message.content
        assert message.content.rstrip().endswith("]]")

        offload_path = tmp_path / "context" / "default_session_id_context" / "offload"
        files = list(offload_path.glob("MessageOffloader_*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["messages"][0]["content"] == content

    @pytest.mark.asyncio
    async def test_rule_compression_truncates_without_dropping_offload_marker(self, tmp_path):
        workspace = SimpleNamespace(root_path=str(tmp_path))
        context = await create_context(context_window_tokens=100, workspace=workspace)
        content = "\n".join(["same line"] * 100)

        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-repeat"))

        message = context.get_messages()[0]
        assert isinstance(message, OffloadMixin)
        assert "[[OFFLOAD: type=filesystem, path=" in message.content
        assert message.content.rstrip().endswith("]]")
        assert str(tmp_path) in message.content

    @pytest.mark.asyncio
    async def test_diff_rule_compression_offloads_original_to_existing_filesystem_store(self, tmp_path):
        workspace = _Workspace(str(tmp_path / "workspace"))
        context = await create_context(
            context_window_tokens=1000,
            workspace=workspace,
        )
        original = _large_diff()

        await context.add_messages(
            [
                UserMessage(content="please inspect auth token security"),
                ToolMessage(content=original, tool_call_id="tc-diff"),
            ]
        )

        message = context.get_messages()[1]
        assert isinstance(message, OffloadMixin)
        assert "diff --git a/auth.py b/auth.py" in message.content
        assert "+new_token = get_secure_token()" in message.content
        assert "Retrieve full diff" in message.content
        assert message.offload_type == "filesystem"

        paths = glob.glob(
            os.path.join(
                workspace.root_path,
                "context",
                f"{context.session_id()}_context",
                "offload",
                f"*_{message.offload_handle}.json",
            )
        )
        assert paths
        with open(paths[0], encoding="utf-8") as handle:
            payload = json.load(handle)
        assert payload["messages"][0]["content"] == original

    @pytest.mark.asyncio
    async def test_non_tool_messages_do_not_trigger_offload(self):
        context = await create_context(context_window_tokens=100)

        await context.add_messages(UserMessage(content="u" * 300))

        message = context.get_messages()[0]
        assert message.content == "u" * 300
        assert not isinstance(message, OffloadMixin)

    @pytest.mark.asyncio
    async def test_protected_tool_is_not_processed(self):
        context = await create_context(context_window_tokens=100)
        await context.add_messages(
            [
                _tool_call("tc-reload", "reload_original_context_messages"),
                ToolMessage(content="x" * 100, tool_call_id="tc-reload"),
            ]
        )

        message = context.get_messages()[1]
        assert message.content == "x" * 100
        assert not isinstance(message, OffloadMixin)

    @pytest.mark.asyncio
    async def test_add_threshold_ratio_is_configurable(self):
        context = await create_context(
            MessageOffloaderConfig(add_message_threshold_ratio=0.5),
            context_window_tokens=100,
        )

        await context.add_messages(ToolMessage(content="x" * 100, tool_call_id="tc-large"))

        message = context.get_messages()[0]
        assert message.content == "x" * 100
        assert not isinstance(message, OffloadMixin)

    @pytest.mark.asyncio
    async def test_add_without_rule_compression_offloads_head_and_tail_preview(self):
        context = await create_context(
            MessageOffloaderConfig(
                enable_rule_compression=False,
                offload_preview_head_tail_chars=2000,
            ),
            context_window_tokens=100,
        )
        content = "h" * 2100 + "middle" + "t" * 2100

        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-direct"))

        message = context.get_messages()[0]
        assert isinstance(message, OffloadMixin)
        assert message.content.startswith("h" * 2000)
        assert "[Content truncated and offloaded." in message.content
        assert "middle" not in message.content
        assert f"{'t' * 2000}[[OFFLOAD: handle={message.offload_handle}, type=in_memory]]" in message.content
        reloaded = await context.reloader_tool().invoke(
            {
                "offload_handle": message.offload_handle,
                "offload_type": message.offload_type,
            }
        )
        assert content in reloaded


class TestMessageOffloaderTtl:
    @pytest.mark.asyncio
    async def test_ttl_requires_idle_timeout_and_half_context_occupancy(self):
        context = await create_context(
            MessageOffloaderConfig(ttl_seconds=10),
            context_window_tokens=50002,
        )
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        messages = [
            ToolMessage(content=character * 30001, tool_call_id=f"tc-{character}")
            for character in ("a", "b", "c")
        ]
        await context.add_messages(messages)

        await context.get_context_window()
        processor._rule_pipeline._time_func = MagicMock(return_value=105.0)  # type: ignore[attr-defined]
        await context.get_context_window()
        assert all(not message.metadata.get("rule_compressed_at") for message in context.get_messages())

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)  # type: ignore[attr-defined]
        await context.get_context_window()

        assert all(isinstance(message, OffloadMixin) for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_ttl_skips_messages_at_or_below_message_threshold_ratio(self):
        context = await create_context(
            MessageOffloaderConfig(ttl_seconds=10),
            context_window_tokens=50002,
        )
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        await context.add_messages(
            [
                ToolMessage(content=character * 15000, tool_call_id=f"tc-{character}")
                for character in ("a", "b", "c", "d", "e", "f")
            ]
        )
        await context.get_context_window()
        router_compress = processor._rule_pipeline._router.compress  # type: ignore[attr-defined]
        processor._rule_pipeline._router.compress = MagicMock(wraps=router_compress)  # type: ignore[attr-defined]

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)  # type: ignore[attr-defined]
        await context.get_context_window()

        assert all(not message.metadata.get("rule_compressed_at") for message in context.get_messages())
        assert all(not isinstance(message, OffloadMixin) for message in context.get_messages())
        processor._rule_pipeline._router.compress.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_ttl_skips_context_below_half_capacity(self):
        context = await create_context(MessageOffloaderConfig(ttl_seconds=10))
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        await context.add_messages(
            [
                ToolMessage(content="a" * 40, tool_call_id="tc-a"),
                ToolMessage(content="b" * 40, tool_call_id="tc-b"),
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)  # type: ignore[attr-defined]
        await context.get_context_window()

        assert all(not message.metadata.get("rule_compressed_at") for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_ttl_context_occupancy_ratio_is_configurable(self):
        context = await create_context(
            MessageOffloaderConfig(
                ttl_seconds=10,
                add_message_threshold_ratio=1.0,
                ttl_context_occupancy_ratio=0.8,
                ttl_message_threshold_ratio=0.25,
            ),
            context_window_tokens=100,
        )
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        await context.add_messages(
            [
                ToolMessage(content="a" * 100, tool_call_id="tc-a"),
                ToolMessage(content="b" * 100, tool_call_id="tc-b"),
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)  # type: ignore[attr-defined]
        await context.get_context_window()

        assert all(not isinstance(message, OffloadMixin) for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_ttl_message_threshold_ratio_is_configurable(self):
        context = await create_context(
            MessageOffloaderConfig(
                ttl_seconds=10,
                add_message_threshold_ratio=1.0,
                ttl_context_occupancy_ratio=0.1,
                ttl_message_threshold_ratio=0.25,
            ),
            context_window_tokens=100,
        )
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        content = "x" * 100
        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-ttl"))
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)  # type: ignore[attr-defined]
        await context.get_context_window()

        message = context.get_messages()[0]
        assert isinstance(message, OffloadMixin)
        reloaded = await context.reloader_tool().invoke(
            {
                "offload_handle": message.offload_handle,
                "offload_type": message.offload_type,
            }
        )
        assert content in reloaded

    @pytest.mark.asyncio
    async def test_ttl_rule_compression_offloads_original_even_when_compressed_fits_budget(self):
        context = await create_context(
            MessageOffloaderConfig(
                ttl_seconds=10,
                add_message_threshold_ratio=1.0,
                ttl_context_occupancy_ratio=0.1,
            ),
            context_window_tokens=100,
        )
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        content = "\n".join(["same line"] * 20)
        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-ttl-repeat"))
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)  # type: ignore[attr-defined]
        await context.get_context_window()

        message = context.get_messages()[0]
        assert isinstance(message, OffloadMixin)
        assert message.metadata["rule_compression_pass"] == "ttl"
        assert "[[OFFLOAD:" in message.content
        reloaded = await context.reloader_tool().invoke(
            {
                "offload_handle": message.offload_handle,
                "offload_type": message.offload_type,
            }
        )
        assert "same line" in reloaded
        assert context.save_state()["offload_messages"][message.offload_handle][0].content == content

    @pytest.mark.asyncio
    async def test_ttl_traverses_full_model_context_not_only_returned_window(self):
        context = await create_context(
            MessageOffloaderConfig(ttl_seconds=10),
            context_window_tokens=50002,
            default_window_message_num=1,
        )
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        await context.add_messages(
            [
                ToolMessage(content=character * 30001, tool_call_id=f"tc-{character}")
                for character in ("a", "b", "c")
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)  # type: ignore[attr-defined]
        window = await context.get_context_window()

        assert len(window.context_messages) <= 1
        assert all(isinstance(message, OffloadMixin) for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_ttl_skips_messages_that_were_already_rule_compressed(self):
        context = await create_context(MessageOffloaderConfig(ttl_seconds=10))
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        await context.add_messages(
            [UserMessage(content="context filler " * 12)]
            + [
                ToolMessage(
                    content="\n".join([f"same {character}"] * 12),
                    tool_call_id=f"tc-{character}",
                )
                for character in ("a", "b", "c")
            ]
        )
        await context.get_context_window()
        tool_messages = [message for message in context.get_messages() if message.role == "tool"]
        contents_before_ttl = [message.content for message in tool_messages]
        timestamps_before_ttl = [message.metadata["rule_compressed_at"] for message in tool_messages]
        router_compress = processor._rule_pipeline._router.compress  # type: ignore[attr-defined]
        processor._rule_pipeline._router.compress = MagicMock(wraps=router_compress)  # type: ignore[attr-defined]

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)  # type: ignore[attr-defined]
        await context.get_context_window()

        tool_messages = [message for message in context.get_messages() if message.role == "tool"]
        assert all(isinstance(message, OffloadMixin) for message in tool_messages)
        assert [message.content for message in tool_messages] == contents_before_ttl
        assert [message.metadata["rule_compressed_at"] for message in tool_messages] == timestamps_before_ttl
        processor._rule_pipeline._router.compress.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_ttl_skips_protected_tool_messages(self):
        context = await create_context(
            MessageOffloaderConfig(
                ttl_seconds=10,
                add_message_threshold_ratio=1.0,
                ttl_context_occupancy_ratio=0.1,
            ),
            context_window_tokens=100,
        )
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        content = "x" * 100
        await context.add_messages(
            [
                _tool_call("tc-reload", "reload_original_context_messages"),
                ToolMessage(content=content, tool_call_id="tc-reload"),
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)  # type: ignore[attr-defined]
        await context.get_context_window()

        message = context.get_messages()[1]
        assert message.content == content
        assert not isinstance(message, OffloadMixin)
        assert not message.metadata.get("rule_compressed_at")

    @pytest.mark.asyncio
    async def test_ttl_offloads_message_when_rule_compression_still_exceeds_budget(self):
        context = await create_context(
            MessageOffloaderConfig(ttl_seconds=10),
            context_window_tokens=50002,
        )
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        content = "x" * 30001
        await context.add_messages(
            [
                UserMessage(content="context filler " * 3001),
                ToolMessage(content=content, tool_call_id="tc-large"),
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)  # type: ignore[attr-defined]
        await context.get_context_window()

        message = context.get_messages()[1]
        assert isinstance(message, OffloadMixin)
        reloaded = await context.reloader_tool().invoke(
            {
                "offload_handle": message.offload_handle,
                "offload_type": message.offload_type,
            }
        )
        assert content in reloaded

    @pytest.mark.asyncio
    async def test_context_window_access_time_is_saved_and_restored(self):
        context = await create_context(MessageOffloaderConfig(ttl_seconds=10))
        processor = context._processors[0]  # type: ignore[attr-defined]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)  # type: ignore[attr-defined]
        await context.get_context_window()

        restored = await create_context(MessageOffloaderConfig(ttl_seconds=10))
        restored.load_state({"test_ctx": context.save_state()})

        assert restored.last_context_window_access_at() == 100.0
