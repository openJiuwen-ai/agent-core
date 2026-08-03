# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


"""Team-state message bodies delivered into a member's conversation history.

Counterpart to :mod:`openjiuwen.agent_teams.prompts.sections`: that module
assembles ``PromptSection`` objects for the system prompt, this one renders the
team state a member is *told about over time* — its own identity, the team
metadata, and the roster.

These bodies do not belong in the system prompt (they either differ between
members or appear only once the team exists) and they no longer ride the
per-round prompt attachment either: an attachment is re-encoded on every single
model call and can never be served from the KV cache. Written into the
conversation history once, at the moment the data first appears, the same tokens
are encoded once and cached from then on.

Delivery, probing and the persisted baseline live in
``openjiuwen.agent_teams.team_context``; this module is pure rendering plus the
roster diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openjiuwen.agent_teams.schema.team import TeamRole

# ---------------------------------------------------------------------------
# Bilingual labels (message bodies only; section headings live in sections.py)
# ---------------------------------------------------------------------------

_LABELS: dict[str, dict[str, str]] = {
    "cn": {
        "identity_heading": "# 成员身份",
        "member_name_line": "你的 member_name",
        "display_name_line": "你的 display_name",
        "member_workspace_line": "你的私有工作区",
        "member_workspace_purpose": (
            "存放你自己的产物、记忆与技能视图；团队共享文件走团队共享工作空间，不要放这里，"
            "也不要把新 skill 创建到这里"
        ),
        "private_prompt_heading": "## 私有工作约定",
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
        "roster_change_heading": "# 成员变更",
        "roster_joined": "加入",
        "roster_left": "退出",
        "roster_updated": "信息更新",
    },
    "en": {
        "identity_heading": "# Member Identity",
        "member_name_line": "Your member_name",
        "display_name_line": "Your display_name",
        "member_workspace_line": "Your private workspace",
        "member_workspace_purpose": (
            "Holds your own artifacts, memory and skills view. Team-shared files belong in the "
            "team shared workspace, not here, and new skills must not be created here either"
        ),
        "private_prompt_heading": "## Private Working Agreement",
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
        "roster_change_heading": "# Roster Change",
        "roster_joined": "joined",
        "roster_left": "left",
        "roster_updated": "updated",
    },
}


def labels_for(language: str) -> dict[str, str]:
    """Return the label table for ``language``, falling back to ``cn``."""
    return _LABELS.get(language, _LABELS["cn"])


# ---------------------------------------------------------------------------
# Roster diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RosterDelta:
    """What changed between two roster snapshots of the same member's peers."""

    joined: list[dict[str, str]] = field(default_factory=list)
    left: list[dict[str, str]] = field(default_factory=list)
    changed: list[dict[str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return True when nothing changed and there is nothing to announce."""
        return not (self.joined or self.left or self.changed)


# Fields whose change is worth announcing. ``member_name`` is the identity, so
# it is the diff key rather than a tracked field; runtime status is deliberately
# excluded — it churns constantly and is not roster membership.
_TRACKED_MEMBER_FIELDS = ("display_name", "desc", "role")


def diff_roster(
    old: list[dict[str, str]] | None,
    new: list[dict[str, str]] | None,
) -> RosterDelta:
    """Diff two peer rosters keyed by ``member_name``.

    Args:
        old: The previously announced roster; ``None`` is treated as empty.
        new: The current roster.

    Returns:
        A :class:`RosterDelta` listing members that joined, left, or had one of
        ``display_name`` / ``desc`` / ``role`` changed.
    """
    old_by_name = {m.get("member_name", ""): m for m in (old or [])}
    new_by_name = {m.get("member_name", ""): m for m in (new or [])}
    joined = [m for name, m in new_by_name.items() if name not in old_by_name]
    left = [m for name, m in old_by_name.items() if name not in new_by_name]
    changed = []
    for name, member in new_by_name.items():
        previous = old_by_name.get(name)
        if previous is None:
            continue
        if any(member.get(f, "") != previous.get(f, "") for f in _TRACKED_MEMBER_FIELDS):
            changed.append(member)
    return RosterDelta(joined=joined, left=left, changed=changed)


# ---------------------------------------------------------------------------
# Message bodies
# ---------------------------------------------------------------------------


def format_member_line(
    member: dict[str, str],
    *,
    mark_humans: bool = False,
    prefix: str | None = None,
) -> str:
    """Render one roster row.

    Args:
        member: Member mapping with ``member_name`` / ``display_name`` and
            optional ``desc`` / ``role``.
        mark_humans: When True, append ``[human]`` to human-agent members.
        prefix: Optional bracketed marker placed before the fields, used by the
            delta body to say whether the member joined / left / was updated.

    Returns:
        A single ``- ...`` markdown list row.
    """
    member_name = member.get("member_name", "")
    display_name = member.get("display_name", "unknown")
    desc = member.get("desc", "")
    head = f"- [{prefix}] " if prefix else "- "
    line = f"{head}member_name={member_name} display_name={display_name}"
    if mark_humans and member.get("role") == TeamRole.HUMAN_AGENT.value:
        line += " [human]"
    if desc:
        line += f" :: {desc}"
    return line


def _parenthesized(text: str, language: str) -> str:
    """Wrap a trailing clause in the brackets that language actually uses."""
    if language == "cn":
        return f"（{text}）"
    return f" ({text})"


def build_identity_text(
    *,
    member_name: str | None,
    display_name: str | None = None,
    member_workspace_path: str | None = None,
    member_prompt: str | None = None,
    language: str = "cn",
) -> str | None:
    """Render the member's own identity body.

    Carries everything specific to this one member: the two names it is known
    by and its private working agreement (the member-private counterpart to the
    public ``desc``, never shared into any peer's roster). All are fixed at
    spawn time and constant afterwards, so this body is delivered exactly once.

    ``display_name`` is here because peers' rosters list members by *both*
    names: without its own label a member cannot tell which roster row is
    itself, nor refer to itself the way the rest of the team does.

    ``member_workspace_path`` is the member's own artifact directory. It is
    per-member and constant, exactly like the names, so it belongs in the same
    body rather than in a channel of its own.

    Args:
        member_name: Semantic member identifier.
        display_name: Human-readable label; blank drops that line.
        member_workspace_path: The member's private workspace; blank drops
            that line.
        member_prompt: The member's private working agreement; blank (a member
            spawned without one) drops that subsection.
        language: Body language ('cn' or 'en').

    Returns:
        The rendered body, or ``None`` when no field is set.
    """
    private_prompt = member_prompt.strip() if member_prompt else ""
    label = display_name.strip() if display_name else ""
    workspace = member_workspace_path.strip() if member_workspace_path else ""
    if not any((member_name, label, workspace, private_prompt)):
        return None
    labels = labels_for(language)
    lines = [labels["identity_heading"], ""]
    if member_name:
        lines.append(f"{labels['member_name_line']}: {member_name}")
    if label:
        lines.append(f"{labels['display_name_line']}: {label}")
    if workspace:
        purpose = _parenthesized(labels["member_workspace_purpose"], language)
        lines.append(f"{labels['member_workspace_line']}: `{workspace}`{purpose}")
    if private_prompt:
        lines.extend(["", labels["private_prompt_heading"], "", private_prompt])
    return "\n".join(lines) + "\n"


def build_team_info_text(
    *,
    team_info: dict[str, Any] | None,
    team_workspace_mount: str | None = None,
    team_workspace_path: str | None = None,
    language: str = "cn",
) -> str | None:
    """Render the team metadata body.

    Args:
        team_info: Mapping with optional ``team_name``, ``display_name`` and
            ``desc`` keys (the shape returned by ``TeamBackend.get_team_info``).
        team_workspace_mount: Agent-relative mount point of the team shared
            workspace (e.g. ``.team/{team_name}/``). When set, the body appends
            a bullet telling the LLM how to read/write team-shared files from
            its own workspace.
        team_workspace_path: Absolute path of the team shared workspace on disk.
            Appended as a nested bullet when ``team_workspace_mount`` is
            provided, or as a standalone bullet when only the path is known.
        language: Body language.

    Returns:
        The rendered body, or ``None`` when no usable field is present.
    """
    labels = labels_for(language)
    team_name = team_info.get("team_name") if team_info else None
    display_name = team_info.get("display_name") if team_info else None
    desc = team_info.get("desc") if team_info else None
    mount = team_workspace_mount.strip() if team_workspace_mount else ""
    path = team_workspace_path.strip() if team_workspace_path else ""
    if not any([team_name, display_name, desc, mount, path]):
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
        if path:
            lines.append(f"  - {labels['team_workspace_abs']}: `{path}`")
    elif path:
        lines.append(f"- {labels['team_workspace']}: `{path}`")
        lines.append(f"  - {labels['team_workspace_purpose']}")
    return "\n".join(lines) + "\n"


def build_roster_snapshot_text(
    *,
    members: list[dict[str, str]] | None,
    mark_humans: bool = False,
    language: str = "cn",
) -> str | None:
    """Render the full peer roster body.

    Sent once, the first time this member has any peer to be told about; every
    later change is announced as a delta instead (see
    :func:`build_roster_delta_text`).

    Args:
        members: Peer members (the caller excludes the member itself).
        mark_humans: When True, tag ``role == human_agent`` entries ``[human]``.
            The caller gates this on the viewer role +
            ``expose_human_agents_to_teammates`` so a teammate's peers stay
            role-anonymous by default.
        language: Body language.

    Returns:
        The rendered body, or ``None`` when there is no peer to list.
    """
    if not members:
        return None
    labels = labels_for(language)
    rows = [format_member_line(member, mark_humans=mark_humans) for member in members]
    return labels["members_heading"] + "\n\n" + "\n".join(rows) + "\n"


def build_roster_delta_text(
    *,
    delta: RosterDelta,
    mark_humans: bool = False,
    language: str = "cn",
) -> str | None:
    """Render a roster change body listing only what moved.

    Args:
        delta: The diff produced by :func:`diff_roster`.
        mark_humans: Same gating as :func:`build_roster_snapshot_text`.
        language: Body language.

    Returns:
        The rendered body, or ``None`` when the delta is empty.
    """
    if delta.is_empty():
        return None
    labels = labels_for(language)
    rows: list[str] = []
    for member in delta.joined:
        rows.append(format_member_line(member, mark_humans=mark_humans, prefix=labels["roster_joined"]))
    for member in delta.left:
        rows.append(format_member_line(member, mark_humans=mark_humans, prefix=labels["roster_left"]))
    for member in delta.changed:
        rows.append(format_member_line(member, mark_humans=mark_humans, prefix=labels["roster_updated"]))
    return labels["roster_change_heading"] + "\n\n" + "\n".join(rows) + "\n"


__all__ = [
    "RosterDelta",
    "build_identity_text",
    "build_roster_delta_text",
    "build_roster_snapshot_text",
    "build_team_info_text",
    "diff_roster",
    "format_member_line",
    "labels_for",
]
