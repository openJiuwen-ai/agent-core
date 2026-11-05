# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ImageWindowOffloader and the shared image reference helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.context.context_utils import CONTEXT_USAGE_STALE_KEY
from openjiuwen.core.context_engine.image_refs import (
    FOLDED_METADATA_KEY,
    IMAGE_METADATA_KEY,
    IMAGE_REFS_OPEN,
    SUMMARY_STATUS_FAILED,
    SUMMARY_STATUS_PENDING,
    SUMMARY_STATUS_READY,
    ImageSummary,
    append_image_refs_block,
    collect_image_refs,
    image_key,
    set_image_summary_provider,
)
from openjiuwen.core.context_engine.processor.base import ContextProcessor
from openjiuwen.core.context_engine.processor.offloader.image_window_offloader import (
    ImageWindowOffloader,
    ImageWindowOffloaderConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.harness.tools.base_tool import ToolOutput
from tests.unit_tests.core.context_engine._stream_state_helpers import (
    capture_context_compression_states,
)


def _data_url(tag: str, size: int = 100) -> str:
    return "data:image/png;base64," + (tag * size)[:size]


def _image_message(*paths: str, size: int = 100) -> UserMessage:
    results = [
        ToolOutput(
            success=True,
            data={
                "content": f"Image file read: {path}",
                "multimodal": [
                    {
                        "type": "image",
                        "source_path": path,
                        "data_url": _data_url(path[-1], size),
                        "width": 800,
                        "height": 600,
                    }
                ],
            },
        )
        for path in paths
    ]
    message = ReActAgent._build_multimodal_tool_results_message(results)
    assert message is not None
    return message


def _tool_call(call_id: str) -> List:
    return [
        AssistantMessage(
            content="",
            tool_calls=[ToolCall(id=call_id, name="read_file", type="function", arguments="{}")],
        ),
        ToolMessage(content="Image file read", tool_call_id=call_id, name="read_file"),
    ]


def _final(text: str = "done") -> AssistantMessage:
    return AssistantMessage(content=text)


def _live_paths(messages) -> List[str]:
    paths = []
    for message in messages:
        if not isinstance(message.content, list):
            continue
        for index, block in enumerate(message.content):
            if isinstance(block, dict) and block.get("type") == "image_url":
                paths.append(message.content[index - 1]["text"].split(": ", 1)[1])
    return paths


def _folded_texts(messages) -> List[str]:
    texts = []
    for message in messages:
        if isinstance(message.content, list):
            texts.extend(
                block["text"]
                for block in message.content
                if isinstance(block, dict) and str(block.get("text", "")).startswith("[图片已折叠")
            )
    return texts


async def _build_context(config: Optional[ImageWindowOffloaderConfig] = None, *, session: Optional[Session] = None):
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=200))
    return await engine.create_context(
        context_id="image_ctx",
        session=session,
        history_messages=[],
        processors=[("ImageWindowOffloader", config or ImageWindowOffloaderConfig())],
    )


async def _read_images(context, call_id: str, *paths: str, size: int = 100) -> None:
    for message in _tool_call(call_id):
        await context.add_messages(message)
    await context.add_messages(_image_message(*paths, size=size))


@pytest.fixture(autouse=True)
def _reset_provider():
    set_image_summary_provider(None)
    yield
    set_image_summary_provider(None)


class TestImageMetadataTagging:
    def test_tags_block_index_key_and_size(self):
        message = _image_message("/tmp/a.png", "/tmp/b.png")
        entries = message.metadata[IMAGE_METADATA_KEY]
        assert [entry["source_path"] for entry in entries] == ["/tmp/a.png", "/tmp/b.png"]
        for entry in entries:
            block = message.content[entry["block_index"]]
            assert block["type"] == "image_url"
            assert entry["key"] == image_key(block["image_url"]["url"])
            assert (entry["width"], entry["height"]) == (800, 600)


