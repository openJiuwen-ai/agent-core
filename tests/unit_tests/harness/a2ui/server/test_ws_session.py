# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.harness.a2ui.server.ws_session."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.harness.a2ui.server.ws_session import (
    ConnectionSession,
    _describe_ui_actions,
    _extract_result,
    _translate,
    _unsent_suffix,
)


def _chunk(chunk_type, payload=None):
    return SimpleNamespace(type=chunk_type, payload=payload)


def _new_state():
    return {"last_llm_text": "", "any_text_sent": False, "last_presentation_text": "", "bubble_open": False}


class TestDescribeUiActions:
    def test_returns_empty_string_for_no_actions(self):
        assert _describe_ui_actions(None) == ""
        assert _describe_ui_actions([]) == ""

    def test_formats_action_name_and_context(self):
        actions = [{"action": {"name": "submit_prefs", "context": {"level": "Beginner"}}}]
        result = _describe_ui_actions(actions)
        assert "submit_prefs" in result
        assert "Beginner" in result


class TestExtractResult:
    def test_dict_result_returns_text_and_genui(self):
        text, genui_messages = _extract_result({"text": "hi", "genui": [{"a": 1}]})
        assert text == "hi"
        assert genui_messages == [{"a": 1}]

    def test_none_result_returns_empty(self):
        assert _extract_result(None) == ("", None)

    def test_non_dict_result_stringifies(self):
        assert _extract_result(42) == ("42", None)


class TestUnsentSuffix:
    def test_empty_final_text_returns_empty(self):
        assert _unsent_suffix("", "anything") == ""

    def test_identical_text_returns_empty(self):
        assert _unsent_suffix("hello", "hello") == ""

    def test_streamed_text_already_contains_final_returns_empty(self):
        assert _unsent_suffix("world", "hello wonderful world") == ""

    def test_returns_unsent_suffix_when_final_extends_streamed(self):
        assert _unsent_suffix("hello world", "hello ") == "world"

    def test_returns_full_text_when_unrelated_to_streamed(self):
        assert _unsent_suffix("a distinct final answer", "hello ") == "a distinct final answer"


