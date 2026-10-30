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

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

from openjiuwen.core.sys_operation.cwd import get_cwd
from openjiuwen.harness.security.permission_engine.fileguard.outbound_paths import (
    is_public_file_url,
    normalize_file_path_list,
    resolve_outbound_path,
)
from openjiuwen.harness.security.permission_engine.fileguard.file_tool_specs import (
    FileToolSpec,
    lookup_file_tool_specs,
)
from openjiuwen.harness.security.permission_engine.delguard.del_count import (
    split_shell_operands,
)
from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
    canonicalize_shell_command_for_permission,
)
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import _PATH_TOOLS, _iter_path_strings

logger = logging.getLogger(__name__)

FileAction = Literal["read", "write", "exec", "delete"]

_MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])(?:/|$)")
_SHELL_WORD_RE = re.compile(r'''(?:"[^"]*"|'[^']*'|[^\s<>"']+)+|>>?|<''')
_POSIX_WORD_RE = re.compile(r'''(?:"(?:\\.|[^"\\])*"|'[^']*'|\\.|[^\s<>"'\\])+|>>?|<''')
_MAX_CWD_CANDIDATES = 128
_MAX_EXTRACTION_STEPS = 1024
_MAX_INLINE_DEPTH = 4

_PATH_AWARE_COMMANDS = frozenset({
    "cd", "rm", "cp", "mv", "mkdir", "touch", "chmod", "chown", "cat",
    "ls", "dir", "type", "del", "erase", "rd", "rmdir", "copy", "move", "md",
    "head", "tail", "more", "less", "vim", "nano", "gedit", "notepad",
    "get-content", "gc",
    "set-content", "add-content", "out-file", "tee", "tee-object", "sc",
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
    "mkdir", "touch", "chmod", "chown", "md",
    "set-content", "add-content", "out-file", "tee", "tee-object", "sc",
    "new-item", "ni",
})
# 删除不是写：不触发写前备份，审批卡标 delete。file_guard 仍按 write 轴判定。
_DELETE_CMDS = frozenset({
    "rm", "del", "erase", "rd", "rmdir", "remove-item", "ri",
})
_PS_PATH_FLAGS = frozenset({"-path", "-literalpath", "-filepath"})
_FD_ALIAS_RE = re.compile(r"^&\d+$")
_UNEXPANDED_VAR_RE = re.compile(r"(?i)\$(\w+|\{[^}]+\}|env:\w+)")
_TRANSFER_CMDS = frozenset({"cp", "copy", "mv", "move"})
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")


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
    """返回仍带引号的原始词，方便后续按引号外逗号拆 PowerShell 列表。"""
    out: list[str] = []
    first_positional = True
    idx = 1
    while idx < len(tokens):
        raw = tokens[idx].strip()
        bare = raw.strip('"').strip("'")
        flag = bare.lower().split(":")[0]
        if flag in _PS_PATH_FLAGS:
            if idx + 1 < len(tokens):
                out.append(tokens[idx + 1].strip())
                idx += 2
                continue
        if bare.startswith("-"):
            idx += 1
            continue
        if first_positional and raw:
            out.append(raw)
            first_positional = False
        idx += 1
    return out


def _is_shell_flag_token(tok: str, *, cmd0: str = "", shell_type: str = "auto") -> bool:
    s = tok.strip().strip('"').strip("'")
    if not s:
        return True
    if s.startswith("-"):
        return True
    if os.name == "nt" and shell_type in {"bash", "sh"} and _MSYS_DRIVE_RE.match(s):
        return False
    return _nt_cmd_exe_switch_token(s, cmd0=cmd0)


def _basename_lower(cmd: str) -> str:
    base = Path(cmd.replace("\\", "/")).name.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def _shell_segments(text: str, shell_type: str) -> list[tuple[str, str]]:
    """Keep literal redirects and the connector after each quoted shell segment."""
    parts: list[tuple[str, str]] = []
    buf: list[str] = []
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        escape = "\\" if shell_type in {"bash", "sh"} else "`" if shell_type == "powershell" else "^"
        if ch == escape and quote != "'" and i + 1 < len(text):
            buf.extend(text[i:i + 2])
            i += 2
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in ";\r\n|&" and not (
            ch == "&" and (text.startswith("&>", i) or (i > 0 and text[i - 1] in "><"))
        ):
            connector = next((op for op in ("&&", "||", "|&") if text.startswith(op, i)), ch)
            i += len(connector)
            part = "".join(buf).strip()
            if part:
                parts.append((part, connector))
            buf = []
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append((tail, ""))
    return parts


