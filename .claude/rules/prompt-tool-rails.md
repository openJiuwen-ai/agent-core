---
description: Prompt templates, tool descriptions, rails, subagents, workspace, and LSP integration for DeepAgents.
language: chinese
paths:
  - "openjiuwen/harness/**/*.py"
alwaysApply: false
---

# Prompt, Tool, and Rails Rules

## Prompt Assembly

- All prompt templates live under `openjiuwen/harness/prompts/sections/`.
- `builder.py` is the entry point for prompt composition.
- Workspace identity content is locale-specific, stored under
  `openjiuwen/harness/prompts/workspace_content/{cn,en}/`.
- If you add/remove prompt variables, update the variable tests in
  `tests/unit_tests/harness/prompts/`.

## Tool Descriptions

- Tool descriptions are generated from schema definitions.
- If you change a tool's parameter schema, update tool-description tests.
- See `openjiuwen/harness/prompts/sections/tools/` for per-tool description builders.
- Tool metadata tests: `tests/unit_tests/harness/prompts/test_tool_descriptions.py`.
- Tool input params tests: `tests/unit_tests/harness/prompts/test_tool_input_params.py`.

## Rails (Behavioral Constraints)

- Rails live under `openjiuwen/harness/rails/`.
- Core rails: task planning, subagent, skill use, security, filesystem, context engineering,
  heartbeat, LSP, memory, plan mode.
- When adding a new rail:
  1. Define the rail class inheriting from `BaseRail`.
  2. Register it in the appropriate config.
  3. Add tests mirroring the source path under `tests/unit_tests/harness/rails/`.
  4. If the rail affects tool output or prompt assembly, update related prompt tests.

## Decision Guide: Rails vs Tools vs Prompts

**Responsibility boundary**:
- **Prompt** -> Initialization content: what the model knows at start
- **Tool** -> Capability extension: what the model can do
- **Rail** -> Behavioral constraint: what the model must not do

**When to add a Rail**:
- You need to restrict the model's behavioral boundaries
- You need runtime injection of check/intercept logic
- You need an enforcement shared across tools/prompts

**Scenarios where Rail is NOT appropriate**:
- Providing a new capability -> use Tool
- Providing initial context -> use Prompt Section
- Passing state to the model -> consider Tool or a dedicated Prompt variable

**Rail and Tool intersection**: Rails can intercept before/after Tool execution,
but should not replace the Tool's own functionality.

## Subagents

- Subagent definitions: `openjiuwen/harness/subagents/`.
- Subagent rails: `openjiuwen/harness/rails/subagent_rail.py`.
- If you add a new subagent type, add corresponding tests in
  `tests/unit_tests/harness/test_subagent_rail.py`.

## Workspace Management

- Workspace handling: `openjiuwen/harness/workspace/workspace.py`.
- Tests for workspace behavior: `tests/unit_tests/harness/test_deep_agent_workspace.py`.

## DeepAgent Entry Points

- Start from `openjiuwen/harness/factory.py`, `openjiuwen/harness/schema/config.py`,
  and `openjiuwen/harness/deep_agent.py` for any DeepAgent-level changes.

## LSP Integration

- LSP tool: `openjiuwen/harness/tools/lsp_tool/`.
- LSP server management: `openjiuwen/harness/lsp/core/`.
- LSP rail: `openjiuwen/harness/rails/lsp_rail.py`.
- If you change LSP behavior, inspect tool tests in `tests/unit_tests/harness/tools/test_lsp_tool.py`.
