# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


"""PromptSection builders for the team policy rail.

Each function produces a single ``PromptSection`` covering one slice of
team-specific content (role, workflow, lifecycle, persona, ...). The
rail composes these sections into the shared ``SystemPromptBuilder``
alongside the harness sections (safety, tools, memory, ...).

Section layout (aligned with ``prompt_design.md``):

  P:11  team_role        — member id + role policy (always)
  P:12  team_hitt        — HITT collaboration rules. LEADER + HUMAN_AGENT
                          always get the full roster section (when human
                          members exist). TEAMMATE gets a role-neutral
                          anonymous section by default — no human_agent
                          ``member_name`` listed and no "real humans"
                          label — so peer role is not leaked into other
                          members' prompts. Setting
                          ``TeamAgentSpec.expose_human_agents_to_teammates=
                          True`` switches teammates to the legacy roster
                          section.
  P:13  team_workflow    — leader workflow (LEADER only)
  P:14  team_lifecycle   — team lifecycle policy (LEADER only)
  P:15  team_persona     — current persona (when persona is set)
  P:16  team_extra       — user-supplied base prompt (when set)
  P:65  team_info        — team metadata (after capabilities)
  P:66  team_members     — relationships with peers
"""

from __future__ import annotations

from typing import Any, Optional

from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.prompts.builder import PromptSection, SystemPromptBuilder

# ---------------------------------------------------------------------------
# Section name constants
# ---------------------------------------------------------------------------


class TeamSectionName:
    """Centralized section names owned by ``TeamPolicyRail``."""

    ROLE = "team_role"
    HITT = "team_hitt"
    BRIDGE = "team_bridge"
    WORKFLOW = "team_workflow"
    LIFECYCLE = "team_lifecycle"
    PERSONA = "team_persona"
    EXTRA = "team_extra"
    INFO = "team_info"
    MEMBERS = "team_members"


# ---------------------------------------------------------------------------
# Bilingual labels
# ---------------------------------------------------------------------------

_LABELS: dict[str, dict[str, str]] = {
    "cn": {
        "member_name_line": "你的 member_name",
        "role_heading": "# 团队角色",
        "workflow_heading": "# 工作流程",
        "lifecycle_heading": "# 团队生命周期",
        "persona_heading": "# 当前人设",
        "info_heading": "# 团队信息",
        "team_name_label": "team_name（团队唯一标识）",
        "display_name_label": "display_name（团队展示名）",
        "team_desc": "团队目标与指令",
        "team_workspace": "团队共享工作空间",
        "team_workspace_purpose": (
            "用于存放团队共享文件（方案、设计、交付成果），"
            "所有成员通过该路径前缀读写同一份文件，系统自动管理版本和文件锁"
        ),
        "team_workspace_abs": "绝对路径",
        "members_heading": "# 成员关系",
        "leader_mode_plan": (
            "团队成员执行模式: plan_mode（成员选择或接到任务后需直接通过 submit_plan 提交计划，"
            "由你通过 approve_plan 审批后才能执行）"
        ),
        "leader_mode_build": ("团队成员执行模式: build_mode（成员领取任务后自主执行并直接完成，无需你审批计划）"),
        "teammate_mode_plan": (
            "你的执行模式: plan_mode（选择或接到任务后必须先通过 submit_plan 提交计划，"
            "该工具会认领任务；"
            "等待 leader 通过 approve_plan 审批后才能开始执行）"
        ),
        "teammate_mode_build": ("你的执行模式: build_mode（领取任务后可自主执行并直接标记完成，无需 leader 审批计划）"),
    },
    "en": {
        "member_name_line": "Your member_name",
        "role_heading": "# Team Role",
        "workflow_heading": "# Workflow",
        "lifecycle_heading": "# Team Lifecycle",
        "persona_heading": "# Current Persona",
        "info_heading": "# Team Info",
        "team_name_label": "team_name (unique identifier)",
        "display_name_label": "display_name (human-readable label)",
        "team_desc": "Team Goal & Directives",
        "team_workspace": "Team Shared Workspace",
        "team_workspace_purpose": (
            "Holds team-shared files (plans, designs, deliverables); "
            "all members read/write the same files through this path prefix. "
            "Versioning and file locks are managed automatically"
        ),
        "team_workspace_abs": "Absolute path",
        "members_heading": "# Relationships",
        "leader_mode_plan": (
            "Teammate execution mode: plan_mode (teammates must submit a plan "
            "with submit_plan after selecting or receiving a task; "
            "that tool reserves the task, then teammates wait for your exact plan_id approval via approve_plan "
            "before executing)"
        ),
        "leader_mode_build": (
            "Teammate execution mode: build_mode (teammates execute and "
            "complete tasks autonomously without plan approval)"
        ),
        "teammate_mode_plan": (
            "Your execution mode: plan_mode (after selecting or receiving a task you must "
            "submit a plan via submit_plan; that tool reserves the task. Wait for the leader to approve "
            "that plan_id via approve_plan before executing)"
        ),
        "teammate_mode_build": (
            "Your execution mode: build_mode (after claiming a task you "
            "execute autonomously and mark it completed without leader plan "
            "approval)"
        ),
    },
}


