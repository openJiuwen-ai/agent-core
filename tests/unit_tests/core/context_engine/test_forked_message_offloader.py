# coding: utf-8

import glob
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.forked.offloader.message_offloader import (
    MessageSummaryOffloaderConfig,
)
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage

pytestmark = pytest.mark.usefixtures("refactored_context_processors")


async def create_context(
    config: MessageSummaryOffloaderConfig | None = None,
    *,
    context_window_tokens: int = 100,
    default_window_message_num: int = 100,
    workspace=None,
    token_counter=None,
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
        processors=[("MessageSummaryOffloader", config or MessageSummaryOffloaderConfig())],
        token_counter=token_counter,
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


def _metadata_token_counter():
    counter = MagicMock()
    counter.count_messages.side_effect = lambda messages: sum(
        message.metadata.get("test_token_count", 0) for message in messages
    )
    return counter


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


def _offloaded_content(message: OffloadMixin) -> str:
    marker_path = message.content.rsplit("path=", 1)[1].rsplit("]]", 1)[0]
    payload = json.loads(open(marker_path, encoding="utf-8").read())
    return payload["messages"][0]["content"]


def _python_function_for_offload(name: str, *, line_count: int) -> list[str]:
    return [
        f"def {name}(base: int) -> int:",
        "    total = base",
        *[f"    total += {index}" for index in range(line_count)],
        "    return total",
    ]


class TestMessageOffloaderConfig:
    def test_only_exposes_ttl_threshold_ratios_and_protected_tools(self):
        assert set(MessageSummaryOffloaderConfig.model_fields) == {
            "add_message_threshold_ratio",
            "ttl_context_occupancy_ratio",
            "ttl_message_threshold_ratio",
            "ttl_seconds",
            "protected_tool_names",
            "enable_debug_dump",
            "debug_dump_dir",
        }

    def test_ignores_official_configuration_fields_for_switch_compatibility(self):
        config = MessageSummaryOffloaderConfig(
            large_message_threshold=15_000,
            offload_message_type=["tool"],
            messages_to_keep=10,
            keep_last_round=False,
        )

        assert config == MessageSummaryOffloaderConfig()


class TestMessageOffloaderAddTrigger:
    @pytest.mark.asyncio
    async def test_add_trigger_uses_message_tokens_above_ratio_threshold(self):
        counter = _metadata_token_counter()
        context = await create_context(context_window_tokens=100, token_counter=counter)

        await context.add_messages(
            ToolMessage(
                content="x",
                tool_call_id="tc-token-large",
                metadata={"test_token_count": 11},
            )
        )

        assert isinstance(context.get_messages()[0], OffloadMixin)

    @pytest.mark.asyncio
    async def test_add_trigger_skips_message_equal_to_token_threshold(self):
        counter = _metadata_token_counter()
        context = await create_context(context_window_tokens=100, token_counter=counter)

        await context.add_messages(
            ToolMessage(
                content="x" * 100,
                tool_call_id="tc-token-boundary",
                metadata={"test_token_count": 10},
            )
        )

        assert not isinstance(context.get_messages()[0], OffloadMixin)

    @pytest.mark.asyncio
    async def test_add_trigger_keeps_character_budget_for_rule_compression(self):
        counter = _metadata_token_counter()
        context = await create_context(context_window_tokens=100, token_counter=counter)
        processor = context._processors[0]
        processor._rule_pipeline.compress = MagicMock(side_effect=lambda message, *_args, **_kwargs: message)

        await context.add_messages(
            ToolMessage(
                content="x",
                tool_call_id="tc-char-budget",
                metadata={"test_token_count": 11},
            )
        )

        assert processor._rule_pipeline.compress.call_args.kwargs["max_chars"] == 30

    @pytest.mark.asyncio
    async def test_does_not_trigger_below_default_token_capacity(self):
        counter = _metadata_token_counter()
        context = await create_context(context_window_tokens=100, token_counter=counter)

        await context.add_messages(
            ToolMessage(
                content="x" * 20,
                tool_call_id="tc-boundary",
                metadata={"test_token_count": 9},
            )
        )

        message = context.get_messages()[0]
        assert message.content == "x" * 20
        assert not isinstance(message, OffloadMixin)

    @pytest.mark.asyncio
    async def test_triggers_above_default_character_capacity(self):
        context = await create_context(context_window_tokens=100)

        await context.add_messages(ToolMessage(content="x" * 61, tool_call_id="tc-large"))

        message = context.get_messages()[0]
        assert isinstance(message, OffloadMixin)
        assert _offloaded_content(message) == "x" * 61

    @pytest.mark.asyncio
    async def test_keeps_original_message_when_filesystem_offload_write_fails(self):
        context = await create_context(context_window_tokens=100)
        processor = context._processors[0]
        processor._write_offload_to_file = AsyncMock(return_value=False)

        await context.add_messages(ToolMessage(content="x" * 61, tool_call_id="tc-large"))

        message = context.get_messages()[0]
        assert not isinstance(message, OffloadMixin)
        assert message.content == "x" * 61
        assert "[[OFFLOAD:" not in message.content

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
        assert _offloaded_content(message) == content

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
        assert f"[[OFFLOAD: handle={message.offload_handle}, type=filesystem, path=" in message.content
        assert message.content.rstrip().endswith("]]")

        offload_path = tmp_path / "context" / "default_session_id_context" / "offload"
        files = list(offload_path.glob("MessageSummaryOffloader_*.json"))
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
        assert f"[[OFFLOAD: handle={message.offload_handle}, type=filesystem, path=" in message.content
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
        assert "[Original content offloaded.]" in message.content
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
                _tool_call("tc-read", "read_file"),
                ToolMessage(content="x" * 100, tool_call_id="tc-read"),
            ]
        )

        message = context.get_messages()[1]
        assert message.content == "x" * 100
        assert not isinstance(message, OffloadMixin)
        states = context._processor_state_recorder.history()
        assert [state["status"] for state in states] == ["started", "noop"]

    @pytest.mark.asyncio
    async def test_add_threshold_ratio_is_configurable(self):
        context = await create_context(
            MessageSummaryOffloaderConfig(add_message_threshold_ratio=0.5),
            context_window_tokens=100,
        )

        await context.add_messages(ToolMessage(content="x" * 100, tool_call_id="tc-large"))

        message = context.get_messages()[0]
        assert message.content == "x" * 100
        assert not isinstance(message, OffloadMixin)

