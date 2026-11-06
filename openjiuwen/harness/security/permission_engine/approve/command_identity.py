# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Literal argv identities with declarative, implementation-bound path schemas.

Unknown commands/options retain every argv byte after shell quote decoding.
Only registered operands become paths. No flag, value or operand is dropped,
sorted or treated as an alias; file effects never authorize a command ASK.
"""

import os
import re
import shlex
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from openjiuwen.core.common.exception.errors import BaseError


@dataclass(frozen=True)
class OptionSpec:
    """An option's arity and optional filesystem operand role."""

    names: tuple[str, ...]
    takes_value: bool = False
    action: str | None = None


@dataclass(frozen=True)
class CommandSchema:
    """Server-owned operand grammar, independent of grant matching/storage."""

    name: str
    layout: str
    options: tuple[OptionSpec, ...] = ()
    dialect: str = "bash"
    revision: int = 1
    effect_options: tuple[str, ...] = ()


_SCHEMAS: dict[tuple[str, str], CommandSchema] = {}
_ACTIONS = frozenset({"read", "write", "delete"})
_STATE_COMMANDS = frozenset(
    {
        "cd",
        "pushd",
        "popd",
        "export",
        "set",
        "unset",
        "alias",
        "unalias",
        "source",
        ".",
        "eval",
        "exec",
        "env",
        "command",
        "builtin",
        "hash",
        "read",
        "enable",
    }
)
_PURE_BUILTINS = frozenset({"echo", "printf", "pwd", "true", "false", "test", "["})
_NATIVE_COMMAND = ContextVar("permission_native_command", default=None)


@contextmanager
def bind_native_command(tool: str, args: dict, identity: dict | None, *, words: list[str] | None = None):
    """Bind authenticated host argv, scoped to one async permission evaluation."""
    token = _NATIVE_COMMAND.set((tool, dict(args), identity, tuple(words) if words else None))
    try:
        yield
    finally:
        _NATIVE_COMMAND.reset(token)


def bound_native_identity(tool: str, args: dict) -> tuple[bool, dict | None]:
    """Do not reinterpret native data as another shell invocation."""
    bound = _NATIVE_COMMAND.get()
    if bound is None or bound[0] != tool:
        return False, None
    # Inner policy candidates can differ from the original command text. They
    # still must not acquire guessed semantic identities during a native check.
    return True, bound[2] if args == bound[1] else None


def restriction_command_views(tool: str, args: dict) -> tuple[str, ...]:
    """Additional argv views for DENY/ASK only, never authorization equivalence.

    Native words come exclusively from the authenticated host context. For a
    shell entry, decode only a single literal invocation. Re-quote every value
    independently so data cannot become another command or an extra argument.
    No options, values, argument boundaries or ordering are changed.
    """
    bound = _NATIVE_COMMAND.get()
    if bound is not None and bound[0] == tool:
        words = bound[3] if args == bound[1] else None
    else:
        command = args.get("command") or args.get("cmd") or ""
        shell = args.get("shell_type") or (
            "powershell" if tool in {"powershell", "pwsh", "core.powershell"} else "bash" if tool == "bash" else ""
        )
        words = literal_shell_words(command, shell)
    if not words:
        return ()
    executable = words[0].replace("\\", "/")
    basename = executable.rsplit("/", 1)[-1]
    # An executable suffix is launcher syntax on Windows, not an argv option.
    # Do not strip version numbers or other name suffixes.
    name = basename[:-4] if os.name == "nt" and basename.lower().endswith(".exe") else basename
    heads = (executable, basename, name)
    if os.name == "nt":
        heads += tuple(head.lower() for head in heads)
    return tuple(dict.fromkeys(shlex.join([head, *words[1:]]) for head in heads))


