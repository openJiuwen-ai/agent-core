#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextWindow
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PROMPT_ATTACHMENT_PRESERVE_TAIL_METADATA_KEY,
    PromptAttachmentManager,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_state_context_processor import (
    BrowserStateContextProcessor,
    BrowserStateContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_browser_state_metadata_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
)


def _state(url: str) -> dict:
    return {
        "ok": True,
        "error": None,
        "url": url,
        "title": "Current page",
        "tabs": [
            {"index": 0, "current": True, "url": url, "title": "Current page"},
            {"index": 1, "current": False, "url": "https://other.example", "title": "Other"},
        ],
        "page_position": {
            "viewport_width": 1280,
            "viewport_height": 720,
            "page_width": 1280,
            "page_height": 2400,
            "scroll_x": 0,
            "scroll_y": 400,
            "pixels_above": 400,
            "pixels_below": 1280,
        },
        "dom": '- button "Continue" [ref=e7]',
    }


def _window_message(window: ContextWindow, name: str) -> UserMessage:
    matches = [message for message in window.context_messages if message.name == name]
    assert len(matches) == 1
    message = matches[0]
    assert isinstance(message, UserMessage)
    return message


async def _add_completed_browser_action(
    context,
    *,
    call_id: str,
    tool_name: str = "browser_evaluate",
    arguments: str = '{"function": "() => window.scrollTo(0, document.body.scrollHeight)"}',
) -> None:
    await context.add_messages(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        id=call_id,
                        type="function",
                        name=tool_name,
                        arguments=arguments,
                    )
                ],
            ),
            ToolMessage(content="completed", tool_call_id=call_id),
        ]
    )


@pytest.mark.asyncio
async def test_processor_reuses_cached_browser_state_without_navigation() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.return_value = _state("https://first.example")
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=provider))
    window = ContextWindow(context_messages=[UserMessage(content="original request")])

    _, window = await processor.on_get_context_window(None, window)
    original_state_message = _window_message(window, "current_browser_state")
    _, window = await processor.on_get_context_window(None, window)

    assert provider.capture_browser_state.await_count == 1
    assert len(window.context_messages) == 3
    assert window.context_messages[0].content == "original request"

    state_message = _window_message(window, "current_browser_state")
    progress_message = _window_message(window, "browser_state_progress")
    assert state_message is original_state_message
    assert state_message.name == "current_browser_state"
    assert state_message.metadata["browser_state_context"] is True
    assert state_message.metadata[PROMPT_ATTACHMENT_PRESERVE_TAIL_METADATA_KEY] is True
    assert "https://first.example" in state_message.content
    assert "https://other.example" in state_message.content
    assert '"scroll_y": 400' in state_message.content
    assert '"page_change"' not in state_message.content
    assert "[ref=e7]" in state_message.content
    assert "image_url" not in state_message.content
    assert '"page_change": "initial"' in progress_message.content
    assert progress_message.metadata[PROMPT_ATTACHMENT_PRESERVE_TAIL_METADATA_KEY] is True
    assert window.context_messages.index(progress_message) > window.context_messages.index(state_message)
    provider.capture_browser_state.assert_awaited_with()


