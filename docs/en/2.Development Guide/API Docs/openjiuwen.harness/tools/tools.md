# openjiuwen.harness.tools

Built-in tools available to `DeepAgent`. Tools are registered via `ToolCard` entries in [`DeepAgentConfig.tools`](../schema/config.md#class-openjiuwenharnessschemadeepagentconfig) or through the `tools` parameter in [`create_deep_agent`](../factory.md#function-openjiuwenharnesscreate_deep_agent).

The tools of the [browser sub-agent](../subagents/browser_agent.md) (Playwright MCP tools, page probes, batch interaction, custom actions) are documented separately in [`browser_tools`](./browser_tools.md).

## Overview

### File System

| Tool | Description |
|---|---|
| `ReadFileTool` | Read the contents of a file. Supports line offset and limit. |
| `WriteFileTool` | Write content to a file, creating or overwriting it. |
| `EditFileTool` | Apply targeted string replacements to a file. |
| `GlobTool` | Find files matching a glob pattern. |
| `ListDirTool` | List directory contents. |
| `GrepTool` | Search file contents using regular expressions (ripgrep). |

### Code Execution

| Tool | Description |
|---|---|
| `BashTool` | Execute a bash command and return its output. |
| `PowerShellTool` | Execute a PowerShell command and return its output. |
| `CodeTool` | Execute a code snippet in a sandboxed environment. |

### Audio

| Tool | Description |
|---|---|
| `AudioTranscriptionTool` | Transcribe an audio file to text. |
| `AudioQuestionAnsweringTool` | Answer a question about the contents of an audio file. |
| `AudioMetadataTool` | Extract metadata (duration, format, sample rate) from an audio file. |

### Vision

| Tool | Description |
|---|---|
| `ImageOCRTool` | Extract text from an image via OCR. |
| `VisualQuestionAnsweringTool` | Answer a question about the contents of an image. |
| `VideoUnderstandingTool` | Analyze and answer questions about video content. |

### Web

| Tool | Description |
|---|---|
| `WebFreeSearchTool` | Perform a free-tier web search and return results. |
| `WebPaidSearchTool` | Perform a paid-tier web search with higher quality results. |
| `WebFetchWebpageTool` | Fetch and extract content from a URL. |

#### Source Selection and Retrieval Quality

`free_search` accepts `provider="auto"` (default), `"bing"`, or `"duckduckgo"`.
An explicit provider must be enabled; it never falls back to another provider or escapes `allowed_domains`.
The automatic order is DuckDuckGo, its reader endpoint for non-challenge failures, then Bing, subject to the source budget.
The retired 360 source cannot be enabled through legacy environment settings or `enabled_engines`; an explicit `provider="360"` returns `invalid_input` without a network request.

After deterministic URL, duplicate, navigation, and domain checks, each nonempty batch of up to 30 candidates
is assessed in one isolated call to the active Agent's `get_llm()` model. The model receives the original task
when available, the search subquery, and bounded titles/snippets/source paths. It does not receive the full
conversation or any tools. Query-specific aliases, intent dictionaries, and keyword relevance rules are not used.
For standalone use, pass an existing `Model` as `WebFreeSearchTool(model=model)`; no client is created implicitly.

The response must classify every supplied candidate ID as `relevant`, `uncertain`, or `rejected`, with a reason.
After successful assessment, relevant leads rank first; up to two uncertain leads may follow, explicitly labeled `Relevance: uncertain`.
An all-provisional response uses `Content status: uncertain`, not `ok`. Missing snippets and search-reported dates
alone are not proof of irrelevance. Assessment is not fact checking and cannot recover results absent from the source.
`FREE_SEARCH_ASSESSMENT_TIMEOUT_SECONDS=15` sets the per-batch deadline (integer, clamped to 1–60 seconds);
assessment also consumes the existing overall retrieval time. There is no evaluator-level retry.
Only the assessment call requests disabled reasoning through the SDK capability mapping, or minimal/low effort
when disabling is unsupported. The active Agent configuration is unchanged. Output is capped at 4096-8192 tokens
depending on batch size; a length-truncated response reports `assessment_output_truncated`, not a valid judgment.
Without a model, or on timeout/invalid output/provider failure, `Assessment status: unavailable` distinguishes
an unevaluated batch from rejection. Structurally valid leads are retained in source order up to `max_results`,
with warnings and without recommended fetch rankings. Compare all leads before choosing a source; do not fetch
every candidate. This does not trigger another market request. Cancellation propagates. Queries, source text, URLs, source counts,
and access-restriction handling are unchanged; rejected candidates are never relabeled as successful results.
Reader summaries are bound to their own result heading and URL; missing summaries never borrow from the next result.
`Content status: ok` identifies usable candidates/text, not verified facts or current prices.

For diagnosis, set `FREE_SEARCH_DEBUG=true` and optionally `FREE_SEARCH_DEBUG_DIR` (default: `.tmp/free_search_debug`
under the working directory). JSON files share a run ID and contain the response status/final URL, a bounded response
excerpt, all parsed candidates before filtering, and per-candidate selection/rejection/demotion reasons.
Assessment status, elapsed milliseconds, and sanitized failure types are recorded separately from relevance.
Selection limits are recorded separately from relevance. Response headers and proxy credentials are not recorded,
but queries and public page excerpts may still be sensitive: keep these files private and do not commit them.
Search bodies are capped at 2 MB; a truncated body reports `truncated`, not `empty`.
Challenge pages report `blocked` even with HTTP 200; do not retry or bypass verification.

Fetch reports blocked/challenge, dynamic-shell, and empty pages separately from usable content, even for HTTP 200.
Challenge detection also checks extracted HTML text and reader content after metadata removal.
PDF and binary responses identified by MIME type or file signature return `unsupported_content`, not raw bytes
reported as extracted text. Use a suitable document tool; this failure does not automatically call the reader.
It preserves HTTP(S) links, table row/column separators, and empty cells as `[missing]`.
Only unblocked public JavaScript shells may use the reader fallback; restricted-domain fetches never use it.
Responses have a 64 KiB per-header-field ceiling, and proxy/reader fallback uses the remaining request timeout.
Authentication, rate limits, and CAPTCHA must not be bypassed. Missing values cannot be inferred from adjacent fields.

For limits shared across model rounds and concurrent tool calls, register the rail on the Agent:

```python
from openjiuwen.harness.rails.web_research_rail import WebResearchBudgetRail

await agent.register_rail(WebResearchBudgetRail())
```

Defaults per invocation: `WEB_RESEARCH_SEARCHES_PER_ENGINE=2`, `WEB_RESEARCH_MAX_ENGINES=2`,
`WEB_RESEARCH_MAX_FETCHES=3`, `WEB_RESEARCH_TIMEOUT_SECONDS=90` (starts on first retrieval).
DuckDuckGo and its reader share one provider counter. Exhaustion returns `Content status: budget_exhausted`
without another network request. Without the rail, separate direct tool invocations do not share counters.
These are retrieval limits, not an authorization boundary, conversation-wide quota, or budget for browser/paid/custom tools.
Stop when evidence is sufficient; fetching two pages is not mandatory. Do not automatically switch to paid search.

### Task Management

| Tool | Description |
|---|---|
| `TodoCreateTool` | Create a new to-do item. |
| `TodoListTool` | List existing to-do items. |
| `TodoModifyTool` | Modify or complete a to-do item. |
| `TaskTool` | Interact with the task plan (view, add, update tasks). |

### Progressive Tool Discovery

| Tool | Description |
|---|---|
| `ToolSearchTool` | Search deferred tools and return their complete schemas. |

### Skills

| Tool | Description |
|---|---|
| `ListSkillTool` | List available learned skills. |
| `SkillTool` | Get the relevant skill file for a skill |

### Human-in-the-Loop

| Tool | Description |
|---|---|
| `AskUserTool` | Ask user questions to gather info, clarify ambiguity, or make decisions (HITL scenario) |

### Session Management

| Tool | Description |
|---|---|
| `SessionsListTool` | List active and past sessions. |
| `SessionsSpawnTool` | Spawn a new sub-agent session. |
| `SessionsCancelTool` | Cancel a running sub-agent session. |
