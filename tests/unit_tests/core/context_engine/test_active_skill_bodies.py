# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for active skill body record/unregister and pin injection."""
from __future__ import annotations

from typing import Any, Dict

import pytest

from openjiuwen.core.context_engine.active_skill_bodies import (
    ACTIVE_SKILL_BODIES_STATE_KEY,
    ACTIVE_SKILL_EVICTION_SIGNATURE_STATE_KEY,
    ACTIVE_SKILL_EVICTIONS_STATE_KEY,
    ACTIVE_SKILL_HINTS_STATE_KEY,
    append_active_skill_pins_to_window,
    derive_hints_from_session,
    normalize_skill_relative_file_path,
    pop_active_skill_hints_for_session,
    record_active_skill_body,
    stage_active_skill_hints_for_session,
    unregister_active_skill_body,
)
from openjiuwen.core.foundation.llm import SystemMessage, ToolMessage, UserMessage


class _FakeSession:
    """Minimal session double with get_state / update_state."""

    def __init__(self, session_id: str = "sess-1"):
        self._sid = session_id
        self._state: Dict[str, Any] = {}

    def get_session_id(self) -> str:
        return self._sid

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    def update_state(self, data: Dict[str, Any]) -> None:
        self._state.update(data)


class _FakeWindow:
    def __init__(self):
        self.system_messages = []
        self.context_messages = []


class _FakeContext:
    def __init__(self, session: _FakeSession):
        self._session = session

    def get_session_ref(self) -> _FakeSession:
        return self._session


def _make_skill_tool_message(name: str, path: str = "SKILL.md") -> ToolMessage:
    return ToolMessage(
        content="full body",
        tool_call_id=f"tc-{name}",
        metadata={
            "is_skill_body": True,
            "skill_name": name,
            "relative_file_path": path,
        },
    )


def _make_stub_tool_message(
    name: str,
    path: str = "SKILL.md",
    *,
    active: bool = True,
) -> ToolMessage:
    """Mirror the shape SkillUseRail writes onto the original ToolMessage after
    a successful skill body record (load stub) or after skill_complete (unload
    stub when ``active=False``)."""
    return ToolMessage(
        content="[SKILL LOADED] ..." if active else "[SKILL UNLOADED] ...",
        tool_call_id=f"tc-{name}",
        metadata={
            "is_skill_body": False,
            "skill_body_stub": True,
            "skill_body_active": active,
            "original_is_skill_body": True,
            "skill_name": name,
            "relative_file_path": path,
        },
    )


class _FakeToolOutput:
    def __init__(self, body: str):
        self.data = {"skill_content": body}


@pytest.mark.unit
class TestNormalizeSkillRelativeFilePath:
    def test_bare_skill_becomes_skill_md(self):
        assert normalize_skill_relative_file_path("SKILL") == "SKILL.md"
        assert normalize_skill_relative_file_path("skill") == "SKILL.md"
        assert normalize_skill_relative_file_path("") == "SKILL.md"

    def test_preserves_other_paths(self):
        assert normalize_skill_relative_file_path("SKILL.md") == "SKILL.md"
        assert normalize_skill_relative_file_path("docs/REF.md") == "docs/REF.md"


