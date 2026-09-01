# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observed path extraction for structured file tools and shell commands.

Shell observations consume normalized ``ShellSubcommand.argv`` values from the
permission engine and intentionally do not claim complete runtime effects.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openjiuwen.harness.security.permission_engine.fileguard.file_tool_specs import (
    FileToolSpec,
    lookup_file_tool_specs,
)
from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
    canonicalize_shell_command_for_permission,
)
from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import (
    _ShellArgSyntax,
    _ShellLexeme,
    _executable_shell_lexemes,
    _lex_shell_words,
    _parse_shell_for_permission_details,
)
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import _PATH_TOOLS, _iter_path_strings

FileAction = Literal["read", "write", "exec"]

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
_TRANSFER_CMDS = frozenset({"cp", "copy", "mv", "move"})
_PLAIN_POSITIONAL_PATH_COMMANDS = frozenset({"cat", "type"})
_CONTROL_CHARS = frozenset("();&|<>")
_MAX_WRAPPER_DEPTH = 3


@dataclass(frozen=True, slots=True)
class _NormalizedShellSubcommand:
    """Private observation input shared by every public shell extractor."""

    argv: tuple[str, ...]
    argv_syntax: tuple[_ShellArgSyntax, ...]
    redirects: tuple[tuple[str, FileAction, _ShellArgSyntax], ...] = ()
    parent_operators: tuple[str, ...] = ()


def _nt_cmd_exe_switch_token(stripped: str) -> bool:
    if os.name != "nt" or not stripped.startswith("/") or stripped.startswith("//"):
        return False
    if "\\" in stripped:
        return False
    if stripped.count("/") != 1:
        return False
    return bool(_NT_CMD_SWITCH_BODY.match(stripped[1:]))


