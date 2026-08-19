# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Top-layer parser for ``interact(str, ...)`` user input.

The runtime keeps mention/channel parsing in exactly one place — here.
Inboxes and the ``_dispatch_payload`` switch consume already-typed
:class:`InteractPayload` values; they never re-parse the body.

Grammar
-------

::

    input := channel? recipients? body
    channel := "# " | "$" name (" " | "@")  # default "# " when absent
    recipients := ("@" name " ")*           # zero or more
    body := <remaining text after consuming channel + recipients>

* ``# body`` — god-view; body lands on the leader's DeepAgent.
* ``$<name> body`` — drive ``<name>``'s human-agent avatar.
* ``@<member> body`` — point-to-point bus traffic to ``<member>`` from
  the channel sender (``user`` for ``#``, ``<name>`` for ``$``).
* ``@all body`` / ``@* body`` — broadcast on the channel.
* ``@<m1> @<m2> body`` — multi-cast: one bus message per recipient.
* ``<bare body>`` — defaults to ``# <bare body>``.

Whitespace separates every prefix from its body — ``#hashtag`` and
``$variable`` are content, not channel markers, because they lack the
trailing space the grammar requires.
"""

from __future__ import annotations

import re
from typing import (
    TYPE_CHECKING,
    Awaitable,
    Callable,
)

from openjiuwen.agent_teams.constants import (
    RESERVED_MEMBER_NAMES,
    USER_PSEUDO_MEMBER_NAME,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.interaction.payload import (
        DeliverResult,
        InteractPayload,
    )
    from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager

_MENTION_RE = re.compile(r"^@(\S+)\s+([\s\S]+)$")
_GOD_VIEW_PREFIX = "# "
_HUMAN_AGENT_PREFIX_RE = re.compile(r"^\$([^\s@]+)(?:\s+|(?=@))([\s\S]*)$")
_RECIPIENT_RE = re.compile(r"^@(\S+)\s+")
_INLINE_RECIPIENT_RE = re.compile(r"(?<!\S)@(\S+)\s+")

BROADCAST_TARGETS: frozenset[str] = frozenset({"all", "*"})
"""Reserved mention targets that mean "team-wide broadcast"."""


def parse_mention(content: str) -> tuple[str, str] | None:
    """Parse a single ``@target body`` from raw text.

    Returns ``(target, body)`` on a match; ``None`` when the input has
    no mention prefix. Kept as a low-level primitive — the
    interact-level grammar uses :func:`parse_interact_str` instead so
    it can capture multiple recipients.
    """
    if not content:
        return None
    match = _MENTION_RE.match(content)
    if match is None:
        return None
    target, body = match.group(1), match.group(2)
    return target, body


def is_reserved_name(name: str) -> bool:
    """Whether ``name`` collides with a runtime-reserved member name.

    Reserved names ("user", "team_leader", "human_agent") are owned by
    the runtime and must not be reused by user-declared members.
    """
    return name in RESERVED_MEMBER_NAMES


def _split_inline_segments(
    recipients: list[str],
    body: str,
) -> list[tuple[str, str]] | None:
    """Split ``@m1 body1 @m2 body2`` into per-recipient body segments.

    A body that starts with another recipient token is the established
    multicast form (``@m1 @m2 shared body``), so it stays on the older
    fan-out path.
    """
    if not recipients:
        return None

    matches = list(_INLINE_RECIPIENT_RE.finditer(body))
    if not matches:
        return None

    first_body = body[: matches[0].start()].rstrip()
    if not first_body:
        return None

    segments: list[tuple[str, str]] = [(recipients[0], first_body)]
    for index, match in enumerate(matches):
        target = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        segments.append((target, body[start:end].strip()))
    return segments


def parse_interact_str(body: str) -> list["InteractPayload"]:
    """Translate a free-form ``interact(str, ...)`` body into typed payloads.

    Produces an empty list only for an empty / whitespace-only input.
    For all other inputs returns a non-empty list:

    * Single :class:`GodViewMessage` — channel ``#`` with no
      ``@<member>`` recipients (also the fallback for unparseable
      inputs and for plain text without any prefix).
    * Single :class:`HumanAgentMessage` (``target=None``) — channel
      ``$<name>`` with no recipients; drives that avatar's DeepAgent.
    * Single :class:`OperatorMessage(target=None)` — channel ``#`` with
      a broadcast token (``@all`` / ``@*``); other recipients listed
      alongside the broadcast token are ignored because broadcast
      already reaches everyone.
    * Single :class:`HumanAgentMessage(target="*")` — channel
      ``$<name>`` with a broadcast token; broadcast as that human
      agent.
    * One :class:`OperatorMessage` per recipient — channel ``#`` with
      one or more named recipients.
    * One :class:`HumanAgentMessage` per recipient — channel
      ``$<name>`` with one or more named recipients (``sender`` set
      to the channel name on every entry).

    The fan-out cases preserve the order of recipients so callers can
    correlate per-target results. Validation of recipient existence
    happens at dispatch time (``HumanAgentInbox`` for ``$``;
    ``UserInbox`` / message bus for ``#``) — this function is pure
    syntax.
    """
    from openjiuwen.agent_teams.interaction.payload import (
        GodViewMessage,
        HumanAgentMessage,
        OperatorMessage,
    )

    if not body or not body.strip():
        return []

    rest = body
    sender = USER_PSEUDO_MEMBER_NAME
    is_human_agent = False

    # ---- channel prefix ----------------------------------------------
    if rest.startswith(_GOD_VIEW_PREFIX):
        rest = rest[len(_GOD_VIEW_PREFIX):].lstrip()
    else:
        match = _HUMAN_AGENT_PREFIX_RE.match(rest)
        if match is not None:
            sender = match.group(1)
            rest = match.group(2).lstrip()
            is_human_agent = True
        # else: no recognised prefix → treat as ``# `` default;
        # ``rest`` keeps the full original body.

    # ---- recipients --------------------------------------------------
    recipients: list[str] = []
    while True:
        match = _RECIPIENT_RE.match(rest)
        if match is None:
            break
        recipients.append(match.group(1))
        rest = rest[match.end():]

    final_body = rest

    # ---- payload synthesis -------------------------------------------
    if not recipients:
        if is_human_agent:
            return [HumanAgentMessage(body=final_body, sender=sender)]
        return [GodViewMessage(body=final_body)]

    has_broadcast = any(r in BROADCAST_TARGETS for r in recipients)
    if has_broadcast:
        # Broadcast supersedes any other listed recipient — broadcast
        # already covers every member, including those named explicitly.
        if is_human_agent:
            return [HumanAgentMessage(body=final_body, sender=sender, target="*")]
        return [OperatorMessage(body=final_body)]

    inline_segments = _split_inline_segments(recipients, final_body)
    if inline_segments is not None:
        if is_human_agent:
            return [
                HumanAgentMessage(body=segment_body, sender=sender, target=name)
                for name, segment_body in inline_segments
            ]
        return [
            OperatorMessage(body=segment_body, target=name)
            for name, segment_body in inline_segments
        ]

    if is_human_agent:
        return [
            HumanAgentMessage(body=final_body, sender=sender, target=name)
            for name in recipients
        ]
    return [OperatorMessage(body=final_body, target=name) for name in recipients]


MemberExistsCheck = Callable[[str], Awaitable[bool]]
"""Async predicate: does ``name`` refer to a real roster member?

Each caller decides where the lookup goes — typically a closure over
``TeamBackend.get_member`` (or any equivalent that maps a name to a
roster row).
"""


def _named_target(payload: "InteractPayload") -> str | None:
    """Return the point-to-point recipient carried by ``payload``.

    ``None`` for channels that address no single roster member —
    god-view, avatar-drive, and broadcast (``@all`` / ``@*``). Those
    never hit the roster and pass through :func:`resolve_targets`
    untouched.
    """
    from openjiuwen.agent_teams.interaction.payload import (
        HumanAgentMessage,
        OperatorMessage,
    )

    if not isinstance(payload, (OperatorMessage, HumanAgentMessage)):
        return None
    target = payload.target
    if target is None or target in BROADCAST_TARGETS:
        return None
    return target


def _fold_unknown_mentions(unknown: list["InteractPayload"]) -> "InteractPayload":
    """Fold unknown ``@<member>`` payloads back into one no-mention message.

    An ``@<member>`` that matches no roster row is not a routing
    directive — it is plain text the user typed. We re-attach those
    mentions to the shared body and route the result to the channel's
    default audience: the leader's DeepAgent for the operator channel,
    or the speaking avatar for the human-agent channel. The original
    text (``@ghost body``) is preserved verbatim.
    """
    from openjiuwen.agent_teams.interaction.payload import (
        GodViewMessage,
        HumanAgentMessage,
    )

    sample = unknown[0]
    mentions = " ".join(f"@{p.target}" for p in unknown)
    general_body = f"{mentions} {sample.body}" if sample.body else mentions
    if isinstance(sample, HumanAgentMessage):
        return HumanAgentMessage(body=general_body, sender=sample.sender)
    return GodViewMessage(body=general_body)


async def resolve_targets(
    payloads: list["InteractPayload"],
    *,
    member_exists: MemberExistsCheck,
) -> list["InteractPayload"]:
    """Strict-match ``@<member>`` recipients against the live roster.

    :func:`parse_interact_str` is pure syntax — it fans every
    ``@<member>`` token out into a targeted payload without knowing the
    roster. This resolution step applies the roster:

    * Known recipients keep their point-to-point payload untouched.
    * Unknown recipients are not routing directives; their mentions are
      folded back into a single no-mention message (see
      :func:`_fold_unknown_mentions`) addressed to the channel default
      (leader for ``#``, avatar for ``$``), preserving the user's text.
    * God-view / avatar-drive / broadcast payloads carry no named
      target and pass through unchanged.

    When every recipient is known the input list is returned as-is.
    Otherwise the result is ``<known point-to-point payloads> +
    [<one folded no-mention payload>]``.
    """
    unknown: list["InteractPayload"] = []
    kept: list["InteractPayload"] = []
    for payload in payloads:
        name = _named_target(payload)
        if name is None or await member_exists(name):
            kept.append(payload)
        else:
            unknown.append(payload)

    if not unknown:
        return payloads
    return kept + [_fold_unknown_mentions(unknown)]


async def deliver_direct(
    body: str,
    *,
    sender: str,
    target: str,
    message_manager: "TeamMessageManager",
    member_exists: MemberExistsCheck,
) -> "DeliverResult":
    """Validate ``target`` and post a point-to-point bus message.

    Shared primitive for callers that already know the recipient
    (e.g. ``HumanAgentInbox`` once the dispatch layer hands it a
    typed payload). Unknown / failed targets surface as stable
    failure tokens (``unknown_member:<target>`` / ``send_failed:<target>``).
    """
    from openjiuwen.agent_teams.interaction.payload import DeliverResult

    if not await member_exists(target):
        return DeliverResult.failure(f"unknown_member:{target}")
    msg_id = await message_manager.send_message(
        content=body,
        to_member_name=target,
        from_member_name=sender,
    )
    if msg_id is None:
        return DeliverResult.failure(f"send_failed:{target}")
    return DeliverResult.success(msg_id)


__all__ = [
    "BROADCAST_TARGETS",
    "MemberExistsCheck",
    "deliver_direct",
    "is_reserved_name",
    "parse_interact_str",
    "parse_mention",
    "resolve_targets",
]
