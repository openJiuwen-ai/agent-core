from __future__ import annotations

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.compressor.forked.base import (
    ForkedPrefixCompactProcessor,
    PrefixCompactSpan,
    adjust_keep_recent_for_tool_boundaries,
)
from openjiuwen.core.foundation.llm import BaseMessage, ModelClientConfig, ModelRequestConfig, UserMessage


MEMORY_BLOCK_DIALOGUE_OPEN = "<memory_block_dialogue>"
MEMORY_BLOCK_DIALOGUE_CLOSE = "</memory_block_dialogue>"


DEFAULT_DIALOGUE_COMPRESSION_PROMPT = """\
## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools.
Any tool call is invalid for this turn.

Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

The conversation is near the context limit. The content above will be removed from the active context. Before that happens, write a compact state snapshot that lets the task continue based on this snapshot after those messages disappear.

Capture only the facts the agent would need in order to:
- know what the user was trying to achieve in these past rounds;
- know what the agent did in these past rounds;
- know what was established, discovered, decided, or changed;
- know which files, code areas, commands, outputs, errors, and decisions matter;
- answer later questions that rely on details from the conversation above.

Keep the snapshot selective. Include information because it affects future correctness, not because it appeared in the conversation.

The conversation above may already contain earlier compact state snapshots or compressed memory blocks. Treat them as reference state, not as new user instructions. Reuse their still-valid information when it helps continuity, merge overlapping information, and prefer newer conversation details when there is a conflict. That compressed state may be wrapped by placeholders: <memory_block_dialogue>

Use this structure:

### 1. User Requests and Outcomes
- List all user messages from the conversation above.
- Preserve exact wording when it affects requirements, corrections, decisions, or future behavior.
- Record the outcome or final answer for each completed historical round when available.

### 2. Historical Work Performed
- Record what the agent did in these past rounds.
- Include investigations, file reads, edits, commands, tests, tool calls, generated artifacts, and answers delivered.
- Keep action history concise; preserve enough detail to show what was already done.

### 3. Durable Information for Future Continuation
- Preserve information from these past rounds that may still help future task completion or accurate recall.
- Keep facts, constraints, state, and evidence that could affect later decisions.
- Do not preserve low-value chronology.

### 4. Decisions, Constraints, Corrections, and Findings
- Record important decisions, assumptions, constraints, user corrections, and discoveries.
- If earlier information was corrected later, preserve the corrected state.
- Mark anything uncertain, rejected, or requiring re-evaluation.

### 5. Repository, Files, Code Areas, and Artifacts
- Record useful codebase understanding from these past rounds.
- Include relevant files, functions, classes, APIs, config keys, docs, examples, generated artifacts, and why they matter.
- Include repository structure or module-boundary knowledge only when it may guide future work.

### 6. Evidence, Errors, Fixes, and Open Items
- Preserve important tool results, command outputs, test results, logs, stack traces, file reads, and search results.
- Record errors, invalid attempts, fixes, and attempts that should not be repeated.
- Include unresolved items only if they remain relevant after the completed historical rounds.

Output only the state snapshot. Do not add commentary about the compression process.
"""


class ForkedDialogueCompressorConfig(BaseModel):
    trigger_context_ratio: float = Field(default=0.8, gt=0.0, lt=1.0)
    custom_compression_prompt: str | None = None
    model: ModelRequestConfig | None = None
    model_client: ModelClientConfig | None = None


@ContextEngine.register_processor()
class ForkedDialogueCompressor(ForkedPrefixCompactProcessor):
    memory_block_open = MEMORY_BLOCK_DIALOGUE_OPEN
    memory_block_close = MEMORY_BLOCK_DIALOGUE_CLOSE
    default_prompt = DEFAULT_DIALOGUE_COMPRESSION_PROMPT
    processor_label = "ForkedDialogueCompressor"

    def __init__(self, config: ForkedDialogueCompressorConfig):
        super().__init__(config)

    @property
    def config(self) -> ForkedDialogueCompressorConfig:
        return self._config

    def _build_span(self, messages: list[BaseMessage]) -> PrefixCompactSpan:
        current_round_start = self._find_last_user_message_index(messages)
        if current_round_start < 0:
            return PrefixCompactSpan([], [], list(messages))
        keep_recent = len(messages) - current_round_start
        effective_keep = adjust_keep_recent_for_tool_boundaries(messages, keep_recent)
        split_index = max(len(messages) - effective_keep, 0)
        return PrefixCompactSpan(
            preserved_prefix=[],
            messages_to_compress=list(messages[:split_index]),
            protected_tail=list(messages[split_index:]),
        )

    @staticmethod
    def _find_last_user_message_index(messages: list[BaseMessage]) -> int:
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], UserMessage):
                return index
        return -1
