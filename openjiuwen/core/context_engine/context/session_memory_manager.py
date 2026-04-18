# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.sys_operation import SysOperation

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import AgentCard, ReActAgent
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

_SESSION_MEMORY_STATE_KEY = "__session_memory__"
_CONTEXT_MESSAGE_ID_KEY = "context_message_id"


DEFAULT_SESSION_MEMORY_TEMPLATE = """# Session Title
_A short and distinctive 5-10 word descriptive title for the session. Super info dense, no filler_

# Current State
_What is actively being worked on right now? Pending tasks not yet completed. Immediate next steps._

# Task specification
_What did the user ask to build? Any design decisions or other explanatory context_

# Files and Functions
_What are the important files? In short, what do they contain and why are they relevant?_

# Workflow
_What bash commands are usually run and in what order? How to interpret their output if not obvious?_

# Errors & Corrections
_Errors encountered and how they were fixed. What did the user correct? What approaches failed and should not be tried again?_

# Codebase and System Documentation
_What are the important system components? How do they work/fit together?_

# Learnings
_What has worked well? What has not? What to avoid? Do not duplicate items from other sections_

# Key results
_If the user asked a specific output such as an answer to a question, a table, or other document, repeat the exact result here_

# Worklog
_Step by step, what was attempted, done? Very terse summary for each step_
"""


DEFAULT_SESSION_MEMORY_PROMPT = (
    """IMPORTANT: This message and these instructions are NOT part of the actual user conversation. """
    """Do NOT include any references to \"note-taking\", \"session notes extraction\", or these """
    """update instructions in the notes content.

Based on the user conversation above (excluding this instruction message, system prompt"""
    """entries, and past session summaries), update the session notes content.

The file {{notesPath}} has already been read for you. Here are its current contents:
<current_notes_content>
{{currentNotes}}
</current_notes_content>

Your ONLY task is to use the edit_file tool to update the notes file, then stop.
You may make multiple edit_file calls until every section is updated.
Do not call any other tools.
Preserve all section headers and italic guidance lines exactly.
After the file has been updated, reply with a short confirmation only.
"""
)


class SessionMemoryConfig(BaseModel):
    trigger_tokens: int = Field(default=10000, gt=0)
    trigger_add_tokens: int = Field(default=5000, gt=0)
    tool_min_: int = Field(default=3, gt=0)
    model: ModelRequestConfig | None = None
    model_client: ModelClientConfig | None = None


