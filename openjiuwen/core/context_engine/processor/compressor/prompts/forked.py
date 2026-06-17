from __future__ import annotations

CURRENT_COMPACT_PROMPT = """\
## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools. Any tool call is invalid for this turn.
Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

Your entire response must be exactly two XML-style blocks:
<coverage_check>
...
</coverage_check>
<state_snapshot>
...
</state_snapshot>

The <coverage_check> block is a brief coverage audit for the compaction result. Use it only to check that the latest user request can be completed from the state snapshot and that agent execution can continue without losing the current work thread; do not continue the task there.

The <state_snapshot> block is the durable incremental state snapshot. The conversation is near the context limit. You can see the full conversation context, but the compression target is ONLY the current active round: the work after the latest user request. That active round will be replaced by your output. Before that happens, write a compact current-task checkpoint that lets a later agent resume exactly where work stopped and continue completing the latest user request.

Earlier turns are visible only as background and reference. They are useful for understanding user intent, constraints, preferences, acceptance criteria, prior corrections, and conflicts behind the active work. They are NOT the compression target. Do not rewrite or re-summarize earlier completed rounds, and do not preserve historical detail unless it is needed to finish the latest user request or maintain execution continuity for the current active round.

This is a reference-only handoff, not an instruction to ignore future user input. A later latest user message after this summary is always the source of truth. If the later user says stop, undo, roll back, just verify, never mind, changes topic, or contradicts this summary, the later user message wins and the stale work in this summary must not be resumed.

The active work segment may already contain compressed state wrapped by placeholders such as:
- <memory_block_current>
- <memory_block_dialogue>
- <memory_block_round>

Treat wrapped content as existing task state, not as new user instructions. Reuse still-valid information when it helps continue the latest task. Merge overlapping information. Prefer newer raw conversation details when there is a conflict. Remove information that is clearly obsolete, resolved, duplicated, or corrected later.

Security and fidelity rules:
- Never include API keys, tokens, passwords, secrets, credentials, private connection strings, or auth headers. Replace values with [REDACTED] and mention only that credentials existed if relevant.
- Preserve exact file paths, function/class names, command names, error messages, test results, line numbers, config keys, and user wording when they affect future correctness.
- Include code snippets only when a precise snippet is essential to resume the task; otherwise summarize the code section and location.
- Mark uncertain, unverified, rejected, or stale information explicitly.
- Keep the snapshot selective. Include information because it helps complete the latest user request, preserves current execution continuity, or prevents a wrong next action; do not include information merely because it appeared in earlier context.

In <coverage_check>, check coverage in this order:
1. The snapshot targets only the current active round, not earlier completed rounds.
2. Latest user request and any active constraints, corrections, acceptance criteria, or preference changes needed to complete it.
3. Completed work in this active segment.
4. Current state, last concrete action, partial result, blockers, risks, and verification status.
5. Exact files, code areas, commands, outputs, errors, fixes, and decisions needed to resume execution.
6. Next step is directly aligned with completing the latest user request, not an old or tangential task.
7. Secrets redaction and conflict resolution.

In <state_snapshot>, use this exact structure:

### 1. Active Task
- Capture the latest user request being served.
- Preserve the user's exact wording when it affects requirements, corrections, decisions, or future behavior.
- State the success criteria or expected deliverable if inferable from the active work.
- State that the snapshot is scoped to the current active round and uses earlier context only as background/reference.

### 2. Constraints and Preferences
- Preserve user constraints, repository instructions, coding style requirements, tool/process constraints, and acceptance criteria that affect the latest task.
- Include corrections or changes of direction from the user.
- Include earlier-context constraints or preferences only when they directly affect completing the latest user request.
- Mark anything uncertain or requiring confirmation.

### 3. Completed Work in This Active Segment
- Record what has been completed since the latest user request.
- Include answers delivered, files inspected, edits made, decisions reached, commands run, tests completed, tool calls, and artifacts produced.
- Preserve enough detail so the next agent does not repeat completed work unnecessarily.

### 4. Current Work and Active State
- Describe precisely what was being worked on immediately before compaction.
- Include the active file, function, class, subtask, branch, plan item, process, or generated artifact if any.
- Include the latest known state and prefer newer/corrected information over earlier state.

### 5. Immediate Resume Point
- Record exactly where execution stopped.
- Include the last concrete action, latest partial result, active file or subtask, and current working direction.
- If the last action failed or timed out, include the exact failure and what had been learned before it failed.

### 6. Pending Tasks and Next Useful Step
- List pending tasks explicitly asked for by the user and not yet fulfilled.
- List the next step that directly continues the latest task. Include it only if it is directly supported by the latest user request and current work.
- Do not invent unrelated follow-up work or revive old completed tasks.

### 7. Key Facts, Decisions, Evidence, and Fixes
- Preserve facts, findings, decisions, assumptions, constraints, user corrections, rejected approaches, and items requiring re-evaluation.
- Preserve important tool results, command outputs, test results, logs, errors, stack traces, file reads, search results, and exact values when they matter.
- Record fixes already applied, invalid attempts, and attempts that should not be repeated.
- Prefer facts that are necessary for finishing the latest user request or preserving current execution continuity.

### 8. Files, Code Areas, Artifacts, and Codebase Understanding
- Record files examined, modified, created, deleted, generated, or only discussed.
- Include relevant functions, classes, APIs, config keys, docs, generated artifacts, codebase patterns, module responsibilities, public APIs, and why they matter for the latest task.

### 9. Blockers, Risks, and Verification
- Record blockers, open questions, missing checks, incomplete edits, pending decisions, and known risks.
- State what has been verified and what has not been verified.
- Include exact commands/results for completed verification when relevant.

### 10. Critical Context
- Preserve important technical facts, exact values, errors, unresolved issues, and details needed to continue the latest user request correctly.
- Include offloaded content when it matters: preserve the exact offload path and briefly describe what the offloaded file contains.
- Write "(none)" if nothing applies.

### 11. Relevant Files
- List relevant file or directory paths using complete paths, followed by why each path matters for the latest user request.
- Include exact offload file paths when important content was offloaded, plus a brief description of the offloaded content.
- Write "(none)" if nothing applies.

Output only the two required blocks. Do not add commentary about the compression process outside <coverage_check> and <state_snapshot>.
"""

