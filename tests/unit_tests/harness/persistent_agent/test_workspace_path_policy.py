# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-14 workspace path policy tests."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from openjiuwen.harness.persistent_agent.workspace.access import WorkspaceReadScope
from openjiuwen.harness.persistent_agent.workspace.path_policy import (
    WorkspacePathError,
    observe_version,
    resolve_read_path,
)

pytestmark = pytest.mark.level0


def scope_for(root: Path, *, status="allowed", current_session="session-a"):
    return WorkspaceReadScope(
        status=status,
        agent_id="agent-a",
        session_id=current_session,
        workspace_root=root,
        allowed_roots=(root / "work", root / "knowledge"),
        private_sessions_root=root / "sessions",
        current_session_root=root / "sessions" / current_session,
        source_ref="workspace://agent-a",
        policy_revision="policy-1",
    )


def test_resolve_shared_and_current_session_paths(tmp_path):
    scope = scope_for(tmp_path)

    shared = resolve_read_path(scope, "work/report.md")
    current = resolve_read_path(scope, "sessions/session-a/draft.md")

    assert shared.path == (tmp_path / "work/report.md").resolve()
    assert shared.relative_path == "work/report.md"
    assert shared.source_ref == "workspace://agent-a/work/report.md"
    assert current.relative_path == "sessions/session-a/draft.md"


def test_sibling_session_is_rejected_before_filesystem_probe(tmp_path, monkeypatch):
    scope = scope_for(tmp_path)

    def forbidden_probe(*args, **kwargs):
        raise AssertionError("forbidden path must not be probed")

    with patch.object(Path, "exists", forbidden_probe), patch.object(Path, "stat", forbidden_probe):
        with pytest.raises(WorkspacePathError) as exc_info:
            resolve_read_path(scope, "sessions/session-b/secret.md")

    assert exc_info.value.status == "forbidden"


@pytest.mark.parametrize(
    "requested_path",
    [
        "../outside.txt",
        "work/../sessions/session-b/secret.md",
        r"C:\outside.txt",
        r"\\server\share\outside.txt",
        "/etc/passwd",
    ],
)
def test_traversal_and_absolute_paths_are_rejected(tmp_path, requested_path):
    with pytest.raises(WorkspacePathError) as exc_info:
        resolve_read_path(scope_for(tmp_path), requested_path)

    assert exc_info.value.status == "forbidden"


def test_unknown_scope_is_rejected_without_filesystem_probe(tmp_path):
    def forbidden_probe(*args, **kwargs):
        raise AssertionError("unknown scope must not be probed")

    with patch.object(Path, "exists", forbidden_probe), patch.object(Path, "stat", forbidden_probe):
        with pytest.raises(WorkspacePathError) as exc_info:
            resolve_read_path(scope_for(tmp_path, status="unknown"), "work/report.md")

    assert exc_info.value.status == "unknown"


def test_symlink_escape_is_rejected_when_symlinks_are_available(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    link = tmp_path / "work" / "link"
    (tmp_path / "work").mkdir()
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("the test environment does not permit directory symlinks")

    with pytest.raises(WorkspacePathError) as exc_info:
        resolve_read_path(scope_for(tmp_path), "work/link/secret.md")

    assert exc_info.value.status == "forbidden"


def test_observe_version_uses_metadata_without_exposing_path(tmp_path):
    target = tmp_path / "report.md"
    target.write_text("first", encoding="utf-8")

    version = observe_version(target)

    assert version
    assert str(target) not in version
