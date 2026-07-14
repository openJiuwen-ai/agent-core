# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-global i18n for agent team runtime strings.

Houses hard-coded user-facing strings that live inside runtime code paths
(dispatcher nudges, backend message content, default persona) so they can
be switched between Chinese and English without source edits.

Modules that already carry their own bilingual dictionaries
(``prompts/sections.py``, ``prompts/policy.py``) or Markdown-backed
descriptions (``tools/locales/``) are intentionally NOT routed through
this module — they accept a ``language`` argument at call time and
should continue to do so.

Usage:

    from openjiuwen.agent_teams.i18n import set_language, t

    set_language("en")
    msg = t("dispatcher.member_online", target_id="dev-1")
"""

from __future__ import annotations

from typing import Literal

Language = Literal["cn", "en"]

_DEFAULT_LANGUAGE: Language = "cn"
_current_language: Language = _DEFAULT_LANGUAGE


STRINGS: dict[str, dict[str, str]] = {
    "cn": {
        # timefmt.py — relative-time buckets ({value} is the bucket count)
        "time.just_now": "刚刚",
        "time.seconds_ago": "{value} 秒前",
        "time.minutes_ago": "{value} 分钟前",
        "time.hours_ago": "{value} 小时前",
        "time.days_ago": "{value} 天前",
        "time.unknown": "时间未知",
        # schema/blueprint.py
        "blueprint.default_persona": "天才项目管理专家",
        # tools/team.py
        "team.shutdown_request_content": "当前任务已全部完成，请结束流程",
        "team.cancel_request_content": "当前任务有变动，请停止执行当前任务，重新尝试认领合适任务",
        # agent/dispatcher.py — member lifecycle events
        "dispatcher.member_online": "[成员事件] 成员 {target_id} 已上线",
        "dispatcher.member_restarted": "[成员事件] 成员 {target_id} 已重启 (第{restart_count}次)",
        "dispatcher.member_status_changed": "[成员事件] 成员 {target_id} 状态变更: {old_status} → {new_status}",
        "dispatcher.member_execution_changed": "[成员事件] 成员 {target_id} 执行状态变更: {old_status} → {new_status}",
        "dispatcher.member_shutdown": "[成员事件] 成员 {target_id} 已关闭",
        "dispatcher.member_canceled": "[成员事件] 成员 {target_id} 已取消",
        # agent/dispatcher.py — stale-claim nudges
        "dispatcher.stale_claim_header": "检测到你已认领且超过 10 分钟未完成的任务（共 {count} 个），请继续推进：",
        "dispatcher.stale_claim_self": (
            "[催促] 你已认领的任务 [{task_id}] {title}（认领于 {time_info}）仍未完成，请继续推进：{content}"
        ),
        # agent/dispatcher.py — task assignment notification
        "dispatcher.task_assigned_to_self": (
            "[任务指派] 任务 [{task_id}] 已指派给你，请通过 view_task 工具查看任务详情并执行。"
        ),
        # agent/dispatcher.py — message formatting
        "dispatcher.task_plan_approved_to_self": (
            "[计划已批准] 任务 [{task_id}] 的执行计划已通过。请开始执行，完成后用 claim_task(status='completed') 标记完成。"
            "{feedback}"
        ),
        "dispatcher.task_plan_rejected_to_self": (
            "[计划需修改] 任务 [{task_id}] 的执行计划未通过。请根据反馈修改并重新调用 submit_plan。反馈：{feedback}"
        ),
        "dispatcher.msg_type_broadcast": "广播消息",
        "dispatcher.msg_type_direct": "单播消息",
        "dispatcher.msg_received": (
            "[收到{msg_type}] message_id={message_id}, "
            "来自: {sender}\n"
            "时间: {time_info}\n"
            "内容: {content}\n"
            "提示: 如果对方在提问或等待回复，请务必通过 send_message 工具回复 {sender}"
        ),
        # agent/dispatcher.py — idle-agent nudges
        "dispatcher.all_done_persistent": ("所有任务已完成。请汇总本轮工作成果。团队继续保持运行，等待新的任务指令。"),
        "dispatcher.all_done_temporary": (
            "所有任务已完成。请汇总团队工作成果，"
            "然后依次调用 shutdown_member 关闭所有成员，"
            "等待所有成员状态转为 shutdown 后，"
            "调用 clean_team 解散团队。"
        ),
        "dispatcher.leader_task_board": (
            "当前任务看板如下，请审查：\n"
            "- 是否需要调整任务（增删、修改、调整依赖）\n"
            "- 就绪任务是否需要指派给 teammate\n"
            "- 整体进度是否符合预期"
        ),
        "dispatcher.teammate_task_list": (
            "当前任务列表如下：\n- 请认领适合你领域的待领取任务\n- 了解相关任务的执行者，必要时与他们协调配合"
        ),
        "dispatcher.task_unassigned_marker": " (待领取)",
        # agent/dispatcher.py — stale-pending leader self-prompt
        "dispatcher.stale_pending_header": (
            "[催促建议] 以下任务已长时间处于 pending 状态未被认领，"
            "请评估每个任务最适合哪位成员，并通过 send_message 工具点名"
            "对方让其使用 claim_task 认领："
        ),
        # HITT — reserved human_agent member
        "hitt.human_agent_display_name": "人类成员",
        "hitt.human_agent_default_persona": (
            "外部用户在团队里的代理（avatar）。所有动作都由对应的真人通过 Inbox 驱动；"
            "可使用文件、任务、工作空间等工具替用户完成事务，但不主动发声、不自主认领任务。"
        ),
        "hitt.human_agent_spawned": "[成员事件] 人类成员 human_agent 已加入团队",
        # HITT — team events delivered to human_agent's harness. Different
        # wording from the teammate templates so the avatar LLM frames the
        # input as a notification for its controller (the real human who
        # operates this avatar via the Inbox), not as a self-execution prompt.
        # The "strictly forbidden" framing is load-bearing — without it the
        # avatar LLM tends to drift into autonomous replies on `send_message`
        # when it sees something that looks reply-shaped in its input.
        "hitt.task_assigned_to_self_human": (
            "[任务指派给控制者] 你被指派了新任务 [{task_id}] {title}。\n"
            "**这是给控制者看的通知，不是给你的工作指令**；"
            "运行时已经把通知原样展示给控制者。\n"
            "**严格禁止任何自主行为**：禁止主动回复发起指派的成员、"
            "禁止自主调用 send_message / member_complete_task / claim_task / "
            "文件 / shell 等任何工具去回应或推进任务、"
            "禁止用纯文本输出表达意图或承诺。\n"
            "**保持静默**，等控制者在 Inbox 里下达明确指令后再行动。"
        ),
        "hitt.msg_received_for_human": (
            "[转发给控制者的{msg_type}] message_id={message_id}, "
            "来自: {sender}\n"
            "时间: {time_info}\n"
            "内容: {content}\n"
            "**这条消息已经原样转给控制者，不是要你回应的指令**。\n"
            "**严格禁止任何自主行为**：禁止主动回复发送方（包括调用 send_message）、"
            "禁止自主调用任何其它工具去回应或采取行动、"
            "禁止用纯文本输出表达意图或承诺。\n"
            "**保持静默**，等控制者在 Inbox 里明确指示你转告或回复时再调 send_message。"
        ),
    },
    "en": {
        # timefmt.py — relative-time buckets ({value} is the bucket count)
        "time.just_now": "just now",
        "time.seconds_ago": "{value}s ago",
        "time.minutes_ago": "{value}m ago",
        "time.hours_ago": "{value}h ago",
        "time.days_ago": "{value}d ago",
        "time.unknown": "unknown time",
        # schema/blueprint.py
        "blueprint.default_persona": "Genius project management expert",
        # tools/team.py
        "team.shutdown_request_content": "All tasks are complete. Please wrap up and exit.",
        "team.cancel_request_content": (
            "The current task has changed. Stop executing it and try claiming a suitable task again."
        ),
        # agent/dispatcher.py — member lifecycle events
        "dispatcher.member_online": "[Member Event] Member {target_id} is online",
        "dispatcher.member_restarted": "[Member Event] Member {target_id} restarted (attempt {restart_count})",
        "dispatcher.member_status_changed": (
            "[Member Event] Member {target_id} status changed: {old_status} → {new_status}"
        ),
        "dispatcher.member_execution_changed": (
            "[Member Event] Member {target_id} execution status changed: {old_status} → {new_status}"
        ),
        "dispatcher.member_shutdown": "[Member Event] Member {target_id} has shut down",
        "dispatcher.member_canceled": "[Member Event] Member {target_id} has been canceled",
        # agent/dispatcher.py — stale-claim nudges
        "dispatcher.stale_claim_header": (
            "Detected {count} task(s) you claimed that have been open for over 10 minutes. Please push forward:"
        ),
        "dispatcher.stale_claim_self": (
            "[Nudge] Your claimed task [{task_id}] {title} (claimed {time_info}) is still open. "
            "Please continue: {content}"
        ),
        # agent/dispatcher.py — task assignment notification
        "dispatcher.task_assigned_to_self": (
            "[Task Assigned] Task [{task_id}] has been assigned to you. "
            "Use view_task to inspect the details and start working on it."
        ),
        # agent/dispatcher.py — message formatting
        "dispatcher.task_plan_approved_to_self": (
            "[Plan Approved] Your execution plan for task [{task_id}] was approved. "
            "Start execution and call claim_task(status='completed') when done. {feedback}"
        ),
        "dispatcher.task_plan_rejected_to_self": (
            "[Plan Rejected] Your execution plan for task [{task_id}] needs revision. "
            "Update it and call submit_plan again. Feedback: {feedback}"
        ),
        "dispatcher.msg_type_broadcast": "broadcast",
        "dispatcher.msg_type_direct": "direct message",
        "dispatcher.msg_received": (
            "[Received {msg_type}] message_id={message_id}, "
            "from: {sender}\n"
            "time: {time_info}\n"
            "content: {content}\n"
            "tip: If the sender is asking or waiting for a reply, make sure to reply to {sender} via send_message"
        ),
        # agent/dispatcher.py — idle-agent nudges
        "dispatcher.all_done_persistent": (
            "All tasks are complete. Please summarize this round's results. "
            "The team remains running and awaits new task instructions."
        ),
        "dispatcher.all_done_temporary": (
            "All tasks are complete. Summarize the team's work, "
            "then call shutdown_member for each member in turn, "
            "wait until all members reach status shutdown, "
            "and finally call clean_team to disband the team."
        ),
        "dispatcher.leader_task_board": (
            "Current task board — please review:\n"
            "- Whether any tasks need adjustment (add/remove/edit/dependencies)\n"
            "- Whether ready tasks should be assigned to a teammate\n"
            "- Whether the overall progress matches expectations"
        ),
        "dispatcher.teammate_task_list": (
            "Current task list:\n"
            "- Claim pending tasks that fit your domain\n"
            "- Know who is working on related tasks and coordinate when needed"
        ),
        "dispatcher.task_unassigned_marker": " (unassigned)",
        # agent/dispatcher.py — stale-pending leader self-prompt
        "dispatcher.stale_pending_header": (
            "[Nudge suggestion] The following tasks have been pending unclaimed for a long time. "
            "Decide which member fits each task best, then use send_message to call them out "
            "and ask them to claim via claim_task:"
        ),
        # HITT — reserved human_agent member
        "hitt.human_agent_display_name": "Human Member",
        "hitt.human_agent_default_persona": (
            "An external user's avatar on the team. Every action is "
            "driven by the corresponding human via the Inbox; uses file, "
            "task, and workspace tools to act on the user's behalf, but "
            "does not speak up on its own and does not autonomously "
            "claim tasks."
        ),
        "hitt.human_agent_spawned": "[Member Event] Human member 'human_agent' joined the team",
        # HITT — team events delivered to human_agent's harness. Wording is
        # distinct from the teammate templates so the avatar LLM frames the
        # input as a notification for its controller (the real human driving
        # this avatar via the Inbox), not as a self-execution prompt. The
        # "strictly forbidden" framing is load-bearing — without it the
        # avatar LLM tends to drift into autonomous replies on send_message
        # when it sees something that looks reply-shaped in its input.
        "hitt.task_assigned_to_self_human": (
            "[Task Assigned For Controller] You have been assigned task "
            '[{task_id}] "{title}".\n'
            "**This is a notification for your controller, NOT a work "
            "instruction for you**; the runtime has already surfaced the "
            "notification to the controller as-is.\n"
            "**Autonomous behavior is strictly forbidden**: do not reply "
            "to the assigner, do not autonomously call send_message / "
            "member_complete_task / claim_task / file tools / shell tools "
            "or any other tool to act on the assignment, and do not emit "
            "plain-text intent or promises.\n"
            "**Stay silent** and act only after the controller issues an "
            "explicit instruction via the Inbox."
        ),
        "hitt.msg_received_for_human": (
            "[For-Controller {msg_type}] message_id={message_id}, "
            "from: {sender}\n"
            "time: {time_info}\n"
            "content: {content}\n"
            "**This message has already been surfaced to your controller "
            "as-is; it is NOT an instruction for you to act on**.\n"
            "**Autonomous behavior is strictly forbidden**: do not reply "
            "to the sender (including via send_message), do not "
            "autonomously call any other tool to respond or take action, "
            "and do not emit plain-text intent or promises.\n"
            "**Stay silent** and only call send_message after the "
            "controller explicitly instructs you via the Inbox to relay "
            "or reply."
        ),
    },
}


def set_language(lang: Language) -> None:
    """Set the process-global language for runtime strings.

    Args:
        lang: Language code, one of ``"cn"`` or ``"en"``.

    Raises:
        ValueError: If ``lang`` is not a supported language.
    """
    if lang not in STRINGS:
        supported = ", ".join(sorted(STRINGS.keys()))
        raise ValueError(f"Unsupported language '{lang}'. Supported: {supported}")
    global _current_language
    _current_language = lang


def get_language() -> Language:
    """Return the current process-global language code."""
    return _current_language


def t(key: str, **kwargs: object) -> str:
    """Resolve a localized string for the current language.

    Args:
        key: Dotted lookup key (e.g. ``"dispatcher.member_online"``).
        **kwargs: Values interpolated via ``str.format_map``.

    Returns:
        The localized string for the current language.

    Raises:
        KeyError: If ``key`` is missing for the active language.
    """
    table = STRINGS[_current_language]
    if key not in table:
        raise KeyError(f"Missing i18n key '{key}' for language '{_current_language}'")
    raw = table[key]
    return raw.format_map(kwargs) if kwargs else raw


__all__ = ["Language", "STRINGS", "get_language", "set_language", "t"]
