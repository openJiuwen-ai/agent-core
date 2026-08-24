# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-team evolvable-workspace cache.

One ``WorkspaceCache`` instance carries all three evolvable text classes:

- A-class prompt templates (``prompts/system/<name>.<lang>.md``) → raw body.
- B-class static fields (``card.md`` desc / ``member_prompt.md`` /
  ``team_card.md`` / ``team_prompt.md``) → str (or ``None``).
- C-class tool descriptions (``prompts/tool/<domain>/<key>.<lang>.md``
  tool-level md + ``tool.param.<lang>.md`` JSON dict) → raw body / entry.

Fill model — **lazy get + write-side priming**: a ``get*`` call is a dict
lookup; on miss the file is read once (via the ``WorkspaceStore``), the value
stored in the dict, and returned. The assembler also *primes* the cache from
the write side (``fill_*``) with the latest body it already holds, so the
first ``get*`` after a write is a dict hit with zero file IO. The cache
**never** proactively scans / builds: no ``build``, no ``rebuild``, no roster
argument, no leader-only gate.

Invalidation — **Runner finally**: ``invalidate()`` drops every resident
value (clears the dicts) without touching the filesystem. Called from
``RuntimeManager.finalize`` on the pause path so the *next* run's first
``get*`` re-reads the md files the evolution party may have edited in
between. The stop path needs no invalidation — the whole agent (and with it
the cache instance) is dropped from the pool and GC'd.

Object lifecycle: one instance per team, attached to the
``TeamWorkspaceManager`` at configure time (new-instance paths only —
``CREATE`` / ``NEW_TEAM_IN_SESSION`` / ``COLD_RECOVER``). The
``RESUME_FROM_PAUSE`` path reuses the agent (and thus the manager + cache
instance); the previous run's ``finalize`` already invalidated the dict, so
the resumed run's first ``get*`` re-reads fresh values. In-process
teammates share the leader's single manager (and cache) by reference via
``share_workspace_cache_with`` — they never build their own.

Team name + language live on this one instance; consumers only talk to this
class. The instance exists only when the evolution mechanism is enabled
(``TeamAgentSpec.evolution_enabled``) — when disabled the assembler never
writes files and the manager carries no cache, so every read falls back to
code defaults / raw DB columns directly.

``display_name`` never rides the overlay — it is not file-evolvable and
always falls back to the DB column. No inheritance, no generics, no
sub-classing: the three classes are just dict fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from openjiuwen.agent_teams.paths import team_member_workspace_dir
from openjiuwen.agent_teams.team_workspace.file_content import FileContent, parse_file_content
from openjiuwen.agent_teams.team_workspace.layout import WorkspaceLayout
from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.prompt import PromptTemplate


