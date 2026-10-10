# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Render memory-bank contents into the markdown block an agent/leader
would eventually see. Purely a read-only viewer/renderer — the one place
that turns bank state into the text ``TeamOfflineMemoryRail`` injects into
a member's system prompt.

Also the one place cross-bank (e.g. predefined vs. dynamic) comparison is
allowed: a `compare` report reads both banks but never writes to either —
the memory stores themselves stay strictly separated.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from openjiuwen.core.common.logging import memory_logger
from openjiuwen.extensions.context_evolver.offline_memory import bank_io


# One section per partner role_type this member has L2 memory about,
# combining the profile YAML and the correlation notes.
def format_for_agent(bank_dir: Path, role_type: str, limit: int | None = None) -> str:
    role_dir = bank_dir / role_type
    profiles = bank_io.load_yaml(role_dir / "teammate_profiles.yaml")
    corr_dir = role_dir / "correlations"

    partner_roles = set(profiles.keys())
    if corr_dir.exists():
        partner_roles |= {p.stem for p in corr_dir.glob("*.md")}

    if not partner_roles:
        return f"(no L2 memory yet for role_type={role_type!r} under {bank_dir})"

    # No support_count exists for L2 profiles the way it does for L3 playbook
    # items -- approximate "how well-observed this partner is" from how much
    # profile/notes content has accumulated, so a limit truncates the
    # thinnest partners first rather than the alphabetically-last ones.
    def _richness(partner: str) -> int:
        profile = profiles.get(partner, {})
        has_notes = (corr_dir / f"{partner}.md").exists()
        return len(profile.get("strengths", [])) + len(profile.get("weaknesses", [])) + (1 if has_notes else 0)

    ordered_partners = sorted(partner_roles, key=lambda p: (-_richness(p), p))
    if limit is not None:
        ordered_partners = ordered_partners[:limit]

    lines = ["## Team Insights (From Your Experience)", ""]
    for partner in ordered_partners:
        profile = profiles.get(partner, {})
        notes_path = corr_dir / f"{partner}.md"
        notes = bank_io.load_text(notes_path) if notes_path.exists() else ""
        if not profile and not notes:
            continue

        lines.append(f"### {partner}")
        if profile.get("communication_style"):
            lines.append(f"- **Style**: {profile['communication_style']}")
        if profile.get("reliability"):
            lines.append(f"- **Reliability**: {profile['reliability']}")
        if profile.get("strengths"):
            lines.append(f"- **Strengths**: {', '.join(profile['strengths'])}")
        if profile.get("weaknesses"):
            lines.append(f"- **Weaknesses**: {', '.join(profile['weaknesses'])}")
        if notes:
            lines.append(f"- **Collaboration notes**: {notes}")
        lines.append("")

    return "\n".join(lines).strip()


# What compositions worked before, what roles to consider adding, what to
# avoid — filtered to one task_category. Reads the ACE-style playbook
# (playbook_<task_category>.yaml). min_support is where "a single sighting
# isn't signal yet" lives -- a read-time filter, since writes are
# incremental and a support_count=1 item may just not have been reinforced
# yet. agent_change items carry trigger_condition/expertise/description as
# separate fields, but description (when present) already restates the
# trigger and reasoning in one coherent sentence -- prefer it alone over
# concatenating all three, which produces literal "when When ..."
# duplication.
def _render_agent_change_line(item: dict) -> str:
    role = item.get("recommended_role_type", "?")
    action = item.get("action", "include")
    support = item.get("support_count", 0)
    description = item.get("description", "").strip()
    if description:
        return f"- **{role}** ({action}, support={support}): {description}"
    trigger = item.get("trigger_condition", "(unspecified)")
    expertise = item.get("expertise", "")
    return f"- **{role}** ({action}, support={support}): when {trigger} — {expertise}"


