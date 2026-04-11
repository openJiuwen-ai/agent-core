# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
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
from openjiuwen.core.foundation.tool import ToolInfo

NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

DETAILED_ANALYSIS_INSTRUCTION = """Before providing your final summary, wrap your analysis in <analysis> 
tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something 
     differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.
"""

BASE_COMPACT_PROMPT = (
    NO_TOOLS_PREAMBLE
    + """Your task is to create a detailed summary of the conversation so far, 
    paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, 
and architectural decisions that would be essential for continuing development work without losing context.

"""
    + DETAILED_ANALYSIS_INSTRUCTION
    + """
Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. 
    Pay special attention to the most recent messages and include full code snippets where applicable and 
    include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. 
    Pay special attention to specific user feedback that you received, 
    especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. 
    These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, 
    paying special attention to the most recent messages from both user and assistant. 
    Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. 
    IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, 
    and the task you were working on immediately before this summary request. If your last task was concluded, 
    then only list next steps if they are explicitly in line with the users request. 
    Do not start on tangential requests or really old requests that were already completed without 
    confirming with the user first.
    If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were
    working on and where you left off. 
    This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, 
following this structure and ensuring precision and thoroughness in your response.
"""
    + "\n\nREMINDER: Do NOT call any tools. "
      "Respond with plain text only — an <analysis> block followed by a <summary> block. "
      "Tool calls will be rejected and you will fail the task."
)


class FullCompactProcessorConfig(BaseModel):
    trigger_total_tokens: int = Field(default=230000, gt=0)
    compression_call_max_tokens: int = Field(default=250000, gt=0)
    messages_to_keep: int = Field(default=10, ge=0)
    keep_tool_message_pairs: bool = Field(default=True)
    state_snapshot_max_chars: int = Field(default=4000, gt=0)
    reinject_recent_tool_calls: int = Field(default=8, ge=0)
    reinject_file_tool_names: List[str] = Field(
        default=["read_file", "write_file", "edit_file", "glob", "grep"]
    )
    reinject_tool_result_hint_names: List[str] = Field(
        default=["read_file", "write_file", "edit_file", "glob", "grep"]
    )
    model: ModelRequestConfig | None = Field(default=None)
    model_client: ModelClientConfig | None = Field(default=None)
    marker: str = Field(default="[FULL_COMPACT_BOUNDARY]")
    state_marker: str = Field(default="[FULL_COMPACT_STATE]")
    synthetic_user_marker: str = Field(
        default="[earlier conversation truncated for compaction retry]"
    )
    summary_intro: str = Field(
        default=(
            "This session is being continued from a previous conversation that "
            "ran out of context. The summary below covers the earlier portion "
            "of the conversation."
        )
    )
    recent_messages_notice: str = Field(default="Recent messages are preserved verbatim.")


