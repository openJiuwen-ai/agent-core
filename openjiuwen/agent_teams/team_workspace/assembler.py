# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workspace assembler — the single spawn-time writer.

Every evolvable workspace file is written into the workspace at assembly time and
becomes evolvable: the framework baseline is written once (recording
``baseline_sha256``), and later edits by the evolution party are detected on
restart by body-hash divergence.

Layout:

    <team>/team-workspace/prompts/system/<name>.<lang>.md   # A-class (role subset + team shared, flattened)
    <team>/team-workspace/prompts/identity/team_card.md     # B-class team desc (no lang)
    <team>/team-workspace/prompts/identity/team_prompt.md   # B-class team prompt (no lang)
    <team>/team-workspace/prompts/tool/<domain>/<key>.<lang>.md  # C-class tool-level (domain mirrors descs/<lang>/<domain>/)
    <team>/team-workspace/prompts/tool/tool.param.<lang>.md # C-class param-level (JSON dict, flat at tool/ root)
    <member_dir>/prompts/identity/card.md                   # B-class member desc
    <member_dir>/prompts/identity/member_prompt.md          # B-class member prompt

Coverage rules — files are always written (baseline seeding); the
``evolution_enabled`` switch only gates the read side (whether file values
override code default / DB values at load time):

  - file missing → write baseline (``evolved=false``)
  - body hash == baseline → code default unchanged → keep;
    code default changed (framework upgrade) → overwrite with the new default
  - body hash != baseline (evolved) → never overwrite (evolution wins)
