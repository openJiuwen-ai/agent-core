# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-global i18n for agent team runtime strings.

Houses hard-coded user-facing strings that live inside runtime code paths
(dispatcher nudges, backend message content, default persona) so they can
be switched between Chinese and English without source edits.

Modules that already carry their own bilingual dictionaries
(``agent/team_rail.py``, ``agent/policy.py``) or Markdown-backed
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
            "[催促] 你已认领的任务 [{task_id}] {title} 已超过 10 mins 仍未完成，请继续推进：{content}"
        ),
        # agent/dispatcher.py — message formatting
        "dispatcher.msg_type_broadcast": "广播消息",
        "dispatcher.msg_type_direct": "单播消息",
        "dispatcher.msg_received": (
            "[收到{msg_type}] message_id={message_id}, "
            "来自: {sender}\n"
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
            "团队中的人类协作者。与 leader、teammate 地位平等；由真实操作者驱动，可接收任务、回复消息、参与协作。"
        ),
        "hitt.human_agent_spawned": "[成员事件] 人类成员 human_agent 已加入团队",
    },
    "en": {
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
            "[Nudge] Your claimed task [{task_id}] {title} has been open for over 10 mins. Please continue: {content}"
        ),
        # agent/dispatcher.py — message formatting
        "dispatcher.msg_type_broadcast": "broadcast",
        "dispatcher.msg_type_direct": "direct message",
        "dispatcher.msg_received": (
            "[Received {msg_type}] message_id={message_id}, "
            "from: {sender}\n"
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
            "The human collaborator on the team. Equal in standing with "
            "the leader and teammates; driven by a real operator, can "
            "receive tasks, reply to messages, and collaborate."
        ),
        "hitt.human_agent_spawned": "[Member Event] Human member 'human_agent' joined the team",
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
