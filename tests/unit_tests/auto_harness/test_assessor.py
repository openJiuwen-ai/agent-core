# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_assessor — stages.assess 单元测试。"""

from __future__ import annotations

import tempfile
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    Experience,
    ExperienceType,
)

_ASSESS_MOD = "openjiuwen.auto_harness.stages.assess"


class _FakeExperienceStore:
    """轻量 ExperienceStore mock。"""

    def __init__(self, experiences=None):
        self._experiences = experiences or []

    async def list_recent(self, limit=10):
        return self._experiences[:limit]


class TestAssessFallback(
    IsolatedAsyncioTestCase,
):
    """测试 fallback（纯 Python）路径。"""

    @patch(
        f"{_ASSESS_MOD}._assess_with_agent",
        new_callable=AsyncMock,
        side_effect=RuntimeError("no model"),
    )
    async def test_fallback_returns_report(
        self, _mock_agent,
    ):
        """agent 失败时回退到纯 Python 版本。"""
        from openjiuwen.auto_harness.stages.assess import (
            run_assess,
        )

        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d, workspace=d,
            )
            experience_store = _FakeExperienceStore()
            report = await run_assess(
                cfg, experience_store
            )
            assert "评估报告" in report
            assert len(report) > 50

    @patch(
        f"{_ASSESS_MOD}._assess_with_agent",
        new_callable=AsyncMock,
        side_effect=RuntimeError("no model"),
    )
    async def test_fallback_with_experiences(
        self, _mock_agent,
    ):
        """fallback 包含经验记录。"""
        from openjiuwen.auto_harness.stages.assess import (
            run_assess,
        )

        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d, workspace=d,
            )
            experiences = [
                Experience(
                    type=ExperienceType.FAILURE,
                    topic="lint-fix",
                    summary="ruff failed",
                ),
            ]
            experience_store = _FakeExperienceStore(
                experiences
            )
            report = await run_assess(
                cfg, experience_store
            )
            assert "lint-fix" in report


class TestAssessWithAgent(
    IsolatedAsyncioTestCase,
):
    """测试 DeepAgent 驱动路径。"""

    async def test_build_query_includes_python_check_strategy(self):
        """query 应包含动态 Python 检查策略。"""
        from openjiuwen.auto_harness.stages.assess import (
            _build_query,
        )

        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d, workspace=d,
            )
            experience_store = _FakeExperienceStore()
            with patch(
                f"{_ASSESS_MOD}._detect_python_check_strategy",
                new_callable=AsyncMock,
                return_value="使用 staged files 运行 make check",
            ):
                query = await _build_query(
                    cfg, experience_store
                )
        assert "Python 检查策略建议" in query
        assert "使用 staged files 运行 make check" in query

    @patch(
        "openjiuwen.auto_harness.agent"
        ".create_assess_agent",
        autospec=False,
    )
    async def test_assess_with_agent(
        self, mock_create,
    ):
        """正常 agent 调用返回报告。"""
        from openjiuwen.auto_harness.stages.assess import (
            run_assess,
        )

        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d, workspace=d,
            )

            long_text = (
                "# 评估报告\n## 构建状态\nOK\n"
                * 10
            )

            class _Chunk:
                def __init__(self, text):
                    self.payload = {"content": text}

            mock_agent = AsyncMock()

            async def _fake_stream(inputs):
                yield _Chunk(long_text)

            mock_agent.stream = _fake_stream
            mock_create.return_value = mock_agent

            experience_store = _FakeExperienceStore()
            report = await run_assess(
                cfg, experience_store
            )
            assert "评估报告" in report

    @patch(
        "openjiuwen.auto_harness.agent"
        ".create_assess_agent",
        autospec=False,
    )
    async def test_short_report_triggers_fallback(
        self, mock_create,
    ):
        """agent 返回过短时回退。"""
        from openjiuwen.auto_harness.stages.assess import (
            run_assess,
        )

        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d, workspace=d,
            )

            class _Chunk:
                def __init__(self, text):
                    self.payload = {"content": text}

            mock_agent = AsyncMock()

            async def _fake_stream(inputs):
                yield _Chunk("too short")

            mock_agent.stream = _fake_stream
            mock_create.return_value = mock_agent

            experience_store = _FakeExperienceStore()
            report = await run_assess(
                cfg, experience_store
            )
            # 应该走 fallback
            assert "评估报告" in report


class TestAssessStream(IsolatedAsyncioTestCase):
    """测试流式评估。"""

    @patch(
        "openjiuwen.auto_harness.agent"
        ".create_assess_agent",
        autospec=False,
    )
    async def test_assess_stream_yields_chunks(
        self, mock_create,
    ):
        """run_assess_stream 透传 agent chunks。"""
        from openjiuwen.auto_harness.stages.assess import (
            run_assess_stream,
        )

        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d, workspace=d,
            )

            class _FakeChunk:
                def __init__(self, text):
                    self.type = "llm_output"
                    self.payload = {"content": text}

            chunks = [
                _FakeChunk("part1"),
                _FakeChunk("part2"),
            ]

            mock_agent = AsyncMock()

            async def _fake_stream(inputs):
                for c in chunks:
                    yield c

            mock_agent.stream = _fake_stream
            mock_create.return_value = mock_agent

            experience_store = _FakeExperienceStore()

            collected = []
            async for chunk in run_assess_stream(
                cfg, experience_store,
            ):
                collected.append(chunk)

            assert len(collected) == 2
            assert (
                collected[0].payload["content"]
                == "part1"
            )


class TestAssessCheckStrategy(IsolatedAsyncioTestCase):
    """测试 assess 阶段的检查策略推导。"""

    def test_format_strategy_prefers_staged_make_targets(self):
        from openjiuwen.auto_harness.stages.assess import (
            _format_python_check_strategy,
        )

        strategy = _format_python_check_strategy(
            ["openjiuwen/auto_harness/agent.py"],
            [],
            [],
        )
        assert "`make check`" in strategy
        assert "`make type-check`" in strategy
        assert "staged" in strategy

    def test_format_strategy_uses_explicit_tools_for_worktree_delta(self):
        from openjiuwen.auto_harness.stages.assess import (
            _format_python_check_strategy,
        )

        strategy = _format_python_check_strategy(
            [],
            ["openjiuwen/auto_harness/agent.py"],
            ["tests/unit_tests/auto_harness/test_agent.py"],
        )
        assert "不要运行 `make check COMMITS=1`" in strategy
        assert "`uv run ruff check <files>`" in strategy
        assert "`uv run mypy <files>`" in strategy

    def test_format_strategy_marks_empty_snapshot_as_not_applicable(self):
        from openjiuwen.auto_harness.stages.assess import (
            _format_python_check_strategy,
        )

        strategy = _format_python_check_strategy(
            [],
            [],
            [],
        )
        assert "No Python files selected" in strategy
        assert "未执行" in strategy
