# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Invocation-local path evidence for a verified local POSIX shell."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import (
    _get_tree_sitter_bash_parser,
    _node_text,
)

FileAction = Literal["read", "write", "exec"]
AccessKind = Literal["directory_change", "file_io"]
Coverage = Literal["complete", "partial", "unsupported"]
_MAX_SOURCE_BYTES = 16_384
_MAX_STATES = 64
_MAX_ACCESSES = 256
_DYNAMIC_WORD = re.compile(r"[*?\[\]{}~$`\\\r\n]")
_PRINTF_FORMAT = re.compile(r"(?:[^%\\]|%[s%]|\\[nt])*")


@dataclass(frozen=True)
class ShellPathAccess:
    """A possible access to an explicit path in the executor's filesystem."""

    path: Path
    action: FileAction
    kind: AccessKind
    requires_prior_directory_change_failure: bool


@dataclass(frozen=True)
class ShellPathAnalysis:
    """Path evidence bound to one normalized invocation and execution cwd."""

    command: str
    workdir: str
    shell_type: str
    cwd: Path
    coverage: Coverage
    accesses: tuple[ShellPathAccess, ...] = ()

    def matches(self, args: Mapping[str, Any]) -> bool:
        """Check the arguments controlled by the modeled shell context."""
        return (
            args.get("command") == self.command
            and args.get("workdir") == self.workdir
            and args.get("shell_type", "auto") == self.shell_type
            and not args.get("run_in_background")
        )

    def blocks_remember(self, ask_accesses: set[tuple[str, str]]) -> bool:
        """A branch-only or traversal ASK cannot become a reusable path prefix."""
        return self.coverage != "complete" or any(
            (entry.kind == "directory_change" or entry.requires_prior_directory_change_failure)
            and (entry.path.as_posix(), entry.action) in ask_accesses
            for entry in self.accesses
        )


class _UnsupportedSyntax(ValueError):
    pass


@dataclass(frozen=True)
class _Command:
    directory: str | None = None
    accesses: tuple[tuple[str, FileAction], ...] = ()
    mkdir_operands: tuple[str, ...] = ()
    opaque: bool = False


@dataclass(frozen=True)
class _State:
    cwd: Path | None
    previous: Path | None = None
    succeeded: bool = True
    directory_failed: bool = False


def _literal(node: Any, source: bytes, *, status_data: bool = False) -> str:
    raw = _node_text(node, source)
    if node.type == "word" and not _DYNAMIC_WORD.search(raw):
        return raw
    if node.type == "raw_string":
        return raw[1:-1]
    if node.type == "string" and "\\" not in raw and all(
        child.type == "string_content"
        or (status_data and child.type == "simple_expansion" and _node_text(child, source) == "$?")
        for child in node.named_children
    ):
        return raw[1:-1]
    raise _UnsupportedSyntax


def _redirect(node: Any, source: bytes) -> tuple[str, FileAction] | None:
    if node.type == "file_redirect" and _node_text(node, source) == "2>&1":
        return None
    operator, target = node.children if len(node.children) == 2 else (None, None)
    if node.type != "file_redirect" or operator is None or operator.type not in {"<", ">", ">>"}:
        raise _UnsupportedSyntax
    path = _literal(target, source)
    if not path:
        raise _UnsupportedSyntax
    return path, "read" if operator.type == "<" else "write"


def _simple_command(
    node: Any, source: bytes, redirects: tuple[tuple[str, FileAction], ...],
) -> _Command:
    name_node = node.child_by_field_name("name")
    if name_node is None or len(name_node.named_children) != 1:
        raise _UnsupportedSyntax
    name = _literal(name_node.named_children[0], source)
    arguments = node.children_by_field_name("argument")
    if len(node.children) != len(arguments) + 1:
        raise _UnsupportedSyntax
    words = [_literal(word, source, status_data=name == "echo") for word in arguments]
    if name == "cd":
        if words[:1] == ["--"]:
            words = words[1:]
        if redirects or len(words) != 1:
            raise _UnsupportedSyntax
        target = words[0]
        if not target or (target.startswith("-") and target != "-"):
            raise _UnsupportedSyntax
        return _Command(directory=target)
    if name == "mkdir" and words[:1] == ["-p"]:
        operands = words[2:] if words[1:2] == ["--"] else words[1:]
        if operands and all(word and not word.startswith("-") for word in operands):
            return _Command(accesses=redirects, mkdir_operands=tuple(operands))
    if name == "echo" or (name == "pwd" and not words):
        return _Command(accesses=redirects)
    if name == "printf" and words:
        fmt = words[0]
        if not fmt.startswith("-") and _PRINTF_FORMAT.fullmatch(fmt):
            return _Command(accesses=redirects)
    if name not in {"cat", "ls", "md5sum"}:
        raise _UnsupportedSyntax
    if name == "ls" and words and words[0] in {"-l", "-la"}:
        words = words[1:]
    if words[:1] == ["--"]:
        words = words[1:]
        invalid = any(not word or word == "-" for word in words)
    else:
        invalid = any(not word or word.startswith("-") for word in words)
    if invalid:
        raise _UnsupportedSyntax
    if not words and (name != "cat" or not any(action == "read" for _, action in redirects)):
        raise _UnsupportedSyntax
    return _Command(accesses=(*redirects, *((word, "read") for word in words)))