def _labels_for(language: str) -> dict[str, str]:
    return _LABELS.get(language, _LABELS["cn"])


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def build_team_role_section(
    *,
    role: TeamRole,
    member_name: str | None,
    teammate_mode: str = "build_mode",
    language: str = "cn",
) -> PromptSection:
    """Build the role + member name section.

    Args:
        role: LEADER or TEAMMATE.
        member_name: Optional member identifier (semantic slug).
        teammate_mode: Execution mode applied to teammates in this team
            (``"plan_mode"`` or ``"build_mode"``). For LEADER, rendered
            as a description of how teammates execute; for TEAMMATE,
            rendered as the member's own execution mode.
        language: Prompt language ('cn' or 'en').

    Returns:
        PromptSection containing role policy text under a single H1
        heading, with the member name appended as a leading line when set.
    """
    labels = _labels_for(language)
    policy_name = "leader_policy" if role == TeamRole.LEADER else "teammate_policy"
    role_text = load_template(policy_name, language).content.strip()

    member_line = f"{labels['member_name_line']}: {member_name}\n\n" if member_name else ""
    is_plan_mode = teammate_mode == "plan_mode"
    if role == TeamRole.LEADER:
        mode_label_key = "leader_mode_plan" if is_plan_mode else "leader_mode_build"
    else:
        mode_label_key = "teammate_mode_plan" if is_plan_mode else "teammate_mode_build"
    mode_line = f"{labels[mode_label_key]}\n\n"
    body = f"{labels['role_heading']}\n\n{member_line}{mode_line}{role_text}\n"
    return PromptSection(
        name=TeamSectionName.ROLE,
        content={language: body},
        priority=11,
    )


_WORKFLOW_TEMPLATES: dict[str, str] = {
    "default": "leader_workflow",
    "predefined": "leader_workflow_predefined",
    "hybrid": "leader_workflow_hybrid",
}


