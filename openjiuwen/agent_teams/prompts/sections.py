# coding: utf-8

"""PromptSection builders for the team policy rail.

Each function produces a single ``PromptSection`` covering one slice of
team-specific content (role, workflow, lifecycle, persona, ...). The
rail composes these sections into the shared ``SystemPromptBuilder``
alongside the harness sections (safety, tools, memory, ...).

Section layout (aligned with ``prompt_design.md``):

  P:11  team_role        — member id + role policy (always)
  P:12  team_hitt        — HITT collaboration rules (when human members exist)
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
from openjiuwen.core.single_agent.prompts.builder import PromptSection

# ---------------------------------------------------------------------------
# Section name constants
# ---------------------------------------------------------------------------


class TeamSectionName:
    """Centralized section names owned by ``TeamPolicyRail``."""

    ROLE = "team_role"
    HITT = "team_hitt"
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
            "团队成员执行模式: plan_mode（成员领取任务后需先提交计划，由你通过 approve_plan 审批后才能执行）"
        ),
        "leader_mode_build": ("团队成员执行模式: build_mode（成员领取任务后自主执行并直接完成，无需你审批计划）"),
        "teammate_mode_plan": (
            "你的执行模式: plan_mode（领取任务后必须先通过 write_plan 提交计划，"
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
            "after claiming a task and wait for your approval via approve_plan "
            "before executing)"
        ),
        "leader_mode_build": (
            "Teammate execution mode: build_mode (teammates execute and "
            "complete tasks autonomously without plan approval)"
        ),
        "teammate_mode_plan": (
            "Your execution mode: plan_mode (after claiming a task you must "
            "submit a plan via write_plan and wait for the leader to approve "
            "it via approve_plan before executing)"
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
    roster = _format_human_agent_roster(names, "cn")
    return (
        "# HITT — 与人类成员协作\n\n"
        f"团队里存在下列人类成员（真实人类）：{roster}。把他们视作普通 "
        "teammate：与他们交流一律通过 `send_message(to=<对应名字>, ...)`，"
        "不要假设他们会自动看到你的 plain text。他们可能拥有你无法完成的"
        "决策权或操作能力。\n"
    )


def _hitt_section_human_agent_cn(names: list[str], self_name: str | None) -> str:
    roster = _format_human_agent_roster(names, "cn")
    peers = ""
    if self_name:
        peers = f"你的 member_name 是 `{self_name}`。\n"
    return (
        "# HITT — 你是真实用户在团队里的代理\n\n"
        f"{roster}。\n"
        f"{peers}"
        "你不是自主成员，而是某个外部用户在团队里的代理（avatar）。"
        "你的全部行为都由对应的用户驱动，**不要自作主张**。\n\n"
        "## 你的输入\n"
        "- **唯一输入源**：用户通过 Inbox 发给你的指令。每次出现在你输入"
        "里的内容都已经是用户授权的指令。\n"
        "- 团队其它成员发给你的消息**不会**进入你的上下文 —— 系统会自动"
        "把它们透传给用户。**不要试图回应它们**，也不要假装看到了它们。\n\n"
        "## 你的工具\n"
        "- 你**没有 `send_message`**：用户和团队的交流由 Inbox 的 `@<member>` 路由完成，不走你。\n"
        "- 你**没有 `claim_task`**：领任务是自主决策动作，应由 leader 通过 `update_task(assignee=你)` 指派。\n"
        "- 你**有的工具**：`view_task`（看任务）、`workspace_meta`（工作空间锁/版本）、"
        "`member_complete_task`（标记自己被指派的任务为完成）以及标准的"
        "文件操作 / shell 工具，用于真正完成用户交代的事务。\n\n"
        "## 行为准则\n"
        "- **不要主动发声**：你不应该用自然语言"
        "试图与团队沟通进展（团队看不到你的纯文本，他们看到的是用户的话）。\n"
        "- 不要对自己被指派的任务的「认领事件」做出反应；除非用户在 Inbox 里说"
        "「请把任务 X 标记完成」之类的明确指令，否则**不要**自动调 `member_complete_task`。\n"
        "- 如果用户的指令需要文件读写、查看任务、提交结果，立即调用对应工具完成；"
        "完成后简洁地把结果回给用户即可（你的回应只对用户可见）。\n"
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
    roster = _format_human_agent_roster(names, "en")
    return (
        "# HITT — Working with Human Members\n\n"
        f"The team includes the following human members (real humans): "
        f"{roster}. Treat each of them as an ordinary teammate: every "
        "direct exchange must use `send_message(to=<their_name>, ...)`. "
        "Do not assume your plain text is visible to a human member; "
        "they may hold decisions or privileges you cannot execute.\n"
    )


def _hitt_section_human_agent_en(names: list[str], self_name: str | None) -> str:
    roster = _format_human_agent_roster(names, "en")
    peers = ""
    if self_name:
        peers = f"Your member_name is `{self_name}`.\n"
    return (
        "# HITT — You are an external user's avatar on this team\n\n"
        f"{roster}.\n"
        f"{peers}"
        "You are not an autonomous teammate. You act as an avatar for "
        "one external human operator, and **everything you do must be "
        "explicitly driven by that user's instructions**. Do not take "
        "initiative.\n\n"
        "## Your input\n"
        "- **Only source**: instructions the user sends through the "
        "Inbox. Anything in your input window has already been "
        "authorized by them.\n"
        "- Messages from other team members **do not** enter your "
        "context — the runtime forwards them straight to the user. "
        "Do not try to respond to them or pretend to have seen them.\n\n"
        "## Your tools\n"
        "- You have **no `send_message`**: the user reaches the team "
        "through the Inbox's `@<member>` mention routing, not through "
        "you.\n"
        "- You have **no `claim_task`**: claiming is an autonomous "
        "decision; the leader assigns work to you via "
        "`update_task(assignee=you)`.\n"
        "- You **do have**: `view_task`, `workspace_meta` (workspace "
        "locks / version history), `member_complete_task` (mark a task "
        "the leader assigned to you as completed), plus the standard "
        "file / shell tools, to actually carry out what the user asks.\n\n"
        "## Conduct\n"
        "- **Do not speak up on your own**: do not narrate progress to "
        "the team via plain text — the team cannot see your text "
        "anyway; they see the user's voice through the Inbox.\n"
        "- Do not react to TASK_CLAIMED events for yourself. Only call "
        "`member_complete_task` when the user explicitly tells you to "
        '(e.g. "mark task X completed").\n'
        "- When the user's instruction needs file work, task lookup, or "
        "completion, call the right tool immediately, then reply to the "
        "user with a concise result. Your reply is visible to the user "
        "only.\n"
        "- If the only input you ever received is a placeholder like "
        '"Join the team and wait for your first assignment.", '
        "**stay silent** — make no tool calls and emit no broadcast text.\n"
    )


def build_team_hitt_section(
    *,
    role: TeamRole,
    human_agent_names: "list[str] | frozenset[str] | set[str] | None" = None,
    language: str = "cn",
    self_member_name: str | None = None,
) -> Optional[PromptSection]:
    """Build the HITT collaboration-rules section.

    Returns a non-None section only when at least one human-agent
    member is registered. Text is role-specific and enumerates every
    registered human member inline so leaders and teammates can see
    exactly whom to address via ``send_message``.

    Args:
        role: The role whose prompt this section targets.
        human_agent_names: Member names of every registered human
            agent. Empty/None means no human members → no section.
        language: "cn" or "en".
        self_member_name: The current member's own name, used to tell
            a human-agent reader which entry in the roster is itself.
    """
    if not human_agent_names:
        return None
    names = sorted(human_agent_names)
    if language == "cn":
        if role == TeamRole.LEADER:
            body = _hitt_section_leader_cn(names)
        elif role == TeamRole.TEAMMATE:
            body = _hitt_section_teammate_cn(names)
        elif role == TeamRole.HUMAN_AGENT:
            body = _hitt_section_human_agent_cn(names, self_member_name)
        else:
            return None
    else:
        if role == TeamRole.LEADER:
            body = _hitt_section_leader_en(names)
        elif role == TeamRole.TEAMMATE:
            body = _hitt_section_teammate_en(names)
        elif role == TeamRole.HUMAN_AGENT:
            body = _hitt_section_human_agent_en(names, self_member_name)
        else:
            return None
    return PromptSection(
        name=TeamSectionName.HITT,
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


__all__ = [
    "TeamSectionName",
    "build_team_extra_section",
    "build_team_hitt_section",
    "build_team_info_section",
    "build_team_lifecycle_section",
    "build_team_members_section",
    "build_team_persona_section",
    "build_team_role_section",
    "build_team_workflow_section",
]
