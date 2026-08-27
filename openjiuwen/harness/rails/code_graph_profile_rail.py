# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Attach a Code Graph profile (off / graph) to a coding agent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
    UserMessageInputs,
)
from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.code_graph_profile import build_code_graph_profile_prompt
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.code_graph import (
    PROMPT_MODE_LOCATE,
    PROMPT_MODE_PRODUCT,
    CodeGraphBudget,
    CodeGraphProfile,
    CodeGraphRequest,
    CodeGraphRunState,
    FIND_GRAPH_QUERY_POLICY,
    bind_code_graph_runtime,
    resolve_code_graph_profile,
)
from openjiuwen.harness.tools.code_graph import (
    LOCATE_EXAM_TOOL_NAMES,
    build_code_graph_profile_tools,
    resolve_repo_root,
)
from openjiuwen.harness.tools.code_graph._base import CodeGraphToolContext

# ``profile: graph`` hides grep/glob while the index works. Missing parser or
# UNAVAILABLE restores them. Locate exam uses its own hide list.
GRAPH_HIDDEN_SEARCH_TOOLS = ("grep", "glob")
_GRAPH_TOOL_NAMES = frozenset(LOCATE_EXAM_TOOL_NAMES)

# The graph profile has no total graph-call budget: the host agent's iteration,
# token, and time limits already stop the task, and a second cap only left it
# without retrieval halfway through a patch. Per-query bounds live in
# ``GraphQueryPolicy``; ``max_locations`` still caps what one run may select.
GRAPH_UNBOUNDED_TOOL_CALLS = 0
GRAPH_MAX_LOCATIONS = 25