def _looks_like_path(token: str) -> bool:
    t = token
    if _nt_cmd_exe_switch_token(t):
        return False
    if t in (".", ".."):
        return True
    if t.startswith(("\\\\", "./", "../")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", t):
        return True
    return "\\" in t or "/" in t


def _collect_ps_path_tokens(
    tokens: tuple[str, ...],
    argv_syntax: tuple[_ShellArgSyntax, ...],
) -> list[tuple[str, _ShellArgSyntax]]:
    out: list[tuple[str, _ShellArgSyntax]] = []
    first_positional = True
    idx = 1
    while idx < len(tokens):
        raw = tokens[idx]
        flag = raw.lower().split(":")[0]
        if flag in _PS_PATH_FLAGS:
            if idx + 1 < len(tokens):
                out.append((tokens[idx + 1], argv_syntax[idx + 1]))
                idx += 2
                continue
        if raw.startswith("-"):
            idx += 1
            continue
        if first_positional and raw:
            out.append((raw, argv_syntax[idx]))
            first_positional = False
        idx += 1
    return out


def _is_shell_flag_token(tok: str) -> bool:
    s = tok
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


def _is_control_token(token: str) -> bool:
    return bool(token) and all(char in _CONTROL_CHARS for char in token)


def _is_static_operand(token: str) -> bool:
    if not token or token == "-":
        return False
    if _is_control_token(token):
        return False
    return True


def _lexical_absolute(
    token: str | Path,
    cwd: Path,
    *,
    expand_user: bool = False,
) -> Path:
    path = Path(token)
    if expand_user:
        raw = os.fspath(path)
        if raw == "~+" or raw.startswith("~+/"):
            suffix = raw[3:] if raw.startswith("~+/") else ""
            path = cwd / suffix
        else:
            path = path.expanduser()
    if not path.is_absolute():
        path = cwd / path
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _path_identities(path: Path) -> tuple[Path, ...]:
    """Keep lexical and resolved identities without claiming either is complete."""

    try:
        lexical = _lexical_absolute(path, Path.cwd())
        resolved = lexical.resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return ()
    return tuple(dict.fromkeys((lexical, resolved)))


def _observed_accesses(
    token: str,
    cwd: Path,
    action: FileAction,
    *,
    syntax: _ShellArgSyntax = _ShellArgSyntax(static=True, expand_user=False),
) -> list[tuple[Path, FileAction]]:
    if not syntax.static or not _is_static_operand(token):
        return []
    try:
        lexical = _lexical_absolute(token, cwd, expand_user=syntax.expand_user)
        resolved = lexical.resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return []
    if lexical == resolved:
        return [(resolved, action)]
    return [(lexical, action), (resolved, action)]


def _path_aware_argv_accesses(
    argv: tuple[str, ...],
    argv_syntax: tuple[_ShellArgSyntax, ...],
    cwd: Path,
) -> tuple[list[tuple[Path, FileAction]], Path | None]:
    if not argv or len(argv_syntax) != len(argv):
        return [], None
    cmd0 = _basename_lower(argv[0])
    if cmd0 not in _PATH_AWARE_COMMANDS:
        return [], None
    base = _lexical_absolute(cwd, Path.cwd())
    path_tokens: list[tuple[str, _ShellArgSyntax]] = []

    def _append_path_token(
        tok: str,
        *,
        syntax: _ShellArgSyntax,
        require_path_shape: bool,
    ) -> None:
        if not syntax.static or not _is_static_operand(tok):
            return
        if require_path_shape and not _looks_like_path(tok):
            return
        path_tokens.append((tok, syntax))

    for raw_token, syntax in zip(argv[1:], argv_syntax[1:], strict=True):
        tok = raw_token
        if not tok or _is_shell_flag_token(tok):
            continue
        _append_path_token(
            tok,
            syntax=syntax,
            require_path_shape=cmd0 not in _PLAIN_POSITIONAL_PATH_COMMANDS,
        )

    if cmd0 in (
        "get-content", "gc", "set-content", "add-content", "out-file",
        "tee-object", "sc", "remove-item", "ri", "new-item", "ni",
    ):
        for extra, syntax in _collect_ps_path_tokens(argv, argv_syntax):
            _append_path_token(
                extra,
                syntax=syntax,
                require_path_shape=False,
            )

    results: list[tuple[Path, FileAction]] = []
    new_cwd: Path | None = None

    if cmd0 in _TRANSFER_CMDS and len(path_tokens) >= 2:
        results.extend(_observed_accesses(path_tokens[0][0], base, "read", syntax=path_tokens[0][1]))
        for token, syntax in path_tokens[1:]:
            results.extend(_observed_accesses(token, base, "write", syntax=syntax))
    elif cmd0 in _WRITE_CMDS:
        for token, syntax in path_tokens:
            results.extend(_observed_accesses(token, base, "write", syntax=syntax))
    elif cmd0 in _READ_CMDS:
        if not path_tokens and cmd0 in ("dir", "ls"):
            results.extend(_observed_accesses(".", base, "read"))
        else:
            for token, syntax in path_tokens:
                results.extend(_observed_accesses(token, base, "read", syntax=syntax))
    elif cmd0 == "cd":
        if path_tokens:
            for token, syntax in path_tokens:
                results.extend(_observed_accesses(token, base, "read", syntax=syntax))
            try:
                new_cwd = _lexical_absolute(
                    path_tokens[-1][0],
                    base,
                    expand_user=path_tokens[-1][1].expand_user,
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                new_cwd = None
        else:
            try:
                home = Path.home().resolve()
                results.append((home, "read"))
                new_cwd = home
            except (OSError, RuntimeError):
                pass
    else:
        for token, syntax in path_tokens:
            results.extend(_observed_accesses(token, base, "write", syntax=syntax))
    return results, new_cwd


def _timeout_inner(argv: tuple[str, ...]) -> tuple[str, ...] | None:
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "--":
            index += 1
            break
        if not token.startswith("-") or token == "-":
            break
        if token in {"-k", "--kill-after", "-s", "--signal"}:
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if token.startswith(("--kill-after=", "--signal=")) or token in {
            "--foreground",
            "--preserve-status",
            "--verbose",
        }:
            index += 1
            continue
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1 :]


def _interpreter_script(argv: tuple[str, ...]) -> tuple[str, int] | None:
    if len(argv) < 2:
        return None
    command = _basename_lower(argv[0])
    index = 1
    if argv[index] == "--":
        index += 1
    elif command in {"pwsh", "powershell"} and argv[index].lower() == "-file":
        index += 1
    elif argv[index].startswith("-"):
        return None
    if index >= len(argv) or not _is_static_operand(argv[index]):
        return None
    return argv[index], index


def _sed_accesses(
    argv: tuple[str, ...],
    argv_syntax: tuple[_ShellArgSyntax, ...],
    cwd: Path,
    *,
    command: str,
) -> list[tuple[Path, FileAction]]:
    dialect = _sed_dialect(command)
    index = 1
    in_place = False
    expression_seen = False
    program_files: list[tuple[str, _ShellArgSyntax]] = []
    operands: list[tuple[str, _ShellArgSyntax]] = []
    while index < len(argv):
        token = argv[index]
        if token == "--":
            operands.extend(zip(argv[index + 1 :], argv_syntax[index + 1 :], strict=True))
            break
        if token in {"-e", "--expression"}:
            if index + 1 >= len(argv):
                return []
            expression_seen = True
            index += 2
            continue
        if token in {"-f", "--file"}:
            if index + 1 >= len(argv):
                return []
            program_files.append((argv[index + 1], argv_syntax[index + 1]))
            expression_seen = True
            index += 2
            continue
        if token == "-i":
            if dialect == "bsd":
                if index + 1 >= len(argv):
                    return []
                index += 2
            elif dialect == "gnu":
                index += 1
            else:
                return []
            in_place = True
            continue
        if token.startswith("-i"):
            in_place = True
            index += 1
            continue
        if token == "--in-place" or token.startswith("--in-place="):
            if dialect != "gnu":
                return []
            in_place = True
            index += 1
            continue
        if token.startswith("-") and token != "-":
            option_body = token[1:]
            if "i" in option_body and all(char in "Enrisuz" for char in option_body):
                if dialect == "bsd" and option_body.endswith("i"):
                    if index + 1 >= len(argv):
                        return []
                    index += 2
                elif dialect == "gnu":
                    index += 1
                else:
                    return []
                in_place = True
                continue
            if all(char in "Enrsuz" for char in option_body):
                index += 1
                continue
            return []
        operands.append((token, argv_syntax[index]))
        index += 1
    if not expression_seen:
        if not operands:
            return []
        operands = operands[1:]
    accesses: list[tuple[Path, FileAction]] = []
    for token, syntax in program_files:
        accesses.extend(_observed_accesses(token, cwd, "read", syntax=syntax))
    for token, syntax in operands:
        accesses.extend(_observed_accesses(token, cwd, "read", syntax=syntax))
        if in_place:
            accesses.extend(_observed_accesses(token, cwd, "write", syntax=syntax))
    return accesses


def _sed_dialect(command: str) -> str:
    if command == "gsed" or sys.platform.startswith("linux"):
        return "gnu"
    if sys.platform == "darwin" or "bsd" in sys.platform:
        return "bsd"
    return "unknown"


def _observe_timeout(
    argv: tuple[str, ...],
    argv_syntax: tuple[_ShellArgSyntax, ...],
    cwd: Path,
    depth: int,
) -> list[tuple[Path, FileAction]]:
    if os.name == "nt" or depth >= _MAX_WRAPPER_DEPTH:
        return []
    inner = _timeout_inner(argv)
    if inner is None:
        return []
    start = len(argv) - len(inner)
    return _observe_command_argv(
        inner,
        argv_syntax[start:],
        cwd,
        depth=depth + 1,
    )


def _observe_interpreter(
    argv: tuple[str, ...],
    argv_syntax: tuple[_ShellArgSyntax, ...],
    cwd: Path,
    _depth: int,
) -> list[tuple[Path, FileAction]]:
    script = _interpreter_script(argv)
    if script is None:
        return []
    token, index = script
    return _observed_accesses(
        token,
        cwd,
        "exec",
        syntax=argv_syntax[index],
    )


def _observe_sed(
    argv: tuple[str, ...],
    argv_syntax: tuple[_ShellArgSyntax, ...],
    cwd: Path,
    _depth: int,
) -> list[tuple[Path, FileAction]]:
    if os.name == "nt":
        return []
    return _sed_accesses(
        argv,
        argv_syntax,
        cwd,
        command=_basename_lower(argv[0]),
    )


_CommandObserver = Callable[
    [tuple[str, ...], tuple[_ShellArgSyntax, ...], Path, int],
    list[tuple[Path, FileAction]],
]

_COMMAND_OBSERVERS: dict[str, _CommandObserver] = {
    "timeout": _observe_timeout,
    "gtimeout": _observe_timeout,
    "sed": _observe_sed,
    "gsed": _observe_sed,
    **{name: _observe_interpreter for name in _INTERPRETER_BASENAMES},
}


def _observe_command_argv(
    argv: tuple[str, ...] | None,
    argv_syntax: tuple[_ShellArgSyntax, ...],
    cwd: Path,
    *,
    depth: int = 0,
) -> list[tuple[Path, FileAction]]:
    if not argv or len(argv_syntax) != len(argv):
        return []
    observer = _COMMAND_OBSERVERS.get(_basename_lower(argv[0]))
    return observer(argv, argv_syntax, cwd, depth) if observer is not None else []


def _normalize_redirect_text(
    redirect: str,
) -> tuple[str, FileAction, _ShellArgSyntax] | None:
    match = re.match(
        r"^\s*(\d*>>?|\d*<|&>>?|[<>]&)\s*(.+?)\s*$",
        redirect,
    )
    if match is None:
        return None
    operator, raw_target = match.groups()
    targets = _lex_shell_words(raw_target)
    if targets is None:
        return None
    if len(targets) != 1 or targets[0].control or _FD_ALIAS_RE.fullmatch(targets[0].value):
        return None
    action: FileAction = "read" if "<" in operator and ">" not in operator else "write"
    return targets[0].value, action, targets[0].syntax


def _redirect_accesses(
    redirects: tuple[tuple[str, FileAction, _ShellArgSyntax], ...],
    cwd: Path,
) -> list[tuple[Path, FileAction]]:
    accesses: list[tuple[Path, FileAction]] = []
    for target, action, syntax in redirects:
        accesses.extend(
            _observed_accesses(
                target,
                cwd,
                action,
                syntax=syntax,
            )
        )
    return accesses


def _fallback_tokens(command: str) -> tuple[_ShellLexeme, ...] | None:
    return _lex_shell_words(command)


def _leading_fallback_argv(
    tokens: tuple[_ShellLexeme, ...],
) -> tuple[_ShellLexeme, ...] | None:
    leading: list[_ShellLexeme] = []
    for token in tokens:
        if token.control:
            break
        leading.append(token)
    executable = _executable_shell_lexemes(tuple(leading))
    return executable or None


def _fallback_redirects(
    tokens: tuple[_ShellLexeme, ...],
) -> tuple[tuple[str, FileAction, _ShellArgSyntax], ...]:
    redirects: list[tuple[str, FileAction, _ShellArgSyntax]] = []
    for index, operator in enumerate(tokens[:-1]):
        if operator.value not in {">", ">>", "<", "&>", "&>>", ">&", "<&"}:
            continue
        target = tokens[index + 1]
        if target.control:
            continue
        if operator.value in {">&", "<&"} and target.value.isdigit():
            continue
        action: FileAction = "read" if operator.value in {"<", "<&"} else "write"
        if _is_static_operand(target.value):
            redirects.append((target.value, action, target.syntax))
    return tuple(dict.fromkeys(redirects))


def _normalize_shell_subcommands(command: str) -> tuple[_NormalizedShellSubcommand, ...]:
    """Normalize tree-sitter and fallback output for one shared traversal."""

    parsed, argv_syntax_by_subcommand = _parse_shell_for_permission_details(command)
    if parsed.kind == "simple" and parsed.subcommands:
        normalized: list[_NormalizedShellSubcommand] = []
        for index, subcommand in enumerate(parsed.subcommands):
            redirects = tuple(
                redirect
                for raw_redirect in subcommand.redirects
                if (redirect := _normalize_redirect_text(raw_redirect)) is not None
            )
            normalized.append(
                _NormalizedShellSubcommand(
                    argv=subcommand.argv,
                    argv_syntax=(
                        argv_syntax_by_subcommand[index]
                        if index < len(argv_syntax_by_subcommand)
                        and len(argv_syntax_by_subcommand[index])
                        == len(subcommand.argv)
                        else tuple(
                            _ShellArgSyntax(False, False)
                            for _ in subcommand.argv
                        )
                    ),
                    redirects=redirects,
                    parent_operators=subcommand.parent_operators,
                )
            )
        return tuple(normalized)

    fallback_tokens = _fallback_tokens(command)
    if fallback_tokens is None:
        return ()
    fallback_argv = _leading_fallback_argv(fallback_tokens) or ()
    return (
        _NormalizedShellSubcommand(
            argv=tuple(token.value for token in fallback_argv),
            argv_syntax=tuple(token.syntax for token in fallback_argv),
            redirects=_fallback_redirects(fallback_tokens),
        ),
    )


def _dedupe_accesses(
    accesses: list[tuple[Path, FileAction]],
) -> list[tuple[Path, FileAction]]:
    result: list[tuple[Path, FileAction]] = []
    seen: set[tuple[str, FileAction]] = set()
    for path, action in accesses:
        key = (path.as_posix(), action)
        if key in seen:
            continue
        seen.add(key)
        result.append((path, action))
    return result


def _extract_observed_shell_accesses(
    command: str,
    workdir: str | Path,
) -> list[tuple[Path, FileAction]]:
    if not command or not isinstance(command, str):
        return []
    command = canonicalize_shell_command_for_permission(command)
    try:
        cwd = _lexical_absolute(workdir, Path.cwd())
    except (OSError, RuntimeError, TypeError, ValueError):
        return []
    subcommands = _normalize_shell_subcommands(command)
    if not subcommands:
        return []
    results: list[tuple[Path, FileAction]] = []
    # Shell parsing observes possible accesses; it does not prove runtime
    # branch outcomes. Preserve the invocation cwd and each sequential
    # lexical/resolved cd candidate so a failed cd cannot hide a later
    # relative access. This is deliberately bounded by the number of cd
    # subcommands rather than branching over every success combination.
    observed_cwds = list(_path_identities(cwd)) or [cwd]
    current_cwd = cwd
    for subcommand in subcommands:
        sequential_new_cwd: Path | None = None
        for candidate_cwd in tuple(observed_cwds):
            path_aware, new_cwd = _path_aware_argv_accesses(
                subcommand.argv,
                subcommand.argv_syntax,
                candidate_cwd,
            )
            results.extend(path_aware)
            results.extend(
                _observe_command_argv(
                    subcommand.argv,
                    subcommand.argv_syntax,
                    candidate_cwd,
                )
            )
            results.extend(_redirect_accesses(subcommand.redirects, candidate_cwd))
            if candidate_cwd == current_cwd:
                sequential_new_cwd = new_cwd
        if sequential_new_cwd is not None and not any(
            operator in {"|", "|&"}
            for operator in subcommand.parent_operators
        ):
            current_cwd = sequential_new_cwd
            for identity in _path_identities(current_cwd):
                if identity not in observed_cwds:
                    observed_cwds.append(identity)
    return _dedupe_accesses(results)


def extract_shell_path_accesses(
    command: str,
    workdir: str | Path,
) -> list[tuple[Path, FileAction]]:
    return _extract_observed_shell_accesses(command, workdir)


def extract_path_aware_command_accesses(
    command: str,
    workdir: str | Path,
) -> list[tuple[Path, FileAction]]:
    return _extract_observed_shell_accesses(command, workdir)


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
        if not workdir and tool_name == "bash":
            workdir = tool_args.get("cwd", "")
        try:
            workspace_lexical = _lexical_absolute(workspace, Path.cwd())
            workdir_resolved = (
                _lexical_absolute(str(workdir), workspace_lexical)
                if workdir
                else workspace_lexical
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            workdir_resolved = workspace
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
