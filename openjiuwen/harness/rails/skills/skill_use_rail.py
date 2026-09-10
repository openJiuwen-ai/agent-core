# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillUseRail implementation for DeepAgent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import yaml

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
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
from openjiuwen.harness.tools import BashTool, ReadFileTool, ListSkillTool, SkillTool
from openjiuwen.agent_evolving.checkpointing import EvolutionStore

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

        self.skills: List[Skill] = []
        self.system_prompt_builder = None
        self.attachment_manager = None

        # Cache loaded skills across invokes.
        self._skill_cache: Dict[str, Skill] = {}
        self._skill_update_at: Dict[str, float] = {}
        self._skill_order: List[str] = []

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

    @property
    def skills_meta(self) -> List[Skill]:
        """Return all managed skills."""
        return list(self.skills)

    async def reload_skills(self) -> None:
        """Refresh managed skills immediately after skills_dir changes."""
        await self._prepare_skills()
        await self._fetch_evolution_texts()
        self._skills_snapshot_signature = self._build_skills_snapshot_signature()

    def clear_skills(self) -> None:
        """Clear loaded skills and the public rail-managed cache."""
        self._skill_cache.clear()
        self._skill_update_at.clear()
        self._skill_order.clear()
        self.skills = []
        self._skills_snapshot_signature = None

    async def _prepare_skills(self) -> None:
        """Refresh skills incrementally from skills_dir and apply filters."""
        if not self.enable_cache:
            self._skill_cache.clear()
            self._skill_update_at.clear()
            self._skill_order.clear()

        await self._refresh_skills_incrementally()
        self.skills = self._filter_skills(self._collect_skills_in_order())

    async def _refresh_skills_incrementally(self) -> None:
        """Refresh skills by loading only new or updated SKILL.md files."""
        roots = self._normalize_skill_dirs(self.skills_dir)
        if not roots:
            raise ValueError("skills_dir is empty")

        discovered_keys: Set[str] = set()
        ordered_keys: List[str] = []

        for item, update_at in self._discover_skill_dirs(roots):
            key = str(item.resolve())

            discovered_keys.add(key)
            ordered_keys.append(key)

            cached_skill = self._skill_cache.get(key)
            cached_update_at = self._skill_update_at.get(key)

            if cached_skill is None or cached_update_at != update_at:
                skill = await self._load_skill(item, update_at)
                self._skill_cache[key] = skill
                self._skill_update_at[key] = update_at

        stale_keys = [key for key in self._skill_cache.keys() if key not in discovered_keys]
        for key in stale_keys:
            self._skill_cache.pop(key, None)
            self._skill_update_at.pop(key, None)

        self._skill_order = [key for key in ordered_keys if key in self._skill_cache]

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
        """Regenerate the skills system prompt"""
        _ = ctx
        await self._prepare_skills()
        await self._fetch_evolution_texts()
        self._skills_snapshot_signature = self._build_skills_snapshot_signature()

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Prepare skills before invoke."""
        await self.refresh_skill_prompt(ctx)
        self._ensure_session_baseline(ctx)
        if self.system_prompt_builder is not None:
            await self._sync_skill_prompt_and_attachment(ctx)

    async def _fetch_evolution_texts(self, skills: Optional[List[Skill]] = None) -> None:
        """Fetch and cache evolution experience texts from EvolutionStore."""
        if self.evolution_store is None:
            return
        skills = self.skills if skills is None else skills
        seen_names: Set[str] = set()
        for skill in skills:
            if skill.name in seen_names:
                continue
            seen_names.add(skill.name)
            try:
                text = await self.evolution_store.format_desc_experience_text(skill.name)
                self._evolution_texts[skill.name] = text
            except Exception as exc:
                logger.warning(
                    "[SkillUseRail] failed to fetch evolution text for '%s': %s",
                    skill.name,
                    exc,
                )

    def _get_skill_description(self, skill: Skill) -> str:
        """Return description with evolution experience text appended if available."""
        desc = skill.description
        evo_text = self._evolution_texts.get(skill.name, "")
        if evo_text:
            desc = f"{desc}\n  演进经验:\n{evo_text}"
        return desc

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Update system_prompt_builder with current skills before model call.

        build() and get_context_window are deferred to _railed_model_call
        so that ContextProcessor has the accurate final token budget.
        """
        if self.system_prompt_builder is None:
            return

        await self._sync_skill_prompt_and_attachment(ctx)

    async def _sync_skill_prompt_and_attachment(self, ctx: AgentCallbackContext) -> None:
        """Refresh the stable skill section and the change-only attachment."""
        await self._refresh_skill_prompt_if_changed(ctx)
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
        await self._fetch_evolution_texts([*baseline_skills, *self.skills])
        skills_section = self._build_skills_section(baseline_skills)
        if skills_section is not None:
            self.system_prompt_builder.add_section(skills_section)
        else:
            self.system_prompt_builder.remove_section(SectionName.SKILLS)
        await self._update_runtime_skill_attachment(ctx, baseline_skills)

    async def _refresh_skill_prompt_if_changed(self, ctx: AgentCallbackContext) -> None:
        """Refresh skills when visible skill directories or SKILL.md mtimes changed."""
        current_signature = self._build_skills_snapshot_signature()
        if current_signature == self._skills_snapshot_signature:
            return

        await self.refresh_skill_prompt(ctx)

    def _build_skills_snapshot_signature(self) -> Tuple[Tuple[str, float], ...]:
        """Build the same incremental-refresh signature used by _prepare_skills."""
        roots = self._normalize_skill_dirs(self.skills_dir)
        return tuple(
            (str(item.resolve()), update_at)
            for item, update_at in self._discover_skill_dirs(roots)
        )

    def _build_skills_section(self, skills: Optional[List[Skill]] = None):
        """Build the stable system prompt section from session baseline skills."""
        skills = self.skills if skills is None else skills
        if self.skill_mode == self.SKILL_MODE_ALL:
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

    def get_skills_for_session(self, session: Any = None) -> List[Skill]:
        """Return the current skill view for a tool invocation.

        The persisted baseline is included even if the directory was changed after
        the session started. Newly discovered skills remain available as runtime
        additions for the current session.
        """
        baseline = self._load_session_baseline(session)
        if self._load_session_state(session) is None:
            return list(self.skills)

        merged = list(baseline)
        known_names = {skill.name for skill in merged}
        merged.extend(skill for skill in self.skills if skill.name not in known_names)
        return merged

    def _get_session_baseline(self, ctx: AgentCallbackContext) -> List[Skill]:
        return self._load_session_baseline(getattr(ctx, "session", None))

    def _ensure_session_baseline(self, ctx: AgentCallbackContext) -> None:
        session = getattr(ctx, "session", None)
        if session is None:
            return
        if self._load_session_state(session) is not None:
            return
        self._save_session_baseline(session, self.skills)

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

    def _save_session_baseline(self, session: Any, skills: List[Skill]) -> None:
        if not callable(getattr(session, "update_state", None)):
            return
        session.update_state(
            {
                self._SESSION_STATE_KEY: {
                    "schema_version": self._SESSION_STATE_SCHEMA_VERSION,
                    "baseline_skills": [
                        {
                            "name": skill.name,
                            "description": skill.description,
                            "directory": str(skill.directory),
                        }
                        for skill in skills
                    ],
                }
            }
        )

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
            return

        await writer.add_section(
            section=self._RUNTIME_ATTACHMENT_SECTION,
            content=content,
            kind=PromptAttachmentKind.SKILL,
            source="skill_use_rail",
        )

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

    async def _read_skill_text(self, path: Path, *, head: Optional[int] = None) -> str:
        """Read SKILL.md text, optionally only its first *head* lines.

        Args:
            path: Path to the SKILL.md file.
            head: When set, read only that many leading lines. Skill metadata is
                read-only, so the cross-process file lock is waived.

        Returns:
            The file text that was read.

        Raises:
            FileNotFoundError: The read failed or returned no content.
        """
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
        if not text.startswith("---"):
            return None
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None
        _, yaml_block, body = parts
        return yaml.safe_load(yaml_block) or {}, body.lstrip()

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
        result = await self.sys_operation.fs().read_file(
            str(path),
            mode="text",
            encoding="utf-8",
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

        text = content if isinstance(content, str) else str(content)

        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                _, yaml_block, body = parts
                yaml_data = yaml.safe_load(yaml_block) or {}
                return yaml_data, body.lstrip()

        return None, text

    async def _load_description(self, path: Path) -> str:
        """Load description from YAML front matter."""
        yaml_data = await self._load_front_matter(path)
        if yaml_data is None or "description" not in yaml_data:
            raise KeyError("SKILL.md file does not contain a description field")

        builder = getattr(self, "system_prompt_builder", None)
        language = str(getattr(builder, "language", "cn") or "cn").strip().lower()
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

                skill_md_path = child / "SKILL.md"
                if skill_md_path.is_file():
                    try:
                        found.append((child, skill_md_path.stat().st_mtime))
                    except OSError as exc:
                        logger.debug("[SkillUseRail] cannot stat %s: %s", skill_md_path, exc)
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
