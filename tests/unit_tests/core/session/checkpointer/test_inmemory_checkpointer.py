# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.agent import AgentSession


async def _add_agent_store(checkpointer: InMemoryCheckpointer, session_id: str) -> None:
    session = AgentSession(
        session_id=session_id,
        config=Config(),
        card=SimpleNamespace(id=f"agent-{session_id}"),
    )
    await checkpointer.pre_agent_execute(session, None)


@pytest.mark.asyncio
async def test_runner_release_removes_exact_session_and_colon_children_only(monkeypatch):
    checkpointer = InMemoryCheckpointer()
    for session_id in ("chat-1", "chat-1:child", "chat-10", "chat-11", "chat-1xyz"):
        await _add_agent_store(checkpointer, session_id)

    monkeypatch.setattr(CheckpointerFactory, "get_checkpointer", lambda: checkpointer)
    monkeypatch.setattr(
        "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
        AsyncMock(return_value=None),
    )

    await Runner.release("chat-1")

    assert set(checkpointer._agent_stores) == {"chat-10", "chat-11", "chat-1xyz"}

    await Runner.release("chat-1")

    assert set(checkpointer._agent_stores) == {"chat-10", "chat-11", "chat-1xyz"}


@pytest.mark.asyncio
async def test_release_for_agent_keeps_session_store():
    checkpointer = InMemoryCheckpointer()
    await _add_agent_store(checkpointer, "chat-1")
    agent_store = checkpointer._agent_stores["chat-1"]

    await checkpointer.release("chat-1", agent_id="agent-chat-1")

    assert checkpointer._agent_stores["chat-1"] is agent_store


@pytest.mark.parametrize(
    "import_stmt",
    [
        "from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer",
        "from openjiuwen.harness.factory import create_deep_agent",
    ],
)
def test_core_and_harness_imports_do_not_eagerly_import_pymilvus_or_load_dotenv(tmp_path: Path, import_stmt: str):
    sentinel_key = "OPENJIUWEN_IMPORT_DOTENV_SENTINEL"
    (tmp_path / ".env").write_text(f"{sentinel_key}=should_not_be_loaded\n", encoding="utf-8")

    repo_root = str(Path(__file__).resolve().parents[5])
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONPATH": os.pathsep.join(
            [repo_root, *(p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p)]
        ),
    }
    probe = (
        "import json, os, sys, dotenv\n"
        "calls = []\n"
        "orig = dotenv.load_dotenv\n"
        "dotenv.load_dotenv = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]\n"
        f"{import_stmt}\n"
        "sys.stdout.write(json.dumps({\n"
        "    'pymilvus_loaded': 'pymilvus' in sys.modules,\n"
        "    'dotenv_calls': len(calls),\n"
        f"    'sentinel': os.environ.get({sentinel_key!r}),\n"
        "}) + '\\n')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload == {
        "pymilvus_loaded": False,
        "dotenv_calls": 0,
        "sentinel": None,
    }