def build_team_workflow_section(
    *,
    role: TeamRole,
    team_mode: str = "default",
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the workflow section (LEADER only).

    Args:
        role: LEADER or TEAMMATE.
        team_mode: Workflow variant — "default", "predefined", or "hybrid".
        language: Prompt language.

    Returns:
        PromptSection wrapping the matching ``leader_workflow_*.md``
        under an H1 heading; ``None`` for non-leader roles.
    """
    if role != TeamRole.LEADER:
        return None
    labels = _labels_for(language)
    template_name = _WORKFLOW_TEMPLATES.get(team_mode, "leader_workflow")
    workflow_text = load_template(template_name, language).content.strip()
    body = f"{labels['workflow_heading']}\n\n{workflow_text}\n"
    return PromptSection(
        name=TeamSectionName.WORKFLOW,
        content={language: body},
        priority=13,
    )


def build_team_lifecycle_section(
    *,
    role: TeamRole,
    lifecycle: str,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the team lifecycle section (LEADER only).

    Args:
        role: LEADER or TEAMMATE.
        lifecycle: ``"persistent"`` or ``"temporary"``.
        language: Prompt language.

    Returns:
        PromptSection containing the lifecycle template; ``None`` for
        non-leader roles.
    """
    if role != TeamRole.LEADER:
        return None
    labels = _labels_for(language)
    template_name = "lifecycle_persistent" if lifecycle == "persistent" else "lifecycle_temporary"
    lifecycle_text = load_template(template_name, language).content.strip()
    body = f"{labels['lifecycle_heading']}\n\n{lifecycle_text}\n"
    return PromptSection(
        name=TeamSectionName.LIFECYCLE,
        content={language: body},
        priority=14,
    )


def build_team_persona_section(
    *,
    persona: str | None,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the persona section.

    Returns:
        PromptSection with the persona description, or ``None`` when
        no persona is set.
    """
    if not persona:
        return None
    labels = _labels_for(language)
    body = f"{labels['persona_heading']}\n\n{persona}\n"
    return PromptSection(
        name=TeamSectionName.PERSONA,
        content={language: body},
        priority=15,
    )


def build_team_extra_section(
    *,
    base_prompt: str | None,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the user-supplied extra instructions section.

    No header is added so the user's text reads like a continuation of
    the policy stack.

    Returns:
        PromptSection with the base prompt body, or ``None`` when empty.
    """
    if not base_prompt or not base_prompt.strip():
        return None
    return PromptSection(
        name=TeamSectionName.EXTRA,
        content={language: f"{base_prompt.strip()}\n"},
        priority=16,
    )


def build_team_info_section(
    *,
    team_info: dict[str, Any] | None,
    team_workspace_mount: str | None = None,
    team_workspace_path: str | None = None,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the team metadata section.

    Args:
        team_info: Mapping with optional ``team_name``, ``display_name``
            and ``desc`` keys (the shape returned by
            ``TeamBackend.get_team_info``).
        team_workspace_mount: Agent-relative mount point of the team
            shared workspace (e.g. ``.team/{team_name}/``).  When set,
            the section appends a bullet telling the LLM how to
            read/write team-shared files from its own workspace.
        team_workspace_path: Absolute path of the team shared
            workspace on disk.  Purely informational; appended as a
            nested bullet when ``team_workspace_mount`` is provided.
        language: Prompt language.

    Returns:
        PromptSection listing team_name, display_name, goal and (when
        configured) the shared workspace mount, or ``None`` when no
        usable fields are present.
    """
    labels = _labels_for(language)
    team_name = team_info.get("team_name") if team_info else None
    display_name = team_info.get("display_name") if team_info else None
    desc = team_info.get("desc") if team_info else None
    mount = team_workspace_mount.strip() if team_workspace_mount else ""
    if not any([team_name, display_name, desc, mount]):
        return None

    lines = [labels["info_heading"], ""]
    if team_name:
        lines.append(f"- {labels['team_name_label']}: {team_name}")
    if display_name:
        lines.append(f"- {labels['display_name_label']}: {display_name}")
    if desc:
        lines.append(f"- {labels['team_desc']}: {desc}")
    if mount:
        lines.append(f"- {labels['team_workspace']}: `{mount}`")
        lines.append(f"  - {labels['team_workspace_purpose']}")
        if team_workspace_path:
            lines.append(f"  - {labels['team_workspace_abs']}: `{team_workspace_path}`")
    body = "\n".join(lines) + "\n"
    return PromptSection(
        name=TeamSectionName.INFO,
        content={language: body},
        priority=65,
    )


def _format_human_agent_roster(names: list[str], language: str) -> str:
    """Render the list of human-agent member names for inline prompts."""
    quoted = ", ".join(f"`{n}`" for n in names)
    if language == "cn":
        return f"注册的人类成员：{quoted}"
    return f"Registered human members: {quoted}"


def _hitt_section_leader_cn(names: list[str]) -> str:
    roster = _format_human_agent_roster(names, "cn")
    return (
        "# HITT — 人类成员协作规则\n\n"
        f"{roster}。他们是真实人类操作者的代理，与你和其它 teammate 平等。"
        "所有 role=human_agent 的成员都适用下列规则：\n\n"
        "1. **禁止** 用 plain text 向任何人类成员发问或对话——所有定向"
        '沟通必须调用 `send_message(to="<human_member_name>", ...)`，你的'
        "纯文本输出对方是看不到的。\n"
        '2. 可以通过 `update_task(task_id=..., assignee="<human_member_name>")` '
        "把需要特定人类判断或操作的任务指派给对应成员。\n"
        "3. 一旦某个人类成员认领了任务（status=claimed），你 **不能** 取消"
        "（update_task status=cancelled）也 **不能** 改派（update_task "
        "assignee=<他人>），即使团队因人类没及时响应而停滞也必须保持停滞，"
        "只能用 `send_message` 催促对应人类成员。\n"
        "4. 每个人类成员始终是 ready 状态，不会进入 busy 或 shutdown，"
        "所以不要对它们调用 `shutdown_member` / `spawn_member`。\n"
        "5. 如果 user 表达了“我也要加入团队”之类的加入意图，且团队尚未"
        "创建，请在 `build_team` 时把 `enable_hitt=true`；若需要多个不同"
        "人类成员，通过 `predefined_members` 传入 role=human_agent 的 spec。\n"
    )


def _hitt_section_teammate_cn(names: list[str]) -> str:
    """Legacy roster-exposing variant.

    Only used when ``TeamAgentSpec.expose_human_agents_to_teammates``
    is True. Lists every human_agent ``member_name`` inline.
    """
    roster = _format_human_agent_roster(names, "cn")
    return (
        "# HITT — 与人类成员协作\n\n"
        f"团队里存在下列人类成员（真实人类）：{roster}。把他们视作普通 "
        "teammate：与他们交流一律通过 `send_message(to=<对应名字>, ...)`，"
        "不要假设他们会自动看到你的 plain text。他们可能拥有你无法完成的"
        "决策权或操作能力。\n"
    )


def _hitt_section_teammate_anonymous_cn() -> str:
    """Default role-neutral variant.

    Used when ``TeamAgentSpec.expose_human_agents_to_teammates`` is
    False. Does not list any human_agent ``member_name``, does not
    say "real humans", and does not hint at why some peers behave
    asynchronously — keeps peer role (teammate vs human_agent)
    hidden while still carrying the collaboration guidance that
    actually matters for teammates.
    """
    return (
        "# HITT — 与 Peer 协作的稳健习惯\n\n"
        "本团队中部分 peer 不会主动读取你的 plain text 输出，"
        "且回复节奏可能慢于一般 LLM 队友。对所有 peer 一律按以下契约协作：\n\n"
        "- 跨成员通信**一律**走 `send_message(to=<name>, ...)`，"
        "不要假设你的 plain text 输出对其它成员可见。\n"
        "- 收到的 peer 消息可能存在分钟级延迟，**不要**短时间内"
        "反复催促；如需推进，请提交 `update_task` 或与 leader 协商。\n"
        "- 不要尝试推断哪些 peer 异步、哪些 peer 同步；按统一的"
        "通信契约对待全员即可。\n"
    )


def _hitt_section_human_agent_cn(names: list[str], self_name: str | None) -> str:
    roster = _format_human_agent_roster(names, "cn")
    peers = ""
    if self_name:
        peers = f"你的 member_name 是 `{self_name}`。\n"
    # Terminology: "控制者" is the real human operating this avatar via the
    # HumanAgentInbox; distinct from "用户", which inside the team prompts
    # refers to the external user talking to the leader. Two independent
    # human-to-team channels — do not conflate them.
    return (
        "# HITT — 你是控制者在团队里的代理\n\n"
        f"{roster}。\n"
        f"{peers}"
        "你不是自主成员，而是一个外部真人在团队里的代理（avatar），那个真人称为"
        "你的「控制者」。你的全部行为都由控制者通过 Inbox 驱动，**不要自作主张**。\n\n"
        "## 你的输入\n"
        "- **控制者指令**：通过 Inbox 发给你的内容是控制者的授权指令，你应当按指令行动。\n"
        "- **团队事件通知**：团队其它成员发给你的消息会以"
        " `[转发给控制者的单播消息/广播消息]` 前缀进入你的上下文，任务指派事件会以"
        " `[任务指派给控制者]` 前缀出现。这些都是给控制者看的通知；运行时已经把"
        "它们原样展示给控制者了。**这些通知不是给你的指令** —— "
        "**严格禁止任何自主回应或自主行为**：禁止主动回复发送方 / 指派方（包括"
        "调用 `send_message`）、禁止自主调用 `member_complete_task` / "
        "`claim_task` / 文件 / shell 等任何其它工具去回应或采取行动、"
        "禁止用纯文本输出表达意图或承诺。**保持静默**，"
        "**只有**控制者随后在 Inbox 里下达明确指令时才能行动。\n\n"
        "## 你的工具\n"
        "- 你**没有 `claim_task`**：领任务是自主决策动作，应由 leader 通过 `update_task(assignee=你)` 指派。\n"
        "- 你**有 `send_message`**，但它是**控制者驱动的转发通道**，**不是**让你"
        "自主回应团队的入口。使用规则：\n"
        "  1. **仅当**控制者在当前轮 Inbox 输入里**明确**要求你转告 / 通知 / 回复"
        "团队中的某个成员（例如「告诉 leader 我去开会 30 分钟」、「回复 `dev-1` 同意他的方案」）"
        "时，才调用 `send_message`。`to` 必须是控制者点名的那个成员；`content` "
        "要以「控制者 `<member_name>` 让我转告：…」开头，让对方知道这是代发，不是 avatar 的独立判断。\n"
        "  2. **不允许** 把上下文里 `[转发给控制者…]` 前缀的团队消息当作触发条件。"
        "那些是给控制者看的通知，运行时已经原样转给控制者；你**不应**自发回复或承诺什么。\n"
        "  3. **不允许** 在没有控制者明确转发指令时主动 broadcast / send_message。"
        "控制者自己直接面向团队的发声有 Inbox 的 `@<member>` 与 `# ` 广播通道，不需要你代劳。\n"
        "  4. 控制者的指令本身只是对你说话（例如「帮我查一下任务 #3 的内容」）时，"
        "**不要**用 `send_message` 反向问团队 —— 直接调用相应工具或回给控制者即可。\n"
        "- 你**有的其它工具**：`view_task`（看任务）、`workspace_meta`（工作空间锁/版本）、"
        "`member_complete_task`（标记自己被指派的任务为完成）以及标准的"
        "文件操作 / shell 工具，用于真正完成控制者交代的事务。\n\n"
        "## 行为准则\n"
        "- **严格禁止主动发声**：你不应该用自然语言"
        "试图与团队沟通进展（团队看不到你的纯文本，他们看到的是控制者的话）。"
        "如果控制者没明确让你转告，就**禁止**触发 `send_message`。\n"
        "- 看到 `[任务指派给控制者]` 通知时**严格禁止**自动调用 `member_complete_task` / "
        "`claim_task` / 文件 / shell 等任何工具去推进任务；"
        "也**严格禁止**对该通知用纯文本「领命」或承诺；"
        "**只有**控制者在 Inbox 里下达明确指令时才能行动。\n"
        "- 如果控制者的指令需要文件读写、查看任务、提交结果，立即调用对应工具完成；"
        "完成后简洁地把结果回给控制者即可（你的回应只对控制者可见）。\n"
        "- 第一次启动时如果只收到「Join the team and wait...」之类的占位消息，"
        "**直接静默等待**，不要调用任何工具，不要广播任何文字。\n"
    )


def _hitt_section_leader_en(names: list[str]) -> str:
    roster = _format_human_agent_roster(names, "en")
    return (
        "# HITT — Collaborating with Human Members\n\n"
        f"{roster}. They represent real human operators and stand on "
        "equal footing with you and the other teammates. The following "
        "rules apply to every member whose role is `human_agent`:\n\n"
        "1. You **must not** address a human member via plain text — "
        "every direct exchange must go through "
        '`send_message(to="<human_member_name>", ...)`. Your plain text '
        "output is not visible to human members.\n"
        "2. Use `update_task(task_id=..., "
        'assignee="<human_member_name>")` to assign tasks that require a '
        "specific human's judgement or action.\n"
        "3. Once a human member claims a task (status=claimed) you "
        "**cannot** cancel it (`update_task status=cancelled`) and "
        "**cannot** reassign it (`update_task assignee=<someone>`). Even "
        "if the team stalls waiting for that human, it must stall — only "
        "`send_message` nudges to the specific human are allowed.\n"
        "4. Every human member stays READY forever; never call "
        "`shutdown_member` or `spawn_member` on them.\n"
        '5. If the user signals intent to join the team (e.g. "I want '
        'to join") and the team has not been created yet, call '
        "`build_team` with `enable_hitt=true`. If multiple distinct "
        "human members are needed, pass them via `predefined_members` "
        "as TeamMemberSpec entries with role=human_agent.\n"
    )


def _hitt_section_teammate_en(names: list[str]) -> str:
    """Legacy roster-exposing variant.

    Only used when ``TeamAgentSpec.expose_human_agents_to_teammates``
    is True. Lists every human_agent ``member_name`` inline.
    """
    roster = _format_human_agent_roster(names, "en")
    return (
        "# HITT — Working with Human Members\n\n"
        f"The team includes the following human members (real humans): "
        f"{roster}. Treat each of them as an ordinary teammate: every "
        "direct exchange must use `send_message(to=<their_name>, ...)`. "
        "Do not assume your plain text is visible to a human member; "
        "they may hold decisions or privileges you cannot execute.\n"
    )


def _hitt_section_teammate_anonymous_en() -> str:
    """Default role-neutral variant.

    Used when ``TeamAgentSpec.expose_human_agents_to_teammates`` is
    False. Does not list any human_agent ``member_name``, does not
    say "real humans", and does not hint at why some peers behave
    asynchronously — keeps peer role (teammate vs human_agent)
    hidden while still carrying the collaboration guidance that
    actually matters for teammates.
    """
    return (
        "# HITT — Robust Habits for Peer Collaboration\n\n"
        "Some peers in this team do not actively read your plain "
        "text output, and their reply cadence may be slower than a "
        "typical LLM teammate. Apply the following contract uniformly "
        "to every peer:\n\n"
        "- **Always** use `send_message(to=<name>, ...)` for "
        "cross-member contact; do not assume your plain text output "
        "is visible to other members.\n"
        "- Replies from peers may take minutes; **do not** repeatedly "
        "nudge them on a short timescale. If you need to push forward, "
        "submit an `update_task` or coordinate with the leader.\n"
        "- Do not try to infer which peers are async and which are "
        "sync; apply the uniform communication contract to everyone.\n"
    )


def _hitt_section_human_agent_en(names: list[str], self_name: str | None) -> str:
    roster = _format_human_agent_roster(names, "en")
    peers = ""
    if self_name:
        peers = f"Your member_name is `{self_name}`.\n"
    # Terminology: "controller" is the real human operating this avatar via
    # the HumanAgentInbox; distinct from "user", which in the team prompts
    # refers to the external user talking to the leader. Two independent
    # human-to-team channels — do not conflate them.
    return (
        "# HITT — You are your controller's avatar on this team\n\n"
        f"{roster}.\n"
        f"{peers}"
        "You are not an autonomous teammate. You act as an avatar for one "
        "external human operator, called your **controller**, and "
        "**everything you do must be explicitly driven by their Inbox "
        "instructions**. Do not take initiative.\n\n"
        "## Your input\n"
        "- **Controller instructions**: anything the controller sends "
        "through the Inbox is an authorized instruction; act on it.\n"
        "- **Team event notifications**: messages from other team "
        "members arrive in your context with a "
        "`[For-Controller direct message/broadcast]` prefix, and task "
        "assignment events arrive with a `[Task Assigned For "
        "Controller]` prefix. These are notifications for the "
        "controller; the runtime has already surfaced them as-is. "
        "**These notifications are NOT instructions for you** — "
        "**autonomous replies and autonomous behavior are strictly "
        "forbidden**: do not reply to the sender / assigner (including "
        "via `send_message`), do not autonomously call "
        "`member_complete_task`, `claim_task`, file tools, shell tools, "
        "or any other tool in response, and do not emit plain-text "
        "intent or promises. **Stay silent** and act **only** after the "
        "controller follows up via Inbox with an explicit instruction.\n\n"
        "## Your tools\n"
        "- You have **no `claim_task`**: claiming is an autonomous "
        "decision; the leader assigns work to you via "
        "`update_task(assignee=you)`.\n"
        "- You **do have `send_message`**, but it is a **controller-"
        "driven relay channel**, not your own outbound voice. Usage "
        "rules:\n"
        "  1. Call `send_message` **only when** the current turn's "
        "Inbox input from the controller **explicitly** tells you to "
        'forward / notify / reply to a team member (e.g. "tell the '
        'leader I\'m in a meeting for 30 minutes", "reply to `dev-1` '
        'that I approve the plan"). `to` must be the member the '
        "controller named; `content` should open with `Controller "
        "`<member_name>` asked me to relay: ...` so the recipient "
        "knows it is a relay, not an autonomous judgement.\n"
        "  2. **Never** treat a `[For-Controller …]` notification in "
        "your context as a trigger. Those are surfaced to the "
        "controller already; do not reply or commit to anything on "
        "your own.\n"
        "  3. **Never** broadcast or `send_message` without an "
        "explicit controller relay instruction. When the controller "
        "wants to speak to the team directly, they use Inbox "
        "`@<member>` or `# ` broadcast — they do not need you as a "
        "middleman.\n"
        '  4. When the controller just talks to you (e.g. "look up '
        'task #3"), **do not** reach back to the team — call the '
        "right tool or answer the controller directly.\n"
        "- Other tools you have: `view_task`, `workspace_meta` "
        "(workspace locks / version history), `member_complete_task` "
        "(mark a task the leader assigned to you as completed), plus "
        "the standard file / shell tools, to actually carry out what "
        "the controller asks.\n\n"
        "## Conduct\n"
        "- **Speaking up on your own is strictly forbidden**: do not "
        "narrate progress to the team via plain text — the team cannot "
        "see your text anyway; they see the controller's voice through "
        "the Inbox. If the controller did not explicitly ask you to "
        "relay something, triggering `send_message` is forbidden.\n"
        "- When a `[Task Assigned For Controller]` notification arrives, "
        "**autonomously calling `member_complete_task`, `claim_task`, "
        "file tools, shell tools, or any other tool to act on the "
        "assignment is strictly forbidden**; also do **not** acknowledge "
        "the assignment with plain text or commit to anything. **Only** "
        "act when the controller follows up with an explicit Inbox "
        'instruction (e.g. "mark task X completed").\n'
        "- When the controller's instruction needs file work, task "
        "lookup, or completion, call the right tool immediately, then "
        "reply to the controller with a concise result. Your reply is "
        "visible to the controller only.\n"
        "- If the only input you ever received is a placeholder like "
        '"Join the team and wait for your first assignment.", '
        "**stay silent** — make no tool calls and emit no broadcast "
        "text.\n"
    )


def build_team_hitt_section(
    *,
    role: TeamRole,
    human_agent_names: "list[str] | frozenset[str] | set[str] | None" = None,
    language: str = "cn",
    self_member_name: str | None = None,
    expose_human_agents_to_teammates: bool = False,
) -> Optional[PromptSection]:
    """Build the HITT collaboration-rules section.

    Returns a non-None section only when at least one human-agent
    member is registered. The section text is role-specific:

    - LEADER / HUMAN_AGENT: always receive the full roster section
      enumerating every human_agent ``member_name``. Leader owns
      spawn/approval flows; human_agent's roster includes itself.
    - TEAMMATE: receives a role-neutral anonymous section by default
      (no ``member_name`` listed, no "real humans" label) so peer
      role (teammate vs human_agent) is not leaked into other
      members' system prompts. Cross-member contact for everyone
      already goes through ``send_message``, so teammates do not
      need to distinguish human peers from LLM peers. Setting
      ``expose_human_agents_to_teammates=True`` (driven by
      ``TeamAgentSpec.expose_human_agents_to_teammates``) switches
      teammates back to the legacy roster section.

    Args:
        role: The role whose prompt this section targets.
        human_agent_names: Member names of every registered human
            agent. Empty/None means no human members → no section.
        language: "cn" or "en".
        self_member_name: The current member's own name, used to tell
            a human-agent reader which entry in the roster is itself.
        expose_human_agents_to_teammates: Only affects the TEAMMATE
            branch. False (default) → anonymous variant. True →
            legacy roster-exposing variant.
    """
    if not human_agent_names:
        return None
    names = sorted(human_agent_names)
    if language == "cn":
        if role == TeamRole.LEADER:
            body = _hitt_section_leader_cn(names)
        elif role == TeamRole.TEAMMATE:
            body = (
                _hitt_section_teammate_cn(names)
                if expose_human_agents_to_teammates
                else _hitt_section_teammate_anonymous_cn()
            )
        elif role == TeamRole.HUMAN_AGENT:
            body = _hitt_section_human_agent_cn(names, self_member_name)
        else:
            return None
    else:
        if role == TeamRole.LEADER:
            body = _hitt_section_leader_en(names)
        elif role == TeamRole.TEAMMATE:
            body = (
                _hitt_section_teammate_en(names)
                if expose_human_agents_to_teammates
                else _hitt_section_teammate_anonymous_en()
            )
        elif role == TeamRole.HUMAN_AGENT:
            body = _hitt_section_human_agent_en(names, self_member_name)
        else:
            return None
    return PromptSection(
        name=TeamSectionName.HITT,
        content={language: body},
        priority=12,
    )


def _format_bridge_agent_roster(names: list[str], language: str) -> str:
    """Render the list of bridge-agent member names for inline prompts."""
    quoted = ", ".join(f"`{n}`" for n in names)
    if language == "cn":
        return f"注册的桥接成员：{quoted}"
    return f"Registered bridge members: {quoted}"


def _bridge_section_leader_cn(names: list[str]) -> str:
    roster = _format_bridge_agent_roster(names, "cn")
    return (
        "# Bridge Agent — 与桥接外部 agent 的成员协作\n\n"
        f"{roster}。他们是注册的正式成员，**与其它 teammate 完全一致**——"
        "你按照普通 teammate 的方式分派任务、收发消息、协作。\n\n"
        "这些成员内部接入了一个 jiuwen 之外的独立 agent 作为**实际执行者**，"
        "由协议适配层驱动，**对你而言行为与普通 teammate 一致**——直接 "
        "`@<bridge_member_name>` 沟通即可。你不需要也无法直接和远程 agent 对话。\n"
    )


def _bridge_section_teammate_cn(names: list[str]) -> str:
    roster = _format_bridge_agent_roster(names, "cn")
    return (
        "# Bridge Agent — 与桥接外部 agent 的成员协作\n\n"
        f"团队里存在下列桥接成员（背后由 jiuwen 之外的独立 agent 执行）："
        f"{roster}。把他们视作普通 teammate，使用 `send_message(to=<对应名字>, ...)` "
        "正常沟通。你无需关心他们的对端是远程 agent —— 他们的输出形式与你完全一致。\n"
    )


def _bridge_section_bridge_agent_cn(names: list[str], self_name: str | None) -> str:
    roster = _format_bridge_agent_roster(names, "cn")
    peers = ""
    if self_name:
        peers = f"你的 member_name 是 `{self_name}`。\n"
    return (
        "# Bridge Agent — 你是外部独立 agent 在团队中的调度员\n\n"
        f"{roster}。\n"
        f"{peers}"
        "你是 jiuwen 团队的 teammate，但**具体工作产出由外部独立 agent**"
        "（如 claudecode / codex / hermes 等）通过协议接入完成。你的角色是"
        "**调度员**，不是内容创造者。\n\n"
        "## 工作流\n"
        "- 团队消息会**自动转发**给外部执行者，你将看到 `[来自团队成员 X 的消息]"
        " + [外部执行者的执行结果]` 一同进入上下文。\n"
        "- 你的工作是**调度决策**：是否调用 `send_message` 把外部的执行结果"
        "原样回传给原发件人；是否调用 `claim_task` / `member_complete_task` "
        "等任务管理工具；或保持沉默。\n\n"
        "## 行为准则（重要）\n"
        "- **不要改写、综合或解释**外部的执行结果——把它原样传达给团队即可，"
        "最多在前后加极简的调度性说明（如「这是任务 X 的结果：」）。\n"
        "- **不要试图自己思考任务的内容**——具体工作由外部执行者完成，你不是"
        "内容生产者。\n"
        "- **不要把原消息再次转发**给团队（消息已经送到了你这；如果你要回复，"
        "调用 `send_message`，传达内容直接用外部执行者的输出）。\n"
        "- **你没有任何「咨询外部」的工具**——外部接入只通过自动转发自然到来。\n"
        "- 当上下文显示 `[remote agent unavailable: no protocol adapter "
        "registered]` 时表示外部尚未接入，此时你应当作为普通 teammate 自主"
        "完成任务（如果你能完成）或通过 send_message 告知发件人外部 agent "
        "暂不可用。\n"
    )


def _bridge_section_leader_en(names: list[str]) -> str:
    roster = _format_bridge_agent_roster(names, "en")
    return (
        "# Bridge Agent — Working with bridge-to-remote members\n\n"
        f"{roster}. They are first-class members and **behave exactly "
        "like ordinary teammates** — assign tasks, exchange messages, "
        "and collaborate with them through the standard channels.\n\n"
        "Internally each of these members is paired with an independent "
        "agent outside jiuwen reached through a protocol adapter. From "
        "your perspective they are still teammates: use "
        "`@<bridge_member_name>` to address them. You neither need to "
        "nor can talk to the remote agent directly.\n"
    )


def _bridge_section_teammate_en(names: list[str]) -> str:
    roster = _format_bridge_agent_roster(names, "en")
    return (
        "# Bridge Agent — Working with bridge-to-remote members\n\n"
        f"The team includes these bridge members (backed by an external "
        f"independent agent): {roster}. Treat each as an ordinary "
        "teammate — use `send_message(to=<their_name>, ...)` normally. "
        "You don't need to care that their backing executor is remote; "
        "their outputs look the same to you as any other teammate's.\n"
    )


def _bridge_section_bridge_agent_en(names: list[str], self_name: str | None) -> str:
    roster = _format_bridge_agent_roster(names, "en")
    peers = ""
    if self_name:
        peers = f"Your member_name is `{self_name}`.\n"
    return (
        "# Bridge Agent — You are an external agent's scheduler on this team\n\n"
        f"{roster}.\n"
        f"{peers}"
        "You are a regular jiuwen teammate locally, but the **concrete "
        "work output** is produced by an independent agent outside "
        "jiuwen (e.g. claudecode / codex / hermes) reached over a "
        "protocol. Your role is the **scheduler** — not the content "
        "producer.\n\n"
        "## Workflow\n"
        "- Inbound team messages are **auto-forwarded** to the remote "
        "executor for you. Your context will show "
        "`[Team message from X]` followed by `[Remote executor's "
        "output]` in the same turn.\n"
        "- Your job is to **schedule**: whether to `send_message` the "
        "remote output verbatim back to the original sender, whether "
        "to call `claim_task` / `member_complete_task` and similar task "
        "management tools, or to stay silent.\n\n"
        "## Conduct (important)\n"
        "- **Do NOT rewrite, synthesize, or interpret** the remote "
        "output — pass it through verbatim. At most prepend a minimal "
        'scheduling preamble (e.g. "Result for task X:").\n'
        "- **Do NOT think through the work yourself** — the concrete "
        "content comes from the remote executor; you are not the "
        "content producer.\n"
        "- **Do NOT forward the original message again** — it already "
        "reached you; if you reply, the content body should be the "
        "remote executor's output.\n"
        "- You have **no 'consult the remote' tool** — the external "
        "executor is invoked automatically by the framework on the "
        "mailbox path; no additional tool is exposed.\n"
        "- When the context shows `[remote agent unavailable: no "
        "protocol adapter registered]`, the remote is not wired yet. "
        "Behave as a regular teammate — complete the work yourself if "
        "you can, or `send_message` the requester to explain that the "
        "remote agent is currently offline.\n"
    )


def build_team_bridge_section(
    *,
    role: TeamRole,
    bridge_agent_names: "list[str] | frozenset[str] | set[str] | None" = None,
    language: str = "cn",
    self_member_name: str | None = None,
) -> Optional[PromptSection]:
    """Build the Bridge Agent collaboration-rules section.

    Returns a non-None section only when at least one bridge-agent
    member is registered. Text is role-specific and enumerates every
    registered bridge member inline so the leader / other teammates
    see whom to address through ``send_message``, and the bridge
    avatar itself sees the scheduling contract.

    Args:
        role: The role whose prompt this section targets.
        bridge_agent_names: Member names of every registered bridge
            agent. Empty/None means no bridges → no section.
        language: ``"cn"`` or ``"en"``.
        self_member_name: The current member's own name, used to tell
            a bridge-agent reader which entry in the roster is itself.
    """
    if not bridge_agent_names:
        return None
    names = sorted(bridge_agent_names)
    if language == "cn":
        if role == TeamRole.LEADER:
            body = _bridge_section_leader_cn(names)
        elif role == TeamRole.TEAMMATE:
            body = _bridge_section_teammate_cn(names)
        elif role == TeamRole.BRIDGE_AGENT:
            body = _bridge_section_bridge_agent_cn(names, self_member_name)
        else:
            return None
    else:
        if role == TeamRole.LEADER:
            body = _bridge_section_leader_en(names)
        elif role == TeamRole.TEAMMATE:
            body = _bridge_section_teammate_en(names)
        elif role == TeamRole.BRIDGE_AGENT:
            body = _bridge_section_bridge_agent_en(names, self_member_name)
        else:
            return None
    return PromptSection(
        name=TeamSectionName.BRIDGE,
        content={language: body},
        priority=12,
    )


def build_team_members_section(
    *,
    team_members: list[dict[str, str]] | None,
    self_member_name: str | None,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the team relationships section.

    Args:
        team_members: List of member dicts with ``member_name``,
            ``display_name`` and optional ``desc``.
        self_member_name: Excluded from the listing if present.
        language: Prompt language.

    Returns:
        PromptSection listing peer members, or ``None`` when the list
        is empty after self exclusion.
    """
    if not team_members:
        return None
    labels = _labels_for(language)
    rows = []
    for member in team_members:
        member_name = member.get("member_name", "")
        if member_name == self_member_name:
            continue
        display_name = member.get("display_name", "unknown")
        desc = member.get("desc", "")
        line = f"- member_name={member_name} display_name={display_name}"
        if desc:
            line += f" :: {desc}"
        rows.append(line)
    if not rows:
        return None
    body = labels["members_heading"] + "\n\n" + "\n".join(rows) + "\n"
    return PromptSection(
        name=TeamSectionName.MEMBERS,
        content={language: body},
        priority=66,
    )


def build_team_static_sections(
    *,
    role: TeamRole,
    persona: str,
    member_name: str | None,
    lifecycle: str = "temporary",
    teammate_mode: str = "build_mode",
    team_mode: str = "default",
    base_prompt: str | None = None,
    language: str = "cn",
    human_agent_names: list[str] | None = None,
    expose_human_agents_to_teammates: bool = False,
    bridge_agent_names: list[str] | None = None,
) -> list[PromptSection]:
    """Build the never-changing team sections for one member.

    Single source of truth for one-shot static team sections. In-process
    DeepAgent members call this through :class:`TeamPolicyRail` for role /
    bridge / workflow / lifecycle / persona / extra; HITT is refreshed by
    the rail dynamically instead of being passed here. External CLI members
    use this function to build a standalone prompt snapshot, so callers may
    still pass ``human_agent_names`` to include a static HITT section.

    Args:
        role: LEADER or TEAMMATE (other roles get the role-appropriate slices).
        persona: The member's persona text (empty drops the persona section).
        member_name: Semantic member identifier.
        lifecycle: Team lifecycle ("temporary" / "persistent").
        teammate_mode: Teammate execution mode ("build_mode" / "plan_mode").
        team_mode: Team mode ("default" / "predefined" / "hybrid").
        base_prompt: Optional user-supplied prompt appended as the extra section.
        language: Prompt language ("cn" / "en").
        human_agent_names: Optional registered human-agent member names used
            for one-shot HITT prompt snapshots, mainly external CLI members.
        expose_human_agents_to_teammates: Whether teammates see human agents
            in that one-shot HITT snapshot.
        bridge_agent_names: Registered bridge-agent member names (bridge section).

    Returns:
        The non-None sections, unsorted (the caller orders by priority).
    """
    builders = [
        build_team_role_section(
            role=role,
            member_name=member_name,
            teammate_mode=teammate_mode,
            language=language,
        ),
        build_team_hitt_section(
            role=role,
            human_agent_names=human_agent_names,
            language=language,
            self_member_name=member_name,
            expose_human_agents_to_teammates=expose_human_agents_to_teammates,
        ),
        build_team_bridge_section(
            role=role,
            bridge_agent_names=bridge_agent_names,
            language=language,
            self_member_name=member_name,
        ),
        build_team_workflow_section(
            role=role,
            team_mode=team_mode,
            language=language,
        ),
        build_team_lifecycle_section(
            role=role,
            lifecycle=lifecycle,
            language=language,
        ),
        build_team_persona_section(
            persona=persona,
            language=language,
        ),
        build_team_extra_section(
            base_prompt=base_prompt,
            language=language,
        ),
    ]
    return [section for section in builders if section is not None]


def build_team_member_system_prompt(
    *,
    role: TeamRole,
    persona: str,
    member_name: str | None,
    lifecycle: str = "temporary",
    teammate_mode: str = "build_mode",
    team_mode: str = "default",
    base_prompt: str | None = None,
    language: str = "cn",
    human_agent_names: list[str] | None = None,
    expose_human_agents_to_teammates: bool = False,
    bridge_agent_names: list[str] | None = None,
) -> str:
    """Render a member's team sections into a single standalone system prompt.

    Used to give an external CLI member (whose brain is not a local DeepAgent)
    the same team-rail sections an in-process member gets, assembled the same
    way (priority-ordered, ``\\n\\n``-joined). It includes ONLY the team
    sections — the harness / other DeepAgent rails do not apply to an external
    CLI, so their prompt contributions are intentionally excluded.

    Args mirror :func:`build_team_static_sections`.

    Returns:
        The rendered system prompt, or ``""`` when no section produced content.
    """
    sections = build_team_static_sections(
        role=role,
        persona=persona,
        member_name=member_name,
        lifecycle=lifecycle,
        teammate_mode=teammate_mode,
        team_mode=team_mode,
        base_prompt=base_prompt,
        language=language,
        human_agent_names=human_agent_names,
        expose_human_agents_to_teammates=expose_human_agents_to_teammates,
        bridge_agent_names=bridge_agent_names,
    )
    builder = SystemPromptBuilder(language=language)
    for section in sections:
        builder.add_section(section)
    return builder.build()


__all__ = [
    "TeamSectionName",
    "build_team_bridge_section",
    "build_team_extra_section",
    "build_team_hitt_section",
    "build_team_info_section",
    "build_team_lifecycle_section",
    "build_team_member_system_prompt",
    "build_team_members_section",
    "build_team_persona_section",
    "build_team_role_section",
    "build_team_static_sections",
    "build_team_workflow_section",
]