class WorkspaceCache:
    """Per-team workspace evolvable-value cache, one instance for A/B/C.

    Lazy get (miss reads the file once, then hits) is the only fill path;
    ``invalidate()`` is the only drop path. No ``build``, no ``rebuild``,
    no roster — the cache never scans the workspace up front.

    The cache stores the *latest* value, not an "evolved overlay": the write
    side (assembler) primes it with whatever body the file ended up with
    (framework default or an evolved edit), and the read side serves that body
    on a dict hit. ``None`` means the file is missing — the caller falls back
    to the code default / raw DB column.
    """

    def __init__(
        self,
        store: WorkspaceStore,
        team_name: str,
        *,
        language: str = "cn",
    ) -> None:
        self._store = store
        self._team_name = team_name
        self._lang = language
        # A-class: template name → PromptTemplate (raw body).
        self._template_values: dict[str, PromptTemplate | None] = {}
        # B-class: (member_name, field) → FileContent | None. One object per
        # file carries both the body (overlay value) and the frontmatter
        # ``updated_at`` (roster mtime probe), so a single file read fills
        # both concerns. ``None`` marks "no file value" (missing file) so
        # the miss path is not retried per call (the caller falls back to
        # the DB column / code default).
        self._member_values: dict[tuple[str, str], FileContent | None] = {}
        # B-class team: field → FileContent | None (same model as member).
        self._team_values: dict[str, FileContent | None] = {}
        # C-class tool-level: desc_key → raw body (unrendered {{slot}}).
        self._tool_md_values: dict[str, str | None] = {}
        # C-class param-level: (desc_key, param) → text.
        self._tool_params: dict[tuple[str, str], str | None] = {}
        # C-class tree scanned once per run (gates ``_load_tools``).
        self._tools_loaded: bool = False

    # ── A-class ────────────────────────────────────────────────────────────

    def get_template(self, name: str) -> PromptTemplate | None:
        """Return the latest A-class template for ``name``, or ``None``.

        ``None`` means the file is missing — the caller falls back to the
        framework ``load_template`` (see ``make_template_loader``). Lazy: the
        first call for ``name`` reads ``prompts/system/<name>.<lang>.md``;
        later calls in the same run are dict hits.
        """
        if name in self._template_values:
            return self._template_values[name]
        body = self._read_body(
            WorkspaceLayout.system_file(self._store.team_workspace_root(self._team_name), name, self._lang)
        )
        value = PromptTemplate(name=name, content=body) if body is not None else None
        self._template_values[name] = value
        return value

    # ── B-class ────────────────────────────────────────────────────────────

    def get_member_field(
        self,
        member_name: str,
        field: Literal["desc", "prompt"],
    ) -> str | None:
        """Return the resident file value for a member field.

        ``None`` means "no file value" — the caller falls back to the raw DB
        column. Lazy: first call reads the member's ``card.md`` /
        ``member_prompt.md`` once into a :class:`FileContent` (which carries
        the body *and* ``updated_at``); later calls are dict hits. Only an
        evolved file (body diverged from its baseline) returns a body — an
        un-evolved file yields ``None`` so the raw DB column stands.
        """
        key = (member_name, field)
        if key in self._member_values:
            content = self._member_values[key]
        else:
            member_dir = team_member_workspace_dir(self._team_name, member_name)
            if field == "desc":
                path = WorkspaceLayout.member_card_file(member_dir)
            else:
                path = WorkspaceLayout.member_prompt_file(member_dir)
            content = self._read_file_content(path)
            self._member_values[key] = content
        if content is None or not content.is_evolved():
            return None
        return content.body

    def get_team_field(self, field: Literal["desc", "prompt"]) -> str | None:
        """Return the resident file value for a team field. Lazy (same as member)."""
        if field in self._team_values:
            content = self._team_values[field]
        else:
            root = self._store.team_workspace_root(self._team_name)
            if field == "desc":
                path = WorkspaceLayout.team_card_file(root)
            else:
                path = WorkspaceLayout.team_prompt_file(root)
            content = self._read_file_content(path)
            self._team_values[field] = content
        if content is None or not content.is_evolved():
            return None
        return content.body

    # ── B-class updated_at probe ──────────────────────────────────────────

    def get_member_updated_at(
        self,
        member_name: str,
        field: Literal["desc", "prompt"],
    ) -> int:
        """Return the resident ``updated_at`` (ms) for a member B-class file.

        Reads from the same :class:`FileContent` object as
        :meth:`get_member_field` — one file read fills both the body overlay
        and the mtime probe. ``0`` when the file is missing (does not
        advance the probe). ``field`` is the same ``"desc"`` / ``"prompt"``
        literal used by ``get_member_field`` and maps to ``card.md`` /
        ``member_prompt.md``.
        """
        key = (member_name, field)
        if key not in self._member_values:
            member_dir = team_member_workspace_dir(self._team_name, member_name)
            if field == "desc":
                path = WorkspaceLayout.member_card_file(member_dir)
            else:
                path = WorkspaceLayout.member_prompt_file(member_dir)
            self._member_values[key] = self._read_file_content(path)
        content = self._member_values[key]
        return content.updated_at if content is not None else 0

    def get_team_updated_at(self, field: Literal["desc", "prompt"]) -> int:
        """Return the resident ``updated_at`` (ms) for a team B-class file."""
        if field not in self._team_values:
            root = self._store.team_workspace_root(self._team_name)
            if field == "desc":
                path = WorkspaceLayout.team_card_file(root)
            else:
                path = WorkspaceLayout.team_prompt_file(root)
            self._team_values[field] = self._read_file_content(path)
        content = self._team_values[field]
        return content.updated_at if content is not None else 0

    # ── C-class ────────────────────────────────────────────────────────────

    def get_tool_md(self, desc_key: str) -> str | None:
        """Return the latest tool-level raw body (unrendered), or ``None``.

        ``{{slot}}`` rendering stays in the ``make_translator`` closure at
        read time. Lazy: the first C-class ``get*`` scans ``prompts/tool/``
        once and populates every tool-level md + param entry; later calls
        are dict hits.
        """
        if not self._tools_loaded:
            self._load_tools()
        return self._tool_md_values.get(desc_key)

    def get_tool_param(self, desc_key: str, param: str) -> str | None:
        """Return the latest param-level description, or ``None``. Lazy (same scan as ``get_tool_md``)."""
        if not self._tools_loaded:
            self._load_tools()
        return self._tool_params.get((desc_key, param))

    # ── fill (write-side priming) ──────────────────────────────────────────
    #
    # The assembler holds the latest body while writing each file (framework
    # default or the evolved file value), so it fills the cache directly; the
    # next ``get*`` is a dict hit with zero file IO. ``body=None`` is a valid
    # prime — it marks "no file value" so the miss path is not retried per
    # call (the caller falls back to the framework / DB default).

    def fill_template(self, name: str, body: str | None) -> None:
        """Prime an A-class template with the write-side latest body."""
        self._template_values[name] = PromptTemplate(name=name, content=body) if body is not None else None

    def fill_member_field(
        self,
        member_name: str,
        field: Literal["desc", "prompt"],
        content: FileContent | None,
    ) -> None:
        """Prime a B-class member field with the write-side file state.

        ``content`` is the :class:`FileContent` the write path just produced
        (or ``None`` when nothing was written); it carries both the overlay
        body and the ``updated_at`` the probe reads — so a single write-side
        prime fills both channels and the next ``get_member_field`` /
        ``get_member_updated_at`` are dict hits with zero file IO.
        """
        self._member_values[(member_name, field)] = content

    def fill_team_field(self, field: Literal["desc", "prompt"], content: FileContent | None) -> None:
        """Prime a B-class team field with the write-side file state (see :meth:`fill_member_field`)."""
        self._team_values[field] = content

    def fill_tool_md(self, desc_key: str, body: str | None) -> None:
        """Prime a C-class tool-level md with the write-side latest body."""
        self._tool_md_values[desc_key] = body

    def fill_tool_param(self, desc_key: str, param: str, text: str | None) -> None:
        """Prime a C-class param-level description with the write-side latest text."""
        self._tool_params[(desc_key, param)] = text

    def mark_tools_loaded(self) -> None:
        """Mark the C-class scan done — the write side primed every tool entry."""
        self._tools_loaded = True

    def _load_tools(self) -> None:
        """Scan ``prompts/tool/`` once and populate both C-class dicts.

        Tool-level md mirrors the framework ``descs/<lang>/`` layout
        (``tool/<domain>/<key>.<lang>.md`` — domain sub-dirs are transparent
        here via ``rglob``); ``tool.param.<lang>.md`` sits flat at the root
        and is parsed into ``_tool_params``. The whole param file carries
        one baseline hash — an evolution anywhere in the JSON marks the
        entire file evolved. Runs at most once per run cycle; the
        caller gates on ``_tools_loaded`` so an absent / un-evolved tree is
        not re-scanned on every lookup.
        """
        self._tools_loaded = True
        tools_dir = WorkspaceLayout.tool_dir(self._store.team_workspace_root(self._team_name))
        for path in WorkspaceLayout.iter_tool_md_files(tools_dir, self._lang):
            key = path.stem.removesuffix(f".{self._lang}")
            body = self._read_body(path)
            if body is not None:
                self._tool_md_values[key] = body
        for path in WorkspaceLayout.iter_tool_param_file(tools_dir, self._lang):
            self._load_tool_params(path)

    def _load_tool_params(self, path: Path) -> None:
        """Parse the evolved ``tool.param.<lang>.md`` JSON dict into lookups.

        The file is the full STRINGS dict verbatim — every dotted key
        ``"<desc_key>.<param>"`` splits on the first dot (nested param names
        like ``task.title`` keep their dots).
        """
        body = self._read_body(path)
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            team_logger.warning("tool.param JSON parse failed: %s", path)
            return
        if not isinstance(data, dict):
            return
        for str_key, text in data.items():
            if "." not in str_key or not isinstance(text, str):
                continue
            desc_key, param = str_key.split(".", 1)
            self._tool_params[(desc_key, param)] = text

    # ── shared: single-read file content ─────────────────────────────────

    @staticmethod
    def _read_file_content(path: Path) -> FileContent | None:
        """Return the file's parsed state; ``None`` when missing.

        A·C-class callers serve any existing file's body (the write side has
        already primed the cache with the latest value, so an un-evolved file's
        body equals the framework default); B-class callers gate on
        :meth:`FileContent.is_evolved`. Both share one read per file via
        :func:`parse_file_content`, which also backfills ``updated_at``.
        """
        try:
            return parse_file_content(path)
        except ValueError:
            team_logger.info("[workspace] %s malformed frontmatter — framework default stands", path)
            return None

    @staticmethod
    def _read_body(path: Path) -> str | None:
        """Return the A·C-class body (any existing file); ``None`` when missing.

        Thin wrapper over :meth:`_read_file_content` for A·C-class callers
        that only want the body — the write side has primed the cache so the
        body of an un-evolved file already equals the framework default.
        """
        content = WorkspaceCache._read_file_content(path)
        if content is None:
            team_logger.info("[workspace] %s missing — framework default stands", path)
            return None
        if content.is_evolved():
            team_logger.info("[workspace] %s evolved — workspace value wins", path)
        else:
            team_logger.info("[workspace] %s un-evolved — workspace value served", path)
        return content.body

    # ── invalidation ───────────────────────────────────────────────────────

    def invalidate(self) -> None:
        """Drop all resident values so the next ``get*`` re-reads the files.

        Called from ``RuntimeManager.finalize`` on the pause path (the run
        boundary). Does not touch the filesystem — the lazy miss on the next
        run is what re-reads the md files the evolution party may have edited.
        """
        self._template_values.clear()
        self._member_values.clear()
        self._team_values.clear()
        self._tool_md_values.clear()
        self._tool_params.clear()
        self._tools_loaded = False


__all__ = ["WorkspaceCache"]