def literal_shell_words(command: str, shell: str) -> list[str] | None:
    """Decode literal shell syntax without resolving programs or rewriting paths."""
    if not isinstance(command, str):
        return None
    try:
        if shell in {"powershell", "pwsh"}:
            return _powershell_words(command)
        if shell in {"bash", "sh"}:
            return _bash_words(command)
    except (ImportError, RuntimeError, TypeError, ValueError):
        return None
    return None


def register_command_schema(schema: CommandSchema) -> None:
    """Register trusted code metadata, never model/tool-supplied configuration."""
    if (
        schema.layout not in {"read", "write", "delete", "copy", "move", "grep", "named"}
        or schema.dialect not in {"bash", "powershell"}
        or schema.revision < 1
    ):
        raise ValueError("Invalid command schema")
    for option in schema.options:
        if not option.names or option.action not in _ACTIONS | {None}:
            raise ValueError("Invalid command option")
        if option.action and not option.takes_value:
            raise ValueError("Invalid command option")
    _SCHEMAS[(schema.dialect, schema.name.casefold())] = schema


def _flags(*names: str) -> OptionSpec:
    return OptionSpec(names)


def _value(*names: str, action: str | None = None) -> OptionSpec:
    return OptionSpec(names, True, action)


for _name, _layout, _options in (
    ("rm", "delete", (_flags("-f", "--force", "-i", "-I", "--interactive", "-r", "-R", "--recursive", "-d", "-v"),)),
    ("unlink", "delete", ()),
    ("cat", "read", (_flags("-n", "-b", "-s", "-v", "-E", "-T", "-A", "--number", "--squeeze-blank"),)),
    ("head", "read", (_flags("-q", "-v"), _value("-n", "--lines", "-c", "--bytes"))),
    ("tail", "read", (_flags("-q", "-v", "-f", "--follow"), _value("-n", "--lines", "-c", "--bytes"))),
    ("wc", "read", (_flags("-c", "-m", "-l", "-w", "-L"),)),
    ("ls", "read", (_flags("-a", "-A", "-l", "-d", "-h", "-R", "-1"),)),
    ("cp", "copy", (_flags("-f", "-i", "-n", "-r", "-R", "-p", "-a", "-T", "--no-clobber"),)),
    ("mv", "move", (_flags("-f", "-i", "-n", "-T", "--no-clobber"),)),
    ("mkdir", "write", (_flags("-p", "--parents", "-v"), _value("-m", "--mode"))),
    (
        "touch",
        "write",
        (
            _flags("-a", "-m", "-c", "--no-create"),
            _value("-d", "--date", "-t"),
            _value("-r", "--reference", action="read"),
        ),
    ),
    (
        "grep",
        "grep",
        (
            _flags("-i", "-v", "-n", "-l", "-c", "-E", "-F", "-w", "-r", "-R"),
            _value("-e", "--regexp"),
            _value("-f", "--file", action="read"),
            _value("-m", "--max-count", "-A", "-B", "-C"),
        ),
    ),
):
    register_command_schema(
        CommandSchema(_name, _layout, _options, effect_options=("-f", "--force", "-v") if _name == "rm" else ())
    )

for _name, _options in (
    (
        "Get-Content",
        (
            _value("-LiteralPath", "-Path", action="read"),
            _flags("-Raw", "-Wait"),
            _value("-TotalCount", "-Tail", "-Encoding"),
        ),
    ),
    (
        "Set-Content",
        (
            _value("-LiteralPath", "-Path", action="write"),
            _value("-Value", "-Encoding"),
            _flags("-Force", "-NoNewline"),
        ),
    ),
    ("Remove-Item", (_value("-LiteralPath", "-Path", action="delete"), _flags("-Force", "-Recurse"))),
    (
        "Copy-Item",
        (
            _value("-LiteralPath", "-Path", action="read"),
            _value("-Destination", action="write"),
            _flags("-Force", "-Recurse"),
        ),
    ),
    (
        "Move-Item",
        (_value("-LiteralPath", "-Path", action="delete"), _value("-Destination", action="write"), _flags("-Force")),
    ),
):
    register_command_schema(CommandSchema(_name, "named", _options, "powershell"))