"""

from __future__ import annotations

import json
from pathlib import Path

from openjiuwen.agent_teams.paths import team_workspace_dir
from openjiuwen.agent_teams.team_workspace.frontmatter import (
    atomic_write,
    body_sha256,
    is_evolved,
    read_frontmatter,
    write_frontmatter,
)
from openjiuwen.agent_teams.team_workspace.layout import WorkspaceLayout
from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore
from openjiuwen.core.common.logging import team_logger


class WorkspaceAssembler:
    """Write A/B/C class workspace files into a member/team workspace at spawn time."""

    def __init__(self, store: WorkspaceStore | None = None) -> None:
        self._store = store or WorkspaceStore()

    # ── entry ──────────────────────────────────────────────────────────────

    def write_team_workspace(
        self,
        *,
        team_name: str,
        language: str = "cn",
        team_desc: str | None = None,
        team_prompt: str | None = None,
    ) -> None:
        """Write team-workspace files (A + C + B team-level) at team build time.

        Runs once when the team is established (``build_team`` success /
        leader recovery) — not tied to any member spawn. Writes:
          - A-class: scan ``prompts/<lang>/`` fully (no role filter, no
            hard-coded list — directory is the manifest);
          - C-class: scan ``descs/<lang>/`` (tool-level domain mirror +
            param-level JSON dict);
          - B-class team: ``team_card.md`` + ``team_prompt.md`` — values come
            from ``team_info`` (via ctx on the async assembly entry, U11) and
            are only written when a value exists (build_team inserts the row).
        Idempotent: existing dirs / baselines are reused, evolved files are
        never overwritten.
        """
        # A-class: full-directory scan, one file per template.
        self._write_prompt_templates(
            team_name=team_name,
            language=language,
        )
        # C-class: descs/ scan (tool-level) + STRINGS aggregate (param-level).
        self._write_tool_descs(
            team_name=team_name,
            language=language,
        )
        # B-class team: values come from the DB row, not the spec (U11).
        self._store.write_team_card(team_name, team_desc)
        self._store.write_team_prompt(team_name, team_prompt)

    def write_member_identity(
        self,
        *,
        team_name: str,
        member_name: str,
        member_desc: str | None = None,
        member_prompt: str | None = None,
    ) -> None:
        """Write B-class member identity files at member spawn time.

        ``card.md`` (body = desc only) + ``member_prompt.md``, values from the
        member's DB row (ctx.desc / ctx.prompt). ``display_name`` never rides
        the file.
        """
        self._store.write_card(team_name, member_name, member_desc)
        self._store.write_member_prompt(team_name, member_name, member_prompt)

    # ── A-class writing ────────────────────────────────────────────────────

    def _write_prompt_templates(self, *, team_name: str, language: str) -> None:
        """Write A-class templates into ``team-workspace/prompts/system/``.

        Full-directory scan of ``prompts/<lang>/`` — the directory is the
        manifest (no hard-coded role list): a new md file is written
        automatically, a removed one stops being written. The read side picks
        its own subset by role (``build_team_static_sections``), the write
        side does not filter.
        """
        prompts_dir = WorkspaceLayout.framework_prompts_dir(language)
        if not prompts_dir.is_dir():
            team_logger.warning("framework prompts dir missing: %s", prompts_dir)
            return
        for md_path in WorkspaceLayout.iter_framework_prompt_files(language):
            name = md_path.stem
            target = self._system_target(team_name, name, language)
            self._write_baseline(name, language, target)

    def _system_target(self, team_name: str, name: str, language: str) -> Path:
        """A-class target path: ``team-workspace/prompts/system/<name>.<lang>.md``."""
        return WorkspaceLayout.system_file(team_workspace_dir(team_name), name, language)

    def _write_baseline(self, name: str, language: str, target: Path) -> None:
        """Write the framework baseline for one A-class file (or upgrade it).

        Rules (``baseline_sha256`` driven):
          - target missing → write baseline
          - body hash == recorded baseline and framework default changed
            → overwrite with the new default
          - body hash == baseline and framework default unchanged → keep
          - body hash != baseline (evolved) → keep (evolution wins)

        Info log per outcome so the write side is auditable (which file was
        seeded / skipped / upgraded and why).
        """
        framework_text = self._framework_body(name, language)
        if framework_text is None:
            return
        if target.exists():
            try:
                text = target.read_text(encoding="utf-8")
            except OSError as exc:
                team_logger.warning("workspace file read %s failed: %s", target, exc)
                return
            try:
                meta, body = read_frontmatter(text)
            except ValueError:
                # Malformed frontmatter → invalid file: rebuild the baseline.
                team_logger.info("[workspace] %s malformed frontmatter — baseline rebuilt", target)
                meta = self._file_meta(name, body=framework_text, language=language)
                atomic_write(target, write_frontmatter(meta, framework_text))
                return
            if is_evolved(meta, body):
                # Hand-written (no baseline) or user-edited (hash diverged):
                # the evolution party's value wins, never overwrite.
                team_logger.info("[workspace] %s evolved — write skipped (evolution wins)", target)
                return
            if body_sha256(framework_text) == meta.get("baseline_sha256"):
                # Framework default unchanged — keep.
                return
            # Framework upgraded, file untouched: write the new default.
            team_logger.info("[workspace] %s framework default upgraded — baseline refreshed", target)
            meta = self._file_meta(name, body=framework_text, language=language)
            atomic_write(target, write_frontmatter(meta, framework_text))
            return
        team_logger.info("[workspace] %s missing — baseline seeded", target)
        meta = self._file_meta(name, body=framework_text, language=language)
        atomic_write(target, write_frontmatter(meta, framework_text))

    def _file_meta(self, name: str, *, body: str, language: str) -> dict:
        """Build the frontmatter meta for one A-class workspace file.

        ``language`` is stamped per A-class file (A/C classes split files
        by language — ``<name>.<lang>.md``).
        """
        return {
            "kind": "prompt",
            "name": name,
            "language": language,
            "baseline_sha256": body_sha256(body),
            "evolved": False,
        }

    def _framework_body(self, name: str, language: str) -> str | None:
        path = WorkspaceLayout.framework_prompt_file(name, language)
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            team_logger.warning("framework prompt missing: %s/%s", language, name)
            return None

    # ── C-class seeding (v2: directory scan, no hard-coded key list) ──────

    def _write_tool_descs(self, *, team_name: str, language: str) -> None:
        """Seed C-class tool descriptions into ``team-workspace/prompts/tool/``.

        Tool-level: scan ``locales/descs/<lang>/`` (every ``*.md``, including
        fragments and common) and write each under the same domain
        sub-directory as the source — ``<domain>/<key>.<lang>.md`` — so the
        layout mirrors ``descs/<lang>/<domain>/<key>.md``. Param-level:
        aggregate the STRINGS dict into a single JSON dict file
        ``tool.param.<lang>.md`` sitting flat at the ``tool/`` root. Never
        depends on ToolCard construction or capability gating.
        """
        tools_dir = self._tool_dir(team_name)
        self._write_tool_md(tools_dir, language)
        self._write_tool_params(tools_dir, language)

    def _tool_dir(self, team_name: str) -> Path:
        return WorkspaceLayout.tool_dir(team_workspace_dir(team_name))

    def _write_tool_md(self, tools_dir: Path, language: str) -> None:
        """Tool-level: copy every ``descs/<lang>/**/*.md``, preserving domain dirs.

        Each file lands at ``tool/<domain>/<key>.<lang>.md`` where ``<domain>``
        is the source sub-directory relative to ``descs/<lang>/`` (e.g.
        ``task/create_task.cn.md``, ``fragments/fork_usage.cn.md``). Only
        ``tool.param.<lang>.md`` sits flat at the ``tool/`` root.
        """
        descs_dir = WorkspaceLayout.framework_descs_dir(language)
        if not descs_dir.is_dir():
            team_logger.warning("descs dir missing: %s", descs_dir)
            return
        for md_path in WorkspaceLayout.iter_framework_desc_files(language):
            desc_key = md_path.stem
            try:
                framework_body = md_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                team_logger.warning("descs read %s failed: %s", md_path, exc)
                continue
            rel_dir = md_path.parent.relative_to(descs_dir)
            target = WorkspaceLayout.tool_md_file(tools_dir, rel_dir, desc_key, language)
            self._write_tool_md_baseline(desc_key, framework_body, target, language)

    def _write_tool_md_baseline(
        self,
        desc_key: str,
        framework_body: str,
        target: Path,
        language: str,
    ) -> None:
        """Write the baseline for one tool-level md (same rules as A-class)."""
        if target.exists():
            try:
                text = target.read_text(encoding="utf-8")
            except OSError as exc:
                team_logger.warning("workspace file read %s failed: %s", target, exc)
                return
            try:
                meta, body = read_frontmatter(text)
            except ValueError:
                team_logger.info("[workspace] %s malformed frontmatter — baseline rebuilt", target)
                meta = self._tool_md_meta(desc_key, framework_body, language)
                atomic_write(target, write_frontmatter(meta, framework_body))
                return  # malformed → invalid file, rebuild baseline
            if is_evolved(meta, body):
                team_logger.info("[workspace] %s evolved — write skipped (evolution wins)", target)
                return  # hand-written or user-edited → never overwrite
            if body_sha256(framework_body) == meta.get("baseline_sha256"):
                return  # framework unchanged — keep
            team_logger.info("[workspace] %s framework default upgraded — baseline refreshed", target)
            meta = self._tool_md_meta(desc_key, framework_body, language)
            atomic_write(target, write_frontmatter(meta, framework_body))
            return
        team_logger.info("[workspace] %s missing — baseline seeded", target)
        meta = self._tool_md_meta(desc_key, framework_body, language)
        atomic_write(target, write_frontmatter(meta, framework_body))

    @staticmethod
    def _tool_md_meta(desc_key: str, body: str, language: str) -> dict:
        return {
            "kind": "tool",
            "name": desc_key,
            "language": language,
            "baseline_sha256": body_sha256(body),
            "evolved": False,
        }

    def _write_tool_params(self, tools_dir: Path, language: str) -> None:
        """Param-level: write the full STRINGS dict into one JSON file.

        The dict is written verbatim — dotted keys (``"<desc_key>.<param>"``,
        e.g. ``create_task.task.title``) keep their original form; the read
        side splits on the first dot. One baseline hash for the whole file.
        """
        strings = _load_strings(language)
        body = json.dumps(strings, ensure_ascii=False, indent=2)
        target = WorkspaceLayout.tool_param_file(tools_dir, language)
        if target.exists():
            try:
                text = target.read_text(encoding="utf-8")
            except OSError as exc:
                team_logger.warning("workspace file read %s failed: %s", target, exc)
                return
            try:
                meta, existing_body = read_frontmatter(text)
            except ValueError:
                meta = None  # malformed → invalid file, rebuild below
            if meta is not None:
                if is_evolved(meta, existing_body):
                    return  # hand-written or user-edited → never overwrite
                if body_sha256(body) == meta.get("baseline_sha256"):
                    return  # framework unchanged — keep
        meta = {
            "kind": "tool_params",
            "name": "tool_params",
            "language": language,
            "baseline_sha256": body_sha256(body),
            "evolved": False,
        }
        atomic_write(target, write_frontmatter(meta, body))


def _load_strings(language: str) -> dict[str, str]:
    """Load the STRINGS dict for a language (cn default, en mirrored)."""
    if language == "en":
        from openjiuwen.agent_teams.tools.locales import en as mod
    else:
        from openjiuwen.agent_teams.tools.locales import cn as mod
    return dict(mod.STRINGS)


__all__ = ["WorkspaceAssembler"]
