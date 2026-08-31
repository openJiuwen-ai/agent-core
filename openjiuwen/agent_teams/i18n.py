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

from openjiuwen.agent_teams.constants import USER_PSEUDO_MEMBER_NAME

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
        "team.shutdown_human_active_tasks": (
            "人类成员 {member_name} 仍持有 {count} 个活跃任务 [{task_ids}]，不允许非强制关闭。"
            "请先通过 send_message 与成员协商是否同意强制关闭并取消任务。"
        ),
        # agent/fork.py — fork name mismatch surfaced to the leader
        "checkpoint.fork_not_found": (
            "[fork 警告] checkpoint '{fork}' 未能按 fork_source 解析{detail}，"
            "已回退为全量继承（成员 {member}）。可用 checkpoint：{available}。"
            "请用 list_checkpoints 核对名字后再 fork。"
        ),
        "checkpoint.fork_source_mismatch": (
            "（创建者 '{creator}' 与 fork_source '{source}' 不匹配）"
        ),
        # agent/coordination/handlers/checkpoint.py — leader announcement body/note
        "checkpoint.created_body": (
            "成员 {member} 创建了 checkpoint '{name}'（消息位置 {count}）{description}"
        ),
        "checkpoint.created_note": (
            "这是 checkpoint 创建公告，仅供你 fork 时核对名字使用，不需要回复。"
        ),
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
        # reliability/external_handler.py + message.py — external runtime
        "reliability.external_runtime_retrying": (
            "[三方运行时] 成员 {member_name}（{agent_kind}）正在自动重试：{category}。"
            "本轮不结束，成员状态不变，等待 SDK 后续结果。{summary}"
        ),
        "reliability.external_runtime_failed": (
            "[三方运行时·失败] 成员 {member_name}（{agent_kind}，阶段 {phase}）最终失败："
            "{category}。{summary} 原始错误：{reason_message} 建议处理：{suggested_action}。"
            "是否需要用户介入：{user_action_required}。请评估成员状态并决定是否继续调度。"
        ),
        "reliability.suggested_action.auth_required": "请登录 CLI 或配置有效的 API key",
        "reliability.suggested_action.quota_exceeded": "请检查账户额度或更换 API key",
        "reliability.suggested_action.rate_limited": "请稍后重试",
        "reliability.suggested_action.server_unavailable": "服务端暂时不可用，请稍后重试",
        "reliability.suggested_action.network_timeout": "请检查网络连接和 API 地址是否可达",
        "reliability.suggested_action.process_start_failed": "成员运行时启动失败，请检查配置或重试",
        "reliability.suggested_action.sdk_error": "运行时异常，请重试或检查日志",
        "reliability.suggested_action.unknown": "运行时异常，请重试或检查日志",
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
            "[催促] 任务 [{task_id}] {title} 归你负责（当前状态：{status}），"
            "但你已空闲 {minutes} 分钟未推进。如需回顾详情请用 view_task；"
            "尚未开始就用 claim_task(status='claimed') 认领开工，已在进行就继续推进，"
            "完成后用 claim_task(status='completed') 标记完成。"
        ),
        "dispatcher.stale_idle_claim_escalate": (
            "[停滞上报] 任务 [{task_id}] {title} 归我负责（当前状态：{status}），"
            "但我已连续空闲 {minutes} 分钟未推进（多次自我催促无效）。"
            "请评估是否需要问询、改派或更换成员。"
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
            "已停止自动返工，任务停在 in_review。已通知承担者向你发送返工总结（通过 inbox）。"
            "最近一轮验证反馈：\n{feedback}\n"
            "收到承担者的返工总结后，综合判断并决定下一步：retry（继续修复）、"
            "replan（调整承担者/验证者/需求）、或 rollback+replan（先回退产物再重分配）。"
        ),
        "scheduler.leader_escalation_stall": (
            "[调度器·需你处置] 任务 [{task_id}]「{title}」第 {round} 轮验收停摆超过 {minutes} 分钟："
            "已投票 {voted}；未投票 {pending}。任务停在 in_review。"
            "可用 send_message 催促验证者，或 update_task 调整验证者/处置任务。"
        ),
        "scheduler.leader_all_done": (
            "[调度器] 任务看板已全部终结（共 {count} 个任务）。请汇总团队执行结果，向用户交付最终结论。"
        ),
        "scheduler.inspector_avg_line": (
            "\n- [检视者平均分] {avg:.2f} / 0.85 ({status})\n"
        ),
        "scheduler.inspector_avg_pass": "达标",
        "scheduler.inspector_avg_fail": "未达标",
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
        # Sender-specific variant: the ``user`` pseudo-member is the human
        # outside the team, not a roster member. Unconditional by design —
        # the generic hint's "if the sender is asking" lets the model talk
        # itself out of replying, and a dropped user reply is invisible.
        "dispatcher.reply_hint_user": (
            "这条消息来自 user——委托本团队工作的**团队外部真人**，不是团队成员，不在成员名册里。"
            "你必须调用 send_message(to=\"user\") 把答复发回用户；"
            "你写在回复正文里的任何文字都不会送达用户，不调用工具就等于没有回复。"
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
            "以下是当前可处理的任务：\n"
            "- 未指派的 pending 任务可由你认领\n"
            "- 已指派给你的任务请先用 view_task 查看详情，再按任务工具推进"
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
        # team_context.py — note attached to every roster snapshot / delta.
        # Load-bearing: without it a member that sees "someone joined" fires off
        # a courtesy greeting, burning an LLM round plus a mailbox delivery on
        # both sides, and the greeting wakes the new member into doing the same.
        "team_context.roster_announcement_note": (
            "以上只是名册公告，不是给你的指令。"
            "**不要**因为看到成员变动就去和相关成员打招呼、确认或寒暄；"
            "只有当你手上的任务确实需要对方配合时才发消息。"
        ),
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
        "swarmflow.budget_exhausted": (
            "[Swarmflow 撞顶] run_id={run_id}\n"
            "{detail}\n"
            "触发层：{trigger_layer}（spent={spent}/{total}）。{workflow_contrast}"
            "消耗最高的 phase：{top_phases}。{guidance}"
        ),
        "swarmflow.budget_exhausted.workflow_guidance": (
            "该上限为本次工作流的单次额度（与会话总额独立），由用户设定不可改；"
            "必须重新设计工作流以降低 token 消耗（简化流程、减少 agent、更换更省 token 的模型等）后重跑。"
            "重跑方式：以上面的 run_id 作为 resume_id、连同改后的 script_path 一起传给 swarmflow——"
            "未改动的 agent 调用直接复用上次的结果缓存（按其记录的 token 重新计入，不重新调用模型），"
            "只有改动的部分重新计费，因此可原位修改脚本、保持 META name 不变，"
            "把改动的预期消耗压回额度内即可。请重新设计工作流后，"
            "以上面的 run_id 作为 resume_id 重试 swarmflow。"
        ),
        "swarmflow.budget_exhausted.session_guidance": (
            "该预算为团队共享总额（会话级），当前已耗尽。"
            "在未上调预算上限前，重启或新建工作流均会立即再次撞顶，"
            "请先上调预算上限并新开会话后再重试 swarmflow。"
        ),
        # tool_swarmflow.py — completed-but-over-budget feedback (rail force-finish)
        "swarmflow.budget_overrun": (
            "[Swarmflow 超预算完成] run_id={run_id}\n"
            "本次 run 已完成并交付结果，但消耗超出了用户设定的上限："
            "{trigger_layer}（spent={spent}/{total}）。{workflow_contrast}"
            "消耗最高的 phase：{top_phases}。\n"
            "{guidance}"
        ),
        "swarmflow.budget_overrun.workflow_guidance": (
            "该上限为本次工作流的单次额度（用户设定，不可改），本次 run 已违反。"
            "若需重跑，请重新设计工作流，把预期消耗压回该额度内"
            "（精简高消耗 phase、减少 agent 数、限制输出长度、更换更省 token 的模型）。"
            "重跑额度规则：同名重跑=额度全新重置（已终结的 run 不结转已花额度），"
            "且未改动的 agent 调用直接复用上次的结果缓存（不重新调用模型、不计 token），"
            "只有改动的部分重新计费——因此可直接原位修改脚本、保持 META name 不变重跑，"
            "把改动的预期消耗压回额度内即可；仅中断未终结的 run（崩溃/暂停/停止）"
            "才续算剩余额度（剩余额度=上限−上次已花）。"
            "改完后以相同 META name 再次调用 swarmflow。"
        ),
        "swarmflow.budget_overrun.session_guidance": (
            "该预算为团队共享总额（会话级），当前已超限。结果已照常交付，"
            "但在用户上调预算上限前，任何新的 swarmflow 调用都会立即撞顶——"
            "请先向用户说明超限情况并请求上调预算（或新开会话），不要盲目重跑。"
        ),
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
        "team.shutdown_human_active_tasks": (
            "Human agent {member_name} still holds {count} active task(s) [{task_ids}] "
            "and cannot be shut down without force. "
            "Use send_message to coordinate with the member on whether to force-shutdown and cancel the tasks."
        ),
        # agent/fork.py — fork name mismatch surfaced to the leader
        "checkpoint.fork_not_found": (
            "[fork warning] checkpoint '{fork}' could not be resolved against "
            "fork_source{detail}; fell back to full-context inheritance "
            "(member {member}). Available checkpoints: {available}. "
            "Use list_checkpoints to verify names before forking."
        ),
        "checkpoint.fork_source_mismatch": (
            " (creator '{creator}' does not match fork_source '{source}')"
        ),
        # agent/coordination/handlers/checkpoint.py — leader announcement body/note
        "checkpoint.created_body": (
            "Member {member} created checkpoint '{name}' (message position {count}){description}"
        ),
        "checkpoint.created_note": (
            "This is a checkpoint-created announcement for your fork coordination; "
            "no reply is needed."
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
        # reliability/external_handler.py + message.py — external runtime
        "reliability.external_runtime_retrying": (
            "[external runtime] Member {member_name} ({agent_kind}) is auto-retrying: {category}. "
            "The round stays open and member status is unchanged; awaiting the next SDK result. {summary}"
        ),
        "reliability.external_runtime_failed": (
            "[external runtime failed] Member {member_name} ({agent_kind}, phase {phase}) finally "
            "failed: {category}. {summary} Reason: {reason_message} Suggested action: {suggested_action}. "
            "User action required: {user_action_required}. Assess the member state and decide whether "
            "to keep scheduling it."
        ),
        "reliability.suggested_action.auth_required": "Log in to the CLI or configure a valid API key",
        "reliability.suggested_action.quota_exceeded": "Check account quota or switch to a different API key",
        "reliability.suggested_action.rate_limited": "Please retry later",
        "reliability.suggested_action.server_unavailable": "Server temporarily unavailable; please retry later",
        "reliability.suggested_action.network_timeout": "Check network connectivity and API endpoint reachability",
        "reliability.suggested_action.process_start_failed": (
            "Member runtime failed to start; check configuration or retry"
        ),
        "reliability.suggested_action.sdk_error": "Runtime error; retry or check logs",
        "reliability.suggested_action.unknown": "Runtime error; retry or check logs",
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
            "[Nudge] Task [{task_id}] {title} is yours (currently {status}) but you have been idle "
            "for {minutes} minute(s) without progressing it. Use view_task to review the details; "
            "call claim_task(status='claimed') to start it if it has not begun, otherwise keep "
            "pushing it forward, and call claim_task(status='completed') when done."
        ),
        "dispatcher.stale_idle_claim_escalate": (
            "[Stall report] Task [{task_id}] {title} is mine (currently {status}) but I have been "
            "idle for {minutes} minute(s) without progressing it (repeated self-nudges did not "
            "help). Please consider checking in, reassigning, or replacing the assignee."
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
            "round(s) in a row; automatic rework stopped and the task stays in_review. "
            "The assignee has been asked to send you a rework summary (via your inbox). "
            "Latest round feedback:\n{feedback}\n"
            "After receiving the assignee's summary, decide: retry, "
            "replan (reassign / adjust reviewers / change requirements), "
            "or rollback+replan (undo file changes before reassigning)."
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
        "scheduler.inspector_avg_line": (
            "\n- [Inspector avg] {avg:.2f} / 0.85 ({status})\n"
        ),
        "scheduler.inspector_avg_pass": "pass",
        "scheduler.inspector_avg_fail": "fail",
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
        # Sender-specific variant: see the cn note above.
        "dispatcher.reply_hint_user": (
            "This message is from user — the **human outside the team** who commissioned this team's work. "
            "They are not a team member and do not appear in the roster. "
            'You MUST call send_message(to="user") to deliver your reply; '
            "any text you write in your reply body never reaches the user, "
            "so not calling the tool means you did not reply at all."
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
            "Tasks available to work on:\n"
            "- Unassigned pending tasks may be claimed by you\n"
            "- For tasks already assigned to you, use view_task for details and proceed with the task tools"
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
        # team_context.py — see the cn note above; the "do NOT" framing is
        # load-bearing.
        "team_context.roster_announcement_note": (
            "The above is a roster announcement, not an instruction for you. "
            "Do **NOT** greet, check in with, or otherwise message a member just "
            "because the roster changed; message someone only when the work you "
            "are actually holding needs them."
        ),
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
        "swarmflow.budget_exhausted": (
            "[Swarmflow budget exhausted] run_id={run_id}\n"
            "{detail}\n"
            "Triggered layer: {trigger_layer} (spent={spent}/{total}). {workflow_contrast}"
            "Heaviest phases: {top_phases}. {guidance}"
        ),
        "swarmflow.budget_exhausted.workflow_guidance": (
            "This ceiling is the run's per-run token budget (independent of the session "
            "total), set by the user and must NOT be changed; you must redesign the "
            "workflow to consume fewer tokens (simplify / fewer agents / cheaper "
            "model) and relaunch. Relaunch by passing resume_id=<the run_id above> "
            "together with the edited script_path: unchanged agent calls replay from "
            "the prior run's result cache (re-billed from their stored tokens, no "
            "model call) and only the changed parts bill afresh, so edit the script "
            "in place, keep the META name, and fit the CHANGED parts' expected spend "
            "under the ceiling. Redesign and retry swarmflow with "
            "resume_id=<the run_id above>."
        ),
        "swarmflow.budget_exhausted.session_guidance": (
            "This is the team's shared (session-wide) token ceiling and is currently "
            "exhausted. Until the ceiling is raised, relaunching or starting a new "
            "workflow will hit the same gate immediately — raise the ceiling and open a "
            "new session before retrying swarmflow."
        ),
        # tool_swarmflow.py — completed-but-over-budget feedback (rail force-finish)
        "swarmflow.budget_overrun": (
            "[Swarmflow finished over budget] run_id={run_id}\n"
            "The run completed and delivered its result, but the spend passed the "
            "user-set ceiling: {trigger_layer} (spent={spent}/{total}). {workflow_contrast}"
            "Heaviest phases: {top_phases}.\n"
            "{guidance}"
        ),
        "swarmflow.budget_overrun.workflow_guidance": (
            "This ceiling is the run's per-run token budget (independent of the "
            "session total), set by the user and must NOT be changed — this run "
            "violated it. Before relaunching, redesign the workflow so its expected "
            "spend fits within it (trim the heaviest phases, fewer agents, shorter "
            "outputs, a cheaper model). Relaunch budget rules: relaunching under the "
            "SAME META name resets the ceiling (a terminal prior run carries no "
            "spent forward) and unchanged agent calls replay from the prior run's "
            "result cache (no model call, zero tokens) — only the changed parts are "
            "billed again, so edit the script in place, keep the META name, and fit "
            "the CHANGED parts' expected spend under the ceiling; only an "
            "interrupted (non-terminal) run continues its ledger on relaunch "
            "(remaining = ceiling − prior spent). Call swarmflow again under the "
            "same META name once redesigned."
        ),
        "swarmflow.budget_overrun.session_guidance": (
            "This is the team's shared (session-wide) token ceiling and it is now "
            "overrun. The result was delivered as usual, but until the user raises "
            "the ceiling, any new swarmflow call hits the same gate immediately — "
            "explain the overrun to the user and ask for a raise (or a new session) "
            "instead of blindly retrying."
        ),
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


def reply_hint_for(sender: str) -> str:
    """Resolve the ``reply-hint`` note body for one inbound sender.

    The ``user`` pseudo-member is not a roster member and has no agent
    process, so a member that answers it in plain text silently drops the
    reply. That case gets its own unconditional wording; every other
    sender keeps the generic, conditional hint.

    Args:
        sender: The sending member's ``member_name``.

    Returns:
        The localized note body for the active language.
    """
    if sender == USER_PSEUDO_MEMBER_NAME:
        return t("dispatcher.reply_hint_user")
    return t("dispatcher.reply_hint", sender=sender)


__all__ = ["Language", "STRINGS", "get_language", "reply_hint_for", "set_language", "t"]