def _powershell_words(source: str) -> list[str] | None:  # pylint: disable=too-many-return-statements
    """Decode a small literal PowerShell invocation; reject expressions/expansion."""
    if "\n" in source or "\r" in source:
        return None
    text = source.strip()
    invocation = text.startswith("&")
    if invocation:
        text = text[1:].lstrip()
    if not invocation and text.startswith(("'", '"')):
        return None  # A quoted expression is not a program invocation.
    token = re.compile(r"'((?:[^']|'')*)'|\"([^\"$`]*)\"|([^\s'\"$`;&|(){}<>@#,\[\]]+)")
    words, cursor = [], 0
    while cursor < len(text):
        match = token.match(text, cursor)
        if match is None:
            return None
        value = match[1].replace("''", "'") if match[1] is not None else match[2] if match[2] is not None else match[3]
        # Quoting a parameter-looking token changes PowerShell parameter
        # binding. --% also changes parsing of the remainder, unlike argv data.
        quoted = match[1] is not None or match[2] is not None
        if value == "--%" or (quoted and value.startswith("-")):
            return None
        words.append(value)
        cursor = match.end()
        if cursor < len(text) and not text[cursor].isspace():
            return None
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
    return words


def _bash_words(source: str) -> list[str] | None:
    from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import parse_shell_for_permission

    parsed = parse_shell_for_permission(source)
    if (
        parsed.backend != "tree-sitter"
        or parsed.kind != "simple"
        or len(parsed.subcommands) != 1
    ):
        return None
    if parsed.flags.has_risky_structure() or parsed.subcommands[0].text != source.strip():
        return None
    # Inspect unquoted words before shlex removes quoting. An expansion cannot
    # become an apparently literal argv value through quote decoding.
    import tree_sitter_bash
    from tree_sitter import Language, Parser

    data = source.encode("utf-8")
    stack = [Parser(Language(tree_sitter_bash.language())).parse(data).root_node]
    allowed = {
        "program",
        "command",
        "command_name",
        "word",
        "number",
        "string",
        "raw_string",
        "string_content",
        "concatenation",
    }
    while stack:
        node = stack.pop()
        if node.is_named and node.type not in allowed:
            return None
        if node.type == "word" and re.search(rb"[$*?~{}\\`\r\n]", node.text):
            return None
        stack.extend(node.named_children)
    return shlex.split(source, posix=True)


def _path(value: str, cwd: str, *, executable: bool = False) -> str | None:  # pylint: disable=too-many-return-statements
    if not value or value == "-" or re.search(r"[$*?{}\[\]\r\n`\x00]", value):
        return None
    if os.name == "nt" and value.startswith("/"):
        if not re.match(r"^/[a-zA-Z]/", value):
            return None
        value = value[1] + ":/" + value[3:]
    if os.name == "nt" and ":" in value and not re.match(r"^[a-zA-Z]:[/\\]", value):
        return None  # Provider paths, drive-relative operands and NTFS streams.
    if os.name == "nt" and ":" in os.path.splitdrive(value)[1]:
        return None  # A stream is not interchangeable with its base file.
    path = Path(value)
    # Lexically collapsing '..' before following a link can select a different
    # target (notably between MSYS and Win32). Keep such operands literal.
    if ".." in path.parts:
        return None
    if not path.is_absolute():
        path = Path(cwd) / path
    # Executables bind to their resolved identity (venv Python is often a link).
    # File operands retain the stricter no-final-link rule.
    if path.is_symlink() and not executable:
        return None
    if executable:
        return str(path.resolve())
    # A final junction also changes the actual resource identity.
    if path.is_dir() and path.resolve() != path:
        return None
    return str(path.parent.resolve() / path.name)