def _segment_tokens(segment: str, shell_type: str = "auto") -> tuple[list[str], list[tuple[str, FileAction]]]:
    """Separate redirects without mistaking quoted text or fd aliases for files."""
    pattern = _POSIX_WORD_RE if shell_type in {"bash", "sh"} else _SHELL_WORD_RE
    words = list(pattern.finditer(segment))
    tokens: list[str] = []
    redirects: list[tuple[str, FileAction]] = []
    idx = 0
    while idx < len(words):
        word = words[idx].group()
        if word in {">", ">>", "<"}:
            if idx and tokens and words[idx - 1].end() == words[idx].start():
                if tokens[-1].isdigit() or tokens[-1] == "&":
                    tokens.pop()
            if idx + 1 < len(words):
                target = words[idx + 1].group()
                if _looks_like_redirect_target(target):
                    redirects.append((target, "read" if word == "<" else "write"))
                idx += 1
        else:
            tokens.append(word)
        idx += 1
    return tokens, redirects


def _resolve_shell_operand(tok: str, cwd: Path, shell_type: str, *, bare: bool = False) -> Path | None:
    quoted = tok.startswith(('"', "'"))
    if shell_type in {"bash", "sh"} and not _is_windows_abs_text(tok):
        try:
            tok = shlex.split(tok, posix=True)[0]
        except (ValueError, IndexError):
            return None
    tok = tok.strip().strip('"').strip("'")
    if not tok or _UNEXPANDED_VAR_RE.search(tok) or (
        shell_type == "cmd" and re.search(r"%[^%\r\n]+%", tok)
    ):
        return None
    if not quoted and shell_type in {"bash", "sh"} and (tok == "~" or tok.startswith("~/")):
        tok = str(Path(os.environ.get("HOME") or Path.home()) / tok[2:])
    msys = _MSYS_DRIVE_RE.match(tok) if os.name == "nt" and shell_type in {"bash", "sh"} else None
    if not bare and not msys and not _looks_like_path(tok):
        return None
    if msys:
        tok = f"{msys.group(1).upper()}:/{tok[msys.end():]}"
    path = Path(tok)
    if not path.is_absolute() and not (os.name != "nt" and _is_windows_abs_text(tok)):
        path = cwd / path
    try:
        return _resolve_extract_path(path)
    except (OSError, RuntimeError):
        return None


def _interpreter_options(cmd: str) -> tuple[set[str], set[str], set[str], set[str], str] | None:
    """Return value, preload, inline and flag options for literal script calls."""
    if re.fullmatch(r"python(?:w|\d+(?:\.\d+)*)?", cmd) or cmd == "py":
        return (
            {"-W", "-X", "--check-hash-based-pycs"}, set(), {"-c", "-m"}, {"--version", "--help"},
            r"-[bBdEhiIOPqRsSuvVx?]+|-\d+(?:\.\d+)*|-V:.+",
        )
    if cmd in {"node", "nodejs"}:
        return (
            {"--title", "--stack-trace-limit", "--max-old-space-size", "--conditions", "-C"},
            {"-r", "--require", "--loader", "--experimental-loader", "--import"},
            {"-e", "--eval", "-p", "--print"},
            {"--trace-warnings", "--no-warnings", "--enable-source-maps", "--inspect", "--inspect-brk"}, r"-[iv]+",
        )
    if cmd in {"pwsh", "powershell"}:
        return (
            {"-executionpolicy", "-inputformat", "-outputformat", "-windowstyle"}, set(),
            {"-command", "-c", "-encodedcommand", "-enc"},
            {"-noprofile", "-noninteractive", "-nologo", "-noexit", "-file", "-f"}, r"(?!)",
        )
    if cmd in _INTERPRETER_BASENAMES:
        return (
            {"-o", "-O", "+o", "+O"}, set(), {"-c"}, {"--noprofile", "--norc", "--posix", "--login"},
            r"[-+][abefhkmnptuvxBCEHPT]+",
        )
    return None


