# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for GitHub CLI preflight helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from openjiuwen.auto_harness.infra.github_cli import (
    ensure_github_cli_ready,
)


def test_github_cli_present_and_authenticated():
    messages: list[str] = []

    with patch(
        "openjiuwen.auto_harness.infra.github_cli.shutil.which",
        return_value="/usr/bin/gh",
    ), patch(
        "openjiuwen.auto_harness.infra.github_cli.subprocess.run",
        return_value=SimpleNamespace(
            returncode=0, stderr="", stdout="",
        ),
    ):
        status = ensure_github_cli_ready(messages.append)

    assert status.available is True
    assert status.authenticated is True
    assert status.installed_now is False
    assert "已登录" in "\n".join(messages)


def test_github_cli_missing_installs_and_prompts_login():
    messages: list[str] = []
    which_values = iter(["", "/usr/local/bin/gh"])
    run_calls: list[list[str]] = []

    def _fake_which(_name: str) -> str:
        return next(which_values, "/usr/local/bin/gh")

    def _fake_run(cmd, **_kwargs):
        run_calls.append(cmd)
        if cmd[1:3] == ["auth", "status"]:
            return SimpleNamespace(
                returncode=1, stderr="not logged in", stdout="",
            )
        return SimpleNamespace(
            returncode=0, stderr="", stdout="",
        )

    with patch(
        "openjiuwen.auto_harness.infra.github_cli.shutil.which",
        side_effect=_fake_which,
    ), patch(
        "openjiuwen.auto_harness.infra.github_cli._install_commands",
        return_value=[(["brew", "install", "gh"], "brew install gh")],
    ), patch(
        "openjiuwen.auto_harness.infra.github_cli.subprocess.run",
        side_effect=_fake_run,
    ):
        status = ensure_github_cli_ready(messages.append)

    assert status.available is True
    assert status.installed_now is True
    assert status.authenticated is False
    assert run_calls[0] == ["brew", "install", "gh"]
    assert run_calls[1] == ["/usr/local/bin/gh", "auth", "status"]
    assert "建议先执行 `gh auth login --web`" in "\n".join(
        messages
    )


def test_github_cli_missing_and_install_fails():
    messages: list[str] = []

    with patch(
        "openjiuwen.auto_harness.infra.github_cli.shutil.which",
        return_value="",
    ), patch(
        "openjiuwen.auto_harness.infra.github_cli._install_commands",
        return_value=[
            (["apt-get", "install", "-y", "gh"], "apt-get install -y gh")
        ],
    ), patch(
        "openjiuwen.auto_harness.infra.github_cli.subprocess.run",
        return_value=SimpleNamespace(
            returncode=1,
            stderr="permission denied",
            stdout="",
        ),
    ):
        status = ensure_github_cli_ready(messages.append)

    assert status.available is False
    assert status.authenticated is False
    joined = "\n".join(messages)
    assert "自动安装 `gh` 失败" in joined
    assert "https://cli.github.com/" in joined
