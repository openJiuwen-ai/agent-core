# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stage one: rule-based file-artifact extraction and the task-parse pseudo-agent.

The pseudo-agent is a single tool-carrying LLM call; the tools only exist to
get a structured response back.  The file-artifact rules follow
``CYR/CONTEXT_01a_tool_re_strings.md``: send_file_to_user (R0) beats files
that were created (R1), which beat files that were only edited (R2).
"""

from __future__ import annotations

import json
import ntpath
import posixpath
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Protocol

from openjiuwen.agent_evolving.attribution.model import TaskParseResult
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode, user_request_nodes, user_request_text

_ASSISTANT_NO_TOOL_CALL_LIMIT = 10000
_EDGE_NODE_LIMIT = 2000
_USER_MESSAGE_LIMIT = 1000
_USER_REQUEST_LIMIT = 8000
_EDGE_NODE_COUNT = 5
_FILE_CANDIDATE_LIMIT = 20
_LEAD_KINDS = frozenset({"team_leader", "main_agent", "main"})
_SHORT_REPLY_CHARS = 50

_PATH_TOKEN = r"(?:'[^']+'|\"[^\"]+\"|[^\s'\"|;&<>]+)"
_PS_PATH = r"(?:'[^']+'|\"[^\"]+\"|[A-Za-z]:\\[^\s;|]+|[^\s;|]+)"
_DEVICE_PATHS = re.compile(r"(?i)^(?:/dev/(?:null|fd|zero|random|stdout|stderr)|nul(?:\.|$))")
_UNRESOLVED_PATH = re.compile(r"[~$%]")

_WRITE_TOOLS = {"write_file"}
_EDIT_TOOLS = {"edit_file", "apply_patch"}
_SHELL_TOOLS = frozenset({"bash", "mcp_exec_command", "cmd", "powershell", "create_terminal"})
_R0_TOOL = "send_file_to_user"
_TEAM_MEMBER_KIND = "team_member"

# POSIX (sh/bash/zsh) ---------------------------------------------------------

_RE_TEE = re.compile(
    rf"(?<![\w.-])tee\b(?P<append>\s+-a\b)?(?:\s+(?!-a\b)-\S+)*\s+"
    rf"(?P<paths>{_PATH_TOKEN}(?:\s+{_PATH_TOKEN})*)"
)
_RE_TOUCH = re.compile(r"(?<![\w.-])touch\b(?:\s+-\S+)*\s+(?P<paths>.+)")
_RE_MKDIR = re.compile(r"(?<![\w.-])(?:mkdir|md)\b(?:\s+-\S+)*\s+(?P<paths>.+)")
_RE_CP = re.compile(r"(?<![\w.-])(?:cp|install)\b")
_RE_MV = re.compile(r"(?<![\w.-])mv\b")
_RE_LN = re.compile(r"(?<![\w.-])ln\b(?=[^;&|]*\s-s\b)")
_RE_TRUNCATE = re.compile(rf"(?<![\w.-])truncate\b[^;&|]*\s-s\s+\S+\s+(?P<path>{_PATH_TOKEN})")
_RE_DD = re.compile(r"(?<![\w.-])dd\b[^;&|]*\bof=(?P<path>[^\s;&|]+)")
_RE_DOWNLOAD = re.compile(rf"(?<![\w.-])(?:wget|curl)\b[^;&|]*?(?:\s-O|\s--output|\s-o)\s*(?P<path>{_PATH_TOKEN})")
_RE_SED_I = re.compile(r"(?<![\w.-])sed\b(?=[^;&|]*?(?:\s-[A-Za-z]*i[A-Za-z.]*\b|\s--in-place\b))")
_RE_PERL_I = re.compile(r"(?<![\w.-])perl\b(?=[^;&|]*\s-[A-Za-z]*i)")
_RE_GAWK_I = re.compile(r"(?<![\w.-])gawk\b(?=[^;&|]*\s-i\s+inplace\b)")
_RE_SORT_O = re.compile(rf"(?<![\w.-])sort\b[^;&|]*\s-o\s*(?P<path>{_PATH_TOKEN})")
_RE_DOS2UNIX = re.compile(r"(?<![\w.-])(?:dos2unix|unix2dos)\b")
_RE_PATCH = re.compile(r"(?<![\w.-])patch\b|(?<![\w.-])git\s+apply\b")
_RE_TAR_X = re.compile(r"(?<![\w.-])tar\b[^;&|]*\s-[A-Za-z]*x")
_RE_UNZIP = re.compile(r"(?<![\w.-])unzip\b")
_RE_PY_OPEN = re.compile(r"open\s*\(\s*(?P<q>['\"])(?P<path>.+?)(?P=q)\s*,\s*(?P<q2>['\"])[wax][+bt]*(?P=q2)")

# Windows cmd ----------------------------------------------------------------

_RE_COPY = re.compile(r"(?i)(?<!\w)(?:copy|xcopy|robocopy)\b")
_RE_MOVE = re.compile(r"(?i)(?<!\w)move\b")
_RE_REN = re.compile(r"(?i)(?<!\w)(?:ren|rename)\b")
_RE_MD = re.compile(r"(?i)(?<!\w)(?:md|mkdir)\b")
_RE_FSUTIL = re.compile(r"(?i)fsutil\s+file\s+createnew\s+(?P<path>[^\s;&|]+)")
_RE_CERTUTIL = re.compile(r"(?i)certutil\b[^;&|]*-decode\s+\S+\s+(?P<path>[^\s;&|]+)")

# PowerShell -----------------------------------------------------------------

_RE_PS_WRITE = re.compile(
    rf"(?i)(?<![\w-])(?:Set-Content|Out-File|Tee-Object)\b[^;|]*?(?:-FilePath|-Path)?\s*(?P<path>{_PS_PATH})"
)
_RE_PS_APPEND = re.compile(rf"(?i)(?<![\w-])Add-Content\b[^;|]*?(?:-Path)?\s*(?P<path>{_PS_PATH})")
_RE_PS_NEWITEM = re.compile(rf"(?i)(?<![\w-])New-Item\b[^;|]*?(?:-Path)?\s*(?P<path>{_PS_PATH})")
_RE_PS_COPY = re.compile(r"(?i)(?<![\w-])(?:Copy-Item|Move-Item|Rename-Item)\b")
_RE_PS_EXPORT = re.compile(rf"(?i)(?<![\w-])(?:Export-Csv|Export-Clixml)\b[^;|]*?(?:-Path)?\s*(?P<path>{_PS_PATH})")
_RE_DOTNET_IO = re.compile(
    r"(?i)\[(?:System\.)?IO\.File\]::(?:WriteAllText|WriteAllLines|WriteAllBytes|AppendAllText|AppendAllLines)"
    r"\s*\(\s*(?P<q>['\"])(?P<path>.+?)(?P=q)"
)


class TaskParseError(Exception):
    """The pseudo-agent refused the run (incomplete or unrelated tasks)."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


