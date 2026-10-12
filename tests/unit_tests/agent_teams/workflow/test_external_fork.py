# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fork credentials, mode gating, cut-point selection and provider materialisation."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec
from openjiuwen.agent_teams.workflow.backends import external_fork
from openjiuwen.agent_teams.workflow.backends.external_fork import (
    ForkCredentials,
    check_fork_mode,
    cut_marker_for,
    materialise_external_fork,
    reconcile,
    supported_fork_modes,
)
from openjiuwen.agent_teams.workflow.engine.errors import BackendError


def _creds(**over) -> ForkCredentials:
    base = dict(provider="codex", parent_member="r1-sess-c-0", parent_pointer="thr_p",
                mode="before", cut_marker="turn_2", keep_rounds=2)
    base.update(over)
    return ForkCredentials(**base)


def test_supported_modes_come_from_the_provider_card():
    assert supported_fork_modes("claude") == frozenset({"full", "before"})
    assert supported_fork_modes("codex") == frozenset({"full", "before"})
    assert supported_fork_modes("gemini") == frozenset()


@pytest.mark.parametrize("mode", ["after", "keep_before_compact_after", "keep_after_compact_before", "bogus"])
def test_unsupported_mode_is_rejected_with_the_supported_list(mode):
    with pytest.raises(BackendError) as exc:
        check_fork_mode("codex", mode)
    text = str(exc.value)
    assert mode in text and "full" in text and "before" in text


def test_supported_modes_pass():
    check_fork_mode("codex", "full")
    check_fork_mode("claude", "before")


def test_cut_marker_selection():
    markers = ["m1", "m2", "m3"]
    assert cut_marker_for(markers, "full", None) is None
    assert cut_marker_for(markers, "before", 2) == "m2"
    assert cut_marker_for(markers, "before", 3) == "m3"
    with pytest.raises(BackendError, match="keep_rounds"):
        cut_marker_for(markers, "before", 4)
    with pytest.raises(BackendError, match="marker"):
        cut_marker_for(["m1", None], "before", 2)


def test_credentials_round_trip_through_fork_data():
    creds = _creds()
    data = creds.to_fork_data()
    assert set(data) == {"external_fork"}
    assert ForkCredentials.from_fork_data(data) == creds
    assert ForkCredentials.from_fork_data({"messages": []}) is None
    assert ForkCredentials.from_fork_data(None) is None


def test_reconcile_requires_the_cut_marker_in_the_parent_history():
    reconcile(_creds(), ["turn_1", "turn_2", "turn_3"])
    reconcile(_creds(mode="full", cut_marker=None), [])
    with pytest.raises(BackendError, match="no longer match"):
        reconcile(_creds(), ["turn_1", "turn_9"])


@pytest.mark.asyncio
async def test_materialise_dispatches_to_codex(monkeypatch):
    calls: list = []

    async def _fake(creds, *, entry, cwd):
        calls.append((creds, entry, cwd))
        return "thr_child"

    monkeypatch.setattr(external_fork, "_fork_codex", _fake)
    entry = ExternalCliAgentSpec(cli_agent="codex")
    child = await materialise_external_fork(_creds(), entry=entry, cwd="C:/p")
    assert child == "thr_child"
    assert calls[0][1] is entry and calls[0][2] == "C:/p"


@pytest.mark.asyncio
async def test_materialise_dispatches_to_claude(monkeypatch):
    async def _fake(creds, *, entry, cwd):
        return "sess_child"

    monkeypatch.setattr(external_fork, "_fork_claude", _fake)
    child = await materialise_external_fork(_creds(provider="claude", parent_pointer="sess_p", cut_marker="a-2"),
                                            entry=ExternalCliAgentSpec(cli_agent="claude"), cwd=None)
    assert child == "sess_child"


@pytest.mark.asyncio
async def test_claude_fork_uses_the_sdk_offline_fork(monkeypatch):
    seen: dict = {}

    class _Sdk:
        @staticmethod
        def fork_session(session_id, directory=None, up_to_message_id=None, title=None):
            seen.update(session_id=session_id, directory=directory, up_to=up_to_message_id)
            return type("R", (), {"session_id": "sess_child"})()

    monkeypatch.setattr(external_fork, "load_claude_sdk", lambda: _Sdk)
    child = await external_fork._fork_claude(
        _creds(provider="claude", parent_pointer="sess_p", cut_marker="a-2"),
        entry=ExternalCliAgentSpec(cli_agent="claude"), cwd="C:/proj",
    )
    assert child == "sess_child"
    assert seen == {"session_id": "sess_p", "directory": "C:/proj", "up_to": "a-2"}