def _interpreter_operands(tokens: list[str]) -> list[str]:
    """Find main/preload files; unknown option arity keeps all possible operands."""
    if not tokens:
        return []
    cmd = _basename_lower(tokens[0].strip('"').strip("'"))
    options = _interpreter_options(cmd)
    if options is None:
        return []
    values, loads, inline, flags, short = options
    out: list[str] = []
    uncertain = False
    idx = 1
    while idx < len(tokens):
        tok = tokens[idx]
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        if tok in {"--", "-"}:
            if tok == "--" and idx + 1 < len(tokens):
                out.append(tokens[idx + 1])
            break
        option, equals, value = tok.partition("=")
        if cmd in {"powershell", "pwsh"}:
            option = option.lower()
        if option in inline or (
            cmd in {"bash", "sh", "dash", "zsh", "fish"} and re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", tok)
        ) or (cmd.startswith("py") and re.fullmatch(r"-[bBdEhiIOPqRsSuvVx]*[cm].*", tok)):
            break  # Code strings / modules are not literal script paths.
        if option in values | loads:
            if not equals:
                idx += 1
                value = tokens[idx] if idx < len(tokens) else ""
            if option in loads and value:
                out.append(value)
        elif cmd in {"python", "python3", "pythonw", "py"} and tok[:2] in {"-W", "-X"}:
            pass  # Attached option value, e.g. -Wignore or -Xutf8.
        elif cmd in {"node", "nodejs"} and tok.startswith("-r") and len(tok) > 2:
            out.append(tok[2:])
        elif option in flags or re.fullmatch(short, tok):
            pass
        elif tok.startswith("-") or ("-o" in values and tok.startswith("+")):
            uncertain = True
        else:
            out.append(tokens[idx])
            if not uncertain:
                break
        idx += 1
    return out


def _path_aware_one_segment(
    segment: str, cwd: Path, *, include_cd_reads: bool = True, shell_type: str = "auto",
) -> tuple[list[tuple[Path, FileAction]], Path | None]:
    tokens, _ = _segment_tokens(segment, shell_type)
    if not tokens:
        return [], None
    cmd0 = _basename_lower(tokens[0].strip('"').strip("'"))
    if cmd0 not in _PATH_AWARE_COMMANDS:
        return [], None
    base = _resolve_extract_path(cwd)
    path_tokens: list[tuple[Path, int]] = []

    def _append_path_token(tok: str, idx: int, *, require_path_shape: bool) -> None:
        path = _resolve_shell_operand(tok, base, shell_type, bare=not require_path_shape)
        if path is not None:
            path_tokens.append((path, idx))

    for idx, tok in enumerate(tokens[1:]):
        if cmd0 in {"dir", "ls"} and any(ch in tok for ch in "*?") and not any(ch in tok for ch in "/\\"):
            continue  # A bare listing glob reads cwd, not a fictitious literal file.
        bare_read_operand = shell_type in {"cmd", "powershell"} and cmd0 in _READ_CMDS
        # tee / Tee-Object 的目标经常是裸文件名，不要求带斜杠。
        loose_operand = bare_read_operand or cmd0 in {"cd", "tee", "tee-object"}
        pieces = [tok] if shell_type == "cmd" and cmd0 not in _NT_CMD_SWITCH_COMMANDS else split_shell_operands(
            tok, shell_type=shell_type, cmd0=cmd0,
        )
        for piece in pieces:
            if _is_shell_flag_token(piece, cmd0=cmd0, shell_type=shell_type):
                continue
            _append_path_token(piece, idx, require_path_shape=not loose_operand)

    if cmd0 in (
        "get-content", "gc", "set-content", "add-content", "out-file",
        "tee-object", "sc", "remove-item", "ri", "new-item", "ni",
    ):
        for extra in _collect_ps_path_tokens(tokens):
            for piece in split_shell_operands(extra, shell_type=shell_type, cmd0=cmd0):
                if _is_shell_flag_token(piece, cmd0=cmd0, shell_type=shell_type):
                    continue
                _append_path_token(piece, len(path_tokens), require_path_shape=False)

    results: list[tuple[Path, FileAction]] = []
    new_cwd: Path | None = None

    if cmd0 in _TRANSFER_CMDS and len(path_tokens) >= 2:
        results.append((path_tokens[0][0], "read"))
        for p, _ in path_tokens[1:]:
            results.append((p, "write"))
    elif cmd0 in _DELETE_CMDS:
        for p, _ in path_tokens:
            results.append((p, "delete"))
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
            operands = [tok for tok in tokens[1:] if not _is_shell_flag_token(tok, cmd0="cd", shell_type=shell_type)]
            if len(operands) == len(path_tokens) == 1:
                new_cwd = path_tokens[0][0]
        else:
            try:
                home = Path.home().resolve()
                # No operand means home; an unresolved operand is not home.
                if len(tokens) == 1:
                    if include_cd_reads:
                        results.append((home, "read"))
                    new_cwd = home
            except (OSError, RuntimeError):
                pass
    else:
        for p, _ in path_tokens:
            results.append((p, "write"))
    return results, new_cwd


