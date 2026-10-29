# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recognize a deliberately small, literal file-delete operation.

This module does not grant permission. The caller must establish the actual
executor dialect, controlled-default provenance, and the applicable file policy.
An unrecognized operation returns None and must retain the ordinary policy path.
"""

from __future__ import annotations

import os

# Exact native classes and their private provider methods are intentional:
# accepting a subclass or a tool-name claim would not verify the executor.
# pylint: disable=protected-access,unidiomatic-typecheck
import re
import stat
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import (
    parse_shell_for_permission,
)
from openjiuwen.harness.security.permission_engine.toolguard.tool_categories import (
    is_shell_tool,
    shell_tools_from_config,
)

DeleteDialect = Literal["posix", "powershell"]


@dataclass(frozen=True)
class LocalDeleteExecution:
    """Host proof of a known local tool's actual shell and working directory."""

    dialect: DeleteDialect
    cwd: Path


# Even inside quotes, these forms are outside the supported subset. Keeping the
# lexer smaller than either shell makes unknown syntax an ordinary-policy case.
_UNSUPPORTED = re.compile(r"[\x00-\x1f\x7f\u2010-\u201f$`*?\[\]{}();&|<>~%#@!,]")
_TOKEN = re.compile(r"(?:'[^']*'|\"[^\"]*\"|[^\s'\"]+)(?=\s|$)")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[/\\]")
_WINDOWS_DEVICE = re.compile(r"(?i)^(?:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\..*)?$")


def literal_delete_operands(command: str, dialect: DeleteDialect) -> tuple[str, ...] | None:  # pylint: disable=too-many-return-statements
    """Return every operand of ``rm -- ...`` or one PS ``-LiteralPath``.

    No aliases, flags other than these exact delimiters, concatenated strings,
    escape sequences, expansions, providers, arrays, or compound commands qualify.
    The existing AST must also recognize one simple command without redirects.
    """
    if not isinstance(command, str) or dialect not in {"posix", "powershell"}:
        return None
    text = command.strip(" ")
    if not text or len(text) > 32768 or _UNSUPPORTED.search(text):
        return None
    parsed = parse_shell_for_permission(text)
    if parsed.kind != "simple" or parsed.flags.has_risky_structure() or len(parsed.subcommands) != 1:
        return None
    # The lexical pass consumes the entire input, including gaps. Do not trust
    # bash argv for PowerShell: its backslash and quote semantics are different.
    tokens: list[str] = []
    cursor = 0
    for match in _TOKEN.finditer(text):
        if text[cursor : match.start()].strip(" "):
            return None
        token = match.group()
        if token[0] in {"'", '"'}:
            token = token[1:-1]
            if "'" in token or '"' in token:
                return None
        if not token:
            return None
        tokens.append(token)
        cursor = match.end()
    if text[cursor:].strip(" "):
        return None
    if dialect == "posix":
        if len(tokens) < 3 or tokens[:2] != ["rm", "--"] or "\\" in text:
            return None
        operands = tokens[2:]
    else:
        if len(tokens) != 3 or [word.lower() for word in tokens[:2]] != ["remove-item", "-literalpath"]:
            return None
        operands = tokens[2:]
    for operand in operands:
        if operand.startswith("-") or "::" in operand:
            return None
        # Reject PS providers, drive-relative paths and NT alternate streams.
        remainder = operand[2:] if _WINDOWS_DRIVE.match(operand) else operand
        if ":" in remainder:
            return None
    return tuple(operands)


def local_delete_execution(tool: Any, args: dict[str, Any]) -> LocalDeleteExecution | None:
    """Inspect an actual registered native tool, never a model-supplied tool ID.

    Remote/MCP/custom providers and POSIX shells on Windows do not establish
    that permission paths have the same meaning as execution paths.
    """
    from openjiuwen.harness.tools.shell.bash import BashTool
    from openjiuwen.harness.tools.shell.powershell import PowerShellTool

    if type(tool) not in {BashTool, PowerShellTool}:
        return None
    try:
        inputs = tool._parse_inputs(args)
        selected = inputs.shell_type if type(tool) is BashTool else "powershell"
        return local_operation_delete_execution(tool._operation, inputs.command, selected, inputs.workdir)
    except Exception:
        return None