class TestTranslate:
    def test_llm_output_emits_chat_token_and_accumulates_state(self):
        state = _new_state()
        events = _translate(_chunk("llm_output", {"content": "hello"}), state)
        assert events == [("chat.token", {"text": "hello"})]
        assert state["last_llm_text"] == "hello"
        assert state["any_text_sent"] is True

        events = _translate(_chunk("llm_output", {"content": " world"}), state)
        assert events == [("chat.token", {"text": " world"})]
        assert state["last_llm_text"] == "hello world"

    def test_llm_output_inserts_separator_between_turns_with_text(self):
        state = _new_state()
        # First turn streams a lead-in sentence.
        _translate(_chunk("llm_output", {"content": "Let me find some photos."}), state)
        # A tool call/result boundary resets the per-turn accumulator.
        _translate(_chunk("tool_call", {"tool_name": "search_images", "tool_call_id": "c1"}), state)
        _translate(
            _chunk(
                "tool_result",
                {"tool_name": "search_images", "tool_call_id": "c1", "tool_result": {"query": "q", "images": []}},
            ),
            state,
        )
        # A second turn streams its own text -- must not run into the first.
        events = _translate(_chunk("llm_output", {"content": "Here are some places."}), state)
        assert events == [
            ("chat.token", {"text": "\n\n"}),
            ("chat.token", {"text": "Here are some places."}),
        ]
        assert state["last_llm_text"] == "Here are some places."

    def test_llm_output_no_separator_for_first_turn(self):
        state = _new_state()
        events = _translate(_chunk("llm_output", {"content": "Let me find some photos."}), state)
        assert events == [("chat.token", {"text": "Let me find some photos."})]

    def test_llm_output_no_separator_after_a_card_already_rendered(self):
        # A genui-bearing tool_result (show_card/show_info_list/etc.) breaks
        # the client's text bubble on its own (a new `createSurface` starts a
        # fresh transcript row) -- so trailing "closing remarks" text after
        # the card must NOT get a leading separator, or that new bubble would
        # visibly start with two blank lines.
        state = _new_state()
        _translate(_chunk("llm_output", {"content": "Let me find some photos."}), state)
        _translate(_chunk("tool_call", {"tool_name": "show_info_list", "tool_call_id": "c1"}), state)
        _translate(
            _chunk(
                "tool_result",
                {
                    "tool_name": "show_info_list",
                    "tool_call_id": "c1",
                    "tool_result": {"text": "Top places\n- The Bund", "genui": [{"createSurface": {}}]},
                },
            ),
            state,
        )
        events = _translate(_chunk("llm_output", {"content": "Enjoy your trip!"}), state)
        assert events == [("chat.token", {"text": "Enjoy your trip!"})]

    def test_answer_emits_only_unsent_suffix(self):
        state = _new_state()
        _translate(_chunk("llm_output", {"content": "hello "}), state)
        events = _translate(_chunk("answer", {"output": "hello world"}), state)
        assert events == [("chat.token", {"text": "world"})]
        assert state["any_text_sent"] is True

    def test_answer_emits_nothing_when_fully_streamed(self):
        state = _new_state()
        _translate(_chunk("llm_output", {"content": "hello"}), state)
        events = _translate(_chunk("answer", {"output": "hello"}), state)
        assert events == []

    def test_tool_call_emits_tool_started_and_resets_streamed_text(self):
        state = _new_state()
        state["last_llm_text"] = "some preceding text"
        events = _translate(_chunk("tool_call", {"tool_name": "show_card", "tool_call_id": "c1"}), state)
        assert events == [("tool.started", {"tool": "show_card", "callId": "c1", "text": "Show card…"})]
        assert state["last_llm_text"] == ""

    def test_tool_call_progress_text_for_geocode_place(self):
        state = _new_state()
        events = _translate(
            _chunk("tool_call", {"tool_name": "geocode_place", "tool_call_id": "c1", "tool_args": {"query": "Maxwell Food Centre"}}),
            state,
        )
        assert events == [
            ("tool.started", {"tool": "geocode_place", "callId": "c1", "text": "Looking up Maxwell Food Centre…"})
        ]

    def test_tool_call_progress_text_for_show_map(self):
        state = _new_state()
        events = _translate(
            _chunk("tool_call", {"tool_name": "show_map", "tool_call_id": "c1", "tool_args": {"title": "Best eats"}}),
            state,
        )
        assert events == [
            ("tool.started", {"tool": "show_map", "callId": "c1", "text": "Building your map: Best eats…"})
        ]

    def test_geocode_batch_completion_reports_planning_text_only_once_all_resolve(self):
        # Three geocode_place calls dispatched (a parallel batch), then two
        # results come back -- the row must stay silent (no "text" key) on
        # tool.finished until the *last* outstanding call resolves.
        state = _new_state()
        for call_id in ("c1", "c2", "c3"):
            _translate(_chunk("tool_call", {"tool_name": "geocode_place", "tool_call_id": call_id}), state)
        assert state["geocode_pending"] == 3

        events1 = _translate(
            _chunk("tool_result", {"tool_name": "geocode_place", "tool_call_id": "c1", "tool_result": {}}), state
        )
        assert events1[0] == ("tool.finished", {"tool": "geocode_place", "callId": "c1"})
        assert state["geocode_pending"] == 2

        events2 = _translate(
            _chunk("tool_result", {"tool_name": "geocode_place", "tool_call_id": "c2", "tool_result": {}}), state
        )
        assert events2[0] == ("tool.finished", {"tool": "geocode_place", "callId": "c2"})
        assert state["geocode_pending"] == 1

        events3 = _translate(
            _chunk("tool_result", {"tool_name": "geocode_place", "tool_call_id": "c3", "tool_result": {}}), state
        )
        assert events3[0] == (
            "tool.finished",
            {"tool": "geocode_place", "callId": "c3", "text": "Got them all — planning your map…"},
        )
        assert state["geocode_pending"] == 0

    def test_non_geocode_tool_result_never_gets_planning_text(self):
        state = _new_state()
        events = _translate(
            _chunk("tool_result", {"tool_name": "show_map", "tool_call_id": "c1", "tool_result": {}}), state
        )
        assert events[0] == ("tool.finished", {"tool": "show_map", "callId": "c1"})

    def test_tool_result_emits_finished_output_and_genui(self):
        state = _new_state()
        payload = {
            "tool_name": "show_card",
            "tool_call_id": "c1",
            "tool_result": {"text": "answer", "genui": [{"createSurface": {}}]},
        }
        events = _translate(_chunk("tool_result", payload), state)
        assert events == [
            ("tool.finished", {"tool": "show_card", "callId": "c1"}),
            ("tool.output", {"tool": "show_card", "callId": "c1", "text": "answer"}),
            ("genui", {"createSurface": {}}),
        ]

    def test_tool_result_with_genui_records_presentation_text(self):
        state = _new_state()
        payload = {
            "tool_name": "show_info_list",
            "tool_call_id": "c1",
            "tool_result": {"text": "Top places\n- The Bund", "genui": [{"createSurface": {}}]},
        }
        _translate(_chunk("tool_result", payload), state)
        assert state["last_presentation_text"] == "Top places\n- The Bund"

    def test_tool_result_without_genui_does_not_record_presentation_text(self):
        # A data tool (e.g. search_images) has no genui -- its "text" (there
        # isn't one) must never masquerade as the reply.
        state = _new_state()
        payload = {
            "tool_name": "search_images",
            "tool_call_id": "c1",
            "tool_result": {"query": "Shanghai", "images": [{"image_url": "https://example.com/a.jpg"}]},
        }
        _translate(_chunk("tool_result", payload), state)
        assert state["last_presentation_text"] == ""

    def test_tool_result_presentation_text_keeps_the_latest(self):
        state = _new_state()
        _translate(
            _chunk(
                "tool_result",
                {"tool_name": "show_card", "tool_call_id": "c1", "tool_result": {"text": "first", "genui": [{}]}},
            ),
            state,
        )
        _translate(
            _chunk(
                "tool_result",
                {"tool_name": "show_card", "tool_call_id": "c2", "tool_result": {"text": "second", "genui": [{}]}},
            ),
            state,
        )
        assert state["last_presentation_text"] == "second"

    def test_tool_result_with_error_prefix_emits_error_tool_instead_of_output(self):
        state = _new_state()
        payload = {
            "tool_name": "fetch_webpage",
            "tool_call_id": "c2",
            "tool_result": "[ERROR] could not reach host",
        }
        events = _translate(_chunk("tool_result", payload), state)
        assert events == [
            ("tool.finished", {"tool": "fetch_webpage", "callId": "c2"}),
            ("error.tool", {"tool": "fetch_webpage", "callId": "c2", "message": "[ERROR] could not reach host"}),
        ]

    def test_tool_error_emits_finished_then_error_tool(self):
        state = _new_state()
        payload = {"tool_name": "browser_inspect_page", "tool_call_id": "c3", "message": "nav timeout"}
        events = _translate(_chunk("tool_error", payload), state)
        assert events == [
            ("tool.finished", {"tool": "browser_inspect_page", "callId": "c3"}),
            ("error.tool", {"tool": "browser_inspect_page", "callId": "c3", "message": "nav timeout"}),
        ]

    def test_unhandled_chunk_type_emits_nothing(self):
        state = _new_state()
        assert _translate(_chunk("llm_usage", {}), state) == []