class CodeGraphProfileRail(DeepAgentRail):
    """Register the tools, prompt, and run state for one Code Graph profile.

    Priority 99 runs after ``SysOperationRail`` (100), so grep and the write
    tools already exist when this rail registers find_* tools. ``profile=off``
    is a no-op: the host stays on grep / read / edit. When the parser can
    index, ``profile=graph`` drops grep/glob so the model cannot ignore the
    graph. If the parser is missing, registration fails, or a graph tool
    later returns ``UNAVAILABLE``, those search tools stay (or come back)
    and the host is the original agent again. Locate-exam mode never
    restores them. Localization does not finish the host agent: the same
    agent owns the later edit.
    """

    priority = 99

    def __init__(
        self,
        profile: Any = CodeGraphProfile.OFF,
        *,
        repo_root: str | None = None,
        config: CodeGraphConfig | None = None,
        prompt_mode: str = PROMPT_MODE_PRODUCT,
    ) -> None:
        super().__init__()
        self.profile = resolve_code_graph_profile(profile)
        self.repo_root = repo_root
        self.config = config or CodeGraphConfig()
        self.prompt_mode = prompt_mode or PROMPT_MODE_PRODUCT
        self.session_id = ""
        self.run_state: CodeGraphRunState | None = None
        self._tools: list[Any] = []
        self._hidden_search: list[tuple[Any, Any]] = []
        self._section: PromptSection | None = None
        self._agent: Any = None
        self._warmup_started = False

    def init(self, agent: Any) -> None:
        from openjiuwen.harness.deep_agent import DeepAgent

        if self.profile == CodeGraphProfile.OFF:
            return
        deep_config = getattr(agent, "deep_config", None)
        if not (isinstance(agent, DeepAgent) and deep_config and hasattr(agent, "ability_manager")):
            logger.warning(
                "CodeGraphProfileRail: host is not a DeepAgent with an ability_manager, skipping"
            )
            return
        if not _parser_ready():
            _warn_if_parser_missing()
            return
        self._agent = agent
        if not self.sys_operation:
            self.set_sys_operation(deep_config.sys_operation)
        if not self.workspace:
            self.set_workspace(deep_config.workspace)
        repo_root = self._live_repo_root(agent)
        config = getattr(deep_config, "code_graph_config", None) or self.config
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        # Conversation / run id is only for locate episode memory, never the
        # workspace graph cache key.
        self.session_id = ""
        graph_config = config if isinstance(config, CodeGraphConfig) else CodeGraphConfig()
        context = CodeGraphToolContext(
            repo_root=repo_root,
            config=graph_config,
            language=getattr(deep_config, "language", None) or "en",
            agent_id=agent_id,
            session_id=self.session_id,
            policy=FIND_GRAPH_QUERY_POLICY,
            resolve_root=lambda: self._live_repo_root(agent),
        )
        self.repo_root = repo_root
        hidden: list[tuple[Any, Any]] = []
        try:
            bind_code_graph_runtime(
                agent,
                session_id=self.session_id,
                repo_root=repo_root,
                config=context.config,
            )
            self.run_state = self._new_run_state()
            bind_code_graph_runtime(
                agent,
                session_id=self.session_id,
                repo_root=repo_root,
                config=context.config,
                run_state=self.run_state,
            )
            for tool in build_code_graph_profile_tools(
                context,
                self.run_state,
                profile=self.profile,
                prompt_mode=self.prompt_mode,
            ):
                if agent.ability_manager.get(tool.card.name) is not None:
                    continue
                agent.ability_manager.add_ability(tool.card, tool)
                self._tools.append(tool)
            self._inject_prompt(agent)
            if self._tools:
                hidden = _hide_text_search_tools(agent)
                self._hidden_search = hidden
            if isinstance(config, CodeGraphConfig) and self._tools:
                from openjiuwen.core.retrieval.code_graph.manager import configure_code_graph_manager
                from openjiuwen.core.retrieval.code_graph.watch import start_workspace_watch

                configure_code_graph_manager(config)
                start_workspace_watch(repo_root, config)
            logger.info(
                "CodeGraphProfileRail: profile=%s tools=%d repo=%s hidden_search=%d",
                self.profile.value,
                len(self._tools),
                repo_root,
                len(self._hidden_search),
            )
        except Exception as exc:  # noqa: BLE001 — never fail agent creation
            logger.warning("CodeGraphProfileRail: registration failed: %s", exc)
            self._hidden_search = list(self._hidden_search or hidden)
            self._restore_baseline(agent)

    def _new_run_state(self) -> CodeGraphRunState:
        budget = CodeGraphBudget(
            max_tool_calls=GRAPH_UNBOUNDED_TOOL_CALLS,
            max_locations=GRAPH_MAX_LOCATIONS,
        )
        return CodeGraphRunState(
            request=CodeGraphRequest(query="", budget=budget),
            profile=self.profile.value,
            prompt_mode=self.prompt_mode or PROMPT_MODE_PRODUCT,
        )

    def _inject_prompt(self, agent: Any) -> None:
        builder = getattr(agent, "system_prompt_builder", None)
        if builder is None:
            return
        content = {
            lang: build_code_graph_profile_prompt(
                self.profile,
                language=lang,
                prompt_mode=self.prompt_mode,
            )
            for lang in ("en", "cn")
        }
        self._section = PromptSection(
            name=SectionName.CODE_GRAPH,
            content=content,
            priority=90,
        )
        builder.add_section(self._section)

    async def on_user_message(self, ctx: AgentCallbackContext) -> None:
        """Bind the coding task itself; there is no TaskTool request to parse."""
        self._schedule_warmup(
            self._live_repo_root(ctx.agent),
            getattr(getattr(ctx.agent or self._agent, "deep_config", None), "code_graph_config", None)
            or self.config,
        )
        state = self.run_state
        if state is None or (state.bound and (state.request.query or "").strip()):
            return
        inputs = ctx.inputs
        if not isinstance(inputs, UserMessageInputs):
            return
        text = "\n".join(str(part) for part in inputs.parts if str(part).strip()).strip()
        if not text:
            return
        self._bind_task(text)

    def _schedule_warmup(self, repo_root: str, config: CodeGraphConfig | None) -> None:
        """Start the index as soon as the project is known. Do not block init."""
        if self._warmup_started or self.profile == CodeGraphProfile.OFF or not self._tools:
            return
        if not repo_root or not isinstance(config, CodeGraphConfig):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._warmup_started = True

        async def _warm() -> None:
            try:
                from openjiuwen.core.retrieval.code_graph.manager import get_code_graph_manager

                await get_code_graph_manager(config).ensure_fresh(repo_root, config)
            except Exception as exc:  # noqa: BLE001 — first find_* still retries
                logger.warning("CodeGraphProfileRail: warmup failed: %s", exc)

        loop.create_task(_warm())

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Report file mutations and restore grep/glob if the graph dies."""
        if self._tools:
            self._report_workspace_mutation(ctx)
        if not self._hidden_search:
            return
        if self._is_locate_exam():
            return
        if not _graph_tool_needs_search_fallback(ctx):
            return
        agent = ctx.agent if ctx.agent is not None else self._agent
        if agent is None:
            return
        restored = _restore_text_search_tools(agent, self._hidden_search)
        self._hidden_search = []
        if restored:
            logger.warning(
                "CodeGraphProfileRail: graph needs text search; restored %s",
                ",".join(GRAPH_HIDDEN_SEARCH_TOOLS),
            )

    def _live_repo_root(self, agent: Any | None = None) -> str:
        """Resolve the workspace the agent is actually in, including worktrees."""
        try:
            from openjiuwen.harness.tools.worktree.session import get_current_session

            session = get_current_session()
            worktree_path = getattr(session, "worktree_path", None) if session is not None else None
            if worktree_path:
                return str(Path(worktree_path).resolve())
        except Exception:  # noqa: BLE001
            pass
        host = agent if agent is not None else self._agent
        deep_config = getattr(host, "deep_config", None) if host is not None else None
        return resolve_repo_root(
            explicit=self.repo_root,
            project_root=getattr(deep_config, "project_root", None),
            cwd=getattr(deep_config, "cwd", None),
            workspace_root=getattr(self.workspace, "root_path", None) if self.workspace else None,
        )

    def _report_workspace_mutation(self, ctx: AgentCallbackContext) -> None:
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        name = (inputs.tool_name or "").strip()
        if not name:
            return
        root = self._live_repo_root(ctx.agent)
        from openjiuwen.core.retrieval.code_graph.manager import get_code_graph_manager

        manager = get_code_graph_manager(self.config)
        if name in {"write_file", "edit_file"}:
            path = _changed_path_from_tool(inputs)
            if path:
                manager.mark_dirty(root, [path], source=name, config=self.config)
            return
        if name in {"bash", "powershell"}:
            manager.mark_dirty_unknown(root, reason="shell", config=self.config)

    def _is_locate_exam(self) -> bool:
        mode = (self.prompt_mode or PROMPT_MODE_PRODUCT).strip().lower()
        return mode == PROMPT_MODE_LOCATE

    def _bind_task(self, text: str) -> None:
        """Attach the issue text to the run state this rail already created.

        Only the query, hints, and scope come from the text. The budget stays as
        this rail set it, which is what stops a locator default from replacing
        the profile's own limits.
        """
        from openjiuwen.harness.schema.code_graph import bind_code_graph_query

        state = self.run_state
        if state is None:
            return
        request = bind_code_graph_query(state, text)
        state.profile = self.profile.value
        self._ensure_session(request.query)

    def _ensure_session(self, query: str) -> None:
        """Reuse the episode for this (repo, task) so a refinement continues it."""
        from openjiuwen.harness.tools.code_graph.session import (
            bind_run_state,
            create_localization,
            persist_run_state,
        )

        state = self.run_state
        if state is None:
            return
        session = create_localization(self.repo_root or ".", query)
        if state.artifact_id:
            persist_run_state(state)
        else:
            bind_run_state(state, session)

    def uninit(self, agent: Any) -> None:
        # Drop graph tools and put grep back. The shared workspace index stays.
        self._restore_baseline(agent)

    def _restore_baseline(self, agent: Any) -> None:
        """Return the host to grep / read / edit. Safe to call twice."""
        hidden = self._hidden_search
        self._hidden_search = []
        if hidden:
            restored = _restore_text_search_tools(agent, hidden)
            if restored:
                logger.warning(
                    "CodeGraphProfileRail: restored %s after graph teardown",
                    ",".join(GRAPH_HIDDEN_SEARCH_TOOLS),
                )
        ability_manager = getattr(agent, "ability_manager", None)
        for tool in self._tools:
            try:
                if ability_manager is not None and ability_manager.get(tool.card.name) is tool.card:
                    ability_manager.remove_ability(tool.card.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "CodeGraphProfileRail: failed to remove %s: %s", tool.card.name, exc
                )
        self._tools = []
        builder = getattr(agent, "system_prompt_builder", None)
        if builder is not None and self._section is not None:
            try:
                builder.remove_section(SectionName.CODE_GRAPH)
            except Exception:  # noqa: BLE001
                pass
        self._section = None
        if getattr(agent, "code_graph_runtime", None) is not None:
            agent.code_graph_runtime = None
        self._warmup_started = False
        self._agent = None


def _hide_text_search_tools(agent: Any) -> list[tuple[Any, Any]]:
    """Detach grep/glob and keep (card, tool) so UNAVAILABLE can restore them."""
    manager = getattr(agent, "ability_manager", None)
    if manager is None:
        return []
    hidden: list[tuple[Any, Any]] = []
    for name in GRAPH_HIDDEN_SEARCH_TOOLS:
        pair = _detach_named_ability(manager, name)
        if pair is not None:
            hidden.append(pair)
    return hidden


def _restore_text_search_tools(agent: Any, hidden: list[tuple[Any, Any]]) -> int:
    manager = getattr(agent, "ability_manager", None)
    if manager is None:
        return 0
    restored = 0
    for card, resource in hidden:
        name = getattr(card, "name", None)
        if not name or manager.get(name) is not None:
            continue
        try:
            manager.add_ability(card, resource)
            restored += 1
        except Exception as exc:  # noqa: BLE001 — host must keep running
            logger.warning("CodeGraphProfileRail: failed to restore %s: %s", name, exc)
    return restored


def _detach_named_ability(manager: Any, name: str) -> tuple[Any, Any] | None:
    try:
        card = manager.get(name)
        if card is None:
            return None
        resource = _lookup_tool_resource(card)
        manager.remove_ability(name)
        if resource is None:
            return None
        return (card, resource)
    except Exception as exc:  # noqa: BLE001 — graph host must still start
        logger.warning("CodeGraphProfileRail: failed to hide %s: %s", name, exc)
        return None


def _lookup_tool_resource(card: Any) -> Any:
    from openjiuwen.core.runner import Runner

    tool_id = getattr(card, "id", None) or getattr(card, "name", None)
    if not tool_id:
        return None
    return Runner.resource_mgr.get_tool(tool_id)


def _graph_tool_returned_unavailable(ctx: AgentCallbackContext) -> bool:
    return _graph_tool_needs_search_fallback(ctx)


def _graph_tool_needs_search_fallback(ctx: AgentCallbackContext) -> bool:
    inputs = ctx.inputs
    if not isinstance(inputs, ToolCallInputs):
        return False
    name = (inputs.tool_name or "").strip()
    if name not in _GRAPH_TOOL_NAMES:
        return False
    return _tool_status(inputs.tool_result) in {
        CodeGraphStatus.UNAVAILABLE.value,
    }


def _changed_path_from_tool(inputs: ToolCallInputs) -> str:
    args = getattr(inputs, "tool_args", None) or {}
    if isinstance(args, dict):
        for key in ("path", "file_path", "file"):
            value = args.get(key)
            if value:
                return str(value)
    result = getattr(inputs, "tool_result", None)
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        for key in ("file_path", "path", "file"):
            value = data.get(key)
            if value:
                return str(value)
    return ""


def _tool_status(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("status") or "")
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return str(data.get("status") or "")
    return ""


def _parser_ready() -> bool:
    from openjiuwen.core.retrieval.code_graph.indexing.parser import parser_available

    return parser_available()


def _warn_if_parser_missing() -> None:
    """``profile: graph`` needs tree-sitter-language-pack; otherwise keep grep."""
    from openjiuwen.core.retrieval.code_graph.indexing.parser import parser_unavailable_reason

    if _parser_ready():
        return
    logger.warning(
        "profile=graph but tree-sitter-language-pack is missing (%s). Falling back to grep.",
        parser_unavailable_reason(),
    )


__all__ = ["CodeGraphProfileRail"]