@pytest.mark.asyncio
async def test_claude_full_fork_passes_no_cut(monkeypatch):
    seen: dict = {}

    class _Sdk:
        @staticmethod
        def fork_session(session_id, directory=None, up_to_message_id=None, title=None):
            seen["up_to"] = up_to_message_id
            return type("R", (), {"session_id": "sess_child"})()

    monkeypatch.setattr(external_fork, "load_claude_sdk", lambda: _Sdk)
    await external_fork._fork_claude(_creds(provider="claude", parent_pointer="sess_p", mode="full", cut_marker=None),
                                     entry=ExternalCliAgentSpec(cli_agent="claude"), cwd=None)
    assert seen == {"up_to": None}


@pytest.mark.asyncio
async def test_claude_fork_retries_without_a_directory(monkeypatch):
    """The recorded cwd may not be the project dir the transcript lives under."""
    calls: list = []

    class _Sdk:
        @staticmethod
        def fork_session(session_id, directory=None, up_to_message_id=None, title=None):
            calls.append(directory)
            if len(calls) == 1:
                raise FileNotFoundError("no transcript under C:/proj")
            return type("R", (), {"session_id": "sess_child"})()

    monkeypatch.setattr(external_fork, "load_claude_sdk", lambda: _Sdk)
    child = await external_fork._fork_claude(
        _creds(provider="claude", parent_pointer="sess_p", cut_marker="a-2"),
        entry=ExternalCliAgentSpec(cli_agent="claude"), cwd="C:/proj",
    )
    assert child == "sess_child"
    assert calls == ["C:/proj", None]


@pytest.mark.asyncio
async def test_claude_fork_gives_up_when_the_directory_free_retry_also_fails(monkeypatch):
    class _Sdk:
        @staticmethod
        def fork_session(session_id, directory=None, up_to_message_id=None, title=None):
            raise FileNotFoundError("nowhere")

    monkeypatch.setattr(external_fork, "load_claude_sdk", lambda: _Sdk)
    with pytest.raises(BackendError, match="Claude fork failed"):
        await external_fork._fork_claude(
            _creds(provider="claude", parent_pointer="sess_p"),
            entry=ExternalCliAgentSpec(cli_agent="claude"), cwd="C:/proj",
        )


def test_codex_fork_client_carries_the_entry_env(monkeypatch):
    """The fork client must reach the same app server as the runtime, or the thread is not addressable."""
    monkeypatch.setattr(external_fork, "build_codex_config", lambda **kw: kw)
    entry = ExternalCliAgentSpec(cli_agent="codex", env={"CODEX_HOME": "X"})
    result = external_fork._codex_config_for(object(), entry, "C:/p")
    assert result["env"]["CODEX_HOME"] == "X"
    assert result["config"].inherit_process_env is False


@pytest.mark.asyncio
async def test_codex_fork_calls_thread_fork_with_last_turn_id(monkeypatch):
    seen: dict = {}

    class _RawClient:
        async def thread_fork(self, thread_id, params):
            seen.update(thread_id=thread_id, params=params)
            return type("R", (), {"thread": type("T", (), {"id": "thr_child"})()})()

    class _Api:
        def __init__(self, config=None):
            self._client = _RawClient()
            seen["config"] = config

        async def _ensure_initialized(self):
            return None

        async def close(self):
            seen["closed"] = True

    class _Sdk:
        AsyncCodex = _Api

    monkeypatch.setattr(external_fork, "load_codex_sdk", lambda: _Sdk)
    monkeypatch.setattr(external_fork, "_codex_config_for", lambda sdk, entry, cwd: {"cwd": cwd})
    child = await external_fork._fork_codex(_creds(), entry=ExternalCliAgentSpec(cli_agent="codex"), cwd="C:/p")
    assert child == "thr_child"
    assert seen["thread_id"] == "thr_p"
    assert seen["params"] == {"lastTurnId": "turn_2", "cwd": "C:/p"}
    assert seen["closed"] is True


@pytest.mark.asyncio
async def test_codex_full_fork_omits_last_turn_id(monkeypatch):
    seen: dict = {}

    class _RawClient:
        async def thread_fork(self, thread_id, params):
            seen["params"] = params
            return type("R", (), {"thread": type("T", (), {"id": "thr_child"})()})()

    class _Api:
        def __init__(self, config=None):
            self._client = _RawClient()

        async def _ensure_initialized(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(external_fork, "load_codex_sdk", lambda: type("S", (), {"AsyncCodex": _Api}))
    monkeypatch.setattr(external_fork, "_codex_config_for", lambda sdk, entry, cwd: {})
    await external_fork._fork_codex(_creds(mode="full", cut_marker=None), entry=ExternalCliAgentSpec(cli_agent="codex"), cwd=None)
    assert seen["params"] == {}
