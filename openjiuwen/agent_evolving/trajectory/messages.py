# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Project canonical projection from trajectory spans to chat messages."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from copy import deepcopy
from types import MappingProxyType
from typing import Any, Final, Literal, TypeAlias

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    is_compaction_span,
    iter_spans,
    read_llm_exchange,
    read_tool_call,
    span_sort_key,
)
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.agent_evolving.trajectory.windows import replay_windows, window_for_inference


MessageField: TypeAlias = Literal["content", "reasoning_content", "name", "tool_calls", "tool_call_id"]
DEFAULT_EVOLUTION_MESSAGE_FIELDS: Final[frozenset[MessageField]] = frozenset(
    {"content", "name", "tool_calls", "tool_call_id"}
)
# ``reasoning_content`` is selectable but not a default: evolution reads what
# the agent said, and a reader that wants the thinking asks for it.
_MESSAGE_FIELDS = frozenset(DEFAULT_EVOLUTION_MESSAGE_FIELDS | {"reasoning_content"})


def _field(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _tool_call_field(tool_call: object, name: str) -> object | None:
    value = _field(tool_call, name)
    if value is not None and value != "":
        return value
    function = _field(tool_call, "function")
    nested_value = _field(function, name) if function is not None else None
    return nested_value if nested_value is not None else value


def tool_call_id(tool_call: object) -> object | None:
    """Return a tool-call ID from project-flat or OpenAI nested input."""

    return _tool_call_field(tool_call, "id")


def tool_call_name(tool_call: object) -> object | None:
    """Return a tool-call name from project-flat or OpenAI nested input."""

    return _tool_call_field(tool_call, "name")


def tool_call_arguments(tool_call: object) -> object | None:
    """Return tool-call arguments from project-flat or OpenAI nested input."""

    return _tool_call_field(tool_call, "arguments")


def _json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _normalize_tool_call(tool_call: object) -> dict[str, Any]:
    result: dict[str, Any] = {}
    call_id = tool_call_id(tool_call)
    if call_id is not None:
        result["id"] = deepcopy(call_id)

    call_type = _field(tool_call, "type")
    result["type"] = deepcopy(call_type) if call_type is not None else "function"

    function: dict[str, Any] = {}
    name = tool_call_name(tool_call)
    arguments = tool_call_arguments(tool_call)
    if name is not None:
        function["name"] = deepcopy(name)
    if arguments is not None:
        function["arguments"] = _json_text(arguments)
    result["function"] = function
    return result


def _normalize_message(message: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(message))
    tool_calls = result.get("tool_calls")
    if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes)):
        result["tool_calls"] = [_normalize_tool_call(tool_call) for tool_call in tool_calls]
    return result


