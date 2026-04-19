---
description: Public API, Card/Config split, core subsystems, and module organization for agent-core.
language: chinese
paths:
  - "openjiuwen/**/*.py"
---

# Architecture Rules

## Public API

- Treat exports from `__init__.py`, documented APIs, examples, and README snippets as public API.
- Keep public API changes additive: preserve names and positional arguments.
- Prefer keyword-only additions for new optional parameters.
- Default to backwards-compatible fallbacks instead of silent breaking changes.
- Before changing behavior, inspect the touched module, its exported surface in `__init__.py`, and nearby tests/examples.

## Card / Config Split

**Design intent**: Cards are static metadata, serializable and transportable;
Configs are runtime objects holding resources and state. This separation
enables Cards to cross process boundaries (e.g., A2A protocol) while
Configs remain process-local singletons.

**When to add a new Card type**:
- Metadata needs to cross process/service boundaries
- Metadata needs to be persisted or stored
- Capabilities need to be discovered and registered at runtime

**Anti-patterns**:
- Putting `session_id`, `runner`, or other runtime data in a Card
- Defining static description fields in a Config (should be in a Card)
- Injecting dynamic computation into a Card's `to_dict()` method

## Core Subsystems

### Single Agent

- `openjiuwen.core.single_agent` is the current single-agent API.
- `openjiuwen.core.single_agent.legacy` exists for compatibility only.
  Do not build new features on legacy classes unless the task is explicitly about compatibility.
- Maintain session, streaming, and interrupt behavior; these areas have wide downstream impact.
- Check whether the touched type is re-exported from `openjiuwen/core/workflow/__init__.py`
  or `openjiuwen/core/single_agent/__init__.py`.

### DeepAgents (harness)

- Start from `openjiuwen/harness/factory.py`, `openjiuwen/harness/schema/config.py`,
  and `openjiuwen/harness/deep_agent.py`.
- Prompts, rails, tools, factory/config, workspace, and tests are tightly coupled.
  Prompt or tool changes usually require updates in `tests/unit_tests/deepagents/`.
- If you add/remove prompt variables, tool descriptions, or rails, inspect prompt-builder
  and tool-description tests.
- If you change workspace or subagent behavior, inspect
  `tests/unit_tests/harness/test_deep_agent_workspace.py`,
  `tests/unit_tests/harness/test_subagent_rail.py`, and related coverage.

### Abilities (tools, workflows, agents, MCP)

- Abilities flow through `AbilityManager` and `Runner.resource_mgr`.
- When adding a tool, workflow, agent, or MCP ability, keep card metadata
  and executable registration in sync.

### Resource Manager

- `Runner.resource_mgr` is shared process-global state.
- Use stable IDs, avoid accidental collisions, and keep tests isolated.

### System Operation / Sandbox

- Inspect both `local/` and `sandbox/` implementations plus provider/registry code.
- Preserve validation around file paths, shell execution, approvals, and interrupts.
- `core/sys_operation` and sandbox code are security-sensitive.
- Add or update unit tests under `tests/unit_tests/core/sys_operation/`
  and extension sandbox tests when behavior changes.

### Workflow

- Workflow engine lives in `openjiuwen/core/workflow/`.
- Components are under `openjiuwen/core/workflow/components/`:
  - `flow/`: start, end, branch, loop
  - `llm/`: LLM call, ReAct, questioner, intent detection
  - `tool/`: tool call, HTTP request
  - `resource/`: memory write/retrieval, knowledge retrieval
  - `condition/`: expression, number, array conditions

## Code Style

- Python 3.11+; Ruff line length is 120.
- Keep library code async-safe; avoid blocking calls in async paths unless the module already does so deliberately.
- Do not use `print()` in library code; use project logging.
- Add type hints for new public APIs; keep docstrings aligned with the surrounding module.
- Do not hard-code secrets, tokens, or real endpoints.

## Testing Expectations

- Prefer targeted unit tests that mirror the source path. Example:
  `openjiuwen/harness/deep_agent.py` -> `tests/unit_tests/harness/test_deep_agent.py`
- Use mock defaults for credentials in tests: `os.getenv(..., "mock-api-key")`.
- This repo uses both `pytest` and `unittest.IsolatedAsyncioTestCase`.
- System tests may require real credentials; do not turn unit tests into network-dependent tests.
- When behavior changes are user-visible, update `docs/` and `examples/` alongside tests.

## Module-Local AGENTS.md

- If a subsystem needs more detail, add a nested `AGENTS.md` near that subtree
  instead of bloating this root file.
- Detailed rules also live in `.claude/rules/` for topic-scoped guidance.
- The `AI-Specific Guidance` section in the root `AGENTS.md` explains this approach.
