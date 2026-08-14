# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ProgressiveToolRail for large-scale tool usage with progressive disclosure."""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import ToolCard, ToolExposure, ToolInfo
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.sections.progressive_tool_rail import (
    build_multilingual_progressive_tool_rules_section,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.tools import ToolSearchTool
from openjiuwen.harness.tools.tool_discovery.bm25 import BM25ToolIndex

_DISCOVERED_TOOLS_KEY = "__progressive_discovered_tool_names__"
_DISCOVERY_TRACE_KEY = "__progressive_tool_discovery_trace__"


class ProgressiveToolRail(DeepAgentRail):
    """Rail that enables progressive tool discovery and callable-tool filtering."""

    priority = 90

    def __init__(self, config: DeepAgentConfig):
        """Initialize ProgressiveToolRail.

        Args:
            config: DeepAgentConfig containing progressive tool settings.
        """
        super().__init__()
        self._config = config

        self._meta_tool_names: Set[str] = set()
        # Meta tools this rail actually registered, mapped from tool name to the
        # exact card that was stored. The name is the ability-manager key, while
        # the card identity tells uninit whether this rail is still the owner or
        # another rail has since taken the name over.
        self._owned_tool_cards: Dict[str, ToolCard] = {}
        self._cached_all_tool_infos: List[ToolInfo] = []
        self._tool_search_index: Optional[BM25ToolIndex] = None
        self._tool_search_index_revision: Optional[int] = None
        self._tool_search_registry: Any = None

    def init(self, agent) -> None:
        """Register progressive meta tools to resource manager and ability manager."""
        language = getattr(self._config, "language", "cn") or "cn"
        agent_id = getattr(getattr(agent, "card", None), "id", None)

        tools = [
            ToolSearchTool(
                search_tools=self._search_tools,
                language=language,
                agent_id=agent_id,
            ),
        ]

        self._meta_tool_names = {tool.card.name for tool in tools}

        if hasattr(agent, "ability_manager"):
            for tool in tools:
                try:
                    result = agent.ability_manager.add_ability(tool.card, tool)
                    if result.added:
                        self._owned_tool_cards[tool.card.name] = tool.card
                except Exception as exc:
                    logger.warning(
                        f"[ProgressiveToolRail] failed to register tool '{tool.card.name}': {exc}"
                    )

        # Keep the registry reference, but defer the initial BM25 build until
        # DeepAgent has initialized every startup-stage rail.  Tool exposure is
        # assigned by AbilityManager at registration time; this rail only
        # consumes that decision for visibility and indexing.
        self._tool_search_registry = getattr(agent, "ability_manager", None)

    def finalize_startup(self, agent: Any) -> None:
        """Build the initial BM25 catalog after startup registrations finish."""
        self._tool_search_registry = getattr(agent, "ability_manager", None)
        self._rebuild_tool_search_index(agent)

    async def finalize_startup_async(self, agent: Any) -> None:
        """Materialize lazy tool schemas, then build the startup catalog.

        MCP-backed cards are materialized by ``AbilityManager.list_tool_info``;
        calling it here ensures those startup tools are included before the
        first BM25 snapshot instead of waiting for the first model request.
        """
        ability_manager = getattr(agent, "ability_manager", None)
        list_tool_info = getattr(ability_manager, "list_tool_info", None)
        if callable(list_tool_info):
            try:
                await list_tool_info()
            except Exception as exc:
                logger.warning(
                    "[ProgressiveToolRail] failed to materialize startup tool schemas: %s",
                    exc,
                )
        self.finalize_startup(agent)

    def uninit(self, agent) -> None:
        """Remove the meta tools this rail still owns."""
        if hasattr(agent, "ability_manager"):
            for tool_name, tool_card in list(self._owned_tool_cards.items()):
                if agent.ability_manager.get(tool_name) is not tool_card:
                    # Another owner re-registered the name after this rail did
                    # and now holds both the card and the live instance; tearing
                    # it down here would unregister that owner's tool.
                    continue
                try:
                    agent.ability_manager.remove_ability(tool_name)
                except Exception as exc:
                    logger.warning(
                        f"[ProgressiveToolRail] failed to remove tool '{tool_name}' "
                        f"from ability_manager: {exc}"
                    )

        self._owned_tool_cards.clear()
        self._meta_tool_names.clear()
        self._cached_all_tool_infos = []
        self._tool_search_index = None
        self._tool_search_index_revision = None
        self._tool_search_registry = None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Cache the full registered tool inventory for discovery authorization."""
        self._cached_all_tool_infos = await self._list_tool_infos(ctx.agent)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Update builder sections and filter callable tools for the current turn."""
        session = getattr(ctx, "session", None)

        # --------------------------------------------------
        # DEBUG 1: ctx.agent 到底是谁，builder 挂没挂上
        # --------------------------------------------------
        logger.info(
            "[ProgressiveToolRail][DEBUG] before_model_call | agent_type=%s | has_system_prompt_builder=%s",
            type(getattr(ctx, "agent", None)).__name__,
            hasattr(getattr(ctx, "agent", None), "system_prompt_builder"),
        )

        builder = self._get_prompt_builder(ctx)

        rules_section = self._build_progressive_tool_rules_section()

        builder.add_section(rules_section)

        inputs = getattr(ctx, "inputs", None)
        tools = getattr(inputs, "tools", None)
        if not isinstance(tools, list):
            logger.info(
                "[ProgressiveToolRail][DEBUG] before_model_call | inputs.tools is not a list: %s",
                type(tools).__name__,
            )
            return

        # --------------------------------------------------
        # DEBUG 2: 过滤前工具数量
        # --------------------------------------------------
        original_tool_names = [
            str(getattr(tool, "name", "") or "")
            for tool in tools
            if str(getattr(tool, "name", "") or "")
        ]
        logger.info(
            "[ProgressiveToolRail][DEBUG] before filter | tool_count=%s | first_20=%s",
            len(original_tool_names),
            original_tool_names[:20],
        )

        meta_visible_tools = set(self._meta_tool_names)
        direct_visible_tools = self._get_direct_tool_names(ctx.agent)

        logger.info(
            "[ProgressiveToolRail][DEBUG] visibility | meta=%s | direct=%s",
            sorted(meta_visible_tools),
            sorted(direct_visible_tools),
        )

        filtered_tools: List[ToolInfo] = []

        for tool in tools:
            tool_name = str(getattr(tool, "name", "") or "")
            if not tool_name:
                continue

            if tool_name in meta_visible_tools:
                filtered_tools.append(tool)
                continue

            if tool_name in direct_visible_tools:
                filtered_tools.append(tool)
                continue

        filtered_tool_names = [
            str(getattr(tool, "name", "") or "")
            for tool in filtered_tools
            if str(getattr(tool, "name", "") or "")
        ]

        # --------------------------------------------------
        # DEBUG 3: 过滤后工具数量
        # --------------------------------------------------
        logger.info(
            "[ProgressiveToolRail][DEBUG] after filter | tool_count=%s | tools=%s",
            len(filtered_tool_names),
            filtered_tool_names,
        )

        inputs.tools = filtered_tools

    @staticmethod
    def _get_direct_tool_names(agent: Any) -> Set[str]:
        """Return registered cards whose exposure policy is direct."""
        ability_manager = getattr(agent, "ability_manager", None)
        list_abilities = getattr(ability_manager, "list", None)
        if not callable(list_abilities):
            return set()
        try:
            abilities = list_abilities() or []
        except Exception:
            return set()
        if not isinstance(abilities, (list, tuple)):
            return set()
        direct_names: Set[str] = set()
        for card in abilities:
            if not isinstance(card, ToolCard):
                continue
            name = str(getattr(card, "name", "") or "")
            exposure = getattr(card, "exposure", ToolExposure.DIRECT)
            if name and exposure == ToolExposure.DIRECT:
                direct_names.add(name)
        return direct_names

    async def _list_tool_infos(self, agent) -> List[ToolInfo]:
        """List all tool infos currently registered on the agent."""
        if not hasattr(agent, "ability_manager"):
            return []
        try:
            tool_infos = await agent.ability_manager.list_tool_info()
            return list(tool_infos or [])
        except Exception as exc:
            logger.warning(f"[ProgressiveToolRail] failed to list tool infos: {exc}")
            return []

    async def _list_all_tool_infos(self) -> List[ToolInfo]:
        """Return cached full tool inventory."""
        return list(self._cached_all_tool_infos or [])

    def _list_registered_deferred_tool_infos(self, agent: Any) -> List[ToolInfo]:
        """Build index documents from registered deferred ToolCards."""
        ability_manager = getattr(agent, "ability_manager", None) or self._tool_search_registry
        list_abilities = getattr(ability_manager, "list", None)
        if not callable(list_abilities):
            return []

        documents: List[ToolInfo] = []
        try:
            abilities = list_abilities() or []
        except Exception as exc:
            logger.warning(
                f"[ProgressiveToolRail] failed to list registered cards for BM25: {exc}"
            )
            return []
        if not isinstance(abilities, (list, tuple)):
            return []

        for card in abilities:
            if not isinstance(card, ToolCard):
                continue
            if str(getattr(card, "name", "") or "") in self._meta_tool_names:
                continue

            exposure = getattr(card, "exposure", ToolExposure.DIRECT)
            if exposure != ToolExposure.DEFERRED:
                continue

            try:
                documents.append(card.tool_info())
            except Exception as exc:
                logger.warning(
                    "[ProgressiveToolRail] failed to convert deferred card '%s' "
                    "to ToolInfo: %s",
                    getattr(card, "name", ""),
                    exc,
                )
        return documents

    def _rebuild_tool_search_index(
        self,
        agent: Any = None,
        tool_infos: Optional[List[ToolInfo]] = None,
    ) -> None:
        """Build and store a new immutable BM25 index."""
        if tool_infos is None:
            tool_infos = self._list_registered_deferred_tool_infos(agent)

        self._tool_search_index = BM25ToolIndex.build(tool_infos)
        ability_manager = getattr(agent, "ability_manager", None) or self._tool_search_registry
        revision = getattr(ability_manager, "registry_revision", None)
        self._tool_search_index_revision = (
            int(revision) if isinstance(revision, int) else None
        )

    def _ensure_tool_search_index(self, agent: Any = None) -> None:
        """Reuse the startup index unless the ability registry changed."""
        ability_manager = getattr(agent, "ability_manager", None) or self._tool_search_registry
        revision = getattr(ability_manager, "registry_revision", None)
        normalized_revision = int(revision) if isinstance(revision, int) else None

        if (
            self._tool_search_index is not None
            and normalized_revision is not None
            and normalized_revision == self._tool_search_index_revision
        ):
            return

        if self._tool_search_index is not None and normalized_revision is None:
            # Test doubles and legacy callers may not expose a registry
            # revision. Keep the startup index stable in that case.
            if self._tool_search_index.document_count > 0:
                return

            if not callable(getattr(ability_manager, "list", None)):
                fallback = [
                    tool
                    for tool in self._cached_all_tool_infos
                    if str(getattr(tool, "name", "") or "") not in self._meta_tool_names
                ]
                if fallback:
                    self._rebuild_tool_search_index(tool_infos=fallback)
            return

        if ability_manager is not None and callable(getattr(ability_manager, "list", None)):
            self._rebuild_tool_search_index(agent)
            return

        fallback = [
            tool
            for tool in self._cached_all_tool_infos
            if str(getattr(tool, "name", "") or "") not in self._meta_tool_names
        ]
        self._rebuild_tool_search_index(tool_infos=fallback)

    async def _get_real_tool_infos(self) -> List[ToolInfo]:
        """Return non-meta tools from the cached inventory."""
        infos = await self._list_all_tool_infos()
        return [
            tool
            for tool in infos
            if getattr(tool, "name", "") not in self._meta_tool_names
        ]

    async def _search_tools(
        self,
        query: str,
        limit: int = 5,
        session: Any = None,
    ) -> List[Dict[str, Any]]:
        """Search BM25 and authorize matching tools for the next direct call."""
        query = (query or "").strip().lower()
        if not query:
            return []

        self._ensure_tool_search_index()
        matched = (
            self._tool_search_index.search(query, limit=max(1, limit))
            if self._tool_search_index is not None
            else []
        )

        matched_names = [str(getattr(tool, "name", "") or "") for tool in matched]
        self._authorize_discovered_tools(session, matched_names)
        self._append_trace(
            session,
            {
                "action": "tool_search",
                "query": query,
                "limit": max(1, limit),
                "matched": matched_names,
            },
        )

        return [self._build_tool_schema(tool) for tool in matched]

    def _authorize_discovered_tools(self, session: Any, names: List[str]) -> None:
        """Record search hits as callable for this session's following turn."""
        if session is None:
            return
        current = self._get_discovered_tools(session)
        self._set_discovered_tools(session, list(dict.fromkeys(current + names)))

    def _get_discovered_tools(self, session: Any) -> List[str]:
        if session is None:
            return []
        state = session.get_state(_DISCOVERED_TOOLS_KEY)
        if isinstance(state, list):
            return [str(item).strip() for item in state if str(item).strip()]
        return []

    def _set_discovered_tools(self, session: Any, names: List[str]) -> None:
        if session is None:
            return
        session.update_state({
            _DISCOVERED_TOOLS_KEY: list(
                dict.fromkeys(str(name).strip() for name in names if str(name).strip())
            )
        })

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Allow direct tools and tools found by ``tool_search`` only."""
        inputs = getattr(ctx, "inputs", None)
        tool_name = str(getattr(inputs, "tool_name", "") or "")
        if not tool_name or tool_name in self._meta_tool_names:
            return

        session = getattr(ctx, "session", None)
        ability_manager = getattr(ctx.agent, "ability_manager", None)
        card = ability_manager.get(tool_name) if ability_manager is not None else None
        if not isinstance(card, ToolCard):
            if (
                tool_name in self._get_discovered_tools(session)
                and tool_name in {
                    str(getattr(tool, "name", "") or "")
                    for tool in self._cached_all_tool_infos
                }
            ):
                return
            self._reject_tool_call(ctx, f"Unknown tool '{tool_name}'.")
            return

        exposure = getattr(card, "exposure", ToolExposure.DIRECT)
        if exposure == ToolExposure.DIRECT:
            return

        if tool_name in self._get_discovered_tools(session):
            # Isolated rail tests and lightweight adapters may expose only the
            # cached ToolInfo inventory. The real AbilityManager path still
            # requires a registered card above; the fallback only confirms
            # that the name came from this rail's search catalog.
            return

        self._reject_tool_call(
            ctx,
            f"Deferred tool '{tool_name}' must be found with tool_search before it can be called.",
        )

    @staticmethod
    def _reject_tool_call(ctx: AgentCallbackContext, message: str) -> None:
        """Prevent execution and provide a normal tool result to the model."""
        inputs = getattr(ctx, "inputs", None)
        tool_call = getattr(inputs, "tool_call", None)
        tool_call_id = str(getattr(tool_call, "id", "") or "")
        if not tool_call_id:
            tool_call_id = "unknown-tool-call"
        from openjiuwen.core.foundation.llm.schema.message import ToolMessage

        if hasattr(ctx, "extra"):
            ctx.extra["_skip_tool"] = True
        if inputs is not None:
            inputs.tool_result = {"error": message}
            inputs.tool_msg = ToolMessage(content=message, tool_call_id=tool_call_id)

    @staticmethod
    def _build_tool_schema(tool: ToolInfo) -> Dict[str, Any]:
        """Return the exact model-callable schema represented by ToolInfo."""
        return {
            "name": str(getattr(tool, "name", "") or ""),
            "description": str(getattr(tool, "description", "") or ""),
            "parameters": ProgressiveToolRail._safe_serialize_parameters(
                getattr(tool, "parameters", None)
            ),
        }

    def _build_progressive_tool_rules_section(self):
        """Build multilingual progressive-tool-rules section."""
        return build_multilingual_progressive_tool_rules_section()

    def _append_trace(self, session: Any, event: Dict[str, Any]) -> None:
        """Append progressive-tool discovery trace into session state."""
        if session is None:
            return
        trace = session.get_state(_DISCOVERY_TRACE_KEY)
        if not isinstance(trace, list):
            trace = []
        trace.append(event)
        session.update_state({_DISCOVERY_TRACE_KEY: trace})

    @staticmethod
    def _build_tool_summary(tool: ToolInfo, *, detail_level: int = 1) -> Dict[str, Any]:
        """Build structured tool summary payload."""
        name = str(getattr(tool, "name", "") or "")
        description = str(getattr(tool, "description", "") or "")
        parameters = getattr(tool, "parameters", None)

        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
        }

        if detail_level >= 2:
            payload["parameter_summary"] = ProgressiveToolRail._parameters_summary(parameters)

        if detail_level >= 3:
            payload["parameters"] = ProgressiveToolRail._safe_serialize_parameters(parameters)

        return payload

    @staticmethod
    def _safe_serialize_parameters(parameters: Any) -> Any:
        """Safely serialize tool parameter schema."""
        try:
            if inspect.isclass(parameters) and issubclass(parameters, BaseModel):
                try:
                    return parameters.model_json_schema()
                except Exception:
                    return str(parameters)
            if isinstance(parameters, dict):
                return parameters
            return str(parameters)
        except Exception as exc:
            logger.warning(f"[ProgressiveToolRail] failed to serialize parameters: {exc}")
            return str(parameters)

    @staticmethod
    def _parameters_summary(parameters: Any) -> str:
        """Build a short textual summary of parameters."""
        try:
            if inspect.isclass(parameters) and issubclass(parameters, BaseModel):
                fields = getattr(parameters, "model_fields", None)
                if isinstance(fields, dict):
                    names = list(fields.keys())
                    return f"fields: {', '.join(names)}" if names else "no declared fields"

            if isinstance(parameters, dict):
                props = parameters.get("properties")
                if isinstance(props, dict) and props:
                    return f"fields: {', '.join(props.keys())}"
                if parameters:
                    return f"schema keys: {', '.join(parameters.keys())}"
                return "empty schema"

            if parameters is None:
                return "no parameters"

            return str(parameters)
        except Exception as exc:
            logger.warning(f"[ProgressiveToolRail] failed to summarize parameters: {exc}")
            return "parameter summary unavailable"

    @staticmethod
    def _parameters_to_text(parameters: Any) -> str:
        """Flatten parameter summary and raw schema into searchable text."""
        summary = ProgressiveToolRail._parameters_summary(parameters)
        raw = ProgressiveToolRail._safe_serialize_parameters(parameters)
        return f"{summary} {raw}"

    @staticmethod
    def _get_prompt_builder(ctx: AgentCallbackContext) -> SystemPromptBuilder:
        """Fetch persistent SystemPromptBuilder from agent."""
        agent = getattr(ctx, "agent", None)
        if agent is None:
            raise RuntimeError("ProgressiveToolRail requires ctx.agent to exist.")

        builder = getattr(agent, "system_prompt_builder", None)
        if not isinstance(builder, SystemPromptBuilder):
            raise RuntimeError(
                "ProgressiveToolRail requires agent.system_prompt_builder "
                "to be an instance of SystemPromptBuilder."
            )
        return builder

__all__ = [
    "ProgressiveToolRail",
]