@ContextEngine.register_processor()
class FullCompactProcessor(ContextProcessor):
    """Fallback compactor aligned with Claude Code's full compact flow."""

    @property
    def config(self) -> FullCompactProcessorConfig:
        return self._config

    async def trigger_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> bool:
        combined_messages = context.get_messages() + list(messages_to_add)
        total_tokens = self._count_message_tokens(combined_messages, context)
        return total_tokens > self.config.trigger_total_tokens

    async def on_add_messages(
        self,
        context: ModelContext,
        messages_to_add: List[BaseMessage],
        **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        all_messages = context.get_messages() + list(messages_to_add)

        if not all_messages:
            return None, messages_to_add

        boundary_index = self._find_last_boundary_index(all_messages)
        prefix = all_messages[:boundary_index] if boundary_index > 0 else []
        active_messages = (
            all_messages[boundary_index + 1:] if boundary_index >= 0 else all_messages
        )
        if not active_messages:
            return None, messages_to_add

        new_context_messages = await self._build_full_compact_messages(
            context=context,
            prefix=prefix,
            active_messages=active_messages,
        )
        if new_context_messages is None:
            return None, messages_to_add

        context.set_messages(new_context_messages)
        start_idx = max(boundary_index + 1, 0)
        end_idx = len(all_messages) - 1
        return ContextEvent(
            event_type=self.processor_type(),
            messages_to_modify=list(range(start_idx, end_idx + 1)),
        ), []

    async def _build_full_compact_messages(
        self,
        *,
        context: ModelContext,
        prefix: List[BaseMessage],
        active_messages: List[BaseMessage],
    ) -> Optional[List[BaseMessage]]:
        compact_source = self._prepare_messages_for_prompt(
            self._strip_media_messages(active_messages)
        )
        if not compact_source:
            return None

        compact_input = self._truncate_for_prompt_budget(compact_source, context)
        if not compact_input:
            return None

        summary = await self._generate_summary(compact_input, context)
        if not summary:
            return None

        messages_to_keep = self._select_messages_to_keep(active_messages)
        summary_message = UserMessage(
            content=self._build_summary_message(summary, bool(messages_to_keep))
        )
        boundary = SystemMessage(content=f"{self.config.marker}\nConversation compacted")

        new_context_messages = prefix + [boundary, summary_message]
        new_context_messages.extend(messages_to_keep)
        new_context_messages.extend(
            self.build_reinjected_state_messages(
                context=context,
                source_messages=active_messages,
                messages_to_keep=messages_to_keep,
                summary_message=summary_message,
                boundary_message=boundary,
            )
        )
        return new_context_messages

    async def _generate_summary(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> str:
        if self.config.model is None or self.config.model_client is None:
            return self._build_fallback_summary(messages)

        model = Model(self.config.model_client, self.config.model)
        prompt_messages = [
            SystemMessage(content=BASE_COMPACT_PROMPT),
            UserMessage(content=self._serialize_messages(messages)),
        ]
        response = await model.invoke(messages=prompt_messages, tools=None)
        content = (response.content or "").strip()
        if not content:
            return self._build_fallback_summary(messages)
        return self._format_summary(content)

    def _truncate_for_prompt_budget(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> List[BaseMessage]:
        groups = self._group_messages_by_api_round(messages)
        while groups:
            candidate = [msg for group in groups for msg in group]
            if self._count_prompt_tokens(candidate, context) <= self.config.compression_call_max_tokens:
                return candidate
            if len(groups) == 1:
                return self._truncate_messages_from_head(candidate, context)
            groups = groups[1:]
            if groups and isinstance(groups[0][0], AssistantMessage):
                groups = [[UserMessage(content=self.config.synthetic_user_marker)]] + groups
        return self._build_minimal_compact_input(messages)

    def _truncate_messages_from_head(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> List[BaseMessage]:
        candidate = list(messages)
        if candidate and self._is_synthetic_marker_message(candidate[0]):
            candidate = candidate[1:]
        while candidate:
            if self._count_prompt_tokens(candidate, context) <= self.config.compression_call_max_tokens:
                return candidate
            candidate = candidate[1:]
            if candidate and isinstance(candidate[0], AssistantMessage):
                candidate = [UserMessage(content=self.config.synthetic_user_marker)] + candidate
        return self._build_minimal_compact_input(messages)

    def _group_messages_by_api_round(self, messages: List[BaseMessage]) -> List[List[BaseMessage]]:
        groups: List[List[BaseMessage]] = []
        current_non_user_group: List[BaseMessage] = []

        for message in messages:
            if isinstance(message, UserMessage):
                if current_non_user_group:
                    groups.append(current_non_user_group)
                    current_non_user_group = []
                groups.append([message])
                continue
            current_non_user_group.append(message)

        if current_non_user_group:
            groups.append(current_non_user_group)

        return groups

    def _build_minimal_compact_input(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        if not messages:
            return []

        tail: List[BaseMessage] = [messages[-1]]
        if isinstance(tail[0], AssistantMessage):
            return [UserMessage(content=self.config.synthetic_user_marker), tail[0]]
        return tail

    def _select_messages_to_keep(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        keep_recent = self.config.messages_to_keep
        if keep_recent <= 0 or not messages:
            return []

        start_index = max(len(messages) - keep_recent, 0)
        if self.config.keep_tool_message_pairs:
            start_index = self._adjust_start_index_for_tool_pairs(messages, start_index)
        return list(messages[start_index:])

    def _adjust_start_index_for_tool_pairs(
        self,
        messages: List[BaseMessage],
        start_index: int,
    ) -> int:
        if start_index <= 0 or start_index >= len(messages):
            return start_index

        adjusted = start_index
        needed_tool_ids = {
            msg.tool_call_id
            for msg in messages[start_index:]
            if isinstance(msg, ToolMessage) and getattr(msg, "tool_call_id", None)
        }
        if not needed_tool_ids:
            return adjusted

        present_tool_calls = set()
        for msg in messages[start_index:]:
            if isinstance(msg, AssistantMessage):
                for tool_call in getattr(msg, "tool_calls", None) or []:
                    tool_call_id = getattr(tool_call, "id", None)
                    if tool_call_id:
                        present_tool_calls.add(tool_call_id)

        missing_tool_calls = needed_tool_ids - present_tool_calls
        if not missing_tool_calls:
            return adjusted

        for index in range(start_index - 1, -1, -1):
            msg = messages[index]
            if not isinstance(msg, AssistantMessage):
                continue
            tool_calls = getattr(msg, "tool_calls", None) or []
            matched = False
            for tool_call in tool_calls:
                tool_call_id = getattr(tool_call, "id", None)
                if tool_call_id in missing_tool_calls:
                    missing_tool_calls.discard(tool_call_id)
                    matched = True
            if matched:
                adjusted = index
            if not missing_tool_calls:
                break
        return adjusted

    @staticmethod
    def _strip_media_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
        stripped: List[BaseMessage] = []
        for msg in messages:
            content = getattr(msg, "content", "")
            if not isinstance(content, list):
                stripped.append(msg)
                continue

            changed = False
            new_content: List[Any] = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type")
                    if block_type == "image":
                        new_content.append("[image]")
                        changed = True
                        continue
                    if block_type == "document":
                        new_content.append("[document]")
                        changed = True
                        continue
                    if block_type == "tool_result":
                        nested = block.get("content")
                        if isinstance(nested, list):
                            nested_changed = False
                            nested_content: List[Any] = []
                            for nested_block in nested:
                                if isinstance(nested_block, dict) and nested_block.get("type") == "image":
                                    nested_content.append({"type": "text", "text": "[image]"})
                                    nested_changed = True
                                elif isinstance(nested_block, dict) and nested_block.get("type") == "document":
                                    nested_content.append({"type": "text", "text": "[document]"})
                                    nested_changed = True
                                else:
                                    nested_content.append(nested_block)
                            if nested_changed:
                                copied_block = dict(block)
                                copied_block["content"] = nested_content
                                new_content.append(copied_block)
                                changed = True
                                continue
                new_content.append(block)

            stripped.append(msg.model_copy(update={"content": new_content}) if changed else msg)
        return stripped

    def _prepare_messages_for_prompt(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        result: List[BaseMessage] = []
        for msg in messages:
            if self._is_boundary_message(msg) or self._is_state_message(msg):
                continue
            result.append(msg)
        return result

    def _build_summary_message(self, summary: str, has_preserved_messages: bool) -> str:
        parts = [self.config.summary_intro, "", summary]
        if has_preserved_messages:
            parts.extend(["", self.config.recent_messages_notice])
        return "\n".join(parts)

    def build_reinjected_state_messages(
        self,
        *,
        context: ModelContext,
        source_messages: List[BaseMessage],
        messages_to_keep: List[BaseMessage],
        summary_message: UserMessage,
        boundary_message: SystemMessage,
    ) -> List[BaseMessage]:
        """Build post-compact state anchors by parsing recoverable text patterns.

        This mirrors Claude Code's post-compact restoration at a lighter weight:
        instead of querying external runtime stores, it recovers plan/skill/file
        anchors directly from the message text that already exists in context.
        """
        _ = context, summary_message, boundary_message

        candidate_messages = self._prepare_messages_for_prompt(source_messages)
        if not candidate_messages:
            return []

        state_messages: List[BaseMessage] = []
        plan_anchor = self._extract_plan_anchor(candidate_messages)
        if plan_anchor:
            state_messages.append(self._make_state_message("PLAN", plan_anchor))

        skill_anchor = self._extract_skill_anchor(candidate_messages)
        if skill_anchor:
            state_messages.append(self._make_state_message("SKILLS", skill_anchor))

        file_anchor = self._extract_file_anchor(candidate_messages, messages_to_keep)
        if file_anchor:
            state_messages.append(self._make_state_message("FILES", file_anchor))

        return state_messages

    def _extract_plan_anchor(self, messages: List[BaseMessage]) -> str:
        progress_markers = (
            "以下是当前任务规划中所有任务的内容和状态",
            "The following is the content and status of all tasks in the current task plan",
        )
        task_line_pattern = re.compile(
            r"id:\s*(?P<id>[^|\n]+)\|status:\s*(?P<status>[^|\n]+)\|content:\s*(?P<content>[^\n]+)"
        )
        in_progress_markers = (
            "正在执行的任务为",
            "The task currently being executed is",
        )

        for message in reversed(messages):
            text = self._message_to_text(message)
            if not any(marker in text for marker in progress_markers):
                continue

            task_lines = [
                "- id="
                f"{match.group('id').strip()} "
                "status="
                f"{match.group('status').strip()} "
                "content="
                f"{match.group('content').strip()}"
                for match in task_line_pattern.finditer(text)
            ]
            if not task_lines:
                continue

            in_progress = ""
            lines = [line.strip() for line in text.splitlines()]
            for index, line in enumerate(lines):
                if any(marker in line for marker in in_progress_markers):
                    for follow in lines[index + 1:]:
                        if follow:
                            in_progress = follow
                            break
                    break

            content = ["Recovered task plan snapshot:", *task_lines]
            if in_progress:
                content.extend(["", f"Current in-progress task: {in_progress}"])
            return "\n".join(content)
        return ""

    def _extract_skill_anchor(self, messages: List[BaseMessage]) -> str:
        skill_reads: List[str] = []
        seen_entries: set[str] = set()

        for message in reversed(messages):
            if not isinstance(message, AssistantMessage):
                continue
            tool_calls = getattr(message, "tool_calls", None) or []
            for tool_call in reversed(tool_calls):
                tool_name = getattr(tool_call, "name", "") or ""
                if tool_name != "read_file":
                    continue

                arguments_text = getattr(tool_call, "arguments", "") or ""
                parsed_arguments = self._parse_tool_arguments(arguments_text)
                file_path = self._extract_argument_value(
                    parsed_arguments, arguments_text, ("file_path",)
                )
                if not self._is_skill_file_path(file_path):
                    continue

                tool_call_id = getattr(tool_call, "id", None)
                result_text = self._find_tool_result_text(messages, tool_call_id)
                skill_text = self._extract_skill_file_content(result_text)
                skill_name = self._extract_skill_name_from_path(file_path)

                entry_parts = [f"path={file_path}"]
                if skill_name:
                    entry_parts.insert(0, f"skill={skill_name}")
                header = " | ".join(entry_parts)
                entry = header
                if skill_text:
                    entry = f"{header}\n{skill_text}"

                if entry in seen_entries:
                    continue
                seen_entries.add(entry)
                skill_reads.append(entry)
                if len(skill_reads) >= self.config.reinject_recent_tool_calls:
                    break
            if len(skill_reads) >= self.config.reinject_recent_tool_calls:
                break

        if skill_reads:
            skill_reads.reverse()
            return "Recovered skill file context:\n" + "\n\n".join(
                f"- {entry}" for entry in skill_reads
            )
        return ""

    @staticmethod
    def _is_skill_file_path(file_path: str) -> bool:
        if not file_path:
            return False
        normalized = file_path.replace("\\", "/").lower()
        return normalized.endswith("/skill.md") or normalized.endswith("skill.md")

    @staticmethod
    def _extract_skill_name_from_path(file_path: str) -> str:
        if not file_path:
            return ""
        normalized = file_path.replace("\\", "/").rstrip("/")
        parts = normalized.split("/")
        if len(parts) >= 2 and parts[-1].lower() == "skill.md":
            return parts[-2]
        return ""

    def _extract_skill_file_content(self, result_text: str) -> str:
        if not result_text:
            return ""

        content_match = re.search(
            r'"content"\s*:\s*"(?P<content>(?:[^"\\]|\\.)*)"',
            result_text,
            re.DOTALL,
        )
        content = ""
        if content_match:
            raw_content = content_match.group("content")
            try:
                content = json.loads(f'"{raw_content}"')
            except Exception:
                content = raw_content.replace('\\"', '"').replace("\\n", "\n")
        else:
            content = result_text

        content = content.strip()
        if not content:
            return ""
        return self._truncate_state_text(content)

    def _extract_file_anchor(
        self,
        messages: List[BaseMessage],
        messages_to_keep: List[BaseMessage],
    ) -> str:
        keep_ids = {
            getattr(msg, "tool_call_id", None)
            for msg in messages_to_keep
            if isinstance(msg, ToolMessage)
        }
        recent_entries: List[str] = []
        allowed_tool_names = set(self.config.reinject_file_tool_names)
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if not isinstance(message, AssistantMessage):
                continue

            tool_calls = getattr(message, "tool_calls", None) or []
            for tool_call in reversed(tool_calls):
                tool_name = getattr(tool_call, "name", "") or ""
                tool_call_id = getattr(tool_call, "id", None)
                if tool_call_id and tool_call_id in keep_ids:
                    continue
                if tool_name not in allowed_tool_names:
                    continue

                arguments_text = getattr(tool_call, "arguments", "") or ""
                detail = self._describe_tool_call(tool_name, arguments_text)
                result_text = self._find_tool_result_text(messages, tool_call_id)
                result_hint = self._extract_tool_result_hint(tool_name, result_text)
                line = detail
                if result_hint:
                    line = f"{line} | {result_hint}"
                if line not in recent_entries:
                    recent_entries.append(line)
                if len(recent_entries) >= self.config.reinject_recent_tool_calls:
                    break
            if len(recent_entries) >= self.config.reinject_recent_tool_calls:
                break

        if not recent_entries:
            return ""

        recent_entries.reverse()
        return "Recovered recent file/tool context:\n" + "\n".join(
            f"- {entry}" for entry in recent_entries
        )

    def _make_state_message(self, label: str, content: str) -> UserMessage:
        compact_content = self._truncate_state_text(content)
        return UserMessage(
            content=f"{self.config.state_marker}\n[{label}]\n{compact_content}"
        )

    def _truncate_state_text(self, text: str) -> str:
        if len(text) <= self.config.state_snapshot_max_chars:
            return text
        return self._build_head_tail_truncated_text(
            text, self.config.state_snapshot_max_chars
        )

    def _describe_tool_call(self, tool_name: str, arguments_text: str) -> str:
        parsed_arguments = self._parse_tool_arguments(arguments_text)
        if tool_name == "read_file":
            file_path = self._extract_argument_value(
                parsed_arguments, arguments_text, ("file_path",)
            )
            return f"read_file path={file_path or '[unknown]'}"
        if tool_name == "write_file":
            file_path = self._extract_argument_value(
                parsed_arguments, arguments_text, ("file_path",)
            )
            return f"write_file path={file_path or '[unknown]'}"
        if tool_name == "edit_file":
            file_path = self._extract_argument_value(
                parsed_arguments, arguments_text, ("file_path",)
            )
            return f"edit_file path={file_path or '[unknown]'}"
        if tool_name == "glob":
            pattern = self._extract_argument_value(
                parsed_arguments, arguments_text, ("pattern",)
            )
            path = self._extract_argument_value(
                parsed_arguments, arguments_text, ("path",)
            )
            return f"glob pattern={pattern or '[unknown]'} path={path or '.'}"
        if tool_name == "grep":
            pattern = self._extract_argument_value(
                parsed_arguments, arguments_text, ("pattern",)
            )
            path = self._extract_argument_value(
                parsed_arguments, arguments_text, ("path", "file_path")
            )
            return f"grep pattern={pattern or '[unknown]'} path={path or '[unknown]'}"
        return f"{tool_name} args={arguments_text}"

    @staticmethod
    def _parse_tool_arguments(arguments_text: str) -> Dict[str, Any]:
        if not arguments_text:
            return {}
        try:
            parsed = json.loads(arguments_text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _extract_argument_value(
        parsed_arguments: Dict[str, Any],
        arguments_text: str,
        keys: Tuple[str, ...],
    ) -> str:
        for key in keys:
            value = parsed_arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in keys:
            match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', arguments_text)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _find_tool_result_text(
        messages: List[BaseMessage],
        tool_call_id: Optional[str],
    ) -> str:
        if not tool_call_id:
            return ""
        for message in reversed(messages):
            if (
                isinstance(message, ToolMessage)
                and getattr(message, "tool_call_id", None) == tool_call_id
            ):
                return FullCompactProcessor._message_to_text(message)
        return ""

    def _extract_tool_result_hint(self, tool_name: str, result_text: str) -> str:
        if not result_text:
            return ""
        allowed_tool_names = set(
            self.config.reinject_tool_result_hint_names
        )
        if tool_name not in allowed_tool_names:
            return ""
        if tool_name == "read_file":
            file_path_match = re.search(r'"file_path"\s*:\s*"([^"]+)"', result_text)
            line_count_match = re.search(r'"line_count"\s*:\s*(\d+)', result_text)
            parts = []
            if file_path_match:
                parts.append(f"result_path={file_path_match.group(1)}")
            if line_count_match:
                parts.append(f"lines={line_count_match.group(1)}")
            return " ".join(parts)
        if tool_name == "glob":
            count_match = re.search(r'"count"\s*:\s*(\d+)', result_text)
            if count_match:
                return f"matches={count_match.group(1)}"
        if tool_name == "grep":
            count_match = re.search(r'"count"\s*:\s*(\d+)', result_text)
            if count_match:
                return f"hits={count_match.group(1)}"
        if tool_name == "edit_file":
            replacements_match = re.search(r'"replacements"\s*:\s*(\d+)', result_text)
            if replacements_match:
                return f"replacements={replacements_match.group(1)}"
        if tool_name == "write_file":
            bytes_match = re.search(r'"bytes_written"\s*:\s*(\d+)', result_text)
            if bytes_match:
                return f"bytes_written={bytes_match.group(1)}"
        return ""

    def _starts_new_api_round(self, current: List[BaseMessage]) -> bool:
        if not current:
            return False
        return any(isinstance(msg, (AssistantMessage, ToolMessage)) for msg in current)

    @staticmethod
    def _ends_api_round(message: BaseMessage) -> bool:
        return isinstance(message, AssistantMessage) and not (getattr(message, "tool_calls", None) or [])

    def _count_prompt_tokens(self, messages: List[BaseMessage], context: ModelContext) -> int:
        prompt_messages = [
            SystemMessage(content=BASE_COMPACT_PROMPT),
            UserMessage(content=self._serialize_messages(messages)),
        ]
        token_counter = context.token_counter()
        if token_counter is not None:
            try:
                return token_counter.count_messages(prompt_messages)
            except Exception:
                return sum(
                    self._estimate_message_tokens(message)
                    for message in prompt_messages
                )
        return sum(
            self._estimate_message_tokens(message)
            for message in prompt_messages
        )

    def _count_message_tokens(
        self,
        messages: List[BaseMessage],
        context: ModelContext,
    ) -> int:
        token_counter = context.token_counter()
        if token_counter is not None:
            try:
                return token_counter.count_messages(messages)
            except Exception:
                return sum(
                    self._estimate_message_tokens(message)
                    for message in messages
                )
        return sum(self._estimate_message_tokens(message) for message in messages)

    def _count_context_window_tokens(
        self,
        system_messages: List[BaseMessage],
        context_messages: List[BaseMessage],
        tools: List[ToolInfo],
        context: ModelContext,
    ) -> int:
        token_counter = context.token_counter()
        all_messages = list(system_messages or []) + list(context_messages or [])
        total = 0
        if token_counter is not None:
            try:
                total += token_counter.count_messages(all_messages)
                total += token_counter.count_tools(list(tools or []))
                return total
            except Exception:
                total = 0
        total += sum(self._estimate_message_tokens(message) for message in all_messages)
        total += sum(self._estimate_tool_tokens(tool) for tool in (tools or []))
        return total

    def _build_fallback_summary(self, messages: List[BaseMessage]) -> str:
        lines = []
        for idx, msg in enumerate(messages[-20:], start=max(len(messages) - 19, 1)):
            lines.append(f"[{idx}] {msg.role}: {self._message_to_text(msg)}")
        return "Summary:\n" + "\n".join(lines)

    @staticmethod
    def _format_summary(content: str) -> str:
        stripped = re.sub(r"<analysis>[\s\S]*?</analysis>", "", content).strip()
        match = re.search(r"<summary>([\s\S]*?)</summary>", stripped)
        if match:
            return "Summary:\n" + match.group(1).strip()
        return stripped

    @staticmethod
    def _serialize_messages(messages: List[BaseMessage]) -> str:
        return "\n".join(
            f"role={msg.role}, content={FullCompactProcessor._message_to_text(msg)}"
            for msg in messages
        )

    @staticmethod
    def _message_to_text(message: BaseMessage) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, ensure_ascii=False)
        except TypeError:
            return str(content)

    @staticmethod
    def _estimate_message_tokens(message: BaseMessage) -> int:
        return max(len(FullCompactProcessor._message_to_text(message)) // 3, 1)

    @staticmethod
    def _estimate_tool_tokens(tool: ToolInfo) -> int:
        try:
            return max(len(json.dumps(tool.model_dump(), ensure_ascii=False)) // 3, 1)
        except Exception:
            return max(len(str(tool)) // 3, 1)

    def _find_last_boundary_index(self, messages: List[BaseMessage]) -> int:
        for idx in range(len(messages) - 1, -1, -1):
            if self._is_boundary_message(messages[idx]):
                return idx
        return -1

    def _is_boundary_message(self, message: BaseMessage) -> bool:
        return (
            isinstance(message, SystemMessage)
            and isinstance(message.content, str)
            and message.content.startswith(self.config.marker)
        )

    def _is_state_message(self, message: BaseMessage) -> bool:
        return (
            isinstance(message, SystemMessage)
            and isinstance(message.content, str)
            and message.content.startswith(self.config.state_marker)
        )

    def _is_synthetic_marker_message(self, message: BaseMessage) -> bool:
        return isinstance(message, UserMessage) and message.content == self.config.synthetic_user_marker

    @staticmethod
    def _build_head_tail_truncated_text(text: str, kept_chars: int) -> str:
        if kept_chars <= 0:
            return "...[TRUNCATED]..."
        head_chars = max(int(kept_chars * 0.2), 0)
        tail_chars = max(kept_chars - head_chars, 0)
        head = text[:head_chars]
        tail = text[-tail_chars:] if tail_chars > 0 else ""
        if head and tail:
            return f"{head}\n...[TRUNCATED]...\n{tail}"
        return head or tail or "...[TRUNCATED]..."

    def load_state(self, state: Dict[str, Any]) -> None:
        return

    def save_state(self) -> Dict[str, Any]:
        return {}
