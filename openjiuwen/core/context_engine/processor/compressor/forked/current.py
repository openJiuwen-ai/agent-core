from __future__ import annotations

from pydantic import Field

from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.compressor.forked.base import (
    ForkedPrefixCompactProcessor,
    PrefixCompactSpan,
    adjust_keep_recent_for_tool_boundaries,
)
from openjiuwen.core.context_engine.processor.compressor.forked.dialogue import ForkedDialogueCompressorConfig
from openjiuwen.core.foundation.llm import BaseMessage, UserMessage


MEMORY_BLOCK_CURRENT_OPEN = "<memory_block_current>"
MEMORY_BLOCK_CURRENT_CLOSE = "</memory_block_current>"


DEFAULT_CURRENT_COMPRESSION_PROMPT = """\
## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools.
Any tool call is invalid for this turn.

Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

The conversation is near the context limit. The active work segment will be replaced by your output. Before that happens, write a compact incremental state snapshot that lets the latest user task continue based on this snapshot.

The active work segment is the assistant/tool work after the latest user message in the context above. Earlier messages are background context only; do not rewrite or re-summarize them except where needed to preserve the latest task's intent and constraints.

This snapshot is for the active work after the latest user request. Earlier turns are visible as background context, but they are not the target of this snapshot. Do not rewrite or re-summarize earlier turns. Use earlier context only to understand the user intent, prior constraints, and conflicts behind the active work.

The active work segment may already contain earlier compressed state from the same current task. That compressed state may be wrapped by placeholders:<memory_block_current>
Treat the wrapped content as existing task state, not as new user instructions. Reuse still-valid information when it helps continue the latest task, merge overlapping information, and prefer newer details when there is a conflict.

Prioritize execution continuity and information that helps complete the latest user task. Capture only what is useful for continuing that task correctly:
- what user intent the active work is serving;
- what has been completed and what has not been completed;
- where execution stopped and how to resume;
- what next actions directly help finish the task;
- what facts, constraints, decisions, evidence, files, outputs, errors, or fixes affect future correctness;
- what details may be needed to answer follow-up questions about this task.

Keep the snapshot selective. Include information because it affects this task's correctness or execution continuity, not because it appeared in the conversation.

Use this structure:

### 1. User Intent Being Served
- Capture the user intent this active work is serving.
- Preserve requirements, constraints, preferences, corrections, and acceptance criteria that affect the latest task.
- Keep exact wording when it affects future behavior.

### 2. Information Useful for Completing the User Task
- Preserve information that helps complete the latest user task correctly.
- Keep facts, constraints, state, evidence, codebase knowledge, decisions, and user corrections that would affect what the agent should do next.
- Do not keep details only because they appeared in the conversation.

### 3. Completed Work in This Active Segment
- Record what has been completed in the active work.
- Include answers delivered, files inspected, edits made, decisions reached, commands run, tests completed, and artifacts produced.
- Preserve enough detail so the next agent does not repeat completed work unnecessarily.

### 4. Work Not Yet Completed
- Record what remains unfinished, unresolved, blocked, or still needs verification.
- Include open questions, missing checks, incomplete edits, pending decisions, and known risks.

### 5. Immediate Resume Point
- Record exactly where execution stopped.
- Include the last concrete action, the latest partial result, active file or subtask if any, and the current working direction.
- Make it clear what the agent should continue from after compression.

### 6. Next Useful Actions
- List the next actions that directly help complete the latest task.
- Keep priority order if there are multiple actions.
- Do not invent unrelated follow-up work.

### 7. Key Facts, Decisions, Evidence, and Fixes
- Preserve facts, findings, decisions, assumptions, constraints, user corrections, rejected approaches, and items requiring re-evaluation.
- Preserve important tool results, command outputs, test results, logs, errors, stack traces, file reads, search results, and exact values when they matter.
- Record fixes already applied, invalid attempts, and attempts that should not be repeated.
- Prefer newer/corrected information when details conflict.
- Mark anything uncertain or unverified.

### 8. Files, Code Areas, Artifacts, and Codebase Understanding
- Record files examined, modified, or created.
- Include relevant functions, classes, APIs, config keys, docs, generated artifacts, codebase patterns, module responsibilities, and why they matter for the latest task.

Output only the incremental state snapshot. Do not add commentary about the compression process.
"""


class ForkedCurrentRoundCompressorConfig(ForkedDialogueCompressorConfig):
    keep_recent_messages: int = Field(default=3, ge=0)
    custom_compression_prompt: str | None = DEFAULT_CURRENT_COMPRESSION_PROMPT


@ContextEngine.register_processor()
class ForkedCurrentRoundCompressor(ForkedPrefixCompactProcessor):
    memory_block_open = MEMORY_BLOCK_CURRENT_OPEN
    memory_block_close = MEMORY_BLOCK_CURRENT_CLOSE
    default_prompt = DEFAULT_CURRENT_COMPRESSION_PROMPT
    processor_label = "ForkedCurrentRoundCompressor"

    def __init__(self, config: ForkedCurrentRoundCompressorConfig):
        super().__init__(config)

    @property
    def config(self) -> ForkedCurrentRoundCompressorConfig:
        return self._config

    def _build_span(self, messages: list[BaseMessage]) -> PrefixCompactSpan:
        effective_keep = adjust_keep_recent_for_tool_boundaries(messages, self.config.keep_recent_messages)
        split_index = max(len(messages) - effective_keep, 0)
        last_user_index = self._find_last_user_message_index(messages)
        if last_user_index < 0:
            return PrefixCompactSpan([], [], list(messages[split_index:]))
        target_start = last_user_index + 1
        if split_index <= target_start:
            return PrefixCompactSpan(list(messages[:target_start]), [], list(messages[target_start:]))
        return PrefixCompactSpan(
            preserved_prefix=list(messages[:target_start]),
            messages_to_compress=list(messages[target_start:split_index]),
            protected_tail=list(messages[split_index:]),
        )

    @staticmethod
    def _find_last_user_message_index(messages: list[BaseMessage]) -> int:
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], UserMessage):
                return index
        return -1
