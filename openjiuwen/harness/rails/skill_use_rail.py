# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillUseRail implementation for DeepAgent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import yaml

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.skills import (
    build_skill_line,
    build_skill_lines,
    build_skills_section,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools import BashTool, CodeTool, ReadFileTool
from openjiuwen.harness.tools.list_skill import ListSkillTool
from openjiuwen.harness.tools import SkillTool, SkillCompleteTool
from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.core.context_engine.active_skill_bodies import (
    ACTIVE_SKILL_HINTS_STATE_KEY,
    DEFAULT_MAX_ACTIVE_SKILL_BODIES,
    normalize_skill_relative_file_path,
    pop_active_skill_hints_for_session,
    record_active_skill_body,
    unregister_active_skill_body,
)
from openjiuwen.core.context_engine.observability import (
    resolve_context_trace_ids,
    skill_trace_metadata_subset,
    write_context_trace,
)
from openjiuwen.core.foundation.llm import SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.prompts import resolve_language


def _coerce_ui_language_string(raw: Optional[str]) -> str:
    """Map config or deep_config language to ``cn`` or ``en`` for the skill load stub."""
    if not raw or not isinstance(raw, str):
        return resolve_language(None)
    s = raw.strip().lower()
    if s in ("en", "english"):
        return "en"
    if s in ("cn", "zh", "zh-cn", "zhcn", "chinese"):
        return "cn"
    return resolve_language(None)


def _safe_deep_config_language(ctx: Optional[AgentCallbackContext]) -> Optional[str]:
    if ctx is None:
        return None
    agent = getattr(ctx, "agent", None)
    if agent is None:
        return None
    dc = getattr(agent, "deep_config", None)
    if dc is None:
        return None
    lang = getattr(dc, "language", None)
    if not isinstance(lang, str):
        return None
    t = lang.strip()
    return t or None


def _resolve_skill_load_stub_ui_language(
    ctx: Optional[AgentCallbackContext],
    rail: Any,
) -> str:
    override = getattr(rail, "skill_tool_stub_language", None)
    if override is not None and isinstance(override, str) and override.strip():
        return _coerce_ui_language_string(override.strip())
    from_ctx = _safe_deep_config_language(ctx)
    if from_ctx is not None:
        return _coerce_ui_language_string(from_ctx)
    return _coerce_ui_language_string(None)


def _format_skill_load_stub_core(
    skill_name: str,
    relative_file_path: str,
    *,
    ui_language: str,
) -> str:
    if ui_language == "en":
        return (
            f"[SKILL LOADED] {skill_name} / {relative_file_path}\n"
            "The full SKILL body is kept and reinjected as an [ACTIVE SKILL BODY] block in later "
            "context—follow that block for workflow; do not open this skill's SKILL.md by path again. "
            "Call skill_tool again only to reload."
        )
    return (
        f"[SKILL LOADED] {skill_name} / {relative_file_path}\n"
        "完整正文已由系统保留，并在后续发给模型的上下文中以 [ACTIVE SKILL BODY] 块注入；"
        "请直接依据该块执行，不要按磁盘路径再次打开本技能的 SKILL.md。"
        "仅在需要强制刷新全文时再调用 skill_tool。"
    )


def _format_skill_unload_stub(
    skill_name: str,
    *,
    ui_language: str,
) -> str:
    if ui_language == "en":
        return (
            f"[SKILL UNLOADED] Body of skill '{skill_name}' was released to save context. "
            f"Re-call skill_tool if you need it again."
        )
    return (
        f"[SKILL UNLOADED] 技能 '{skill_name}' 的正文已释放以节省上下文，"
        f"如需再次使用请重新调用 skill_tool。"
    )


class SkillUseRail(DeepAgentRail):
    """Rail that manages skill prompt injection and tool registration."""

    priority = 100

    SKILL_MODE_ALL = "all"
    SKILL_MODE_AUTO_LIST = "auto_list"
    _VALID_SKILL_MODES = {SKILL_MODE_ALL, SKILL_MODE_AUTO_LIST}

    def __init__(
        self,
        skills_dir: Union[str, List[str]],
        *,
        skill_mode: str = SKILL_MODE_AUTO_LIST,
        list_skill_model: Optional[Model] = None,
        enable_cache: bool = True,
        include_tools: bool = True,
        include_skill_body_tools: bool = True,
        enabled_skills: Optional[Union[str, List[str]]] = None,
        disabled_skills: Optional[Union[str, List[str]]] = None,
        evolution_store: Optional[EvolutionStore] = None,
        max_active_skill_bodies: int = DEFAULT_MAX_ACTIVE_SKILL_BODIES,
        skill_tool_stub_language: Optional[str] = None,
    ):
        """Initialize SkillUseRail.

        Args:
            skills_dir: Skill root directory or directories.
            skill_mode: Skill expose mode, supports:
                - "all": inject all enabled skills into system prompt
                - "auto_list": add list_skill tool and let model decide when to inspect skills
            list_skill_model: Optional model used by list_skill tool.
            enable_cache: Whether to cache loaded skills across invokes.
            include_tools: Whether to register harness read_file / code / bash tools (and,
                when True, skill_tool / skill_complete in the same bundle).
            include_skill_body_tools: When ``include_tools`` is False, still register
                ``skill_tool`` and ``skill_complete`` so the skills prompt matches the tool
                list (e.g. workspace file tools come from FileSystemRail). Set False for
                profiles that must not expose skill body tools (e.g. ACP).
            enabled_skills: Optional allow-list of skill names. Supports str or List[str].
            disabled_skills: Optional deny-list of skill names. Supports str or List[str].
            evolution_store: Optional EvolutionStore for progressive disclosure experience text.
            skill_tool_stub_language: Optional override for the short ``[SKILL LOADED]`` tool
                message (``"cn"`` or ``"en"``). When None, use ``agent.deep_config.language``
                on each tool callback; if still unset, the harness default (usually ``"cn"``).
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
        self.include_skill_body_tools = include_skill_body_tools
        self.enabled_skills = self._normalize_name_set(enabled_skills)
        self.disabled_skills = self._normalize_name_set(disabled_skills)
        self.evolution_store: Optional[EvolutionStore] = evolution_store
        self.max_active_skill_bodies = max_active_skill_bodies
        self.skill_tool_stub_language = skill_tool_stub_language

        self.skills: List[Skill] = []
        self.system_prompt_builder = None

        # Cache loaded skills across invokes.
        self._skill_cache: Dict[str, Skill] = {}
        self._skill_update_at: Dict[str, float] = {}
        self._skill_order: List[str] = []

        # Cache evolution experience texts per skill name.
        self._evolution_texts: Dict[str, str] = {}

        # Track tools added by this rail only.
        self._owned_tool_names: Set[str] = set()
        self._owned_tool_ids: Set[str] = set()

    @property
    def skills_meta(self) -> List[Skill]:
        """Return all managed skills."""
        return list(self.skills)

    def get_skills_meta(self) -> List[Skill]:
        """Return all managed skills."""
        return list(self.skills)

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

            for item in sorted(root.iterdir(), key=lambda p: p.name):
                if not item.is_dir():
                    continue

                skill_md_path = item / "SKILL.md"
                if not skill_md_path.exists():
                    continue

                key = str(item.resolve())
                update_at = skill_md_path.stat().st_mtime

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
        """Register tool cards into agent and concrete tools into resource manager."""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        tools = []

        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        skill_search_roots = self._normalize_skill_dirs(self.skills_dir)
        if self.include_tools:
            tools.extend(
                [
                    ReadFileTool(self.sys_operation, language=lang, agent_id=agent_id),
                    CodeTool(self.sys_operation, language=lang, agent_id=agent_id),
                    BashTool(self.sys_operation, language=lang, agent_id=agent_id),
                    SkillTool(
                        self.sys_operation,
                        self.get_skills_meta,
                        language=lang,
                        agent_id=agent_id,
                        skill_search_roots=skill_search_roots,
                        enabled_skill_names=self.enabled_skills if self.enabled_skills else None,
                        disabled_skill_names=self.disabled_skills if self.disabled_skills else None,
                    ),
                    SkillCompleteTool(language=lang, agent_id=agent_id),
                ]
            )
        elif self.include_skill_body_tools:
            tools.extend(
                [
                    SkillTool(
                        self.sys_operation,
                        self.get_skills_meta,
                        language=lang,
                        agent_id=agent_id,
                        skill_search_roots=skill_search_roots,
                        enabled_skill_names=self.enabled_skills if self.enabled_skills else None,
                        disabled_skill_names=self.disabled_skills if self.disabled_skills else None,
                    ),
                    SkillCompleteTool(language=lang, agent_id=agent_id),
                ]
            )

        if self.skill_mode == self.SKILL_MODE_AUTO_LIST:
            tools.append(
                ListSkillTool(
                    get_skills=lambda: self.skills,
                    list_skill_model=self.list_skill_model,
                    language=lang,
                    agent_id=agent_id,
                )
            )

        for tool in tools:
            try:
                existing_tool = Runner.resource_mgr.get_tool(tool.card.id)
                if existing_tool is None:
                    Runner.resource_mgr.add_tool(tool)
                    self._owned_tool_ids.add(tool.card.id)
            except Exception as exc:
                logger.warning(
                    f"[SkillUseRail] failed to add tool resource '{tool.card.id}' "
                    f"to resource_mgr: {exc}"
                )

        if hasattr(agent, "ability_manager"):
            for tool in tools:
                try:
                    result = agent.ability_manager.add(tool.card)
                    if result.added:
                        self._owned_tool_names.add(tool.card.name)
                except Exception as exc:
                    logger.warning(
                        f"[SkillUseRail] failed to add tool card '{tool.card.name}' "
                        f"to ability_manager: {exc}"
                    )

        # Propagate active-skill-body cap to agent context engine config so
        # the window-pin helper sees the same limit as record_active_skill_body.
        ce_config = getattr(getattr(agent, "_config", None), "context_engine_config", None)
        if ce_config is not None:
            try:
                if getattr(ce_config, "max_active_skill_bodies", None) != self.max_active_skill_bodies:
                    ce_config.max_active_skill_bodies = self.max_active_skill_bodies
            except Exception as exc:
                logger.debug(f"[SkillUseRail] could not sync max_active_skill_bodies: {exc}")

    def uninit(self, agent):
        """Remove tool cards from agent ability manager."""
        if hasattr(agent, "ability_manager"):
            for tool_name in list(self._owned_tool_names):
                try:
                    agent.ability_manager.remove(tool_name)
                except Exception as exc:
                    logger.warning(
                        f"[SkillUseRail] failed to remove tool '{tool_name}' "
                        f"from ability_manager: {exc}"
                    )

        self._owned_tool_names.clear()
        self._owned_tool_ids.clear()

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Prepare skills before invoke."""
        await self._prepare_skills()
        await self._fetch_evolution_texts()
        self._consume_pending_active_skill_hints(ctx)

    def _consume_pending_active_skill_hints(self, ctx: AgentCallbackContext) -> None:
        """Pull staged hints from spawn paths into this session's state."""
        session = getattr(ctx, "session", None)
        if session is None:
            return
        try:
            session_id = session.get_session_id()
        except Exception:
            return
        hints = pop_active_skill_hints_for_session(session_id)
        if not hints:
            return
        try:
            existing = session.get_state(ACTIVE_SKILL_HINTS_STATE_KEY) or []
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []
        # De-duplicate by (skill_name, relative_file_path).
        seen = {(h.get("skill_name"), h.get("relative_file_path")) for h in existing}
        merged = list(existing)
        for h in hints:
            key = (h.get("skill_name"), h.get("relative_file_path"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(h)
        try:
            session.update_state({ACTIVE_SKILL_HINTS_STATE_KEY: merged})
        except Exception as exc:
            logger.debug(f"[SkillUseRail] failed to persist active skill hints: {exc}")

    async def _fetch_evolution_texts(self) -> None:
        """Fetch and cache evolution experience texts from EvolutionStore."""
        if self.evolution_store is None:
            return
        for skill in self.skills:
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

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Track skill body load/unload lifecycle.

        - skill_tool success: record body into session active state and replace
          original ToolMessage with a short load stub (only when active state
          write succeeded; otherwise keep full body intact).
        - skill_complete: clear session active state and mark prior skill_tool
          ToolMessage as unloaded.
        """
        inputs = getattr(ctx, "inputs", None)
        if not isinstance(inputs, ToolCallInputs):
            return

        tool_name = (inputs.tool_name or "").strip()
        session = ctx.session
        if session is None and ctx.context is not None:
            getter = getattr(ctx.context, "get_session_ref", None)
            if callable(getter):
                try:
                    session = getter()
                except Exception:
                    session = None

        trace_ids = resolve_context_trace_ids(session, ctx.context)

        if tool_name == "skill_tool":
            self._handle_skill_tool_loaded(inputs, session, trace_ids, ctx)
            return

        if tool_name == "skill_complete":
            self._handle_skill_complete(inputs, session, ctx.context, trace_ids, ctx)
            return

    def _handle_skill_tool_loaded(
        self,
        inputs: ToolCallInputs,
        session,
        trace_ids: Dict[str, Any],
        ctx: AgentCallbackContext,
    ) -> None:
        tool_msg = inputs.tool_msg
        tool_result = inputs.tool_result
        if not isinstance(tool_msg, ToolMessage):
            return
        meta = getattr(tool_msg, "metadata", None) or {}
        if not meta.get("is_skill_body"):
            return

        skill_name = meta.get("skill_name") or ""
        relative_file_path = normalize_skill_relative_file_path(
            str(meta.get("relative_file_path") or "")
        )

        raw_content = getattr(tool_msg, "content", "") or ""
        content_len_before = len(raw_content) if isinstance(raw_content, str) else len(str(raw_content))
        meta_before = skill_trace_metadata_subset(meta)

        recorded = record_active_skill_body(
            session,
            tool_msg,
            tool_result,
            max_active_skill_bodies=self.max_active_skill_bodies,
        )
        if not recorded:
            write_context_trace(
                "skill.lifecycle.skill_tool_loaded",
                {
                    **trace_ids,
                    "skill_name": skill_name,
                    "relative_file_path": relative_file_path,
                    "recorded": False,
                    "content_len_before": content_len_before,
                    "metadata_before": meta_before,
                },
            )
            logger.info(
                "[SkillUseRail] skill_tool_loaded session_id=%s recorded=False skill=%s path=%s "
                "content_len_before=%s metadata_keys=%s",
                trace_ids.get("session_id"),
                skill_name,
                relative_file_path,
                content_len_before,
                sorted(meta_before.keys()),
            )
            return

        # append_active_skill_pins_to_window reads only context.get_session_ref(). If
        # ModelContext._session_ref is unset while active state was written to
        # ctx.session, the next get_context_window would not inject the pin. Align
        # the ref to the same Session used for record_active_skill_body.
        if session is not None and ctx is not None:
            _ctx_model = getattr(ctx, "context", None)
            # [PIN_DIAG] writer-rail: log alignment attempt + before/after ids (via trace)
            _before_ref = getattr(_ctx_model, "_session_ref", None) if _ctx_model is not None else None
            _align_err: Optional[str] = None
            if _ctx_model is not None:
                try:
                    setattr(_ctx_model, "_session_ref", session)
                except Exception as _exc:
                    _align_err = repr(_exc)
            _after_ref = getattr(_ctx_model, "_session_ref", None) if _ctx_model is not None else None
            write_context_trace(
                "pin_diag.rail.align",
                {
                    **trace_ids,
                    "ctx_id": id(ctx),
                    "ctx_context_id": id(_ctx_model) if _ctx_model is not None else None,
                    "session_obj_id": id(session),
                    "before_ref_id": id(_before_ref) if _before_ref is not None else None,
                    "after_ref_id": id(_after_ref) if _after_ref is not None else None,
                    "aligned": (_after_ref is session),
                    "error": _align_err,
                },
            )

        # Replace original ToolMessage content with short load stub.
        # Models often try workspace file tools after seeing only a short ack; spell out
        # that the full body is reinjected as [ACTIVE SKILL BODY] on later turns.
        ui_lang = _resolve_skill_load_stub_ui_language(ctx, self)
        stub_core = _format_skill_load_stub_core(
            skill_name, relative_file_path, ui_language=ui_lang
        )
        tool_msg.content = stub_core
        new_meta = dict(meta)
        new_meta.update({
            "is_skill_body": False,
            "skill_body_stub": True,
            "skill_body_active": True,
            "original_is_skill_body": True,
            "skill_name": skill_name,
            "relative_file_path": relative_file_path,
            "source_tool_call_id": getattr(tool_msg, "tool_call_id", None),
        })
        tool_msg.metadata = new_meta
        meta_after = skill_trace_metadata_subset(tool_msg.metadata)
        write_context_trace(
            "skill.lifecycle.skill_tool_loaded",
            {
                **trace_ids,
                "skill_name": skill_name,
                "relative_file_path": relative_file_path,
                "recorded": True,
                "content_len_before": content_len_before,
                "content_len_after_stub": len(tool_msg.content or ""),
                "metadata_before": meta_before,
                "metadata_after": meta_after,
            },
        )
        logger.info(
            "[SkillUseRail] skill_tool_loaded session_id=%s recorded=True skill=%s path=%s "
            "content_len_before=%s content_len_after_stub=%s metadata_after_keys=%s",
            trace_ids.get("session_id"),
            skill_name,
            relative_file_path,
            content_len_before,
            len(tool_msg.content or ""),
            sorted(meta_after.keys()),
        )

    def _handle_skill_complete(
        self,
        inputs: ToolCallInputs,
        session,
        context,
        trace_ids: Dict[str, Any],
        ctx: Optional[AgentCallbackContext] = None,
    ) -> None:
        tool_result = inputs.tool_result
        skill_name = ""
        extra = getattr(tool_result, "extra_metadata", None) if tool_result is not None else None
        if isinstance(extra, dict):
            skill_name = (extra.get("unload_skill_name") or "").strip()
        if not skill_name:
            args = inputs.tool_args
            if isinstance(args, dict):
                skill_name = str(args.get("skill_name", "") or "").strip()
        if not skill_name:
            return

        removed = unregister_active_skill_body(session, skill_name)
        ctx_session = getattr(context, "_session_ref", None)
        if ctx_session is not None and ctx_session is not session:
            removed += unregister_active_skill_body(ctx_session, skill_name)

        if context is None:
            write_context_trace(
                "skill.lifecycle.skill_complete",
                {
                    **trace_ids,
                    "skill_name": skill_name,
                    "unregister_removed": removed,
                    "set_messages_called": False,
                    "tool_messages_touched": 0,
                    "buffer_changed": False,
                    "set_messages_error": None,
                    "note": "no_context",
                },
            )
            logger.info(
                "[SkillUseRail] skill_complete session_id=%s skill=%s unregister_removed=%s "
                "set_messages_called=False note=no_context",
                trace_ids.get("session_id"),
                skill_name,
                removed,
            )
            return
        try:
            buffered = list(context.get_messages() or [])
        except Exception as exc:
            write_context_trace(
                "skill.lifecycle.skill_complete",
                {
                    **trace_ids,
                    "skill_name": skill_name,
                    "unregister_removed": removed,
                    "set_messages_called": False,
                    "tool_messages_touched": 0,
                    "buffer_changed": False,
                    "set_messages_error": str(exc),
                    "note": "get_messages_failed",
                },
            )
            logger.warning(
                "[SkillUseRail] skill_complete session_id=%s skill=%s get_messages_failed: %s",
                trace_ids.get("session_id"),
                skill_name,
                exc,
            )
            return

        changed = False
        tool_messages_touched = 0
        ui_lang = _resolve_skill_load_stub_ui_language(ctx, self)
        unload_stub = _format_skill_unload_stub(skill_name, ui_language=ui_lang)
        kept: List[Any] = []
        for msg in buffered:
            meta = getattr(msg, "metadata", None) or {}
            if (
                isinstance(msg, (SystemMessage, UserMessage))
                and meta.get("active_skill_pin")
                and meta.get("skill_name") == skill_name
            ):
                changed = True
                continue
            kept.append(msg)
        buffered = kept
        for msg in buffered:
            if not isinstance(msg, ToolMessage):
                continue
            meta = getattr(msg, "metadata", None) or {}
            if meta.get("skill_name") != skill_name:
                continue
            if meta.get("skill_unloaded"):
                continue
            # Skip the skill_complete tool's own ToolMessage.
            if "unload_skill_name" in meta:
                continue
            if meta.get("skill_body_offloaded"):
                new_meta = dict(meta)
                new_meta["skill_unloaded"] = True
                msg.metadata = new_meta
                changed = True
                tool_messages_touched += 1
                continue
            if meta.get("is_skill_body"):
                # Degraded path: full body still in buffer; replace with unload stub.
                msg.content = unload_stub
            new_meta = dict(meta)
            new_meta.update({
                "is_skill_body": False,
                "skill_body_active": False,
                "skill_unloaded": True,
            })
            msg.metadata = new_meta
            changed = True
            tool_messages_touched += 1

        set_messages_called = False
        set_messages_error: Optional[str] = None
        if changed:
            try:
                context.set_messages(buffered)
                set_messages_called = True
            except Exception as exc:
                set_messages_error = str(exc)
                logger.warning(f"[SkillUseRail] failed to set_messages on unload: {exc}")

        write_context_trace(
            "skill.lifecycle.skill_complete",
            {
                **trace_ids,
                "skill_name": skill_name,
                "unregister_removed": removed,
                "set_messages_called": set_messages_called,
                "tool_messages_touched": tool_messages_touched,
                "buffer_changed": changed,
                "set_messages_error": set_messages_error,
            },
        )
        log_fn = logger.warning if set_messages_error else logger.info
        log_fn(
            "[SkillUseRail] skill_complete session_id=%s skill=%s unregister_removed=%s "
            "set_messages_called=%s tool_messages_touched=%s buffer_changed=%s err=%s",
            trace_ids.get("session_id"),
            skill_name,
            removed,
            set_messages_called,
            tool_messages_touched,
            changed,
            set_messages_error,
        )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Update system_prompt_builder with current skills before model call.

        build() and get_context_window are deferred to _railed_model_call
        so that ContextProcessor has the accurate final token budget.
        """
        if self.system_prompt_builder is None:
            return

        hints = self._read_active_skill_hints(ctx)
        skills_section = self._build_skills_section(hints=hints)
        if skills_section is not None:
            self.system_prompt_builder.add_section(skills_section)
        else:
            self.system_prompt_builder.remove_section(SectionName.SKILLS)

    def _read_active_skill_hints(self, ctx: AgentCallbackContext) -> List[Dict[str, Any]]:
        session = getattr(ctx, "session", None)
        if session is None:
            return []
        try:
            raw = session.get_state(ACTIVE_SKILL_HINTS_STATE_KEY) or []
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        return [h for h in raw if isinstance(h, dict) and h.get("skill_name")]

    def _build_skills_section(self, hints: Optional[List[Dict[str, Any]]] = None):
        """Build PromptSection from current skills."""
        language = self.system_prompt_builder.language
        if self.skill_mode == self.SKILL_MODE_ALL:
            body_lines: List[str] = []
            for idx, skill in enumerate(self.skills):
                body_lines.append(
                    build_skill_line(
                        index=idx,
                        skill_name=skill.name,
                        description=self._get_skill_description(skill),
                    )
                )
            section = build_skills_section(
                skill_lines=build_skill_lines(body_lines),
                language=language,
                mode="all",
            )
        else:
            section = build_skills_section(
                skill_lines="",
                language=language,
                mode="auto_list",
            )

        # Append parent-derived active skill hints, if any.
        if section is not None and hints:
            hint_text = self._render_active_skill_hint_block(hints, language)
            if hint_text:
                base = section.content.get(language, "")
                section.content[language] = (base + "\n\n" + hint_text) if base else hint_text
        return section

    @staticmethod
    def _render_active_skill_hint_block(hints: List[Dict[str, Any]], language: str) -> str:
        items = [
            f"- {h.get('skill_name')} ({h.get('relative_file_path') or 'SKILL.md'})"
            for h in hints
            if h.get("skill_name")
        ]
        if not items:
            return ""
        if language == "en":
            header = (
                "Parent task currently has the following skills active. "
                "If you need to follow them, call `skill_tool` to load each one yourself "
                "(child sessions do not inherit the body):"
            )
        else:
            header = (
                "父任务当前激活了以下 skill。如果你需要遵从它们，请自行调用 `skill_tool` 加载对应正文（子会话不会自动继承）："
            )
        return header + "\n" + "\n".join(items)

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

    async def _load_yaml(self, path: Path) -> Tuple[Optional[dict], str]:
        """Load YAML front matter and markdown body from SKILL.md."""
        result = await self.sys_operation.fs().read_file(
            str(path),
            mode="text",
            encoding="utf-8",
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
        yaml_data, _ = await self._load_yaml(path)
        if yaml_data is None or "description" not in yaml_data:
            raise KeyError("SKILL.md file does not contain a description field")
        return str(yaml_data["description"])

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

            for item in sorted(root.iterdir(), key=lambda p: p.name):
                if not item.is_dir():
                    continue

                skill_md_path = item / "SKILL.md"
                if not skill_md_path.exists():
                    continue

                update_at = skill_md_path.stat().st_mtime
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
