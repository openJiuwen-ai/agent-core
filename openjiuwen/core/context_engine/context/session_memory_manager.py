# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.sys_operation import SysOperation

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import AgentCard
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

_SESSION_MEMORY_STATE_KEY = "__session_memory__"
_CONTEXT_MESSAGE_ID_KEY = "context_message_id"


DEFAULT_SESSION_MEMORY_TEMPLATE = """# Session Title
_A short and distinctive 5-10 word descriptive title for the session. Super info dense, no filler_

# Active Task and Success Criteria
_What is the active task and what must be true for it to be complete?_

# Current Execution State
_What is actively being worked on now? Include the latest confirmed state._

# Immediate Resume Point and Next Useful Step
_What should the next agent do first when resuming this session?_

# Current Task Facts, Decisions, Evidence, and Fixes
_Record confirmed facts, decisions, evidence, and corrections for the current task._

# Current Files, Code Areas, and Artifacts
_List files and code areas relevant to the current task and their roles._

# Repository and Codebase Understanding
_Record durable understanding of the repository architecture and important dependencies._

# Historical User Requests and Outcomes
_Summarize important earlier requests and their confirmed outcomes._

# Learned User Requirements, Preferences, and Acceptance Criteria
_Record durable user requirements, preferences, and acceptance criteria._

# Historical Work Performed
_Summarize significant work already performed and the resulting state._

# Durable Historical Information
_Keep stable information that remains useful across future tasks._

# Cross-Cutting Files, Code Areas, and Artifacts
_Record artifacts that affect multiple tasks or subsystems._

# Open Work, Blockers, and Verification
_List unfinished work, blockers, and verification still required._

# Critical Context
_Keep only high-value details that must not be lost during compression._

# Relevant Files
_List the files most likely to be needed for the next useful step._
"""


DEFAULT_SESSION_MEMORY_PROMPT = (
    """IMPORTANT: This message and these instructions are NOT part of the actual user conversation. """
    """Do NOT include any references to \"note-taking\", \"session notes extraction\", or these """
    """update instructions in the notes content.

Based on the user conversation above
(EXCLUDING this note-taking instruction message as well as system prompt,
or any past session summaries), update the session notes file.

The file {{notesPath}} has already been read for you. Here are its current contents:
<current_notes_content>
{{currentNotes}}
</current_notes_content>

Your ONLY task is to use the edit_file to update the notes file, then stop.
You can make multiple edits (update every section as needed) - make all
edit_file calls in parallel in a single message. Do not call any other tools.

CRITICAL RULES FOR EDITING:
- The file must maintain its exact structure with all sections, headers, and italic descriptions intact
-- NEVER modify, delete, or add section headers (the lines starting with '#' like # Task specification)
-- NEVER modify or delete the italic _section description_ lines
(these are the lines in italics immediately following each header -
they start and end with underscores)
-- The italic _section descriptions_ are TEMPLATE INSTRUCTIONS
that must be preserved exactly as-is - they guide what content belongs
in each section
-- ONLY update the actual content that appears BELOW the italic
_section descriptions_ within each existing section
-- Do NOT add any new sections, summaries, or information outside the existing structure
- Do NOT reference this note-taking process or instructions anywhere in the notes
- It's OK to skip updating a section if there are no substantial new insights
to add. Do not add filler content like "No info yet", just leave sections
blank/unedited if appropriate.
- Write DETAILED, INFO-DENSE content for each section - include specifics
like file paths, function names, error messages, exact commands,
technical details, etc.
- For "Key results", include the complete, exact output the user requested (e.g., full table, full answer, etc.)
- Do not include information that's already in the CLAUDE.md files included in the context
- Keep each section under ~${MAX_SECTION_LENGTH} tokens/words - if a
section is approaching this limit, condense it by cycling out less
important details while preserving the most critical information
- Focus on actionable, specific information that would help someone
understand or recreate the work discussed in the conversation
- IMPORTANT: Always update "Current State" to reflect the most recent work -
this is critical for continuity after compaction

Use the edit_file with file_path: {{notesPath}}

STRUCTURE PRESERVATION REMINDER:
Each section has TWO parts that must be preserved exactly as they appear
in the current file:
1. The section header (line starting with #)
2. The italic description line
(the _italicized text_ immediately after the header -
this is a template instruction)

You ONLY update the actual content that comes AFTER these two preserved lines.
The italic description lines starting and ending with underscores are part of
the template structure, NOT content to be edited or removed.

REMEMBER: Use the edit_file in parallel and stop. Do not continue after
the edits. Only include insights from the actual user conversation,
never from these note-taking instructions. Do not delete or change
section headers or italic _section descriptions_.`

"""
)


