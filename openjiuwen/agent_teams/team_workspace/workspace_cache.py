# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-team evolvable-workspace cache.

One ``WorkspaceCache`` instance carries all three evolvable text classes:

- A-class prompt templates (``prompts/system/<name>.<lang>.md``) → raw body.
- B-class static fields (``card.md`` desc / ``member_prompt.md`` /
  ``team_card.md`` / ``team_prompt.md``) → str (or ``None``).
- C-class tool descriptions (``prompts/tool/<domain>/<key>.<lang>.md``
  tool-level md + ``tool.param.<lang>.md`` JSON dict) → raw body / entry.

Fill model — **lazy get**: a ``get*`` call is a dict lookup; on miss the file
is read once (via the ``WorkspaceStore``), the value stored in the dict, and
returned. Subsequent same-key calls are plain dict hits with zero file IO.
The cache **never** proactively scans / builds: no ``build``, no ``rebuild``,
no roster argument, no leader-only gate.

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

``evolution_enabled`` + team name + language live on this one instance;
consumers only talk to this class. When the switch is off no file is read —
the dict stays empty so callers fall back to code defaults / raw DB columns
(files are written regardless; the switch only gates the read side).

``display_name`` never rides the overlay — it is not file-evolvable and
always falls back to the DB column. No inheritance, no generics, no
sub-classing: the three classes are just dict fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from openjiuwen.agent_teams.team_workspace.frontmatter import (
    is_evolved,
    read_frontmatter,
)
from openjiuwen.agent_teams.team_workspace.layout import WorkspaceLayout
from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.prompt import PromptTemplate


class WorkspaceCache:
    """Per-team workspace evolvable-value cache, one instance for A/B/C.

    Lazy get (miss reads the file once, then hits) is the only fill path;
    ``invalidate()`` is the only drop path. No ``build``, no ``rebuild``,
    no roster — the cache never scans the workspace up front.

    Runtime read priority: the file value wins when the file exists and its
    body hash diverges from the recorded baseline (evolved); otherwise the
    code default / raw DB value stands (the cache stores ``None`` for a
    present-but-un-evolved file so the miss path is not retried per call).
    """

    def __init__(
        self,
        store: WorkspaceStore,
        team_name: str,
        *,
        language: str = "cn",
        evolution_enabled: bool = True,
    ) -> None:
        self._store = store
        self._team_name = team_name
        self._lang = language
        self._enabled = evolution_enabled
        # A-class: template name → PromptTemplate (raw body).
        self._template_values: dict[str, PromptTemplate | None] = {}
        # B-class: (member_name, field) → resident value (str | None).
        self._member_values: dict[tuple[str, str], str | None] = {}
        # B-class team: field → resident value.
        self._team_values: dict[str, str | None] = {}
        # C-class tool-level: desc_key → raw body (unrendered {{slot}}).
        self._tool_md_values: dict[str, str | None] = {}
        # C-class param-level: (desc_key, param) → text.
        self._tool_params: dict[tuple[str, str], str | None] = {}
        # C-class tree scanned once per run (gates ``_load_tools``).
        self._tools_loaded: bool = False

    # ── A-class ────────────────────────────────────────────────────────────

    def get_template(self, name: str) -> PromptTemplate | None:
        """Return the evolved A-class template for ``name``, or ``None``.

        ``None`` means "no evolved file value" — the caller falls back to the
        framework ``load_template`` (see ``make_template_loader``). Lazy: the
        first call for ``name`` reads ``prompts/system/<name>.<lang>.md``;
        later calls in the same run are dict hits.
        """
        if not self._enabled:
            return None
        if name in self._template_values:
            return self._template_values[name]
        body = self._read_evolved(
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
        ``member_prompt.md``; later calls are dict hits.
        """
        if not self._enabled:
            return None
        key = (member_name, field)
        if key in self._member_values:
            return self._member_values[key]
        if field == "desc":
            value = self._store.read_card(self._team_name, member_name)
        else:
            value = self._store.read_member_prompt(self._team_name, member_name)
        self._member_values[key] = value
        return value

    def get_team_field(self, field: Literal["desc", "prompt"]) -> str | None:
        """Return the resident file value for a team field. Lazy (same as member)."""
        if not self._enabled:
            return None
        if field in self._team_values:
            return self._team_values[field]
        if field == "desc":
            value = self._store.read_team_card(self._team_name)
        else:
            value = self._store.read_team_prompt(self._team_name)
        self._team_values[field] = value
        return value

    # ── C-class ────────────────────────────────────────────────────────────

    def get_tool_md(self, desc_key: str) -> str | None:
        """Return the evolved tool-level raw body (unrendered), or ``None``.

        ``{{slot}}`` rendering stays in the ``make_translator`` closure at
        read time. Lazy: the first C-class ``get*`` scans ``prompts/tool/``
        once and populates every tool-level md + param entry; later calls
        are dict hits.
        """
        if not self._enabled:
            return None
        if not self._tools_loaded:
            self._load_tools()
        return self._tool_md_values.get(desc_key)

    def get_tool_param(self, desc_key: str, param: str) -> str | None:
        """Return the evolved param-level description, or ``None``. Lazy (same scan as ``get_tool_md``)."""
        if not self._enabled:
            return None
        if not self._tools_loaded:
            self._load_tools()
        return self._tool_params.get((desc_key, param))

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
            body = self._read_evolved(path)
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
        body = self._read_evolved(path)
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

    # ── shared: evolution judgement (frontmatter primitives) ───────────────

    @staticmethod
    def _read_evolved(path: Path) -> str | None:
        """Return the file body iff it is evolved; ``None`` otherwise.

        Evolved = body hash diverges from the recorded ``baseline_sha256``.
        A hand-written file without frontmatter has no baseline → treated as
        evolved (its body always wins). A file with malformed frontmatter is
        *invalid* — never read, the framework default stands.

        Info log per read so the value source is auditable: an ``evolved``
        line means the workspace value wins over the framework default.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            team_logger.info("[workspace] %s missing — framework default stands", path)
            return None
        try:
            meta, body = read_frontmatter(text)
        except ValueError:
            team_logger.info("[workspace] %s malformed frontmatter — framework default stands", path)
            return None
        if is_evolved(meta, body):
            team_logger.info("[workspace] %s evolved — workspace value wins", path)
            return body
        team_logger.info("[workspace] %s un-evolved — framework default stands", path)
        return None

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