def _resolve_program(token: str, plan: list, dialect: str, cwd: str) -> tuple[str, bool] | None:
    launcher = shutil.which(plan[0])
    if not launcher:
        return None
    directories = [Path(launcher).resolve().parent]
    sibling = directories[0].parent / "usr/bin"
    if os.name == "nt" and dialect in {"bash", "sh"} and sibling.is_dir():
        directories.insert(0, sibling)
    search = os.pathsep.join([*(str(p) for p in directories), os.environ.get("PATH", "")])
    if "/" in token or "\\" in token:
        candidate = _path(token, cwd, executable=True)
        resolved = candidate if candidate and Path(candidate).is_file() else None
    else:
        resolved = shutil.which(token, path=search)
    if not resolved:
        return None
    executable = Path(resolved).resolve()
    trusted_dirs = (
        {*directories, Path("/usr/bin").resolve(), Path("/bin").resolve()} if os.name != "nt" else set(directories)
    )
    return str(executable), executable.parent in trusted_dirs


def _typed_arguments(words: list[str], schema: CommandSchema, cwd: str) -> tuple[list, list] | None:
    # Keep grammar rejection explicit: an unsupported token must not fall
    # through into path inference or lose its original argument position.
    # pylint: disable=too-many-locals,too-many-return-statements
    options = {
        name.casefold() if schema.dialect == "powershell" else name: spec
        for spec in schema.options
        for name in spec.names
    }
    typed = [["literal", word] for word in words]
    positional, accesses, used = [], [], []
    cursor, ended = 0, False
    while cursor < len(words):
        word = words[cursor]
        if not ended and word == "--" and schema.dialect == "bash":
            ended = True
        elif not ended and word.startswith("-") and word != "-":
            name, equals, value = word.partition("=")
            key = name.casefold() if schema.dialect == "powershell" else name
            spec = options.get(key)
            if spec is None or (equals and not spec.takes_value):
                return None  # Unknown option/cluster: retain literal argv only.
            repeated_path = key in {"-path", "-literalpath"} and any(k in {"-path", "-literalpath"} for k in used)
            if schema.dialect == "powershell" and (key in used or repeated_path):
                return None
            used.append(key)
            if spec.takes_value:
                if not equals:
                    cursor += 1
                    if cursor >= len(words):
                        return None
                    value = words[cursor]
                if spec.action:
                    path = _path(value, cwd)
                    if path is None:
                        return None
                    kind = "path_option" if equals else "path"
                    typed[cursor] = [
                        kind + ("_dir" if Path(path).is_dir() else ""),
                        (name + "=" if equals else "") + path,
                    ]
                    accesses.append((path, spec.action))
        else:
            positional.append(cursor)
        cursor += 1
    if schema.layout == "named":
        return (typed, accesses) if not positional and accesses else None
    if schema.layout in {"copy", "move"}:
        if len(positional) < 2:
            return None
        roles = ["read" if schema.layout == "copy" else "delete"] * (len(positional) - 1) + ["write"]
    else:
        if schema.layout == "grep" and not any(key in {"-e", "--regexp", "-f", "--file"} for key in used):
            if not positional:
                return None
            positional = positional[1:]  # Pattern is literal, never a path.
        roles = ["read" if schema.layout in {"read", "grep"} else schema.layout] * len(positional)
    for index, role in zip(positional, roles):
        if words[index] == "-" and role == "read":
            continue
        path = _path(words[index], cwd)
        if path is None:
            return None
        typed[index] = ["path_dir" if schema.name == "mkdir" or Path(path).is_dir() else "path", path]
        accesses.append((path, role))
    # Directory copies, recursive operations and conditional writes can have
    # effects beyond these operands. Keep the argv identity, infer no receipts.
    if schema.layout in {"copy", "move", "delete"}:
        if (
            any(key not in schema.effect_options for key in used)
            or any(Path(path).is_dir() for path, _ in accesses)
            or len(positional) != (2 if schema.layout in {"copy", "move"} else 1)
        ):
            accesses = []
    return typed, accesses


