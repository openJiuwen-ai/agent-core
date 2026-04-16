"""LSP result formatters for the 8 operations."""

from __future__ import annotations

import sys
import urllib.parse
from typing import Any

from openjiuwen.harness.tools.lsp_tool._schemas import LspOperation


SYMBOL_KIND_MAP: dict[int, str] = {
    1: "File",
    2: "Module",
    3: "Namespace",
    4: "Package",
    5: "Class",
    6: "Method",
    7: "Property",
    8: "Field",
    9: "Constructor",
    10: "Enum",
    11: "Interface",
    12: "Function",
    13: "Variable",
    14: "Constant",
    15: "String",
    16: "Number",
    17: "Boolean",
    18: "Array",
    19: "Object",
    20: "Key",
    21: "Null",
    22: "EnumMember",
    23: "Event",
    24: "Operator",
    25: "TypeParameter",
}


def format_location(loc: dict[str, Any]) -> str:
    """Format a Location dict as 'path:line:char'."""
    uri = loc.get("uri", "")
    path = format_uri(uri)
    start = loc.get("range", {}).get("start", {})
    line = start.get("line", 0) + 1
    char = start.get("character", 0) + 1
    return f"{path}:{line}:{char}"


def _is_windows_drive_path(path: str) -> bool:
    """Check if path looks like a Windows drive letter path (e.g. /C:/...)."""
    return len(path) >= 3 and path[0] == "/" and path[2] == ":"


def format_uri(uri: str) -> str:
    """Convert file:///path to filesystem path with URL decoding."""
    if uri.startswith("file://"):
        path = uri[7:]
        if sys.platform == "win32" and _is_windows_drive_path(path):
            path = path[1:]
        return urllib.parse.unquote(path)
    return uri