class TaskParseLLM(Protocol):
    """Minimal async model surface for the task-parse pseudo-agent call."""

    async def invoke(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Return a response with ``tool_calls`` and ``content`` surfaces."""


def _json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _arguments(tool_call: Mapping[str, Any]) -> Any:
    function = tool_call.get("function")
    if isinstance(function, Mapping):
        arguments = function.get("arguments")
    else:
        arguments = tool_call.get("arguments")
    return _json_loads(arguments) if arguments is not None else {}


def _split_segments(command: str) -> list[str]:
    """Split a shell command on ``;``, ``&&``, ``||`` and newlines outside quotes."""
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            index += 1
            continue
        if char in (";", "\n", "\r"):
            segments.append("".join(current))
            current = []
            index += 1
            continue
        if char in ("&", "|") and index + 1 < length and command[index + 1] == char:
            segments.append("".join(current))
            current = []
            index += 2
            continue
        current.append(char)
        index += 1
    if current:
        segments.append("".join(current))
    return segments


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _positional_tokens(segment: str, is_windows: bool = False) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in segment:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            continue
        if char.isspace():
            if current:
                tokens.append(_strip_quotes("".join(current)))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append(_strip_quotes("".join(current)))
    positional: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            continue
        if is_windows and token.startswith("/"):
            continue
        if "=" in token and not token.startswith(("/", "\\")):
            continue
        positional.append(token)
    return positional


def _clean_path(path: str) -> str | None:
    value = _strip_quotes(path).strip()
    if not value:
        return None
    if _DEVICE_PATHS.search(value):
        return None
    return value


def _normalize_path(path: str, workspace: str | None) -> str | None:
    cleaned = _clean_path(path)
    if cleaned is None:
        return None
    if _UNRESOLVED_PATH.search(cleaned):
        return None
    looks_windows = bool(re.match(r"^[A-Za-z]:[\\/]", cleaned)) or "\\" in cleaned
    norm = ntpath.normpath if looks_windows else posixpath.normpath
    normalized = norm(cleaned)
    if looks_windows:
        normalized = normalized.casefold()
    if not posixpath.isabs(normalized) and not ntpath.isabs(normalized) and workspace:
        normalized = norm(str(Path(workspace) / normalized))
    return normalized


def _extract_r0(arguments: Any) -> list[str]:
    value = arguments.get("abs_file_path_list") if isinstance(arguments, Mapping) else None
    if value is None:
        return []
    parsed = _json_loads(value)
    if isinstance(parsed, str):
        parsed = _json_loads(parsed)
    if isinstance(parsed, list):
        return [item for item in (str(entry) for entry in parsed) if item]
    if isinstance(parsed, str) and parsed:
        return [parsed]
    return []


def _scan_redirects(segment: str) -> list[tuple[str, str]]:
    """Scan ``>`` / ``>>`` redirects outside of quotes (heredocs excluded)."""
    result: list[tuple[str, str]] = []
    index = 0
    length = len(segment)
    while index < length:
        char = segment[index]
        if char in ("'", '"'):
            quote = char
            index += 1
            while index < length and segment[index] != quote:
                index += 1
            index += 1
            continue
        if char != ">":
            index += 1
            continue
        if segment[index - 1 : index] in ("=", "<", ">", "&", "-"):
            index += 1
            continue
        run_end = index
        while run_end < length and segment[run_end] == ">":
            run_end += 1
        if run_end < length and segment[run_end] == "<":
            index = run_end
            continue
        cursor = run_end
        while cursor < length and segment[cursor].isspace():
            cursor += 1
        if cursor >= length:
            break
        if segment[cursor] in ("'", '"'):
            quote = segment[cursor]
            cursor += 1
            start = cursor
            while cursor < length and segment[cursor] != quote:
                cursor += 1
            result.append((segment[start:cursor], "create"))
            index = cursor + 1
            continue
        start = cursor
        while cursor < length and not segment[cursor].isspace() and segment[cursor] not in ";&|<>":
            cursor += 1
        result.append((segment[start:cursor], "create"))
        index = cursor
    return result


def _extract_shell_paths(tool_name: str, command: str) -> tuple[list[tuple[str, str]], int]:
    """Return ``[(path, operation), ...]`` plus a low-confidence count."""
    created: list[tuple[str, str]] = []
    edited: list[tuple[str, str]] = []
    low_confidence = 0
    is_windows = tool_name in {"cmd", "powershell", "create_terminal"}

    for segment in _split_segments(command):
        if not segment.strip():
            continue
        if is_windows:
            created.extend(_cmd_paths(segment))
            edited.extend(_ps_append_paths(segment))
            created.extend(_ps_create_paths(segment))
            continue
        created.extend(_scan_redirects(segment))
        for match in _RE_TEE.finditer(segment):
            append = match.group("append") is not None
            for path in (match.group("paths") or "").split():
                (edited if append else created).append((path, "edit" if append else "create"))
        for match in _RE_TOUCH.finditer(segment):
            created.extend((path, "create") for path in _split_paths(match.group("paths")))
        for match in _RE_MKDIR.finditer(segment):
            created.extend((path, "create") for path in _split_paths(match.group("paths")))
        for pattern, operation in (
            (_RE_CP, "create"),
            (_RE_MV, "create"),
            (_RE_LN, "create"),
        ):
            if pattern.search(segment):
                positional = _positional_tokens(segment)
                if len(positional) >= 2:
                    created.append((positional[-1], operation))
        for pattern, operation in ((_RE_SED_I, "edit"), (_RE_PERL_I, "edit"), (_RE_GAWK_I, "edit")):
            if pattern.search(segment):
                positional = _positional_tokens(segment)
                if positional:
                    edited.append((positional[-1], operation))
        for pattern, operation in ((_RE_SORT_O, "create"), (_RE_TRUNCATE, "create"), (_RE_DD, "create")):
            match = pattern.search(segment)
            if match:
                created.append((match.group("path"), operation))
        for pattern, operation in ((_RE_DOWNLOAD, "create"),):
            match = pattern.search(segment)
            if match:
                created.append((match.group("path"), operation))
        for pattern, operation in ((_RE_DOS2UNIX, "edit"),):
            if pattern.search(segment):
                positional = _positional_tokens(segment)
                if len(positional) >= 2:
                    edited.append((positional[1], operation))
        if _RE_PATCH.search(segment) or _RE_TAR_X.search(segment) or _RE_UNZIP.search(segment):
            low_confidence += 1
        if _RE_PY_OPEN.search(segment):
            low_confidence += 1
    return created + edited, low_confidence


def _split_paths(value: str) -> list[str]:
    return [path for path in (part.strip() for part in value.split()) if path]


def _cmd_paths(segment: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if _RE_FSUTIL.search(segment):
        match = _RE_FSUTIL.search(segment)
        if match:
            result.append((match.group("path"), "create"))
    if _RE_CERTUTIL.search(segment):
        match = _RE_CERTUTIL.search(segment)
        if match:
            result.append((match.group("path"), "create"))
    for pattern in (_RE_COPY, _RE_MOVE, _RE_REN, _RE_MD):
        if pattern.search(segment):
            positional = _positional_tokens(segment, is_windows=True)
            if len(positional) >= 3:
                result.append((positional[2], "create"))
            elif _RE_MD.search(segment) and len(positional) >= 2:
                result.append((positional[1], "create"))
    return result


def _ps_append_paths(segment: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for match in _RE_PS_APPEND.finditer(segment):
        result.append((match.group("path"), "edit"))
    return result


_PS_FLAG_PATH = re.compile(rf"(?i)(?:-FilePath|-Path)\s+(?P<path>{_PS_PATH})")


def _ps_create_paths(segment: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for pattern in (_RE_PS_WRITE, _RE_PS_NEWITEM, _RE_PS_EXPORT):
        match = pattern.search(segment)
        if not match:
            continue
        flag = _PS_FLAG_PATH.search(segment)
        if flag:
            result.append((flag.group("path"), "create"))
            continue
        positional = _positional_tokens(segment, is_windows=True)
        if len(positional) >= 2:
            result.append((positional[1], "create"))
    for match in _RE_DOTNET_IO.finditer(segment):
        operation = "create" if "write" in segment[match.start() : match.end()].lower() else "edit"
        result.append((match.group("path"), operation))
    if _RE_PS_COPY.search(segment):
        destination = re.search(r"(?i)-Destination\s+(\S+)", segment)
        if destination:
            result.append((destination.group(1), "create"))
        else:
            positional = _positional_tokens(segment, is_windows=True)
            if len(positional) >= 2:
                result.append((positional[-1], "create"))
    return result


def extract_file_candidates(
    nodes_by_subagent: Mapping[str, list[TrajectoryNode]],
    workspace: str | None = None,
) -> tuple[list[tuple[str, str, str]], dict[str, Any]]:
    """Scan assistant tool calls for file artifacts and return candidates.

    Returns ``(file_candidates, run_meta)``; candidates are
    ``[(normalized_path, node_id, tool_type), ...]`` ordered R0 > R1 > R2.
    """
    r0_entries: dict[str, tuple[str, str]] = {}
    created: set[str] = set()
    edited: set[str] = set()
    last_touch: dict[str, tuple[str, str]] = {}
    first_seen: dict[str, int] = {}
    order = 0
    stats = {"low_confidence": 0, "unresolved": 0}

    def touch(path: str, node_id: str, tool_type: str, operation: str | None) -> None:
        nonlocal order
        last_touch[path] = (node_id, tool_type)
        first_seen.setdefault(path, order)
        order += 1
        if operation == "create":
            created.add(path)
        elif operation == "edit":
            edited.add(path)
        else:
            r0_entries[path] = (node_id, tool_type)

    for subagent in sorted(nodes_by_subagent):
        for node in nodes_by_subagent[subagent]:
            if node.role != "assistant" or not node.tool_calls:
                continue
            for tool_call in node.tool_calls:
                name = str(tool_call.get("name") or tool_call.get("function", {}).get("name") or "")
                arguments = _arguments(tool_call)
                if not isinstance(arguments, Mapping):
                    arguments = {}
                if name == _R0_TOOL:
                    for raw_path in _extract_r0(arguments):
                        path = _normalize_path(raw_path, workspace)
                        if path is None:
                            stats["unresolved"] += 1
                            continue
                        touch(path, node.node_id, name, None)
                    continue
                if name in _WRITE_TOOLS:
                    for key in ("file_path", "path"):
                        if arguments.get(key):
                            path = _normalize_path(str(arguments[key]), workspace)
                            if path is None:
                                stats["unresolved"] += 1
                                continue
                            touch(path, node.node_id, name, "create")
                            break
                    continue
                if name in _EDIT_TOOLS:
                    if name == "apply_patch":
                        stats["low_confidence"] += 1
                        continue
                    for key in ("file_path", "path"):
                        if arguments.get(key):
                            path = _normalize_path(str(arguments[key]), workspace)
                            if path is None:
                                stats["unresolved"] += 1
                                continue
                            touch(path, node.node_id, name, "edit")
                            break
                    continue
                if name in _SHELL_TOOLS:
                    command = arguments.get("command")
                    if not isinstance(command, str):
                        continue
                    hits, low = _extract_shell_paths(name, command)
                    stats["low_confidence"] += low
                    for raw_path, operation in hits:
                        path = _normalize_path(raw_path, workspace)
                        if path is None:
                            stats["unresolved"] += 1
                            continue
                        touch(path, node.node_id, f"{name}:{operation}", operation)

    def grade(path: str) -> int:
        if path in r0_entries:
            return 0
        if path in created:
            return 1
        return 2

    all_paths = set(r0_entries) | created | edited
    candidates = sorted(
        ((path, *last_touch[path]) for path in all_paths),
        key=lambda item: (grade(item[0]), first_seen.get(item[0], order)),
    )
    return candidates, stats


def _node_has_tool_calls(node: TrajectoryNode) -> bool:
    return bool(node.tool_calls)


def user_request_of(nodes_by_subagent: Mapping[str, list[TrajectoryNode]]) -> list[tuple[str, str]]:
    """``(node_id, text)`` of the user's own messages: those the slicer kept from the whole session
    (:class:`~openjiuwen.agent_evolving.attribution.snapshot.SlicedNodes`), else those in the given nodes.
    """
    nodes = getattr(nodes_by_subagent, "user_requests", None)
    if nodes is None:
        nodes = user_request_nodes(nodes_by_subagent)
    return [(node.node_id, text) for node in nodes if (text := user_request_text(node))]


def build_task_parse_digest(
    nodes_by_subagent: Mapping[str, list[TrajectoryNode]],
    file_candidates: Sequence[tuple[str, str, str]],
    language: str = "zh",
    user_requests: Sequence[tuple[str, str]] = (),
) -> str:
    """Assemble the disclosure digest (the user's own messages, assistant texts, head/tail, users)."""
    assistant_block: list[str] = []
    edge_block: list[str] = []
    user_block: list[str] = []
    disclosed: set[int] = set()
    node_id_by_index: dict[int, str] = {}
    index_by_node_id: dict[str, int] = {}

    flat: list[TrajectoryNode] = []
    for subagent in sorted(nodes_by_subagent):
        for node in nodes_by_subagent[subagent]:
            node_id_by_index[id(node)] = node.node_id
            index_by_node_id[node.node_id] = len(flat)
            flat.append(node)

    for node in flat:
        if node.role == "assistant" and not _node_has_tool_calls(node) and node.content:
            assistant_block.append(f"{node.node_id}{_clock(node)}: {node.content[:_ASSISTANT_NO_TOOL_CALL_LIMIT]}")
            disclosed.add(id(node))

    for subagent in sorted(nodes_by_subagent):
        nodes = nodes_by_subagent[subagent]
        for node in nodes[:_EDGE_NODE_COUNT] + nodes[-_EDGE_NODE_COUNT:]:
            if id(node) in disclosed:
                continue
            edge_block.append(f"{node.node_id}: {_node_line(node, _EDGE_NODE_LIMIT)}")
            disclosed.add(id(node))

    for node in flat:
        if node.role != "user" or id(node) in disclosed:
            continue
        content = str(node.content or "")
        if "<system-reminder>" in content:
            continue
        user_block.append(f"{node.node_id}: {content[:_USER_MESSAGE_LIMIT]}")
        disclosed.add(id(node))

    sections: list[str] = []
    if user_requests:
        clocks = {
            node.node_id: _clock(node)
            for node in getattr(nodes_by_subagent, "user_requests", None) or flat
            if node.node_id in dict(user_requests)
        }
        request_text = "\n\n".join(
            f"{node_id}{clocks.get(node_id, '')}: {text[:_USER_REQUEST_LIMIT]}" for node_id, text in user_requests
        )
        title = (
            "## 用户本人发出的消息（原文，按时间顺序；任务以此为准）"
            if language != "en"
            else "## The user's own messages (verbatim, in order; the task comes from these)"
        )
        sections.append(f"{title}\n{request_text}")
    assistant_text = "\n".join(assistant_block) if assistant_block else "（无）"
    edge_text = "\n".join(edge_block) if edge_block else "（无）"
    user_text = "\n".join(user_block) if user_block else "（无）"
    sections.append(f"## 助手消息正文（不含 tool call 的 assistant 消息）\n{assistant_text}")
    sections.append(f"## 各 subagent 首/尾节点（前 5 + 后 5，带 node_id）\n{edge_text}")
    sections.append(f"## 过程中的用户消息\n{user_text}")
    if file_candidates:
        rows = "\n".join(
            f"- {path} | {node_id} | {tool_type}"
            for path, node_id, tool_type in file_candidates[:_FILE_CANDIDATE_LIMIT]
        )
        sections.append(f"## 文件产物候选（路径 | 节点ID | 工具类型，按优先级排序）\n{rows}")
    del node_id_by_index, index_by_node_id
    return "\n\n".join(sections)


def _clock(node: TrajectoryNode) -> str:
    """`` [HH:MM:SS]`` (UTC) of a node's start, to compare what came first; ``""`` when unknown."""
    if not node.start_ts_ns:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(node.start_ts_ns / 1e9, tz=timezone.utc).strftime(" [%H:%M:%S]")


def _node_line(node: TrajectoryNode, limit: int) -> str:
    text = node.content or ""
    if node.tool_calls:
        import json as _json

        text = f"{text}\ntool_calls: {_json.dumps(node.tool_calls, ensure_ascii=False)}"
    return text[:limit]


def _response_content_preview(response: Any) -> str | None:
    content = getattr(response, "content", None)
    if content is None and isinstance(response, Mapping):
        content = response.get("content")
    if isinstance(content, str):
        return content[:100] or None
    return None


def _now_ns() -> int:
    import time

    return time.time_ns()


TASK_PARSE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "report_task_description",
            "description": (
                "汇报总结的任务描述文本（可含多个相关子任务）。 / Report the summarized task "
                "description (may cover multiple related subtasks)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "任务描述文本。 / The task description text."}
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_message_artifacts",
            "description": (
                "汇报任务消息产物节点（node_id 必须照抄轨迹摘要，不可编造）。 / Report the task "
                "message artifact nodes (node_ids must be copied from the digest; never fabricate)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "任务消息产物的节点 ID 列表。 / List of node ids of the task message artifacts.",
                    }
                },
                "required": ["node_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_file_artifacts",
            "description": (
                "汇报任务文件产物（绝对路径，必须来自候选清单；可舍弃无关文件）。 / Report the task "
                "file artifacts (absolute paths, chosen from the candidate list; unrelated files "
                "may be dropped)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "任务文件产物的绝对路径列表。 / List of absolute paths of the task file artifacts."
                        ),
                    }
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_delivery_round",
            "description": (
                "指出最后一次把任务结果交给用户的那一轮从用户的哪条消息开始（照抄「用户本人发出的消息」的"
                "node_id）；之后只是道谢、确认或闲聊的消息不算。 / Name the user message that starts the round "
                "in which the task's result was last delivered (node id copied from the user's own messages); "
                "later thanks, confirmations or small talk do not count."
            ),
            "parameters": {
                "type": "object",
                "properties": {"node_id": {"type": "string", "description": "该用户消息的节点 ID。 / Its node id."}},
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_requirements",
            "description": (
                "汇报当前有效的需求清单：把用户所有消息里对结果的要求合并成一份，后面的修改以后面为准，被推翻的"
                "旧要求不写；确认、回答、闲聊和只关于做法的话不写；用户纠正或抱怨过的点 corrected=true。 / "
                "Report the current requirement list: every demand on the result from all the user's messages, "
                "later changes overriding earlier ones (overridden demands left out); confirmations, answers, "
                "small talk and remarks only about how to work are not demands; mark points the user "
                "corrected or complained about with corrected=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": (
                                        "一条要求，一句话，保留原话中的时间、数量、名称和交付要求。 / One demand "
                                        "in one sentence, keeping the times, numbers, names and delivery terms."
                                    ),
                                },
                                "corrected": {
                                    "type": "boolean",
                                    "description": "用户纠正或抱怨过这一点。 / The user corrected or complained.",
                                },
                            },
                            "required": ["text"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_user_feedback",
            "description": (
                "汇报用户本人追加的消息里，表明之前的工作有问题或没满足用户的那些（纠正、抱怨、追问漏掉的"
                "信息）；新需求、换一种做法、回答、确认都不算；没有就传空列表。 / Report which of the user's "
                "follow-up messages say the work so far was wrong or fell short (a correction, a complaint, "
                "asking for something left out); a new request, another way of doing it, an answer or a "
                "confirmation is not; pass an empty list when there is none."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "这些用户消息的节点 ID（照抄「用户本人发出的消息」）。 / Node ids of those "
                            "messages, copied from the user's own messages."
                        ),
                    }
                },
                "required": ["node_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_task_error",
            "description": (
                "报错：选中区域不含完整任务（incomplete_task）或含多个互不相关子任务"
                "（multiple_unrelated_tasks）；不得与汇报类工具同轮调用。 / Report an error: "
                "incomplete_task or multiple_unrelated_tasks; must not be mixed with report "
                "tools in the same turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "error_type": {
                        "type": "string",
                        "enum": ["incomplete_task", "multiple_unrelated_tasks"],
                        "description": (
                            "只允许 incomplete_task 或 multiple_unrelated_tasks。/ Only "
                            '"incomplete_task" or "multiple_unrelated_tasks".'
                        ),
                    }
                },
                "required": ["error_type"],
            },
        },
    },
]

