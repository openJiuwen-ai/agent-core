# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_ci_gate_tool — CIGateRunner 单元测试。"""

from __future__ import annotations

import os
import tempfile
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import yaml

from openjiuwen.auto_harness.infra.ci_gate_runner import CIGateRunner


class _FakeProc:
    def __init__(self, returncode: int, stdout: bytes):
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self):
        return self._stdout, b""


class TestCIGateRunnerInit(IsolatedAsyncioTestCase):
    def test_loads_gates_from_yaml(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False,
        ) as f:
            yaml.dump(
                {"ci_gates": [
                    {"name": "lint", "command": "make check"},
                ]},
                f,
            )
            path = f.name
        try:
            tool = CIGateRunner("/tmp", config_path=path)
            assert len(tool._gates) == 1
            assert tool._gates[0]["name"] == "lint"
        finally:
            os.unlink(path)

    def test_missing_yaml_returns_empty(self):
        tool = CIGateRunner(
            "/tmp", config_path="/nonexistent.yaml",
        )
        assert tool._gates == []


class TestCIGateRunnerMatchGates(IsolatedAsyncioTestCase):
    def _make_tool(self):
        tool = CIGateRunner.__new__(CIGateRunner)
        tool._workspace = "/tmp"
        tool._python_executable = ""
        tool._install_command = ""
        tool._prepared = True
        tool._gates = [
            {"name": "lint", "command": "make check"},
            {"name": "test", "command": "make test"},
            {"name": "type-check", "command": "make type-check"},
        ]
        return tool

    def test_match_all(self):
        tool = self._make_tool()
        assert len(tool._match_gates("all")) == 3

    def test_match_check_maps_to_lint(self):
        tool = self._make_tool()
        matched = tool._match_gates("check")
        assert len(matched) == 1
        assert matched[0]["name"] == "lint"

    def test_match_test(self):
        tool = self._make_tool()
        matched = tool._match_gates("test")
        assert len(matched) == 1
        assert matched[0]["name"] == "test"

    def test_match_unknown(self):
        tool = self._make_tool()
        assert tool._match_gates("unknown") == []

    def test_normalize_make_test_uses_bash_shell(self):
        tool = CIGateRunner(
            "/tmp", python_executable="/tmp/python3.11"
        )
        with patch(
            "pathlib.Path.is_file",
            return_value=True,
        ):
            normalized = tool._normalize_command("make test")
        assert normalized == "/tmp/python3.11 -m pytest"

    def test_normalize_make_test_preserves_flags(self):
        tool = CIGateRunner(
            "/tmp", python_executable="/tmp/python3.11"
        )
        with patch(
            "pathlib.Path.is_file",
            return_value=True,
        ):
            normalized = tool._normalize_command(
                "make test TESTFLAGS=tests/unit_tests/harness/"
            )
        assert normalized == (
            "/tmp/python3.11 -m pytest "
            "tests/unit_tests/harness/"
        )

    def test_normalize_shell_prefixed_make_test_uses_configured_python(self):
        tool = CIGateRunner(
            "/tmp", python_executable="/tmp/python3.11"
        )
        with patch(
            "pathlib.Path.is_file",
            return_value=True,
        ):
            normalized = tool._normalize_command(
                'PATH="/tmp/bin:$PATH" make test'
            )
        assert normalized == (
            'PATH="/tmp/bin:$PATH" '
            "/tmp/python3.11 -m pytest"
        )

    def test_normalize_python_module_command_uses_configured_python(self):
        tool = CIGateRunner(
            "/tmp", python_executable="/tmp/python3.11"
        )
        with patch(
            "pathlib.Path.is_file",
            return_value=True,
        ):
            normalized = tool._normalize_command(
                "python -m pytest -q"
            )
        assert normalized == "/tmp/python3.11 -m pytest -q"

    def test_resolve_python_executable_prefers_configured_path(self):
        tool = CIGateRunner(
            "/tmp", python_executable="/tmp/python3.11"
        )
        with patch(
            "pathlib.Path.is_file",
            side_effect=[True],
        ):
            assert tool._resolve_python_executable() == (
                "/tmp/python3.11"
            )

    def test_sanitize_failure_output_keeps_only_pytest_failure_sections(self):
        output = """
============================= test session starts ==============================
tests/unit_tests/core/foundation/tool/test_api_param_mapper.py F         [100%]

=================================== FAILURES ===================================
E   AssertionError: expected value

=============================== warnings summary ===============================
tests/unit_tests/core/foundation/tool/test_api_param_mapper.py:60
  PydanticDeprecatedSince20: `location` is deprecated

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
- Generated html report: file:///tmp/report/index.html -
=========================== short test summary info ============================
FAILED tests/unit_tests/core/foundation/tool/test_api_param_mapper.py::test_x
========================= 1 failed, 2 warnings in 0.10s ========================
""".strip()
        sanitized = CIGateRunner._sanitize_failure_output(output)
        assert "AssertionError" in sanitized
        assert "short test summary info" in sanitized
        assert "test session starts" not in sanitized
        assert "PydanticDeprecatedSince20" not in sanitized
        assert "warnings summary" not in sanitized
        assert "Generated html report" not in sanitized


