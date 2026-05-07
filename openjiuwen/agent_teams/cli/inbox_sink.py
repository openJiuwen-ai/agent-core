# coding: utf-8
"""Render HumanAgentInboundEvent notifications into the CLI console.

``Runner.register_human_agent_inbound`` accepts an async callback that
the runtime fires for every team-side message addressed to a registered
human-agent member. The CLI delegates that callback to
:func:`make_inbox_callback` which formats the event into a rich-styled
line and prints it through the same console used by the stream renderer
— prompt_toolkit's ``patch_stdout`` keeps the input area intact.
"""

from __future__ import annotations

from typing import (
    Awaitable,
    Callable,
)

from rich.console import Console

from openjiuwen.agent_teams.interaction import HumanAgentInboundEvent


def make_inbox_callback(console: Console) -> Callable[[HumanAgentInboundEvent], Awaitable[None]]:
    """Build an async ``on_inbound`` callback that prints to ``console``.

    The returned coroutine is what gets passed to
    ``Runner.register_human_agent_inbound``. It renders one line per
    event with kind (``broadcast`` / ``direct``), sender, recipient,
    and a hint reminding the user how to reply through the standard
    text prompt routing.
    """

    async def _callback(event: HumanAgentInboundEvent) -> None:
        kind = "broadcast" if event.broadcast else "direct"
        body = event.body.replace("\n", " ").strip()
        console.print(
            f"[bold yellow][inbox/{event.member_name}][/bold yellow] [dim]{kind} from <{event.sender}>[/dim] {body}",
        )
        console.print(
            f"  [dim italic]reply with: ${event.member_name} @{event.sender} <body>"
            f" or @{event.sender} <body>[/dim italic]",
        )

    return _callback


__all__ = ["make_inbox_callback"]