_TASK_ERROR_MESSAGES = {
    "incomplete_task": "选中区域不包含完整任务（无法提取任务描述文本），流程中止。",
    "multiple_unrelated_tasks": "选中轨迹包含多个无关子任务，流程中止。",
}

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

_prompt_cache: dict[str, str] = {}


def load_task_parse_prompt(language: str) -> str:
    lang = "zh" if str(language).strip().lower() in {"zh", "cn"} else "en"
    if lang not in _prompt_cache:
        _prompt_cache[lang] = (_PROMPT_DIR / f"task_parse_{lang}.md").read_text(encoding="utf-8")
    return _prompt_cache[lang]


def _response_tool_calls(response: Any) -> list[dict[str, Any]]:
    raw = getattr(response, "tool_calls", None)
    if raw is None and isinstance(response, Mapping):
        raw = response.get("tool_calls")
    if not raw:
        return []
    result: list[dict[str, Any]] = []
    for call in raw:
        if isinstance(call, Mapping):
            result.append(dict(call))
            continue
        function = getattr(call, "function", None)
        name = getattr(function, "name", None) or getattr(call, "name", None)
        arguments = getattr(function, "arguments", None) or getattr(call, "arguments", None)
        if name is not None:
            result.append({"name": name, "arguments": arguments or "{}"})
    return result


