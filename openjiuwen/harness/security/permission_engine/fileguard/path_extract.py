# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native L1 path extraction：registry 通道 + shell（分段 / redirect / 解释器）。

源自 enterprise ``permissions/files/extract.py``，依赖改为 agent-core
``openjiuwen.harness.security.shell_ast``。本期不迁 L3 ``command_intent``。
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from openjiuwen.harness.security.permission_engine.fileguard.file_tool_specs import (
    FileToolSpec,
    lookup_file_tool_specs,
)
from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
    canonicalize_shell_command_for_permission,
)
from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import parse_shell_for_permission
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import _PATH_TOOLS, _iter_path_strings

logger = logging.getLogger(__name__)

FileAction = Literal["read", "write", "exec"]

_CHAIN_SPLIT_RE = re.compile(r"\s+(?:&&|\|\|)\s+")

_PATH_AWARE_COMMANDS = frozenset({
    "cd", "rm", "cp", "mv", "mkdir", "touch", "chmod", "chown", "cat",
    "ls", "dir", "type", "del", "rd", "copy", "move", "md",
    "head", "tail", "more", "less", "vim", "nano", "gedit", "notepad",
    "get-content", "gc",
    "set-content", "add-content", "out-file", "tee-object", "sc",
    "remove-item", "ri", "new-item", "ni",
})

_INTERPRETER_BASENAMES = frozenset({
    "python", "python3", "pythonw", "py",
    "node", "nodejs", "bash", "sh", "dash", "zsh", "fish",
    "pwsh", "powershell",
})

_WRITE_PATH_TOOLS = frozenset({
    "write_file", "edit_file", "write_text_file", "write", "search_replace",
})

_NT_CMD_SWITCH_BODY = re.compile(r"^[A-Za-z]{1,2}(?::[^\s/\\]+)?$")

_READ_CMDS = frozenset({
    "cat", "ls", "dir", "type", "head", "tail", "more", "less",
    "get-content", "gc",
})
_WRITE_CMDS = frozenset({
    "rm", "mkdir", "touch", "chmod", "chown", "del", "rd", "md",
    "set-content", "add-content", "out-file", "tee-object", "sc",
    "remove-item", "ri", "new-item", "ni",
})
_PS_PATH_FLAGS = frozenset({"-path", "-literalpath", "-filepath"})
_FD_ALIAS_RE = re.compile(r"^&\d+$")
_UNEXPANDED_VAR_RE = re.compile(r"(?i)\$(\w+|\{[^}]+\}|env:\w+)")
_TRANSFER_CMDS = frozenset({"cp", "copy", "mv", "move"})


def _nt_cmd_exe_switch_token(stripped: str) -> bool:
    if os.name != "nt" or not stripped.startswith("/") or stripped.startswith("//"):
        return False
    if "\\" in stripped:
        return False
    if stripped.count("/") != 1:
        return False
    return bool(_NT_CMD_SWITCH_BODY.match(stripped[1:]))


