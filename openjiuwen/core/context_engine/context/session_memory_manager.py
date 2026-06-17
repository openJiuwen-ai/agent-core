# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal

from pydantic import BaseModel, Field

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
from openjiuwen.core.common.logging import logger
from openjiuwen.core.sys_operation import SysOperation

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import AgentCard, ReActAgent
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

_SESSION_MEMORY_STATE_KEY = "__session_memory__"
_CONTEXT_MESSAGE_ID_KEY = "context_message_id"


async def _sm_skip_ack_after_tool(ctx: "AgentCallbackContext") -> None:
    """SM-edit 批量返回后直接 force_finish，跳过 LLM 多吐的 "All sections updated" ack 轮。"""
    ctx.request_force_finish({"output": "session memory updated", "result_type": "answer"})


DEFAULT_SESSION_MEMORY_TEMPLATE = """# Session Title
_A short and distinctive 5-10 word descriptive title for the session. Super info dense, no filler._

# Current State
_What is actively being worked on right now? Pending tasks not yet completed. Immediate next steps._

# Task Brief
_User request; deliverable type (Word/PPT/Excel/PDF/email/meeting notes); format, style, length, audience, deadline; confirmed plan._

# Files and Sources
_Input files, references, attachments; generated output paths and versions; purpose and status of each._

# Processing Progress
_Skills/tools used and stage progress; outline/draft/data status; items awaiting user confirmation._

# Issues and Corrections
_Errors, retries, user corrections; approaches proven ineffective._

# Key Deliverables
_Final or interim outputs: file paths, summaries, tables, conclusions, email drafts; preserve user-confirmed content in full._

# Work Log
_Chronological terse log of key actions for quick resumption._
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

The notes above already include the full standard section template (headers and _description_
lines). Use those sections as your editing guide. The pending file {{notesPath}} must NOT
contain any _description_ lines — delete them from every section you keep.

Your ONLY task is to use the edit_file to update the notes file, then stop.
You can make multiple edits (update every section as needed) - make all
edit_file calls in parallel in a single message. Do not call any other tools.

CRITICAL RULES FOR EDITING:
- The italic _description_ lines in <current_notes_content> are editing reference only;
  do NOT copy or keep them in {{notesPath}}
- NEVER modify, delete, or add section headers (the lines starting with '#' like # Task specification)
- Do NOT add any new sections, summaries, or information outside the sections present
in <current_notes_content>
- Use each _description_ line to decide what body content belongs in that section, then
write or update body text directly below the "# Header" line
- The saved file must contain ONLY sections with substantive body content
- Each included section has exactly two parts: one "# Header" line, then body text
- Do NOT write italic _description_ lines in the saved file
- Omit empty sections entirely — do not leave placeholder headers or filler like "No info yet"
- Output section headers must come from <current_notes_content> only
- Keep sections in the same order as they appear in <current_notes_content>
- You MUST delete every italic _description_ line from {{notesPath}} for each section
with body content — including sections whose body text you leave unchanged
- Preserve existing valid body content; append or update rather than discard
- If a section already has body content and there are no substantial new insights,
keep the existing body text but still remove its _description_ line from {{notesPath}}
- Do NOT reference this note-taking process or instructions anywhere in the notes
- It's OK to skip updating a section with no body and no substantial new insights to add
- Write DETAILED, INFO-DENSE content for each included section - include specifics
like file paths, document names, skill stages, tool outputs, commands, 
error messages, and deliverable summaries as relevant, etc.
- For "Key Deliverables", include the complete output the user requested (e.g., full table, 
outline, file path, email draft, etc.)
- Do not include information that's already in the session_context.md files included in the context
- Keep each section under ~${MAX_SECTION_LENGTH} tokens/words - if a
section is approaching this limit, condense it by cycling out less
important details while preserving the most critical information
- Focus on actionable, specific information that would help someone
understand or recreate the work discussed in the conversation
- IMPORTANT: Always update "Current State" to reflect the most recent work -
this is critical for continuity after compaction

Use the edit_file with file_path: {{notesPath}}

STRUCTURE PRESERVATION REMINDER:
<current_notes_content> shows headers and _description_ lines as template guidance only.
Read them to decide what belongs in each section. The pending file ({{notesPath}}) must
contain only "# Header" + body — delete every _description_ line from the pending file.

REMEMBER: Use the edit_file in parallel and stop. Do not continue after
the edits. Only include insights from the actual user conversation,
never from these note-taking instructions. The pending file must use
header + body only, with no empty sections and no _description_ lines.

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

The notes above already include the full standard section template (headers and _description_
lines). Use those sections as your editing guide. Your returned content must NOT contain
any _description_ lines — omit them from every section you include.

Your ONLY task is to return the COMPLETE updated notes file content, then stop. Do not call any tools.

CRITICAL RULES FOR EDITING:
- The italic _description_ lines in <current_notes_content> are editing reference only;
  do NOT include them in your returned content
- NEVER modify, delete, or add section headers (the lines starting with '#' like # Task specification)
- Do NOT add any new sections, summaries, or information outside the sections present
in <current_notes_content>
- Use each _description_ line to decide what body content belongs in that section, then
output body text directly below the "# Header" line
- The saved file must contain ONLY sections with substantive body content
- Each included section has exactly two parts: one "# Header" line, then body text
- Do NOT write italic _description_ lines in the saved file
- Omit empty sections entirely — do not leave placeholder headers or filler like "No info yet"
- Output section headers must come from <current_notes_content> only
- Keep sections in the same order as they appear in <current_notes_content>
- Remove every italic _description_ line from the result — for all sections with body content
- Preserve existing valid body content; append or update rather than discard
- If a section already has body content and there are no substantial new insights,
keep the existing body text but do not include any _description_ line for that section
- Do NOT reference this note-taking process or instructions anywhere in the notes
- It's OK to skip updating a section with no body and no substantial new insights to add
- Write DETAILED, INFO-DENSE content for each included section - include specifics
like file paths, document names, skill stages, tool outputs, commands, 
error messages, and deliverable summaries as relevant, etc.
- For "Key Deliverables", include the complete output the user requested (e.g., full table, 
outline, file path, email draft, etc.)
- Do not include information that's already in the session_context.md files included in the context
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
<current_notes_content> shows headers and _description_ lines as template guidance only.
Read them to decide what belongs in each section. Your returned content must contain
only "# Header" + body — never include _description_ lines.

REMEMBER: Only include insights from the actual user conversation,
never from these note-taking instructions. Return the full file in
header + body format only, with no empty sections and no _description_ lines.

"""
)