def _artifact_text(nodes_by_subagent: Mapping[str, list[TrajectoryNode]], node_id: str) -> str | None:
    for nodes in nodes_by_subagent.values():
        for node in nodes:
            if node.node_id == node_id:
                return node.content
    return None


def _user_feedback_texts(calls: list[dict[str, Any]], user_requests: Sequence[tuple[str, str]]) -> list[str] | None:
    """Texts of the user's messages the model judged to be feedback on the work; ``None`` when it did not say."""
    reported = [call for call in calls if call.get("name") == "report_user_feedback"]
    if not reported:
        return None
    by_id = dict(user_requests)
    texts: list[str] = []
    for call in reported:
        arguments = _arguments(call)
        if not isinstance(arguments, Mapping):
            continue
        for node_id in arguments.get("node_ids") or ():
            text = by_id.get(str(node_id))
            if text and text not in texts:
                texts.append(text)
    return texts


def _is_team_internal(nodes_by_subagent: Mapping[str, list[TrajectoryNode]], node_id: str) -> bool:
    """Whether the node belongs to a team member: its messages go to the leader, never to the user."""
    return any(
        node.node_id == node_id and node.kind == _TEAM_MEMBER_KIND
        for nodes in nodes_by_subagent.values()
        for node in nodes
    )


