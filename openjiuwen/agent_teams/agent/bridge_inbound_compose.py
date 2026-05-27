# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compose the bridge avatar's DeepAgent input after auto-forwarding.

The coordination message handler calls :func:`compose_bridge_inbound`
after relaying an inbound team message to the remote (or after that
relay is short-circuited to the sentinel because no adapter is
registered). The composed string is delivered into the bridge
avatar's DeepAgent context as-if the avatar received the message
directly — except it carries both the original body AND the remote's
execution output.

The composed template makes the scheduling contract explicit to the
bridge LLM:

* The remote produced the work content — pass it through verbatim.
* Bridge LLM only schedules (claim_task / member_complete_task /
  send_message timing / addressee). Do not rewrite or synthesize.
* The original team message has already been auto-forwarded; do not
  ``send_message`` it elsewhere thinking it needs to be relayed.

Pure function — no I/O, no LLM call — exactly predictable and
unit-testable.
"""

from __future__ import annotations

__all__ = ["compose_bridge_inbound"]


def compose_bridge_inbound(
    *,
    original_sender: str,
    original_body: str,
    remote_reply: str,
    language: str = "cn",
    time_info: str | None = None,
) -> str:
    """Build the text injected into the bridge avatar's context.

    Args:
        original_sender: ``member_name`` of the team member that sent
            the inbound message (or ``"user"``).
        original_body: Raw body of the inbound team message.
        remote_reply: Text returned by the remote agent for this turn.
            Pass the ``REMOTE_UNAVAILABLE_SENTINEL`` constant when no
            adapter is registered so the bridge LLM can react to the
            degradation.
        language: ``"cn"`` (default) or ``"en"``.
        time_info: Pre-rendered ``<absolute local time> (<relative
            diff>)`` of the inbound message's send time, or ``None`` to
            omit it. Lets the bridge avatar gauge how delayed the
            message is when it schedules the relay. Passed as a ready
            string so this stays a zero-dependency pure function.

    Returns:
        Composed text suitable for ``agent.deliver_input``.
    """
    if language == "en":
        header = f"[Team message from {original_sender}]"
        if time_info:
            header = f"[Team message from {original_sender} · {time_info}]"
        return (
            f"{header}\n"
            f"{original_body}\n\n"
            f"[Remote executor's output — relay this verbatim back to the team]\n"
            f"{remote_reply}\n\n"
            f"Your job: schedule only. Decide whether to send_message "
            f"the remote output above back to {original_sender} verbatim, "
            f"whether to call claim_task / member_complete_task, or "
            f"whether to stay silent. Do NOT rewrite or synthesize the "
            f"remote output — pass it through as-is. The original "
            f"message has already been forwarded to the remote; do NOT "
            f"call send_message to forward it again."
        )
    header = f"[来自团队成员 {original_sender} 的消息]"
    if time_info:
        header = f"[来自团队成员 {original_sender} 的消息 · {time_info}]"
    return (
        f"{header}\n"
        f"{original_body}\n\n"
        f"[外部执行者的执行结果（要原样回传给团队的内容）]\n"
        f"{remote_reply}\n\n"
        f"你的工作：仅做调度。决定是否使用 send_message 把上述执行结果"
        f"原样回传给 {original_sender}，是否需要调用 claim_task / "
        f"member_complete_task 等任务管理工具，或保持沉默。"
        f"**不要改写或综合**执行结果的内容——原样转发即可。"
        f"注意：原消息已自动转发给外部执行者，无需再调用 send_message 转发原消息。"
    )
