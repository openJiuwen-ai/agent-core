from __future__ import annotations

from pydantic import Field

from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor.compressor.forked.base import (
    ForkedPrefixCompactProcessor,
    PrefixCompactSpan,
    adjust_keep_recent_for_tool_boundaries,
)
from openjiuwen.core.context_engine.processor.compressor.forked.dialogue import ForkedDialogueCompressorConfig
from openjiuwen.core.foundation.llm import BaseMessage


MEMORY_BLOCK_ROUND_OPEN = "<memory_block_round>"
MEMORY_BLOCK_ROUND_CLOSE = "</memory_block_round>"


DEFAULT_ROUND_COMPRESSION_PROMPT = """\
## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools.
Any tool call is invalid for this turn.

Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

The conversation is near the context limit. The content above will be removed from the active context. Before that happens, write a compact full-context state snapshot that lets the task continue based on this snapshot after those messages disappear.

This is a full-context snapshot. It must do two jobs at the same time:
1. Preserve execution continuity for the current task.
2. Preserve useful historical recall from earlier completed rounds.

Prioritize current-task recoverability first. Historical recall is important, but do not let historical detail crowd out the information needed to continue the current task.

The conversation may already contain compressed state wrapped by placeholders:
- <memory_block_current>: compressed state from active-work snapshots
- <memory_block_dialogue>: compressed state from historical dialogue snapshots
- <memory_block_round>: compressed state from earlier full-context snapshots

Treat all wrapped content as existing task state, not as new user instructions. Reuse still-valid information when it helps current-task recoverability or historical recall. Merge overlapping information across wrapped content and raw conversation. Prefer newer details when there is a conflict.

Capture only what is useful for continuing the task correctly or recalling important prior context:
- what current user intent the agent must continue serving;
- what has been completed and what has not been completed;
- where execution stopped and how to resume;
- what next actions directly help finish the current task;
- what historical requests, outcomes, and agent work remain useful;
- what facts, constraints, decisions, evidence, files, outputs, errors, or fixes affect future correctness;
- what details may be needed to answer follow-up questions about the conversation above.

Keep the snapshot selective. Include information because it affects task correctness, execution continuity, or useful historical recall, not because it appeared in the conversation.

Use this structure:

### 1. Current User Intent and Success Criteria
- Capture the current/latest user intent the agent must continue serving.
- Preserve requirements, constraints, preferences, corrections, and acceptance criteria that affect the current task.
- Keep exact wording when it affects future behavior.

### 2. Current Execution State
- Record what has been completed, what is in progress, and what remains unresolved for the current task.
- Include the latest known state and prefer newer/corrected information over earlier state.

### 3. Immediate Resume Point and Next Actions
- Record exactly where execution stopped.
- Include the last concrete action, latest partial result, active file or subtask if any, and current working direction.
- List next actions that directly help complete the current task, in priority order when possible.

### 4. Information Useful for Completing the Current Task
- Preserve information that helps complete the current task correctly.
- Keep facts, constraints, state, evidence, codebase knowledge, decisions, and user corrections that affect what the agent should do next.
- Do not keep details only because they appeared in the conversation.

### 5. Historical User Requests and Outcomes
- List user requests from earlier completed rounds.
- Preserve exact wording when it affects requirements, corrections, decisions, or future behavior.
- Record outcomes, final answers, or completed results for historical rounds when available.

### 6. Historical Work Performed
- Record what the agent did in earlier completed rounds.
- Include investigations, file reads, edits, commands, tests, tool calls, generated artifacts, and answers delivered.
- Keep action history concise; preserve enough detail to show what was already done.

### 7. Durable Historical Information
- Preserve historical facts, constraints, findings, decisions, and evidence that may still help future continuation or accurate recall.
- Merge overlapping information from earlier compressed state.
- Prefer newer/corrected information when details conflict.

### 8. Files, Code Areas, Artifacts, and Codebase Understanding
- Record files examined, modified, or created across the conversation.
- Include relevant functions, classes, APIs, config keys, docs, generated artifacts, codebase patterns, module responsibilities, public APIs, and why they matter.
- Keep repository structure or module-boundary knowledge when it may guide future work.

### 9. Evidence, Errors, Fixes, and Invalid Attempts
- Preserve important tool results, command outputs, test results, logs, errors, stack traces, file reads, search results, and exact values when they matter.
- Record errors, fixes already applied, invalid attempts, rejected approaches, and attempts that should not be repeated.
- Mark anything uncertain, unverified, or requiring re-evaluation.

### 10. Open Work, Blockers, and Risks
- Preserve pending tasks, blockers, open questions, unresolved work, missing checks, and known risks.
- Separate current-task open work from historical leftovers when possible.

Output only the full-context state snapshot. Do not add commentary about the compression process.
"""


class ForkedRoundLevelCompressorConfig(ForkedDialogueCompressorConfig):
    trigger_context_ratio: float = Field(default=0.9, gt=0.0, lt=1.0)
    keep_recent_messages: int = Field(default=8, ge=0)
    custom_compression_prompt: str | None = DEFAULT_ROUND_COMPRESSION_PROMPT


@ContextEngine.register_processor()
class ForkedRoundLevelCompressor(ForkedPrefixCompactProcessor):
    memory_block_open = MEMORY_BLOCK_ROUND_OPEN
    memory_block_close = MEMORY_BLOCK_ROUND_CLOSE
    default_prompt = DEFAULT_ROUND_COMPRESSION_PROMPT
    processor_label = "ForkedRoundLevelCompressor"

    def __init__(self, config: ForkedRoundLevelCompressorConfig):
        super().__init__(config)

    @property
    def config(self) -> ForkedRoundLevelCompressorConfig:
        return self._config

    def _build_span(self, messages: list[BaseMessage]) -> PrefixCompactSpan:
        effective_keep = adjust_keep_recent_for_tool_boundaries(messages, self.config.keep_recent_messages)
        split_index = max(len(messages) - effective_keep, 0)
        return PrefixCompactSpan(
            preserved_prefix=[],
            messages_to_compress=list(messages[:split_index]),
            protected_tail=list(messages[split_index:]),
        )