class TestImageWindowOffloader:
    @pytest.mark.asyncio
    async def test_current_turn_images_are_not_folded_before_model_sees_them(self):
        context = await _build_context(ImageWindowOffloaderConfig(keep_current_turn_max=2, keep_history_k=1))
        await context.add_messages(UserMessage(content="compare"))
        await _read_images(context, "c1", "/tmp/a.png", "/tmp/b.png", "/tmp/c.png")

        assert _live_paths(context.get_messages()) == ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"]

    @pytest.mark.asyncio
    async def test_current_turn_cap_applies_once_seen(self):
        context = await _build_context(ImageWindowOffloaderConfig(keep_current_turn_max=2, keep_history_k=1))
        await context.add_messages(UserMessage(content="compare"))
        await _read_images(context, "c1", "/tmp/a.png", "/tmp/b.png", "/tmp/c.png")
        await _read_images(context, "c2", "/tmp/d.png")

        live = _live_paths(context.get_messages())
        assert "/tmp/d.png" in live
        assert len(live) == 2

    @pytest.mark.asyncio
    async def test_history_keeps_newest_k_after_turn_ends(self):
        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=2))
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png", "/tmp/b.png", "/tmp/c.png")
        assert len(_live_paths(context.get_messages())) == 3

        await context.add_messages(_final())

        messages = context.get_messages()
        assert _live_paths(messages) == ["/tmp/b.png", "/tmp/c.png"]
        assert _folded_texts(messages) == [
            "[图片已折叠 path=/tmp/a.png，需要查看请调用 read_file 重新读取该路径]"
        ]

    @pytest.mark.asyncio
    async def test_folding_marks_usage_baseline_stale(self):
        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=0))
        await context.add_messages(UserMessage(content="look"))
        tool_call_message, tool_message = _tool_call("c1")
        tool_call_message.usage_metadata = UsageMetadata(input_tokens=90, output_tokens=10, total_tokens=100)
        await context.add_messages(tool_call_message)
        await context.add_messages(tool_message)
        await context.add_messages(_image_message("/tmp/a.png"))
        await context.add_messages(_final())

        assert context.get_messages()[1].metadata.get(CONTEXT_USAGE_STALE_KEY) is True

    @pytest.mark.asyncio
    async def test_same_path_keeps_only_newest_copy(self):
        context = await _build_context()
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png")
        await _read_images(context, "c2", "/tmp/a.png")

        messages = context.get_messages()
        assert _live_paths(messages) == ["/tmp/a.png"]
        assert len(_folded_texts(messages)) == 1
        assert isinstance(messages[-1].content[1], dict) and messages[-1].content[1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_byte_cap_folds_oldest_seen_images(self):
        config = ImageWindowOffloaderConfig(keep_current_turn_max=8, max_total_base64_bytes=2500)
        context = await _build_context(config)
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png", size=1000)
        await _read_images(context, "c2", "/tmp/b.png", size=1000)
        await _read_images(context, "c3", "/tmp/c.png", size=1000)

        assert _live_paths(context.get_messages()) == ["/tmp/b.png", "/tmp/c.png"]

    @pytest.mark.asyncio
    async def test_unseen_batch_is_kept_even_over_byte_cap(self):
        config = ImageWindowOffloaderConfig(max_total_base64_bytes=500)
        context = await _build_context(config)
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png", "/tmp/b.png", size=1000)

        assert _live_paths(context.get_messages()) == ["/tmp/a.png", "/tmp/b.png"]

    @pytest.mark.asyncio
    async def test_folding_is_idempotent_and_records_metadata(self):
        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=0))
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png")
        await context.add_messages(_final())
        first = [message.model_dump() for message in context.get_messages()]

        await context.add_messages(UserMessage(content="again"))
        await context.add_messages(_final())
        second = [message.model_dump() for message in context.get_messages()[: len(first)]]

        folded = [m for m in context.get_messages() if m.metadata.get(FOLDED_METADATA_KEY)]
        assert len(folded) == 1
        entry = folded[0].metadata[FOLDED_METADATA_KEY][0]
        assert entry["source_path"] == "/tmp/a.png"
        assert folded[0].content[entry["block_index"]]["text"].startswith("[图片已折叠")
        assert [m["content"] for m in first] == [m["content"] for m in second]

    @pytest.mark.asyncio
    async def test_untagged_legacy_message_is_folded_via_text_path(self):
        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=0))
        legacy = UserMessage(
            content=[
                {"type": "text", "text": "Image loaded from read_file: /tmp/old.png"},
                {"type": "image_url", "image_url": {"url": _data_url("o")}},
            ]
        )
        await context.add_messages(UserMessage(content="look"))
        await context.add_messages(legacy)
        await context.add_messages(_final())

        assert _live_paths(context.get_messages()) == []
        assert _folded_texts(context.get_messages())[0].startswith("[图片已折叠 path=/tmp/old.png")

    @pytest.mark.asyncio
    async def test_images_without_source_are_left_alone(self):
        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=0))
        user_image = UserMessage(
            content=[
                {"type": "text", "text": "what is this"},
                {"type": "image_url", "image_url": {"url": _data_url("u")}},
            ]
        )
        await context.add_messages(user_image)
        await context.add_messages(_final())

        assert context.get_messages()[0].content[1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_disabled_config_is_noop(self):
        context = await _build_context(ImageWindowOffloaderConfig(enabled=False, keep_history_k=0))
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png")
        await context.add_messages(_final())

        assert _live_paths(context.get_messages()) == ["/tmp/a.png"]

    @pytest.mark.asyncio
    async def test_summary_rendered_only_in_window_copy(self):
        class _Provider:
            def lookup(self, key, *, session_id=None):
                return ImageSummary(status=SUMMARY_STATUS_READY, summary="一张柱状图", width=800, height=600)

        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=0))
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png")
        await context.add_messages(_final())
        set_image_summary_provider(_Provider())

        window = await context.get_context_window()
        rendered = _folded_texts(window.context_messages)
        assert rendered and "摘要=一张柱状图" in rendered[0] and "size=800x600" in rendered[0]
        stored = _folded_texts(context.get_messages())
        assert "摘要=" not in stored[0]

    @pytest.mark.asyncio
    async def test_failed_summary_rendered_in_window_copy(self):
        class _Provider:
            def lookup(self, key, *, session_id=None):
                return ImageSummary(status=SUMMARY_STATUS_FAILED, width=800, height=600)

        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=0))
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png")
        await context.add_messages(_final())
        set_image_summary_provider(_Provider())

        window = await context.get_context_window()
        rendered = _folded_texts(window.context_messages)
        assert rendered and "摘要=生成失败" in rendered[0] and "size=800x600" in rendered[0]
        stored = _folded_texts(context.get_messages())
        assert "摘要=" not in stored[0]

    @pytest.mark.asyncio
    async def test_active_compression_folds_storage(self):
        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=0))
        context.set_messages(
            [
                UserMessage(content="look"),
                *_tool_call("c1"),
                _image_message("/tmp/a.png"),
                _final(),
            ]
        )
        await context.compress_context()

        assert _live_paths(context.get_messages()) == []

    @pytest.mark.asyncio
    async def test_passive_get_folds_history_loaded_via_set_messages(self):
        """Images restored via set_messages (bypassing on_add_messages) still get
        folded on the outgoing window copy in a passive GET, while storage stays
        untouched (A-1 regression: the old on_get_context_window non-force branch
        never folded, so restored history bypassed the base64 caps)."""
        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=0))
        context.set_messages(
            [
                UserMessage(content="look"),
                *_tool_call("c1"),
                _image_message("/tmp/a.png"),
                _final(),
            ]
        )

        window = await context.get_context_window()

        # Outgoing window copy folds the image (no live base64 sent to the model).
        assert _live_paths(window.context_messages) == []
        assert _folded_texts(window.context_messages)
        # Storage is not rewritten by a passive GET.
        assert _live_paths(context.get_messages()) == ["/tmp/a.png"]



