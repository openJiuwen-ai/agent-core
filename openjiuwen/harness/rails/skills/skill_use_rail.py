# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillUseRail implementation for DeepAgent."""

from __future__ import annotations

import asyncio
import itertools
import os
import time
import uuid
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.skills import (
    build_all_mode_skill_prompt,
    build_skill_line,
    build_skill_lines,
    build_skills_section,
)
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentKind
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails._multimodal import (
    build_read_image_multimodal_resolver,
)
from openjiuwen.harness.rails.skills.skill_lifecycle_events import (
    is_skill_complete_call,
)
from openjiuwen.harness.skills.mount import SkillRootMount, split_skill_front_matter
from openjiuwen.harness.tools import BashTool, ReadFileTool, ListSkillTool, SkillTool
from openjiuwen.agent_evolving.checkpointing import EvolutionStore

# Per-task skill name for tool-call attribution.
# ContextVar is a same-task fast path; session state is the cross-tool source of
# truth (tool execution contexts often do not inherit the ContextVar binding).
# Set only after skill_tool succeeds; cleared on skill_complete and at end of invoke.
_current_skill_name: ContextVar[Optional[str]] = ContextVar(
    "current_skill_name",
    default=None,
)

# Top-level AgentSession global_state key (not nested under skill_use baseline).
_CURRENT_SKILL_SESSION_KEY = "current_skill_name"


def _read_session_current_skill_name(session: Any) -> Optional[str]:
    """Read active skill name from session state, or None when unset/unavailable."""
    if session is None or not callable(getattr(session, "get_state", None)):
        return None
    try:
        stored = session.get_state(_CURRENT_SKILL_SESSION_KEY)
    except Exception:
        logger.debug("read current_skill_name from session failed", exc_info=True)
        return None
    if stored is None:
        return None
    cleaned = str(stored).strip()
    return cleaned or None


def _write_session_current_skill_name(session: Any, name: Optional[str]) -> None:
    """Persist or clear active skill name on session state when supported."""
    if session is None or not callable(getattr(session, "update_state", None)):
        return
    cleaned = str(name or "").strip() or None
    try:
        session.update_state({_CURRENT_SKILL_SESSION_KEY: cleaned})
    except Exception:
        logger.debug("write current_skill_name to session failed", exc_info=True)


def get_current_skill_name(session: Any = None) -> Optional[str]:
    """Return the active skill name, or None when unset.

    When ``session`` is provided, session state is preferred (survives tool
    execution context switches). ContextVar is used as a same-task fast path
    when session has no binding or is omitted.
    """
    if session is not None:
        stored = _read_session_current_skill_name(session)
        if stored is not None:
            return stored
    name = _current_skill_name.get()
    if name is None:
        return None
    cleaned = str(name).strip()
    return cleaned or None


def set_current_skill_name(name: str, session: Any = None) -> Token[Optional[str]]:
    """Bind the active skill name for the current task.

    Always updates the ContextVar. When ``session`` is provided, also persists
    to session state so later tool rails can resolve ``source_skill``.
    Returns a ContextVar reset token.
    """
    cleaned = str(name or "").strip() or None
    token = _current_skill_name.set(cleaned)
    if session is not None:
        _write_session_current_skill_name(session, cleaned)
    return token


def clear_current_skill_name(session: Any = None) -> Token[Optional[str]]:
    """Clear the active skill name.

    Always clears the ContextVar. When ``session`` is provided, also clears
    session state. Returns a ContextVar reset token.
    """
    token = _current_skill_name.set(None)
    if session is not None:
        _write_session_current_skill_name(session, None)
    return token


# Lines read when probing a SKILL.md for its YAML front matter. Bodies run to
# tens of KB while the front matter is a handful of lines; a file whose front
# matter does not fit within this budget falls back to a full read.
_FRONT_MATTER_PROBE_LINES = 64

# Directory names the skill scan never descends into. This answers a different
# question from ``skill_tool._TREE_SKIP_DIR_NAMES`` ("do not *show* this in the
# directory tree") — here it is "do not go looking for skills in there" — so the
# two sets are deliberately independent even though they currently agree.
_SKILL_SCAN_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "output",
        "temp",
        "assets",
        "node_modules",
    }
)


def _read_skill_refresh_ttl() -> float:
    """Seconds to reuse the last skill-directory scan. ``0`` disables the cache."""
    try:
        return max(0.0, float(os.environ.get("SKILL_REFRESH_TTL_SECONDS", "0") or 0))
    except ValueError:
        return 0.0


# Per-coroutine outcome slot set while ``before_invoke`` or
# ``_refresh_skill_prompt_if_changed`` awaits the public
# ``refresh_skill_prompt(ctx) -> None``. Keeps the public ``None`` return
# contract for delegating subclasses while carrying either the exact
# ``(generation, scan_seq)`` token that committed or ``False`` on abort so
# even post-``super()`` awaits inside an override are validated cleanly.
_REFRESH_OUTCOME: ContextVar[Optional[List[Any]]] = ContextVar(
    "_skill_use_rail_refresh_outcome", default=None
)
# Active scan language carried out-of-band so existing 2-argument
# ``_load_skill(skill_dir, update_at)`` subclass overrides keep working unchanged.
_SCAN_LANGUAGE: ContextVar[Optional[str]] = ContextVar(
    "_skill_use_rail_scan_language", default=None
)