class SessionMemoryUpdateAgent:
    """Dedicated session-memory updater backed by a real ReActAgent."""

    def __init__(self, config: SessionMemoryConfig):
        self._config = config
        self._agent: ReActAgent | None = None
        self._agent_card: AgentCard | None = None
        self._sys_operation: SysOperation | None = None
        self._inherited_system_prompt: str = ""
        self._tool_namespace = f"session_memory_update_{uuid.uuid4().hex}"
        self._workspace_root: str | None = None

    def bind_model_defaults(
        self,
        model_config: ModelRequestConfig | None,
        model_client_config: ModelClientConfig | None,
    ) -> None:
        if self._config.model is None:
            self._config.model = model_config
        if self._config.model_client is None:
            self._config.model_client = model_client_config

    def set_inherited_system_prompt(self, inherited_system_prompt: str) -> None:
        self._inherited_system_prompt = inherited_system_prompt
        self._refresh_prompt_template()

    def get_inherited_system_prompt(self) -> str:
        return self._inherited_system_prompt

    async def invoke(
        self,
        *,
        full_context_messages: List[BaseMessage],
        notes_path: Path,
        current_notes: str,
    ) -> None:
        self._ensure_agent(notes_path)
        if self._agent is None:
            raise RuntimeError("Session memory update agent is not initialized")
        query = build_session_memory_prompt(str(notes_path), current_notes)
        session = self._create_agent_session()
        inputs = {
            "query": query,
            "conversation_id": self._tool_namespace,
        }
        await session.pre_run(inputs=inputs)
        try:
            await self._prime_inherited_context(session, full_context_messages)
            response = await self._agent.invoke(inputs, session=session)
        finally:
            await session.post_run()
        _ = response

    async def _prime_inherited_context(
        self,
        session,
        full_context_messages: List[BaseMessage],
    ) -> None:
        if self._agent is None:
            return
        if not full_context_messages:
            return
        init_context = getattr(self._agent, "_init_context", None)
        if init_context is None:
            raise RuntimeError("Session memory update agent does not support context initialization")
        context = await init_context(session)
        if context.get_messages():
            logger.warning("agent context is empty")
            return
        await context.add_messages(full_context_messages)

    def _ensure_agent(
        self,
        notes_path: Path,
    ) -> None:
        if self._agent is not None and self._workspace_root == str(notes_path.parent.parent):
            return
        from openjiuwen.core.runner import Runner
        from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
        from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard

        workspace_root = notes_path.parent.parent

        sysop_card = SysOperationCard(
            id=f"{self._tool_namespace}_sysop",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(work_dir=str(workspace_root)),
        )
        self._sys_operation = SysOperation(sysop_card)

        agent_card = AgentCard(
            id=f"{self._tool_namespace}_agent",
            name="session_memory_update_agent",
            description="Updates the session memory markdown file using filesystem tools.",
        )
        self._agent_card = agent_card
        prompt_template = self._build_prompt_template(self._inherited_system_prompt)
        agent_config = ReActAgentConfig(
            model_name=self._config.model.model_name,
            prompt_template=prompt_template,
            max_iterations=10,
            model_client_config=self._config.model_client,
            model_config_obj=self._config.model,
        )
        agent = ReActAgent(card=agent_card).configure(agent_config)

        for tool in self._build_tools(self._sys_operation):
            existing_tool = Runner.resource_mgr.get_tool(tool.card.id)
            if existing_tool is None:
                result = Runner.resource_mgr.add_tool(tool, tag=agent_card.id)
                if result.is_err():
                    raise RuntimeError(f"Failed to register session memory tool: {result.msg()}")
            agent.ability_manager.add(tool.card)

        self._agent = agent
        self._workspace_root = str(workspace_root)

    @staticmethod
    def _build_prompt_template(inherited_system_prompt: str) -> List[Dict[str, str]]:
        prompt_template: List[Dict[str, str]] = []
        inherited_system_prompt = (inherited_system_prompt or "").strip()
        if inherited_system_prompt:
            prompt_template.append(
                {
                    "role": "system",
                    "content": inherited_system_prompt,
                }
            )
        return prompt_template

    def _refresh_prompt_template(self) -> None:
        if self._agent is None:
            return
        config = getattr(self._agent, "_config", None)
        if config is not None:
            config.prompt_template = self._build_prompt_template(self._inherited_system_prompt)

    def _create_agent_session(self):
        if self._agent_card is None:
            raise RuntimeError("Session memory update agent card is not initialized")
        from openjiuwen.core.session.agent import create_agent_session

        return create_agent_session(
            session_id=f"{self._tool_namespace}_{uuid.uuid4().hex[:8]}",
            card=self._agent_card,
        )

    def _build_tools(self, operation: SysOperation) -> List[Any]:
        from openjiuwen.harness.tools.filesystem import EditFileTool, ReadFileTool

        tools = [EditFileTool(operation), ReadFileTool(operation)]
        for tool in tools:
            tool.card.id = f"{self._tool_namespace}.{tool.card.name}"
        return tools


def _build_session_memory_runtime(
    *,
    memory_path: str = "",
    initialized: bool = False,
    tokens_at_last_update: int = 0,
    tool_calls_at_last_update: int = 0,
    last_summarized_message_count: int = 0,
    notes_upto_message_id: str | None = None,
    is_extracting: bool = False,
) -> Dict[str, Any]:
    return {
        "memory_path": memory_path,
        "initialized": initialized,
        "is_extracting": is_extracting,
        "tokens_at_last_update": tokens_at_last_update,
        "tool_calls_at_last_update": tool_calls_at_last_update,
        "last_summarized_message_count": last_summarized_message_count,
        "notes_upto_message_id": notes_upto_message_id,
    }


def get_session_memory_runtime(session: Any) -> Dict[str, Any]:
    if session is None or not hasattr(session, "get_state"):
        logger.info("Session memory runtime is empty")
        return _build_session_memory_runtime()
    state = session.get_state(_SESSION_MEMORY_STATE_KEY) or {}
    if not isinstance(state, dict):
        logger.info("Session memory runtime is not dict, will return init memory state dict")
        return _build_session_memory_runtime()
    return {
        **dict(state),
    }


def update_session_memory_runtime(session: Any, state: Dict[str, Any]) -> None:
    if session is None or not hasattr(session, "update_state"):
        return
    existing = get_session_memory_runtime(session)
    merged = {**existing, **dict(state)}
    session.update_state({_SESSION_MEMORY_STATE_KEY: merged})