class _AlwaysTriggerGetProcessor(ContextProcessor):
    """Minimal unsuppressed processor: triggers on every GET so it must still stream state."""

    class _Config(BaseModel):
        model: str = "test-model"

    def __init__(self):
        super().__init__(_AlwaysTriggerGetProcessor._Config())

    async def trigger_get_context_window(self, context, context_window, **kwargs) -> bool:
        return True

    async def on_get_context_window(self, context, context_window, **kwargs):
        return None, context_window

    def load_state(self, state: Dict[str, Any]) -> None:
        return None

    def save_state(self) -> Dict[str, Any]:
        return {}


class TestImageWindowOffloaderStateSuppression:
    """suppress_state_events: folding/summary rendering stays silent, unsuppressed processors still stream."""

    def test_suppress_flag_defaults_off_and_is_set_on_offloader(self):
        assert ContextProcessor.suppress_state_events is False
        assert ImageWindowOffloader.suppress_state_events is True

    @pytest.mark.asyncio
    async def test_get_window_summary_rendering_emits_no_state_events(self):
        class _Provider:
            def lookup(self, key, *, session_id=None):
                return ImageSummary(status=SUMMARY_STATUS_READY, summary="一张柱状图", width=800, height=600)

        session = Session(session_id="image-offloader-stream-session")
        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=0), session=session)
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png")
        await context.add_messages(_final())
        set_image_summary_provider(_Provider())

        window, states = await capture_context_compression_states(
            session,
            lambda: context.get_context_window(),
        )

        assert states == []
        assert context._processor_state_recorder.history() == []
        rendered = _folded_texts(window.context_messages)
        assert rendered and "摘要=一张柱状图" in rendered[0]

    @pytest.mark.asyncio
    async def test_add_phase_folding_emits_no_state_events(self):
        session = Session(session_id="image-offloader-stream-session-add")
        context = await _build_context(ImageWindowOffloaderConfig(keep_history_k=0), session=session)
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png")

        _, states = await capture_context_compression_states(
            session,
            lambda: context.add_messages(_final()),
        )

        assert states == []
        assert context._processor_state_recorder.history() == []
        assert _live_paths(context.get_messages()) == []
        assert _folded_texts(context.get_messages())

    @pytest.mark.asyncio
    async def test_unsuppressed_processor_still_emits_alongside_suppressed_offloader(self):
        class _Provider:
            def lookup(self, key, *, session_id=None):
                return ImageSummary(status=SUMMARY_STATUS_READY, summary="一张柱状图", width=800, height=600)

        session = Session(session_id="image-offloader-stream-session-mixed")
        context = SessionModelContext(
            "image_ctx_mixed",
            session.get_session_id(),
            ContextEngineConfig(default_window_message_num=200),
            history_messages=[],
            processors=[
                ImageWindowOffloader(ImageWindowOffloaderConfig(keep_history_k=0)),
                _AlwaysTriggerGetProcessor(),
            ],
            token_counter=None,
        )
        setattr(context, "_session_ref", session)
        await context.add_messages(UserMessage(content="look"))
        await _read_images(context, "c1", "/tmp/a.png")
        await context.add_messages(_final())
        set_image_summary_provider(_Provider())

        _, states = await capture_context_compression_states(
            session,
            lambda: context.get_context_window(),
        )

        assert [state.status for state in states] == ["started", "noop"]
        assert {state.processor for state in states} == {"_AlwaysTriggerGetProcessor"}