@pytest.mark.parametrize(
    "tool_name",
    [
        "browser_batch_interact",
        "browser_click",
        "browser_close",
        "browser_custom_action",
        "browser_drag",
        "browser_drop",
        "browser_evaluate",
        "browser_file_upload",
        "browser_fill_form",
        "browser_handle_dialog",
        "browser_hover",
        "browser_navigate",
        "browser_navigate_back",
        "browser_press_key",
        "browser_run_code",
        "browser_run_code_unsafe",
        "browser_select_option",
        "browser_tabs",
        "browser_type",
        "browser_wait_for",
        "browser_mouse_click_xy",
        "browser_mouse_down",
        "browser_mouse_drag_xy",
        "browser_mouse_move_xy",
        "browser_mouse_up",
        "browser_mouse_wheel",
        "browser_annotate",
        "browser_hide_highlight",
        "browser_highlight",
        "browser_resize",
        "browser_resume",
        "browser_video_hide_actions",
        "browser_video_show_actions",
        "browser_network_state_set",
        "browser_route",
        "browser_unroute",
        "browser_cookie_clear",
        "browser_cookie_delete",
        "browser_cookie_set",
        "browser_localstorage_clear",
        "browser_localstorage_delete",
        "browser_localstorage_set",
        "browser_sessionstorage_clear",
        "browser_sessionstorage_delete",
        "browser_sessionstorage_set",
        "browser_set_storage_state",
        "mcp_playwright-official_browser_navigate",
    ],
)
@pytest.mark.asyncio
async def test_context_engine_refreshes_state_after_completed_mutating_tool(
    tool_name: str,
) -> None:
    provider = AsyncMock()
    provider.capture_browser_state.side_effect = [
        _state("https://first.example"),
        _state("https://second.example"),
    ]
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-state-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="original request"))

    first_window = await context.get_context_window()
    cached_window = await context.get_context_window()
    assert provider.capture_browser_state.await_count == 1

    await context.add_messages(
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    id="mutating-call",
                    type="function",
                    name=tool_name,
                    arguments="{}",
                )
            ],
        )
    )
    pending_window = await context.get_context_window()
    assert provider.capture_browser_state.await_count == 1

    await context.add_messages(ToolMessage(content="completed", tool_call_id="mutating-call"))
    refreshed_window = await context.get_context_window()
    reused_window = await context.get_context_window()

    persisted_messages = context.get_messages()
    assert len(persisted_messages) == 3
    assert persisted_messages[0].content == "original request"
    assert all(
        not message.metadata.get("browser_state_context") and not message.metadata.get("browser_state_progress_context")
        for message in persisted_messages
    )
    first_state = _window_message(first_window, "current_browser_state")
    cached_state = _window_message(cached_window, "current_browser_state")
    pending_state = _window_message(pending_window, "current_browser_state")
    refreshed_state = _window_message(refreshed_window, "current_browser_state")
    reused_state = _window_message(reused_window, "current_browser_state")
    refreshed_progress = _window_message(refreshed_window, "browser_state_progress")
    assert "https://first.example" in first_state.content
    assert cached_state is first_state
    assert pending_state is first_state
    assert "https://second.example" in refreshed_state.content
    assert refreshed_state is not first_state
    assert '"page_change": "changed"' in refreshed_progress.content
    assert '"observable_progress": true' in refreshed_progress.content
    assert '"consecutive_no_progress": 0' in refreshed_progress.content
    assert reused_state is refreshed_state
    assert len(first_window.context_messages) == 3
    assert provider.capture_browser_state.await_count == 2


@pytest.mark.asyncio
async def test_context_engine_injects_new_capture_after_evaluate_and_reports_unchanged() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.side_effect = [
        _state("https://same.example"),
        _state("https://same.example"),
    ]
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-state-unchanged-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="original request"))

    initial_window = await context.get_context_window()
    await _add_completed_browser_action(context, call_id="unchanged-call")
    unchanged_window = await context.get_context_window()

    initial_state = _window_message(initial_window, "current_browser_state")
    unchanged_state = _window_message(unchanged_window, "current_browser_state")
    progress = _window_message(unchanged_window, "browser_state_progress")
    assert unchanged_state is not initial_state
    assert unchanged_state.content == initial_state.content
    assert '"page_change": "unchanged"' in progress.content
    assert '"observable_progress": false' in progress.content
    assert '"consecutive_no_progress": 1' in progress.content
    assert "<required_next_action>" in progress.content
    assert "NO OBSERVABLE PROGRESS. Do not repeat the previous action." in progress.content
    assert "</required_next_action>" in progress.content
    assert unchanged_window.context_messages.index(progress) > unchanged_window.context_messages.index(unchanged_state)
    assert unchanged_window.context_messages[-1] is progress
    assert provider.capture_browser_state.await_count == 2