class TestMessageOffloaderTtl:
    @pytest.mark.asyncio
    async def test_ttl_uses_token_thresholds_for_context_and_each_message(self):
        counter = _metadata_token_counter()
        context = await create_context(
            MessageSummaryOffloaderConfig(
                ttl_seconds=10,
                add_message_threshold_ratio=10,
                ttl_context_occupancy_ratio=0.5,
                ttl_message_threshold_ratio=0.1,
            ),
            context_window_tokens=100,
            token_counter=counter,
        )
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        await context.add_messages(
            [
                ToolMessage(
                    content=character * 31,
                    tool_call_id=f"tc-{character}",
                    metadata={"test_token_count": 25},
                )
                for character in ("a", "b")
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        await context.get_context_window()

        assert all(isinstance(message, OffloadMixin) for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_ttl_requires_idle_timeout_and_half_context_occupancy(self):
        counter = _metadata_token_counter()
        context = await create_context(
            MessageSummaryOffloaderConfig(ttl_seconds=10, add_message_threshold_ratio=1.0),
            context_window_tokens=50002,
            token_counter=counter,
        )
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        messages = [
            ToolMessage(
                content=character * 30001,
                tool_call_id=f"tc-{character}",
                metadata={"test_token_count": 10000},
            )
            for character in ("a", "b", "c")
        ]
        await context.add_messages(messages)

        await context.get_context_window()
        processor._rule_pipeline._time_func = MagicMock(return_value=105.0)
        await context.get_context_window()
        assert all(not message.metadata.get("rule_compressed_at") for message in context.get_messages())

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        await context.get_context_window()

        assert all(isinstance(message, OffloadMixin) for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_ttl_falls_back_to_offload_when_rule_compression_has_no_savings(self):
        counter = _metadata_token_counter()
        context = await create_context(
            MessageSummaryOffloaderConfig(ttl_seconds=10),
            context_window_tokens=50002,
            token_counter=counter,
        )
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        await context.add_messages(
            [
                ToolMessage(
                    content=character * 15000,
                    tool_call_id=f"tc-{character}",
                    metadata={"test_token_count": 5000},
                )
                for character in ("a", "b", "c", "d", "e", "f")
            ]
        )
        await context.get_context_window()
        router_compress = processor._rule_pipeline._router.compress
        processor._rule_pipeline._router.compress = MagicMock(wraps=router_compress)

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        await context.get_context_window()

        assert all(not message.metadata.get("rule_compressed_at") for message in context.get_messages())
        assert all(isinstance(message, OffloadMixin) for message in context.get_messages())
        assert processor._rule_pipeline._router.compress.call_count == 6

    @pytest.mark.asyncio
    async def test_ttl_skips_context_below_half_capacity(self):
        context = await create_context(MessageSummaryOffloaderConfig(ttl_seconds=10))
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        await context.add_messages(
            [
                ToolMessage(content="a" * 40, tool_call_id="tc-a"),
                ToolMessage(content="b" * 40, tool_call_id="tc-b"),
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        await context.get_context_window()

        assert all(not message.metadata.get("rule_compressed_at") for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_ttl_context_occupancy_ratio_is_configurable(self):
        context = await create_context(
            MessageSummaryOffloaderConfig(
                ttl_seconds=10,
                add_message_threshold_ratio=1.0,
                ttl_context_occupancy_ratio=0.8,
                ttl_message_threshold_ratio=0.25,
            ),
            context_window_tokens=100,
        )
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        await context.add_messages(
            [
                ToolMessage(content="a" * 100, tool_call_id="tc-a"),
                ToolMessage(content="b" * 100, tool_call_id="tc-b"),
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        await context.get_context_window()

        assert all(not isinstance(message, OffloadMixin) for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_ttl_message_threshold_ratio_is_configurable(self):
        context = await create_context(
            MessageSummaryOffloaderConfig(
                ttl_seconds=10,
                add_message_threshold_ratio=1.0,
                ttl_context_occupancy_ratio=0.1,
                ttl_message_threshold_ratio=0.25,
            ),
            context_window_tokens=100,
        )
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        content = "x" * 100
        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-ttl"))
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        await context.get_context_window()

        message = context.get_messages()[0]
        assert isinstance(message, OffloadMixin)
        assert _offloaded_content(message) == content

    @pytest.mark.asyncio
    async def test_ttl_rule_compression_offloads_original_even_when_compressed_fits_budget(self):
        context = await create_context(
            MessageSummaryOffloaderConfig(
                ttl_seconds=10,
                add_message_threshold_ratio=1.0,
                ttl_context_occupancy_ratio=0.1,
            ),
            context_window_tokens=100,
        )
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        content = "\n".join(["same line"] * 20)
        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-ttl-repeat"))
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        await context.get_context_window()

        message = context.get_messages()[0]
        assert isinstance(message, OffloadMixin)
        assert message.metadata["rule_compression_pass"] == "ttl"
        assert "[[OFFLOAD:" in message.content
        assert _offloaded_content(message) == content

    @pytest.mark.asyncio
    async def test_writes_absolute_debug_log_for_rule_compression_and_offload(
        self,
        monkeypatch,
        tmp_path,
    ):
        debug_dir = tmp_path / "debug"
        monkeypatch.setenv("OPENJIUWEN_MESSAGE_OFFLOADER_DEBUG_LOG_DIR", str(debug_dir))
        context = await create_context(
            MessageSummaryOffloaderConfig(enable_debug_dump=True),
            context_window_tokens=100,
        )
        content = "\n".join(["same line"] * 10000)

        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-debug"))

        log_path = debug_dir / "message_offloader_debug.jsonl"
        assert log_path.is_absolute()
        records = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [record["event"] for record in records] == [
            "add_message_triggered_offload",
            "rule_compression_triggered",
            "rule_compression_completed",
            "message_offloaded",
        ]
        completed = records[2]
        assert completed["tool_call_id"] == "tc-debug"
        assert completed["original_content"] == content
        assert completed["compressed_content"].startswith("same line")
        assert len(completed["compressed_content"]) < len(content)
        assert records[3]["offloaded_content"].endswith(context.get_messages()[0].content)

    @pytest.mark.asyncio
    async def test_does_not_write_debug_log_by_default(self, monkeypatch, tmp_path):
        debug_dir = tmp_path / "debug"
        monkeypatch.setenv("OPENJIUWEN_MESSAGE_OFFLOADER_DEBUG_LOG_DIR", str(debug_dir))
        context = await create_context(context_window_tokens=100)

        await context.add_messages(ToolMessage(content="x" * 10000, tool_call_id="tc-no-debug"))

        assert not (debug_dir / "message_offloader_debug.jsonl").exists()

    @pytest.mark.asyncio
    async def test_uses_configured_debug_dump_dir(self, tmp_path):
        debug_dir = tmp_path / "configured-debug"
        context = await create_context(
            MessageSummaryOffloaderConfig(
                enable_debug_dump=True,
                debug_dump_dir=str(debug_dir),
            ),
            context_window_tokens=100,
        )

        content = "\n".join(["same line"] * 10000)

        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-config-debug"))

        assert (debug_dir / "message_offloader_debug.jsonl").exists()
        assert list(debug_dir.glob("*.json"))

    @pytest.mark.asyncio
    async def test_expands_session_template_debug_dump_dir(self, tmp_path):
        context = await create_context(
            MessageSummaryOffloaderConfig(
                enable_debug_dump=True,
                debug_dump_dir=str(
                    tmp_path / "context" / "{session_id}_context" / "debug_artifacts" / "message_offloader"
                ),
            ),
            context_window_tokens=100,
        )

        content = "\n".join(["same line"] * 10000)

        await context.add_messages(ToolMessage(content=content, tool_call_id="tc-session-debug"))

        debug_dir = tmp_path / "context" / "default_session_id_context" / "debug_artifacts" / "message_offloader"
        assert (debug_dir / "message_offloader_debug.jsonl").exists()
        assert list(debug_dir.glob("*.json"))

    @pytest.mark.asyncio
    async def test_ttl_traverses_full_model_context_not_only_returned_window(self):
        counter = _metadata_token_counter()
        context = await create_context(
            MessageSummaryOffloaderConfig(ttl_seconds=10, add_message_threshold_ratio=1.0),
            context_window_tokens=50002,
            default_window_message_num=1,
            token_counter=counter,
        )
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        await context.add_messages(
            [
                ToolMessage(
                    content=character * 30001,
                    tool_call_id=f"tc-{character}",
                    metadata={"test_token_count": 10000},
                )
                for character in ("a", "b", "c")
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        window = await context.get_context_window()

        assert len(window.context_messages) <= 1
        assert all(isinstance(message, OffloadMixin) for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_ttl_skips_messages_that_were_already_rule_compressed(self):
        context = await create_context(MessageSummaryOffloaderConfig(ttl_seconds=10))
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
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
        router_compress = processor._rule_pipeline._router.compress
        processor._rule_pipeline._router.compress = MagicMock(wraps=router_compress)

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        await context.get_context_window()

        tool_messages = [message for message in context.get_messages() if message.role == "tool"]
        assert all(isinstance(message, OffloadMixin) for message in tool_messages)
        assert [message.content for message in tool_messages] == contents_before_ttl
        assert [message.metadata["rule_compressed_at"] for message in tool_messages] == timestamps_before_ttl
        processor._rule_pipeline._router.compress.assert_not_called()

    @pytest.mark.asyncio
    async def test_ttl_skips_protected_tool_messages(self):
        context = await create_context(
            MessageSummaryOffloaderConfig(
                ttl_seconds=10,
                add_message_threshold_ratio=1.0,
                ttl_context_occupancy_ratio=0.1,
            ),
            context_window_tokens=100,
        )
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        content = "x" * 100
        await context.add_messages(
            [
                _tool_call("tc-read", "read_file"),
                ToolMessage(content=content, tool_call_id="tc-read"),
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        await context.get_context_window()

        message = context.get_messages()[1]
        assert message.content == content
        assert not isinstance(message, OffloadMixin)
        assert not message.metadata.get("rule_compressed_at")

    @pytest.mark.asyncio
    async def test_ttl_offloads_message_when_rule_compression_still_exceeds_budget(self):
        counter = _metadata_token_counter()
        context = await create_context(
            MessageSummaryOffloaderConfig(ttl_seconds=10, add_message_threshold_ratio=1.0),
            context_window_tokens=50002,
            token_counter=counter,
        )
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        content = "x" * 30001
        await context.add_messages(
            [
                UserMessage(
                    content="context filler " * 3001,
                    metadata={"test_token_count": 20000},
                ),
                ToolMessage(
                    content=content,
                    tool_call_id="tc-large",
                    metadata={"test_token_count": 10000},
                ),
            ]
        )
        await context.get_context_window()

        processor._rule_pipeline._time_func = MagicMock(return_value=120.0)
        await context.get_context_window()

        message = context.get_messages()[1]
        assert isinstance(message, OffloadMixin)
        assert _offloaded_content(message) == content

    @pytest.mark.asyncio
    async def test_context_window_access_time_is_saved_and_restored(self):
        context = await create_context(MessageSummaryOffloaderConfig(ttl_seconds=10))
        processor = context._processors[0]
        processor._rule_pipeline._time_func = MagicMock(return_value=100.0)
        await context.get_context_window()

        restored = await create_context(MessageSummaryOffloaderConfig(ttl_seconds=10))
        restored.load_state({"test_ctx": context.save_state()})

        assert restored.last_context_window_access_at() == 100.0
