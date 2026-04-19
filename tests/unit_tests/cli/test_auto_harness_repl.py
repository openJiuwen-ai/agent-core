# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_auto_harness_repl — /auto-harness 自然语言入口测试。"""

from __future__ import annotations

import importlib
import os
import sys
import types
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from openjiuwen.core.session.stream.base import (
    OutputSchema,
)


def _install_prompt_toolkit_stubs() -> dict[str, types.ModuleType]:
    prompt_toolkit = types.ModuleType(
        "prompt_toolkit"
    )
    completion = types.ModuleType(
        "prompt_toolkit.completion"
    )
    document = types.ModuleType(
        "prompt_toolkit.document"
    )
    history = types.ModuleType(
        "prompt_toolkit.history"
    )

    class PromptSession: ...

    class Completer: ...

    class Completion:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    class Document: ...

    class FileHistory:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    prompt_toolkit.PromptSession = PromptSession
    completion.Completer = Completer
    completion.Completion = Completion
    document.Document = Document
    history.FileHistory = FileHistory

    return {
        "prompt_toolkit": prompt_toolkit,
        "prompt_toolkit.completion": completion,
        "prompt_toolkit.document": document,
        "prompt_toolkit.history": history,
    }


def _import_repl_module():
    stubs = _install_prompt_toolkit_stubs()
    with patch.dict(sys.modules, stubs):
        sys.modules.pop(
            "openjiuwen.harness.cli.ui.repl", None,
        )
        return importlib.import_module(
            "openjiuwen.harness.cli.ui.repl"
        )


class TestAutoHarnessRepl:
    """验证 REPL 中 auto-harness 的入口行为。"""

    @pytest.mark.asyncio
    async def test_subcmd_run_goal_keeps_full_flow(
        self, tmp_path,
    ) -> None:
        """--goal 应保留 tasks=None，走 assess→plan。"""
        repl = _import_repl_module()

        captured_config = None
        received_tasks = "NOT_SET"

        def _capture_create(config):
            nonlocal captured_config
            captured_config = config

            async def _fake_stream(tasks=None):
                nonlocal received_tasks
                received_tasks = tasks
                yield OutputSchema(
                    type="message",
                    index=0,
                    payload={"content": "ok"},
                )

            mock_orch = MagicMock()
            mock_orch.run_session_stream = _fake_stream
            mock_orch.results = []
            return mock_orch

        with patch(
            "openjiuwen.auto_harness.orchestrator"
            ".create_auto_harness_orchestrator",
            side_effect=_capture_create,
        ):
            with patch.dict(
                sys.modules,
                _install_prompt_toolkit_stubs(),
            ):
                repo = tmp_path / "agent-core"
                repo.mkdir()
                (repo / ".git").mkdir()
                (repo / "pyproject.toml").write_text(
                    "[project]\nname='x'\n",
                    encoding="utf-8",
                )
                (repo / "openjiuwen").mkdir()
                console = Console(file=open(
                    os.devnull, "w",
                ))
                data_dir = tmp_path / "auto_harness"
                data_dir.mkdir(parents=True)

                await repl._subcmd_run(
                    console,
                    ["--goal", "分析差距 claude-code"],
                    str(tmp_path),
                )

        assert captured_config is not None
        assert (
            captured_config.optimization_goal
            == "分析差距 claude-code"
        )
        assert captured_config.local_repo == str(
            (tmp_path / "agent-core").resolve()
        )
        assert captured_config.workspace == str(
            (tmp_path / "agent-core").resolve()
        )
        assert received_tasks is None

    @pytest.mark.asyncio
    async def test_natural_language_dispatch_runs_full_flow(
        self, tmp_path,
    ) -> None:
        """未知子命令应直接当成自然语言目标。"""
        repl = _import_repl_module()

        captured_config = None
        received_tasks = "NOT_SET"

        def _capture_create(config):
            nonlocal captured_config
            captured_config = config

            async def _fake_stream(tasks=None):
                nonlocal received_tasks
                received_tasks = tasks
                yield OutputSchema(
                    type="message",
                    index=0,
                    payload={"content": "ok"},
                )

            mock_orch = MagicMock()
            mock_orch.run_session_stream = _fake_stream
            mock_orch.results = []
            return mock_orch

        with patch(
            "openjiuwen.auto_harness.orchestrator"
            ".create_auto_harness_orchestrator",
            side_effect=_capture_create,
        ):
            with patch.dict(
                sys.modules,
                _install_prompt_toolkit_stubs(),
            ):
                console = Console(
                    file=StringIO(),
                    force_terminal=False,
                )
                data_dir = tmp_path / "auto_harness"
                data_dir.mkdir(parents=True)

                await repl._cmd_auto_harness(
                    console,
                    "/auto-harness 分析差距 claude-code",
                )

        assert captured_config is not None
        assert (
            captured_config.optimization_goal
            == "分析差距 claude-code"
        )
        assert received_tasks is None
