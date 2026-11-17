"""Delivery metadata for explicitly mode-tagged team private messages."""

import json

from openjiuwen.agent_teams.schema.status import TaskStatus

MODE_KEY = "private_delivery_mode"
MODES = ("steer", "follow_up", "abort")
ACTIVE_TASKS = frozenset(
    status.value for status in (TaskStatus.PLANNING, TaskStatus.IN_PROGRESS, TaskStatus.IN_REVIEW)
)


def read_delivery(message):
    """Read framework metadata, never instructions embedded in the message body."""
    if getattr(message, "broadcast", False):
        return None, {}
    raw = getattr(message, "meta", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, {}
    if not isinstance(raw, dict) or raw.get(MODE_KEY) not in MODES:
        return None, {}
    tasks = raw.get("target_tasks")
    return raw[MODE_KEY], tasks if isinstance(tasks, dict) else {}