DIRECT_SESSION_MEMORY_PROMPT = (
    """IMPORTANT: This message and these instructions are NOT part of the actual user conversation. """
    """Do NOT include any references to \"note-taking\", \"session notes extraction\", or these """
    """update instructions in the notes content.

Based on the user conversation above
(EXCLUDING this note-taking instruction message as well as system prompt,
or any past session summaries), update the session notes file.

The file {{notesPath}} has already been read for you. Here are its current contents:
<current_notes_content>
{{currentNotes}}
</current_notes_content>

Your ONLY task is to return the COMPLETE updated notes file content, then stop. Do not call any tools.

CRITICAL RULES FOR EDITING:
- The file must maintain its exact structure with all sections, headers, and italic descriptions intact
-- NEVER modify, delete, or add section headers (the lines starting with '#' like # Task specification)
-- NEVER modify or delete the italic _section description_ lines
(these are the lines in italics immediately following each header -
they start and end with underscores)
-- The italic _section descriptions_ are TEMPLATE INSTRUCTIONS
that must be preserved exactly as-is - they guide what content belongs
in each section
-- ONLY update the actual content that appears BELOW the italic
_section descriptions_ within each existing section
-- Do NOT add any new sections, summaries, or information outside the existing structure
- Do NOT reference this note-taking process or instructions anywhere in the notes
- It's OK to skip updating a section if there are no substantial new insights
to add. Do not add filler content like "No info yet", just leave sections
blank/unedited if appropriate.
- Write DETAILED, INFO-DENSE content for each section - include specifics
like file paths, function names, error messages, exact commands,
technical details, etc.
- For "Key results", include the complete, exact output the user requested (e.g., full table, full answer, etc.)
- Do not include information that's already in the CLAUDE.md files included in the context
- Keep each section under ~${MAX_SECTION_LENGTH} tokens/words - if a
section is approaching this limit, condense it by cycling out less
important details while preserving the most critical information
- Focus on actionable, specific information that would help someone
understand or recreate the work discussed in the conversation
- IMPORTANT: Always update "Current State" to reflect the most recent work -
this is critical for continuity after compaction
- Output plain markdown only
- Do NOT wrap the result in code fences

STRUCTURE PRESERVATION REMINDER:
Each section has TWO parts that must be preserved exactly as they appear
in the current file:
1. The section header (line starting with #)
2. The italic description line
(the _italicized text_ immediately after the header -
this is a template instruction)

You ONLY update the actual content that comes AFTER these two preserved lines.
The italic description lines starting and ending with underscores are part of
the template structure, NOT content to be edited or removed.

"""
)


class SessionMemoryConfig(BaseModel):
    update_trigger_context_ratio: float = Field(default=0.7, gt=0.0, lt=1.0)
    model: ModelRequestConfig | None = None
    model_client: ModelClientConfig | None = None
    update_mode: Literal["agent_edit", "direct_replace"] = Field(default="agent_edit")
    direct_replace_max_retries: int = Field(default=2, ge=0)
    enable_debug_dump: bool = Field(default=False)
    debug_dump_dir: str | None = Field(default=None)