@pytest.mark.asyncio
async def test_prompt_attachments_remain_before_browser_state_and_progress_tail() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.return_value = _state("https://tail.example")
    manager = PromptAttachmentManager()
    await manager.add_section(
        session_id="browser-state-tail-test",
        section="runtime",
        kind="runtime",
        source="test.browser_state",
        content="runtime attachment",
    )
    engine = ContextEngine()
    engine.register_window_mutator(manager.make_window_mutator("browser-state-tail-test"))
    context = await engine.create_context(
        "browser-state-tail-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="original request"))

    window = await context.get_context_window()
    messages = window.context_messages

    assert "<system-reminder>" in messages[-3].content
    assert "runtime attachment" in messages[-3].content
    assert messages[-2].name == "current_browser_state"
    assert messages[-1].name == "browser_state_progress"


@pytest.mark.asyncio
async def test_no_progress_count_increments_and_resets_after_changed_state() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.side_effect = [
        _state("https://same.example"),
        _state("https://same.example"),
        _state("https://same.example"),
        _state("https://changed.example"),
    ]
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-state-progress-count-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="original request"))

    initial_window = await context.get_context_window()
    initial_state = _window_message(initial_window, "current_browser_state")

    await _add_completed_browser_action(context, call_id="unchanged-call-1")
    first_unchanged_window = await context.get_context_window()
    await _add_completed_browser_action(context, call_id="unchanged-call-2")
    second_unchanged_window = await context.get_context_window()
    await _add_completed_browser_action(
        context,
        call_id="changed-call",
        tool_name="browser_click",
        arguments='{"ref": "e7"}',
    )
    changed_window = await context.get_context_window()

    first_progress = _window_message(first_unchanged_window, "browser_state_progress")
    second_progress = _window_message(second_unchanged_window, "browser_state_progress")
    changed_progress = _window_message(changed_window, "browser_state_progress")
    first_unchanged_state = _window_message(first_unchanged_window, "current_browser_state")
    second_unchanged_state = _window_message(second_unchanged_window, "current_browser_state")
    assert '"consecutive_no_progress": 1' in first_progress.content
    assert '"consecutive_no_progress": 2' in second_progress.content
    assert first_unchanged_state is not initial_state
    assert second_unchanged_state is not first_unchanged_state
    assert first_unchanged_state.content == initial_state.content
    assert second_unchanged_state.content == first_unchanged_state.content
    assert '"page_change": "changed"' in changed_progress.content
    assert '"observable_progress": true' in changed_progress.content
    assert '"consecutive_no_progress": 0' in changed_progress.content
    assert _window_message(changed_window, "current_browser_state") is not second_unchanged_state
    assert provider.capture_browser_state.await_count == 4


@pytest.mark.asyncio
async def test_context_engine_does_not_refresh_after_read_only_browser_tool() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.return_value = _state("https://first.example")
    engine = ContextEngine()
    context = await engine.create_context(
        "browser-state-non-navigation-test",
        processors=[
            (
                "BrowserStateContextProcessor",
                BrowserStateContextProcessorConfig(provider=provider),
            )
        ],
    )
    await context.add_messages(UserMessage(content="original request"))
    await context.get_context_window()
    await context.add_messages(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        id="console-call",
                        type="function",
                        name="browser_console_messages",
                        arguments="{}",
                    )
                ],
            ),
            ToolMessage(content="console messages", tool_call_id="console-call"),
        ]
    )

    window = await context.get_context_window()

    assert provider.capture_browser_state.await_count == 1
    assert "https://first.example" in _window_message(window, "current_browser_state").content


@pytest.mark.asyncio
async def test_processor_injects_explicit_unavailable_state_without_stale_image() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.side_effect = RuntimeError("browser disconnected")
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=provider))
    stale = UserMessage(
        name="current_browser_state",
        metadata={"browser_state_context": True},
        content=[
            {"type": "text", "text": "stale"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,STALE"},
            },
        ],
    )
    window = ContextWindow(context_messages=[stale])

    _, window = await processor.on_get_context_window(None, window)

    assert len(window.context_messages) == 2
    state_content = _window_message(window, "current_browser_state").content
    progress_content = _window_message(window, "browser_state_progress").content
    assert "browser disconnected" in state_content
    assert "[DOM snapshot unavailable]" in state_content
    assert "image_url" not in state_content
    assert '"page_change": "unknown"' in progress_content
    assert "browser disconnected" in progress_content


@pytest.mark.asyncio
async def test_processor_load_state_resets_page_change_baseline() -> None:
    provider = AsyncMock()
    provider.capture_browser_state.side_effect = [
        _state("https://first.example"),
        _state("https://second.example"),
    ]
    processor = BrowserStateContextProcessor(BrowserStateContextProcessorConfig(provider=provider))
    window = ContextWindow(context_messages=[UserMessage(content="original request")])

    _, first_window = await processor.on_get_context_window(None, window)
    first_state = _window_message(first_window, "current_browser_state")
    first_progress = _window_message(first_window, "browser_state_progress")
    processor.load_state({})
    _, restored_window = await processor.on_get_context_window(None, first_window)

    restored_state = _window_message(restored_window, "current_browser_state")
    restored_progress = _window_message(restored_window, "browser_state_progress")
    assert '"page_change": "initial"' in first_progress.content
    assert '"page_change": "initial"' in restored_progress.content
    assert "https://second.example" in restored_state.content
    assert restored_state is not first_state
    assert provider.capture_browser_state.await_count == 2