def format_for_leader(bank_dir: Path, task_category: str, limit: int = 5, min_support: int = 2) -> str:
    team_dir = bank_dir / "team"
    compositions = [
        c for c in bank_io.load_jsonl(team_dir / "team_compositions.jsonl")
        if c.get("task_category") == task_category
    ]
    playbook = bank_io.load_yaml(team_dir / f"playbook_{task_category}.yaml")
    active_items = {
        item_id: item for item_id, item in playbook.get("items", {}).items()
        if item.get("status", "active") == "active" and item.get("support_count", 0) >= min_support
    }

    if not compositions and not active_items:
        return f"(no L3 memory yet for task_category={task_category!r} under {bank_dir})"

    success_counter: dict[tuple[str, ...], int] = {}
    for c in compositions:
        if c.get("outcome") == "success":
            key = tuple(sorted(c.get("roles", [])))
            success_counter[key] = success_counter.get(key, 0) + 1

    lines = ["## Team-Building Insights (From Past Tasks)", ""]

    if success_counter:
        lines.append("### Compositions That Worked")
        for roles, count in sorted(success_counter.items(), key=lambda kv: -kv[1])[:limit]:
            lines.append(f"- {', '.join(roles)} (x{count} successes)")
        lines.append("")

    # Only "agent_change" carries real include/avoid polarity -- workflow/
    # constitution/communication items are neutral guidance, never "avoid"
    # framed, so they get their own neutral heading instead of being lumped
    # under "Anti-Patterns (Avoid)" with the wrong polarity.
    agent_changes = {
        k: v for k, v in active_items.items() if v.get("category") == "agent_change" and v.get("action") != "avoid"
    }
    agent_avoid = {
        k: v for k, v in active_items.items() if v.get("category") == "agent_change" and v.get("action") == "avoid"
    }
    process_notes = {k: v for k, v in active_items.items() if v.get("category") != "agent_change"}

    if agent_changes:
        lines.append("### Spawning Strategy Guidance")
        ranked = sorted(agent_changes.items(), key=lambda kv: -kv[1].get("support_count", 0))[:limit]
        for _, item in ranked:
            lines.append(_render_agent_change_line(item))
        lines.append("")

    if agent_avoid:
        lines.append("### Roles to Avoid Spawning")
        ranked = sorted(agent_avoid.items(), key=lambda kv: -kv[1].get("support_count", 0))[:limit]
        for _, item in ranked:
            lines.append(_render_agent_change_line(item))
        lines.append("")

    if process_notes:
        lines.append("### Process & Workflow Notes")
        ranked = sorted(process_notes.items(), key=lambda kv: -kv[1].get("support_count", 0))[:limit]
        for _, item in ranked:
            lines.append(
                f"- [{item.get('category', '')}, support={item.get('support_count', 0)}] "
                f"{item.get('description', '')}"
            )

    return "\n".join(lines).strip()


def compare_modes(predefined_bank: Path, dynamic_bank: Path) -> str:
    """Read-only cross-bank report. Never writes to either bank."""
    lines = ["## Predefined vs Dynamic — Cross-Mode Comparison (read-only report)", ""]
    for label, bank in (("predefined", predefined_bank), ("dynamic", dynamic_bank)):
        comps = bank_io.load_jsonl(bank / "team" / "team_compositions.jsonl")
        if not comps:
            lines.append(f"### {label}\n(no team_compositions recorded yet)\n")
            continue
        n = len(comps)
        avg_compliance = sum(c.get("compliance", 0.0) for c in comps) / n
        n_success = sum(1 for c in comps if c.get("outcome") == "success")
        n_failure = sum(1 for c in comps if c.get("outcome") == "failure")
        n_timeout = sum(1 for c in comps if c.get("outcome") == "timeout")
        lines.append(
            f"### {label}\n"
            f"- episodes recorded: {n}\n"
            f"- avg compliance: {avg_compliance:.3f}\n"
            f"- success / failure / timeout: {n_success} / {n_failure} / {n_timeout}\n"
        )
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    p_agent = sub.add_parser("agent", help="Render L2 memory for one role_type")
    p_agent.add_argument("--bank-dir", required=True)
    p_agent.add_argument("--role", required=True)
    p_agent.add_argument(
        "--limit", type=int, default=None, help="Cap number of partner-role sections shown (default: no cap)"
    )

    p_leader = sub.add_parser("leader", help="Render L3 memory for one task_category")
    p_leader.add_argument("--bank-dir", required=True)
    p_leader.add_argument("--task-category", default="general")
    p_leader.add_argument("--limit", type=int, default=5)
    p_leader.add_argument(
        "--min-support", type=int, default=2, help="Hide playbook items reinforced fewer than N times"
    )

    p_compare = sub.add_parser("compare", help="Read-only predefined-vs-dynamic report")
    p_compare.add_argument("--predefined-bank", required=True)
    p_compare.add_argument("--dynamic-bank", required=True)

    return p


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.command == "agent":
        output = format_for_agent(Path(args.bank_dir), args.role, args.limit)
    elif args.command == "leader":
        output = format_for_leader(Path(args.bank_dir), args.task_category, args.limit, args.min_support)
    else:
        output = compare_modes(Path(args.predefined_bank), Path(args.dynamic_bank))
    memory_logger.info("%s", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