class SessionMemoryUpdateAgent:
    """Dedicated session-memory updater backed by a real ReActAgent."""

    def __init__(self, config: SessionMemoryConfig):
        self._config = config
        self._agent: Any = None
        self._agent_card: AgentCard | None = None
        self._sys_operation: SysOperation | None = None
        self._direct_model: Model | None = None
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
        system_messages: List[BaseMessage] | None = None,
        tools: List[Any] | None = None,
    ) -> None:
        if self._config.update_mode == "direct_replace":
            await self._invoke_direct_replace(
                full_context_messages=full_context_messages,
                notes_path=notes_path,
                current_notes=current_notes,
            )
            return

        self._ensure_agent(notes_path)
        if self._agent is None:
            raise RuntimeError("Session memory update agent is not initialized")
        self._prime_notes_file_as_read(notes_path, current_notes)
        query = build_session_memory_prompt(str(notes_path), current_notes)
        self._agent.configure_request(tools=tools, allowed_notes_path=notes_path)
        from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
            CompressionRequest,
        )

        request = CompressionRequest(
            prompt=query,
            context_messages=list(full_context_messages),
            system_messages=([] if self._inherited_system_prompt else list(system_messages or [])),
            tools=list(tools or []),
        )
        await self._agent.invoke(request)

    async def _invoke_direct_replace(
        self,
        *,
        full_context_messages: List[BaseMessage],
        notes_path: Path,
        current_notes: str,
    ) -> None:
        model = self._ensure_direct_model()
        prompt_messages: List[BaseMessage] = []
        inherited_system_prompt = (self._inherited_system_prompt or "").strip()
        if inherited_system_prompt:
            prompt_messages.append(SystemMessage(content=inherited_system_prompt))
        prompt_messages.extend(full_context_messages)
        prompt_messages.append(UserMessage(content=build_direct_session_memory_prompt(str(notes_path), current_notes)))
        response = await self._invoke_direct_model_with_retry(model=model, prompt_messages=prompt_messages)
        content = self._normalize_direct_response_content(response.content)
        if not content:
            raise RuntimeError("Session memory direct replace returned empty content")
        notes_path.write_text(content, encoding="utf-8")

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
        from openjiuwen.core.context_engine.processor.forked.compressor.session_memory_agent import (
            SessionMemoryAgent,
            SessionMemoryAgentConfig,
        )

        workspace_root = notes_path.parent.parent
        prompt_template = self._build_prompt_template(self._inherited_system_prompt)
        if self._config.model is None or self._config.model_client is None:
            raise RuntimeError("Session memory agent requires model and model_client config")
        self._agent = SessionMemoryAgent(
            Model(self._config.model_client, self._config.model),
            model_config=self._config.model,
            model_client_config=self._config.model_client,
            prompt_template=prompt_template,
            workspace_root=workspace_root,
            config=SessionMemoryAgentConfig(
                max_iterations=3,
                enable_debug_dump=self._config.enable_debug_dump,
                debug_dump_dir=self._config.debug_dump_dir,
            ),
        )
        self._workspace_root = str(workspace_root)

    def _ensure_direct_model(self) -> Model:
        if self._direct_model is not None:
            return self._direct_model
        if self._config.model is None or self._config.model_client is None:
            raise RuntimeError("Session memory direct replace requires model and model_client config")
        self._direct_model = Model(self._config.model_client, self._config.model)
        return self._direct_model

    async def _invoke_direct_model_with_retry(
        self,
        *,
        model: Model,
        prompt_messages: List[BaseMessage],
    ):
        attempts = self._config.direct_replace_max_retries + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await model.invoke(messages=prompt_messages, tools=None)
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                logger.warning(
                    "[SessionMemory] direct_replace model invoke failed attempt=%s/%s, retrying: %s",
                    attempt,
                    attempts,
                    exc,
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("Session memory direct replace failed without an exception")

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
        from openjiuwen.harness.tools.filesystem import EditFileTool

        tools = [EditFileTool(operation)]
        for tool in tools:
            tool.card.id = f"{self._tool_namespace}.{tool.card.name}"
            if tool.card.name == "edit_file":
                tool.card.description = (
                    f"{tool.card.description}\n"
                    "Session-memory updater note: the target notes file content is already provided in the prompt. "
                    "You do not need to call read_file before editing that file. "
                    "Apply the required edits directly and finish in as a few edit_file calls as possible."
                )
        return tools

    @staticmethod
    def _prime_notes_file_as_read(notes_path: Path, current_notes: str) -> None:
        from openjiuwen.harness.tools.filesystem import _FILE_READ_REGISTRY, _FileReadState

        try:
            stat = notes_path.stat()
        except FileNotFoundError:
            return

        _FILE_READ_REGISTRY[str(notes_path)] = _FileReadState(
            mtime_ns=stat.st_mtime_ns,
            size_bytes=stat.st_size,
            is_partial=False,
            content=current_notes,
        )

    @staticmethod
    def _normalize_direct_response_content(content: Any) -> str:
        text = content if isinstance(content, str) else str(content or "")
        normalized = text.strip()
        if normalized.startswith("```"):
            lines = normalized.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                normalized = "\n".join(lines[1:-1]).strip()
        return normalized


def _build_session_memory_runtime(
    *,
    memory_path: str = "",
    pending_memory_path: str = "",
    initialized: bool = False,
    tokens_at_last_update: int = 0,
    tool_calls_at_last_update: int = 0,
    last_summarized_message_count: int = 0,
    notes_upto_message_id: str | None = None,
    is_extracting: bool = False,
) -> Dict[str, Any]:
    return {
        "memory_path": memory_path,
        "pending_memory_path": pending_memory_path,
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
    session_id = ""
    if hasattr(session, "get_session_id"):
        try:
            session_id = session.get_session_id()
        except Exception:
            session_id = ""
    logger.info(
        "[SessionMemory] update_runtime session_obj=%s session_id=%s merged=%s",
        hex(id(session)),
        session_id,
        {
            "memory_path": merged.get("memory_path"),
            "pending_memory_path": merged.get("pending_memory_path"),
            "initialized": merged.get("initialized"),
            "is_extracting": merged.get("is_extracting"),
            "tokens_at_last_update": merged.get("tokens_at_last_update"),
            "tool_calls_at_last_update": merged.get("tool_calls_at_last_update"),
            "last_summarized_message_count": merged.get("last_summarized_message_count"),
            "notes_upto_message_id": merged.get("notes_upto_message_id"),
        },
    )
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


def build_direct_session_memory_prompt(notes_path: str, current_notes: str) -> str:
    return DIRECT_SESSION_MEMORY_PROMPT.replace("{{notesPath}}", notes_path).replace("{{currentNotes}}", current_notes)


def build_system_prompt_text(messages: List[BaseMessage]) -> str:
    if not messages:
        return ""
    first_message = messages[0]
    if not isinstance(first_message, SystemMessage):
        return ""
    return first_message.content


def _load_context_debug_writer():
    """Lazy import the forked context-debug writer to avoid circular imports.

    ``session_memory_manager`` is imported early by ``processor.base`` and the
    forked subtree, so a top-level import of ``processor.forked.support.*
    here would cycle. Resolve it at call time instead.
    """
    try:
        from openjiuwen.core.context_engine.processor.forked.support.context_debug import (
            write_debug_record,
        )
    except Exception:  # pragma: no cover - defensive
        return None
    return write_debug_record


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
            logger.info(
                "[SessionMemory] skip schedule: task already running session_obj=%s session_id=%s",
                hex(id(ctx.session)),
                session_id,
            )
            return

        context_window = self.collect_context_window(ctx)
        completed_context_window = self._truncate_context_window_to_completed_api_round(context_window)
        notes_path = self._get_session_memory_path(workspace, session_id)
        pending_notes_path = self._get_pending_session_memory_path(notes_path)
        runtime_update = {
            "session_id": session_id,
            "memory_path": str(notes_path),
            "pending_memory_path": str(pending_notes_path),
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
        logger.info(
            "[SessionMemory] schedule update session_obj=%s session_id=%s notes_path=%s messages=%s",
            hex(id(ctx.session)),
            session_id,
            notes_path,
            len(completed_context_window.context_messages),
        )

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
        input_messages = list(getattr(ctx.inputs, "messages", None) or [])
        system_messages = [message for message in input_messages if isinstance(message, SystemMessage)]
        return ContextWindow(
            system_messages=system_messages,
            context_messages=list(ctx.context.get_messages()),
            tools=list(getattr(ctx.inputs, "tools", None) or []),
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

        current_tokens = self._count_tokens(context, context_window)
        context_max = self._resolve_context_max(context)
        threshold = max(int(context_max * self.config.update_trigger_context_ratio), 1)
        if current_tokens < threshold:
            logger.info(
                "[SessionMemory] should_update skipped: tokens=%s threshold=%s ratio=%.2f context_max=%s",
                current_tokens,
                threshold,
                self.config.update_trigger_context_ratio,
                context_max,
            )
            write_debug_record = _load_context_debug_writer()
            if write_debug_record is not None:
                write_debug_record(
                    context,
                    processor_type="SessionMemory",
                    event="threshold_check",
                    enabled=bool(getattr(self.config, "enable_debug_dump", False)),
                    dump_dir=getattr(self.config, "debug_dump_dir", None),
                    phase="should_update",
                    total_tokens=current_tokens,
                    context_max=context_max,
                    threshold=threshold,
                    hit=False,
                    reason="below_threshold",
                )
            return False

        runtime = self._get_runtime_state(session)
        unsummarized_messages = self._select_unsummarized_messages(
            messages,
            runtime.get("notes_upto_message_id"),
        )
        if not unsummarized_messages:
            logger.info(
                "[SessionMemory] should_update skipped: no unsummarized messages session_id=%s",
                session.get_session_id() if hasattr(session, "get_session_id") else "",
            )
            return False

        if not self._truncate_messages_to_completed_api_round(unsummarized_messages):
            logger.info(
                "[SessionMemory] should_update skipped: no new completed API round session_id=%s",
                session.get_session_id() if hasattr(session, "get_session_id") else "",
            )
            return False
        logger.info(
            "[SessionMemory] should_update triggered: tokens=%s threshold=%s ratio=%.2f context_max=%s",
            current_tokens,
            threshold,
            self.config.update_trigger_context_ratio,
            context_max,
        )
        write_debug_record = _load_context_debug_writer()
        if write_debug_record is not None:
            write_debug_record(
                context,
                processor_type="SessionMemory",
                event="threshold_check",
                enabled=bool(getattr(self.config, "enable_debug_dump", False)),
                dump_dir=getattr(self.config, "debug_dump_dir", None),
                phase="should_update",
                total_tokens=current_tokens,
                context_max=context_max,
                threshold=threshold,
                hit=True,
                reason="reached_threshold",
            )
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
        pending_notes_path = self._get_pending_session_memory_path(notes_path)
        current_notes = self._read_or_init_session_memory(notes_path)
        self._prepare_pending_session_memory(notes_path, pending_notes_path, current_notes)
        logger.info(
            "[SessionMemory] update_background start session_obj=%s session_id=%s "
            "mode=%s notes_path=%s pending_notes_path=%s messages=%s",
            hex(id(session)),
            session_id,
            self.config.update_mode,
            notes_path,
            pending_notes_path,
            len(messages),
        )
        try:
            update_kwargs = {
                "full_context_messages": list(context_window.context_messages or []),
                "notes_path": pending_notes_path,
                "current_notes": current_notes,
            }
            if context_window.system_messages:
                update_kwargs["system_messages"] = list(context_window.system_messages)
            if context_window.tools:
                update_kwargs["tools"] = list(context_window.tools)
            await self._update_agent.invoke(**update_kwargs)
            self._commit_pending_session_memory(pending_notes_path, notes_path)
            if ctx.context is not None:
                runtime["tokens_at_last_update"] = self._count_tokens(ctx.context, context_window)
            runtime["tool_calls_at_last_update"] = self._count_tool_calls(messages)
            runtime["last_summarized_message_count"] = len(messages)
            runtime["notes_upto_message_id"] = get_context_message_id(messages[-1]) if messages else None
            runtime["initialized"] = True
            logger.info(
                "[SessionMemory] update complete notes_upto=%s count=%s tokens=%s tool_calls=%s",
                runtime["notes_upto_message_id"],
                runtime["last_summarized_message_count"],
                runtime["tokens_at_last_update"],
                runtime["tool_calls_at_last_update"],
            )
        except Exception:
            logger.warning(
                "[SessionMemory] update failed session_id=%s notes_path=%s pending_notes_path=%s",
                session_id,
                notes_path,
                pending_notes_path,
                exc_info=True,
            )
            raise
        finally:
            runtime["is_extracting"] = False
            logger.info(
                "[SessionMemory] update_background finalize session_obj=%s session_id=%s runtime=%s",
                hex(id(session)),
                session_id,
                {
                    "memory_path": runtime.get("memory_path"),
                    "pending_memory_path": runtime.get("pending_memory_path"),
                    "initialized": runtime.get("initialized"),
                    "is_extracting": runtime.get("is_extracting"),
                    "tokens_at_last_update": runtime.get("tokens_at_last_update"),
                    "tool_calls_at_last_update": runtime.get("tool_calls_at_last_update"),
                    "last_summarized_message_count": runtime.get("last_summarized_message_count"),
                    "notes_upto_message_id": runtime.get("notes_upto_message_id"),
                },
            )
            self._set_runtime_state(session, runtime)

    @staticmethod
    def _get_session_memory_path(workspace, session_id: str) -> Path:
        return Path(workspace.root_path) / "context" / f"{session_id}_context" / "session_memory" / "session_context.md"

    @staticmethod
    def _get_pending_session_memory_path(path: Path) -> Path:
        return path.with_name(f"{path.stem}.pending{path.suffix}")

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
    def _prepare_pending_session_memory(active_path: Path, pending_path: Path, current_notes: str) -> None:
        if not pending_path.parent.exists():
            pending_path.parent.mkdir(parents=True, exist_ok=True)
        if active_path.exists():
            shutil.copy2(active_path, pending_path)
            return
        pending_path.write_text(current_notes, encoding="utf-8")

    @staticmethod
    def _commit_pending_session_memory(pending_path: Path, active_path: Path) -> None:
        if not pending_path.exists():
            raise RuntimeError(f"Pending session memory does not exist: {pending_path}")
        pending_path.replace(active_path)

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
        all_messages = list(context_window.system_messages or []) + list(context_window.context_messages or [])
        # 优先用末尾 AssistantMessage.usage_metadata.total_tokens 作为整窗口
        # 基准（已含当时 system+tools），尾部新增消息用 len//3 补算；无
        # usage 时 fallback 到 token_counter / char 估算。
        for idx in range(len(all_messages) - 1, -1, -1):
            message = all_messages[idx]
            if ContextUtils.has_valid_usage_metadata(message):
                tail = all_messages[slice(idx + 1, None)]
                return message.usage_metadata.total_tokens + sum(
                    SessionMemoryManager._estimate_message_tokens(msg) for msg in tail
                )
        token_counter = context.token_counter()
        if token_counter is not None:
            try:
                return token_counter.count_messages(all_messages)
            except Exception:
                logger.debug("Failed to count session memory tokens with token counter", exc_info=True)
        return sum(SessionMemoryManager._estimate_message_tokens(message) for message in all_messages)

    @staticmethod
    def _resolve_context_max(context: ModelContext) -> int:
        return ContextUtils.resolve_context_max(
            model_name=getattr(context, "_model_name", None),
            fallback_context_window_tokens=getattr(context, "_context_window_tokens", None),
            model_context_window_tokens=getattr(context, "_model_context_window_tokens", None),
        )

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
            return list(messages[message_index + 1:])  # fmt: skip
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
            pending_memory_path=state.get("pending_memory_path", ""),
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
