# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Two-phase rendering for framework-templated team messages (F_63).

A scheduler handoff is *sent* as an intent and *rendered* at delivery:

* **send time** — the scheduler writes a mailbox row whose ``content`` is
  empty and whose ``meta`` column carries a template key plus its data
  bindings (``refs`` to table rows, ``params`` for transient values).
* **delivery time** — this module loads the markdown template in the
  recipient's language, resolves every ``{{namespace.field}}`` against the
  *current* task / member rows, and hands the harness one coherent document.

Storing a reference instead of a snapshot buys three things: the task body
lives in exactly one place (the task table), a later ``update_task`` is
visible to messages already queued, and the message history stays readable
instead of carrying N copies of a task brief.

The placeholder contract:

* one syntax, ``{{ns.field}}``, resolved in a single ``re.sub`` pass — the
  substituted values are **never rescanned**, so LLM-authored task text
  containing ``{{...}}`` cannot inject placeholders;
* namespaces declare an explicit field whitelist (no ``getattr``
  passthrough) so a new DB column never silently leaks into a prompt;
* an unknown namespace / field / missing row renders as ``<missing:ns.field>``
  rather than raising — lazy-safe, in the spirit of the error system's
  message rendering.

Templates are code assets under ``prompts/<lang>/<key>.md``, never stored in
the DB: a reworded template takes effect immediately for messages already
queued, and a half-rendered ``{{...}}`` string can never reach a reader.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.core.common.logging import team_logger

# Async row lookups the expansion needs. Narrow callables rather than a DB
# handle: the two delivery sites (in-process mailbox drain, external inbox)
# reach their rows through different objects, and tests fake them in one line.
TaskGetter = Callable[[str], Awaitable[Any]]
MemberGetter = Callable[[str], Awaitable[Any]]

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\.([a-z_]+)\s*\}\}")

_REF_TASK = "task"
_REF_MEMBER = "member"
_NS_PARAM = "param"


def _task_reviewers(task: Any) -> str:
    return ", ".join(task.reviewers())


def _task_max_rounds(task: Any) -> str:
    return "" if task.max_review_rounds is None else str(task.max_review_rounds)


# Field whitelists. A namespace resolver maps a whitelisted field name to a
# reader over the row — never a raw getattr, so adding a DB column does not
# widen the prompt surface by accident.
_TASK_FIELDS: dict[str, Callable[[Any], str]] = {
    "task_id": lambda task: str(task.task_id),
    "title": lambda task: task.title or "",
    "content": lambda task: task.content or "",
    "status": lambda task: str(task.status),
    "assignee": lambda task: task.assignee or "",
    "reviewer": _task_reviewers,
    "review_round": lambda task: str(task.review_round),
    "max_review_rounds": _task_max_rounds,
}

_MEMBER_FIELDS: dict[str, Callable[[Any], str]] = {
    "member_name": lambda member: str(member.member_name),
    "display_name": lambda member: member.display_name or "",
    "desc": lambda member: member.desc or "",
}


class RefUnresolved(Exception):
    """A row named by ``meta.refs`` could not be fetched.

    Distinct from a placeholder the namespaces cannot answer: a missing
    *field* is a template bug and renders inline as ``<missing:ns.field>``,
    while a missing *row* (task cancelled and swept, member removed) means the
    document has no subject at all — the delivery degrades to the fallback
    line instead of shipping a brief full of holes.
    """


@dataclass(frozen=True, slots=True)
class ExpandedMessage:
    """The text to deliver for one message row.

    Attributes:
        body: The rendered document (templated messages) or the sender's
            original content (ordinary messages).
        is_template: Whether the row carried a framework template. Delivery
            sites use it to drop the reply hint — a framework instruction is
            answered with a tool call, not a reply.
    """

    body: str
    is_template: bool


