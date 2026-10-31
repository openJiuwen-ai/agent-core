"""CLI error reporting for ``auto-harness run``."""

from __future__ import annotations

from click.testing import CliRunner

from openjiuwen.harness.cli.cli import cli


def test_auto_harness_missing_api_key_is_a_clean_cli_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(cli, ["auto-harness", "run"])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert isinstance(result.exception, SystemExit)


def test_auto_harness_missing_task_file_is_a_clean_cli_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(
        cli,
        [
            "--api-key",
            "dummy",
            "--workspace",
            str(tmp_path),
            "auto-harness",
            "run",
            "--task-file",
            str(tmp_path / "missing.json"),
        ],
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert isinstance(result.exception, SystemExit)