class SkillUseRail(DeepAgentRail):
    """Rail that manages skill prompt injection and tool registration."""

    # Below the filesystem toolset tier (SysOperationRail / WorktreeRail, 100)
    # on purpose: ``init`` checks whether read_file / code / bash already have
    # an owner before contributing its own fallback copies, and that check only
    # means anything once the rails that own them for real have initialized.
    # Level with McpRail / SubagentRail, which register their own tools and
    # neither read nor write anything this rail touches.
    priority = 95

    SKILL_MODE_ALL = "all"
    SKILL_MODE_AUTO_LIST = "auto_list"
    _VALID_SKILL_MODES = {SKILL_MODE_ALL, SKILL_MODE_AUTO_LIST}
    _SESSION_STATE_KEY = "skill_use"
    _SESSION_STATE_SCHEMA_VERSION = 1
    _RUNTIME_ATTACHMENT_SECTION = "skills.runtime_changes"

    def __init__(
        self,
        skills_dir: Union[str, List[str]],
        *,
        skill_mode: str = SKILL_MODE_AUTO_LIST,
        list_skill_model: Optional[Model] = None,
        enable_cache: bool = True,
        include_tools: bool = True,
        enabled_skills: Optional[Union[str, List[str]]] = None,
        disabled_skills: Optional[Union[str, List[str]]] = None,
        evolution_store: Optional[EvolutionStore] = None,
        multimodal_skill_mode: str = "hint",
        max_skills: Optional[int] = None,
        max_total_chars: Optional[int] = None,
    ):
        """Initialize SkillUseRail.

        Args:
            skills_dir: Skill root directory or directories.
            skill_mode: Skill expose mode, supports:
                - "all": inject all enabled skills into system prompt
                - "auto_list": add list_skill tool and let model decide when to inspect skills
            list_skill_model: Optional model used by list_skill tool.
            enable_cache: Whether to cache loaded skills across invokes.
            include_tools: Whether to register read_file / bash tools.
            enabled_skills: Optional allow-list of skill names. Supports str or List[str].
            disabled_skills: Optional deny-list of skill names. Supports str or List[str].
            evolution_store: Optional EvolutionStore for progressive disclosure experience text.
            multimodal_skill_mode: ``hint`` (default), ``attach``, or ``branch``.
            max_skills: Optional hard cap on the number of skills injected in ``all`` mode.
            max_total_chars: Optional soft cap on total skill description chars in ``all`` mode.
                When exceeded, skills are ranked by keyword overlap with the query and low-ranked
                whole skills are dropped (gentle truncation — never mid-text). The cap is soft:
                the single highest-ranked skill is always retained, even when it alone exceeds
                the limit.
        """
        super().__init__()

        if skill_mode not in self._VALID_SKILL_MODES:
            raise ValueError(
                f"Unsupported skill_mode: {skill_mode}. "
                f"Expected one of {sorted(self._VALID_SKILL_MODES)}"
            )

        self.skills_dir = skills_dir
        self.skill_mode = skill_mode
        self.list_skill_model = list_skill_model
        self.enable_cache = enable_cache
        self.include_tools = include_tools
        self.enabled_skills = self._normalize_name_set(enabled_skills)
        self.disabled_skills = self._normalize_name_set(disabled_skills)
        self.evolution_store: Optional[EvolutionStore] = evolution_store
        self.multimodal_skill_mode = multimodal_skill_mode
        self.max_skills = max_skills
        self.max_total_chars = max_total_chars

        self.skills: List[Skill] = []
        self.system_prompt_builder = None
        self.attachment_manager = None

        # Cache loaded skills across invokes.
        self._skill_cache: Dict[str, Skill] = {}
        self._skill_update_at: Dict[str, float] = {}
        self._skill_order: List[str] = []
        # The prompt language the cached descriptions were localized for, and
        # a counter that clear_skills() bumps. A refresh compares the counter
        # before publishing, so a read that finishes after the cache was
        # cleared (backend rebound, language switched, root unmounted) cannot
        # write its stale result back.
        self._cached_language: Optional[str] = None
        self._cache_generation: int = 0
        # Monotonic counter & opaque in-process token bumped only when
        # ``set_sys_operation`` switches from one bound backend to another on
        # this rail instance. Persisted on session reconciliation so wrapper
        # recreation across turns never rewrites the baseline a second time
        # while retaining zero session references in memory on the rail.
        self._binding_epoch: int = 0
        self._binding_token: Optional[str] = None
        # Scans are numbered as well: an older scan that resumes after a
        # newer one started must not commit over the newer result even when
        # neither the backend nor the language changed in between. The
        # discovery snapshot of the scan that last committed the cache is
        # kept with it, so the signature stamped for the cache is the walk
        # that produced it, not a later one.
        self._scan_seq: int = 0
        self._committed_snapshot_signature: Optional[Tuple[Tuple[str, float], ...]] = None
        self._override_discovery_signature: Optional[Tuple[Tuple[str, float], ...]] = None
        self._visibility_conflict: bool = False
        self._attachment_needs_sync: bool = False
        self._current_gen_attachment_sessions: Set[str] = set()
        # ``(generation, scan)`` of the refresh that last committed.
        self._last_committed_refresh_token: Optional[Tuple[int, int]] = None

        # Cache evolution experience texts per skill name.
        self._evolution_texts: Dict[str, str] = {}

        # Abilities this rail actually registered, mapped from tool name to the
        # exact card that was stored. The name is the ability-manager key
        # (``add_ability`` rewrites card ids, so an id is not a stable handle),
        # while the card identity tells uninit whether this rail is still the
        # owner or another rail has since taken the name over.
        self._owned_tool_cards: Dict[str, ToolCard] = {}

        # Snapshot of visible skill directories and SKILL.md mtimes.
        self._skills_snapshot_signature: Optional[Tuple[Tuple[str, float], ...]] = None
        # Directory-scan TTL. 0 keeps the existing per-invoke refresh. A
        # performance-sensitive deploy opts in with SKILL_REFRESH_TTL_SECONDS
        # (for example 30); within the window the last scan is reused, so a
        # newly installed skill shows up at latest TTL seconds later.
        # clear_skills() drops the stamp, so an explicit install/unmount still
        # refreshes on the next invoke. Read once at construction: the value
        # is a process deploy knob, not a per-request setting.
        self._last_full_refresh_ts: Optional[float] = None
        self._skill_refresh_ttl_s = _read_skill_refresh_ttl()

    @property
    def skills_meta(self) -> List[Skill]:
        """Return all managed skills."""
        return list(self.skills)

    async def _refresh_view(self) -> Optional[Tuple[int, int]]:
        """Scan the skill roots, fetch the evolution texts and stamp the signature.

        Returns:
            The ``(generation, scan)`` of the scan that committed, or None
            when the backend or language changed, or a newer scan started,
            while a read was awaited: nothing is stamped and the caller
            publishes nothing.
        """
        target_lang = self._current_description_language()
        scan = await self._prepare_skills()
        if scan is None:
            return None
        await self._fetch_evolution_texts()
        # The evolution store is awaited too, so the backend, language, or a
        # newer scan can still change after the skills were read. Re-check
        # before stamping, or the next model call would see an unchanged
        # signature and skip discovery with no skills visible.
        if not self._scan_still_current(scan, target_lang):
            return None
        self._stamp_committed_signature()
        return scan

    async def reload_skills(self) -> None:
        """Refresh managed skills immediately after skills_dir changes."""
        await self._refresh_view()

    def clear_skills(self) -> None:
        """Clear loaded skills and the public rail-managed cache."""
        self._skill_cache.clear()
        self._skill_update_at.clear()
        self._skill_order.clear()
        self.skills = []
        self._skills_snapshot_signature = None
        self._last_full_refresh_ts = None
        self._cached_language = None
        self._cache_generation += 1
        self._committed_snapshot_signature = None
        self._override_discovery_signature = None
        self._visibility_conflict = False
        self._attachment_needs_sync = True
        self._current_gen_attachment_sessions.clear()
        self._last_committed_refresh_token = None
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.SKILLS)

    def _current_description_language(self) -> str:
        """Return the prompt language the cached descriptions must follow."""
        return str(getattr(self.system_prompt_builder, "language", "cn") or "cn").strip().lower()

    def _invalidate_if_language_changed(self) -> None:
        """Drop the cache when the prompt language no longer matches it.

        ``DeepAgent`` pushes ``system_prompt_builder`` onto a pending rail
        *before* ``init`` runs, and hot reconfiguration swaps the builder
        without calling ``init`` again, so a language switch is not visible
        inside ``init``. The language that produced the cache is tracked
        instead and re-checked before any cached state is reused.
        """
        if self._cached_language is not None and self._cached_language != self._current_description_language():
            self.clear_skills()

    def _refresh_still_current(self, generation: int, target_lang: str) -> bool:
        """Whether a refresh that started as *generation* / *target_lang* is still live."""
        return self._cache_generation == generation and self._current_description_language() == target_lang

    def _scan_still_current(self, scan: Tuple[int, int], target_lang: str) -> bool:
        """Whether the scan started as *scan* / *target_lang* is still the live one."""
        generation, scan_seq = scan
        return (
            scan_seq == self._scan_seq
            and self._cache_generation == generation
            and self._current_description_language() == target_lang
        )

    async def _run_refresh_prompt(self, ctx: AgentCallbackContext) -> bool:
        """Run ``refresh_skill_prompt(ctx)`` and return whether *this* call committed."""
        generation = self._cache_generation
        target_lang = self._current_description_language()
        self._visibility_conflict = False
        outcome: List[Any] = [None]
        token = _REFRESH_OUTCOME.set(outcome)
        try:
            await self.refresh_skill_prompt(ctx)
        except Exception:
            self._last_full_refresh_ts = None
            raise
        finally:
            _REFRESH_OUTCOME.reset(token)
        result = outcome[0]
        if result is False:
            return False
        if isinstance(result, tuple):
            return self._scan_still_current(result, target_lang)
        # Custom override/test replaced refresh_skill_prompt without calling super().
        if not self._refresh_still_current(generation, target_lang):
            return False
        if self._cached_language is None:
            self._cached_language = target_lang
        return True

    def set_sys_operation(self, sys_operation: Any) -> None:
        """Bind sys_operation and clear cached skills when the backend changes."""
        # A pre-mount reload may have read skills from the local disk (or via a
        # previous backend); a newly bound sandbox/virtual filesystem must get
        # the chance to re-read (and possibly reject) them instead of serving
        # that cached result.
        prev_operation = self.sys_operation
        changed = sys_operation is not prev_operation
        super().set_sys_operation(sys_operation)
        if changed:
            if prev_operation is not None and sys_operation is not None:
                self._binding_epoch += 1
                self._binding_token = uuid.uuid4().hex
            self.clear_skills()

    def mount_skill_root(self, directory: str | Path) -> SkillRootMount:
        """Append a native discovery root and return its ownership handle."""
        root = Path(directory).expanduser().resolve()
        roots = self._normalize_skill_dirs(self.skills_dir)
        owned = root not in roots
        if owned:
            self.skills_dir = [*(str(item) for item in roots), str(root)]
            self.clear_skills()
        return SkillRootMount(self, root, owned)

    def unmount_skill_root(self, directory: str | Path) -> None:
        """Remove an explicitly owned root; callers retain the ownership handle."""
        root = Path(directory).expanduser().resolve()
        roots = self._normalize_skill_dirs(self.skills_dir)
        self.skills_dir = [str(item) for item in roots if item != root]
        self.clear_skills()

    def owns_tool(self, card: ToolCard | None) -> bool:
        """Whether the ability card is still the native tool this rail registered."""
        return card is not None and self._owned_tool_cards.get(card.name) is card

    def check_skill_source(self, name: str, directory: str | Path) -> None:
        """Reject filters and same-name sources that hide a required skill."""
        if (self.enabled_skills and name not in self.enabled_skills) or name in self.disabled_skills:
            raise ValueError(f"is filtered: {name}.")
        expected = Path(directory).resolve()
        roots = self._normalize_skill_dirs(self.skills_dir)
        for discovered, _ in self._discover_skill_dirs(roots):
            if discovered.name == name and discovered.resolve() != expected:
                raise ValueError(f"name conflict: {name}.")

    async def validate_skill_source(
        self,
        name: str,
        directory: str | Path,
        session: Any = None,
        *,
        refresh: bool = False,
    ) -> None:
        """Validate a required skill against discovery, visibility and session state."""
        self.check_skill_source(name, directory)
        if refresh:
            await self.reload_skills()
        expected = Path(directory).resolve()
        # Check all sources before name deduplication: a hidden duplicate is
        # still a conflict for a capability that requires this exact resource.
        for skill in self._skill_cache.values():
            if skill.name == name and skill.directory.resolve() != expected:
                raise ValueError(f"name conflict: {name}.")
        visible = next((item for item in self.skills if item.name == name), None)
        if visible is None or visible.directory.resolve() != expected:
            raise ValueError(f"is missing or filtered: {name}.")
        baseline = next((item for item in self.get_skills_for_session(session) if item.name == name), None)
        if baseline is None or baseline.directory.resolve() != expected:
            raise ValueError(f"conflicts with the session baseline: {name}.")

    async def _prepare_skills(self) -> Optional[Tuple[int, int]]:
        """Refresh skills incrementally from skills_dir and apply filters.

        Returns:
            The ``(generation, scan)`` the refreshed view was published under,
            for the caller to re-check after its own awaits. None when
            ``clear_skills()`` ran while a read was in flight (the backend was
            rebound), the prompt language switched under the refresh, or a
            newer scan started: nothing is published and the state is left
            for the next refresh.
        """
        self._invalidate_if_language_changed()
        self._visibility_conflict = False
        target_lang = self._current_description_language()
        self._scan_seq += 1
        scan = (self._cache_generation, self._scan_seq)

        if not self.enable_cache:
            self._skill_cache.clear()
            self._skill_update_at.clear()
            self._skill_order.clear()

        refreshed = await self._refresh_skills_incrementally(scan=scan, language=target_lang)
        if not refreshed:
            return None
        collected = self._collect_skills_in_order()
        self.skills = self._filter_skills(collected)
        self._cached_language = target_lang

        # Freeze the full committed signature synchronously right here — using
        # the exact discovery snapshot that produced the cache and the exact
        # visibility decision that produced ``self.skills`` — before any
        # post-filter await (such as ``_fetch_evolution_texts``) can interleave.
        discovery_sig = self._committed_snapshot_signature or ()
        if type(self)._build_skills_snapshot_signature is SkillUseRail._build_skills_snapshot_signature:
            self._committed_snapshot_signature = discovery_sig
        else:
            prev_enabled = set(self.enabled_skills)
            prev_disabled = set(self.disabled_skills)
            self._override_discovery_signature = discovery_sig
            try:
                full_sig = self._build_skills_snapshot_signature()
            finally:
                self._override_discovery_signature = None
            if self.enabled_skills != prev_enabled or self.disabled_skills != prev_disabled:
                # Visibility shifted during signature build: keep the
                # subclass-filtered ``self.skills`` from this scan, preserve
                # the most recent successful provider decision in
                # ``enabled_skills`` / ``disabled_skills`` for fallback,
                # invalidate any open TTL window, and leave the signature
                # unstamped so the next callback polls fresh visibility.
                self._committed_snapshot_signature = None
                self._last_full_refresh_ts = None
                self._visibility_conflict = True
            else:
                self._committed_snapshot_signature = full_sig
        return scan

    async def _refresh_skills_incrementally(
        self,
        scan: Optional[Tuple[int, int]] = None,
        language: Optional[str] = None,
    ) -> bool:
        """Refresh skills by loading only new or updated SKILL.md files.

        Args:
            scan: The ``(cache generation, scan sequence)`` the caller captured
                when the refresh started. ``clear_skills()`` bumps the
                generation (the cache was wiped under this refresh) and every
                ``_prepare_skills()`` bumps the sequence (a newer scan
                superseded this one), so a scan that resumes after either
                must not write.
            language: The prompt language the descriptions must be localized
                for. Every load in this scan uses it, so a language switch
                mid-scan cannot yield a mix.

        Returns:
            False when the scan was superseded while a read was awaited:
            nothing is written so the caller publishes nothing. True
            otherwise, with the cache, its order and the discovery snapshot
            that produced it committed together.
        """
        if scan is None:
            scan = (self._cache_generation, self._scan_seq)
        if language is None:
            language = self._current_description_language()
        roots = self._normalize_skill_dirs(self.skills_dir)
        if not roots:
            raise ValueError("skills_dir is empty")

        discovered = self._discover_skill_dirs(roots)
        # The exact snapshot the loads below are based on. The disk can move
        # while a read is awaited, and a signature walked later would then
        # describe files this scan never loaded.
        discovered_snapshot = self._snapshot_signature(discovered)
        # One consistent view of the cache for the whole scan: what a newer
        # scan commits meanwhile is not this scan's to reuse.
        cache_snapshot = dict(self._skill_cache)
        update_at_snapshot = dict(self._skill_update_at)

        ordered_keys: List[str] = []
        # The next skill cache is staged and published only once
        # the whole scan is known to be the live one: a switch mid-scan
        # leaves no partially mixed cache behind.
        staged_skills: Dict[str, Skill] = {}
        staged_update_at: Dict[str, float] = {}

        for item, update_at in discovered:
            key = str(item.resolve())
            ordered_keys.append(key)

            skill = cache_snapshot.get(key)
            cached_update_at = update_at_snapshot.get(key)

            if skill is None or cached_update_at != update_at:
                lang_token = _SCAN_LANGUAGE.set(language)
                try:
                    skill = await self._load_skill(item, update_at)
                finally:
                    _SCAN_LANGUAGE.reset(lang_token)
                if not self._scan_still_current(scan, language):
                    return False
            staged_skills[key] = skill
            staged_update_at[key] = update_at

        if not self._scan_still_current(scan, language):
            return False
        self._skill_cache.clear()
        self._skill_cache.update(staged_skills)
        self._skill_update_at.clear()
        self._skill_update_at.update(staged_update_at)
        self._skill_order = ordered_keys
        self._committed_snapshot_signature = discovered_snapshot
        return True

    async def _load_skill(self, skill_dir: Path, update_at: float) -> Skill:
        """Load one skill from a skill directory."""
        skill_md_path = skill_dir / "SKILL.md"

        description = ""
        try:
            description = await self._load_description(skill_md_path)
        except Exception as exc:
            logger.warning(f"Failed to load description from {skill_md_path}: {exc}")

        skill = Skill(
            name=skill_dir.name,
            description=description or f"Skill located in {skill_dir}",
            directory=skill_dir,
        )
        try:
            setattr(skill, "update_at", update_at)
        except (AttributeError, TypeError, ValueError) as exc:
            logger.debug(
                "[SkillUseRail] skip setting update_at for skill '%s': %s",
                skill.name,
                exc,
            )
        return skill

    def _collect_skills_in_order(self) -> List[Skill]:
        """Collect cached skills in directory traversal order and deduplicate by name."""
        collected: List[Skill] = []
        seen_names: Set[str] = set()

        for key in self._skill_order:
            skill = self._skill_cache.get(key)
            if skill is None:
                continue

            if skill.name in seen_names:
                logger.warning(
                    f"[SkillUseRail] duplicate skill name detected: '{skill.name}'. "
                    f"keep first loaded skill, skip '{skill.directory}'."
                )
                continue

            seen_names.add(skill.name)
            collected.append(skill)

        return collected

    def _filter_skills(self, skills: List[Skill]) -> List[Skill]:
        """Filter skills by enabled_skills and disabled_skills."""
        filtered: List[Skill] = []

        for skill in skills:
            if self.enabled_skills and skill.name not in self.enabled_skills:
                continue
            if skill.name in self.disabled_skills:
                continue
            filtered.append(skill)

        return filtered

    def init(self, agent):
        """Register this rail's tools through the agent ability manager.

        Every tool is registered with ``AbilityManager.add_ability`` (card plus
        concrete instance) so the ability-manager card id and the
        resource-manager key stay consistent. Abilities already owned by another
        rail are left untouched; see the loop comment for why.
        """
        # Cached descriptions are localized (``description_<lang>``). The
        # builder is already assigned here (DeepAgent pushes it onto pending
        # rails before init), so a language switch is detected by tracking the
        # cached language in ``_invalidate_if_language_changed()`` instead.
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

        tools = []

        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        enable_read_image_multimodal = build_read_image_multimodal_resolver(agent)

        tools.append(
            SkillTool(
                operation=self.sys_operation,
                get_skills=lambda session=None: self.get_skills_for_session(session),
                language=lang,
                agent_id=agent_id,
                multimodal_skill_mode=self.multimodal_skill_mode,
                enable_read_image_multimodal=enable_read_image_multimodal,
            ),
        )

        if self.include_tools:
            tools.extend(
                [
                    ReadFileTool(
                        self.sys_operation,
                        language=lang,
                        agent_id=agent_id,
                        enable_image_multimodal=enable_read_image_multimodal,
                    ),
                    BashTool(self.sys_operation, language=lang, agent_id=agent_id),
                ]
            )

        if self.skill_mode == self.SKILL_MODE_AUTO_LIST:
            tools.append(
                ListSkillTool(
                    get_skills=lambda session=None: self.get_skills_for_session(session),
                    list_skill_model=self.list_skill_model,
                    language=lang,
                    agent_id=agent_id,
                )
            )

        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is None:
            logger.warning(
                "[SkillUseRail] agent has no ability_manager; skill tools are not registered"
            )
            return

        for tool in tools:
            # ``include_tools`` only provides a *fallback* read_file / code /
            # bash set for hosts that mount no filesystem rail. When another
            # rail (typically SysOperationRail) already owns the ability, that
            # owner's instance carries policy this rail cannot reproduce
            # (read-only mode, bash deny patterns, an explicit multimodal
            # override), so defer to it instead of rebinding the shared
            # resource-manager entry behind its back.
            if ability_manager.get(tool.card.name) is not None:
                logger.debug(
                    "[SkillUseRail] ability '%s' is already registered by another "
                    "owner; skip the fallback registration",
                    tool.card.name,
                )
                continue
            try:
                # Register card + instance through the single entry point so the
                # ability-manager card id and the resource-manager key stay
                # consistent (stateful tools get an agent-qualified id).
                result = ability_manager.add_ability(tool.card, tool)
                if result.added:
                    self._owned_tool_cards[tool.card.name] = tool.card
            except Exception as exc:
                logger.warning(
                    f"[SkillUseRail] failed to register tool '{tool.card.name}' "
                    f"on ability_manager: {exc}"
                )

    def uninit(self, agent):
        """Remove the abilities this rail still owns from the agent."""
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is not None:
            for tool_name, tool_card in list(self._owned_tool_cards.items()):
                if ability_manager.get(tool_name) is not tool_card:
                    # Another rail re-registered the name after this rail did
                    # and now owns both the card and the live instance; tearing
                    # it down here would unregister that rail's tool.
                    continue
                try:
                    # remove_ability mirrors add_ability: it drops the card and,
                    # for stateful tools, the agent-qualified resource entry.
                    ability_manager.remove_ability(tool_name)
                except Exception as exc:
                    logger.warning(
                        f"[SkillUseRail] failed to remove tool '{tool_name}' "
                        f"from ability_manager: {exc}"
                    )

        self._owned_tool_cards.clear()

    async def refresh_skill_prompt(self, ctx: AgentCallbackContext) -> None:
        """Regenerate the skills system prompt.

        Returns None by contract; whether *this* refresh committed — the
        backend, effective policy, language, and scan sequence were still live
        after its final await — is reported through the per-coroutine outcome
        slot so delegating overrides that await ``super()`` keep all callback
        work while superseded refreshes abort cleanly.
        """
        _ = ctx
        committed = await self._refresh_view()
        slot = _REFRESH_OUTCOME.get()
        if committed is not None:
            self._last_committed_refresh_token = committed
            if slot is not None:
                slot[0] = committed
        elif slot is not None:
            slot[0] = False

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Prepare skills before invoke.

        The directory walk plus signature is on the order of a hundred
        milliseconds per turn even when no skill changed. Skill installs are
        rare, so a deploy can set ``SKILL_REFRESH_TTL_SECONDS`` and reuse the
        last scan inside that window. The default ``0`` keeps a full refresh
        every turn. A failed refresh does not stamp the window, so the next
        turn retries immediately. The session baseline and prompt sync still
        run on a cache hit; only the filesystem scan is skipped.
        """
        self._invalidate_if_language_changed()
        if not self._skill_refresh_within_ttl():
            # A refresh that lost its cache or was superseded by a newer scan
            # leaves the window open and skips session/prompt publication so
            # stale skills are never frozen into the session baseline.
            if not await self._run_refresh_prompt(ctx):
                return
            if not self._visibility_conflict:
                self._last_full_refresh_ts = time.monotonic()
        self._ensure_session_baseline(ctx)
        if self.system_prompt_builder is not None:
            await self._sync_skill_prompt_and_attachment(ctx)

    async def _fetch_evolution_texts(self, skills: Optional[List[Skill]] = None) -> None:
        """Fetch and cache evolution experience texts from EvolutionStore."""
        if self.evolution_store is None:
            return
        generation = self._cache_generation
        skills = self.skills if skills is None else skills
        seen_names: Set[str] = set()
        for skill in skills:
            if skill.name in seen_names:
                continue
            seen_names.add(skill.name)
            try:
                text = await self.evolution_store.format_desc_experience_text(skill.name)
            except Exception as exc:
                logger.warning(
                    "[SkillUseRail] failed to fetch evolution text for '%s': %s",
                    skill.name,
                    exc,
                )
                continue
            if self._cache_generation != generation:
                # The await outlasted a rebind: the text was fetched for a
                # skill set the rail no longer holds and is obsolete.
                return
            self._evolution_texts[skill.name] = text

    def _get_skill_description(self, skill: Skill) -> str:
        """Return description with evolution experience text appended if available."""
        desc = skill.description
        evo_text = self._evolution_texts.get(skill.name, "")
        if evo_text:
            desc = f"{desc}\n  演进经验:\n{evo_text}"
        return desc

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Clear skill attribution when the model ends the skill via skill_complete.

        Aligns with skill_authorization_rail: skill_complete means the skill is
        over, so later tools in the same invoke must not keep the old source_skill.
        after_invoke still clears as a cross-turn safety net.
        """
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "")
        if not is_skill_complete_call(tool_name):
            return
        clear_current_skill_name(session=getattr(ctx, "session", None))

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Drop skill activation at the end of an invoke to avoid cross-turn bleed."""
        clear_current_skill_name(session=getattr(ctx, "session", None))

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Update system_prompt_builder with current skills before model call.

        build() and get_context_window are deferred to _railed_model_call
        so that ContextProcessor has the accurate final token budget.
        """
        if self.system_prompt_builder is None:
            return

        await self._sync_skill_prompt_and_attachment(ctx)

    async def _clear_stale_runtime_skill_attachment(self, ctx: AgentCallbackContext) -> None:
        """Remove a pending rail-owned runtime attachment left over from a previous source."""
        if not self._attachment_needs_sync:
            return
        manager = self.attachment_manager
        if manager is None:
            return
        writer = manager.bind_context(ctx)
        if not writer.session_id or writer.session_id in self._current_gen_attachment_sessions:
            return
        await writer.clear_section(self._RUNTIME_ATTACHMENT_SECTION)
        self._current_gen_attachment_sessions.add(writer.session_id)

    async def _sync_skill_prompt_and_attachment(self, ctx: AgentCallbackContext) -> None:
        """Refresh the stable skill section and the change-only attachment."""
        if not await self._refresh_skill_prompt_if_changed(ctx):
            await self._clear_stale_runtime_skill_attachment(ctx)
            return
        # Evolution records can change without changing the Skill.md snapshot.
        # Refresh them independently so the attachment always contains the
        # latest experience while the system prompt remains stable.
        # Some task-loop paths may enter model call without BEFORE_INVOKE.
        # Establish the durable baseline at the first point where skills are used.
        self._ensure_session_baseline(ctx)
        session_state = self._load_session_state(getattr(ctx, "session", None))
        baseline_skills = (
            self._get_session_baseline(ctx)
            if session_state is not None
            else list(self.skills)
        )
        generation = self._cache_generation
        target_lang = self._current_description_language()
        await self._fetch_evolution_texts([*baseline_skills, *self.skills])
        # Another callback may have rebound the rail and rendered for the new
        # source while this one was parked on the evolution store. The
        # baseline captured above is then obsolete, and publishing it would
        # replace the current prompt section with the previous source's view.
        if not self._refresh_still_current(generation, target_lang):
            await self._clear_stale_runtime_skill_attachment(ctx)
            return
        query = self._extract_query_from_messages(ctx)
        skills_section = self._build_skills_section(baseline_skills, query=query)
        if skills_section is not None:
            self.system_prompt_builder.add_section(skills_section)
        else:
            self.system_prompt_builder.remove_section(SectionName.SKILLS)
        await self._update_runtime_skill_attachment(ctx, baseline_skills)

    def _skill_refresh_within_ttl(self) -> bool:
        """Whether the last successful directory scan is still inside the TTL.

        ``SKILL_REFRESH_TTL_SECONDS <= 0`` (the default) is always a miss, so
        every invoke keeps today's full refresh. ``enable_cache=False`` also
        forces a miss: that flag means every invoke must re-read the disk, and
        a TTL hit would skip ``_prepare_skills`` entirely.
        """
        if not self.enable_cache:
            return False
        if self._cached_language is not None and self._cached_language != self._current_description_language():
            return False
        ttl = self._skill_refresh_ttl_s
        if ttl <= 0 or self._last_full_refresh_ts is None:
            return False
        return (time.monotonic() - self._last_full_refresh_ts) < ttl

    async def _refresh_skill_prompt_if_changed(self, ctx: AgentCallbackContext) -> bool:
        """Refresh skills when visible skill directories or SKILL.md mtimes changed.

        Inside an opted-in TTL window the signature walk is the same directory
        scan ``before_invoke`` just skipped, so it is skipped here too.
        ``before_model_call`` reaches this without a preceding ``before_invoke``
        on some task-loop paths; a live window still reuses the last scan.
        A successful refresh here stamps the TTL so those paths open the
        window instead of rescanning every call.
        """
        self._invalidate_if_language_changed()
        if self._skill_refresh_within_ttl():
            return True
        current_signature = self._build_skills_snapshot_signature()
        if current_signature == self._skills_snapshot_signature:
            return True

        if not await self._run_refresh_prompt(ctx):
            return False
        if not self._visibility_conflict:
            self._last_full_refresh_ts = time.monotonic()
        return True

    def _build_skills_snapshot_signature(self) -> Tuple[Tuple[str, float], ...]:
        """Build the same incremental-refresh signature used by _prepare_skills."""
        if self._override_discovery_signature is not None:
            return self._override_discovery_signature
        roots = self._normalize_skill_dirs(self.skills_dir)
        return self._snapshot_signature(self._discover_skill_dirs(roots))

    @staticmethod
    def _snapshot_signature(discovered: List[Tuple[Path, float]]) -> Tuple[Tuple[str, float], ...]:
        """Turn one discovery walk into the signature that represents it."""
        return tuple((str(item.resolve()), update_at) for item, update_at in discovered)

    def _stamp_committed_signature(self) -> None:
        """Stamp the exact discovery and visibility signature committed by ``_prepare_skills``."""
        self._skills_snapshot_signature = self._committed_snapshot_signature

    def _build_skills_section(self, skills: Optional[List[Skill]] = None, query: Optional[str] = None):
        """Build the stable system prompt section from session baseline skills.

        Args:
            skills: Optional skill list override. Defaults to self.skills.
            query: Optional task query for budget-based ranking.
        """
        skills = self.skills if skills is None else skills
        if self.skill_mode == self.SKILL_MODE_ALL:
            skills = self._apply_skill_budget(skills, query)
            body_lines: List[str] = []
            for idx, skill in enumerate(skills):
                body_lines.append(
                    build_skill_line(
                        index=idx,
                        skill_name=skill.name,
                        description=skill.description,
                        language=self.system_prompt_builder.language,
                        # skill_md_path=str(self._skill_md_path(skill)), # No longer needed with SkillTool
                    )
                )
            return build_skills_section(
                skill_lines=build_skill_lines(body_lines),
                language=self.system_prompt_builder.language,
                mode="all",
            )
        else:
            return build_skills_section(
                skill_lines="",
                language=self.system_prompt_builder.language,
                mode="auto_list",
            )

    def _apply_skill_budget(
        self,
        skills: List[Skill],
        query: Optional[str] = None,
    ) -> List[Skill]:
        """Rank skills by query relevance and drop low-ranked ones to stay under budget.

        This implements *gentle truncation*: whole skills are dropped,
        never mid-description. ``max_total_chars`` is a soft cap — the
        highest-ranked skill is always retained, even when it alone exceeds
        the limit, so callers never get an empty list back from a non-empty
        input.

        Args:
            skills: Full list of skills to consider.
            query: Optional task query for relevance scoring.

        Returns:
            Filtered skill list within budget constraints.
        """
        if not skills:
            return skills
        if self.max_skills is None and self.max_total_chars is None:
            return skills

        # Score skills by keyword overlap with query (or description length as fallback)
        if query:
            keywords = self._extract_keywords(query)
            scored = []
            for skill in skills:
                text = f"{skill.name} {skill.description}".lower()
                score = sum(1 for kw in keywords if kw in text)
                scored.append((score, skill))
            scored.sort(key=lambda x: (-x[0], x[1].name))
            ranked = [s for _, s in scored]
        else:
            # Without a query, preserve original order (assumed pre-sorted by importance)
            ranked = list(skills)

        # Apply hard skill count cap
        if self.max_skills is not None and len(ranked) > self.max_skills:
            dropped = ranked[self.max_skills:]
            ranked = ranked[:self.max_skills]
            logger.info(
                "[SkillUseRail] Dropped %d skills due to max_skills=%d: %s",
                len(dropped),
                self.max_skills,
                ", ".join(s.name for s in dropped),
            )

        # Apply soft char cap by dropping lowest-ranked whole skills.
        # The cap is soft: the highest-ranked skill is always retained, even if it
        # alone exceeds max_total_chars, so the model never sees an empty skill list.
        if self.max_total_chars is not None:
            total_chars = sum(len(s.description or "") for s in ranked)
            while len(ranked) > 1 and total_chars > self.max_total_chars:
                dropped_skill = ranked.pop()
                total_chars -= len(dropped_skill.description or "")
                logger.info(
                    "[SkillUseRail] Dropped skill '%s' to stay under max_total_chars=%d "
                    "(remaining chars=%d)",
                    dropped_skill.name,
                    self.max_total_chars,
                    total_chars,
                )
            if ranked and total_chars > self.max_total_chars:
                logger.warning(
                    "[SkillUseRail] Skill '%s' alone exceeds max_total_chars=%d "
                    "(%d chars); keeping it so the skills section is never empty",
                    ranked[0].name,
                    self.max_total_chars,
                    total_chars,
                )

        return ranked

    _STOP_WORDS: Set[str] = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "to", "of", "and", "or", "in", "on", "at", "by", "for", "with", "as",
        "this", "that", "these", "those", "it", "its", "from", "have", "has",
        "had", "do", "does", "did", "will", "would", "could", "should", "may",
        "might", "can", "shall", "you", "your", "we", "our", "i", "my", "he",
        "she", "they", "them", "their", "what", "which", "who", "when", "where",
        "why", "how", "all", "each", "every", "both", "few", "more", "most",
        "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    })

    @classmethod
    def _extract_keywords(cls, text: str) -> Set[str]:
        """Extract lowercase alphanumeric keywords from text, removing stop words."""
        words = set()
        for token in text.lower().split():
            token = "".join(c for c in token if c.isalnum())
            if token and token not in cls._STOP_WORDS and len(token) > 2:
                words.add(token)
        return words

    @staticmethod
    def _extract_query_from_messages(ctx: AgentCallbackContext) -> Optional[str]:
        """Extract the latest user query from context messages for skill ranking.

        Looks at the last few messages and returns the content of the most
        recent UserMessage, or None if no user messages are found.
        """
        messages = getattr(ctx.inputs, "messages", None)
        if not messages:
            return None
        for message in reversed(messages):
            if isinstance(message, UserMessage):
                content = getattr(message, "content", None)
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return None

    def get_skills_for_session(self, session: Any = None) -> List[Skill]:
        """Return the current skill view for a tool invocation.

        The persisted baseline is included even if the directory was changed after
        the session started. Newly discovered skills remain available as runtime
        additions for the current session. The baseline is reconciled with the
        source the rail reads from now first, so a backend that was switched
        without a callback in between cannot keep answering through it.
        """
        self._invalidate_if_language_changed()
        if self._load_session_state(session) is None:
            return list(self.skills)

        merged = list(self._reconcile_session_baseline(session) or [])
        known_names = {skill.name for skill in merged}
        merged.extend(skill for skill in self.skills if skill.name not in known_names)
        return merged

    def _reconcile_session_baseline(self, session: Any) -> Optional[List[Skill]]:
        """Bring the persisted baseline in line with the current rail binding and locale.

        - An unbound prewarm persists a ``provisional=True`` baseline and never
          overwrites an already-bound or legacy persisted session.
        - Once a ``sys_operation`` is bound, a ``provisional=True`` baseline is
          replaced by the bound backend's committed view.
        - When ``set_sys_operation`` explicitly switches one bound backend to
          another on this live rail instance (``_binding_token`` is set), each
          session is reconciled once to the newly bound backend and records the
          token in session state so wrapper recreation across turns preserves
          the baseline without retaining session objects on the rail.
        - A prompt-language switch re-localizes descriptions in place while
          preserving baseline skill membership and order.
        """
        self._invalidate_if_language_changed()
        state = self._load_session_state(session)
        if state is None:
            return None

        if self.sys_operation is None:
            if state.get("provisional") is False:
                # Durable bound session: serve current unbound view without overwriting.
                return list(self.skills)
            if state.get("provisional") is None:
                # Legacy schema-v1 state without provenance: preserve untouched.
                return self._load_session_baseline(session)

        provisional_promoted = state.get("provisional") is True and self.sys_operation is not None
        live_rebound = (
            self.sys_operation is not None
            and self._binding_token is not None
            and state.get("rebind_token") != self._binding_token
        )

        if provisional_promoted or live_rebound:
            if self._cached_language is not None:
                self._save_session_baseline(
                    session,
                    self.skills,
                    provisional=self.sys_operation is None,
                    language=self._cached_language,
                )
            return list(self.skills)

        if (
            state.get("language") is not None
            and self._cached_language is not None
            and state.get("language") != self._cached_language
        ):
            # Session membership and order stay stable; only the localized
            # descriptions follow the language the rail has committed.
            current_by_name = {skill.name: skill for skill in self.skills}
            rebased = []
            for skill in self._load_session_baseline(session):
                current = current_by_name.get(skill.name)
                rebased.append(
                    Skill(
                        name=skill.name,
                        description=current.description if current is not None else skill.description,
                        directory=skill.directory,
                    )
                )
            self._save_session_baseline(
                session,
                rebased,
                provisional=state.get("provisional", False),
                language=self._cached_language,
            )
            return rebased

        # Same binding and language, or a legacy state: served as persisted.
        return self._load_session_baseline(session)

    def _get_session_baseline(self, ctx: AgentCallbackContext) -> List[Skill]:
        return self._reconcile_session_baseline(getattr(ctx, "session", None)) or []

    def _ensure_session_baseline(self, ctx: AgentCallbackContext) -> None:
        session = getattr(ctx, "session", None)
        if session is None:
            return
        self._invalidate_if_language_changed()
        if self._load_session_state(session) is None:
            # Only a rail that has committed a view may establish a baseline:
            # a cleared, not yet refreshed rail must not freeze [].
            if self._cached_language is not None:
                self._save_session_baseline(session, self.skills)
            return
        self._reconcile_session_baseline(session)

    def _load_session_baseline(self, session: Any) -> List[Skill]:
        state = self._load_session_state(session)
        if state is None:
            return []
        baseline = state.get("baseline_skills", [])
        if not isinstance(baseline, list):
            logger.warning("[SkillUseRail] invalid persisted baseline_skills; ignoring it")
            return []

        skills: List[Skill] = []
        for item in baseline:
            if not isinstance(item, dict) or not item.get("name") or not item.get("directory"):
                continue
            skills.append(
                Skill(
                    name=str(item["name"]),
                    description=str(item.get("description") or ""),
                    directory=Path(str(item["directory"])),
                )
            )
        return skills

    def _load_session_state(self, session: Any) -> Optional[dict]:
        if session is None or not callable(getattr(session, "get_state", None)):
            return None
        state = session.get_state(self._SESSION_STATE_KEY)
        if not isinstance(state, dict):
            return None
        if state.get("schema_version") != self._SESSION_STATE_SCHEMA_VERSION:
            logger.warning("[SkillUseRail] unsupported persisted state schema; ignoring it")
            return None
        return state

    def _save_session_baseline(
        self,
        session: Any,
        skills: List[Skill],
        *,
        provisional: Optional[bool] = None,
        language: Optional[str] = None,
    ) -> None:
        """Persist the session baseline."""
        if not callable(getattr(session, "update_state", None)):
            return
        if provisional is None:
            provisional = self.sys_operation is None
        if language is None:
            language = (
                self._cached_language
                if self._cached_language is not None
                else self._current_description_language()
            )
        payload: Dict[str, Any] = {
            "schema_version": self._SESSION_STATE_SCHEMA_VERSION,
            "baseline_skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "directory": str(skill.directory),
                }
                for skill in skills
            ],
            "provisional": bool(provisional),
            "language": language,
        }
        if not provisional and self._binding_token is not None:
            payload["rebind_token"] = self._binding_token
        session.update_state({self._SESSION_STATE_KEY: payload})

    async def _update_runtime_skill_attachment(
        self,
        ctx: AgentCallbackContext,
        baseline_skills: List[Skill],
    ) -> None:
        manager = self.attachment_manager
        if manager is None:
            return
        baseline_by_name = {skill.name: skill for skill in baseline_skills}
        current_by_name = {skill.name: skill for skill in self.skills}
        additions = [skill for skill in self.skills if skill.name not in baseline_by_name]
        removals = [skill for skill in baseline_skills if skill.name not in current_by_name]
        writer = manager.bind_context(ctx)
        if not writer.session_id:
            return
        content = self._build_runtime_skill_change_content(
            additions,
            removals,
            baseline_skills,
        )
        if not content:
            await writer.clear_section(self._RUNTIME_ATTACHMENT_SECTION)
            if self._attachment_needs_sync:
                self._current_gen_attachment_sessions.add(writer.session_id)
            return

        await writer.add_section(
            section=self._RUNTIME_ATTACHMENT_SECTION,
            content=content,
            kind=PromptAttachmentKind.SKILL,
            source="skill_use_rail",
        )
        if self._attachment_needs_sync:
            self._current_gen_attachment_sessions.add(writer.session_id)

    def _build_runtime_skill_change_content(
        self,
        additions: List[Skill],
        removals: List[Skill],
        baseline_skills: List[Skill],
    ) -> str:
        """Render Skill changes and evolution experience in one attachment."""
        language = getattr(self.system_prompt_builder, "language", "cn")
        is_english = str(language).lower().startswith("en")
        evolution_skills: List[Skill] = []
        seen_names: Set[str] = set()
        for skill in [*baseline_skills, *additions]:
            if skill.name in seen_names:
                continue
            if self._evolution_texts.get(skill.name, "").strip():
                evolution_skills.append(skill)
                seen_names.add(skill.name)

        if not additions and not removals and not evolution_skills:
            return ""

        if is_english:
            lines = [
                "Skill environment status update. Invoke relevant skills only when needed for the current task.",
            ]
            if additions:
                lines.append("Newly available skills:")
                lines.extend(
                    build_skill_line(
                        index=index,
                        skill_name=skill.name,
                        description=skill.description,
                        language=self.system_prompt_builder.language,
                    )
                    for index, skill in enumerate(additions)
                )
            if removals:
                lines.append("Unavailable skills (removed from the environment):")
                lines.extend(f"- {skill.name}" for skill in removals)
            if evolution_skills:
                lines.append("Skill evolution experience reference:")
                for skill in evolution_skills:
                    lines.append(f"[Skill: {skill.name}]")
                    lines.append(self._evolution_texts[skill.name].strip())
            return "\n".join(lines)

        lines = ["Skill 环境状态更新。请根据当前任务需要，按需调用相关 Skill。"]
        if additions:
            lines.append("新增可用 Skill：")
            lines.extend(
                build_skill_line(
                    index=index,
                    skill_name=skill.name,
                    description=skill.description,
                    language=self.system_prompt_builder.language,
                )
                for index, skill in enumerate(additions)
            )
        if removals:
            lines.append("已移除、当前不可用的 Skill：")
            lines.extend(f"- {skill.name}" for skill in removals)
        if evolution_skills:
            lines.append("Skill 演进经验参考：")
            for skill in evolution_skills:
                lines.append(f"[Skill: {skill.name}]")
                lines.append(self._evolution_texts[skill.name].strip())
        return "\n".join(lines)

    def _build_all_mode_prompt(self) -> str:
        """Build skill prompt for all mode."""
        body_lines: List[str] = []

        for idx, skill in enumerate(self.skills):
            body_lines.append(
                build_skill_line(
                    index=idx,
                    skill_name=skill.name,
                    description=self._get_skill_description(skill),
                    language=self.system_prompt_builder.language,
                    # skill_md_path=str(self._skill_md_path(skill)), # No longer needed with SkillTool
                )
            )

        return build_all_mode_skill_prompt(build_skill_lines(body_lines), language=self.system_prompt_builder.language)

    @staticmethod
    def _normalize_name_list(raw: Optional[Union[str, List[str]]]) -> List[str]:
        """Normalize env-style or list-style skill name inputs."""
        if raw is None:
            return []

        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return []
            normalized = text.replace(";", ",")
            return [item.strip() for item in normalized.split(",") if item.strip()]

        names: List[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text:
                continue
            normalized = text.replace(";", ",")
            names.extend([part.strip() for part in normalized.split(",") if part.strip()])
        return names

    @classmethod
    def _normalize_name_set(cls, raw: Optional[Union[str, List[str]]]) -> Set[str]:
        """Normalize skill names into a set."""
        return set(cls._normalize_name_list(raw))

    @staticmethod
    async def _read_local_skill_text(path: Path, *, head: Optional[int] = None) -> str:
        """Read UTF-8 text straight from the local file at *path*.

        Only used while the rail has no ``sys_operation`` (a standalone
        ``load_skills_from_dir`` or an unmounted ``reload_skills``). Mirrors
        ``LocalFS.read_file``: ``head > 0`` keeps the first *head* lines and
        streams only those (a 64-line probe never reads the whole body),
        ``head == 0`` or ``None`` means no limit, and a negative *head*
        yields no lines once the path is validated — before any byte is
        decoded, so a file that is not valid UTF-8 still returns ``""``.

        Raises:
            FileNotFoundError: The file is missing, is a directory, or cannot
                be read as UTF-8.
        """

        def _read() -> str:
            if head is not None and head < 0:
                if not path.is_file():
                    raise FileNotFoundError(f"read_file failed: {path}")
                return ""
            with open(path, mode="r", encoding="utf-8") as handle:
                if head is not None and head > 0:
                    return "".join(itertools.islice(handle, head))
                return handle.read()

        try:
            return await asyncio.to_thread(_read)
        except (OSError, UnicodeDecodeError) as exc:
            raise FileNotFoundError(f"read_file failed: {path}") from exc

    async def _read_skill_text(self, path: Path, *, head: Optional[int] = None) -> str:
        """Read SKILL.md text, optionally only its first *head* lines.

        An unmounted rail (``sys_operation`` is ``None``) reads the local file
        directly. Once a ``sys_operation`` is bound, every read goes through
        it, so a sandboxed or virtual filesystem stays authoritative.

        Args:
            path: Path to the SKILL.md file.
            head: When set, read only that many leading lines. Skill metadata is
                read-only, so the cross-process file lock is waived.

        Returns:
            The file text that was read.

        Raises:
            FileNotFoundError: The read failed or returned no content.
        """
        if self.sys_operation is None:
            return await self._read_local_skill_text(path, head=head)

        result = await self.sys_operation.fs().read_file(
            str(path),
            mode="text",
            encoding="utf-8",
            head=head,
            only_read=True,
        )

        if getattr(result, "code", 0) != 0:
            raise FileNotFoundError(
                getattr(result, "message", f"read_file failed: {path}")
            )

        data = getattr(result, "data", None)
        content = getattr(data, "content", None) if data is not None else None
        if content is None:
            raise FileNotFoundError(f"read_file content is None: {path}")

        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _split_front_matter(text: str) -> Optional[Tuple[dict, str]]:
        """Split YAML front matter from the markdown body.

        Args:
            text: File text starting at the top of the file.

        Returns:
            The parsed front matter and the remaining body, or None when the
            text does not open with a complete ``---`` delimited block.
        """
        return split_skill_front_matter(text)

    async def _load_front_matter(self, path: Path) -> Optional[dict]:
        """Load only the YAML front matter of a SKILL.md.

        A SKILL.md body can be tens of KB while the front matter is a handful of
        lines, so a bounded head read is tried first and only a file whose front
        matter does not fit falls back to reading the whole file.

        Args:
            path: Path to the SKILL.md file.

        Returns:
            The parsed front matter mapping, or None when the file has none.
        """
        head_text = await self._read_skill_text(path, head=_FRONT_MATTER_PROBE_LINES)
        parsed = self._split_front_matter(head_text)
        if parsed is not None:
            return parsed[0]
        if not head_text.startswith("---"):
            return None
        yaml_data, _ = await self._load_yaml(path)
        return yaml_data

    async def _load_yaml(self, path: Path) -> Tuple[Optional[dict], str]:
        """Load YAML front matter and markdown body from SKILL.md."""
        text = await self._read_skill_text(path)

        parsed = self._split_front_matter(text)
        return parsed if parsed is not None else (None, text)

    async def _load_description(self, path: Path, language: Optional[str] = None) -> str:
        """Load description from YAML front matter.

        Args:
            path: Path to the SKILL.md file.
            language: The prompt language to localize for. Defaults to the
                live builder language; a scan passes the language it started
                with so a mid-scan switch cannot leak into one skill.
        """
        yaml_data = await self._load_front_matter(path)
        if yaml_data is None or "description" not in yaml_data:
            raise KeyError("SKILL.md file does not contain a description field")

        if language is None:
            language = _SCAN_LANGUAGE.get() or self._current_description_language()
        localized_key = f"description_{language}"
        description = yaml_data.get(localized_key) or yaml_data.get("description")
        if not description:
            raise KeyError("SKILL.md file does not contain a description field")
        return str(description).strip()

    @staticmethod
    def _skill_md_path(skill: Skill) -> Path:
        """Return SKILL.md path for a skill."""
        return skill.directory / "SKILL.md"

    @staticmethod
    def _parse_skill_dirs(raw: str) -> List[str]:
        """Parse env-style multi-skill-dir string."""
        if not raw or not raw.strip():
            return []
        normalized = raw.replace(",", ";")
        return [item.strip() for item in normalized.split(";") if item.strip()]

    @classmethod
    def _normalize_skill_dirs(cls, skills_dir: Union[str, List[str]]) -> List[Path]:
        """Normalize one or more skill directories."""
        if isinstance(skills_dir, str):
            raw_dirs = cls._parse_skill_dirs(skills_dir)
            if not raw_dirs and skills_dir.strip():
                raw_dirs = [skills_dir.strip()]
        else:
            raw_dirs = []
            for item in skills_dir:
                if isinstance(item, str):
                    parsed = cls._parse_skill_dirs(item)
                    if parsed:
                        raw_dirs.extend(parsed)
                    elif item.strip():
                        raw_dirs.append(item.strip())

        normalized: List[Path] = []
        for raw in raw_dirs:
            if not raw or not str(raw).strip():
                continue
            normalized.append(Path(raw).expanduser().resolve())

        return normalized

    @classmethod
    def _discover_skill_dirs(cls, roots: List[Path]) -> List[Tuple[Path, float]]:
        """Find every skill directory under ``roots``, with its SKILL.md mtime.

        A skill library is not necessarily flat: skills are commonly filed
        under grouping directories (``skills/lark/lark-doc/SKILL.md``), and a
        scan that only looked one level down found the group instead of the
        skills and reported nothing.

        So the walk descends — but **stops at the first ``SKILL.md`` it finds
        on a branch**. A directory holding a ``SKILL.md`` is a skill, and what
        it keeps inside is its own business: sub-skills there are private
        detail its author discloses through the parent's own content (see
        ``skill_tool``'s nested-skill listing), not top-level entries the
        model sees before it has read the parent. The rule needs no special
        case for "is this a group or a skill" — finding a ``SKILL.md`` answers
        it.

        Args:
            roots: Normalized library roots, as returned by
                ``_normalize_skill_dirs``.

        Returns:
            ``(skill_dir, skill_md_mtime)`` pairs, ordered by root and then by
            directory name at each level, so callers get a stable sequence.
        """
        found: List[Tuple[Path, float]] = []
        # Resolved paths already walked. Symlinked libraries are a normal way
        # to share one skill across teams, so a cycle is reachable and would
        # otherwise recurse forever.
        visited: Set[str] = set()

        def _walk(directory: Path) -> None:
            try:
                dir_key = str(directory.resolve())
            except OSError:
                dir_key = str(directory)
            if dir_key in visited:
                return
            visited.add(dir_key)

            # An explicit root may itself be a skill rather than a library.
            # Keep the same first-SKILL.md boundary for both entry points.
            skill_md_path = directory / "SKILL.md"
            if skill_md_path.is_file():
                try:
                    found.append((directory, skill_md_path.stat().st_mtime))
                except OSError as exc:
                    logger.debug("[SkillUseRail] cannot stat %s: %s", skill_md_path, exc)
                return

            try:
                children = sorted(directory.iterdir(), key=lambda p: p.name)
            except OSError as exc:
                logger.debug("[SkillUseRail] cannot list %s: %s", directory, exc)
                return

            for child in children:
                if not child.is_dir():
                    continue
                if child.name.startswith(".") or child.name in _SKILL_SCAN_SKIP_DIRS:
                    continue

                _walk(child)

        for root in roots:
            if not root.exists():
                logger.debug(
                    "[SkillUseRail] skills_dir does not exist, "
                    "skipping: %s",
                    root,
                )
                continue
            if not root.is_dir():
                logger.debug(
                    "[SkillUseRail] skills_dir is not a directory, "
                    "skipping: %s",
                    root,
                )
                continue
            _walk(root)

        return found

    @classmethod
    async def load_skills_from_dir(
        cls,
        skills_dir: Union[str, List[str]],
    ) -> List[Skill]:
        """Load skills from one or more skills directories."""
        roots = cls._normalize_skill_dirs(skills_dir)
        if not roots:
            raise ValueError("skills_dir is empty")

        skill_map: Dict[str, Skill] = {}

        loader = cls(
            skills_dir=skills_dir,
            skill_mode=cls.SKILL_MODE_ALL,
            include_tools=False,
        )

        for item, update_at in cls._discover_skill_dirs(roots):
            skill = await loader._load_skill(item, update_at)

            if skill.name in skill_map:
                prev_dir = skill_map[skill.name].directory
                logger.warning(
                    f"[SkillUseRail] duplicate skill name detected: '{skill.name}'. "
                    f"keep='{prev_dir}', skip='{item}'."
                )
                continue

            skill_map[skill.name] = skill

        return list(skill_map.values())


__all__ = [
    "SkillUseRail",
]
