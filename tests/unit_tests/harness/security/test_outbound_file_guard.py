# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
from pathlib import Path

import pytest

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.fileguard.path_extract import extract_accesses_native

TOOLS = ["send_file_to_user", "save_media_to_gallery", "save_file_to_file_manager"]


@pytest.mark.parametrize("source", [
    "C:/outbound/report.pdf", r"C:\outbound\report.pdf",
    r"\\server\share\report.pdf", "//server/share/report.pdf", "/outbound/report.pdf",
])
@pytest.mark.parametrize("name", TOOLS)
def test_outbound_path_forms_use_native_absolute_target(tmp_path, monkeypatch, source, name):
    monkeypatch.setattr(
        "openjiuwen.harness.security.permission_engine.fileguard.outbound_paths.get_cwd",
        lambda: str(tmp_path),
    )
    args = {"abs_file_path_list": [source]} if name == TOOLS[0] else {"url": source}
    expected = (Path(tmp_path) / source).resolve()
    assert expected.is_absolute()
    assert extract_accesses_native(name, args, tmp_path) == [(expected, "read", "tool_arg")]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("policy", ["allow", "ask", "deny"])
async def test_outbound_source_read_policy(tmp_path, name, policy):
    source = str(tmp_path / "private.pdf")
    engine = PermissionEngine({
        "enabled": True, "tools": {name: "allow"},
        "file_guard": {"enabled": True,
                       "defaults": {"read": "allow", "write": "deny", "exec": "deny"},
                       "paths": [{"path": source, "read": policy}]},
    }, workspace_root=tmp_path)
    args = {"abs_file_path_list": [source]} if name == "send_file_to_user" else {"url": source}
    result = await engine.check_permission(name, args)
    assert result.permission == PermissionLevel(policy)


@pytest.mark.asyncio
@pytest.mark.parametrize("encode", [lambda p: p, json.dumps, repr])
async def test_send_checks_every_file_in_all_array_forms(tmp_path, encode):
    paths = [str(tmp_path / "public.txt"), str(tmp_path / "secret.txt")]
    engine = PermissionEngine({
        "enabled": True, "tools": {"send_file_to_user": "allow"},
        "file_guard": {"enabled": True, "defaults": {"read": "allow"},
                       "paths": [{"path": paths[1], "read": "deny"}]},
    }, workspace_root=tmp_path)
    result = await engine.check_permission("send_file_to_user", {"abs_file_path_list": encode(paths)})
    assert result.permission == PermissionLevel.DENY


@pytest.mark.parametrize("name", TOOLS[1:])
@pytest.mark.parametrize("url", [
    "http://example.com/a.pdf", "https://example.com/a.pdf",
    "HTTPS://example.com/a.pdf", "  HtTp://example.com/a.pdf  ",
])
def test_remote_url_has_no_local_access(tmp_path, name, url):
    assert extract_accesses_native(name, {"url": url}, tmp_path) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", TOOLS)
async def test_relative_source_uses_agent_cwd(tmp_path, monkeypatch, name):
    process = tmp_path / "process"
    process.mkdir()
    monkeypatch.chdir(process)
    monkeypatch.setattr(
        "openjiuwen.harness.security.permission_engine.fileguard.outbound_paths.get_cwd",
        lambda: str(tmp_path / "agent" / "work"),
    )
    args = {"abs_file_path_list": "../a.txt"} if name == TOOLS[0] else {"url": "../a.txt"}
    assert extract_accesses_native(name, args, tmp_path / "other") == [
        ((tmp_path / "agent" / "a.txt").resolve(), "read", "tool_arg"),
    ]
    engine = PermissionEngine({
        "enabled": True, "tools": {name: "allow"},
        "file_guard": {"enabled": True, "defaults": {"read": "allow"},
                       "paths": [{"path": str(tmp_path / "agent" / "a.txt"), "read": "deny"}]},
    }, workspace_root=tmp_path / "agent" / "work")
    assert (await engine.check_permission(name, args)).permission == PermissionLevel.DENY


def test_approval_targets_exact_source_files(tmp_path):
    from openjiuwen.harness.security.file_guard import FileGuardChecker, normalize_path_guard_config

    cfg = {"file_guard": {"enabled": True, "defaults": {"read": "ask"}}}
    checker = FileGuardChecker(normalize_path_guard_config(cfg, workspace_root=tmp_path), cfg)
    paths = [tmp_path / "first.pdf", tmp_path / "second.pdf"]
    assert checker.collect_ask_accesses("send_file_to_user", {"abs_file_path_list": [str(p) for p in paths]}) == [
        (p.as_posix(), "read") for p in paths
    ]


@pytest.mark.parametrize("source", ["~/a.pdf", "$OUTBOUND_TEST_DIR/a.pdf", " report.pdf "])
def test_save_preserves_literal_path_characters(tmp_path, monkeypatch, source):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OUTBOUND_TEST_DIR", str(tmp_path / "expanded"))
    monkeypatch.setattr(
        "openjiuwen.harness.security.permission_engine.fileguard.outbound_paths.get_cwd",
        lambda: str(tmp_path / "agent"),
    )
    assert extract_accesses_native("save_media_to_gallery", {"url": source}, tmp_path / "workspace") == [
        ((tmp_path / "agent" / source).resolve(), "read", "tool_arg"),
    ]