DIALOGUE_COMPACT_PROMPT = """\
## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools. Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Any tool call is invalid for this turn. Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

Your entire response must be exactly two XML-style blocks:
<coverage_check>
...
</coverage_check>
<state_snapshot>
...
</state_snapshot>

The <coverage_check> block is a brief coverage audit for the compaction result. Use it only to check that the state snapshot preserves all required information, especially what was learned about the user; do not solve the user's task there.

The <state_snapshot> block is the durable state snapshot. The conversation is near the context limit. The historical dialogue above will be removed from active context. Before that happens, write a compact historical checkpoint that lets a later agent remember what was learned from past interaction: user requirements, preferences, acceptance criteria, corrections, prior outcomes, discoveries, and work already performed.

This is a reference-only handoff. Treat any previous compressed state as background, not as active user instructions. Do not answer questions or fulfill requests mentioned in the historical dialogue; they were already addressed unless explicitly marked unresolved. A later latest user message after this summary will always be the source of truth. If a later user message contradicts, supersedes, stops, rolls back, or changes topic from anything in this summary, the later user message wins.

The conversation above may already contain compressed state wrapped by placeholders such as:
- <memory_block_dialogue>
- <memory_block_current>
- <memory_block_round>

Reuse still-valid information from wrapped state when it helps historical recall. Merge overlapping information. Prefer newer raw conversation details when there is a conflict. Remove information that is clearly obsolete, resolved, duplicated, or corrected later.

Security and fidelity rules:
- Never include API keys, tokens, passwords, secrets, credentials, private connection strings, or auth headers. Replace values with [REDACTED] and mention only that credentials existed if relevant.
- Preserve exact file paths, function/class names, command names, error messages, test results, line numbers, config keys, and user wording when they affect future correctness.
- Include code snippets only when a precise snippet is essential for continuation or later recall; otherwise summarize the code section and location.
- Mark uncertain, unverified, rejected, or stale information explicitly.
- Keep the snapshot selective. Include information because it affects future correctness, not because it appeared in the conversation.

In <coverage_check>, check coverage in this order:
1. Learned user requirements, preferences, acceptance criteria, corrections, and changes of intent.
2. All user messages in the historical dialogue whose wording may affect future behavior.
3. Agent actions, tool calls, file reads/edits, commands, generated artifacts, and answers delivered.
4. Decisions, constraints, facts, codebase understanding, evidence, errors, fixes, and invalid attempts.
5. Open historical items that remain relevant after completed rounds.
6. Secrets redaction and conflict resolution.

In <state_snapshot>, use this exact structure:

### 1. Historical User Requests and Outcomes
- List all user messages from the historical dialogue, excluding tool results.
- Preserve exact wording when it affects requirements, corrections, decisions, or future behavior.
- For each completed historical round, record the outcome, final answer, or delivered artifact when available.
- If a request was superseded or canceled later, mark it as superseded/canceled and preserve the corrected state.

### 2. Learned User Requirements, Preferences, and Acceptance Criteria
- Extract what the agent learned about the user from the historical dialogue.
- Preserve explicit requirements, preferred workflows, style preferences, output format preferences, acceptance criteria, review criteria, recurring constraints, and instructions about what to avoid.
- Include user corrections and feedback as learned behavior rules when they may affect future responses.
- Keep exact wording when the wording itself constrains behavior.
- Prefer newer/corrected preferences when they conflict with older ones.

### 3. Historical Work Performed
- Record what the agent did in these past rounds.
- Include investigations, file reads, edits, commands, tests, tool calls, generated artifacts, and answers delivered.
- Keep action history concise; preserve enough detail to show what was already done and avoid duplicate work.

### 4. Key Technical Concepts and Codebase Understanding
- List important technologies, frameworks, APIs, architectural concepts, module boundaries, and repo patterns discovered.
- Include public API/export constraints, config conventions, and test/build entry points when they may guide future work.
- Omit generic knowledge that can be re-derived easily.

### 5. Files, Code Areas, and Artifacts
- Record relevant files examined, modified, or created and why each matters.
- Include functions, classes, methods, config keys, docs, examples, generated assets, or output files that may matter later.
- Note whether each item was read-only, edited, created, deleted, generated, or only discussed.

### 6. Decisions, Constraints, Corrections, and Findings
- Record important decisions and the rationale behind them.
- Preserve user preferences, constraints, acceptance criteria, corrections, and rejected approaches.
- If earlier information was corrected later, keep only the corrected state unless the correction itself matters.

### 7. Evidence, Errors, Fixes, and Invalid Attempts
- Preserve important command outputs, test results, logs, stack traces, search results, file-read findings, and exact values when they matter.
- Record errors, invalid attempts, fixes, workarounds, and attempts that should not be repeated.
- Mark anything uncertain, stale, or requiring re-evaluation.

### 8. Critical Context
- Preserve important technical facts, exact values, errors, unresolved issues, and details that would be costly or risky to lose.
- Include offloaded content when it matters: preserve the exact offload path and briefly describe what the offloaded file contains.
- Write "(none)" if nothing applies.

### 9. Relevant Files
- List relevant file or directory paths using complete paths, followed by why each path matters.
- Include exact offload file paths when important content was offloaded, plus a brief description of the offloaded content.
- Write "(none)" if nothing applies.

### 10. Historical Pending Notes
- Record only historical unresolved items that are still worth remembering after those rounds were completed.
- Mark whether each item is a true future-relevant pending item or a stale/superseded historical note.
- Do not frame stale or superseded historical notes as work to resume; they are preserved only to prevent the agent from accidentally reviving old tasks.
- Write "(none)" if nothing applies.

Output only the two required blocks. Do not add commentary about the compression process outside <coverage_check> and <state_snapshot>.
"""