def _trim_prompt_to_last_user(prompt: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Keep the current-invoke entry: last ``user`` message through the end.

    LLM spans may store the full request prompt (prior session rounds included).
    Callers such as TTSE pass ``invoke_local=True`` so a missing-window fallback
    does not re-import that history. This is a slice, not text-overlap merge.
    """
    last_user: int | None = None
    for index, message in enumerate(prompt):
        if message.get("role") == "user":
            last_user = index
    if last_user is None:
        return []
    return list(prompt[last_user:])


def _tool_message(
    tool_call: Mapping[str, Any],
    tool_call_names: Mapping[str, object],
) -> dict[str, Any]:
    result: dict[str, Any] = {"role": "tool"}
    call_id = tool_call.get("id")
    name = tool_call.get("name")
    if name is None and call_id is not None:
        name = tool_call_names.get(str(call_id))
    if name is not None:
        result["name"] = deepcopy(name)
    if call_id is not None:
        result["tool_call_id"] = deepcopy(call_id)

    output = tool_call.get("output")
    if output is not None:
        result["content"] = _json_text(output)
    else:
        error = tool_call.get("error")
        if isinstance(error, Mapping) and error.get("message") is not None:
            result["content"] = str(error["message"])
    return result


def _select_fields(message: Mapping[str, Any], fields: frozenset[str]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in message.items() if key == "role" or key in fields}


def _window_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Render one context-window message as a chat message."""

    result: dict[str, Any] = {"role": message.get("role")}
    for key in ("content", "name", "tool_calls", "tool_call_id"):
        if key in message and message[key] is not None:
            result[key] = deepcopy(message[key])
    return _normalize_message(result)


def _tool_call_ids(message: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(tool_call_id(call)) for call in message.get("tool_calls") or () if tool_call_id(call) is not None)


def _same_assistant_turn(completion: Mapping[str, Any], message: Mapping[str, Any]) -> bool:
    """Whether a window's assistant message is a completion recorded earlier.

    A completion is recorded before any window names it, so it has no
    message id to join by. Its tool calls do have ids; a turn without tool
    calls is joined by its text, which the context engine keeps verbatim.
    """

    completion_calls = _tool_call_ids(completion)
    if completion_calls or _tool_call_ids(message):
        return completion_calls == _tool_call_ids(message)
    return _json_text(completion.get("content")) == _json_text(message.get("content"))


class _MessageProjection:
    """Accumulate one conversation from windows, completions and tool results."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.issues: list[Mapping[str, object]] = []
        self._seen_message_ids: set[str] = set()
        self._unconfirmed_completions: list[int] = []
        self._tool_results: dict[str, int] = {}
        self._tool_call_names: dict[str, object] = {}

    def add_window(self, window: Sequence[Mapping[str, Any]]) -> None:
        for message in window:
            message_id = str(message.get("message_id") or "")
            if message_id in self._seen_message_ids:
                continue
            self._seen_message_ids.add(message_id)
            rendered = _window_message(message)
            if rendered.get("role") == "assistant" and self._confirm_completion(rendered):
                continue
            if rendered.get("role") == "tool" and self._confirm_tool_result(rendered):
                continue
            self._remember_tool_call_names(rendered)
            self.messages.append(rendered)

    def add_prompt(self, prompt: Sequence[Mapping[str, Any]]) -> None:
        for message in prompt:
            self._remember_tool_call_names(message)
            self.messages.append(dict(message))

    def add_completions(self, completions: Sequence[Mapping[str, Any]]) -> None:
        for message in completions:
            self._remember_tool_call_names(message)
            if message.get("role") == "assistant":
                self._unconfirmed_completions.append(len(self.messages))
            self.messages.append(dict(message))

    def add_tool_result(self, call: Mapping[str, Any]) -> None:
        message = _tool_message(call, self._tool_call_names)
        call_id = message.get("tool_call_id")
        if call_id is not None:
            self._tool_results[str(call_id)] = len(self.messages)
        self.messages.append(message)

    def _confirm_completion(self, message: Mapping[str, Any]) -> bool:
        for position, index in enumerate(self._unconfirmed_completions):
            if _same_assistant_turn(self.messages[index], message):
                del self._unconfirmed_completions[: position + 1]
                return True
        return False

    def _confirm_tool_result(self, message: Mapping[str, Any]) -> bool:
        call_id = message.get("tool_call_id")
        index = self._tool_results.pop(str(call_id), None) if call_id is not None else None
        if index is None:
            return False
        # The model was given the window's content; the span keeps the tool's
        # name and, for a failed call without content, its error.
        recorded = self.messages[index]
        if message.get("content") is not None:
            recorded["content"] = deepcopy(message["content"])
        return True

    def _remember_tool_call_names(self, message: Mapping[str, Any]) -> None:
        for call in message.get("tool_calls") or ():
            call_id = tool_call_id(call)
            name = tool_call_name(call)
            if call_id is not None and name is not None:
                self._tool_call_names[str(call_id)] = deepcopy(name)


def project_trajectory_messages(
    trajectory: Trajectory,
    *,
    fields: Collection[MessageField] = DEFAULT_EVOLUTION_MESSAGE_FIELDS,
    invoke_local: bool = False,
) -> tuple[list[dict[str, Any]], tuple[Mapping[str, object], ...]]:
    """Project a trajectory into detached chat messages, with its defects.

    Each model request contributes the context window it was sent, rebuilt
    from its ``context.window.commit`` chain; a message already contributed
    by an earlier window is recognised by its ``message_id`` and not repeated.
    A request then contributes its completions, and a tool span its result.
    A completion or tool result a later window restates is kept once: tool
    results take the content the model was given, completions keep what the
    model produced.

    A request with no committed window contributes its recorded prompt as it
    stands and is reported as ``missing_context_window``; no attempt is made
    to guess which of its messages an earlier request already had.
    Compaction requests contribute nothing: their prompt is about the
    conversation, not part of it.

    ``invoke_local=True`` (TTSE detect/induce) slices a missing-window prompt
    or a committed window to the last ``user`` message. Later spans in the same
    projection do not re-append that prompt; they only add completions/tools.
    This does not compare prompt text to merge overlap.

    Args:
        trajectory: Canonical trajectory including its v2 event spans.
        fields: Semantic message fields to keep besides ``role``.
        invoke_local: If True, keep only the current-invoke user turn.

    Returns:
        The messages, and the issues found while projecting them.
    """

    selected_fields = frozenset(fields)
    unknown_fields = selected_fields - _MESSAGE_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unknown trajectory message fields: {names}")

    replay = replay_windows(trajectory)
    projection = _MessageProjection()
    # A gap only says some event of the subject was not captured (an ask_user
    # logged on an agent span outside this window, say); a later commit still
    # replays, so it does not make any message wrong.
    projection.issues.extend(issue for issue in replay.issues if issue.get("code") != "v2.sequence_gap")
    for span in sorted(iter_spans(trajectory), key=span_sort_key):
        category = span_category(span)
        if category == "llm":
            if is_compaction_span(span):
                continue
            raw_prompt, raw_completions = read_llm_exchange(span)
            window = window_for_inference(replay, span)
            if window is None:
                projection.issues.append(
                    MappingProxyType(
                        {
                            "code": "missing_context_window",
                            "message": "model request has no committed context window",
                            "span_id": str(span.get("spanId") or ""),
                        }
                    )
                )
                prompt = [_normalize_message(message) for message in raw_prompt]
                if invoke_local:
                    if not projection.messages:
                        projection.add_prompt(_trim_prompt_to_last_user(prompt))
                else:
                    projection.add_prompt(prompt)
            else:
                if invoke_local:
                    window = _trim_prompt_to_last_user(window)
                projection.add_window(window)
            projection.add_completions([_normalize_message(message) for message in raw_completions])
            continue
        if category == "tool":
            projection.add_tool_result(read_tool_call(span))

    messages = [_select_fields(message, selected_fields) for message in projection.messages]
    return messages, tuple(projection.issues)


def trajectory_to_messages(
    trajectory: Trajectory,
    *,
    fields: Collection[MessageField] = DEFAULT_EVOLUTION_MESSAGE_FIELDS,
    invoke_local: bool = False,
) -> list[dict[str, Any]]:
    """Project canonical spans into detached OpenAI-compatible messages.

    See :func:`project_trajectory_messages`, which also returns the issues.
    """

    messages, _ = project_trajectory_messages(
        trajectory, fields=fields, invoke_local=invoke_local
    )
    return messages


__all__ = [
    "DEFAULT_EVOLUTION_MESSAGE_FIELDS",
    "MessageField",
    "project_trajectory_messages",
    "tool_call_arguments",
    "tool_call_id",
    "tool_call_name",
    "trajectory_to_messages",
]
