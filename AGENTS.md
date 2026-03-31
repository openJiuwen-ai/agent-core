# AGENTS.md

Shared instructions for AI coding assistants working in `agent-core`.
Keep this file specific, factual, and cross-tool. Prefer nearby code and
tests over assumptions.

`pyproject.toml` is the canonical source of truth for Python/tooling
settings, and `Makefile` defines the common lint/test entry points.

## What This Repo Is

- `openjiuwen/core/`: public SDK/runtime for agents, workflows, sessions,
  memory, retrieval, security, and system operations.
- `openjiuwen/deepagents/`: coding-agent framework built on core
  primitives; includes prompts, rails, tools, subagents, task loop, and
  workspace handling.
- `openjiuwen/extensions/`: optional integrations such as storage,
  checkpointers, sandbox providers, and vendor-specific adapters.
- `openjiuwen/agent_evolving/` and `openjiuwen/dev_tools/`: optimization,
  evaluation, and developer tooling.
- `tests/unit_tests/`: fast deterministic coverage used in CI.
- `tests/system_tests/`: higher-level tests; some require real model/API
  credentials and are commonly skipped in CI.
- `examples/` and `docs/`: user-facing usage references. Update them when
  public behavior changes.

## Instruction Priority

- Follow system, tool, and user instructions first, then this file, then
  module-local docs.
- Before changing behavior, inspect the touched module, its exported
  surface in `__init__.py`, and nearby tests/examples.
- Prefer small, targeted diffs. Do not refactor unrelated areas
  opportunistically.

## Architecture Rules

- Treat exports from `__init__.py`, documented APIs, examples, and README
  snippets as public API.
- Keep public API changes additive: preserve names and positional
  arguments; prefer keyword-only additions for new options.
- `openjiuwen.core.single_agent` is the current single-agent API.
  `openjiuwen.core.single_agent.legacy` exists for compatibility only. Do
  not build new features on legacy classes unless the task is explicitly
  about compatibility.
- Preserve the Card/Config split: cards define identity and metadata
  (`AgentCard`, `ToolCard`, `WorkflowCard`, `SysOperationCard`); runtime
  behavior belongs in configs, agents, managers, or resource instances.
- Abilities flow through `AbilityManager` and `Runner.resource_mgr`. When
  adding a tool, workflow, agent, or MCP ability, keep card metadata and
  executable registration in sync.
- `Runner.resource_mgr` is shared process-global state. Use stable IDs,
  avoid accidental collisions, and keep tests isolated.
- `deepagents` is cross-cutting: prompts, rails, tools, factory/config,
  workspace, and tests are tightly coupled. Prompt or tool changes usually
  require updates in `tests/unit_tests/deepagents/`.
- `core/sys_operation` and sandbox code are security-sensitive. Preserve
  path scoping, guardrails, interrupt/confirm flows, and cleanup
  semantics.

## Project Map

- `openjiuwen/core/application/`: higher-level application agents.
- `openjiuwen/core/multi_agent/`: group and multi-agent collaboration
  primitives.
- `openjiuwen/core/single_agent/`: `ReActAgent`, rails, interrupts,
  ability management, agent cards, and migration boundary with `legacy/`.
- `openjiuwen/core/workflow/`: workflow engine, cards, components, and
  workflow sessions.
- `openjiuwen/core/foundation/`: LLM models/clients, tool types, stores,
  and shared primitives.
- `openjiuwen/core/runner/`: runtime orchestration and shared resource
  manager.
- `openjiuwen/core/session/`: session state, streaming, and persistence
  hooks.
- `openjiuwen/core/memory/`, `context_engine/`, `retrieval/`, `security/`,
  and `sys_operation/`: supporting subsystems with broad downstream impact.
- `openjiuwen/deepagents/`: `DeepAgent`, subagents, task loop, rails,
  tools, workspace, and prompt assembly.

## Change Rules By Area

### Public API

- Search `tests/`, `examples/`, and `docs/` before editing a constructor,
  config field, exported symbol, or default behavior.
- If you change a public surface, update the nearest example and tests in
  the same change.
- Default to backwards-compatible fallbacks instead of silent breaking
  changes.

### DeepAgents

- Start from `openjiuwen/deepagents/factory.py`,
  `openjiuwen/deepagents/schema/config.py`, and
  `openjiuwen/deepagents/deep_agent.py`.
- If you add or remove prompt variables, tool descriptions, or rails,
  inspect prompt-builder and tool-description tests.
- If you change workspace or subagent behavior, inspect
  `tests/unit_tests/deepagents/test_deep_agent_workspace.py`,
  `tests/unit_tests/deepagents/test_subagent_rail.py`, and related e2e
  coverage.

### System Operation / Sandbox

- Inspect both `local/` and `sandbox/` implementations plus
  provider/registry code.
- Preserve validation around file paths, shell execution, approvals, and
  interrupts.
- Add or update unit tests under `tests/unit_tests/core/sys_operation/`
  and extension sandbox tests when behavior changes.

### Workflow / Single Agent

- Check whether the touched type is re-exported from
  `openjiuwen/core/workflow/__init__.py` or
  `openjiuwen/core/single_agent/__init__.py`.
- Maintain session, streaming, and interrupt behavior; these areas have
  wide downstream impact.

## Commands

- Setup project env: `uv sync`
- Install lint/test tooling if needed: `make install`
- Run all tests: `make test`
- Run a targeted test: `make test TESTFLAGS="tests/unit_tests/deepagents/test_deep_agent.py"`
- Run staged-file checks: `make check`
- Run checks for last N commits: `make check COMMITS=2`
- Type-check staged files: `make type-check`
- Auto-fix staged files: `make fix`

`make check`, `make lint`, `make type-check`, and related targets operate
on staged Python files by default. Stage files first or pass `COMMITS=N`.

## Code Style

- Python 3.11+; Ruff line length is 120.
- Match surrounding module style before introducing new patterns.
- Keep library code async-safe; avoid blocking calls in async paths unless
  the module already does so deliberately.
- Do not use `print()` in library code; use project logging.
- Add type hints for new public APIs and keep docstrings aligned with the
  surrounding module.
- Do not hard-code secrets, tokens, or real endpoints.

## Testing Expectations

- Prefer targeted unit tests that mirror the source path.
- Use mock defaults for credentials in tests, for example
  `os.getenv(..., "mock-api-key")`.
- Match the local test style: this repo uses both `pytest` and
  `unittest.IsolatedAsyncioTestCase`.
- System tests may require real credentials; do not turn unit tests into
  network-dependent tests.
- When behavior changes are user-visible, update docs/examples as well as
  tests.

## AI-Specific Guidance

- Keep shared repo instructions in this file so Codex, Claude Code,
  Cursor, and similar tools see the same architecture rules.
- Keep `CLAUDE.md` thin and Claude-specific.
- If a subsystem needs more detail later, add a nested `AGENTS.md` near
  that subtree instead of bloating this root file.
