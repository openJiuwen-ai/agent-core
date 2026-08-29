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
    <team>/team-workspace/prompts/tool/<domain>/<key>.<lang>.md
        # C-class tool-level (domain mirrors descs/<lang>/<domain>/)
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

from typing import TYPE_CHECKING

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
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.team_workspace.workspace_cache import WorkspaceCache


class WorkspaceAssembler:
    """Write A/B/C class workspace files into a member/team workspace at spawn time."""

    def __init__(
        self,
        store: WorkspaceStore | None = None,
        cache: WorkspaceCache | None = None,
    ) -> None:
        self._store = store or WorkspaceStore()
        self._cache = cache

    # ── entry ──────────────────────────────────────────────────────────────

    def write_system_and_tool_prompts(self, *, team_name: str, language: str = "cn") -> None:
        """Write the framework-source team-workspace files (A + C class).

        These are static copies of the framework defaults — system prompt
        templates (``prompts/system/<name>.<lang>.md``) and tool descriptions
        / params (``prompts/tool/...``). Their values come from the framework
        source, not the team DB row, so they do not depend on ``build_team``
        having run: writing them at ``coordination.start`` (before the first
        tool call) is safe and lets the read-side cache serve every member
        that runs in this process without re-writing.

        Idempotent: existing baselines are reused, evolved files are never
        overwritten, framework defaults that changed since the last write are
        upgraded in place (see ``_write_baseline``).
        """
        # System prompt templates: full-directory scan, one file per template.
        self._write_prompt_templates(
            team_name=team_name,
            language=language,
        )
        # Tool descriptions: descs/ scan (tool-level) + STRINGS aggregate
        # (param-level).
        self._write_tool_descs(
            team_name=team_name,
            language=language,
        )

    def write_team_identity(
        self,
        *,
        team_name: str,
        team_desc: str | None = None,
        team_prompt: str | None = None,
    ) -> None:
        """Write the team-level identity files (B class).

        ``team_card.md`` (body = desc) + ``team_prompt.md`` — values come from
        the team DB row (``build_team`` writes the row with ``desc``), so this
        belongs right after ``create_team`` succeeds. ``team_prompt`` is
        currently a write-only column (``build_team`` has no prompt argument,
        so the DB column stays NULL and ``write_team_prompt(None)`` is a
        no-op); the call stays for the day a prompt source is wired in.

        Idempotent and evolution-safe: ``_evolved_content`` never overwrites
        an evolved file. The final file state (body *and* ``updated_at``) is
        primed into the shared cache so the read side does not re-read the
        file.
        """
        desc_content = self._store.write_team_card(team_name, team_desc)
        prompt_content = self._store.write_team_prompt(team_name, team_prompt)
        if self._cache is not None:
            self._cache.fill_team_field("desc", desc_content)
            self._cache.fill_team_field("prompt", prompt_content)

    def write_member_identity(
        self,
        *,
        team_name: str,
        member_name: str,
        member_desc: str | None = None,
        member_prompt: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Write/protect B-class md, prime cache, return bodies.

        The caller (``spawn_member`` / ``_assemble_member_workspace``) is
        responsible for ensuring the member workspace root exists first via
        ``prepare_member_workspace``; this method only writes ``card.md`` /
        ``member_prompt.md`` through that path and primes the shared cache. It
        never creates the workspace directory itself, so it cannot race the
        binder's link creation. ``card.md`` (body = desc only) +
        ``member_prompt.md``, values from the member's DB row (ctx.desc /
        ctx.prompt). ``display_name`` never rides the file. The final file state
        (body *and* ``updated_at``) is primed into the shared cache so the read
        side does not re-read the file.

        Returns ``(desc_body, prompt_body)`` — the evolved md body when the file
        was already evolved (write skipped, evolution wins), the newly-written
        baseline body when it was written, or ``None`` when the value was empty.
        Callers (``spawn_member``) use these to write the db row with the latest
        identity known at spawn time instead of the spec baseline, closing the
        first-roster race where the roster is delivered before member spawn.
        """
        desc_content = self._store.write_card(team_name, member_name, member_desc)
        prompt_content = self._store.write_member_prompt(team_name, member_name, member_prompt)
        if self._cache is not None:
            self._cache.fill_member_field(member_name, "desc", desc_content)
            self._cache.fill_member_field(member_name, "prompt", prompt_content)
        return (
            desc_content.body if desc_content is not None else None,
            prompt_content.body if prompt_content is not None else None,
        )

    # ── write-side cache priming ──────────────────────────────────────────
    #
    # Each write branch already knows the body the file ended up with (the
    # framework default it just wrote, or the evolved value it kept). Prime the
    # shared cache with it so the read side never re-reads / re-hashes the same
    # file. When ``cache`` is None (evolution disabled or single-agent) the
    # priming is a no-op and the read side falls back to its lazy miss.

    def _fill_template(self, name: str, body: str) -> None:
        if self._cache is not None:
            self._cache.fill_template(name, body)

    def _fill_tool_md(self, desc_key: str, body: str) -> None:
        if self._cache is not None:
            self._cache.fill_tool_md(desc_key, body)

    def _fill_tool_params(self, data: dict[str, str]) -> None:
        if self._cache is None:
            return
        for str_key, text in data.items():
            if "." not in str_key or not isinstance(text, str):
                continue
            desc_key, param = str_key.split(".", 1)
            self._cache.fill_tool_param(desc_key, param, text)

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

    @staticmethod
    def _system_target(team_name: str, name: str, language: str) -> Path:
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

        Cache-hit guard: a teammate sharing the leader's cache instance sees
        the leader already primed this name, so the whole read-write-judge
        pass is skipped. The cache is the single read/write entry point: when
        a value is resident it is the truth for the whole session, and a
        teammate re-entering the write path must not re-read the md file
        (which would pick up an evolution-party edit made mid-session and
        pollute the stable cache). The guard is on resident state, not on
        role, so the first writer wins regardless of who it is.
        """
        framework_text = self._framework_body(name, language)
        if framework_text is None:
            return
        if self._cache is not None and self._cache.has_template(name):
            return  # leader already primed — teammate keeps the cache stable
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
                self._fill_template(name, framework_text)
                return
            if is_evolved(meta, body):
                # Hand-written (no baseline) or user-edited (hash diverged):
                # the evolution party's value wins, never overwrite.
                team_logger.info("[workspace] %s evolved — write skipped (evolution wins)", target)
                self._fill_template(name, body)
                return
            if body_sha256(framework_text) == meta.get("baseline_sha256"):
                # Framework default unchanged — keep.
                self._fill_template(name, framework_text)
                return
            # Framework upgraded, file untouched: write the new default.
            team_logger.info("[workspace] %s framework default upgraded — baseline refreshed", target)
            meta = self._file_meta(name, body=framework_text, language=language)
            atomic_write(target, write_frontmatter(meta, framework_text))
            self._fill_template(name, framework_text)
            return
        team_logger.info("[workspace] %s missing — baseline seeded", target)
        meta = self._file_meta(name, body=framework_text, language=language)
        atomic_write(target, write_frontmatter(meta, framework_text))
        self._fill_template(name, framework_text)

    @staticmethod
    def _file_meta(name: str, *, body: str, language: str, now: int | None = None) -> dict:
        """Build the frontmatter meta for one A-class workspace file.

        ``language`` is stamped per A-class file (A/C classes split files
        by language — ``<name>.<lang>.md``). ``now`` is the write timestamp
        stamped into ``updated_at`` (defaults to current time).
        """
        return {
            "kind": "prompt",
            "name": name,
            "language": language,
            "baseline_sha256": body_sha256(body),
            "evolved": False,
            "updated_at": now if now is not None else get_current_time(),
        }

    @staticmethod
    def _framework_body(name: str, language: str) -> str | None:
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

        Cache-hit guard: once the leader's pass has primed every C-class
        entry (``mark_tools_loaded``), a teammate sharing the same cache
        skips the whole pass. The C-class scan is one unit — either every
        tool md / param is primed or none is — so the single flag covers it.
        """
        if self._cache is not None and self._cache.is_tools_loaded():
            return  # leader already primed the C-class tree — teammate skips
        tools_dir = self._tool_dir(team_name)
        self._write_tool_md(tools_dir, language)
        self._write_tool_params(tools_dir, language)
        if self._cache is not None:
            self._cache.mark_tools_loaded()

    @staticmethod
    def _tool_dir(team_name: str) -> Path:
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
        if self._cache is not None and self._cache.has_tool_md(desc_key):
            return  # leader already primed — teammate keeps the cache stable
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
                self._fill_tool_md(desc_key, framework_body)
                return  # malformed → invalid file, rebuild baseline
            if is_evolved(meta, body):
                team_logger.info("[workspace] %s evolved — write skipped (evolution wins)", target)
                self._fill_tool_md(desc_key, body)
                return  # hand-written or user-edited → never overwrite
            if body_sha256(framework_body) == meta.get("baseline_sha256"):
                self._fill_tool_md(desc_key, framework_body)
                return  # framework unchanged — keep
            team_logger.info("[workspace] %s framework default upgraded — baseline refreshed", target)
            meta = self._tool_md_meta(desc_key, framework_body, language)
            atomic_write(target, write_frontmatter(meta, framework_body))
            self._fill_tool_md(desc_key, framework_body)
            return
        team_logger.info("[workspace] %s missing — baseline seeded", target)
        meta = self._tool_md_meta(desc_key, framework_body, language)
        atomic_write(target, write_frontmatter(meta, framework_body))
        self._fill_tool_md(desc_key, framework_body)

    @staticmethod
    def _tool_md_meta(desc_key: str, body: str, language: str, *, now: int | None = None) -> dict:
        return {
            "kind": "tool",
            "name": desc_key,
            "language": language,
            "baseline_sha256": body_sha256(body),
            "evolved": False,
            "updated_at": now if now is not None else get_current_time(),
        }

    def _write_tool_params(self, tools_dir: Path, language: str) -> None:
        """Param-level: write the full STRINGS dict into one JSON file.

        The dict is written verbatim — dotted keys (``"<desc_key>.<param>"``,
        e.g. ``create_task.task.title``) keep their original form; the read
        side splits on the first dot. One baseline hash for the whole file.
        The final dict (framework STRINGS, or the evolved JSON when the file is
        protected) is primed into the cache so the read side does not re-read.
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
            existing_body = ""
            try:
                meta, existing_body = read_frontmatter(text)
            except ValueError:
                meta = None  # malformed → invalid file, rebuild below
            if meta is not None:
                if is_evolved(meta, existing_body):
                    # hand-written or user-edited → never overwrite
                    try:
                        evolved_data = json.loads(existing_body)
                    except json.JSONDecodeError:
                        evolved_data = None
                    if isinstance(evolved_data, dict):
                        self._fill_tool_params(evolved_data)
                    team_logger.info("[workspace] %s evolved — write skipped (evolution wins)", target)
                    return
                if body_sha256(body) == meta.get("baseline_sha256"):
                    # framework unchanged — keep
                    self._fill_tool_params(strings)
                    return
        meta = {
            "kind": "tool_params",
            "name": "tool_params",
            "language": language,
            "baseline_sha256": body_sha256(body),
            "evolved": False,
            "updated_at": get_current_time(),
        }
        atomic_write(target, write_frontmatter(meta, body))
        self._fill_tool_params(strings)


def _load_strings(language: str) -> dict[str, str]:
    """Load the STRINGS dict for a language (cn default, en mirrored)."""
    if language == "en":
        from openjiuwen.agent_teams.tools.locales import en as mod
    else:
        from openjiuwen.agent_teams.tools.locales import cn as mod
    return dict(mod.STRINGS)


__all__ = ["WorkspaceAssembler"]