@pytest.mark.asyncio
async def test_runtime_combines_snapshot_with_page_metadata() -> None:
    runtime = object.__new__(BrowserAgentRuntime)
    runtime._page_generation = 0
    runtime._reference_generations = {}
    runtime._selector_primary_links = {}
    runtime._last_observed_url = ""
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._call_playwright_tool = AsyncMock(return_value='- link "Docs" [ref=e3]')
    runtime._call_playwright_run_code_unsafe = AsyncMock(
        return_value={
            "ok": True,
            "url": "https://example.test/docs",
            "title": "Docs",
            "tabs": [
                {
                    "index": 0,
                    "current": True,
                    "url": "https://example.test/docs",
                    "title": "Docs",
                }
            ],
            "page_position": {"scroll_y": 200, "pixels_below": 800},
        }
    )

    state = await runtime.capture_browser_state()

    runtime.ensure_runtime_ready.assert_awaited_once()
    runtime._call_playwright_tool.assert_awaited_once_with("browser_snapshot", {})
    run_code = runtime._call_playwright_run_code_unsafe.await_args.args[0]
    assert "page.screenshot" not in run_code
    assert state["ok"] is True
    assert state["url"] == "https://example.test/docs"
    assert state["dom"] == '- link "Docs" [ref=e3]'
    assert "screenshot" not in state
    target = runtime._ensure_page_state().resolve_target(generation_id="g0", ref="e3")
    assert target is not None
    assert target.locator == {"ref": "e3"}


@pytest.mark.asyncio
async def test_runtime_automatic_capture_replaces_refs_after_url_generation_sync() -> None:
    runtime = object.__new__(BrowserAgentRuntime)
    runtime._page_generation = 0
    runtime._reference_generations = {}
    runtime._selector_primary_links = {}
    runtime._last_observed_url = ""
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._call_playwright_tool = AsyncMock(
        side_effect=[
            '- button "First" [ref=e1]',
            '- button "Second" [ref=e2]',
        ]
    )
    runtime._call_playwright_run_code_unsafe = AsyncMock(
        side_effect=[
            {
                "ok": True,
                "url": "https://example.test/first",
                "title": "First",
                "tabs": [],
                "page_position": {},
            },
            {
                "ok": True,
                "url": "https://example.test/second",
                "title": "Second",
                "tabs": [],
                "page_position": {},
            },
        ]
    )

    first_state = await runtime.capture_browser_state()
    second_state = await runtime.capture_browser_state()

    assert first_state["dom"] == '- button "First" [ref=e1]'
    assert second_state["dom"] == '- button "Second" [ref=e2]'
    assert runtime.generation_id == "g1"
    assert runtime._ensure_page_state().resolve_target(generation_id="g1", ref="e2") is not None
    with pytest.raises(ValueError, match="Stale AX ref e1 belongs to g0"):
        runtime._ensure_page_state().resolve_target(generation_id="g1", ref="e1")


@pytest.mark.asyncio
async def test_runtime_does_not_reuse_dom_when_snapshot_capture_fails() -> None:
    runtime = object.__new__(BrowserAgentRuntime)
    runtime._page_generation = 0
    runtime._reference_generations = {}
    runtime._selector_primary_links = {}
    runtime._last_observed_url = ""
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._call_playwright_tool = AsyncMock(side_effect=RuntimeError("snapshot timeout"))
    runtime._call_playwright_run_code_unsafe = AsyncMock(
        return_value={
            "ok": True,
            "url": "https://fresh.example",
            "title": "Fresh",
            "tabs": [],
            "page_position": {},
        }
    )

    state = await runtime.capture_browser_state()

    assert state["ok"] is False
    assert state["dom"] == ""
    assert "snapshot timeout" in state["dom_error"]
    assert state["url"] == "https://fresh.example"
    assert "screenshot" not in state


def test_browser_state_metadata_probe_collects_tabs_and_position_without_screenshot() -> None:
    js = build_browser_state_metadata_js()

    assert "page.context().pages()" in js
    assert "page_position" in js
    assert "pixels_below" in js
    assert "page.screenshot" not in js
