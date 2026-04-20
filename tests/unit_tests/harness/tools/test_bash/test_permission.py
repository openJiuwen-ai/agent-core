# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import re

import pytest

from openjiuwen.harness.tools.bash._permission import (
    PermissionConfig,
    PermissionMode,
    PermissionResult,
    check_permission,
)


def _cfg(
    mode: str = "auto",
    deny: list[str] | None = None,
    allow: list[str] | None = None,
) -> PermissionConfig:
    return PermissionConfig(
        mode=PermissionMode(mode),
        deny_patterns=PermissionConfig.compile_patterns(deny),
        allow_patterns=PermissionConfig.compile_patterns(allow),
    )


# ── bypass mode ───────────────────────────────────────────────

class TestBypassMode:

    def test_allows_everything(self) -> None:
        cfg = _cfg(mode="bypass")
        assert check_permission("rm -rf /", cfg).allowed is True

    def test_ignores_deny_patterns(self) -> None:
        cfg = PermissionConfig(
            mode=PermissionMode.BYPASS,
            deny_patterns=PermissionConfig.compile_patterns([r"rm"]),
        )
        assert check_permission("rm foo", cfg).allowed is True


# ── deny patterns ─────────────────────────────────────────────

class TestDenyPatterns:

    def test_deny_blocks(self) -> None:
        cfg = _cfg(deny=[r"\bsudo\b"])
        r = check_permission("sudo apt install foo", cfg)
        assert r.allowed is False
        assert "denied" in r.reason

    def test_deny_checks_each_segment(self) -> None:
        cfg = _cfg(deny=[r"\bsudo\b"])
        r = check_permission("echo hi | sudo tee file", cfg)
        assert r.allowed is False

    def test_no_deny_match_passes(self) -> None:
        cfg = _cfg(deny=[r"\bsudo\b"])
        assert check_permission("echo hello", cfg).allowed is True


# ── allow patterns ────────────────────────────────────────────

class TestAllowPatterns:

    def test_allow_passes(self) -> None:
        cfg = _cfg(allow=[r"^git\s"])
        assert check_permission("git status", cfg).allowed is True

    def test_deny_takes_precedence(self) -> None:
        cfg = _cfg(deny=[r"--force"], allow=[r"^git\s"])
        r = check_permission("git push --force", cfg)
        assert r.allowed is False


# ── read_only mode ────────────────────────────────────────────

class TestReadOnlyMode:

    def test_read_command_allowed(self) -> None:
        cfg = _cfg(mode="read_only")
        assert check_permission("cat foo.txt | grep bar", cfg).allowed is True

    def test_write_command_denied(self) -> None:
        cfg = _cfg(mode="read_only")
        r = check_permission("rm foo.txt", cfg)
        assert r.allowed is False
        assert "Read-only" in r.reason

    def test_ls_allowed(self) -> None:
        cfg = _cfg(mode="read_only")
        assert check_permission("ls -la", cfg).allowed is True

    def test_git_push_denied(self) -> None:
        cfg = _cfg(mode="read_only")
        assert check_permission("git push origin main", cfg).allowed is False

    def test_echo_pipeline_to_grep(self) -> None:
        cfg = _cfg(mode="read_only")
        assert check_permission("echo hello | grep h", cfg).allowed is True


# ── accept_edits mode ─────────────────────────────────────────

class TestAcceptEditsMode:

    def test_file_ops_allowed(self) -> None:
        cfg = _cfg(mode="accept_edits")
        assert check_permission("mkdir -p /tmp/foo", cfg).allowed is True
        assert check_permission("cp a.txt b.txt", cfg).allowed is True
        assert check_permission("sed -i 's/old/new/' file", cfg).allowed is True

    def test_known_dev_tools_allowed(self) -> None:
        cfg = _cfg(mode="accept_edits")
        assert check_permission("git commit -m test", cfg).allowed is True
        assert check_permission("python3 -m pytest", cfg).allowed is True
        assert check_permission("make test", cfg).allowed is True

    def test_unknown_command_denied(self) -> None:
        cfg = _cfg(mode="accept_edits")
        r = check_permission("my_custom_script --dangerous", cfg)
        assert r.allowed is False
        assert "unknown command" in r.reason.lower()

    def test_pipeline_with_unknown_denied(self) -> None:
        cfg = _cfg(mode="accept_edits")
        r = check_permission("cat file | evil_binary", cfg)
        assert r.allowed is False


# ── auto mode ─────────────────────────────────────────────────

class TestAutoMode:

    def test_any_command_allowed(self) -> None:
        cfg = _cfg(mode="auto")
        assert check_permission("anything_at_all --foo", cfg).allowed is True

    def test_deny_still_works(self) -> None:
        cfg = _cfg(mode="auto", deny=[r"\bsudo\b"])
        assert check_permission("sudo rm -rf /", cfg).allowed is False

    def test_empty_command(self) -> None:
        cfg = _cfg(mode="auto")
        assert check_permission("", cfg).allowed is True


# ── compile_patterns ──────────────────────────────────────────

class TestCompilePatterns:

    def test_none_returns_empty(self) -> None:
        assert PermissionConfig.compile_patterns(None) == []

    def test_empty_returns_empty(self) -> None:
        assert PermissionConfig.compile_patterns([]) == []

    def test_compiles_regex(self) -> None:
        patterns = PermissionConfig.compile_patterns([r"\bfoo\b", r"bar"])
        assert len(patterns) == 2
        assert all(isinstance(p, re.Pattern) for p in patterns)
