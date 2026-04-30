# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team-scoped orchestration for per-member memory tools and prompt injection."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Optional, Set

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.memory.team.manager_params import (
    PromptMode,
    TeamLanguage,
    TeamLifecycle,
    TeamMemoryManagerParams,
    TeamRole,
    TeamScenario,
)
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.harness.workspace.workspace import Workspace

if TYPE_CHECKING:
    from openjiuwen.core.memory.lite.manager import MemoryIndexManager
    from openjiuwen.core.memory.team.member_memory_toolkit import MemberMemoryToolkit
    from openjiuwen.core.memory.team.shared_memory import SharedMemoryManager
    from openjiuwen.core.sys_operation.sys_operation import SysOperation
    from openjiuwen.harness.deep_agent import DeepAgent


_MAX_PERSONAL_MEMORY_BYTES = 10 * 1024


class TeamMemoryManager:
    """Team member memory manager.
        Independent of the Rail mechanism, it is explicitly invoked at key lifecycle points of TeamAgent:
        - _setup_agent: init_toolkit() + register_tools()
        - _start_coordination: load_and_inject() → injects the system prompt
        - _finalize_round: extract_after_round()
        - _stop_coordination: close()

        Resource Contract
        - register_tools(deep_agent) registers tools that do not yet exist with Runner.resource_mgr,
        and registers capabilities with the same name to deep_agent.ability_manager; simultaneously records
        
        _owned_tool_ids / _owned_tool_names and _deep_agent_for_cleanup for symmetric unmounting
        in close().
        
        - close() will: remove SECTION_NAME from system_prompt_builder, remove registered tools
        from ability_manager by name, remove tools added by this instance from resource_mgr by id,
        and then close MemberMemoryToolkit. If the registration logic was never successfully executed,
        only the toolkit is closed and collections are cleared.
    """

    SECTION_NAME = "team_memory"

    def __init__(self, params: TeamMemoryManagerParams) -> None:
        self._member_name = params.member_name
        self._team_name = params.team_name
        self._role = params.role
        self._lifecycle = params.lifecycle
        self._scenario = params.scenario
        self._embedding_config = params.embedding_config
        self._language = params.language
        self._prompt_mode = params.prompt_mode
        self._enable_auto_extract = params.enable_auto_extract
        self._read_only_source = params.read_only_source_workspace
        self._db = params.db
        self._task_manager = params.task_manager
        self._extraction_model = params.extraction_model
        self._tz_offset = params.timezone_offset_hours
        self._sys_operation = params.sys_operation

        if self._read_only_source:
            self._workspace = Workspace(root_path=self._read_only_source)
        else:
            self._workspace = params.workspace

        self._team_memory_dir = params.team_memory_dir

        self._toolkit: Optional["MemberMemoryToolkit"] = None
        self._owned_tool_names: Set[str] = set()
        self._owned_tool_ids: Set[str] = set()
        self._deep_agent_for_cleanup: Optional["DeepAgent"] = None

        self._shared_manager: Optional["SharedMemoryManager"] = None

        self._cached_base_section: Optional[PromptSection] = None

    async def init_toolkit(self) -> bool:
        if self._toolkit is not None:
            return True

        if self._workspace is None:
            logger.warning("[TeamMemoryManager] No workspace available, skipping init")
            return False

        from openjiuwen.core.memory.team.member_memory_toolkit import MemberMemoryToolkit

        self._toolkit = MemberMemoryToolkit(
            member_name=self._member_name,
            team_name=self._team_name,
            workspace=self._workspace,
            scenario=self._scenario,
            embedding_config=self._embedding_config,
            sys_operation=self._sys_operation,
            read_only=bool(self._read_only_source),
        )

        success = await self._toolkit.initialize()
        if not success:
            logger.warning("[TeamMemoryManager] Toolkit init failed, memory tools unavailable")
            return False

        if self._team_memory_dir:
            from openjiuwen.core.memory.team.shared_memory import SharedMemoryManager

            self._shared_manager = SharedMemoryManager(
                team_memory_dir=self._team_memory_dir,
                sys_operation=self._sys_operation,
            )
            await self._shared_manager.ensure_dir()

        logger.info(
            f"[TeamMemoryManager] Initialized for {self._team_name}.{self._member_name} "
            f"lifecycle={self._lifecycle} scenario={self._scenario} "
            f"read_only={bool(self._read_only_source)}"
        )
        return True

    @staticmethod
    def _strip_memory_rails_from_deep_agent(deep_agent: "DeepAgent") -> int:
        from openjiuwen.harness.rails.memory.coding_memory_rail import CodingMemoryRail
        from openjiuwen.harness.rails.memory.memory_rail import MemoryRail

        strip_fn = getattr(deep_agent, "strip_rails_by_type", None)
        if callable(strip_fn):
            return int(strip_fn((MemoryRail, CodingMemoryRail)))
        return 0

    def register_tools(self, deep_agent: "DeepAgent") -> None:
        """注册记忆工具到 DeepAgent，同时移除已有 MemoryRail/CodingMemoryRail。

        幂等：已注册过则跳过。

        副作用登记在 ``_owned_*`` 与 ``_deep_agent_for_cleanup``，由 ``close()`` 回收。
        """
        if self._owned_tool_names:
            return

        rails_removed = self._strip_memory_rails_from_deep_agent(deep_agent)
        if rails_removed:
            logger.info(
                f"[TeamMemoryManager] Stripped {rails_removed} memory rail(s) "
                f"for {self._team_name}.{self._member_name}"
            )

        if not self._toolkit or not hasattr(deep_agent, "ability_manager"):
            return

        self._deep_agent_for_cleanup = deep_agent

        for tool in self._toolkit.get_tools():
            try:
                tool_card = tool.card
                if not tool_card or not tool_card.id:
                    continue
                existing = Runner.resource_mgr.get_tool(tool_card.id)
                if existing is None:
                    Runner.resource_mgr.add_tool(tool)
                    self._owned_tool_ids.add(tool_card.id)
                result = deep_agent.ability_manager.add(tool_card)
                if result.added:
                    self._owned_tool_names.add(tool_card.name)
                    logger.info(
                        f"[TeamMemoryManager] Registered tool: {tool_card.name} ({tool_card.id})"
                    )
            except Exception as exc:
                logger.warning(f"[TeamMemoryManager] Failed to register tool: {exc}")

    async def _fetch_personal_memory_for_prompt(self, query: str) -> Optional[str]:
        coding = (self._scenario or "general").strip().lower() == "coding"
        workspace = self._workspace
        index_manager: Optional["MemoryIndexManager"] = (
            self._toolkit.manager if self._toolkit else None
        )
        sys_operation: Optional["SysOperation"] = self._sys_operation

        has_query = bool(query)
        has_runtime = workspace is not None and sys_operation is not None
        has_index = index_manager is not None

        if has_query and has_runtime and has_index:
            try:
                node_name = "coding_memory" if coding else "memory"
                memory_dir_path = str(workspace.get_node_path(node_name) or "")
                results = await index_manager.search(query, opts={"max_results": 5})
                if results:
                    parts: list[str] = []
                    total_bytes = 0
                    for r in results:
                        r_path = r.get("path", "")
                        if r_path.endswith("MEMORY.md"):
                            continue
                        full_path = os.path.join(memory_dir_path, r_path)
                        try:
                            file_result = await sys_operation.fs().read_file(full_path)
                            content = (
                                file_result.data.content
                                if file_result and file_result.data
                                else ""
                            )
                        except Exception:
                            content = ""
                        if not content:
                            continue
                        content_bytes = len(content.encode("utf-8"))
                        if total_bytes + content_bytes > _MAX_PERSONAL_MEMORY_BYTES:
                            remaining = _MAX_PERSONAL_MEMORY_BYTES - total_bytes
                            if remaining > 200:
                                parts.append(
                                    f"### {r_path}\n\n{content[:remaining]}\n... (truncated)"
                                )
                            break
                        parts.append(f"### {r_path}\n\n{content}")
                        total_bytes += content_bytes
                    if parts:
                        return "\n\n---\n\n".join(parts)
            except Exception as e:
                logger.error(f"[_fetch_personal_memory_for_prompt] search branch failed: {e}")

        if workspace and sys_operation:
            try:
                node_name = "coding_memory" if coding else "memory"
                memory_dir_path = workspace.get_node_path(node_name)
                if memory_dir_path:
                    index_path = os.path.join(str(memory_dir_path), "MEMORY.md")
                    result = await sys_operation.fs().read_file(index_path)
                    try:
                        content = result.data.content
                        return content.strip() if content else None
                    except (AttributeError, TypeError):
                        return None
            except Exception:
                logger.error(f"[_fetch_personal_memory_for_prompt] read MEMORY.md failed: {e}")

        return None

    async def load_and_inject(self, deep_agent: "DeepAgent", query: str = "") -> None:
        builder = getattr(deep_agent, "system_prompt_builder", None)
        if builder is None:
            return

        if self._cached_base_section is None:
            from openjiuwen.harness.prompts.sections.coding_memory import (
                build_coding_memory_section,
            )
            from openjiuwen.harness.prompts.sections.memory import build_memory_section

            if self._scenario == "coding":
                memory_dir = (
                    str(self._workspace.get_node_path("coding_memory"))
                    if self._workspace
                    else "coding_memory/"
                )
                base = build_coding_memory_section(
                    language=self._language,
                    read_only=bool(self._read_only_source),
                    memory_dir=memory_dir,
                )
            else:
                base = build_memory_section(
                    language=self._language,
                    read_only=bool(self._read_only_source),
                    is_proactive=(self._prompt_mode == "proactive"),
                )
            if base is None:
                return
            self._cached_base_section = PromptSection(
                name=self.SECTION_NAME,
                content=base.content,
                priority=base.priority,
            )

        section = PromptSection(
            name=self._cached_base_section.name,
            content=dict(self._cached_base_section.content),
            priority=self._cached_base_section.priority,
        )

        cn = self._language == "cn"

        personal_content = await self._fetch_personal_memory_for_prompt(query)
        if personal_content:
            header = "\n\n## 你的相关记忆\n\n" if cn else "\n\n## Your relevant memories\n\n"
            for key in section.content:
                section.content[key] += header + personal_content

        if self._shared_manager:
            try:
                team_summary = await self._shared_manager.read_team_summary()
                if team_summary:
                    header = "\n\n## 团队共享记忆\n\n" if cn else "\n\n## Team shared memory\n\n"
                    for key in section.content:
                        section.content[key] += header + team_summary
            except Exception as e:
                logger.warning(f"[TeamMemoryManager] Failed to read team summary: {e}")

        builder.remove_section(self.SECTION_NAME)
        builder.add_section(section)

    async def extract_after_round(self) -> None:
        if not self._enable_auto_extract:
            return
        if self._lifecycle != "persistent":
            return
        if self._role != "leader":
            return
        if not self._team_memory_dir or not self._db:
            return

        from openjiuwen.core.memory.team.extractor import extract_team_memories

        try:
            await extract_team_memories(
                team_name=self._team_name,
                db=self._db,
                task_manager=self._task_manager,
                team_memory_dir=self._team_memory_dir,
                sys_operation=self._sys_operation,
                model=self._extraction_model,
                tz_offset_hours=self._tz_offset,
            )
        except Exception as e:
            logger.warning(f"[TeamMemoryManager] extract_after_round failed: {e}")

    async def close(self) -> None:
        tool_ids = list(self._owned_tool_ids)
        tool_names = list(self._owned_tool_names)
        deep_agent = self._deep_agent_for_cleanup

        if deep_agent is not None:
            builder = getattr(deep_agent, "system_prompt_builder", None)
            if builder is not None:
                try:
                    builder.remove_section(self.SECTION_NAME)
                except Exception as e:
                    logger.warning(f"[TeamMemoryManager] remove_section failed: {e}")

            am = getattr(deep_agent, "ability_manager", None)
            if am is not None and tool_names:
                try:
                    am.remove(tool_names)
                except Exception as e:
                    logger.warning(f"[TeamMemoryManager] ability_manager.remove failed: {e}")

        self._deep_agent_for_cleanup = None
        self._owned_tool_names.clear()
        self._owned_tool_ids.clear()

        for tid in tool_ids:
            try:
                Runner.resource_mgr.remove_tool(tid)
            except Exception as e:
                logger.warning(f"[TeamMemoryManager] remove_tool({tid}) failed: {e}")

        if self._toolkit:
            try:
                await self._toolkit.close()
            except Exception as e:
                logger.warning(f"[TeamMemoryManager] toolkit close failed: {e}")
            self._toolkit = None

        self._cached_base_section = None

        logger.info(f"[TeamMemoryManager] Closed for {self._team_name}.{self._member_name}")

    @property
    def extraction_model(self):
        return self._extraction_model

    def set_extraction_model(self, model):
        self._extraction_model = model


__all__ = [
    "PromptMode",
    "TeamLanguage",
    "TeamLifecycle",
    "TeamMemoryManager",
    "TeamMemoryManagerParams",
    "TeamRole",
    "TeamScenario",
]