def _shell_segment_accesses(
    segment: str, cwd: Path, *, include_cd_reads: bool, shell_type: str, shell_extras: bool,
    depth: int, budget: list[int],
) -> tuple[list[tuple[Path, FileAction]], Path | None]:
    """Use one entry cwd for file operands, interpreter scripts and redirects."""
    part, new_cwd = _path_aware_one_segment(
        segment, cwd, include_cd_reads=include_cd_reads, shell_type=shell_type,
    )
    if shell_extras:
        tokens, redirects = _segment_tokens(segment, shell_type)
        for tok, action in [(tok, "exec") for tok in _interpreter_operands(tokens)] + redirects:
            path = _resolve_shell_operand(tok, cwd, shell_type, bare=True)
            if path is not None:
                part.append((path, action))
        inline = _literal_shell_inline(tokens)
        if inline is not None:
            if depth >= _MAX_INLINE_DEPTH:
                _raise_analysis_limit("inline shell depth limit")
            part.extend(_extract_segment_accesses(
                inline, cwd, include_cd_reads=include_cd_reads, shell_type="sh", shell_extras=True,
                depth=depth + 1, budget=budget,
            ))
    return part, new_cwd


def _literal_shell_inline(tokens: list[str]) -> str | None:
    """Read only literal Bash/Sh -c bodies; the child cwd never escapes."""
    if not tokens or _basename_lower(tokens[0].strip('"').strip("'")) not in {"bash", "sh", "dash", "zsh"}:
        return None
    for index, token in enumerate(tokens[1:], 1):
        if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", token) and index + 1 < len(tokens):
            raw = tokens[index + 1]
            # A single-quoted body is literal in the parent shell; variables
            # inside it are handled per operand by the recursive extractor.
            single_quoted = raw.startswith("'") and raw.endswith("'")
            if not single_quoted and (_UNEXPANDED_VAR_RE.search(raw) or "`" in raw):
                return None
            try:
                return shlex.split(raw, posix=True)[0]
            except (ValueError, IndexError):
                return None
        if not token.startswith("-"):
            break
    return None


def _raise_analysis_limit(reason: str) -> None:
    raise_error(
        StatusCode.TOOL_EXECUTION_ERROR, card=None, reason=reason,
        details={"rule_id": "file_guard:analysis_limit"},
    )


def _shell_profiles(command: str, shell_type: str) -> tuple[str, ...]:
    """Use the executor's Windows auto predicates, without launching a shell."""
    if shell_type == "auto":
        if os.name != "nt":
            return ("sh",)
        from openjiuwen.core.sys_operation.local.shell_operation import (
            _available_bash, _looks_like_posix, _looks_like_powershell, _unwrap_powershell_command,
        )
        if _unwrap_powershell_command(command) is not None or _looks_like_powershell(command):
            return ("powershell",)
        if _looks_like_posix(command) and _available_bash(allow_wsl=False):
            return ("bash",)
        return ("cmd",)
    return (shell_type,)


