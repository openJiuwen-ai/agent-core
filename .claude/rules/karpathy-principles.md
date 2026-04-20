---
description: LLM coding behavior guidelines: avoid assumptions, prefer simplicity, make surgical changes, and drive by verifiable goals.
language: chinese
paths: []
alwaysApply: true
---

# Karpathy-Inspired Coding Principles

Source: [forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills),
derived from Andrej Karpathy's observations on LLM coding pitfalls.

These principles complement the technical rules in `AGENTS.md` and `.claude/rules/`.
They govern how the agent thinks and acts, not what the code does.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State assumptions explicitly. If uncertain, ask rather than guess.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

In agent-core context: The codebase has complex abstractions (Card/Config split,
`Runner.resource_mgr`, sandbox/real op duality). Do not silently assume how
they interact. Ask when behavior is ambiguous.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If 200 lines could be 50, rewrite it.

In agent-core context: When adding a new tool or rail, prefer a minimal
implementation first. Complex features can be added incrementally, but
overengineered abstractions are hard to remove later. "Would a senior
engineer say this is overcomplicated?" — if yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

In agent-core context: The `deepagents` subsystem (prompts, rails, tools,
workspace, tests) is tightly coupled. Changes to one component often
require updates elsewhere — but stay focused. Every changed line should
trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

| Instead of... | Transform to... |
|--------------|----------------|
| "Add validation" | "Write tests for invalid inputs, then make them pass" |
| "Fix the bug" | "Write a test that reproduces it, then make it pass" |
| "Refactor X" | "Ensure tests pass before and after" |

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let the agent loop independently. Weak criteria
("make it work") require constant clarification.

In agent-core context: Use `make test TESTFLAGS="..."` to verify targeted
changes. Use `make check COMMITS=N` to verify style before committing.
Return the verification result to the user.

---

**These principles are working if:** fewer unnecessary changes in diffs,
fewer rewrites due to overcomplication, and clarifying questions come
before implementation rather than after mistakes.