def invalidate_session_memory_anchor(session: Any) -> None:
    if session is None or not hasattr(session, "update_state"):
        return
    update_session_memory_runtime(
        session,
        {
            "tokens_at_last_update": 0,
            "last_summarized_message_count": 0,
            "notes_upto_message_id": None,
        },
    )


def get_context_message_id(message: BaseMessage) -> str | None:
    metadata = getattr(message, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    message_id = metadata.get(_CONTEXT_MESSAGE_ID_KEY)
    return message_id if isinstance(message_id, str) and message_id else None


def find_message_index_by_context_message_id(messages: List[BaseMessage], message_id: str | None) -> int:
    if not message_id:
        return -1
    for index, message in enumerate(messages):
        if get_context_message_id(message) == message_id:
            return index
    return -1


def find_last_completed_api_round_end(messages: List[BaseMessage]) -> int:
    completed_rounds = group_completed_api_rounds(messages)
    if not completed_rounds:
        return 0
    return completed_rounds[-1][1]


def group_completed_api_rounds(messages: List[BaseMessage]) -> List[tuple[int, int]]:
    rounds: List[tuple[int, int]] = []
    current_start: int | None = None
    pending_tool_call_ids: set[str] | None = None

    for index, message in enumerate(messages):
        if current_start is None:
            current_start = index
        elif isinstance(message, UserMessage) and pending_tool_call_ids is None:
            current_start = index

        if isinstance(message, AssistantMessage):
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                pending_tool_call_ids = {
                    getattr(tool_call, "id", "") or "" for tool_call in tool_calls if getattr(tool_call, "id", "") or ""
                }
                if not pending_tool_call_ids:
                    rounds.append((current_start, index + 1))
                    current_start = None
                continue
            rounds.append((current_start, index + 1))
            current_start = None
            pending_tool_call_ids = None
            continue

        if isinstance(message, ToolMessage) and pending_tool_call_ids is not None:
            tool_call_id = getattr(message, "tool_call_id", "") or ""
            if tool_call_id in pending_tool_call_ids:
                pending_tool_call_ids.discard(tool_call_id)
            if not pending_tool_call_ids:
                rounds.append((current_start, index + 1))
                current_start = None
                pending_tool_call_ids = None

    return rounds


def build_session_memory_prompt(notes_path: str, current_notes: str) -> str:
    return DEFAULT_SESSION_MEMORY_PROMPT.replace("{{notesPath}}", notes_path).replace("{{currentNotes}}", current_notes)


def build_system_prompt_text(messages: List[BaseMessage]) -> str:
    if not messages:
        return ""
    first_message = messages[0]
    if not isinstance(first_message, SystemMessage):
        return ""
    return first_message.content


class SessionMemoryManager:
    def __init__(self, config: SessionMemoryConfig):
        self.config = config
        self._tasks: dict[str, asyncio.Task] = {}
        self._task_owners: dict[str, tuple[Any, ModelContext | None]] = {}
        self._update_agent = SessionMemoryUpdateAgent(config)

    def bind_model_defaults(
        self,
        model_config: ModelRequestConfig | None,
        model_client_config: ModelClientConfig | None,
    ) -> None:
        if self.config.model is None:
            self.config.model = model_config
        if self.config.model_client is None:
            self.config.model_client = model_client_config
        self._update_agent.bind_model_defaults(model_config, model_client_config)

    async def maybe_schedule_update(
        self,
        ctx: AgentCallbackContext,
        *,
        workspace,
    ) -> None:
        if workspace is None or ctx.session is None:
            return

        session_id = ctx.session.get_session_id()
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            return

        context_window = self.collect_context_window(ctx)
        completed_context_window = self._truncate_context_window_to_completed_api_round(context_window)
        notes_path = self._get_session_memory_path(workspace, session_id)
        runtime_update = {
            "session_id": session_id,
            "memory_path": str(notes_path),
        }
        update_session_memory_runtime(ctx.session, runtime_update)
        if not self.should_update(
            ctx.session,
            ctx.context,
            completed_context_window,
        ):
            logger.info("[SessionMemory] skip schedule: should_update returned False session_id=%s", session_id)
            return

        runtime = get_session_memory_runtime(ctx.session)
        runtime["is_extracting"] = True
        update_session_memory_runtime(ctx.session, runtime)

        task = asyncio.create_task(
            self._update_background(ctx, workspace, completed_context_window),
            name=f"session-memory-{session_id}",
        )
        self._task_owners[session_id] = (ctx.session, ctx.context)
        task.add_done_callback(lambda done: self._on_task_done(session_id, done))
        self._tasks[session_id] = task

    def update_inherited_system_prompt(
        self,
        ctx: AgentCallbackContext,
    ) -> None:
        messages = list(getattr(ctx.inputs, "messages", None) or [])
        inherited_system_prompt = build_system_prompt_text(messages)
        self._update_agent.set_inherited_system_prompt(inherited_system_prompt)

    @staticmethod
    def collect_context_window(ctx: AgentCallbackContext) -> ContextWindow:
        if ctx.context is None:
            return ContextWindow(system_messages=[], context_messages=[], tools=[])
        return ContextWindow(
            system_messages=[],
            context_messages=list(ctx.context.get_messages()),
            tools=[],
        )

    def shutdown(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._task_owners.clear()

    def should_update(
        self,
        session,
        context: ModelContext | None,
        context_window: ContextWindow,
    ) -> bool:
        messages = list(context_window.context_messages)
        if session is None or context is None or not messages:
            logger.info(
                "[SessionMemory] should_update skipped session_exists=%s context_exists=%s messages=%s",
                session is not None,
                context is not None,
                len(messages),
            )
            return False

        runtime = self._get_runtime_state(session)
        current_tokens = self._count_tokens(context, context_window)
        if not runtime["initialized"]:
            if current_tokens >= self.config.trigger_tokens:
                logger.info(
                    "[SessionMemory] should_update triggered: tokens=%s threshold=%s",
                    current_tokens,
                    self.config.trigger_tokens,
                )
                runtime["initialized"] = True
                self._set_runtime_state(session, runtime)
                return True
            logger.info(
                "[SessionMemory] should_update skipped: init threshold not reached tokens=%s threshold=%s",
                current_tokens,
                self.config.trigger_tokens,
            )
            return False

        total_tool_calls = self._count_tool_calls(messages)
        baseline_reset = False
        if current_tokens < runtime["tokens_at_last_update"]:
            logger.info(
                "[SessionMemory] token baseline reset after context shrink current=%s previous=%s",
                current_tokens,
                runtime["tokens_at_last_update"],
            )
            runtime["tokens_at_last_update"] = 0
            baseline_reset = True
        if total_tool_calls < runtime["tool_calls_at_last_update"]:
            logger.info(
                "[SessionMemory] tool-call baseline reset after context shrink current=%s previous=%s",
                total_tool_calls,
                runtime["tool_calls_at_last_update"],
            )
            runtime["tool_calls_at_last_update"] = 0
            baseline_reset = True
        if baseline_reset:
            self._set_runtime_state(session, runtime)

        tokens_since_last = current_tokens - runtime["tokens_at_last_update"]
        if tokens_since_last < self.config.trigger_add_tokens:
            logger.info(
                "[SessionMemory] should_update skipped: delta tokens=%s threshold=%s",
                tokens_since_last,
                self.config.trigger_add_tokens,
            )
            return False

        tool_calls_since_last = total_tool_calls - runtime["tool_calls_at_last_update"]
        if tool_calls_since_last < self.config.tool_min_:
            logger.info(
                "[SessionMemory] should_update skipped: delta tokens=%s threshold=%s",
                tool_calls_since_last,
                self.config.tool_min_,
            )
            return False
        return True

    async def _update_background(
        self,
        ctx: AgentCallbackContext,
        workspace,
        context_window: ContextWindow,
    ) -> None:
        if ctx.session is None:
            return

        messages = list(context_window.context_messages)
        session = ctx.session
        session_id = session.get_session_id()
        runtime = self._get_runtime_state(session)
        notes_path = self._get_session_memory_path(workspace, session_id)
        current_notes = self._read_or_init_session_memory(notes_path)

        await self._update_agent.invoke(
            full_context_messages=context_window.get_messages(),
            notes_path=notes_path,
            current_notes=current_notes,
        )
        if ctx.context is not None:
            runtime["tokens_at_last_update"] = self._count_tokens(ctx.context, context_window)
        runtime["tool_calls_at_last_update"] = self._count_tool_calls(messages)
        runtime["last_summarized_message_count"] = len(messages)
        runtime["notes_upto_message_id"] = get_context_message_id(messages[-1]) if messages else None
        runtime["initialized"] = True
        runtime["is_extracting"] = False
        self._set_runtime_state(session, runtime)
        logger.info(
            "[SessionMemory] update complete notes_upto=%s count=%s tokens=%s tool_calls=%s",
            runtime["notes_upto_message_id"],
            runtime["last_summarized_message_count"],
            runtime["tokens_at_last_update"],
            runtime["tool_calls_at_last_update"],
        )

    @staticmethod
    def _get_session_memory_path(workspace, session_id: str) -> Path:
        return Path(workspace.root_path) / "context" / f"{session_id}_context" / "session_memory" / "session_context.md"

    @staticmethod
    def _read_or_init_session_memory(path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")

        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        template_path = path.parents[2] / "session_memory.md"
        if template_path.exists():
            shutil.copy2(template_path, path)
            return path.read_text(encoding="utf-8")

        path.write_text(DEFAULT_SESSION_MEMORY_TEMPLATE, encoding="utf-8")
        return DEFAULT_SESSION_MEMORY_TEMPLATE

    @staticmethod
    def _count_tool_calls(messages: List[BaseMessage]) -> int:
        total = 0
        for message in messages:
            if isinstance(message, AssistantMessage):
                total += len(getattr(message, "tool_calls", None) or [])
        return total

    @staticmethod
    def _count_tokens(
        context: ModelContext,
        context_window: ContextWindow,
    ) -> int:
        token_counter = context.token_counter()
        all_messages = list(context_window.system_messages or []) + list(context_window.context_messages or [])
        if token_counter is not None:
            try:
                return token_counter.count_messages(all_messages)
            except Exception:
                logger.debug("Failed to count session memory tokens with token counter", exc_info=True)
        return sum(SessionMemoryManager._estimate_message_tokens(message) for message in all_messages)

    @staticmethod
    def _estimate_message_tokens(message: BaseMessage) -> int:
        return ContextUtils.estimate_message_tokens(message)

    @staticmethod
    def _find_last_completed_api_round_end(messages: List[BaseMessage]) -> int:
        return find_last_completed_api_round_end(messages)

    @classmethod
    def _truncate_messages_to_completed_api_round(cls, messages: List[BaseMessage]) -> List[BaseMessage]:
        completed_end = cls._find_last_completed_api_round_end(messages)
        if completed_end <= 0:
            return []
        return list(messages[:completed_end])

    @classmethod
    def _truncate_context_window_to_completed_api_round(cls, context_window: ContextWindow) -> ContextWindow:
        completed_messages = cls._truncate_messages_to_completed_api_round(list(context_window.context_messages or []))
        return ContextWindow(
            system_messages=list(context_window.system_messages or []),
            context_messages=completed_messages,
            tools=list(context_window.tools or []),
        )

    @staticmethod
    def _select_unsummarized_messages(
        messages: List[BaseMessage],
        notes_upto_message_id: str | None,
    ) -> List[BaseMessage]:
        message_index = find_message_index_by_context_message_id(messages, notes_upto_message_id)
        if message_index >= 0:
            logger.info(
                "[SessionMemory] select_unsummarized using context id notes_upto=%s index=%s",
                notes_upto_message_id,
                message_index,
            )
            return list(messages[message_index + 1:])
        logger.info(
            "[SessionMemory] select_unsummarized using context id notes_upto=%s index=%s",
            notes_upto_message_id,
            message_index,
        )
        return list(messages)

    @staticmethod
    def _get_runtime_state(session) -> Dict[str, Any]:
        state = get_session_memory_runtime(session)
        runtime = _build_session_memory_runtime(
            memory_path=state.get("memory_path", ""),
            initialized=bool(state.get("initialized", False)),
            tokens_at_last_update=int(state.get("tokens_at_last_update", 0) or 0),
            tool_calls_at_last_update=int(state.get("tool_calls_at_last_update", 0) or 0),
            last_summarized_message_count=int(state.get("last_summarized_message_count", 0) or 0),
            notes_upto_message_id=state.get("notes_upto_message_id"),
            is_extracting=bool(state.get("is_extracting", False)),
        )
        return runtime

    @staticmethod
    def _set_runtime_state(session, state: Dict[str, Any]) -> None:
        update_session_memory_runtime(session, state)

    def _on_task_done(self, session_id: str, task: asyncio.Task) -> None:
        self._tasks.pop(session_id, None)
        self._task_owners.pop(session_id, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("[SessionMemoryManager] Session memory background task failed: %s", exc)


__all__ = [
    "DEFAULT_SESSION_MEMORY_PROMPT",
    "DEFAULT_SESSION_MEMORY_TEMPLATE",
    "SessionMemoryConfig",
    "SessionMemoryManager",
    "SessionMemoryUpdateAgent",
    "build_session_memory_prompt",
    "find_message_index_by_context_message_id",
    "get_context_message_id",
    "get_session_memory_runtime",
    "invalidate_session_memory_anchor",
    "update_session_memory_runtime",
]
