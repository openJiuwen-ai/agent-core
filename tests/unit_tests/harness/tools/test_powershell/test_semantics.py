# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for PowerShell command semantics."""

from __future__ import annotations

from openjiuwen.harness.tools.shell.powershell._semantics import interpret_exit_code


def test_zero_exit_code_is_success() -> None:
    meaning = interpret_exit_code("Write-Output ok", 0)

    assert meaning.is_error is False
    assert meaning.message is None


def test_read_only_pipeline_with_stdout_and_empty_stderr_is_partial_success() -> None:
    command = (
        "Get-ChildItem -Path C:\\ -Recurse -File -ErrorAction SilentlyContinue "
        "| Where-Object { $_.Length -gt 100MB } "
        "| Sort-Object Length -Descending "
        "| Select-Object -First 30 FullName "
        "| Format-Table -AutoSize"
    )
    meaning = interpret_exit_code(command, 1, stdout="C:\\Windows\\Panther\\setupact.log", stderr="")

    assert meaning.is_error is False
    assert meaning.message == (
        "PowerShell returned exit code 1 after producing output; treating output as partial result"
    )


def test_read_only_pipeline_with_calculated_property_is_partial_success() -> None:
    command = (
        "Get-ChildItem -Path C:\\ -Recurse -File -ErrorAction SilentlyContinue "
        "| Sort-Object Length -Descending "
        "| Select-Object -First 20 @{Name='Size(MB)';Expression={[math]::Round($_.Length/1MB,2)}}, FullName "
        "| Format-Table -AutoSize"
    )
    meaning = interpret_exit_code(command, 1, stdout="C:\\Users\\admin\\java_error_in_pycharm.hprof", stderr="")

    assert meaning.is_error is False
    assert meaning.message == (
        "PowerShell returned exit code 1 after producing output; treating output as partial result"
    )


def test_read_only_pipeline_ignores_operators_inside_strings_and_script_blocks() -> None:
    command = (
        "Get-ChildItem -Path C:\\ -Recurse -File -ErrorAction SilentlyContinue "
        "| Where-Object { $_.Name -like 'a;b|c' } "
        "| Select-Object FullName"
    )
    meaning = interpret_exit_code(command, 1, stdout="C:\\tmp\\a;b|c.txt", stderr="")

    assert meaning.is_error is False
    assert meaning.message == (
        "PowerShell returned exit code 1 after producing output; treating output as partial result"
    )


def test_get_child_item_exit_one_with_stdout_and_empty_stderr_reports_inaccessible_items() -> None:
    meaning = interpret_exit_code(
        "Get-ChildItem -Path C:\\ -Recurse",
        1,
        stdout="C:\\Windows\\Panther\\setupact.log",
        stderr="",
    )

    assert meaning.is_error is False
    assert meaning.message == "Partial results produced; some items may be inaccessible"


def test_get_child_item_with_empty_stdout_is_error() -> None:
    meaning = interpret_exit_code("Get-ChildItem -Path C:\\ -Recurse", 1, stdout="", stderr="")

    assert meaning.is_error is True


def test_get_child_item_with_stderr_is_error() -> None:
    meaning = interpret_exit_code(
        "Get-ChildItem -Path C:\\ -Recurse",
        1,
        stdout="C:\\Windows\\Panther\\setupact.log",
        stderr="Access is denied",
    )

    assert meaning.is_error is True


def test_select_string_exit_one_without_output_is_no_match() -> None:
    meaning = interpret_exit_code("Select-String -Path file.txt -Pattern missing", 1, stdout="", stderr="")

    assert meaning.is_error is False
    assert meaning.message == "No matches found"


def test_unknown_command_exit_one_is_error() -> None:
    meaning = interpret_exit_code("python script.py", 1, stdout="partial", stderr="")

    assert meaning.is_error is True
