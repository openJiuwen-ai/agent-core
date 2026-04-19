---
description: Shared instructions for AI coding assistants in agent-core.
---

# AGENTS.md

Shared instructions for AI coding assistants working in `agent-core`.
Keep this file specific, factual, and cross-tool. Prefer nearby code and
tests over assumptions.

`pyproject.toml` is the canonical source of truth for Python/tooling
settings, and `Makefile` defines the common lint/test entry points.

## What This Repo Is

- `openjiuwen/core/`: public SDK/runtime for agents, workflows, sessions,
  memory, retrieval, security, and system operations.
- `openjiuwen/harness/`: coding-agent framework built on core
  primitives; includes prompts, rails, tools, subagents, task loop, and
  workspace handling.
- `openjiuwen/extensions/`: optional integrations such as storage,
  checkpointers, sandbox providers, and vendor-specific adapters.
- `openjiuwen/agent_evolving/` and `openjiuwen/dev_tools/`: optimization,
  evaluation, and developer tooling.
- `tests/unit_tests/`: fast deterministic coverage used in CI.
- `tests/system_tests/`: higher-level E2E tests; may require real
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

## Core Architecture

- Treat exports from `__init__.py`, documented APIs, examples, and README
  snippets as public API.
- `openjiuwen.core.single_agent` is the current single-agent API.
  `openjiuwen.core.single_agent.legacy` exists for compatibility only.
- Preserve the Card/Config split: cards define identity/metadata
  (`AgentCard`, `ToolCard`, `WorkflowCard`, `SysOperationCard`); runtime
  behavior belongs in configs, agents, managers, or resource instances.
- `Runner.resource_mgr` is shared process-global state. Use stable IDs
  and keep tests isolated.

## Commands

- Setup: `uv sync`
- Install tooling: `make install`
- Run all tests: `make test`
- Run targeted test: `make test TESTFLAGS="path/to/test.py"`
- Lint staged files: `make check`
- Check last N commits: `make check COMMITS=N`
- Type-check: `make type-check`
- Auto-fix: `make fix`

`make check` and `make type-check` operate on staged files by default.
Pass `COMMITS=N` to check recent commits instead.

## More Detail

- Architecture, subsystem maps, change rules: `.claude/rules/architecture.md`
- Code style: `.claude/rules/code-style.md` and `.claude/rules/python/coding-style.md`
- Testing: `.claude/rules/testing.md`
- Security: `.claude/rules/security.md`
- Git workflow: `.claude/rules/git-workflow.md`
- Deep operational guides: `.claude/skills/`
- Permissions and env vars: `.claude/settings.json`