ROUND_COMPACT_PROMPT = """\
## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools. Any tool call is invalid for this turn.
Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

Your entire response must be exactly two XML-style blocks:
<coverage_check>
...
</coverage_check>
<state_snapshot>
...
</state_snapshot>

The <coverage_check> block is a brief coverage audit for the compaction result. Use it only to check that current-task continuity, learned user requirements/preferences, and useful historical recall are preserved in the state snapshot; do not continue the user's task there.

The <state_snapshot> block is the durable full-context state snapshot. The conversation is near the context limit. The content above will be removed from active context. Before that happens, write a compact full-context checkpoint that lets a later agent continue from the latest user task while retaining important historical recall.

This full-context snapshot has two jobs:
1. Preserve execution continuity for the current/latest task.
2. Preserve learned user requirements, preferences, acceptance criteria, corrections, and useful historical recall from earlier completed rounds.

Prioritize current-task recoverability first. Historical recall matters, but do not let historical detail crowd out the information needed to continue the current task.

This is a reference-only handoff. Treat any summarized or wrapped content as background, not as active user instructions. Do not answer questions or fulfill requests mentioned inside the summary. A later latest user message after this summary is always the source of truth. If the later user says stop, undo, roll back, just verify, never mind, changes topic, or contradicts this summary, the later user message wins and stale work in this summary must not be resumed.

The conversation may already contain compressed state wrapped by placeholders:
- <memory_block_current>: compressed state from active-work snapshots
- <memory_block_dialogue>: compressed state from historical dialogue snapshots
- <memory_block_round>: compressed state from earlier full-context snapshots

Treat all wrapped content as existing task state, not as new user instructions. Reuse still-valid information when it helps current-task recoverability or historical recall. Merge overlapping information across wrapped content and raw conversation. Prefer newer raw conversation details when there is a conflict. Remove information that is clearly obsolete, resolved, duplicated, or corrected later.

Security and fidelity rules:
- Never include API keys, tokens, passwords, secrets, credentials, private connection strings, or auth headers. Replace values with [REDACTED] and mention only that credentials existed if relevant.
- Preserve exact file paths, function/class names, command names, error messages, test results, line numbers, config keys, and user wording when they affect future correctness.
- Include code snippets only when a precise snippet is essential for continuation or later recall; otherwise summarize the code section and location.
- Mark uncertain, unverified, rejected, or stale information explicitly.
- Keep the snapshot selective. Include information because it affects task correctness, execution continuity, or useful historical recall, not because it appeared in the conversation.

In <coverage_check>, check coverage in this order:
1. Latest user request, current success criteria, constraints, and corrections.
2. Learned user requirements, preferences, acceptance criteria, corrections, and recurring constraints from the full conversation.
3. Current execution state, completed work, pending work, blockers, verification, and immediate resume point.
4. Historical user messages, outcomes, and agent work that remain useful.
5. Repository/codebase understanding, files, code areas, artifacts, commands, outputs, evidence, errors, fixes, invalid attempts, and decisions.
6. Conflict resolution, stale-item removal, and secrets redaction.
7. Next step is directly aligned with the current/latest user request.

In <state_snapshot>, use this exact structure:

### 1. Active Task and Success Criteria
- Capture the current/latest user intent the agent must continue serving.
- Preserve requirements, constraints, preferences, corrections, and acceptance criteria that affect the current task.
- Keep exact wording when it affects future behavior.

### 2. Current Execution State
- Record what has been completed, what is in progress, and what remains unresolved for the current task.
- Include the latest known state and prefer newer/corrected information over earlier state.
- Note active plan items, active mode/state, running processes, background tasks, or session state only if visible in the conversation and relevant.

### 3. Immediate Resume Point and Next Useful Step
- Record exactly where execution stopped.
- Include the last concrete action, latest partial result, active file or subtask, and current working direction.
- List the next step that directly helps complete the current task. Include it only if it is directly supported by the latest user request and current work.
- Do not invent unrelated follow-up work or revive old completed tasks.

### 4. Current Task Facts, Decisions, Evidence, and Fixes
- Preserve facts, constraints, state, codebase knowledge, user corrections, decisions, assumptions, rejected approaches, and items requiring re-evaluation that affect the current task.
- Preserve important tool results, command outputs, test results, logs, errors, stack traces, file reads, search results, and exact values when they matter.
- Record fixes already applied, invalid attempts, and attempts that should not be repeated.

### 5. Current Files, Code Areas, and Artifacts
- Record files examined, modified, created, deleted, generated, or only discussed for the current task.
- Include relevant functions, classes, APIs, config keys, docs, generated artifacts, codebase patterns, module responsibilities, public APIs, and why they matter.

### 6. Repository and Codebase Understanding
- Preserve useful understanding of the repository gathered across the conversation.
- Include architecture, subsystem responsibilities, module boundaries, public APIs, exported surfaces, config conventions, test/build/lint entry points, coding patterns, and ownership boundaries when they may guide future work.
- Record important relationships between files, classes, functions, commands, docs, examples, and generated artifacts.
- Prefer concise, durable understanding over raw file listings.
- Mark assumptions, uncertain mappings, and knowledge that should be re-verified.

### 7. Historical User Requests and Outcomes
- List user messages from earlier completed rounds, excluding tool results.
- Preserve exact wording when it affects requirements, corrections, decisions, or future behavior.
- Record outcomes, final answers, completed results, or delivered artifacts for historical rounds when available.
- Mark superseded, canceled, or resolved historical requests clearly.

### 8. Learned User Requirements, Preferences, and Acceptance Criteria
- Extract durable lessons about the user from the full conversation.
- Preserve explicit requirements, preferred workflows, style preferences, output format preferences, acceptance criteria, review criteria, recurring constraints, and instructions about what to avoid.
- Include corrections and feedback as behavior rules when they may affect future responses.
- Keep exact wording when the wording itself constrains behavior.
- Prefer newer/corrected preferences when they conflict with older ones.
- Separate current-task requirements from broader user preferences when possible.

### 9. Historical Work Performed
- Record what the agent did in earlier completed rounds.
- Include investigations, file reads, edits, commands, tests, tool calls, generated artifacts, and answers delivered.
- Keep action history concise; preserve enough detail to show what was already done.

### 10. Durable Historical Information
- Preserve historical facts, constraints, findings, decisions, evidence, codebase understanding, and user preferences that may still help future continuation or accurate recall.
- Merge overlapping information from earlier compressed state.
- Prefer newer/corrected information when details conflict.

### 11. Cross-Cutting Files, Code Areas, and Artifacts
- Record files examined, modified, created, deleted, generated, or only discussed across the whole conversation when they remain relevant.
- Include relevant functions, classes, APIs, config keys, docs, examples, generated artifacts, module boundaries, public APIs, and why they matter.
- Keep repository structure knowledge only when it may guide future work.

### 12. Open Work, Blockers, Risks, and Verification
- Preserve pending tasks, blockers, open questions, unresolved work, missing checks, incomplete edits, pending decisions, and known risks.
- Separate current-task open work from historical leftovers that should not be resumed without a new user request.
- State what has been verified and what has not been verified.

### 13. Critical Context
- Preserve important technical facts, exact values, errors, unresolved issues, and details that would be costly or risky to lose.
- Include both current-task critical context and durable historical critical context when relevant.
- Include offloaded content when it matters: preserve the exact offload path and briefly describe what the offloaded file contains.
- Write "(none)" if nothing applies.

### 14. Relevant Files
- List relevant file or directory paths using complete paths, followed by why each path matters.
- Include current-task files, durable historical files, and cross-cutting repository paths when relevant.
- Include exact offload file paths when important content was offloaded, plus a brief description of the offloaded content.
- Write "(none)" if nothing applies.

Output only the two required blocks. Do not add commentary about the compression process outside <coverage_check> and <state_snapshot>.
"""

__all__ = ["CURRENT_COMPACT_PROMPT", "DIALOGUE_COMPACT_PROMPT", "ROUND_COMPACT_PROMPT"]