def parse_meta(raw: Any) -> dict | None:
    """Parse the ``meta`` column into a template descriptor, or None.

    Returns None for rows without meta, for malformed JSON, and for meta
    without a ``template`` key — all of which mean "an ordinary message,
    render ``content`` verbatim".
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        meta = json.loads(raw)
    except (ValueError, TypeError):
        team_logger.warning("[message_template] malformed meta, treating as a plain message")
        return None
    if not isinstance(meta, dict) or not meta.get("template"):
        return None
    return meta


def build_meta(template: str, *, refs: dict[str, str] | None = None, params: dict[str, str] | None = None) -> dict:
    """Assemble a delivery payload for one templated message.

    Args:
        template: Template key, i.e. the ``prompts/<lang>/<key>.md`` basename.
        refs: Row bindings resolved *at delivery* — ``{"task": task_id}`` and
            ``{"member": member_name}``. Anything a table can answer belongs
            here, so the reader sees current truth rather than a snapshot.
        params: Transient scalars a table cannot answer (e.g. an aggregated
            reviewer feedback block), frozen at send time.
    """
    meta: dict[str, Any] = {"template": template}
    if refs:
        meta["refs"] = refs
    if params:
        meta["params"] = {key: str(value) for key, value in params.items()}
    return meta


def fallback_line(meta: dict) -> str:
    """Synthesize a one-line stand-in when expansion fails.

    Deliberately not a stored copy: the template key and the task id in meta
    are all a reader needs to recover (``view_task`` fills in the rest), so a
    healthy delivery pays nothing for a fallback that almost never runs.
    """
    template = meta.get("template") or "?"
    task_id = (meta.get("refs") or {}).get(_REF_TASK)
    if task_id:
        return f"[{template}] task_id={task_id} — details unavailable, call view_task for the task."
    return f"[{template}] — details unavailable."


async def expand_message(
    msg: Any,
    *,
    task_getter: TaskGetter,
    member_getter: MemberGetter,
    language: str,
) -> ExpandedMessage:
    """Render one message row into the text to deliver.

    Ordinary messages pass through untouched. A templated message loads its
    markdown in ``language``, resolves the placeholders against the rows named
    by ``meta.refs``, and returns the document; any failure (template gone,
    task row deleted, unreadable meta) degrades to ``fallback_line`` so a
    delivery never dies on a rendering problem.
    """
    meta = parse_meta(getattr(msg, "meta", None))
    if meta is None:
        return ExpandedMessage(body=msg.content, is_template=False)
    try:
        values = await _resolve_namespaces(meta, task_getter=task_getter, member_getter=member_getter)
        template = load_template(str(meta["template"]), language)
        return ExpandedMessage(body=_substitute(template.content, values), is_template=True)
    except Exception:
        team_logger.warning(
            "[message_template] expansion of template %s failed, delivering the fallback line",
            meta.get("template"),
            exc_info=True,
        )
        return ExpandedMessage(body=fallback_line(meta), is_template=True)


async def _resolve_namespaces(
    meta: dict,
    *,
    task_getter: TaskGetter,
    member_getter: MemberGetter,
) -> dict[str, dict[str, str]]:
    """Fetch the referenced rows and project them onto the whitelists.

    Raises:
        RefUnresolved: A referenced row is gone; the caller degrades to the
            fallback line rather than render a document without its subject.
    """
    refs = meta.get("refs") or {}
    params = meta.get("params") or {}
    values: dict[str, dict[str, str]] = {_NS_PARAM: {key: str(value) for key, value in params.items()}}

    task_id = refs.get(_REF_TASK)
    if task_id:
        task = await task_getter(str(task_id))
        if task is None:
            raise RefUnresolved(f"task {task_id} not found")
        values[_REF_TASK] = {field: read(task) for field, read in _TASK_FIELDS.items()}

    member_name = refs.get(_REF_MEMBER)
    if member_name:
        member = await member_getter(str(member_name))
        if member is None:
            raise RefUnresolved(f"member {member_name} not found")
        values[_REF_MEMBER] = {field: read(member) for field, read in _MEMBER_FIELDS.items()}

    return values


def _substitute(template: str, values: dict[str, dict[str, str]]) -> str:
    """Replace every ``{{ns.field}}`` in one pass (values are never rescanned)."""

    def _replace(match: re.Match[str]) -> str:
        namespace, field = match.group(1), match.group(2)
        resolved = values.get(namespace, {}).get(field)
        if resolved is None:
            return f"<missing:{namespace}.{field}>"
        return resolved

    return _PLACEHOLDER_RE.sub(_replace, template)


__all__ = [
    "ExpandedMessage",
    "MemberGetter",
    "RefUnresolved",
    "TaskGetter",
    "build_meta",
    "expand_message",
    "fallback_line",
    "parse_meta",
]