@pytest.mark.unit
class TestRecordActiveSkillBody:
    def test_record_normalizes_bare_skill_path_in_state_key(self):
        sess = _FakeSession()
        recorded = record_active_skill_body(
            sess,
            _make_skill_tool_message("pptx-craft", "SKILL"),
            _FakeToolOutput("BODY"),
            max_active_skill_bodies=1,
        )
        assert recorded is True
        active = sess.get_state(ACTIVE_SKILL_BODIES_STATE_KEY)
        # State key uses ``\x01`` as name/path separator and encodes ``.`` as
        # ``\x02`` so ``session.update_state`` won't split it into nested dicts.
        assert list(active.keys()) == ["pptx-craft\x01SKILL\x02md"]
        assert next(iter(active.values()))["relative_file_path"] == "SKILL.md"

    def test_record_writes_session_state(self):
        sess = _FakeSession()
        recorded = record_active_skill_body(
            sess,
            _make_skill_tool_message("alpha"),
            _FakeToolOutput("ALPHA BODY"),
            max_active_skill_bodies=1,
        )
        assert recorded is True
        active = sess.get_state(ACTIVE_SKILL_BODIES_STATE_KEY)
        assert active and any(v["skill_name"] == "alpha" for v in active.values())
        assert any(v["body"] == "ALPHA BODY" for v in active.values())

    def test_record_disabled_when_max_zero(self):
        sess = _FakeSession()
        recorded = record_active_skill_body(
            sess,
            _make_skill_tool_message("alpha"),
            _FakeToolOutput("ALPHA"),
            max_active_skill_bodies=0,
        )
        assert recorded is False
        assert not sess.get_state(ACTIVE_SKILL_BODIES_STATE_KEY)

    def test_record_evicts_oldest_when_over_cap(self):
        sess = _FakeSession()
        record_active_skill_body(
            sess, _make_skill_tool_message("alpha"),
            _FakeToolOutput("AAA"), max_active_skill_bodies=1,
        )
        record_active_skill_body(
            sess, _make_skill_tool_message("beta"),
            _FakeToolOutput("BBB"), max_active_skill_bodies=1,
        )
        active = sess.get_state(ACTIVE_SKILL_BODIES_STATE_KEY)
        assert len(active) == 1
        assert next(iter(active.values()))["skill_name"] == "beta"
        evictions = sess.get_state(ACTIVE_SKILL_EVICTIONS_STATE_KEY)
        assert evictions and any(v["skill_name"] == "alpha" for v in evictions.values())

    def test_record_rejects_non_skill_body_message(self):
        sess = _FakeSession()
        msg = ToolMessage(content="x", tool_call_id="t1", metadata={})
        assert record_active_skill_body(
            sess, msg, _FakeToolOutput("body"), max_active_skill_bodies=1
        ) is False

    def test_record_requires_structured_skill_content(self):
        sess = _FakeSession()
        # data not dict -> no body extracted -> not recorded
        result = type("R", (), {"data": "raw string"})()
        assert record_active_skill_body(
            sess, _make_skill_tool_message("alpha"), result, max_active_skill_bodies=1
        ) is False


@pytest.mark.unit
class TestUnregisterActiveSkillBody:
    def test_unregister_removes_all_paths_under_skill(self):
        sess = _FakeSession()
        record_active_skill_body(
            sess, _make_skill_tool_message("alpha", "SKILL.md"),
            _FakeToolOutput("a1"), max_active_skill_bodies=2,
        )
        record_active_skill_body(
            sess, _make_skill_tool_message("alpha", "REF.md"),
            _FakeToolOutput("a2"), max_active_skill_bodies=2,
        )
        removed = unregister_active_skill_body(sess, "alpha")
        assert removed == 2
        assert not sess.get_state(ACTIVE_SKILL_BODIES_STATE_KEY)

    def test_unregister_idempotent_on_missing(self):
        sess = _FakeSession()
        assert unregister_active_skill_body(sess, "ghost") == 0


