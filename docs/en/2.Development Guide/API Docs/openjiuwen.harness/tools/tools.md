# openjiuwen.harness.tools

Built-in tools available to `DeepAgent`. Tools are registered via `ToolCard` entries in [`DeepAgentConfig.tools`](../schema/config.md#class-openjiuwenharnessschemadeepagentconfig) or through the `tools` parameter in [`create_deep_agent`](../factory.md#function-openjiuwenharnesscreate_deep_agent).

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

### Task Management

| Tool | Description |
|---|---|
| `TodoCreateTool` | Create a new to-do item. |
| `TodoListTool` | List existing to-do items. |
| `TodoModifyTool` | Modify or complete a to-do item. |
| `TaskTool` | Delegate a task to a subagent. |

Task-loop subagents use an isolated Session. If a subagent returns an error,
`TaskTool` returns `ToolOutput(success=False, error=...)` and preserves any existing
`output`. A round timeout returns `success=False, error="completion_timeout"`
after waiting for that round's execution task to be cancelled.
For single-round subagents, `TaskTool` also applies the child's `completion_timeout`
to the entire invocation, including model/tool calls and permission waits, and waits
for cancellation cleanup before returning `completion_timeout`. Caller cancellation
and errors originating inside tools retain their existing semantics. This deadline
applies to each invocation; it does not accumulate across separately resumed calls.
This ends the subagent invocation; the parent agent can continue handling the tool result.

### Progressive Tool Discovery

| Tool | Description |
|---|---|
| `SearchToolsTool` | Search for available tools by keyword. |
| `LoadToolsTool` | Load additional tools into the active tool set. |

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
