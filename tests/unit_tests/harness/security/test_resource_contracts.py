"""Shared approval semantics for LOCAL, ACL and runtime resource checks."""

import pytest

from openjiuwen.harness.security.permission_engine.approve.operation_grants import (
    build_operation_grant, has_operation_grant, matches_subject,
)
from openjiuwen.harness.security.permission_engine.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.fileguard.path_extract import extract_accesses_native


@pytest.mark.asyncio
@pytest.mark.parametrize("move,copy,shell", [("mv", "cp", "bash"), ("move", "copy", "cmd")])
async def test_move_source_requires_write_while_copy_only_reads(tmp_path, move, copy, shell):
    source, target = tmp_path / "source.txt", tmp_path / "target.txt"
    config = {"enabled": True, "package_builtin_rules": False, "tools": {"bash": "allow"},
              "file_guard": {"enabled": True, "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                             "paths": [{"path": str(source), "write": "deny"}]}}
    engine = PermissionEngine(config, workspace_root=tmp_path)
    for command, denied in [(move, True), (copy, False)]:
        args = {"command": f'{command} "{source.as_posix()}" "{target.as_posix()}"',
                "shell_type": shell, "workdir": str(tmp_path)}
        accesses = {(path, action) for path, action, _ in extract_accesses_native("bash", args, tmp_path)}
        assert (source, "read") in accesses
        assert (target, "write") in accesses
        assert ((source, "delete") in accesses) is denied
        assert (await engine.check_permission("bash", args)).is_denied is denied


def test_multiple_move_sources_all_require_delete(tmp_path):
    sources = [tmp_path / "a.txt", tmp_path / "b.txt"]
    target = tmp_path / "destination"
    args = {"command": "mv " + " ".join(f'"{path.as_posix()}"' for path in [*sources, target]),
            "shell_type": "bash", "workdir": str(tmp_path)}
    accesses = {(path, action) for path, action, _ in extract_accesses_native("bash", args, tmp_path)}
    for source in sources:
        assert (source, "read") in accesses
        assert (source, "delete") in accesses
    assert (target, "write") in accesses
    assert (target, "delete") not in accesses


@pytest.mark.asyncio
async def test_multiple_copy_sources_are_read_only_and_destination_is_write(tmp_path):
    sources = [tmp_path / "a.txt", tmp_path / "b.txt"]
    target = tmp_path / "destination"
    args = {"command": "cp " + " ".join(f'"{path.as_posix()}"' for path in [*sources, target]),
            "shell_type": "bash", "workdir": str(tmp_path)}
    accesses = {(path, action) for path, action, _ in extract_accesses_native("bash", args, tmp_path)}
    assert accesses == {(sources[0], "read"), (sources[1], "read"), (target, "write")}
    config = {"enabled": True, "package_builtin_rules": False, "tools": {"bash": "allow"},
        "file_guard": {"enabled": True, "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
                       "paths": [{"path": str(path), "write": "deny"} for path in sources]}}
    assert (await PermissionEngine(config, workspace_root=tmp_path).check_permission("bash", args)).is_allowed
    config["file_guard"]["paths"].append({"path": str(target), "write": "deny"})
    assert (await PermissionEngine(config, workspace_root=tmp_path).check_permission("bash", args)).is_denied


@pytest.mark.parametrize("approved,requested", [
    ("https://example.test", "https://example.test/"),
    ("https://example.test/", "https://example.test"),
    ("http://example.test:8080?q=1#section", "http://example.test:8080/?q=1#section"),
])
def test_exact_url_root_path_has_one_shared_identity(approved, requested):
    grant = build_operation_grant("mcp_fetch_webpage", {"url": approved}, {})
    assert has_operation_grant({"approval_overrides": [grant]}, "mcp_fetch_webpage", {"url": requested})
    assert matches_subject(grant, {"kind": "url", "url": requested})


@pytest.mark.parametrize("requested", [
    "http://example.test/", "https://other.test/", "https://sub.example.test/",
    "https://example.test:443/", "https://example.test/other", "https://example.test/?q=1",
    "https://example.test/?", "https://example.test/#fragment", "https://user@example.test/",
])
def test_root_url_normalization_does_not_widen_exact_scope(requested):
    grant = build_operation_grant("mcp_fetch_webpage", {"url": "https://example.test"}, {})
    assert not matches_subject(grant, {"kind": "url", "url": requested})


@pytest.mark.asyncio
async def test_url_equivalence_never_overrides_netguard_deny():
    grant = build_operation_grant("mcp_fetch_webpage", {"url": "https://example.test"}, {})
    engine = PermissionEngine({"enabled": True, "package_builtin_rules": False,
        "approval_overrides": [grant], "net_guard": {"enabled": True, "urls": {"example.test": "deny"}}})
    assert (await engine.check_permission("mcp_fetch_webpage", {"url": "https://example.test/"})).is_denied