@pytest.mark.unit
class TestAppendActiveSkillPinsToWindow:
    def test_pin_target_system_appends_to_system_messages(self):
        sess = _FakeSession()
        record_active_skill_body(
            sess, _make_skill_tool_message("alpha"),
            _FakeToolOutput("ALPHA BODY"), max_active_skill_bodies=1,
        )
        ctx = _FakeContext(sess)
        win = _FakeWindow()
        new_pins = append_active_skill_pins_to_window(
            ctx, win, max_active_skill_bodies=1, pin_target="system"
        )
        assert new_pins, "expected at least one pin appended"
        assert any(
            isinstance(m, SystemMessage)
            and m.metadata.get("active_skill_pin")
            and m.metadata.get("skill_name") == "alpha"
            for m in win.system_messages
        )

    def test_pin_target_user_prefix_inserts_before_first_user(self):
        sess = _FakeSession()
        record_active_skill_body(
            sess, _make_skill_tool_message("alpha"),
            _FakeToolOutput("ALPHA"), max_active_skill_bodies=1,
        )
        ctx = _FakeContext(sess)
        win = _FakeWindow()
        original_user = UserMessage(content="hi")
        win.context_messages = [original_user]
        new_pins = append_active_skill_pins_to_window(
            ctx, win, max_active_skill_bodies=1, pin_target="user_prefix"
        )
        assert new_pins
        # pin should appear strictly before the original user message
        assert win.context_messages[-1] is original_user
        assert win.context_messages[0].metadata.get("active_skill_pin") is True

    def test_eviction_signature_dedupes_repeat_notice(self):
        sess = _FakeSession()
        # cap = 1, two records to force eviction
        record_active_skill_body(
            sess, _make_skill_tool_message("alpha"),
            _FakeToolOutput("a"), max_active_skill_bodies=1,
        )
        record_active_skill_body(
            sess, _make_skill_tool_message("beta"),
            _FakeToolOutput("b"), max_active_skill_bodies=1,
        )
        ctx = _FakeContext(sess)

        win1 = _FakeWindow()
        new1 = append_active_skill_pins_to_window(
            ctx, win1, max_active_skill_bodies=1, pin_target="system"
        )
        notice_count_first = sum(
            1 for m in win1.system_messages
            if m.metadata.get("active_skill_eviction_notice")
        )
        assert notice_count_first == 1
        assert sess.get_state(ACTIVE_SKILL_EVICTION_SIGNATURE_STATE_KEY)

        win2 = _FakeWindow()
        new2 = append_active_skill_pins_to_window(
            ctx, win2, max_active_skill_bodies=1, pin_target="system"
        )
        notice_count_second = sum(
            1 for m in win2.system_messages
            if m.metadata.get("active_skill_eviction_notice")
        )
        # Same eviction signature -> no repeated notice on second window.
        assert notice_count_second == 0

    def test_disabled_when_max_zero(self):
        sess = _FakeSession()
        record_active_skill_body(
            sess, _make_skill_tool_message("alpha"),
            _FakeToolOutput("a"), max_active_skill_bodies=1,
        )
        ctx = _FakeContext(sess)
        win = _FakeWindow()
        new_pins = append_active_skill_pins_to_window(
            ctx, win, max_active_skill_bodies=0, pin_target="system"
        )
        assert new_pins == []
        assert win.system_messages == []

    def test_append_migrates_legacy_skill_key_without_md(self):
        sess = _FakeSession()
        legacy_key = "pptx-craft\x00SKILL"
        sess.update_state({
            ACTIVE_SKILL_BODIES_STATE_KEY: {
                legacy_key: {
                    "skill_name": "pptx-craft",
                    "relative_file_path": "SKILL",
                    "body": "X",
                    "tool_call_id": "t1",
                    "invoked_at": 1.0,
                    "source_session_id": "sess-1",
                },
            },
        })
        ctx = _FakeContext(sess)
        win = _FakeWindow()
        append_active_skill_pins_to_window(
            ctx, win, max_active_skill_bodies=1, pin_target="system"
        )
        active = sess.get_state(ACTIVE_SKILL_BODIES_STATE_KEY)
        assert legacy_key not in active
        # New key format: ``\x01`` separator, ``.`` encoded as ``\x02``.
        assert "pptx-craft\x01SKILL\x02md" in active
        assert active["pptx-craft\x01SKILL\x02md"]["relative_file_path"] == "SKILL.md"
        pin = next(
            m for m in win.system_messages
            if m.metadata.get("active_skill_pin") and not m.metadata.get("active_skill_eviction_notice")
        )
        assert pin.metadata.get("relative_file_path") == "SKILL.md"


