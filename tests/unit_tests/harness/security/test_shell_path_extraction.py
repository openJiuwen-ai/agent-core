# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Child interpreters keep their own working directories during path analysis."""

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.permission_engine.fileguard import path_extract


@pytest.mark.parametrize("extractor", [
    path_extract.extract_shell_path_accesses,
    path_extract.extract_path_aware_command_accesses,
])
@pytest.mark.parametrize("child", [
    "bash -c 'cd ./child && cat ./inside'",
    "sh -lc 'cd ./child && cat ./inside'",
    'cmd /c "cd child && type ./inside"',
    'powershell -Command "cd child; Get-Content ./inside"',
])
def test_child_shell_cd_does_not_change_following_parent_paths(tmp_path, extractor, child):
    accesses = extractor(f"{child} && cat ./outside", tmp_path, shell_type="bash")
    assert (tmp_path / "child" / "inside", "read") in accesses
    assert (tmp_path / "outside", "read") in accesses
    assert (tmp_path / "child" / "outside", "read") not in accesses


def test_parent_redirect_keeps_parent_cwd_after_child_shell(tmp_path):
    accesses = path_extract.extract_shell_path_accesses(
        "bash -c 'cd ./child && node ./inside.js' && echo ok > ./outside.txt",
        tmp_path, shell_type="bash",
    )
    assert (tmp_path / "child" / "inside.js", "exec") in accesses
    assert (tmp_path / "outside.txt", "write") in accesses
    assert (tmp_path / "child" / "outside.txt", "write") not in accesses


@pytest.mark.parametrize("child", [
    "bash -c 'cat ./secret'", 'cmd /c "type ./secret"',
    'powershell -Command "Get-Content ./secret"',
])
def test_child_shell_recursion_cannot_bypass_analysis_depth_budget(tmp_path, monkeypatch, child):
    monkeypatch.setattr(path_extract, "_MAX_INLINE_DEPTH", 0)
    with pytest.raises(BaseError, match="depth limit") as caught:
        path_extract.extract_shell_path_accesses(
            f"echo ok && {child}", tmp_path, shell_type="bash",
        )
    assert caught.value.details == {"rule_id": "file_guard:analysis_limit"}


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [
    "bash -c 'cd ./child && echo ok'",
    'cmd /c "cd child && echo ok"',
    'powershell -Command "cd child; Write-Output ok"',
])
@pytest.mark.parametrize("action,following", [
    ("read", "cat ./outside"),
    ("write", "echo ok > ./outside"),
    ("exec", "node ./outside"),
])
async def test_parent_resource_deny_survives_child_shell_cd(tmp_path, child, action, following):
    engine = PermissionEngine({
        "enabled": True,
        "tools": {"bash": "allow"},
        "defaults": {"*": "allow"},
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
            "paths": [{"path": str(tmp_path / "outside"), action: "deny"}],
        },
    }, workspace_root=tmp_path)
    result = await engine.check_permission("bash", {
        "command": f"{child} && {following}",
        "workdir": str(tmp_path), "shell_type": "bash",
    })
    assert result.permission is PermissionLevel.DENY
