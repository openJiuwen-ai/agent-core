# coding: utf-8

"""TeamRail — decomposes team policy into ordered PromptSections.

Replaces the legacy monolithic ``build_system_prompt`` (which packed
role policy, workflow, lifecycle, persona, team info and member
relationships into a single IDENTITY blob) with one PromptSection per
content category.  Each section is registered against the shared
``SystemPromptBuilder`` before every model call so the team-specific
slices line up with the harness sections (safety, tools, memory,
workspace, ...) by priority.

Section layout (aligned with ``prompt_design.md``):

  P:11  team_role        — member id + role policy (always)
  P:13  team_workflow    — leader workflow (LEADER only)
  P:14  team_lifecycle   — team lifecycle policy (LEADER only)
  P:15  team_persona     — current persona (when persona is set)
  P:16  team_extra       — user-supplied base prompt (when set)
  P:65  team_info        — team metadata (after capabilities)
  P:66  team_members     — relationships with peers
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from openjiuwen.agent_teams.agent.prompts import load_template
from openjiuwen.agent_teams.agent.team_section_cache import MtimeSectionCache
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.team import TeamBackend


# ---------------------------------------------------------------------------
# Section name constants
# ---------------------------------------------------------------------------


class TeamSectionName:
    """Centralized section names owned by ``TeamRail``."""

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
        "2. 可以通过 `update_task(task_id=..., assignee=\"<human_member_name>\")` "
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
        "# HITT — 你是团队里的人类成员\n\n"
        f"{roster}。\n"
        f"{peers}"
        "你是团队里真实人类操作者的代理，与 leader、teammate 平等。\n"
        "- 你只能通过 `send_message` 与团队交互；没有 `claim_task`、"
        "`update_task`、`spawn_member` 等工具。\n"
        "- Leader 通过 `update_task` 把任务指派给你后，你需要以对话方式"
        "与团队沟通进展；完成后通过 `send_message` 告知 leader。\n"
        "- 发送给你的消息一律自动标记已读，不会堆积未读。\n"
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
        "# HITT — You are a human member\n\n"
        f"{roster}.\n"
        f"{peers}"
        "You represent the human operator on this team, equal in "
        "standing with the leader and teammates.\n"
        "- Your only tool is `send_message`; you do not have "
        "`claim_task`, `update_task`, `spawn_member`, etc.\n"
        "- When the leader assigns you a task via `update_task`, reply "
        "and coordinate through `send_message`. Announce completion "
        "through `send_message` too.\n"
        "- Every message addressed to you is auto-marked-read; there is "
        "no unread backlog on your side.\n"
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


# ---------------------------------------------------------------------------
# Rail
# ---------------------------------------------------------------------------

_DYNAMIC_SECTION_NAMES: tuple[str, ...] = (
    TeamSectionName.INFO,
    TeamSectionName.MEMBERS,
)


class TeamRail(DeepAgentRail):
    """Inject team-specific PromptSections into the system prompt builder.

    Sections fall into two buckets:

      * **Static** -- role, workflow, lifecycle, persona, extra.  Built
        once at ``__init__`` from constructor arguments and re-added to
        the builder on every ``before_model_call`` (cheap dict insert).
      * **Dynamic** -- ``team_info`` and ``team_members``.  Backed by
        :class:`MtimeSectionCache` instances that probe the team
        database for an ``updated_at`` change before re-running the
        full fetch.  This lets the rail pick up newly spawned members
        on the next LLM call without paying for a full table read on
        every call.

    When ``team_backend`` is ``None`` (e.g. unit tests that only care
    about static content) the dynamic caches are skipped entirely and
    the rail behaves like the previous static-only implementation.
    """

    priority = 12

    def __init__(
        self,
        *,
        role: TeamRole,
        persona: str,
        member_name: str | None = None,
        lifecycle: str = "temporary",
        teammate_mode: str = "build_mode",
        language: str = "cn",
        team_mode: str = "default",
        base_prompt: str | None = None,
        team_workspace_mount: str | None = None,
        team_workspace_path: str | None = None,
        team_backend: "TeamBackend | None" = None,
    ) -> None:
        super().__init__()
        self._language = language
        self._member_name = member_name
        self._team_backend = team_backend
        self._team_workspace_mount = team_workspace_mount
        self._team_workspace_path = team_workspace_path
        self.system_prompt_builder = None

        # Static sections built once and reused on every call. The HITT
        # section receives the roster snapshot captured at rail-init
        # time; dynamic additions to the human-agent set (rare — only
        # the build_team path adds them) take effect on the next rail
        # rebuild.
        human_names: list[str] = sorted(team_backend.human_agent_names()) if team_backend else []
        self._static_sections: list[PromptSection] = self._build_static_sections(
            role=role,
            persona=persona,
            member_name=member_name,
            lifecycle=lifecycle,
            teammate_mode=teammate_mode,
            team_mode=team_mode,
            base_prompt=base_prompt,
            human_agent_names=human_names,
        )

        # Dynamic section caches: keyed on table-level mtime probes so
        # repeated calls pay only for the cheap probe + dict insert.
        self._info_cache: MtimeSectionCache | None = None
        self._members_cache: MtimeSectionCache | None = None
        if team_backend is not None:
            self._info_cache = MtimeSectionCache(
                probe=team_backend.get_team_updated_at,
                fetch_and_build=self._fetch_and_build_info_section,
            )
            self._members_cache = MtimeSectionCache(
                probe=team_backend.get_members_max_updated_at,
                fetch_and_build=self._fetch_and_build_members_section,
            )

    # -- Lifecycle hooks ------------------------------------------------------

    def init(self, agent: Any) -> None:
        """Cache the agent's shared prompt builder."""
        super().init(agent)
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:
        """Remove all team sections from the shared builder."""
        if self.system_prompt_builder is not None:
            for section in self._static_sections:
                self.system_prompt_builder.remove_section(section.name)
            for name in _DYNAMIC_SECTION_NAMES:
                self.system_prompt_builder.remove_section(name)
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject static sections + refresh dynamic ones before each call."""
        if self.system_prompt_builder is None:
            return

        for section in self._static_sections:
            self.system_prompt_builder.add_section(section)

        if self._info_cache is not None:
            info_section = await self._info_cache.refresh()
            if info_section is not None:
                self.system_prompt_builder.add_section(info_section)

        if self._members_cache is not None:
            members_section = await self._members_cache.refresh()
            if members_section is not None:
                self.system_prompt_builder.add_section(members_section)

    # -- Internal -------------------------------------------------------------

    def _build_static_sections(
        self,
        *,
        role: TeamRole,
        persona: str,
        member_name: str | None,
        lifecycle: str,
        teammate_mode: str,
        team_mode: str,
        base_prompt: str | None,
        human_agent_names: list[str],
    ) -> list[PromptSection]:
        """Construct the never-changing sections once at rail init time."""
        builders = [
            build_team_role_section(
                role=role,
                member_name=member_name,
                teammate_mode=teammate_mode,
                language=self._language,
            ),
            build_team_hitt_section(
                role=role,
                human_agent_names=human_agent_names,
                language=self._language,
                self_member_name=member_name,
            ),
            build_team_workflow_section(
                role=role,
                team_mode=team_mode,
                language=self._language,
            ),
            build_team_lifecycle_section(
                role=role,
                lifecycle=lifecycle,
                language=self._language,
            ),
            build_team_persona_section(
                persona=persona,
                language=self._language,
            ),
            build_team_extra_section(
                base_prompt=base_prompt,
                language=self._language,
            ),
        ]
        return [section for section in builders if section is not None]

    async def _fetch_and_build_info_section(self) -> Optional[PromptSection]:
        """Reload team metadata from DB and rebuild the info section."""
        info = await self._team_backend.get_team_info()
        info_dict: dict[str, Any] | None = None
        if info is not None:
            info_dict = {
                "team_name": info.team_name,
                "display_name": info.display_name,
                "desc": info.desc or "",
            }
        return build_team_info_section(
            team_info=info_dict,
            team_workspace_mount=self._team_workspace_mount,
            team_workspace_path=self._team_workspace_path,
            language=self._language,
        )

    async def _fetch_and_build_members_section(self) -> Optional[PromptSection]:
        """Reload member roster from DB and rebuild the members section."""
        members = await self._team_backend.list_members()
        members_list: list[dict[str, str]] | None = None
        if members:
            members_list = [
                {
                    "member_name": m.member_name,
                    "display_name": m.display_name,
                    "desc": m.desc or "",
                }
                for m in members
            ]
        return build_team_members_section(
            team_members=members_list,
            self_member_name=self._member_name,
            language=self._language,
        )


__all__ = [
    "TeamRail",
    "TeamSectionName",
    "build_team_role_section",
    "build_team_hitt_section",
    "build_team_workflow_section",
    "build_team_lifecycle_section",
    "build_team_persona_section",
    "build_team_extra_section",
    "build_team_info_section",
    "build_team_members_section",
]