@pytest.mark.unit
class TestAppendActiveSkillPinsAfterSkillTool:
    """``pin_target='after_skill_tool'`` anchors each body pin right after its
    matching stub ToolMessage, falling back to user_prefix when the stub is no
    longer in the window. This mirrors claude-code's
    [assistant(tool_use), tool_result(short ack), user(SKILL.md body)] layout.
    """

    def _record_and_window(
        self,
        sess,
        ctx_messages,
        skill_specs,
    ):
        for name, body in skill_specs:
            record_active_skill_body(
                sess, _make_skill_tool_message(name),
                _FakeToolOutput(body), max_active_skill_bodies=max(1, len(skill_specs)),
            )
        win = _FakeWindow()
        win.context_messages = list(ctx_messages)
        return win

    def _body_pin(self, msgs, skill_name):
        for m in msgs:
            meta = getattr(m, "metadata", None) or {}
            if (
                meta.get("active_skill_pin")
                and meta.get("skill_name") == skill_name
                and not meta.get("active_skill_eviction_notice")
            ):
                return m
        return None

    def test_pin_inserts_right_after_matching_stub(self):
        sess = _FakeSession()
        original_user = UserMessage(content="please make a deck")
        stub = _make_stub_tool_message("alpha")
        next_msg = ToolMessage(content="follow up", tool_call_id="tc-other", metadata={})
        win = self._record_and_window(
            sess, [original_user, stub, next_msg], [("alpha", "ALPHA BODY")]
        )
        new_pins = append_active_skill_pins_to_window(
            _FakeContext(sess), win,
            max_active_skill_bodies=len(win.context_messages),
            pin_target="after_skill_tool",
        )
        assert new_pins, "expected a pin to be appended"

        msgs = win.context_messages
        stub_idx = msgs.index(stub)
        # Pin must be the immediate next message after the stub.
        pin = msgs[stub_idx + 1]
        assert isinstance(pin, UserMessage)
        assert pin.metadata.get("active_skill_pin") is True
        assert pin.metadata.get("skill_name") == "alpha"
        assert pin.metadata.get("pin_anchor") == "after_skill_tool"
        assert "ALPHA BODY" in (pin.content or "")
        # Surrounding order is preserved.
        assert msgs[0] is original_user
        assert msgs[stub_idx + 2] is next_msg

    def test_pin_falls_back_to_user_prefix_when_stub_missing(self):
        sess = _FakeSession()
        original_user = UserMessage(content="please make a deck")
        unrelated = ToolMessage(content="other", tool_call_id="tc-x", metadata={})
        win = self._record_and_window(
            sess, [original_user, unrelated], [("alpha", "ALPHA BODY")]
        )
        new_pins = append_active_skill_pins_to_window(
            _FakeContext(sess), win,
            max_active_skill_bodies=1,
            pin_target="after_skill_tool",
        )
        assert new_pins
        # Pin lives at user_prefix position (before the original UserMessage).
        first = win.context_messages[0]
        assert isinstance(first, UserMessage)
        assert first.metadata.get("active_skill_pin") is True
        assert first.metadata.get("pin_anchor") == "user_prefix_fallback"
        # Original user message is preserved right after the pin.
        assert win.context_messages[1] is original_user

    def test_multiple_skills_each_anchored_independently(self):
        sess = _FakeSession()
        u = UserMessage(content="multi-skill task")
        stub_a = _make_stub_tool_message("alpha")
        between = ToolMessage(content="step", tool_call_id="tc-mid", metadata={})
        stub_b = _make_stub_tool_message("beta")
        tail = ToolMessage(content="tail", tool_call_id="tc-tail", metadata={})
        win = self._record_and_window(
            sess,
            [u, stub_a, between, stub_b, tail],
            [("alpha", "ALPHA"), ("beta", "BETA")],
        )
        new_pins = append_active_skill_pins_to_window(
            _FakeContext(sess), win,
            max_active_skill_bodies=2,
            pin_target="after_skill_tool",
        )
        assert len(new_pins) == 2

        msgs = win.context_messages
        stub_a_idx = msgs.index(stub_a)
        stub_b_idx = msgs.index(stub_b)
        # Each pin sits immediately after its own stub.
        assert msgs[stub_a_idx + 1].metadata.get("skill_name") == "alpha"
        assert msgs[stub_b_idx + 1].metadata.get("skill_name") == "beta"
        # Between marker (the original ToolMessage between stubs) survives.
        assert between in msgs
        assert tail in msgs

    def test_eviction_notice_anchored_at_user_prefix_even_with_after_target(self):
        sess = _FakeSession()
        # Force eviction with cap=1 and two records.
        record_active_skill_body(
            sess, _make_skill_tool_message("alpha"),
            _FakeToolOutput("AAA"), max_active_skill_bodies=1,
        )
        record_active_skill_body(
            sess, _make_skill_tool_message("beta"),
            _FakeToolOutput("BBB"), max_active_skill_bodies=1,
        )
        u = UserMessage(content="task")
        stub_b = _make_stub_tool_message("beta")
        win = _FakeWindow()
        win.context_messages = [u, stub_b]
        append_active_skill_pins_to_window(
            _FakeContext(sess), win,
            max_active_skill_bodies=1,
            pin_target="after_skill_tool",
        )
        msgs = win.context_messages
        # The eviction notice (for alpha) must appear at user_prefix, not after stub_b.
        notice = next(
            (m for m in msgs if (m.metadata or {}).get("active_skill_eviction_notice")),
            None,
        )
        assert notice is not None
        assert msgs.index(notice) < msgs.index(u)
        # Beta's body pin still anchored to its stub.
        beta_pin = self._body_pin(msgs, "beta")
        assert beta_pin is not None
        assert msgs.index(beta_pin) == msgs.index(stub_b) + 1

    def test_pin_anchors_to_most_recent_stub_on_reload(self):
        """Same skill loaded twice without skill_complete: window has two live
        load stubs (both ``skill_body_active=True``). Pin must anchor to the
        *latest* stub, since the registry holds the latest body and the model
        just saw the latest tool_result.
        """
        sess = _FakeSession()
        # First load: record then simulate the rail's stub of the original.
        record_active_skill_body(
            sess, _make_skill_tool_message("alpha"),
            _FakeToolOutput("OLD BODY"), max_active_skill_bodies=1,
        )
        # Second load (reload before unload): registry overwrites with new body
        # and stores the *new* tool_call_id.
        reload_msg = ToolMessage(
            content="full body",
            tool_call_id="tc-alpha-RELOAD",
            metadata={
                "is_skill_body": True,
                "skill_name": "alpha",
                "relative_file_path": "SKILL.md",
            },
        )
        record_active_skill_body(
            sess, reload_msg, _FakeToolOutput("NEW BODY"),
            max_active_skill_bodies=1,
        )

        # Window contains BOTH live stubs (no skill_complete fired between).
        u = UserMessage(content="task")
        old_stub = _make_stub_tool_message("alpha")  # tool_call_id = "tc-alpha"
        between = ToolMessage(content="step", tool_call_id="tc-mid", metadata={})
        new_stub = ToolMessage(
            content="[SKILL LOADED] ...",
            tool_call_id="tc-alpha-RELOAD",
            metadata={
                "is_skill_body": False,
                "skill_body_stub": True,
                "skill_body_active": True,
                "original_is_skill_body": True,
                "skill_name": "alpha",
                "relative_file_path": "SKILL.md",
            },
        )
        win = _FakeWindow()
        win.context_messages = [u, old_stub, between, new_stub]

        new_pins = append_active_skill_pins_to_window(
            _FakeContext(sess), win,
            max_active_skill_bodies=1,
            pin_target="after_skill_tool",
        )
        assert len(new_pins) == 1
        msgs = win.context_messages
        new_stub_idx = msgs.index(new_stub)
        old_stub_idx = msgs.index(old_stub)
        pin = msgs[new_stub_idx + 1]
        assert pin.metadata.get("active_skill_pin") is True
        assert pin.metadata.get("skill_name") == "alpha"
        assert pin.metadata.get("pin_anchor") == "after_skill_tool"
        # Body is the *latest* one.
        assert "NEW BODY" in (pin.content or "")
        assert "OLD BODY" not in (pin.content or "")
        # Pin tracks the latest tool_call_id, not the old one.
        assert pin.metadata.get("source_tool_call_id") == "tc-alpha-RELOAD"
        # Critically: pin sits AFTER the new stub, NOT after the old stub.
        assert msgs.index(pin) > old_stub_idx + 1

@pytest.mark.unit
class TestActiveSkillHintStaging:
    def test_stage_and_pop(self):
        stage_active_skill_hints_for_session(
            "child-1",
            [{"skill_name": "alpha", "relative_file_path": "SKILL.md"}],
        )
        hints = pop_active_skill_hints_for_session("child-1")
        assert hints == [{"skill_name": "alpha", "relative_file_path": "SKILL.md"}]
        # Second pop yields empty.
        assert pop_active_skill_hints_for_session("child-1") == []

    def test_derive_hints_from_session(self):
        sess = _FakeSession()
        record_active_skill_body(
            sess, _make_skill_tool_message("alpha"),
            _FakeToolOutput("a"), max_active_skill_bodies=2,
        )
        record_active_skill_body(
            sess, _make_skill_tool_message("beta"),
            _FakeToolOutput("b"), max_active_skill_bodies=2,
        )
        hints = derive_hints_from_session(sess)
        names = sorted(h["skill_name"] for h in hints)
        assert names == ["alpha", "beta"]
        assert all("body" not in h for h in hints)
