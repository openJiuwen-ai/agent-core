# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-16 workspace write path policy tests."""

import os
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import pytest

from openjiuwen.harness.persistent_agent.workspace.access import WorkspaceWriteScope

pytestmark = pytest.mark.level0


def scope_for(root: Path, *, status="allowed", current_session="session-a"):
    return WorkspaceWriteScope(
        status=status,
        agent_id="agent-a",
        session_id=current_session,
        workspace_root=root,
        allowed_write_roots=(root / "work", root / "shared"),
        private_sessions_root=root / "sessions",
        current_session_root=root / "sessions" / current_session,
        source_ref="workspace://agent-a",
        policy_revision="policy-1",
    )


def resolve_write_path(scope, requested_path):
    module = import_module("openjiuwen.harness.persistent_agent.workspace.path_policy")
    resolver = getattr(module, "resolve_write_path", None)
    assert callable(resolver), "PA-16 write path resolver is not implemented"
    return resolver(scope, requested_path)


def test_resolve_shared_and_current_session_write_paths(tmp_path):
    scope = scope_for(tmp_path)

    shared = resolve_write_path(scope, "work/report.md")
    current = resolve_write_path(scope, "sessions/session-a/draft.md")

    assert shared.path == (tmp_path / "work/report.md").resolve()
    assert shared.relative_path == "work/report.md"
    assert shared.source_ref == "workspace://agent-a/work/report.md"
    assert current.relative_path == "sessions/session-a/draft.md"


def test_write_path_resolver_is_exported_from_workspace_package():
    workspace = import_module("openjiuwen.harness.persistent_agent.workspace")
    resolver_module = import_module("openjiuwen.harness.persistent_agent.workspace.path_policy")

    assert workspace.resolve_write_path is resolver_module.resolve_write_path


def test_sibling_session_is_rejected_before_path_resolution(tmp_path):
    scope = scope_for(tmp_path)

    def forbidden_probe(*args, **kwargs):
        raise AssertionError("unauthorized path was probed")

    with (
        patch("os.path.realpath", side_effect=forbidden_probe),
        patch.object(Path, "exists", forbidden_probe),
        patch.object(Path, "stat", forbidden_probe),
    ):
        with pytest.raises(ValueError) as exc_info:
            resolve_write_path(scope, "sessions/session-b/secret.md")

    assert getattr(exc_info.value, "status", None) == "forbidden"
    assert str(tmp_path) not in str(exc_info.value)


@pytest.mark.parametrize(
    "requested_path",
    [
        "../outside.txt",
        "work/../sessions/session-b/secret.md",
        r"C:\outside.txt",
        r"\\server\share\outside.txt",
        "/etc/passwd",
        "work/file\x00.txt",
        "",
        ".",
    ],
)
def test_traversal_absolute_and_nul_paths_are_rejected(tmp_path, requested_path):
    with pytest.raises(ValueError) as exc_info:
        resolve_write_path(scope_for(tmp_path), requested_path)

    assert getattr(exc_info.value, "status", None) == "forbidden"
    assert str(tmp_path) not in str(exc_info.value)


@pytest.mark.parametrize("status", ["forbidden", "unknown"])
def test_non_allowed_scope_is_rejected_before_path_resolution(tmp_path, status):
    scope = scope_for(tmp_path, status=status)

    def forbidden_probe(*args, **kwargs):
        raise AssertionError("unauthorized scope was probed")

    with (
        patch("os.path.realpath", side_effect=forbidden_probe),
        patch.object(Path, "exists", forbidden_probe),
        patch.object(Path, "stat", forbidden_probe),
    ):
        with pytest.raises(ValueError) as exc_info:
            resolve_write_path(scope, "work/report.md")

    assert getattr(exc_info.value, "status", None) == status
    assert str(tmp_path) not in str(exc_info.value)


def test_unlisted_root_is_rejected(tmp_path):
    with pytest.raises(ValueError) as exc_info:
        resolve_write_path(scope_for(tmp_path), "private/secret.md")

    assert getattr(exc_info.value, "status", None) == "forbidden"


def test_symlink_escape_is_rejected_when_symlinks_are_available(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "work").mkdir()
    link = tmp_path / "work" / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("the test environment does not permit directory symlinks")

    with pytest.raises(ValueError) as exc_info:
        resolve_write_path(scope_for(tmp_path), "work/link/new-file.md")

    assert getattr(exc_info.value, "status", None) == "forbidden"
    assert str(outside) not in str(exc_info.value)


def test_symlink_target_escape_is_rejected_when_symlinks_are_available(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.md")
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "work").mkdir()
    link = tmp_path / "work" / "alias.md"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("the test environment does not permit file symlinks")

    with pytest.raises(ValueError) as exc_info:
        resolve_write_path(scope_for(tmp_path), "work/alias.md")

    assert getattr(exc_info.value, "status", None) == "forbidden"
    assert str(outside) not in str(exc_info.value)