class TestCIGateRunnerInvoke(IsolatedAsyncioTestCase):
    async def test_all_pass(self):
        tool = CIGateRunner.__new__(CIGateRunner)
        tool._workspace = "/tmp"
        tool._python_executable = ""
        tool._install_command = ""
        tool._prepared = True
        tool._gates = [
            {"name": "lint", "command": "echo ok"},
        ]
        fake_proc = _FakeProc(0, b"All good")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ):
            result = await tool.run("all")
        assert result["passed"] is True
        assert len(result["gates"]) == 1
        assert result["errors"] == ""

    async def test_gate_fails(self):
        tool = CIGateRunner.__new__(CIGateRunner)
        tool._workspace = "/tmp"
        tool._python_executable = ""
        tool._install_command = ""
        tool._prepared = True
        tool._gates = [
            {"name": "lint", "command": "ruff check ."},
        ]
        fake_proc = _FakeProc(1, b"E501 line too long")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ):
            result = await tool.run("check")
        assert result["passed"] is False
        assert "[lint]" in result["errors"]
        assert "E501 line too long" in result["errors"]

    async def test_no_matching_gate(self):
        tool = CIGateRunner.__new__(CIGateRunner)
        tool._workspace = "/tmp"
        tool._python_executable = ""
        tool._install_command = ""
        tool._prepared = True
        tool._gates = []
        result = await tool.run("test")
        assert result["passed"] is False
        assert "No gate matched" in result["errors"]

    async def test_default_action_is_all(self):
        tool = CIGateRunner.__new__(CIGateRunner)
        tool._workspace = "/tmp"
        tool._python_executable = ""
        tool._install_command = ""
        tool._prepared = True
        tool._gates = [
            {"name": "lint", "command": "echo ok"},
        ]
        fake_proc = _FakeProc(0, b"ok")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ):
            result = await tool.run()
        assert result["passed"] is True

    async def test_run_gate_rewrites_make_test(self):
        tool = CIGateRunner.__new__(CIGateRunner)
        tool._workspace = "/tmp"
        tool._python_executable = "/tmp/python3.11"
        tool._install_command = ""
        tool._prepared = True
        fake_proc = _FakeProc(0, b"ok")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ) as mock_exec, patch(
            "pathlib.Path.is_file",
            return_value=True,
        ):
            result = await tool._run_gate({
                "name": "test",
                "command": "make test",
            })
        assert result["passed"] is True
        mock_exec.assert_awaited_once()
        assert mock_exec.await_args.args[:2] == (
            "bash",
            "-c",
        )
        assert (
            mock_exec.await_args.args[2]
            == "/tmp/python3.11 -m pytest"
        )

    async def test_run_gate_rewrites_shell_prefixed_make_test(self):
        tool = CIGateRunner.__new__(CIGateRunner)
        tool._workspace = "/tmp"
        tool._python_executable = "/tmp/python3.11"
        tool._install_command = ""
        tool._prepared = True
        fake_proc = _FakeProc(0, b"ok")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ) as mock_exec, patch(
            "pathlib.Path.is_file",
            return_value=True,
        ):
            result = await tool._run_gate({
                "name": "test",
                "command": 'PATH="/tmp/bin:$PATH" make test',
            })
        assert result["passed"] is True
        assert (
            mock_exec.await_args.args[2]
            == 'PATH="/tmp/bin:$PATH" /tmp/python3.11 -m pytest'
        )

    async def test_run_gate_executes_install_command_once(self):
        tool = CIGateRunner.__new__(CIGateRunner)
        tool._workspace = "/tmp"
        tool._python_executable = "/tmp/python3.11"
        tool._install_command = (
            "uv sync --active --group dev --extra cli"
        )
        tool._prepared = False
        tool._gates = []
        fake_proc = _FakeProc(0, b"ok")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ) as mock_exec, patch(
            "pathlib.Path.is_file",
            return_value=True,
        ):
            await tool._run_gate({
                "name": "test",
                "command": "echo ok",
            })
            await tool._run_gate({
                "name": "lint",
                "command": "echo ok",
            })
        assert mock_exec.await_count == 3
        assert mock_exec.await_args_list[0].args[2] == (
            "uv sync --active --group dev --extra cli"
        )

    async def test_run_gate_filters_warning_summary_from_failed_output(self):
        tool = CIGateRunner.__new__(CIGateRunner)
        tool._workspace = "/tmp"
        tool._python_executable = ""
        tool._install_command = ""
        tool._prepared = True
        tool._gates = [
            {"name": "test", "command": "make test"},
        ]
        fake_proc = _FakeProc(
            1,
            (
                b"=================================== FAILURES ===================================\n"
                b"E   AssertionError: expected value\n"
                b"\n"
                b"=============================== warnings summary ===============================\n"
                b"tests/unit_tests/core/foundation/tool/test_api_param_mapper.py:60\n"
                b"  PydanticDeprecatedSince20: `location` is deprecated\n"
                b"\n"
                b"-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html\n"
                b"- Generated html report: file:///tmp/report/index.html -\n"
                b"=========================== short test summary info ============================\n"
                b"FAILED tests/unit_tests/core/foundation/tool/test_api_param_mapper.py::test_x\n"
            ),
        )
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ):
            result = await tool.run("all")
        assert result["passed"] is False
        assert "AssertionError: expected value" in result["errors"]
        assert "FAILED tests/unit_tests/core/foundation/tool/test_api_param_mapper.py::test_x" in result["errors"]
        assert "PydanticDeprecatedSince20" not in result["errors"]