# Each uncertain boundary intentionally exits to ordinary policy.
# pylint: disable-next=too-many-return-statements
def local_operation_delete_execution(
    operation: Any,
    command: str,
    shell_type: str,
    workdir: str | None,
) -> LocalDeleteExecution | None:
    """Verify the native provider actually bound to a host-owned command tool."""
    from openjiuwen.core.sys_operation import OperationMode, SysOperation
    from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation
    from openjiuwen.core.sys_operation.shell import ShellType

    try:
        if type(operation) is not SysOperation or operation.mode != OperationMode.LOCAL:
            return None
        shell = operation.shell()
        if type(shell) is not ShellOperation:
            return None
        candidate_dialect: DeleteDialect = "powershell" if os.name == "nt" else "posix"
        if literal_delete_operands(command, candidate_dialect) is None:
            return None
        _, _, actual = shell._resolve_execution_plan(command, ShellType.from_str(shell_type))
        if os.name == "nt" and actual == "powershell":
            dialect: DeleteDialect = "powershell"
        elif os.name != "nt" and actual in {"bash", "sh"}:
            dialect = "posix"
        else:
            return None
        if literal_delete_operands(command, dialect) is None:
            return None
        return LocalDeleteExecution(dialect, shell._resolve_cwd(workdir))
    except Exception:  # Provider/plan uncertainty must retain ordinary checks.
        return None


def validate_delete_metadata(config: Mapping[str, Any]) -> None:
    """Reject malformed derived provenance at ingest, before any tool check."""
    composition = config.get("composition") or {}
    metadata = composition.get("workspace_delete_defaults")
    if metadata is None:
        return
    if (
        composition.get("schema_version") != 2
        or not isinstance(metadata, dict)
        or set(metadata)
        != {
            "schema_version",
            "rules",
            "tools",
        }
        or type(metadata["schema_version"]) is not int
        or metadata["schema_version"] != 1
    ):
        raise ValueError("invalid workspace delete default metadata")
    order = composition.get("source_order", [])
    layers = composition.get("tool_layers")
    if not isinstance(layers, dict) or set(layers) != set(order):
        raise ValueError("invalid workspace delete tool layers")
    for tools in layers.values():
        if not isinstance(tools, dict) or any(
            not isinstance(name, str) or not name or not isinstance(value, str) or value not in {"allow", "ask", "deny"}
            for name, value in tools.items()
        ):
            raise ValueError("invalid workspace delete tool contribution")
    for section, key, allowed in (("rules", "id", {"user"}), ("tools", "name", {"user", "builtin"})):
        entries = metadata.get(section)
        if not isinstance(entries, list):
            raise ValueError("invalid workspace delete default records")
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"layer", key}:
                raise ValueError("invalid workspace delete default record")
            layer, name = entry["layer"], entry[key]
            if not isinstance(layer, str) or layer not in allowed or layer not in order:
                raise ValueError("invalid workspace delete default identity")
            if not isinstance(name, str) or not name or (layer, name) in seen:
                raise ValueError("invalid workspace delete default identity")
            seen.add((layer, name))
            if section == "tools" and name not in layers[layer]:
                raise ValueError("missing workspace delete default tool")
            if (
                section == "rules"
                and sum(rule.get("layer") == layer and rule.get("id") == name for rule in config.get("rules", [])) != 1
            ):
                raise ValueError("missing workspace delete default rule")
    fallbacks = composition.get("tool_fallbacks", {})
    if not isinstance(fallbacks, dict) or any(
        not isinstance(name, str)
        or not isinstance(value, str)
        or value not in {"allow", "ask", "deny"}
        or any(name in tools for tools in layers.values())
        for name, value in fallbacks.items()
    ):
        raise ValueError("invalid workspace delete preset fallback")