def _looks_like_path(token: str) -> bool:
    t = token.strip().strip('"').strip("'")
    if _nt_cmd_exe_switch_token(t):
        return False
    if t in (".", ".."):
        return True
    if t.startswith(("\\\\", "./", "../")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", t):
        return True
    return "\\" in t or "/" in t


def _looks_like_redirect_target(token: str) -> bool:
    t = token.strip().strip('"').strip("'")
    if not t or _FD_ALIAS_RE.match(t):
        return False
    if t.startswith("$") or _UNEXPANDED_VAR_RE.search(t):
        return False
    return True


def _collect_ps_path_tokens(tokens: list[str]) -> list[str]:
    out: list[str] = []
    first_positional = True
    idx = 1
    while idx < len(tokens):
        raw = tokens[idx].strip().strip('"').strip("'")
        flag = raw.lower().split(":")[0]
        if flag in _PS_PATH_FLAGS:
            if idx + 1 < len(tokens):
                out.append(tokens[idx + 1].strip().strip('"').strip("'"))
                idx += 2
                continue
        if raw.startswith("-"):
            idx += 1
            continue
        if first_positional and raw:
            out.append(raw)
            first_positional = False
        idx += 1
    return out


def _is_shell_flag_token(tok: str) -> bool:
    s = tok.strip().strip('"').strip("'")
    if not s:
        return True
    if s.startswith("-"):
        return True
    return _nt_cmd_exe_switch_token(s)


def _basename_lower(cmd: str) -> str:
    base = Path(cmd.replace("\\", "/")).name.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def _segments_for_extract(command: str) -> list[str]:
    text = (command or "").strip()
    if not text:
        return []
    pr = parse_shell_for_permission(text)
    inv = [sc.text.strip() for sc in (pr.subcommands or ()) if sc.text and sc.text.strip()]
    heuristic = [p.strip() for p in _CHAIN_SPLIT_RE.split(text) if p.strip()]
    if len(inv) >= 2:
        return inv
    if len(heuristic) >= 2:
        return heuristic
    if len(inv) == 1:
        return inv
    return [text]


def _path_aware_one_segment(
    segment: str, cwd: Path,
) -> tuple[list[tuple[Path, FileAction]], Path | None]:
    try:
        tokens = shlex.split(segment.strip(), posix=False)
    except ValueError:
        tokens = segment.strip().split()
    if not tokens:
        return [], None
    cmd0 = _basename_lower(tokens[0])
    if cmd0 not in _PATH_AWARE_COMMANDS:
        return [], None
    base = cwd.resolve()
    path_tokens: list[tuple[Path, int]] = []

    def _append_path_token(tok: str, idx: int, *, require_path_shape: bool) -> None:
        tok = tok.strip().strip('"').strip("'")
        if not tok or _UNEXPANDED_VAR_RE.search(tok):
            return
        if require_path_shape and not _looks_like_path(tok):
            return
        p = Path(tok)
        if not p.is_absolute():
            p = base / tok
        try:
            path_tokens.append((p.resolve(), idx))
        except (OSError, RuntimeError):
            return

    for idx, tok in enumerate(tokens[1:]):
        tok = tok.strip().strip('"').strip("'")
        if not tok or _is_shell_flag_token(tok):
            continue
        _append_path_token(tok, idx, require_path_shape=True)

    if cmd0 in (
        "get-content", "gc", "set-content", "add-content", "out-file",
        "tee-object", "sc", "remove-item", "ri", "new-item", "ni",
    ):
        for extra in _collect_ps_path_tokens(tokens):
            _append_path_token(extra, len(path_tokens), require_path_shape=False)

    results: list[tuple[Path, FileAction]] = []
    new_cwd: Path | None = None

    if cmd0 in _TRANSFER_CMDS and len(path_tokens) >= 2:
        results.append((path_tokens[0][0], "read"))
        for p, _ in path_tokens[1:]:
            results.append((p, "write"))
    elif cmd0 in _WRITE_CMDS:
        for p, _ in path_tokens:
            results.append((p, "write"))
    elif cmd0 in _READ_CMDS:
        if not path_tokens and cmd0 in ("dir", "ls"):
            try:
                results.append((base.resolve(), "read"))
            except (OSError, RuntimeError):
                pass
        else:
            for p, _ in path_tokens:
                results.append((p, "read"))
    elif cmd0 == "cd":
        if path_tokens:
            for p, _ in path_tokens:
                results.append((p, "read"))
            new_cwd = path_tokens[-1][0]
        else:
            try:
                home = Path.home().resolve()
                results.append((home, "read"))
                new_cwd = home
            except (OSError, RuntimeError):
                pass
    else:
        for p, _ in path_tokens:
            results.append((p, "write"))
    return results, new_cwd


def extract_path_aware_command_accesses(
    command: str,
    workdir: str | Path,
) -> list[tuple[Path, FileAction]]:
    if not command or not isinstance(command, str):
        return []
    command = canonicalize_shell_command_for_permission(command)
    cwd = Path(workdir).resolve()
    combined: list[tuple[Path, FileAction]] = []
    for seg in _segments_for_extract(command):
        part, new_cwd = _path_aware_one_segment(seg, cwd)
        combined.extend(part)
        if new_cwd is not None:
            cwd = new_cwd
    return combined


def extract_shell_path_accesses(
    command: str,
    workdir: str | Path,
) -> list[tuple[Path, FileAction]]:
    if not command or not isinstance(command, str):
        return []
    command = canonicalize_shell_command_for_permission(command)
    base = Path(workdir).resolve()
    results: list[tuple[Path, FileAction]] = []

    def _resolve(tok: str, *, redirect: bool = False) -> Path | None:
        tok = tok.strip().strip('"').strip("'")
        if redirect:
            if not _looks_like_redirect_target(tok):
                return None
        elif not tok or not _looks_like_path(tok):
            return None
        p = Path(tok)
        if not p.is_absolute():
            p = base / tok
        try:
            return p.resolve()
        except (OSError, RuntimeError):
            return None

    for p, act in extract_path_aware_command_accesses(command, workdir):
        results.append((p, act))

    for m in re.finditer(r"(?:^|[\s;|&])(\d*>>?|\d*<|&>)\s*([^\s;|&<>]+)", command):
        op, target = m.group(1), m.group(2)
        rp = _resolve(target, redirect=True)
        if rp is None:
            continue
        if "<" in op and ">" not in op:
            results.append((rp, "read"))
        else:
            results.append((rp, "write"))

    try:
        tokens = shlex.split(command.strip(), posix=False)
    except ValueError:
        tokens = command.strip().split()
    if len(tokens) >= 2:
        cmd0 = _basename_lower(tokens[0])
        if cmd0 in _INTERPRETER_BASENAMES:
            script_tok = tokens[1].strip('"').strip("'")
            if script_tok and not script_tok.startswith("-"):
                rp = _resolve(script_tok)
                if rp is not None:
                    results.append((rp, "exec"))

    return results


def _resolve_path_str(raw: str, workspace: Path) -> Path | None:
    raw = raw.strip().strip('"').strip("'")
    if not raw:
        return None
    try:
        p = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not p.is_absolute():
            p = (workspace / p).resolve()
        else:
            p = p.resolve()
        return p
    except (OSError, RuntimeError):
        return None


def _specs_for_tool(tool_name: str) -> list[FileToolSpec] | None:
    return lookup_file_tool_specs(tool_name)


def extract_accesses_native(
    tool_name: str,
    tool_args: Mapping[str, Any],
    workspace: Path,
) -> list[tuple[Path, FileAction, str]]:
    """Native 抽取：``(path, action, source)``；source 为 ``tool_arg`` / ``shlex``。"""
    out: list[tuple[Path, FileAction, str]] = []

    if tool_name in ("mcp_exec_command", "bash", "powershell", "core.powershell", "create_terminal"):
        workdir = tool_args.get("workdir", "")
        try:
            workdir_resolved = (workspace / str(workdir)).resolve() if workdir else workspace
        except (OSError, RuntimeError):
            workdir_resolved = workspace
        # workdir 若已是绝对路径
        raw_wd = tool_args.get("workdir")
        if isinstance(raw_wd, str) and raw_wd.strip():
            try:
                wd_p = Path(raw_wd)
                if wd_p.is_absolute():
                    workdir_resolved = wd_p.resolve()
            except (OSError, RuntimeError):
                pass
        cmd = str(tool_args.get("command", "") or tool_args.get("cmd", ""))
        for p, act in extract_shell_path_accesses(cmd, workdir_resolved):
            out.append((p, act, "shlex"))
        return out

    specs = _specs_for_tool(tool_name)
    if specs:
        for spec in specs:
            raw = tool_args.get(spec.arg_name)
            if not isinstance(raw, str) or not raw.strip():
                continue
            rp = _resolve_path_str(raw, workspace)
            if rp is None:
                continue
            out.append((rp, spec.action, "tool_arg"))
        return out

    if tool_name in _PATH_TOOLS:
        action: FileAction = "write" if tool_name in _WRITE_PATH_TOOLS else "read"
        for s in _iter_path_strings(tool_name, tool_args):
            rp = _resolve_path_str(s, workspace)
            if rp is None:
                continue
            out.append((rp, action, "tool_arg"))
    return out