def group_by_file(locations: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group locations by their file path."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for loc in locations:
        groups.setdefault(format_uri(loc.get("uri", "")), []).append(loc)
    return groups


def format_go_to_definition(result: dict[str, Any] | None) -> str:
    """Format a single Location as 'Defined in path:line:char' or 'No definition found.'."""
    if not result:
        return "No definition found."
    return f"Defined in {format_location(result)}"


def format_find_references(result: list[dict[str, Any]]) -> str:
    """Format a list of Locations grouped by file, each entry as 'path:\n  line:char'."""
    if not result:
        return "No references found."
    groups = group_by_file(result)
    lines = []
    for path, locs in groups.items():
        lines.append(f"{path}:")
        for loc in locs:
            line = loc["range"]["start"]["line"] + 1
            char = loc["range"]["start"]["character"] + 1
            lines.append(f"  {line}:{char}")
    return "\n".join(lines)


def format_document_symbol(symbols: list[dict[str, Any]] | dict[str, Any]) -> str:
    """
    Format document symbols, supporting both the hierarchical DocumentSymbol
    tree form and the flat SymbolInformation form.
    """
    if not symbols:
        return "No symbols found."
    first = symbols[0] if isinstance(symbols, list) else symbols
    is_tree = "children" in first
    if is_tree:
        return _format_symbol_tree(symbols, 0)
    lines = []
    for sym in symbols:
        name = sym.get("name", "?")
        kind = SYMBOL_KIND_MAP.get(sym.get("kind", 0), "?")
        loc = sym.get("location", {})
        path = format_uri(loc.get("uri", ""))
        line = loc.get("range", {}).get("start", {}).get("line", 0) + 1
        container = sym.get("containerName", "")
        if container:
            lines.append(f"{path}:{line}: {kind} {container}.{name}")
        else:
            lines.append(f"{path}:{line}: {kind} {name}")
    return "\n".join(lines)


def _format_symbol_tree(symbols: list[dict[str, Any]], indent: int) -> str:
    """Recursively format a symbol tree."""
    lines = []
    for sym in symbols:
        name = sym.get("name", "?")
        kind = SYMBOL_KIND_MAP.get(sym.get("kind", 0), "?")
        detail = sym.get("detail", "")
        lines.append(f"{'  ' * indent}{kind} {name}" + (f" - {detail}" if detail else ""))
        children = sym.get("children", [])
        if children:
            lines.append(_format_symbol_tree(children, indent + 1))
    return "\n".join(lines)


def format_workspace_symbol(result: list[dict[str, Any]]) -> str:
    """Format workspace symbol results grouped by file, each entry as 'path:\n  line: kind Name'."""
    if not result:
        return "No symbols found."
    groups: dict[str, list[dict[str, Any]]] = {}
    for s in result:
        uri = s.get("location", {}).get("uri") if isinstance(s.get("location"), dict) else s.get("uri", "")
        groups.setdefault(format_uri(uri), []).append(s)
    lines = []
    for path, syms in groups.items():
        lines.append(f"{path}:")
        for sym in syms:
            name = sym.get("name", "?")
            kind = SYMBOL_KIND_MAP.get(sym.get("kind", 0), "?")
            line = sym.get("location", {}).get("range", {}).get("start", {}).get("line", 0) + 1
            container = sym.get("containerName", "")
            if container:
                lines.append(f"  {line}: {kind} {container}.{name}")
            else:
                lines.append(f"  {line}: {kind} {name}")
    return "\n".join(lines)


def format_prepare_call_hierarchy(result: list[dict[str, Any]]) -> str:
    """Format call hierarchy preparation results."""
    if not result:
        return "No call hierarchy available."
    if len(result) == 1:
        item = result[0]
        loc = item.get("originSelectionRange", item.get("range", {}))
        path = format_uri(item.get("uri", ""))
        line = loc["start"]["line"] + 1
        return f"{path}:{line}: {item.get('name', '?')}"
    lines = [f"{len(result)} call hierarchy items:"]
    for item in result:
        loc = item.get("originSelectionRange", item.get("range", {}))
        path = format_uri(item.get("uri", ""))
        line = loc["start"]["line"] + 1
        lines.append(f"  {path}:{line}: {item.get('name', '?')}")
    return "\n".join(lines)


def format_incoming_calls(result: list[dict[str, Any]]) -> str:
    """Format incoming call hierarchy results."""
    if not result:
        return "No incoming calls found."
    groups: dict[str, list[dict[str, Any]]] = {}
    for i in result:
        uri = i.get("from", {}).get("uri", "")
        groups.setdefault(format_uri(uri), []).append(i)
    lines = []
    for path, calls in groups.items():
        lines.append(f"{path}:")
        for call in calls:
            caller = call.get("from", {})
            ranges = call.get("fromRanges", [])
            caller_path = format_uri(caller.get("uri", ""))
            caller_line = caller.get("range", {}).get("start", {}).get("line", 0) + 1
            for r in ranges:
                r_line = r["start"]["line"] + 1
                r_char = r["start"]["character"] + 1
                lines.append(f"  {caller_path}:{caller_line} -> call site {r_line}:{r_char}")
    return "\n".join(lines)


def format_outgoing_calls(result: list[dict[str, Any]]) -> str:
    """Format outgoing call hierarchy results."""
    if not result:
        return "No outgoing calls found."
    groups: dict[str, list[dict[str, Any]]] = {}
    for i in result:
        uri = i.get("from", {}).get("uri", "")
        groups.setdefault(format_uri(uri), []).append(i)
    lines = []
    for path, calls in groups.items():
        lines.append(f"{path}:")
        for call in calls:
            callee = call.get("from", {})
            ranges = call.get("fromRanges", [])
            from_line = callee.get("range", {}).get("start", {}).get("line", 0) + 1
            for r in ranges:
                r_line = r["start"]["line"] + 1
                r_char = r["start"]["character"] + 1
                lines.append(f"  {from_line}: call site {r_line}:{r_char} -> {callee.get('name', '?')}")
    return "\n".join(lines)


def format_result(operation: LspOperation, result: Any) -> str:  # noqa: ANN401
    """Route to the appropriate formatter based on operation type."""
    return {
        LspOperation.GO_TO_DEFINITION: format_go_to_definition,
        LspOperation.FIND_REFERENCES: format_find_references,
        LspOperation.DOCUMENT_SYMBOL: format_document_symbol,
        LspOperation.WORKSPACE_SYMBOL: format_workspace_symbol,
        LspOperation.GO_TO_IMPLEMENTATION: format_go_to_definition,
        LspOperation.PREPARE_CALL_HIERARCHY: format_prepare_call_hierarchy,
        LspOperation.INCOMING_CALLS: format_incoming_calls,
        LspOperation.OUTGOING_CALLS: format_outgoing_calls,
    }[operation](result)