def _commands(
    node: Any, source: bytes, redirects: tuple[Any, ...] = (),
) -> Iterator[_Command]:
    if node.type == "command":
        try:
            accesses = tuple(item for item in (_redirect(redir, source) for redir in redirects) if item is not None)
            step = _simple_command(node, source, accesses)
        except _UnsupportedSyntax:
            step = _Command(opaque=True)
        yield step
        return
    if node.type == "list" and len(node.children) == 3 and node.children[1].type == "&&":
        left, _, right = node.children
        yield from _commands(left, source)
        # The Bash grammar can attach the redirect to the whole AND list.
        yield from _commands(right, source, redirects)
        return
    if node.type == "redirected_statement":
        body = node.child_by_field_name("body")
        own_redirects = node.children_by_field_name("redirect")
        if body is not None and own_redirects and len(node.children) == len(own_redirects) + 1:
            yield from _commands(body, source, (*own_redirects, *redirects))
            return
    yield _Command(opaque=True)


def _path_at(cwd: Path | None, name: str) -> Path | None:
    path = Path(name)
    if path.is_absolute():
        return path
    return cwd / path if cwd is not None else None


def _directory_prefixes(raw: Path) -> Iterator[tuple[Path, bool]]:
    """Walk directory prefixes, including those traversed before a `..` operand."""
    current = Path(raw.anchor)
    for part in raw.parts[1:]:
        if part == "..":
            current = current.parent
            continue
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                # A physical cwd cannot represent shell logical traversal through a link.
                raise _UnsupportedSyntax
        except FileNotFoundError:
            yield current, True
        except NotADirectoryError:
            yield current, False
        else:
            yield current, False


def _directory_target(state: _State, name: str) -> Path | None:
    raw = state.previous if name == "-" else _path_at(state.cwd, name)
    if raw is None:
        return None
    try:
        for _ in _directory_prefixes(raw):
            pass
    except _UnsupportedSyntax:
        return None
    return Path(os.path.normpath(raw))


def analyze_posix_shell_paths(
    command: str, workdir: str, shell_type: str, cwd: Path,
) -> ShellPathAnalysis:
    """Collect explicit path candidates without executing any shell commands."""
    parser = _get_tree_sitter_bash_parser()
    source = command.encode("utf-8")
    if parser is None or len(source) > _MAX_SOURCE_BYTES:
        return ShellPathAnalysis(command, workdir, shell_type, cwd, "unsupported")
    root = parser.parse(source).root_node
    if root.has_error:
        return ShellPathAnalysis(command, workdir, shell_type, cwd, "unsupported")

    coverage: Coverage = "complete"
    paths: dict[tuple[Path, FileAction, AccessKind], bool] = {}
    states = {_State(cwd)}

    def record(path: Path, action: FileAction, kind: AccessKind, failed: bool) -> None:
        key = (path, action, kind)
        if len(paths) == _MAX_ACCESSES and key not in paths:
            raise _UnsupportedSyntax
        paths[key] = paths.get(key, True) and failed

    try:
        for statement in root.children:
            if statement.type in {";", "\n", "comment"}:
                continue
            states = {replace(state, succeeded=True) for state in states}
            for step in _commands(statement, source):
                ready = {state for state in states if state.succeeded}
                following = states - ready
                if step.opaque and ready:
                    # The command may change the shell itself. Continue only branches that skipped it.
                    coverage = "partial"
                    states = following
                    continue
                for state in sorted(ready, key=lambda s: (str(s.cwd), s.directory_failed, str(s.previous))):
                    directory_failed = state.directory_failed or step.directory is not None
                    following.add(replace(state, succeeded=False, directory_failed=directory_failed))
                    if step.directory is not None:
                        target = _directory_target(state, step.directory)
                        if target is None:
                            coverage = "partial"
                        else:
                            record(target, "read", "directory_change", state.directory_failed)
                        following.add(replace(state, cwd=target, previous=state.cwd))
                        continue
                    for name, action in step.accesses:
                        path = _path_at(state.cwd, name)
                        if path is None:
                            coverage = "partial"
                        else:
                            record(path.resolve(), action, "file_io", state.directory_failed)
                    for name in step.mkdir_operands:
                        path = _path_at(state.cwd, name)
                        if path is None:
                            coverage = "partial"
                            continue
                        for directory, missing in _directory_prefixes(path):
                            if missing:
                                record(directory, "write", "file_io", state.directory_failed)
                        record(Path(os.path.normpath(path)), "write", "file_io", state.directory_failed)
                    following.add(state)
                if len(following) > _MAX_STATES:
                    raise _UnsupportedSyntax
                states = following
    except (OSError, RuntimeError, ValueError):
        coverage = "partial"

    accesses = tuple(
        ShellPathAccess(path, action, kind, failed and coverage == "complete")
        for (path, action, kind), failed in paths.items()
    )
    return ShellPathAnalysis(command, workdir, shell_type, cwd, coverage, accesses)
