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
    "ls", "dir", "type", "del", "erase", "rd", "rmdir", "copy", "move", "md",
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
_NT_CMD_SWITCH_COMMANDS = frozenset({
    "cd", "dir", "del", "erase", "copy", "move", "rd", "rmdir", "md",
    "type", "ren", "rename", "xcopy", "attrib", "cmd", "notepad",
})

_READ_CMDS = frozenset({
    "cat", "ls", "dir", "type", "head", "tail", "more", "less",
    "get-content", "gc",
})
_WRITE_CMDS = frozenset({
    "rm", "mkdir", "touch", "chmod", "chown", "del", "erase", "rd", "rmdir", "md",
    "set-content", "add-content", "out-file", "tee-object", "sc",
    "remove-item", "ri", "new-item", "ni",
})
_PS_PATH_FLAGS = frozenset({"-path", "-literalpath", "-filepath"})
_FD_ALIAS_RE = re.compile(r"^&\d+$")
_UNEXPANDED_VAR_RE = re.compile(r"(?i)\$(\w+|\{[^}]+\}|env:\w+)")
_QUOTED_SPAN_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_TRANSFER_CMDS = frozenset({"cp", "copy", "mv", "move"})
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_WIN_USERPROFILE_RE = re.compile(r"%USERPROFILE%", re.IGNORECASE)
_WIN_HOMEDRIVE_HOMEPATH_RE = re.compile(r"%HOMEDRIVE%%HOMEPATH%", re.IGNORECASE)


def _is_windows_abs_text(text: str) -> bool:
    return bool(_WIN_ABS_RE.match(str(text).strip().strip('"').strip("'")))


def _resolve_extract_path(path: Path) -> Path:
    """Resolve local paths; keep Windows drive paths intact on POSIX."""
    if os.name != "nt" and _is_windows_abs_text(str(path)):
        return path
    return path.resolve()


def _nt_cmd_exe_switch_token(stripped: str, *, cmd0: str = "") -> bool:
    """True for cmd.exe switches such as ``/b``, ``/d``, ``/s/q``.

    Linux still sees Windows ``dir /b`` / ``cd /d`` (via ``cmd //c``). Treat
    those tokens as switches for cmd builtins even when ``os.name != "nt"``.
    POSIX tools like ``ls /b`` or ``cat /tmp`` keep the token as a path.
    """
    if not stripped.startswith("/") or stripped.startswith("//"):
        return False
    if "\\" in stripped:
        return False
    parts = [part for part in stripped.split("/") if part]
    if not parts or not all(_NT_CMD_SWITCH_BODY.match(part) for part in parts):
        return False
    if os.name == "nt":
        return True
    return cmd0 in _NT_CMD_SWITCH_COMMANDS