def last_writes(nodes_by_subagent: Mapping[str, list[TrajectoryNode]], workspace: str | None = None) -> dict[str, str]:
    """``{path: node_id}`` of the latest call, across all subjects, that wrote each file.

    Calls are taken in time order over every subject (a member may rewrite what the leader wrote);
    sending a file to the user is not a write.
    """
    import dataclasses

    rows = sorted((node for nodes in nodes_by_subagent.values() for node in nodes), key=lambda node: node.start_ts_ns)
    writes_only = [
        dataclasses.replace(
            node,
            tool_calls=[
                call
                for call in node.tool_calls
                if str(call.get("name") or call.get("function", {}).get("name") or "") != _R0_TOOL
            ],
        )
        if node.tool_calls
        else node
        for node in rows
    ]
    candidates, _ = extract_file_candidates({"": writes_only}, workspace)
    return {path: node_id for path, node_id, _ in candidates}


def _named_user_message(calls: list[dict[str, Any]], tool: str, user_requests: Sequence[tuple[str, str]]) -> str | None:
    """The user's message the model named with ``tool``, when it is one of the user's own messages."""
    ids = {node_id for node_id, _ in user_requests}
    for call in calls:
        if call.get("name") != tool:
            continue
        arguments = _arguments(call)
        node_id = str(arguments.get("node_id") or "") if isinstance(arguments, Mapping) else ""
        if node_id in ids:
            return node_id
    return None