class TestConnectionSessionDispatch:
    @pytest.mark.asyncio
    async def test_heartbeat_updates_timestamp_without_reply(self):
        websocket = SimpleNamespace(send_json=AsyncMock())
        session = ConnectionSession(websocket, agent=object(), user_id="u1")
        before = session.last_heartbeat

        await session._dispatch({"type": "heartbeat"})

        websocket.send_json.assert_not_awaited()
        assert session.last_heartbeat >= before

    @pytest.mark.asyncio
    async def test_unknown_message_type_sends_validation_error(self):
        websocket = SimpleNamespace(send_json=AsyncMock())
        session = ConnectionSession(websocket, agent=object(), user_id="u1")

        await session._dispatch({"type": "bogus", "conversationId": "c1"})

        websocket.send_json.assert_awaited_once()
        sent = websocket.send_json.await_args.args[0]
        assert sent["type"] == "error.validation"
        assert "bogus" in sent["payload"]["message"]

    @pytest.mark.asyncio
    async def test_chat_start_runs_agent_and_streams_events(self):
        websocket = SimpleNamespace(send_json=AsyncMock())
        session = ConnectionSession(websocket, agent=object(), user_id="u1")

        async def fake_stream(*args, **kwargs):
            yield _chunk("llm_output", {"content": "hi there"})

        with patch(
            "openjiuwen.harness.a2ui.server.ws_session.Runner.run_agent_streaming",
            side_effect=fake_stream,
        ):
            await session._dispatch({"type": "chat.start", "conversationId": "c1", "payload": {"text": "hi"}})
            assert session._active_task is not None
            await session._active_task

        sent_types = [call.args[0]["type"] for call in websocket.send_json.await_args_list]
        assert sent_types == ["chat.accepted", "chat.token", "chat.completed"]

    @pytest.mark.asyncio
    async def test_falls_back_to_presentation_text_when_model_says_nothing(self):
        websocket = SimpleNamespace(send_json=AsyncMock())
        session = ConnectionSession(websocket, agent=object(), user_id="u1")

        async def fake_stream(*args, **kwargs):
            yield _chunk("tool_call", {"tool_name": "show_info_list", "tool_call_id": "c1"})
            yield _chunk(
                "tool_result",
                {
                    "tool_name": "show_info_list",
                    "tool_call_id": "c1",
                    "tool_result": {"text": "Top places\n- The Bund", "genui": [{"createSurface": {}}]},
                },
            )

        with patch(
            "openjiuwen.harness.a2ui.server.ws_session.Runner.run_agent_streaming",
            side_effect=fake_stream,
        ):
            await session._dispatch({"type": "chat.start", "conversationId": "c1", "payload": {"text": "hi"}})
            await session._active_task

        sent = [(call.args[0]["type"], call.args[0]["payload"]) for call in websocket.send_json.await_args_list]
        assert ("chat.token", {"text": "Top places\n- The Bund"}) in sent
        # The fallback must come after the card's genui, and before chat.completed.
        types = [t for t, _ in sent]
        assert types.index("genui") < types.index("chat.token") < types.index("chat.completed")

    @pytest.mark.asyncio
    async def test_no_fallback_when_model_already_said_something(self):
        websocket = SimpleNamespace(send_json=AsyncMock())
        session = ConnectionSession(websocket, agent=object(), user_id="u1")

        async def fake_stream(*args, **kwargs):
            yield _chunk("tool_call", {"tool_name": "show_card", "tool_call_id": "c1"})
            yield _chunk(
                "tool_result",
                {
                    "tool_name": "show_card",
                    "tool_call_id": "c1",
                    "tool_result": {"text": "card text", "genui": [{"createSurface": {}}]},
                },
            )
            yield _chunk("llm_output", {"content": "Here's what I found."})

        with patch(
            "openjiuwen.harness.a2ui.server.ws_session.Runner.run_agent_streaming",
            side_effect=fake_stream,
        ):
            await session._dispatch({"type": "chat.start", "conversationId": "c1", "payload": {"text": "hi"}})
            await session._active_task

        sent_texts = [
            call.args[0]["payload"]["text"]
            for call in websocket.send_json.await_args_list
            if call.args[0]["type"] == "chat.token"
        ]
        assert sent_texts == ["Here's what I found."]

    @pytest.mark.asyncio
    async def test_second_chat_start_while_running_is_rejected(self):
        websocket = SimpleNamespace(send_json=AsyncMock())
        session = ConnectionSession(websocket, agent=object(), user_id="u1")
        session._active_task = SimpleNamespace(done=lambda: False)

        await session._dispatch({"type": "chat.start", "conversationId": "c1", "payload": {"text": "hi"}})

        sent = websocket.send_json.await_args.args[0]
        assert sent["type"] == "error.validation"
        assert "already running" in sent["payload"]["message"]