def _looks_like_path(token: str) -> bool:
    t = token.strip().strip('"').strip("'")
    if _nt_cmd_exe_switch_token(t):
        return False
    if t in (".", "..", "~") or t.startswith(("~/", "~\\")):
        return True
    if t.startswith(("\\\\", "./", "../")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", t):
        return True
    return "\\" in t or "/" in t


def _join_home_rest(rest: str) -> str:
    rest = rest.replace("\\", "/").lstrip("/")
    parts = [p for p in rest.split("/") if p]
    return str(Path.home().joinpath(*parts)) if parts else str(Path.home())


def _unglue_home_hidden(path_text: str) -> str:
    """Insert ``/`` if home was concatenated onto ``.kube`` after a lost ``\\.``."""
    home = Path.home().as_posix().rstrip("/")
    text = path_text.replace("\\", "/")
    prefix = home + "."
    if text.startswith(prefix):
        return home + "/" + text[len(home):]
    return text


def _expand_home_token(tok: str) -> str:
    """Expand ``~`` / ``~/`` / ``~\\`` and Windows home env vars to the user home path."""
    t = tok.strip().strip('"').strip("'")
    if not t:
        return t
    if t == "~":
        return str(Path.home())
    if t.startswith(("~/", "~\\", "~.")):
        return _join_home_rest(t[1:])
    expanded = _WIN_HOMEDRIVE_HOMEPATH_RE.sub(lambda _m: Path.home().as_posix(), t)
    expanded = _WIN_USERPROFILE_RE.sub(lambda _m: Path.home().as_posix(), expanded)
    if expanded == t:
        return t
    return _unglue_home_hidden(expanded.replace("\\", "/"))


def _looks_like_redirect_target(token: str) -> bool:
    t = token.strip().strip('"').strip("'")
    if not t or _FD_ALIAS_RE.match(t):
        return False
    if t.startswith("$") or _UNEXPANDED_VAR_RE.search(t):
        return False
    return True


def _quoted_spans(command: str) -> tuple[tuple[int, int], ...]:
    return tuple((m.start(), m.end()) for m in _QUOTED_SPAN_RE.finditer(command))


def _index_in_quoted_span(index: int, spans: tuple[tuple[int, int], ...]) -> bool:
    return any(start < index < end for start, end in spans)


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


def _is_shell_flag_token(tok: str, *, cmd0: str = "") -> bool:
    s = tok.strip().strip('"').strip("'")
    if not s:
        return True
    if s.startswith("-"):
        return True
    return _nt_cmd_exe_switch_token(s, cmd0=cmd0)


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
    segment: str, cwd: Path, *, include_cd_reads: bool = True,
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
    base = _resolve_extract_path(cwd)
    path_tokens: list[tuple[Path, int]] = []

    def _append_path_token(tok: str, idx: int, *, require_path_shape: bool) -> None:
        tok = tok.strip().strip('"').strip("'")
        if not tok or _UNEXPANDED_VAR_RE.search(tok):
            return
        if require_path_shape and not _looks_like_path(tok):
            return
        tok = _expand_home_token(tok)
        p = Path(tok)
        win_abs = _is_windows_abs_text(tok)
        if not p.is_absolute() and not (win_abs and os.name != "nt"):
            p = base / tok
        try:
            if win_abs and os.name != "nt":
                path_tokens.append((p, idx))
            else:
                path_tokens.append((_resolve_extract_path(p), idx))
        except (OSError, RuntimeError):
            return

    for idx, tok in enumerate(tokens[1:]):
        tok = tok.strip().strip('"').strip("'")
        if not tok or _is_shell_flag_token(tok, cmd0=cmd0):
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
                results.append((_resolve_extract_path(base), "read"))
            except (OSError, RuntimeError):
                pass
        else:
            for p, _ in path_tokens:
                results.append((p, "read"))
    elif cmd0 == "cd":
        if path_tokens:
            if include_cd_reads:
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
    *,
    include_cd_reads: bool = True,
) -> list[tuple[Path, FileAction]]:
    if not command or not isinstance(command, str):
        return []
    command = canonicalize_shell_command_for_permission(command)
    cwd = Path(workdir).resolve()
    combined: list[tuple[Path, FileAction]] = []
    for seg in _segments_for_extract(command):
        part, new_cwd = _path_aware_one_segment(
            seg, cwd, include_cd_reads=include_cd_reads,
        )
        combined.extend(part)
        if new_cwd is not None:
            cwd = new_cwd
    return combined


def extract_shell_path_accesses(
    command: str,
    workdir: str | Path,
    *,
    include_cd_reads: bool = True,
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
        tok = _expand_home_token(tok)
        p = Path(tok)
        win_abs = _is_windows_abs_text(tok)
        if not p.is_absolute() and not (win_abs and os.name != "nt"):
            p = base / tok
        try:
            if win_abs and os.name != "nt":
                return p
            return _resolve_extract_path(p)
        except (OSError, RuntimeError):
            return None

    for p, act in extract_path_aware_command_accesses(
        command, workdir, include_cd_reads=include_cd_reads,
    ):
        results.append((p, act))

    quoted = _quoted_spans(command)
    for m in re.finditer(r"(?:^|[\s;|&])(\d*>>?|\d*<|&>)\s*([^\s;|&<>]+)", command):
        if _index_in_quoted_span(m.start(1), quoted):
            continue
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
    permission_config: Mapping[str, Any] | None = None,
) -> list[tuple[Path, FileAction, str]]:
    """Native 抽取：``(path, action, source)``；source 为 ``tool_arg`` / ``shlex``。"""
    out: list[tuple[Path, FileAction, str]] = []

    from openjiuwen.harness.security.permission_engine.toolguard.tool_categories import (
        is_shell_tool,
        shell_tools_from_config,
    )

    if is_shell_tool(tool_name, shell_tools_from_config(permission_config)):
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
