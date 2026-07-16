# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-global i18n for agent team runtime strings.

Houses hard-coded user-facing strings that live inside runtime code paths
(dispatcher nudges, backend message content, default desc) so they can
be switched between Chinese and English without source edits.

Modules that already carry their own bilingual dictionaries
(``prompts/sections.py``) or Markdown-backed
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
        "blueprint.default_desc": "天才项目管理专家",
        # tools/team.py
        "team.shutdown_request_content": "当前任务已全部完成，请结束流程",
        "team.cancel_request_content": "当前任务有变动，请停止执行当前任务，重新尝试认领合适任务",
        # reliability/ — anomaly remediation messages
        "reliability.steer_self_correct": (
            "⚙️[可靠性] 检测到 {kind}：{summary}。请停止重复无效操作，改换策略或换用其他工具。"
        ),
        "reliability.report_leader": (
            "[可靠性告警] {summary}。请评估该成员状态并决定处理方式"
            "（发消息提醒 / 取消任务 / 停止成员 / 新建成员或任务）。"
        ),
        "reliability.escalate_user": (
            "[可靠性·严重] {summary}。已超出自动处理范围，建议立即上报控制者 / 用户决策。"
        ),
        # agent/dispatcher.py — member lifecycle events
        "dispatcher.member_online": "[成员事件] 成员 {target_id} 已上线",
        "dispatcher.member_restarted": "[成员事件] 成员 {target_id} 已重启 (第{restart_count}次)",
        "dispatcher.member_status_changed": "[成员事件] 成员 {target_id} 状态变更: {old_status} → {new_status}",
        "dispatcher.member_execution_changed": "[成员事件] 成员 {target_id} 执行状态变更: {old_status} → {new_status}",
        "dispatcher.member_shutdown": "[成员事件] 成员 {target_id} 已关闭",
        "dispatcher.member_canceled": "[成员事件] 成员 {target_id} 已取消",
        # agent/dispatcher.py — stale-claim nudges
        "dispatcher.stale_claim_self": (
            "[催促] 你已认领的任务 [{task_id}] {title}（认领于 {time_info}）仍未完成。"
            "如需回顾详情请用 view_task；请继续推进，完成后用 claim_task(status='completed') 标记完成。"
        ),
        # agent/coordination/handlers/stale_task.py — idle-clock stall nudges (autonomous, F_65)
        "dispatcher.stale_idle_claim_self": (
            "[催促] 你持有任务 [{task_id}] {title}，但已空闲 {minutes} 分钟未推进。"
            "如需回顾详情请用 view_task；请继续推进，完成后用 claim_task(status='completed') 标记完成。"
        ),
        "dispatcher.stale_idle_claim_escalate": (
            "[停滞上报] 我持有任务 [{task_id}] {title}，但已连续空闲 {minutes} 分钟未推进"
            "（多次自我催促无效）。请评估是否需要问询、改派或更换成员。"
        ),
        # agent/dispatcher.py — task assignment notification
        "dispatcher.task_assigned_to_self": (
            "[任务指派] 任务 [{task_id}] 已指派给你，请通过 view_task 工具查看任务详情并执行。"
        ),
        # agent/coordination/handlers/task_board.py — task reassigned away from this member
        "dispatcher.task_revoked_from_self": (
            "[任务撤回] 任务 [{task_id}] 已被转交给其他成员。请立即停止该任务的工作，"
            "并通过 view_task 查看是否有新的可认领任务。"
        ),
        "dispatcher.task_cancelled_to_self": (
            "[任务取消] 你正在执行的任务 [{task_id}] 已被取消。请立即停止该任务的工作，"
            "并通过 view_task 查看是否有新的可认领任务。"
        ),
        "dispatcher.task_content_updated_to_self": (
            "[任务变更] 你正在执行的任务 [{task_id}] 的内容已被更新。请通过 view_task "
            "重新查看最新要求后继续执行（任务仍归你，无需重新认领）。"
        ),
        # agent/dispatcher.py — message formatting
        "dispatcher.task_plan_approved_to_self": (
            "[计划已批准] 任务 [{task_id}] 的执行计划已通过。请开始执行，完成后用 claim_task(status='completed') 标记完成。"
            "{feedback}"
        ),
        "dispatcher.task_plan_rejected_to_self": (
            "[计划需修改] 任务 [{task_id}] 的执行计划未通过。请根据反馈修改并重新调用 submit_plan。反馈：{feedback}"
        ),
        # agent/coordination/handlers/task_board.py — verify gate (F_59)
        "dispatcher.task_submitted_for_review_to_reviewer": (
            "[待验证] 任务 [{task_id}] 已由 {author} 提交验证，你是该任务的验证者。请通过 view_task(action=get) "
            "查看产出，然后用 verify_task(decision='pass'|'fail') 给出验证结论。"
        ),
        "dispatcher.task_revision_requested_to_self": (
            "[验证打回] 你的任务 [{task_id}] 未通过验证，已退回让你返工。请根据反馈修改后，用 "
            "member_complete_task / claim_task(status='completed') 重新提交。反馈：{feedback}"
        ),
        "dispatcher.task_verified_to_self": (
            "[验证通过] 你的任务 [{task_id}] 已通过验证并标记完成。请通过 view_task 查看是否有新的可认领任务。"
        ),
        # agent/scheduling/render.py — leader-side digests / escalations (F_62).
        # Member handoffs are NOT here: they are mailbox messages rendered at
        # delivery from prompts/<lang>/scheduler_*.md, the single source of
        # their wording (F_63). Leader digests bypass the mailbox (direct input
        # injection), so they have no meta channel and stay one-line i18n.
        "scheduler.leader_task_done": (
            "[调度器] 任务 [{task_id}]「{title}」已完成（{how}）。看板剩余未终结任务 {remaining} 个。"
        ),
        "scheduler.leader_task_done_how_verified": "验收通过",
        "scheduler.leader_task_done_how_direct": "无验证直接完成",
        "scheduler.leader_escalation_rounds": (
            "[调度器·需你处置] 任务 [{task_id}]「{title}」连续 {rounds} 轮验收未通过，"
            "已停止自动返工，任务停在 in_review。最近一轮验证反馈：\n{feedback}\n"
            "可选处置：update_task 调整承担者/验证者/任务内容（先 reset），"
            "update_task(status='cancelled') 取消，或增删成员后重新规划。"
        ),
        "scheduler.leader_escalation_stall": (
            "[调度器·需你处置] 任务 [{task_id}]「{title}」第 {round} 轮验收停摆超过 {minutes} 分钟："
            "已投票 {voted}；未投票 {pending}。任务停在 in_review。"
            "可用 send_message 催促验证者，或 update_task 调整验证者/处置任务。"
        ),
        "scheduler.leader_all_done": (
            "[调度器] 任务看板已全部终结（共 {count} 个任务）。请汇总团队执行结果，向用户交付最终结论。"
        ),
        "scheduler.none": "（无）",
        "dispatcher.msg_type_broadcast": "广播消息",
        "dispatcher.msg_type_direct": "单播消息",
        "dispatcher.msg_received": (
            "[收到{msg_type}] message_id={message_id}, "
            "来自: {sender}\n"
            "时间: {time_info}\n"
            "内容: {content}\n"
            "提示: 如果对方在提问或等待回复，请务必通过 send_message 工具回复 {sender}"
        ),
        # XML inbound track (inbound_render.py) — note bodies kept separate
        # from the legacy flat templates above so the original message and
        # the framework hint land in distinct <team-inbound> / <team-note>
        # tags. The legacy templates stay for the external/format.py path.
        "dispatcher.reply_hint": "如果对方在提问或等待回复，请务必通过 send_message 工具回复 {sender}。",
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
            "以下是当前可认领的任务：\n- 请认领适合你领域的任务\n- 认领后用 view_task 查看详情并开始执行"
        ),
        "dispatcher.task_unassigned_marker": " (待领取)",
        # agent/dispatcher.py — stale-pending leader self-prompt
        "dispatcher.stale_pending_header": (
            "[催促建议] 以下任务已长时间处于 pending 状态未被认领（如需回顾详情用 view_task）。"
            "请评估每个任务最适合哪位成员，并通过 send_message 工具点名"
            "对方让其使用 claim_task 认领："
        ),
        # HITT — reserved human_agent member
        "hitt.human_agent_display_name": "人类成员",
        "hitt.human_agent_default_desc": (
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
        # XML inbound track (inbound_render.py) — the HITT silence constraint
        # carried in a <team-note kind="hitt-silence">. The "strictly
        # forbidden" framing is load-bearing: without it the avatar LLM
        # drifts into autonomous replies on send_message. Kept equivalent to
        # the legacy hitt.* flat templates above, which the external path uses.
        "hitt.silence_note": (
            "**这是给控制者看的通知，不是要你执行的指令**，运行时已把它原样转给控制者。\n"
            "**严格禁止任何自主行为**：禁止主动回复发送方 / 指派方（包括调用 send_message）、"
            "禁止自主调用 member_complete_task / claim_task / 文件 / shell 等任何工具去回应或推进、"
            "禁止用纯文本输出表达意图或承诺。\n"
            "**保持静默**，只有控制者在 Inbox 里下达明确指令后才能行动。"
        ),
        "hitt.assigned_event": "你被指派了新任务 [{task_id}] {title}。",
        # agent/coordination/handlers/workflow.py — swarmflow spectator broadcast
        "workflow.started": "编排 [{run_id}]「{name}」已启动，我将在每个阶段向你汇报进展。",
        "workflow.phase": "编排 [{run_id}] 进入阶段：{phase}",
        "workflow.human_prompt": "正在等待人工回复 [{label}]：{prompt}（correlation_id={corr}）",
        "workflow.human_replied": "人工已回复 [{label}]，编排继续。",
        "swarmflow.launched": (
            "[Swarmflow 已启动] run_id={run_id}，task_id={task_id}，script_path={script_path}。"
            "并行工作流计数请只认 run_id，不要用 task_id 当作新的一局。"
            "重跑 / 迭代请用上面的 script_path（内联 script 已落盘到此绝对路径），无需重发整段源码。"
        ),
        "swarmflow.completed": "[Swarmflow 完成] run_id={run_id}\n{result}",
        "swarmflow.failed": "[Swarmflow 失败] run_id={run_id}，错误={error}",
        # harness/async_tools.py — async background-tool framework feedback
        "async_tool.launched": (
            "[后台任务] {tool} 已启动（task_id={task_id}）。完成后结果会自动回灌给你，"
            "无需轮询；你可以继续处理其他输入。"
        ),
        "async_tool.completed": "[后台任务完成] 工具={tool}\n{result}",
        "async_tool.failed": "[后台任务失败] 工具={tool}，错误={error}",
        "async_tool.spilled_notice": (
            "[完整输出过大，已写入磁盘 {path}。"
            "调用 async_task_output(task_id='{task_id}') 取回全文。]"
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
        "blueprint.default_desc": "Genius project management expert",
        # tools/team.py
        "team.shutdown_request_content": "All tasks are complete. Please wrap up and exit.",
        "team.cancel_request_content": (
            "The current task has changed. Stop executing it and try claiming a suitable task again."
        ),
        # reliability/ — anomaly remediation messages
        "reliability.steer_self_correct": (
            "[reliability] Detected {kind}: {summary}. Stop repeating the ineffective action; "
            "change strategy or use a different tool."
        ),
        "reliability.report_leader": (
            "[reliability alert] {summary}. Assess this member's state and decide how to handle it "
            "(send a reminder / cancel the task / stop the member / spawn a new member or task)."
        ),
        "reliability.escalate_user": (
            "[reliability critical] {summary}. Beyond automated handling; escalate to the "
            "controller/user for a decision now."
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
        "dispatcher.stale_claim_self": (
            "[Nudge] Your claimed task [{task_id}] {title} (claimed {time_info}) is still open. "
            "Use view_task to review the details; keep pushing it forward and call "
            "claim_task(status='completed') when done."
        ),
        # agent/coordination/handlers/stale_task.py — idle-clock stall nudges (autonomous, F_65)
        "dispatcher.stale_idle_claim_self": (
            "[Nudge] You hold task [{task_id}] {title} but have been idle for {minutes} minute(s) "
            "without progressing it. Use view_task to review the details; keep pushing it forward "
            "and call claim_task(status='completed') when done."
        ),
        "dispatcher.stale_idle_claim_escalate": (
            "[Stall report] I hold task [{task_id}] {title} but have been idle for {minutes} "
            "minute(s) without progressing it (repeated self-nudges did not help). Please consider "
            "checking in, reassigning, or replacing the assignee."
        ),
        # agent/dispatcher.py — task assignment notification
        "dispatcher.task_assigned_to_self": (
            "[Task Assigned] Task [{task_id}] has been assigned to you. "
            "Use view_task to inspect the details and start working on it."
        ),
        # agent/coordination/handlers/task_board.py — task reassigned away from this member
        "dispatcher.task_revoked_from_self": (
            "[Task Revoked] Task [{task_id}] has been reassigned to another member. "
            "Stop working on it now and call view_task to find your next available task."
        ),
        "dispatcher.task_cancelled_to_self": (
            "[Task Cancelled] Task [{task_id}] you were working on has been cancelled. "
            "Stop working on it now and call view_task to find your next available task."
        ),
        "dispatcher.task_content_updated_to_self": (
            "[Task Updated] The content of task [{task_id}] you are working on has changed. "
            "Call view_task to re-read the latest requirements, then continue (the task is "
            "still yours — no need to re-claim)."
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
        # agent/coordination/handlers/task_board.py — verify gate (F_59)
        "dispatcher.task_submitted_for_review_to_reviewer": (
            "[Awaiting Review] {author} submitted task [{task_id}] for verification and you are a "
            "reviewer. Inspect the deliverable via view_task(action=get), then call "
            "verify_task(decision='pass'|'fail') with your verdict."
        ),
        "dispatcher.task_revision_requested_to_self": (
            "[Revision Requested] Your task [{task_id}] failed verification and was sent back for "
            "rework. Revise per the feedback and resubmit via member_complete_task / "
            "claim_task(status='completed'). Feedback: {feedback}"
        ),
        "dispatcher.task_verified_to_self": (
            "[Verified] Your task [{task_id}] passed verification and is now completed. "
            "Call view_task to find your next available task."
        ),
        # agent/scheduling/render.py — leader-side digests / escalations (F_62).
        # Member handoffs are NOT here: they are mailbox messages rendered at
        # delivery from prompts/<lang>/scheduler_*.md, the single source of
        # their wording (F_63). Leader digests bypass the mailbox (direct input
        # injection), so they have no meta channel and stay one-line i18n.
        "scheduler.leader_task_done": (
            "[Scheduler] Task [{task_id}] \"{title}\" completed ({how}). {remaining} unfinished "
            "task(s) remain on the board."
        ),
        "scheduler.leader_task_done_how_verified": "review passed",
        "scheduler.leader_task_done_how_direct": "no review, completed directly",
        "scheduler.leader_escalation_rounds": (
            "[Scheduler · Action Needed] Task [{task_id}] \"{title}\" failed {rounds} review "
            "round(s) in a row; automatic rework stopped and the task stays in_review. Latest "
            "round feedback:\n{feedback}\n"
            "Options: update_task to adjust assignee/reviewers/content (reset first), "
            "update_task(status='cancelled') to cancel, or reshape the roster and re-plan."
        ),
        "scheduler.leader_escalation_stall": (
            "[Scheduler · Action Needed] Task [{task_id}] \"{title}\" review round {round} has "
            "stalled for over {minutes} minute(s): voted {voted}; pending {pending}. The task "
            "stays in_review. Nudge the reviewers via send_message, or adjust reviewers / settle "
            "the task via update_task."
        ),
        "scheduler.leader_all_done": (
            "[Scheduler] Every task on the board is terminal ({count} task(s) total). Summarize "
            "the team's results and deliver the final conclusion to the user."
        ),
        "scheduler.none": "(none)",
        "dispatcher.msg_type_broadcast": "broadcast",
        "dispatcher.msg_type_direct": "direct message",
        "dispatcher.msg_received": (
            "[Received {msg_type}] message_id={message_id}, "
            "from: {sender}\n"
            "time: {time_info}\n"
            "content: {content}\n"
            "tip: If the sender is asking or waiting for a reply, make sure to reply to {sender} via send_message"
        ),
        # XML inbound track (inbound_render.py) — see the cn note above.
        "dispatcher.reply_hint": (
            "If the sender is asking or waiting for a reply, be sure to reply to {sender} via send_message."
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
            "Tasks available to claim:\n"
            "- Claim the ones that fit your domain\n"
            "- After claiming, use view_task for details and start working"
        ),
        "dispatcher.task_unassigned_marker": " (unassigned)",
        # agent/dispatcher.py — stale-pending leader self-prompt
        "dispatcher.stale_pending_header": (
            "[Nudge suggestion] The following tasks have been pending unclaimed for a long time "
            "(use view_task to review the details). "
            "Decide which member fits each task best, then use send_message to call them out "
            "and ask them to claim via claim_task:"
        ),
        # HITT — reserved human_agent member
        "hitt.human_agent_display_name": "Human Member",
        "hitt.human_agent_default_desc": (
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
        # XML inbound track (inbound_render.py) — see the cn note above. The
        # "strictly forbidden" framing is load-bearing.
        "hitt.silence_note": (
            "**This is a notification for your controller, NOT an instruction "
            "for you to act on**; the runtime has already surfaced it to the "
            "controller as-is.\n"
            "**Autonomous behavior is strictly forbidden**: do not reply to "
            "the sender / assigner (including via send_message), do not "
            "autonomously call member_complete_task / claim_task / file tools "
            "/ shell tools or any other tool to respond or push work forward, "
            "and do not emit plain-text intent or promises.\n"
            "**Stay silent** and act only after the controller issues an "
            "explicit instruction via the Inbox."
        ),
        "hitt.assigned_event": 'You have been assigned task [{task_id}] "{title}".',
        # agent/coordination/handlers/workflow.py — swarmflow spectator broadcast
        "workflow.started": (
            "Orchestration [{run_id}] '{name}' has started; I will "
            "report progress to you at each phase."
        ),
        "workflow.phase": "Orchestration [{run_id}] entering phase: {phase}",
        "workflow.human_prompt": "Awaiting a human reply [{label}]: {prompt} (correlation_id={corr})",
        "workflow.human_replied": "The human replied [{label}]; orchestration continues.",
        "swarmflow.launched": (
            "[Swarmflow launched] run_id={run_id}, task_id={task_id}, script_path={script_path}. "
            "Count parallel workflows by run_id only — do not treat task_id as a new run. "
            "To re-run / iterate, pass the script_path above (an inline script has been written to this "
            "absolute path) — no need to resend the source."
        ),
        "swarmflow.completed": "[Swarmflow completed] run_id={run_id}\n{result}",
        "swarmflow.failed": "[Swarmflow failed] run_id={run_id}, error={error}",
        # harness/async_tools.py — async background-tool framework feedback
        "async_tool.launched": (
            "[Background task] {tool} started (task_id={task_id}). The result will be "
            "fed back to you automatically on completion — do not poll; you may "
            "continue handling other input."
        ),
        "async_tool.completed": "[Background task completed] tool={tool}\n{result}",
        "async_tool.failed": "[Background task failed] tool={tool}, error={error}",
        "async_tool.spilled_notice": (
            "[Full output was large and written to disk at {path}. "
            "Call async_task_output(task_id='{task_id}') to retrieve it.]"
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
