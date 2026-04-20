# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_auto_harness_cli — Click auto-harness 入口测试。"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.session.stream.base import (
    OutputSchema,
)
from openjiuwen.harness.cli.cli import (
    AutoHarnessRunRequest,
    _run_auto_harness,
)


def _install_renderer_stubs(fake_render):
    ui_module = types.ModuleType(
        "openjiuwen.harness.cli.ui"
    )
    renderer_module = types.ModuleType(
        "openjiuwen.harness.cli.ui.renderer"
    )
    renderer_module.render_stream = fake_render
    ui_module.renderer = renderer_module
    return {
        "openjiuwen.harness.cli.ui": ui_module,
        "openjiuwen.harness.cli.ui.renderer": renderer_module,
    }


class TestAutoHarnessCli:
    """验证 Click 版 auto-harness run 的全流程入口。"""

    @pytest.mark.asyncio
    async def test_run_without_manual_tasks_uses_full_session(
        self, tmp_path,
    ) -> None:
        """未传 task 时应传 tasks=None 给 orchestrator。"""
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

        async def _fake_render(stream, _console):
            async for _ in stream:
                pass
            return SimpleNamespace(text="")

        opts = SimpleNamespace(
            workspace=str(tmp_path),
            provider="OpenAI",
            model="gpt-4o",
            api_key="mock-api-key",
            api_base="https://api.openai.com/v1",
            verbose=False,
        )
        (tmp_path / "auto_harness").mkdir(
            parents=True, exist_ok=True,
        )

        with patch(
            "openjiuwen.auto_harness.orchestrator"
            ".create_auto_harness_orchestrator",
            side_effect=_capture_create,
        ), patch(
            "openjiuwen.core.foundation.llm.model.Model",
            return_value=MagicMock(name="mock_model"),
        ), patch.dict(
            sys.modules,
            _install_renderer_stubs(_fake_render),
        ):
            exit_code = await _run_auto_harness(
                opts=opts,
                request=AutoHarnessRunRequest(
                    goal="分析差距 claude-code"
                ),
            )

        assert exit_code == 0
        assert captured_config is not None
        assert (
            captured_config.optimization_goal
            == "分析差距 claude-code"
        )
        assert captured_config.local_repo == str(
            Path.cwd().resolve()
        )
        assert captured_config.workspace == str(
            Path.cwd().resolve()
        )
        assert captured_config.data_dir == str(
            (tmp_path / "auto_harness").resolve()
        )
        assert received_tasks is None

    @pytest.mark.asyncio
    async def test_run_with_detected_local_repo_sets_workspace(
        self, tmp_path,
    ) -> None:
        """探测到 local_repo 时，workspace 也应切到仓库根。"""
        captured_config = None

        repo = tmp_path / "agent-core"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "pyproject.toml").write_text(
            "[project]\nname='x'\n",
            encoding="utf-8",
        )
        (repo / "openjiuwen").mkdir()

        def _capture_create(config):
            nonlocal captured_config
            captured_config = config

            async def _fake_stream(tasks=None):
                yield OutputSchema(
                    type="message",
                    index=0,
                    payload={"content": "ok"},
                )

            mock_orch = MagicMock()
            mock_orch.run_session_stream = _fake_stream
            mock_orch.results = []
            return mock_orch

        async def _fake_render(stream, _console):
            async for _ in stream:
                pass
            return SimpleNamespace(text="")

        opts = SimpleNamespace(
            workspace=str(tmp_path),
            provider="OpenAI",
            model="gpt-4o",
            api_key="mock-api-key",
            api_base="https://api.openai.com/v1",
            verbose=False,
        )
        (tmp_path / "auto_harness").mkdir(
            parents=True, exist_ok=True,
        )

        with patch(
            "openjiuwen.auto_harness.orchestrator"
            ".create_auto_harness_orchestrator",
            side_effect=_capture_create,
        ), patch(
            "openjiuwen.core.foundation.llm.model.Model",
            return_value=MagicMock(name="mock_model"),
        ), patch.dict(
            sys.modules,
            _install_renderer_stubs(_fake_render),
        ):
            exit_code = await _run_auto_harness(
                opts=opts,
                request=AutoHarnessRunRequest(
                    goal="分析差距 claude-code"
                ),
            )

        assert exit_code == 0
        assert captured_config is not None
        assert captured_config.local_repo == str(
            repo.resolve()
        )
        assert captured_config.workspace == str(
            repo.resolve()
        )

    @pytest.mark.asyncio
    async def test_run_assess_invokes_github_cli_preflight(
        self, tmp_path,
    ) -> None:
        """Assess stage should run GitHub CLI preflight first."""
        preflight_called = False

        async def _fake_render(stream, _console):
            async for _ in stream:
                pass
            return SimpleNamespace(
                text="# assessment\n",
            )

        async def _fake_assess_stream(_config, _memory):
            yield OutputSchema(
                type="message",
                index=0,
                payload={"content": "ok"},
            )

        opts = SimpleNamespace(
            workspace=str(tmp_path),
            provider="OpenAI",
            model="gpt-4o",
            api_key="mock-api-key",
            api_base="https://api.openai.com/v1",
            verbose=False,
        )
        (tmp_path / "auto_harness").mkdir(
            parents=True, exist_ok=True,
        )

        def _fake_preflight(_emit):
            nonlocal preflight_called
            preflight_called = True
            return None

        with patch(
            "openjiuwen.auto_harness.infra.github_cli.ensure_github_cli_ready",
            side_effect=_fake_preflight,
        ), patch(
            "openjiuwen.auto_harness.stages.assess.run_assess_stream",
            side_effect=_fake_assess_stream,
        ), patch(
            "openjiuwen.core.foundation.llm.model.Model",
            return_value=MagicMock(name="mock_model"),
        ), patch.dict(
            sys.modules,
            _install_renderer_stubs(_fake_render),
        ):
            exit_code = await _run_auto_harness(
                opts=opts,
                request=AutoHarnessRunRequest(
                    stage="assess",
                    competitor="claude-code",
                ),
            )

        assert exit_code == 0
        assert preflight_called is True
