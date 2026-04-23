# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import pytest

from openjiuwen.harness.tools.shell.bash._security import (
    check_injection,
    get_destructive_warning,
)


# ── check_injection ───────────────────────────────────────────

class TestCheckInjection:

    def test_safe_command(self) -> None:
        r = check_injection("echo hello")
        assert r.blocked is False

    def test_backtick(self) -> None:
        r = check_injection("echo `whoami`")
        assert r.blocked is True
        assert "backtick" in r.reason

    def test_dollar_paren(self) -> None:
        r = check_injection("echo $(id)")
        assert r.blocked is True
        assert "$(" in r.reason

    def test_process_substitution(self) -> None:
        r = check_injection("diff <(ls a) <(ls b)")
        assert r.blocked is True
        assert "process substitution" in r.reason

    def test_single_quoted_backtick_blocked(self) -> None:
        # the heuristic is conservative — it blocks even if context is ambiguous
        r = check_injection("echo \"hello `world`\"")
        assert r.blocked is True

    def test_normal_redirect_allowed(self) -> None:
        r = check_injection("echo hello > output.txt")
        assert r.blocked is False

    def test_pipe_allowed(self) -> None:
        r = check_injection("cat file | grep foo")
        assert r.blocked is False


# ── get_destructive_warning ───────────────────────────────────

class TestGetDestructiveWarning:

    @pytest.mark.parametrize("cmd,keyword", [
        ("git reset --hard HEAD~1", "uncommitted"),
        ("git push --force origin main", "remote history"),
        ("git push -f origin main", "remote history"),
        ("git clean -fd", "untracked"),
        ("git checkout -- .", "unstaged"),
        ("git stash drop", "stashed"),
        ("git stash clear", "stashed"),
        ("git branch -D feature", "force-delete"),
        ("git commit --amend", "rewrite"),
        ("git push --no-verify", "hooks"),
        ("DROP TABLE users;", "database"),
        ("TRUNCATE TABLE logs;", "truncate"),
        ("kubectl delete pod foo", "Kubernetes"),
        ("terraform destroy", "Terraform"),
    ])
    def test_destructive_detected(self, cmd: str, keyword: str) -> None:
        w = get_destructive_warning(cmd)
        assert w is not None
        assert keyword.lower() in w.lower()

    @pytest.mark.parametrize("cmd", [
        "git status",
        "git log --oneline",
        "git push origin main",
        "ls -la",
        "echo hello",
        "python test.py",
    ])
    def test_safe_commands(self, cmd: str) -> None:
        assert get_destructive_warning(cmd) is None
