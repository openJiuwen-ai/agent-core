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
    reconcile,
)
from openjiuwen.agent_teams.workflow.engine.errors import BackendError


def _creds(**over) -> ForkCredentials:
    base = dict(provider="codex", parent_member="r1-sess-c-0", parent_pointer="thr_p",
                mode="before", cut_marker="turn_2", keep_rounds=2)
    base.update(over)
    return ForkCredentials(**base)


@pytest.mark.parametrize("mode", ["after", "keep_before_compact_after", "keep_after_compact_before", "bogus"])
def test_unsupported_mode_is_rejected_with_the_supported_list(mode):
    with pytest.raises(BackendError) as exc:
        check_fork_mode("codex", mode)
    text = str(exc.value)
    assert mode in text and "full" in text and "before" in text


def test_cut_marker_selection_and_reconcile():
    """keep_rounds picks the parent marker at that round; reconcile demands it still exist.

    full never cuts; an out-of-range keep_rounds and a round that recorded no
    marker both refuse; a parent history that drifted away from the credential's
    cut marker no longer matches.
    """
    markers = ["m1", "m2", "m3"]
    assert cut_marker_for(markers, "full", None) is None
    assert cut_marker_for(markers, "before", 2) == "m2"
    with pytest.raises(BackendError, match="keep_rounds"):
        cut_marker_for(markers, "before", 4)
    with pytest.raises(BackendError, match="marker"):
        cut_marker_for(["m1", None], "before", 2)
    reconcile(_creds(), ["turn_1", "turn_2", "turn_3"])
    reconcile(_creds(mode="full", cut_marker=None), [])
    with pytest.raises(BackendError, match="no longer match"):
        reconcile(_creds(), ["turn_1", "turn_9"])


@pytest.mark.asyncio
async def test_claude_fork_uses_the_sdk_offline_fork_and_surfaces_failure(monkeypatch):
    """The Claude fork calls the SDK's offline fork at the cut; a failing SDK is wrapped."""
    seen: dict = {}

    class _Sdk:
        @staticmethod
        def fork_session(session_id, directory=None, up_to_message_id=None, title=None):
            if session_id == "sess_broken":
                raise FileNotFoundError("nowhere")
            seen.update(session_id=session_id, directory=directory, up_to=up_to_message_id)
            return type("R", (), {"session_id": "sess_child"})()

    monkeypatch.setattr(external_fork, "load_claude_sdk", lambda: _Sdk)
    entry = ExternalCliAgentSpec(cli_agent="claude")
    child = await external_fork._fork_claude(
        _creds(provider="claude", parent_pointer="sess_p", cut_marker="a-2"), entry=entry, cwd="C:/proj",
    )
    assert child == "sess_child"
    assert seen == {"session_id": "sess_p", "directory": "C:/proj", "up_to": "a-2"}
    with pytest.raises(BackendError, match="Claude fork failed"):
        await external_fork._fork_claude(_creds(provider="claude", parent_pointer="sess_broken"), entry=entry, cwd="C:/proj")


@pytest.mark.asyncio
async def test_codex_fork_calls_thread_fork_with_entry_env_and_last_turn_id(monkeypatch):
    """The Codex fork reaches the runtime's own app server (entry env, no
    inherited process env) and forks the parent thread at the cut turn."""
    monkeypatch.setattr(external_fork, "build_codex_config", lambda **kw: kw)
    entry = ExternalCliAgentSpec(cli_agent="codex", env={"CODEX_HOME": "X"})
    cfg = external_fork._codex_config_for(object(), entry, "C:/p")
    assert cfg["env"]["CODEX_HOME"] == "X"
    assert cfg["config"].inherit_process_env is False

    seen: dict = {}

    class _RawClient:
        def __init__(self, config=None):
            seen["lifecycle"] = []

        async def start(self):
            seen["lifecycle"].append("start")

        async def initialize(self):
            seen["lifecycle"].append("initialize")

        async def thread_fork(self, thread_id, params):
            seen.update(thread_id=thread_id, params=params)
            return type("R", (), {"thread": type("T", (), {"id": "thr_child"})()})()

        async def close(self):
            seen["lifecycle"].append("close")

    fake_sdk = type("S", (), {"async_client": type("M", (), {"AsyncCodexClient": _RawClient})})
    monkeypatch.setattr(external_fork, "load_codex_sdk", lambda: fake_sdk)
    monkeypatch.setattr(external_fork, "_codex_config_for", lambda sdk, entry, cwd: {"cwd": cwd})
    child = await external_fork._fork_codex(_creds(), entry=entry, cwd="C:/p")
    assert child == "thr_child"
    assert seen["thread_id"] == "thr_p"
    assert seen["params"] == {"lastTurnId": "turn_2", "cwd": "C:/p"}
    assert seen["lifecycle"] == ["start", "initialize", "close"]
