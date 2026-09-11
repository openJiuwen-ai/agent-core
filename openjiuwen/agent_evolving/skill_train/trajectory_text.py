# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rendering of stored agent conversations into analyst-readable transcripts.

Both the minibatch reflect stage and the epoch-level slow update need the same
bracketed transcript format, so the renderer lives here once.  Entries are
classified by shape (tool call / environment step / system note / plain
message) and each shape has its own line emitter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from openjiuwen.agent_evolving.skill_train.utils import prediction_item_dir

MISSING_TRAJECTORY = "(trajectory not available)"
UNREADABLE_TRAJECTORY = "(trajectory read error)"
EMPTY_TRAJECTORY = "(empty trajectory)"

Entry = Mapping[str, Any]
LineEmitter = Callable[[Entry], list[str]]


def as_text(value: Any) -> str:
    """Stringify an optional transcript field; ``None`` renders as empty.

    Nothing is truncated on purpose -- the optimizer is shown exactly what the
    agent saw and did.
    """
    return "" if value is None else str(value)


def _tool_call_lines(entry: Entry) -> list[str]:
    return [
        f"[action] {as_text(entry.get('cmd'))}",
        f"[obs]    {as_text(entry.get('obs'))}",
    ]


def _env_step_lines(entry: Entry) -> list[str]:
    step = entry.get("step", "?")
    lines: list[str] = []
    thought = as_text(entry.get("reasoning"))
    if thought:
        lines.append(f"[step {step} think] {thought}")
    lines.append(f"[step {step} action] {as_text(entry.get('action'))}")
    lines.append(f"[step {step} obs]    {as_text(entry.get('env_feedback'))}")
    return lines


def _verification_lines(entry: Entry) -> list[str]:
    return [f"[verification] {as_text(entry.get('content'))}"]


def _message_lines(entry: Entry) -> list[str]:
    return [f"[{entry.get('role', 'agent')}] {as_text(entry.get('content'))}"]


#: Ordered ``(matcher, emitter)`` rules; the first matching rule wins.
_SHAPES: tuple[tuple[Callable[[Entry], bool], LineEmitter], ...] = (
    (lambda entry: entry.get("type") == "tool_call", _tool_call_lines),
    (lambda entry: "action" in entry and "env_feedback" in entry, _env_step_lines),
    (lambda entry: entry.get("role") == "system", _verification_lines),
)


def _emit(entry: Entry) -> list[str]:
    for matches, emitter in _SHAPES:
        if matches(entry):
            return emitter(entry)
    return _message_lines(entry)


def render_conversation(conversation: Sequence[Any], *, keep_scalars: bool = False) -> str:
    """Render a stored conversation into bracketed transcript lines.

    ``keep_scalars`` controls what happens to non-dict entries: reflect keeps
    them as anonymous agent lines, the slow update drops them.
    """
    lines: list[str] = []
    for entry in conversation:
        if isinstance(entry, Mapping):
            lines.extend(_emit(entry))
        elif keep_scalars:
            lines.append(f"[agent] {as_text(entry)}")
    return "\n".join(lines)


def conversation_path(prediction_dir: str, task_id: str) -> Path:
    """Locate ``<prediction_dir>/<task_id>/conversation.json``."""
    return Path(prediction_item_dir(prediction_dir, task_id)) / "conversation.json"


def load_conversation(prediction_dir: str, task_id: str) -> list | None:
    """Read one stored conversation, or ``None`` when absent/unreadable."""
    path = conversation_path(prediction_dir, task_id)
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt transcript must not stop a run
        return None
    return parsed if isinstance(parsed, list) else None


def _predictions_root(rollout_dir: str) -> str:
    """Accept either a rollout dir or the ``predictions/`` dir inside it."""
    root = Path(rollout_dir)
    nested = root / "predictions"
    if nested.is_dir() or root.name != "predictions":
        return str(nested)
    return str(root)


def read_trajectory(rollout_dir: str, task_id: str) -> str:
    """Render one task's transcript from a rollout directory.

    Returns a short placeholder string when the transcript is missing, broken
    or empty so the text stays usable inside a comparison prompt.
    """
    root = _predictions_root(rollout_dir)
    if not conversation_path(root, task_id).exists():
        return MISSING_TRAJECTORY
    conversation = load_conversation(root, task_id)
    if conversation is None:
        return UNREADABLE_TRAJECTORY
    if not conversation:
        return EMPTY_TRAJECTORY
    return render_conversation(conversation)