def scoped_delete_policy(config: Mapping[str, Any], tool_name: str) -> dict[str, Any] | None:  # pylint: disable=too-many-locals,too-many-return-statements
    """Remove only backend-proven default ASK contributions for this operation.

    Called after complete literal-target and effective workspace-write checks.
    This returns an input to normal tiered policy evaluation, not a decision.
    """
    if not is_shell_tool(tool_name, shell_tools_from_config(config)):
        return None
    composition = config.get("composition") or {}
    metadata = composition.get("workspace_delete_defaults") or {}
    layers = composition.get("tool_layers")
    if composition.get("schema_version") != 2 or metadata.get("schema_version") != 1 or not isinstance(layers, dict):
        return None
    if (config.get("defaults") or {}).get("*") in {"ask", "deny"}:
        return None  # No provenance for a customized global default.
    order = composition.get("source_order", [])
    defaults = {(entry["layer"], entry["id"]) for entry in metadata.get("rules", [])}
    tool_defaults = {(entry["layer"], entry["name"]) for entry in metadata.get("tools", [])}
    tools, filtered = {}, {}
    for layer in order:
        if layer not in layers or not isinstance(layers[layer], dict):
            return None
        for name, value in layers[layer].items():
            if name not in tools or value == "deny":
                tools[name] = value
            if (layer, name) in tool_defaults and value == "ask":
                continue
            if name not in filtered or value == "deny":
                filtered[name] = value
    fallbacks = composition.get("tool_fallbacks", {})
    original = {**fallbacks, **tools}
    current_tools = config.get("tools", {})
    if (tool_name in original) != (tool_name in current_tools) or original.get(tool_name) != current_tools.get(
        tool_name
    ):
        return None  # Stale metadata must never establish default provenance.
    selected = deepcopy(dict(config))
    selected["rules"] = [
        deepcopy(rule)
        for rule in config.get("rules", [])
        if not (rule.get("action") == "ask" and (rule.get("layer"), rule.get("id")) in defaults)
    ]
    selected["tools"] = deepcopy(current_tools)
    if tool_name in filtered:
        selected["tools"][tool_name] = filtered[tool_name]
    if tool_name not in filtered and (tool_name not in fallbacks or fallbacks[tool_name] in {"allow", "ask"}):
        selected["tools"][tool_name] = "allow"  # Scoped product/default tool gate only.
    return selected


def _plain_ancestry(path: Path) -> bool:
    """Refuse symlinks, NT reparse points and inaccessible ancestors."""
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue  # A nonexistent literal target can only fail at execution.
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return False
    return True


# Each uncertain boundary intentionally exits to ordinary policy.
# pylint: disable-next=too-many-return-statements
def workspace_delete_targets(
    command: str,
    *,
    dialect: DeleteDialect,
    cwd: Path,
    workspace_root: Path | None,
) -> tuple[Path, ...] | None:
    """Resolve all literal file targets, strictly below a real workspace root.

    ``cwd`` is the executor's actual working directory, not the authorization
    root. Directory deletion, links (including links within the root), parent
    traversal and special Windows path forms remain outside this exception.
    Filesystem checks are observations, not an atomic filesystem sandbox.
    """
    operands = literal_delete_operands(command, dialect)
    if operands is None or workspace_root is None:
        return None
    if not cwd.is_absolute() or not workspace_root.is_absolute():
        return None
    try:
        if not cwd.is_dir() or not workspace_root.is_dir():
            return None
        if not _plain_ancestry(cwd) or not _plain_ancestry(workspace_root):
            return None
        root = workspace_root.resolve(strict=True)
        targets: list[Path] = []
        for operand in operands:
            # Native separators only. POSIX backslashes have already been
            # rejected, while PowerShell on Windows accepts either separator.
            if operand.startswith(("//", "\\\\")):
                return None
            if any(part == ".." for part in re.split(r"[/\\]", operand)):
                return None
            if dialect == "powershell" and any(
                part.endswith((" ", ".")) or _WINDOWS_DEVICE.fullmatch(part)
                for part in re.split(r"[/\\]", operand)
                if part not in {"", "."}
            ):
                return None
            raw = Path(operand)
            target = raw if raw.is_absolute() else cwd / raw
            if not _plain_ancestry(target):
                return None
            resolved = target.resolve(strict=False)
            if resolved == root or not resolved.is_relative_to(root):
                return None
            if resolved.is_dir():
                return None
            # An existing non-regular target (device, socket, pipe) is not an
            # ordinary file. Missing final operands are safe to recognize.
            if resolved.exists() and not resolved.is_file():
                return None
            targets.append(resolved)
        return tuple(targets)
    except (OSError, RuntimeError, ValueError):
        return None