class SessionMemoryConfig(BaseModel):
    trigger_tokens: int = Field(default=10000, gt=0)
    trigger_add_tokens: int = Field(default=5000, gt=0)
    tool_min_: int = Field(default=3, gt=0)
    model: ModelRequestConfig | None = None
    model_client: ModelClientConfig | None = None
    update_mode: Literal["agent_edit", "direct_replace"] = Field(default="agent_edit")
    direct_replace_max_retries: int = Field(default=2, ge=0)
    incremental_mode: bool = Field(default=False)


_MEMORY_UPDATE_SYSTEM_PROMPT = (
    "You are a session memory updater. "
    "Your only task is to update a markdown notes file based on conversation context. "
    "Use only the edit_file tool. Do not execute shell commands, "
    "do not interact with users, do not use any other tools."
)


class SessionMemoryUpdateAgent:
    """Dedicated session-memory updater backed by a real ReActAgent."""

    def __init__(self, config: SessionMemoryConfig):
        self._config = config
        self._agent: ReActAgent | None = None
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
        context_messages: List[BaseMessage],
        notes_path: Path,
        current_notes: str,
        is_incremental: bool = False,
        trigger_tokens: int = 0,
        full_scan_tokens: int = 0,
    ) -> None:
        if self._config.update_mode == "direct_replace":
            await self._invoke_direct_replace(
                context_messages=context_messages,
                notes_path=notes_path,
                current_notes=current_notes,
                is_incremental=is_incremental,
                trigger_tokens=trigger_tokens,
                full_scan_tokens=full_scan_tokens,
            )
            return

        await self._ensure_agent(notes_path)
        if self._agent is None:
            raise RuntimeError("Session memory update agent is not initialized")
        self._prime_notes_file_as_read(notes_path, current_notes)
        if is_incremental:
            query = build_incremental_session_memory_prompt(str(notes_path), current_notes)
        else:
            query = build_session_memory_prompt(str(notes_path), current_notes)
        session = self._create_agent_session()
        inputs = {
            "query": query,
            "conversation_id": self._tool_namespace,
        }
        await session.pre_run(inputs=inputs)
        try:
            await self._prime_inherited_context(session, context_messages)
            system_tokens = ContextUtils.estimate_tokens(_MEMORY_UPDATE_SYSTEM_PROMPT)
            context_tokens = sum(ContextUtils.estimate_message_tokens(m) for m in context_messages)
            query_tokens = ContextUtils.estimate_tokens(inputs.get("query", ""))
            llm_input_tokens = system_tokens + context_tokens + query_tokens
            saved_tokens = max(full_scan_tokens - llm_input_tokens, 0) if is_incremental else 0
            logger.debug(
                "[SessionMemory] agent_invoke %s",
                json.dumps(
                    {
                        "agent": "session_memory_update",
                        "is_incremental": is_incremental,
                        "message_count": len(context_messages),
                        "trigger_tokens": trigger_tokens,
                        "llm_input_tokens": llm_input_tokens,
                        "full_scan_tokens": full_scan_tokens,
                        "saved_tokens": saved_tokens,
                        "conversation_id": inputs.get("conversation_id", ""),
                        "session_id": session.get_session_id() if hasattr(session, "get_session_id") else "",
                        "inputs": inputs,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
            response = await self._agent.invoke(inputs, session=session)
        finally:
            await session.post_run()
        _ = response

    async def _invoke_direct_replace(
        self,
        *,
        context_messages: List[BaseMessage],
        notes_path: Path,
        current_notes: str,
        is_incremental: bool = False,
        trigger_tokens: int = 0,
        full_scan_tokens: int = 0,
    ) -> None:
        model = self._ensure_direct_model()
        prompt_messages: List[BaseMessage] = [
            SystemMessage(content=_MEMORY_UPDATE_SYSTEM_PROMPT),
        ]
        prompt_messages.extend(context_messages)
        if is_incremental:
            prompt_messages.append(
                UserMessage(content=build_incremental_direct_session_memory_prompt(str(notes_path), current_notes))
            )
        else:
            prompt_messages.append(
                UserMessage(content=build_direct_session_memory_prompt(str(notes_path), current_notes))
            )
        response = await self._invoke_direct_model_with_retry(model=model, prompt_messages=prompt_messages)
        llm_input_tokens = sum(ContextUtils.estimate_message_tokens(m) for m in prompt_messages)
        saved_tokens = max(full_scan_tokens - llm_input_tokens, 0) if is_incremental else 0
        logger.debug(
            "[SessionMemory] agent_invoke %s",
            json.dumps(
                {
                    "agent": "session_memory_update_direct",
                    "is_incremental": is_incremental,
                    "message_count": len(context_messages),
                    "trigger_tokens": trigger_tokens,
                    "llm_input_tokens": llm_input_tokens,
                    "full_scan_tokens": full_scan_tokens,
                    "saved_tokens": saved_tokens,
                    "conversation_id": self._tool_namespace,
                    "session_id": "",
                },
                ensure_ascii=False,
                default=str,
            ),
        )
        content = self._normalize_direct_response_content(response.content)
        if not content:
            raise RuntimeError("Session memory direct replace returned empty content")
        notes_path.write_text(content, encoding="utf-8")

    async def _prime_inherited_context(
        self,
        session,
        context_messages: List[BaseMessage],
    ) -> None:
        if self._agent is None:
            return
        if not context_messages:
            return
        init_context = getattr(self._agent, "_init_context", None)
        if init_context is None:
            raise RuntimeError("Session memory update agent does not support context initialization")
        context = await init_context(session)
        if context.get_messages():
            logger.warning("agent context is empty")
            return
        await context.add_messages(context_messages)

    async def _ensure_agent(
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
        prompt_template = self._build_prompt_template(_MEMORY_UPDATE_SYSTEM_PROMPT)
        agent_config = ReActAgentConfig(
            model_name=self._config.model.model_name,
            prompt_template=prompt_template,
            max_iterations=2,
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
        from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
        await agent.agent_callback_manager.register_callback(
            AgentCallbackEvent.AFTER_TOOL_CALL, _sm_skip_ack_after_tool,
        )

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
            config.prompt_template = self._build_prompt_template(_MEMORY_UPDATE_SYSTEM_PROMPT)

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


def _is_session_memory_section_description_line(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) > 2 and stripped.startswith("_") and stripped.endswith("_")


def _parse_session_memory_into_sections(
    content: str,
    *,
    include_descriptions: bool = True,
) -> List[Dict[str, Any]]:
    """Parse session memory markdown into section dicts with header, optional description, and body_lines."""
    lines = content.splitlines()
    sections: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None

    for line in lines:
        if line.startswith("# "):
            if current is not None:
                sections.append(current)
            if include_descriptions:
                current = {"header": line, "description": None, "body_lines": []}
            else:
                current = {"header": line, "body_lines": []}
            continue
        if current is None:
            continue
        if include_descriptions:
            if current["description"] is None and _is_session_memory_section_description_line(line):
                current["description"] = line
            else:
                current["body_lines"].append(line)
        elif _is_session_memory_section_description_line(line):
            continue
        else:
            current["body_lines"].append(line)

    if current is not None:
        sections.append(current)

    return sections


def _clean_session_memory_sections(content: str) -> str:
    """Drop empty sections and strip italic description lines; output header + body only."""
    lines = content.splitlines()
    sections: List[Dict[str, Any]] = []
    preamble: List[str] = []
    current: Dict[str, Any] | None = None

    for line in lines:
        if line.startswith("# "):
            if current is not None:
                sections.append(current)
            current = {"header": line, "description": None, "body_lines": []}
            continue
        if current is None:
            preamble.append(line)
            continue
        if current["description"] is None and _is_session_memory_section_description_line(line):
            current["description"] = line
        else:
            current["body_lines"].append(line)

    if current is not None:
        sections.append(current)

    kept_sections = [sec for sec in sections if "\n".join(sec["body_lines"]).strip()]

    rendered_sections = []
    for sec in kept_sections:
        body = "\n".join(sec["body_lines"]).strip()
        if body:
            rendered_sections.append(f"{sec['header']}\n{body}")

    result_parts: List[str] = []
    preamble_text = "\n".join(preamble).strip()
    if preamble_text:
        result_parts.append(preamble_text)
    if rendered_sections:
        result_parts.append("\n\n".join(rendered_sections))

    if not result_parts:
        return ""
    return "\n\n".join(result_parts).strip() + "\n"


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
    return DIRECT_SESSION_MEMORY_PROMPT.replace("{{notesPath}}", notes_path).replace(
        "{{currentNotes}}", current_notes
    )


INCREMENTAL_SESSION_MEMORY_PROMPT = (
    """IMPORTANT: This message and these instructions are NOT part of the actual user conversation. """
    """Do NOT include any references to \"note-taking\", \"session notes extraction\", or these """
    """update instructions in the notes content.

Based on the NEW conversation messages above (since the last notes update)
(EXCLUDING this note-taking instruction message as well as system prompt,
or any past session summaries), update the session notes file.

The file {{notesPath}} has already been read for you. Here are its current contents:
<current_notes_content>
{{currentNotes}}
</current_notes_content>

The notes above already include the full standard section template (headers and _description_
lines). Use those sections as your editing guide. The pending file {{notesPath}} must NOT
contain any _description_ lines — delete them from every section you keep.

Your ONLY task is to use the edit_file to update the notes file, then stop.
You can make multiple edits (update every section as needed) - make all
edit_file calls in parallel in a single message. Do not call any other tools.

INCREMENTAL UPDATE RULES:
- You only see NEW messages since the last update. The existing notes already
  cover earlier conversation.
- Update sections based on the new information only.
- Do NOT remove information from the notes unless the new messages explicitly
  contradict or supersede it.
- If new messages don't add relevant information to a section, leave its body text
  unchanged but still remove any _description_ line from that section in {{notesPath}}

CRITICAL RULES FOR EDITING:
- The italic _description_ lines in <current_notes_content> are editing reference only;
  do NOT copy or keep them in {{notesPath}}
- NEVER modify, delete, or add section headers
- Do NOT add any new sections outside those present in <current_notes_content>
- Use each _description_ line to decide what body content belongs in that section, then
write or update body text directly below the "# Header" line
- The saved file must contain ONLY sections with substantive body content
- Each included section: one "# Header" line, then body text — no _description_ lines
- Omit empty sections; do not use filler like "No info yet"
- Output section headers must come from <current_notes_content> only
- You MUST delete every italic _description_ line from {{notesPath}} for each section
with body content — including sections whose body text you leave unchanged
- Preserve existing valid body content; append or update rather than discard
- Do NOT reference this note-taking process or instructions anywhere in the notes
- Write DETAILED, INFO-DENSE content for each included section
- IMPORTANT: Always update "Current State" to reflect the most recent work -
this is critical for continuity after compaction

Use the edit_file with file_path: {{notesPath}}

STRUCTURE PRESERVATION REMINDER:
<current_notes_content> shows headers and _description_ lines as template guidance only.
Read them to decide what belongs in each section. The pending file ({{notesPath}}) must
contain only "# Header" + body — delete every _description_ line from the pending file.

REMEMBER: Use the edit_file in parallel and stop. Do not continue after
the edits. Only include insights from the actual user conversation,
never from these note-taking instructions. The pending file must use
header + body only, with no empty sections and no _description_ lines.
"""
)

INCREMENTAL_DIRECT_SESSION_MEMORY_PROMPT = (
    """IMPORTANT: This message and these instructions are NOT part of the actual user conversation. """
    """Do NOT include any references to \"note-taking\", \"session notes extraction\", or these """
    """update instructions in the notes content.

Based on the NEW conversation messages above (since the last notes update)
(EXCLUDING this note-taking instruction message as well as system prompt,
or any past session summaries), update the session notes file.

The file {{notesPath}} has already been read for you. Here are its current contents:
<current_notes_content>
{{currentNotes}}
</current_notes_content>

The notes above already include the full standard section template (headers and _description_
lines). Use those sections as your editing guide. Your returned content must NOT contain
any _description_ lines — omit them from every section you include.

Your ONLY task is to return the COMPLETE updated notes file content, then stop. Do not call any tools.

INCREMENTAL UPDATE RULES:
- You only see NEW messages since the last update. The existing notes already
  cover earlier conversation.
- Merge the new information into the existing notes structure.
- Preserve all existing information that is still valid.
- Add new information from the new messages.
- Update sections that are affected by the new messages.
- Remove information only if new messages explicitly contradict it.

CRITICAL RULES FOR EDITING:
- The italic _description_ lines in <current_notes_content> are editing reference only;
  do NOT include them in your returned content
- NEVER modify, delete, or add section headers
- Do NOT add any new sections outside those present in <current_notes_content>
- Use each _description_ line to decide what body content belongs in that section, then
output body text directly below the "# Header" line
- The saved file must contain ONLY sections with substantive body content
- Each included section: one "# Header" line, then body text — no _description_ lines
- Omit empty sections; do not use filler like "No info yet"
- Output section headers must come from <current_notes_content> only
- Remove every italic _description_ line from the result — for all sections with body content
- Preserve existing valid body content; append or update rather than discard
- Do NOT reference this note-taking process or instructions anywhere in the notes
- Write DETAILED, INFO-DENSE content for each included section
- IMPORTANT: Always update "Current State" to reflect the most recent work
- Output plain markdown only
- Do NOT wrap the result in code fences

STRUCTURE PRESERVATION REMINDER:
<current_notes_content> shows headers and _description_ lines as template guidance only.
Read them to decide what belongs in each section. Your returned content must contain
only "# Header" + body — never include _description_ lines.

REMEMBER: Only include insights from the actual user conversation,
never from these note-taking instructions. Return the full file in
header + body format only, with no empty sections and no _description_ lines.
"""
)


def build_incremental_session_memory_prompt(notes_path: str, current_notes: str) -> str:
    return INCREMENTAL_SESSION_MEMORY_PROMPT.replace("{{notesPath}}", notes_path).replace(
        "{{currentNotes}}", current_notes
    )


def build_incremental_direct_session_memory_prompt(notes_path: str, current_notes: str) -> str:
    return INCREMENTAL_DIRECT_SESSION_MEMORY_PROMPT.replace("{{notesPath}}", notes_path).replace(
        "{{currentNotes}}", current_notes
    )


def build_system_prompt_text(messages: List[BaseMessage]) -> str:
    if not messages:
        return ""
    first_message = messages[0]
    if not isinstance(first_message, SystemMessage):
        return ""
    return first_message.content


class SessionMemoryManager:
    # Workspace template: {workspace}/context/session_memory.md
    # Resolved from session_context.md at .../context/{session_id}_context/session_memory/
    _WORKSPACE_TEMPLATE_FILENAME = "session_memory.md"
    _WORKSPACE_CONTEXT_ANCESTOR_DEPTH = 2

    @staticmethod
    def _get_workspace_template_path(session_memory_path: Path) -> Path:
        return (
            session_memory_path.parents[SessionMemoryManager._WORKSPACE_CONTEXT_ANCESTOR_DEPTH]
            / SessionMemoryManager._WORKSPACE_TEMPLATE_FILENAME
        )

    def __init__(self, config: SessionMemoryConfig):
        self.config = config
        self._tasks: dict[str, asyncio.Task] = {}
        self._task_owners: dict[str, tuple[Any, ModelContext | None]] = {}
        self._update_agent = SessionMemoryUpdateAgent(config)
        logger.debug(
            "[SessionMemory] initialized mode=%s incremental=%s update_mode=%s "
            "trigger_tokens=%s trigger_add_tokens=%s tool_min=%s",
            "incremental" if config.incremental_mode else "full",
            config.incremental_mode,
            config.update_mode,
            config.trigger_tokens,
            config.trigger_add_tokens,
            config.tool_min_,
        )

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

    async def force_schedule_update(
        self,
        ctx: AgentCallbackContext,
        *,
        workspace,
    ) -> None:
        """Force schedule a session memory update, bypassing token/tool-call thresholds.

        Intended for the context-overflow recovery chain: when an LLM call
        fails with a 413 / context-length-exceeded error, we must capture
        key context into session memory *before* compacting, even if the
        normal trigger thresholds have not been reached.

        Note: This schedules an async background task and does **not** await
        its completion.  The FullCompact triggered by the recovery rail will
        read session-memory files; if the background write has not finished
        by then, FullCompact falls back to its own LLM-generated summary.
        In practice the background task typically completes before the retry
        reaches ``before_model_call → get_context_window``.
        """
        if workspace is None or ctx.session is None:
            return

        session_id = ctx.session.get_session_id()
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            logger.info(
                "[SessionMemory] skip force schedule: task already running session_id=%s",
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

        is_incremental = False
        update_context_window = completed_context_window
        if self.config.incremental_mode:
            incremental_result = self._select_incremental_context(completed_context_window, ctx.session)
            if incremental_result is not None:
                is_incremental = incremental_result["is_incremental"]
                update_context_window = incremental_result["context_window"]

        runtime = get_session_memory_runtime(ctx.session)
        runtime["is_extracting"] = True
        update_session_memory_runtime(ctx.session, runtime)
        logger.info(
            "[SessionMemory] FORCE schedule update session_id=%s notes_path=%s messages=%s incremental=%s",
            session_id,
            notes_path,
            len(update_context_window.context_messages),
            is_incremental,
        )

        bg_task = asyncio.create_task(
            self._update_background(ctx, workspace, update_context_window, is_incremental=is_incremental),
            name=f"session-memory-force-{session_id}",
        )
        self._task_owners[session_id] = (ctx.session, ctx.context)
        bg_task.add_done_callback(lambda done: self._on_task_done(session_id, done))
        self._tasks[session_id] = bg_task

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

        is_incremental = False
        update_context_window = completed_context_window
        if self.config.incremental_mode:
            incremental_result = self._select_incremental_context(completed_context_window, ctx.session)
            if incremental_result is None:
                logger.info("[SessionMemory] no new messages since last update, skip session_id=%s", session_id)
                return
            is_incremental = incremental_result["is_incremental"]
            update_context_window = incremental_result["context_window"]

        runtime = get_session_memory_runtime(ctx.session)
        runtime["is_extracting"] = True
        update_session_memory_runtime(ctx.session, runtime)
        logger.info(
            "[SessionMemory] schedule update session_obj=%s session_id=%s notes_path=%s messages=%s incremental=%s",
            hex(id(ctx.session)),
            session_id,
            notes_path,
            len(update_context_window.context_messages),
            is_incremental,
        )

        task = asyncio.create_task(
            self._update_background(ctx, workspace, update_context_window, is_incremental=is_incremental),
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

    def _select_incremental_context(
        self,
        context_window: ContextWindow,
        session,
    ) -> Dict[str, Any] | None:
        runtime = self._get_runtime_state(session)
        notes_upto_message_id = runtime.get("notes_upto_message_id")

        if not notes_upto_message_id:
            logger.debug("[SessionMemory] no anchor found, falling back to full scan")
            return {"is_incremental": False, "context_window": context_window}

        all_messages = list(context_window.context_messages or [])
        anchor_index = find_message_index_by_context_message_id(all_messages, notes_upto_message_id)
        if anchor_index < 0:
            logger.debug("[SessionMemory] anchor message lost, falling back to full scan anchor=%s",
                         notes_upto_message_id)
            invalidate_session_memory_anchor(session)
            return {"is_incremental": False, "context_window": context_window}

        incremental_messages = self._select_unsummarized_messages(all_messages, notes_upto_message_id)
        if not incremental_messages:
            logger.debug("[SessionMemory] no new messages since anchor, skip update")
            return None

        logger.debug(
            "[SessionMemory] incremental scan: total=%s incremental=%s anchor=%s",
            len(all_messages),
            len(incremental_messages),
            notes_upto_message_id,
        )
        incremental_context_window = ContextWindow(
            system_messages=list(context_window.system_messages or []),
            context_messages=incremental_messages,
            tools=list(context_window.tools or []),
        )
        return {"is_incremental": True, "context_window": incremental_context_window}

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
        *,
        is_incremental: bool = False,
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
        merged_notes = self._prepare_pending_session_memory(notes_path, pending_notes_path, current_notes)
        logger.info(
            "[SessionMemory] update_background start session_obj=%s session_id=%s "
            "mode=%s incremental=%s notes_path=%s pending_notes_path=%s messages=%s",
            hex(id(session)),
            session_id,
            self.config.update_mode,
            is_incremental,
            notes_path,
            pending_notes_path,
            len(messages),
        )
        try:
            full_context_window = self.collect_context_window(ctx)
            if not full_context_window.context_messages and context_window.context_messages:
                full_context_window = context_window
            full_scan_tokens = 0
            if is_incremental:
                full_scan_tokens = (
                    ContextUtils.estimate_tokens(self._update_agent.get_inherited_system_prompt() or "")
                    + sum(ContextUtils.estimate_message_tokens(m) for m in full_context_window.context_messages)
                    + ContextUtils.estimate_tokens(build_session_memory_prompt(str(pending_notes_path), merged_notes))
                )
            await self._update_agent.invoke(
                context_messages=context_window.get_messages(),
                notes_path=pending_notes_path,
                current_notes=merged_notes,
                is_incremental=is_incremental,
                trigger_tokens=self._count_tokens(ctx.context, full_context_window) if ctx.context else 0,
                full_scan_tokens=full_scan_tokens,
            )
            committed = self._commit_pending_session_memory(pending_notes_path, notes_path)
            if not committed:
                logger.warning(
                    "[SessionMemory] update skipped anchor advance session_id=%s "
                    "notes_path=%s pending_notes_path=%s reason=empty_commit_refused",
                    session_id,
                    notes_path,
                    pending_notes_path,
                )
                return
            if ctx.context is not None:
                runtime["tokens_at_last_update"] = self._count_tokens(ctx.context, full_context_window)
            runtime["tool_calls_at_last_update"] = self._count_tool_calls(list(full_context_window.context_messages))
            runtime["last_summarized_message_count"] = len(full_context_window.context_messages)
            runtime["notes_upto_message_id"] = get_context_message_id(messages[-1]) if messages else None
            runtime["initialized"] = True
            logger.info(
                "[SessionMemory] update complete notes_upto=%s count=%s tokens=%s tool_calls=%s incremental=%s",
                runtime["notes_upto_message_id"],
                runtime["last_summarized_message_count"],
                runtime["tokens_at_last_update"],
                runtime["tool_calls_at_last_update"],
                is_incremental,
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
    def notes_path_for(workspace, session_id: str, unit: str | None = None) -> Path:
        """Per-session notes (unit=None) or per-QA overview (unit=qa_id)."""
        base = Path(workspace.root_path) / "context" / f"{session_id}_context" / "session_memory"
        if unit is None:
            return base / "session_context.md"
        return base / f"{unit}.md"

    async def generate_overview_for_qa(
        self,
        ctx: Any,
        *,
        workspace: Any,
        qa_id: str,
        messages: List[BaseMessage],
        pending_path: Path,
        active_path: Path,
    ) -> None:
        """Full (non-incremental) per-QA overview; atomic commit pending → active."""
        _ = workspace
        inherited_messages = list(getattr(getattr(ctx, "inputs", None), "messages", None) or [])
        self._update_agent.set_inherited_system_prompt(build_system_prompt_text(inherited_messages))

        current_notes = self._read_or_init_session_memory(active_path)
        self._prepare_pending_session_memory(active_path, pending_path, current_notes)

        await self._update_agent.invoke(
            context_messages=messages,
            notes_path=pending_path,
            current_notes=current_notes,
            is_incremental=False,
        )

        if pending_path.exists():
            self._commit_pending_session_memory(pending_path, active_path)
        logger.info(
            "[SessionMemory] qa overview committed qa_id=%s path=%s messages=%s",
            qa_id,
            active_path,
            len(messages),
        )

    @staticmethod
    def _get_session_memory_path(workspace, session_id: str) -> Path:
        return SessionMemoryManager.notes_path_for(workspace, session_id, unit=None)

    @staticmethod
    def _get_pending_session_memory_path(path: Path) -> Path:
        return path.with_name(f"{path.stem}.pending{path.suffix}")

    @staticmethod
    def _read_or_init_session_memory(path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")

        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        template_path = SessionMemoryManager._get_workspace_template_path(path)
        if template_path.exists():
            shutil.copy2(template_path, path)
            return path.read_text(encoding="utf-8")

        logger.warning(
            "[SessionMemory] workspace template not found at %s (notes_path=%s), "
            "initializing session_context.md from DEFAULT_SESSION_MEMORY_TEMPLATE",
            template_path,
            path,
        )
        path.write_text(DEFAULT_SESSION_MEMORY_TEMPLATE, encoding="utf-8")
        return DEFAULT_SESSION_MEMORY_TEMPLATE

    @staticmethod
    def _prepare_pending_session_memory(active_path: Path, pending_path: Path, current_notes: str) -> str:
        template_path = SessionMemoryManager._get_workspace_template_path(active_path)
        if template_path.exists():
            template_content = template_path.read_text(encoding="utf-8")
        else:
            logger.warning(
                "[SessionMemory] workspace template not found at %s (notes_path=%s), "
                "falling back to DEFAULT_SESSION_MEMORY_TEMPLATE for pending merge",
                template_path,
                active_path,
            )
            template_content = DEFAULT_SESSION_MEMORY_TEMPLATE

        current_sections = _parse_session_memory_into_sections(current_notes, include_descriptions=False)

        current_by_header: Dict[str, List[str]] = {}
        for sec in current_sections:
            body_text = "\n".join(sec["body_lines"]).strip()
            if not body_text:
                continue
            current_by_header[sec["header"]] = list(sec["body_lines"])

        template_sections = _parse_session_memory_into_sections(template_content, include_descriptions=True)

        matched_headers: set[str] = set()
        merged_blocks: List[str] = []
        for template_section in template_sections:
            header = template_section["header"]
            body_lines: List[str] = []
            if header in current_by_header:
                body_lines = list(current_by_header[header])
                matched_headers.add(header)

            block_lines = [header]
            if template_section["description"] is not None:
                block_lines.append(template_section["description"])
            body = "\n".join(body_lines).strip()
            if body:
                block_lines.append(body)
            merged_blocks.append("\n".join(block_lines))

        for sec in current_sections:
            body_text = "\n".join(sec["body_lines"]).strip()
            if not body_text:
                continue
            if sec["header"] not in matched_headers:
                logger.warning(
                    "[SessionMemory] section body not merged into template: "
                    "header=%r body_chars=%s",
                    sec["header"],
                    len(body_text),
                )

        merged_notes = "\n\n".join(merged_blocks).strip()
        if merged_notes:
            merged_notes += "\n"

        if not pending_path.parent.exists():
            pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(merged_notes, encoding="utf-8")
        return merged_notes

    @staticmethod
    def _commit_pending_session_memory(pending_path: Path, active_path: Path) -> bool:
        if not pending_path.exists():
            raise RuntimeError(f"Pending session memory does not exist: {pending_path}")
        stripped = _clean_session_memory_sections(pending_path.read_text(encoding="utf-8"))
        if not stripped.strip():
            logger.warning(
                "[SessionMemory] refusing empty commit: pending=%s active=%s",
                pending_path,
                active_path,
            )
            return False
        pending_path.write_text(stripped, encoding="utf-8")
        pending_path.replace(active_path)
        return True

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
    "INCREMENTAL_SESSION_MEMORY_PROMPT",
    "INCREMENTAL_DIRECT_SESSION_MEMORY_PROMPT",
    "SessionMemoryConfig",
    "SessionMemoryManager",
    "SessionMemoryUpdateAgent",
    "build_session_memory_prompt",
    "build_incremental_session_memory_prompt",
    "build_incremental_direct_session_memory_prompt",
    "find_message_index_by_context_message_id",
    "get_context_message_id",
    "get_session_memory_runtime",
    "invalidate_session_memory_anchor",
    "update_session_memory_runtime",
]