def _requirements(calls: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """The current requirement list the model reported: ``[{"text", "corrected"}]``; ``None`` when it did not."""
    reported = [call for call in calls if call.get("name") == "report_requirements"]
    if not reported:
        return None
    items: list[dict[str, Any]] = []
    for call in reported:
        arguments = _arguments(call)
        if not isinstance(arguments, Mapping):
            continue
        for item in arguments.get("items") or ():
            text = str(item.get("text") or "").strip() if isinstance(item, Mapping) else str(item or "").strip()
            if text and all(text != existing["text"] for existing in items):
                items.append({"text": text, "corrected": bool(isinstance(item, Mapping) and item.get("corrected"))})
    return items


def _sends_file(node: TrajectoryNode) -> bool:
    return any(
        str(call.get("name") or call.get("function", {}).get("name") or "") == _R0_TOOL for call in node.tool_calls
    )


@dataclass(frozen=True)
class DeliveryRound:
    """The round in which the task's result was last delivered: from a user message to the next one.

    Only this round's deliverables are reviewed, and only when the selection holds the step in which the
    leader (or the main agent) handed them over: its last reply, sending a file, or writing a deliverable
    itself.  Selecting the start of the round, or only team members, is a look at the process.
    """

    start: TrajectoryNode
    end_ns: int | None
    whole: Mapping[str, list[TrajectoryNode]]
    workspace: str | None

    def holds(self, node: TrajectoryNode) -> bool:
        return node.start_ts_ns >= self.start.start_ts_ns and (self.end_ns is None or node.start_ts_ns < self.end_ns)

    def delivery_nodes(self, reported: Sequence[str] = ()) -> set[str]:
        """The leader's last step handing the result over in this round, as a one-item set (empty when none).

        A hand-over step is its last reply, a call sending a file to the user, or its own last write of a
        reported deliverable file (a single agent writes its files itself); only the latest of them counts,
        so a selection ending before the final reply does not review the deliverables.
        """
        steps = {node_id for node_id, _ in self.reply()}
        for nodes in self.whole.values():
            for node in nodes:
                if node.kind in _LEAD_KINDS and self.holds(node) and _sends_file(node):
                    steps.add(node.node_id)
        lead = {node.node_id: node for nodes in self.whole.values() for node in nodes if node.kind in _LEAD_KINDS}
        written = last_writes(self.whole, self.workspace)
        steps.update(written[path] for path in self.files(list(reported)) if written.get(path) in lead)
        timed = [lead[node_id] for node_id in steps if node_id in lead]
        if not timed:
            return set()
        last = max(timed, key=lambda node: (node.start_ts_ns, node.end_ts_ns))
        return {last.node_id}

    def lead_selected(
        self, nodes_by_subagent: Mapping[str, list[TrajectoryNode]], reported: Sequence[str] = ()
    ) -> bool:
        """Whether the selection holds the leader's last hand-over step of this round (see ``delivery_nodes``)."""
        steps = self.delivery_nodes(reported)
        return any(node.node_id in steps for nodes in nodes_by_subagent.values() for node in nodes)

    def replies(self) -> list[TrajectoryNode]:
        rows = [
            node
            for nodes in self.whole.values()
            for node in nodes
            if node.kind in _LEAD_KINDS
            and node.role == "assistant"
            and not node.tool_calls
            and node.content
            and self.holds(node)
        ]
        return sorted(rows, key=lambda node: node.start_ts_ns)

    def reply(self) -> list[tuple[str, str]]:
        """The leader's last reply in the round, as the message deliverable."""
        replies = self.replies()
        return [(replies[-1].node_id, str(replies[-1].content))] if replies else []

    def written(self) -> list[str]:
        """Files last written, or last sent to the user, in this round (a file sent was delivered then)."""
        at = {node.node_id: node for nodes in self.whole.values() for node in nodes}
        rows = sorted(at.values(), key=lambda node: node.start_ts_ns)
        touched, _ = extract_file_candidates({"": rows}, self.workspace)  # in time order, sending included
        last = dict(last_writes(self.whole, self.workspace))
        paths: list[str] = []
        for path, node_id, _ in touched:
            if any(n in at and self.holds(at[n]) for n in (node_id, last.get(path))):
                paths.append(path)
        return paths

    def files(self, reported: list[str]) -> list[str]:
        """The reported deliverable files last written in this round (an earlier version is not final)."""
        written = set(self.written())
        return [path for path in reported if path in written]

    def delivers(self) -> bool:
        """Whether the round handed over a result: a file written, or a reply of some length."""
        if self.written():
            return True
        return sum(len(str(node.content)) for node in self.replies()) >= _SHORT_REPLY_CHARS


def delivery_round(
    whole: Mapping[str, list[TrajectoryNode]], workspace: str | None = None, named: str | None = None
) -> DeliveryRound | None:
    """The round that last delivered the task's result; ``None`` without any message from the user.

    By rule it is the last round that wrote or sent a file, else the last one with a reply of some length
    (so a closing "thanks" / "you're welcome" is skipped).  The model names the user message that started
    the delivery (``named``, from task parsing).  It only matters when the rule's round sent no file: a
    named earlier round that delivered is then the delivery (a later side question answered at length is
    not the result).  Otherwise the rule stands: a file sent says where the result was delivered.
    """
    starts = user_request_nodes(whole)
    if not starts:
        return None
    rounds = [
        DeliveryRound(node, starts[index + 1].start_ts_ns if index + 1 < len(starts) else None, whole, workspace)
        for index, node in enumerate(starts)
    ]
    by_rule = next((item for item in reversed(rounds) if item.written()), None) or next(
        (item for item in reversed(rounds) if item.delivers()), rounds[-1]
    )
    chosen = next((item for item in rounds if item.start.node_id == named), None)
    if chosen is not None and not by_rule.written() and chosen.delivers():
        return chosen
    return by_rule


async def run_task_parse(
    nodes_by_subagent: Mapping[str, list[TrajectoryNode]],
    language: str,
    llm: TaskParseLLM,
    *,
    workspace: str | None = None,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    max_retries: int = 2,
    system_prompt: str | None = None,
    status_sink: Any = None,
    model_name: str = "",
    status_counter: list[int] | None = None,
) -> TaskParseResult:
    """Run the file-artifact rules and the task-parse pseudo-agent.

    The pseudo-agent gets one response per attempt; malformed responses retry
    up to ``max_retries`` times.  An explicit ``report_task_error`` aborts the
    run with :class:`TaskParseError`.
    """
    whole = getattr(nodes_by_subagent, "whole", None)
    # deliverables are judged at their final version, so the candidates come from the whole session
    file_candidates, extraction_stats = extract_file_candidates(whole or nodes_by_subagent, workspace)
    user_requests = user_request_of(nodes_by_subagent)
    digest = build_task_parse_digest(nodes_by_subagent, file_candidates, language, user_requests)
    system = system_prompt or load_task_parse_prompt(language)
    counter = status_counter if status_counter is not None else [0]
    attempts = 0
    while attempts <= max_retries:
        attempts += 1
        started_at = _now_ns()
        try:
            response = await llm.invoke(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": digest},
                ],
                tools=TASK_PARSE_TOOLS,
            )
        except Exception as exc:  # noqa: BLE001 - a failed call counts as one attempt
            from openjiuwen.agent_evolving.attribution.events import emit_single_llm_event

            emit_single_llm_event(
                status_sink,
                stage="task_parse",
                model=model_name,
                counter=counter,
                started_at_ns=started_at,
                ended_at_ns=_now_ns(),
                retry_index=attempts - 1,
                error=str(exc)[:200],
            )
            if attempts > max_retries:
                raise
            continue
        calls = _response_tool_calls(response)
        from openjiuwen.agent_evolving.attribution.events import emit_single_llm_event

        emit_single_llm_event(
            status_sink,
            stage="task_parse",
            model=model_name,
            counter=counter,
            started_at_ns=started_at,
            ended_at_ns=_now_ns(),
            preview=_response_content_preview(response),
            tool_calls=[str(call.get("name") or "") for call in calls],
            retry_index=attempts - 1,
        )
        error_calls = [call for call in calls if call.get("name") == "report_task_error"]
        report_calls = [call for call in calls if call.get("name") != "report_task_error"]
        if error_calls and not report_calls:
            arguments = _arguments(error_calls[0])
            error_type = (
                str(arguments.get("error_type") or "incomplete_task")
                if isinstance(arguments, Mapping)
                else "incomplete_task"
            )
            message = _TASK_ERROR_MESSAGES.get(error_type, _TASK_ERROR_MESSAGES["incomplete_task"])
            raise TaskParseError(error_type, message)
        if error_calls and report_calls:
            continue
        description = next((call for call in calls if call.get("name") == "report_task_description"), None)
        if description is None:
            continue
        task_description = _arguments(description)
        if not isinstance(task_description, Mapping) or not str(task_description.get("task") or "").strip():
            continue
        message_artifacts: list[tuple[str, str]] = []
        for call in calls:
            if call.get("name") != "report_message_artifacts":
                continue
            arguments = _arguments(call)
            if not isinstance(arguments, Mapping):
                continue
            for node_id in arguments.get("node_ids") or ():
                text = _artifact_text(nodes_by_subagent, str(node_id))
                if text is not None:
                    message_artifacts.append((str(node_id), text))
        file_artifacts: list[str] = []
        for call in calls:
            if call.get("name") != "report_file_artifacts":
                continue
            arguments = _arguments(call)
            if not isinstance(arguments, Mapping):
                continue
            for path in arguments.get("paths") or ():
                file_artifacts.append(str(path))
        user_feedback = _user_feedback_texts(calls, user_requests)
        requirements = _requirements(calls)
        named_round = _named_user_message(calls, "report_delivery_round", user_requests)
        reported_messages, reported_files = len(message_artifacts), len(file_artifacts)
        round_start, round_selected = None, None
        if whole is None:
            message_artifacts = [m for m in message_artifacts if not _is_team_internal(nodes_by_subagent, m[0])]
        else:
            delivery = delivery_round(whole, workspace, named_round)
            round_start = delivery.start.node_id if delivery else None
            round_selected = delivery is not None and delivery.lead_selected(nodes_by_subagent, file_artifacts)
            if round_selected:
                message_artifacts = delivery.reply()
                file_artifacts = delivery.files(file_artifacts)
            else:
                message_artifacts, file_artifacts = [], []
        run_meta = {
            "attempts": attempts,
            "file_candidates": len(file_candidates),
            "extraction": extraction_stats,
            "file_artifacts": len(file_artifacts),
            "message_artifacts": len(message_artifacts),
            "user_requests": len(user_requests),
            "reported_messages": reported_messages,
            "reported_files": reported_files,
            "delivery_round": round_start,
            "delivery_round_named": named_round,
            "delivery_round_selected": round_selected,
            "requirements": None if requirements is None else len(requirements),
            "user_feedback": None if user_feedback is None else len(user_feedback),
        }
        if on_progress is not None:
            await on_progress(run_meta)
        return TaskParseResult(
            task_description=str(task_description["task"]),
            message_artifacts=message_artifacts,
            file_artifacts=file_artifacts,
            file_candidates=list(file_candidates),
            run_meta=run_meta,
            user_request="\n\n".join(text for _, text in user_requests),
            user_feedback=user_feedback,
            requirements=requirements or [],
            delivery_round=round_start,
            delivery_round_selected=round_selected,
        )
    raise TaskParseError("invalid_response", f"task parse failed after {attempts} attempts")


__all__ = [
    "TASK_PARSE_TOOLS",
    "TaskParseError",
    "TaskParseLLM",
    "DeliveryRound",
    "build_task_parse_digest",
    "delivery_round",
    "user_request_of",
    "extract_file_candidates",
    "last_writes",
    "load_task_parse_prompt",
    "run_task_parse",
]