class TestImageRefs:
    def test_collects_paths_from_live_folded_text_and_previous_refs(self):
        messages = [
            _image_message("/tmp/a.png"),
            UserMessage(content="[图片已折叠 path=/tmp/b.png，需要查看请调用 read_file 重新读取该路径]"),
            UserMessage(content=f"summary\n{IMAGE_REFS_OPEN}\n- /tmp/c.png | size=10x10\n</image_refs>"),
        ]
        assert [ref.path for ref in collect_image_refs(messages)] == ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"]

    def test_append_block_replaces_model_written_block(self):
        messages = [_image_message("/tmp/a.png")]
        summary = append_image_refs_block("state\n<image_refs>\n- bogus\n</image_refs>", messages)
        assert summary.count(IMAGE_REFS_OPEN) == 1
        assert "- /tmp/a.png | size=800x600" in summary
        assert "bogus" not in summary

    def test_block_includes_summary_status_from_provider(self):
        class _Provider:
            def lookup(self, key, *, session_id=None):
                return ImageSummary(status=SUMMARY_STATUS_PENDING)

        set_image_summary_provider(_Provider())
        summary = append_image_refs_block("state", [_image_message("/tmp/a.png")])
        assert "摘要=生成中" in summary

    def test_block_shows_failed_status_label(self):
        class _Provider:
            def lookup(self, key, *, session_id=None):
                return ImageSummary(status=SUMMARY_STATUS_FAILED)

        set_image_summary_provider(_Provider())
        summary = append_image_refs_block("state", [_image_message("/tmp/a.png")])
        assert "摘要=生成失败" in summary

    def test_no_images_keeps_summary_unchanged(self):
        assert append_image_refs_block("state", [UserMessage(content="hi")]) == "state"