def _identity_from_words(words: list[str], plan: list, dialect: str, cwd: str) -> dict | None:
    if not words or words[0].casefold() in _STATE_COMMANDS | _PURE_BUILTINS:
        return None
    name = Path(words[0]).stem.casefold()
    schema = _SCHEMAS.get(("powershell" if dialect == "powershell" else "bash", name))
    if (
        dialect == "powershell"
        and schema
        and words[0].casefold()
        not in {schema.name.casefold(), "microsoft.powershell.management\\" + schema.name.casefold()}
    ):
        schema = None
    if dialect == "powershell" and schema:
        executable = shutil.which(plan[0])
        trusted = True  # SDK starts a fresh -NoProfile interpreter.
    else:
        resolved = _resolve_program(words[0], plan, dialect, cwd)
        if resolved is None:
            return None
        executable, trusted = resolved
    if not executable:
        return None
    parsed = _typed_arguments(words[1:], schema, cwd) if schema and trusted else None
    argv, accesses = parsed if parsed is not None else ([["literal", word] for word in words[1:]], [])
    return {
        "version": 2,
        "exe": str(Path(executable).resolve()),
        "dialect": dialect,
        "name": name if dialect == "powershell" and schema else "",
        "schema": f"{schema.name}:{schema.revision}" if parsed is not None else "literal",
        "argv": argv,
        "accesses": accesses,
    }


def _identity_execution_plan(command: str, shell: str) -> tuple[list[str] | str, bool, str]:
    """Represent POSIX subprocess-shell's fixed /bin/sh executor explicitly."""
    from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation
    from openjiuwen.core.sys_operation.shell import ShellType

    plan, use_shell, dialect = ShellOperation._resolve_execution_plan(command, ShellType(shell))  # pylint: disable=protected-access
    if use_shell and dialect == "sh" and isinstance(plan, str):
        return ["/bin/sh", "-c", command], False, dialect
    return plan, use_shell, dialect


def native_command_identity(exe: str, argv: list[str], cwd: str, shell: str) -> dict | None:
    """Normalize actual argv without applying shell quoting or path rewriting."""
    try:
        plan, use_shell, dialect = _identity_execution_plan("", shell)
        if use_shell or not isinstance(plan, list) or dialect not in {"bash", "sh", "powershell"}:
            return None
        return _identity_from_words([exe, *argv], plan, dialect, cwd)
    except (BaseError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None


def literal_command_identity(command: str, cwd: str, shell: str) -> dict | None:  # pylint: disable=too-many-return-statements
    """Bind literal argv; normalize only schema-identified filesystem operands."""
    if not isinstance(command, str):
        return None
    try:
        plan, use_shell, dialect = _identity_execution_plan(command, shell)
        if use_shell or not isinstance(plan, list) or dialect not in {"bash", "sh", "powershell"}:
            return None
        source = plan[-1]
        words = _powershell_words(source) if dialect == "powershell" else _bash_words(source)
        return _identity_from_words(words, plan, dialect, cwd) if words else None
    except (BaseError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None


def static_shell_context(command: str, cwd: str, shell: str) -> bool:  # pylint: disable=too-many-return-statements
    """External programs cannot change their parent CWD; stateful builtins can."""
    from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import (
        parse_shell_for_permission,
        split_shell_for_grants,
    )

    if shell == "powershell":
        return literal_command_identity(command, cwd, shell) is not None
    children = split_shell_for_grants(command)
    if shell not in {"bash", "sh", "auto"} or not children:
        return False
    for child in children:
        parsed = parse_shell_for_permission(child)
        if parsed.kind != "simple" or len(parsed.subcommands) != 1:
            return False
        # Only state analysis may inspect the redirect-free command. The grant
        # itself always retains the full redirection and its destination.
        body = parsed.subcommands[0].text
        try:
            words = shlex.split(body)
        except ValueError:
            return False
        if not words or words[0] in _STATE_COMMANDS:
            return False
        if words[0] not in _PURE_BUILTINS and literal_command_identity(body, cwd, shell) is None:
            return False
    return True
