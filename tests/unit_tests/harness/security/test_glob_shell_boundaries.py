# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation import SysOperation, SysOperationCard, OperationMode
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.file_guard import FileGuardChecker, normalize_path_guard_config
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.fileguard.file_guard import extract_paths_legacy
from openjiuwen.harness.security.permission_engine.fileguard.path_extract import extract_accesses_native
from openjiuwen.harness.tools.filesystem import GlobTool


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", ["../*", "../**/*", "../../**/*", "sub/../*", r"..\*",
                                    "/tmp/*", "C:/secret/*", r"\secret\*", "{..,src}/*",
                                    "{{..,src},docs}/*", "{src,/tmp}/*"])
async def test_glob_rejects_escape_before_backend(tmp_path, pattern):
    root = tmp_path / "allowed"
    root.mkdir()
    engine = PermissionEngine({
        "enabled": True, "tools": {"glob": "allow"},
        "file_guard": {"enabled": True, "paths": [
            {"path": str(tmp_path), "read": "deny"},
            {"path": str(root), "read": "allow"},
        ]},
    }, workspace_root=root)
    args = {"path": str(root), "pattern": pattern}
    assert (await engine.check_permission("glob", args)).permission == PermissionLevel.DENY
    search = AsyncMock()
    tool = GlobTool(SimpleNamespace(fs=lambda: SimpleNamespace(search_files=search)))
    result = await tool.invoke(args)
    assert not result.success
    assert "search root" in result.error
    search.assert_not_awaited()


@pytest.mark.parametrize("mode", ["native", "legacy"])
@pytest.mark.parametrize("enabled", [True, False])
def test_glob_pattern_guard_respects_mode_and_switch(tmp_path, mode, enabled):
    config = {"file_guard": {"enabled": enabled}, "external_directory": "allow"}
    if mode == "native":
        config["file_guard"]["defaults"] = {"read": "allow"}
    effective = normalize_path_guard_config(config, workspace_root=tmp_path)
    if enabled:
        assert effective.mode == mode
    checker = FileGuardChecker(effective, config)
    result = checker.evaluate("glob", {"path": str(tmp_path), "pattern": "../*"})
    if enabled:
        assert result.permission == PermissionLevel.DENY
        assert result.matched_rule == "file_guard:glob_pattern"
    else:
        assert result is None


@pytest.mark.asyncio
async def test_glob_real_local_search_preserves_recursive_patterns(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "ok.txt").write_text("ok")
    operation = SysOperation(SysOperationCard(id="glob-boundary-test", mode=OperationMode.LOCAL))
    result = await GlobTool(operation).invoke({"path": str(tmp_path), "pattern": "**/*.{txt,md}"})
    assert result.success
    assert result.data["filenames"] == [str(Path("sub") / "ok.txt")]


@pytest.mark.asyncio
async def test_glob_rejects_outside_backend_result(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    result = SimpleNamespace(code=StatusCode.SUCCESS.code, data=SimpleNamespace(
        matching_files=[SimpleNamespace(path=str(root / ".." / "secret.txt"))]))
    tool = GlobTool(SimpleNamespace(fs=lambda: SimpleNamespace(search_files=AsyncMock(return_value=result))))
    output = await tool.invoke({"path": str(root), "pattern": "**/*"})
    assert not output.success
    assert output.data is None
    assert "secret.txt" not in output.error


@pytest.mark.asyncio
@pytest.mark.parametrize("cwd_inside", [False, True])
@pytest.mark.parametrize("relative_path", ["sub/ok.txt", "../secret.txt"])
async def test_glob_backend_relative_results_use_search_root(tmp_path, monkeypatch, cwd_inside, relative_path):
    from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import JiuwenBoxFSProvider

    root = tmp_path / "allowed"
    root.mkdir()
    process = root / "process" if cwd_inside else tmp_path / "process"
    process.mkdir()
    monkeypatch.chdir(process)
    paths = [relative_path]
    if relative_path == "sub/ok.txt":
        # Mixed backend representations must not duplicate the same file.
        paths.append(str(root / relative_path))
    provider = object.__new__(JiuwenBoxFSProvider)
    provider._get_sandbox_id = Mock(return_value="sandbox-a")
    client = Mock()
    client.search_files.return_value = [{"name": "result.txt", "path": p} for p in paths]
    provider._get_client = Mock(return_value=client)
    tool = GlobTool(SimpleNamespace(fs=lambda: provider))
    output = await tool.invoke({"path": str(root), "pattern": "**/*"})
    client.search_files.assert_called_once_with("sandbox-a", str(root.resolve()), "**/*", None)
    if relative_path == "sub/ok.txt":
        assert output.success
        assert output.data["filenames"] == [str(Path(relative_path))]
        assert output.data["matching_files"] == [str((root / relative_path).resolve())]
        assert output.data["count"] == 1
    else:
        assert not output.success
        assert output.data is None
        assert "secret.txt" not in output.error


@pytest.mark.asyncio
@pytest.mark.parametrize("name,shell,command", [
    ("mcp_exec_command", "cmd", "type secret.txt"),
    ("mcp_exec_command", "cmd", '"type secret.txt"'),
    ("bash", "cmd", "type secret.txt"),
    ("bash", "cmd", '"type secret.txt"'),
    ("mcp_exec_command", "powershell", "gc secret.txt"),
    ("bash", "powershell", "gc secret.txt"),
    ("bash", "powershell", "type secret.txt"),
    ("bash", " PowerShell ", "type secret.txt"),
    ("bash", " CMD ", "type secret.txt"),
    ("powershell", "auto", "gc secret.txt"),
])
@pytest.mark.parametrize("policy", ["deny", "ask", "allow"])
async def test_selected_shell_reads_bare_filename(tmp_path, name, shell, command, policy):
    secret = tmp_path / "secret.txt"
    args = {"command": command, "shell_type": shell, "workdir": str(tmp_path)}
    assert (secret, "read", "shlex") in extract_accesses_native(name, args, tmp_path)
    assert secret in extract_paths_legacy(name, args, tmp_path)
    engine = PermissionEngine({
        "enabled": True, "tools": {name: "allow"},
        "file_guard": {"enabled": True, "defaults": {"read": "allow"},
                       "paths": [{"path": str(secret), "read": policy}]},
    }, workspace_root=tmp_path)
    assert (await engine.check_permission(name, args)).permission == PermissionLevel(policy)


def test_bash_type_does_not_gain_cmd_file_semantics(tmp_path):
    assert extract_accesses_native("bash", {"command": "type secret.txt", "shell_type": "bash"}, tmp_path) == []


def test_powershell_content_value_is_not_a_path(tmp_path):
    accesses = extract_accesses_native("powershell", {"command": "Set-Content output.txt 'hello'"}, tmp_path)
    assert [(path, action) for path, action, _ in accesses] == [(tmp_path / "output.txt", "write")]


def test_selected_shell_preserves_legacy_relative_paths(tmp_path):
    assert tmp_path / "private" / "secret.txt" in extract_paths_legacy(
        "bash", {"command": "cat ./private/secret.txt", "shell_type": "powershell"}, tmp_path,
    )
