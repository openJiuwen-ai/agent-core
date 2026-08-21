# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Attach a Code Graph profile (off / graph) to a coding agent."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    UserMessageInputs,
)
from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.code_graph_profile import build_code_graph_profile_prompt
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.code_graph import (
    PROMPT_MODE_PRODUCT,
    CodeGraphBudget,
    CodeGraphProfile,
    CodeGraphRequest,
    CodeGraphRunState,
    FIND_GRAPH_QUERY_POLICY,
    resolve_code_graph_profile,
)
from openjiuwen.harness.tools.code_graph import (
    build_code_graph_profile_tools,
    resolve_repo_root,
)
from openjiuwen.harness.tools.code_graph._base import CodeGraphToolContext

# The graph profile has no total graph-call budget: the host agent's iteration,
# token, and time limits already stop the task, and a second cap only left it
# without retrieval halfway through a patch. Per-query bounds live in
# ``GraphQueryPolicy``; ``max_locations`` still caps what one run may select.
GRAPH_UNBOUNDED_TOOL_CALLS = 0
GRAPH_MAX_LOCATIONS = 25


class CodeGraphProfileRail(DeepAgentRail):
    """Register the tools, prompt, and run state for one Code Graph profile.

    Priority 99 runs after ``SysOperationRail`` (100), so grep and the write
    tools already exist when this rail registers find_* tools. Localization
    does not finish the host agent: the same agent owns the later edit.
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
        self._section: PromptSection | None = None
        self._agent: Any = None

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
        self._agent = agent
        if not self.sys_operation:
            self.set_sys_operation(deep_config.sys_operation)
        if not self.workspace:
            self.set_workspace(deep_config.workspace)
        repo_root = resolve_repo_root(
            explicit=self.repo_root,
            project_root=getattr(deep_config, "project_root", None),
            cwd=getattr(deep_config, "cwd", None),
            workspace_root=getattr(self.workspace, "root_path", None) if self.workspace else None,
        )
        config = getattr(deep_config, "code_graph_config", None) or self.config
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        # Graph-on-coding-agent pins one index this session may refresh in place.
        self.session_id = (
            f"{agent_id or uuid4().hex}" if self.profile == CodeGraphProfile.GRAPH else ""
        )
        context = CodeGraphToolContext(
            repo_root=repo_root,
            config=config if isinstance(config, CodeGraphConfig) else CodeGraphConfig(),
            language=getattr(deep_config, "language", None) or "en",
            agent_id=agent_id,
            session_id=self.session_id,
            policy=FIND_GRAPH_QUERY_POLICY,
        )
        self.repo_root = repo_root
        agent._code_graph_session_id = self.session_id  # noqa: SLF001
        agent._code_graph_repo_root = repo_root  # noqa: SLF001
        agent._code_graph_config = context.config  # noqa: SLF001
        try:
            self.run_state = self._new_run_state()
            agent._code_graph_run_state = self.run_state  # noqa: SLF001 — tools share it
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
            logger.info(
                "CodeGraphProfileRail: profile=%s tools=%d repo=%s",
                self.profile.value,
                len(self._tools),
                repo_root,
            )
        except Exception as exc:  # noqa: BLE001 — never fail agent creation
            logger.warning("CodeGraphProfileRail: registration failed: %s", exc)
            self._tools = []

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
        if self.session_id:
            from openjiuwen.core.retrieval.code_graph.manager import get_code_graph_manager

            get_code_graph_manager().drop_session(self.session_id)
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
        self._agent = agent
        builder = getattr(agent, "system_prompt_builder", None)
        if builder is not None and self._section is not None:
            try:
                builder.remove_section(SectionName.CODE_GRAPH)
            except Exception:  # noqa: BLE001
                pass
        self._section = None
        self._agent = None


__all__ = ["CodeGraphProfileRail"]
