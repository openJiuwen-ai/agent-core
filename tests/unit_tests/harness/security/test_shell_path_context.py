# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.harness.security.permission_engine.fileguard.shell_paths import ShellPathAnalysis
from openjiuwen.harness.security.permission_engine.shell_path_context import (
    current_shell_path_analysis,
    shell_path_call,
)


@pytest.fixture
def evidence(tmp_path):
    analysis = ShellPathAnalysis("cat file.txt", str(tmp_path), "auto", tmp_path, "complete")
    tool = SimpleNamespace(analyze_local_paths=Mock(return_value=analysis))
    args = {"command": analysis.command, "workdir": analysis.workdir}
    return tool, args, analysis


def test_scope_restores_outer_result_and_binds_once(evidence):
    tool, args, analysis = evidence
    with shell_path_call() as outer:
        outer.bind(tool, args)
        with shell_path_call() as inner:
            assert current_shell_path_analysis("bash", args) is None
            with pytest.raises(RuntimeError, match="active permission scope"):
                outer.bind(tool, args)
            inner.bind(tool, args)
            assert current_shell_path_analysis("bash", args) is analysis
            with pytest.raises(RuntimeError, match="already bound"):
                inner.bind(tool, args)
        assert current_shell_path_analysis("bash", args) is analysis
        assert current_shell_path_analysis("bash", {**args, "command": "echo hi"}) is None
    assert current_shell_path_analysis("bash", args) is None
    with pytest.raises(RuntimeError, match="active permission scope"):
        outer.bind(tool, args)
    assert tool.analyze_local_paths.call_count == 2


@pytest.mark.parametrize("error", [ValueError, asyncio.CancelledError])
def test_scope_exception_clears_evidence(evidence, error):
    tool, args, _ = evidence
    with pytest.raises(error):
        with shell_path_call() as scope:
            scope.bind(tool, args)
            raise error()
    assert current_shell_path_analysis("bash", args) is None


@pytest.mark.asyncio
async def test_parallel_scopes_do_not_share_results_or_allow_cross_task_binding(evidence):
    tool, args, analysis = evidence
    other_analysis = replace(analysis, coverage="partial")
    other = SimpleNamespace(analyze_local_paths=Mock(return_value=other_analysis))
    with shell_path_call() as scope:
        scope.bind(tool, args)

        async def check_other_call():
            assert current_shell_path_analysis("bash", args) is None
            with pytest.raises(RuntimeError, match="active permission scope"):
                scope.bind(tool, args)
            with shell_path_call() as other_scope:
                other_scope.bind(other, args)
                assert current_shell_path_analysis("bash", args) is other_analysis
            assert current_shell_path_analysis("bash", args) is None

        await asyncio.create_task(check_other_call())
        assert current_shell_path_analysis("bash", args) is analysis


def test_unsupported_analysis_is_visible_only_in_scope(evidence):
    tool, args, analysis = evidence
    tool.analyze_local_paths.return_value = replace(analysis, coverage="unsupported")
    with shell_path_call() as scope:
        scope.bind(tool, args)
        assert current_shell_path_analysis("bash", args).coverage == "unsupported"
    assert current_shell_path_analysis("bash", args) is None
