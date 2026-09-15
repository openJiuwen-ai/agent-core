# OpenJiuwen conventions

OpenJiuwen is the harness underneath everything here — both the auto-research
pipeline itself and every experiment the pipeline designs and runs. There are two
distinct layers that build on it, and they follow different rules.

## Layer 1 — the pipeline (`auto_research/`)

Hand-developed by the maintainer. Built directly on OpenJiuwen — no adapter layer,
since this whole tree is first-party code and there's nothing to decouple from:

- `auto_research/modules/` — pipeline stages, import and call OpenJiuwen directly.
- `auto_research/extensions/` — the pipeline's own reusable toolbox: custom
  `Tool` / `Rail` / `Agent` subclasses registered through OpenJiuwen's public
  extension points. Grows deliberately and is code-reviewed like anything else.
  Each module builds/configures its own OpenJiuwen runtime as needed — there is no
  shared factory.

## Layer 2 — generated experiments (`experiments/<run_id>/`)

Produced at runtime by the `experiment_design` and `code_implementation` modules
for a specific research task, not hand-written. Also builds on OpenJiuwen (and may
import from Layer 1's `extensions/`), but is disposable and scoped to one run.
`experiment_execution` only *runs* this generated code (deterministic subprocess
invocation, no OpenJiuwen involved) — see `docs/code_implementation_design.md`.

## The hard rule

**Only add. Never delete or modify anything already in OpenJiuwen.**

This applies to both layers, but matters most for Layer 2, since that code is
LLM-generated at runtime with less oversight:

- Extend via subclassing / registration — never monkeypatch OpenJiuwen internals,
  never edit files in the installed package, never delete existing OpenJiuwen
  classes/configs/state.
- Generated code writes only inside its own `experiments/<run_id>/` folder — never
  into `auto_research/extensions/` directly (see "promotion" below), and never
  into the OpenJiuwen reference docs directory (treated as read-only).
- Before writing a new Tool/Rail/Agent, check `auto_research/extensions/` for
  something reusable first.
- If a generated experiment produces something worth keeping permanently, that's a
  **manual promotion** step (a human moves/reviews it into `extensions/`) — never
  automatic.

This document is meant to be pulled into the `code_implementation` system prompt
verbatim (it is — see `code_implementation/agent.py::_render_system_prompt`), so
the constraint is enforced at the prompt level and not just the folder-structure
level, since that's the stage actually writing OpenJiuwen code for a run.

## Reference material

- OpenJiuwen's own real `docs/` directory (agent-core-rsi's repo root, via
  `OpenJiuwenReferenceRail`) — used so these agents have grounded, correct
  knowledge of the SDK's actual API instead of relying on parametric guesses.
  No example-code corpus is available (the old vendored
  `assets/openjiuwen/examples/` snapshot was never migrated and has been
  dropped rather than pointed at something unreliable — see
  docs/agent_core_rsi_migration_risks.md).