def _extract_segment_accesses(
    command: str, workdir: str | Path, *, include_cd_reads: bool, shell_type: str, shell_extras: bool,
    depth: int = 0, budget: list[int] | None = None,
) -> list[tuple[Path, FileAction]]:
    """Walk literal segments, conservatively retaining conditional cwd states."""
    results: list[tuple[Path, FileAction]] = []
    if budget is None:
        budget = [_MAX_EXTRACTION_STEPS]
    for profile in _shell_profiles(command, shell_type):
        segments = _shell_segments(command, profile)
        uncertain_cwd = any(op not in {"", "&&"} for _, op in segments)
        cwds = {_resolve_extract_path(Path(workdir))}
        for segment, _ in segments:
            next_cwds: set[Path] = set()
            for cwd in sorted(cwds, key=str):
                budget[0] -= 1
                if budget[0] < 0:
                    _raise_analysis_limit("shell path analysis work limit")
                part, new_cwd = _shell_segment_accesses(
                    segment, cwd, include_cd_reads=include_cd_reads, shell_type=profile, shell_extras=shell_extras,
                    depth=depth, budget=budget,
                )
                results.extend(part)
                if new_cwd is not None:
                    next_cwds.add(new_cwd)
                else:
                    next_cwds.add(cwd)
            # && reaches the following command only after a successful cd.
            # Other control flow can retain an earlier directory (cd failure,
            # pipeline child, or an unexecuted branch); keep those candidates.
            cwds = cwds | next_cwds if uncertain_cwd else next_cwds
            if len(cwds) > _MAX_CWD_CANDIDATES:
                _raise_analysis_limit("shell path extraction exceeded the cwd candidate limit")
    return list(dict.fromkeys(results))


def extract_path_aware_command_accesses(
    command: str,
    workdir: str | Path,
    *,
    include_cd_reads: bool = True,
    shell_type: str = "auto",
) -> list[tuple[Path, FileAction]]:
    if not command or not isinstance(command, str):
        return []
    shell_type = _shell_profiles(command, shell_type)[0]
    command = canonicalize_shell_command_for_permission(command)
    return _extract_segment_accesses(
        command, workdir, include_cd_reads=include_cd_reads, shell_type=shell_type, shell_extras=False,
    )


def extract_shell_path_accesses(
    command: str,
    workdir: str | Path,
    *,
    include_cd_reads: bool = True,
    shell_type: str = "auto",
) -> list[tuple[Path, FileAction]]:
    if not command or not isinstance(command, str):
        return []
    shell_type = _shell_profiles(command, shell_type)[0]
    if shell_type == "cmd":
        # Match cmd /s /c: remove the first and last double quotes when the
        # command starts with a quote. This is a permission-view copy only.
        command = command.strip()
        last_quote = command.rfind('"')
        if command.startswith('"') and last_quote > 0:
            command = command[1:last_quote] + command[last_quote + 1:]
    command = canonicalize_shell_command_for_permission(command)
    return _extract_segment_accesses(
        command, workdir, include_cd_reads=include_cd_reads, shell_type=shell_type, shell_extras=True,
    )


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


def shell_type_for_file_access(tool_name: str, tool_args: Mapping[str, Any]) -> str:
    if tool_name in {"powershell", "core.powershell"}:
        return "powershell"
    return str(tool_args.get("shell_type") or "auto").strip().lower()


def extract_accesses_native(
    tool_name: str,
    tool_args: Mapping[str, Any],
    workspace: Path,
    permission_config: Mapping[str, Any] | None = None,
) -> list[tuple[Path, FileAction, str]]:
    """Native 抽取：``(path, action, source)``；source 为 ``tool_arg`` / ``shlex``。"""
    out: list[tuple[Path, FileAction, str]] = []

    if tool_name == "send_file_to_user":
        return [(resolve_outbound_path(p), "read", "tool_arg")
                for p in normalize_file_path_list(tool_args.get("abs_file_path_list"))]
    if tool_name in {"save_media_to_gallery", "save_file_to_file_manager"}:
        raw = tool_args.get("url")
        if isinstance(raw, str) and raw.strip() and not is_public_file_url(raw):
            return [(resolve_outbound_path(raw), "read", "tool_arg")]
        return []

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
        for p, act in extract_shell_path_accesses(
            cmd, workdir_resolved, shell_type=shell_type_for_file_access(tool_name, tool_args),
        ):
            out.append((p, act, "shlex"))
        return out

    specs = _specs_for_tool(tool_name)
    if specs:
        for spec in specs:
            raw = tool_args.get(spec.arg_name)
            if tool_name == "glob" and spec.arg_name == "path":
                # Match GlobTool: omitted/empty path searches the agent CWD;
                # relative paths also resolve against CWD, not the workspace.
                raw = raw or "."
                if isinstance(raw, str):
                    try:
                        root = Path(get_cwd()).expanduser().resolve()
                        out.append(((root / Path(raw).expanduser()).resolve(), spec.action, "tool_arg"))
                    except (OSError, RuntimeError):
                        pass
                continue
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
