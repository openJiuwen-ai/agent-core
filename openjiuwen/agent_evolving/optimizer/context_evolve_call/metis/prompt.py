# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Metis reflector prompt skeletons (single-turn, environment-neutral).

Covers the three tip categories, quality gates, analysis steps, and the output
schema. Tools are plain Python helpers described by signature + docstring.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .schema import BaseTip, CodeTool, TipCategory

_CATEGORY_LABEL = {
    TipCategory.ENVIRONMENT: "ENVIRONMENT",
    TipCategory.EXECUTION_PLAN: "EXECUTION_PLAN",
    TipCategory.EXECUTION_PITFALL: "EXECUTION_PITFALL",
}
_CATEGORY_ORDER = (
    TipCategory.ENVIRONMENT,
    TipCategory.EXECUTION_PLAN,
    TipCategory.EXECUTION_PITFALL,
)


def format_existing_tips(tips: List[BaseTip], *, exclude_ids: Optional[List[str]] = None) -> str:
    """Render live tips by category for the reflector."""
    exclude = set(exclude_ids or [])
    live = [t for t in tips if not t.is_invalidated and t.id not in exclude]
    lines = ["## Existing Knowledge Library"]
    if not live:
        lines.append("(empty)")
        return "\n".join(lines) + "\n"
    for category in _CATEGORY_ORDER:
        category_tips = [t for t in live if t.category == category]
        if not category_tips:
            continue
        lines.append(f"\n### {_CATEGORY_LABEL[category]}")
        lines.extend(f"- [{tip.id}] {tip.content}" for tip in category_tips)
    return "\n".join(lines) + "\n"


def format_existing_tools(tools: List[CodeTool]) -> str:
    """Render distilled tools by function name and one-line docstring."""
    if not tools:
        return "## Existing Distilled Tools\n(none)\n"
    lines = ["## Existing Distilled Tools"]
    for tool in tools:
        head = tool.docstring.strip().splitlines()[0] if tool.docstring.strip() else ""
        lines.append(f"- `{tool.function_name}`" + (f": {head}" if head else ""))
    return "\n".join(lines) + "\n"


def format_recent_queries(queries: List[str]) -> str:
    """Render the recent task-query FIFO."""
    if not queries:
        return ""
    return "## Recent Task Queries\n" + "\n".join(f"- {query}" for query in queries) + "\n"


def format_recently_codified(pairs: Sequence[Tuple[BaseTip, Sequence[CodeTool]]]) -> str:
    """Render tools codified earlier in the current evolution pass."""
    live = [(plan, tools) for plan, tools in pairs if tools]
    if not live:
        return ""
    lines = ["## Recently Codified Tools"]
    for plan, tools in live:
        names = ", ".join(f"`{tool.function_name}`" for tool in tools)
        lines.append(f"- plan [{plan.id}] -> {names}")
    return "\n".join(lines) + "\n"


TEXT_REFLECTOR_PROMPT = """\
You analyze an agent's execution trajectory on a task and extract reusable \
knowledge entries that will be injected into the agent's prompt for future tasks.

Extract 1-3 high-quality reusable entries when the trajectory contains real \
reusable signal (efficient sub-procedures, avoidable mistakes, API/tool behavior, \
hidden constraints, reasoning patterns). Return an empty list [] when nothing \
passes the quality bar — a vague or generic entry wastes prompt space and can \
mislead a future agent. You get ONE turn: reason, then emit the final JSON array.
{executor_context_section}
## Knowledge Entry Categories (each entry is exactly one)

1. ENVIRONMENT — a fact about the system, a tool/API's behavior, or a runtime \
constraint that holds regardless of the task.
   GOOD: "Paginated listing calls default to a small page size; always read the \
actual default before writing a pagination loop."
   BAD: "Be careful with pagination." (vague, no mechanism)

2. EXECUTION_PLAN — a procedure template for a reusable sub-routine that lets a \
future agent skip exploration or avoid a wrong path. Prefer a coherent workflow \
with clear numbered substeps; keep it general and flexible (do not hardcode \
whole-task scripts or termination policy). Begin with a "When ..." clause naming \
the sub-goal it applies to.
   GOOD: "When asked for 'the most recent' item from a filtered set: (1) push the \
filter into the retrieval call, (2) sort by date descending, (3) take the first. \
Fetching everything and scanning risks missing the target when results paginate."
   BAD: "Plan your steps carefully." (generic, no behavioral change){dependent_tools_clause}

3. EXECUTION_PITFALL — a generalizable warning from a failure or inefficiency. \
Name the TRIGGER (when it applies), the MISTAKE (what goes wrong), and the \
CONSEQUENCE. Must apply to a class of tasks, not just this instance.
   GOOD: "When searching a paginated source using only the first page, the target \
may be absent; loop all pages — stopping early risks missing it entirely."
   BAD: "Be careful with API calls." (vague)

{existing_tips_section}
{existing_tools_section}
{recent_queries_section}
{recently_codified_hint}

## Trajectory
Task: {query}
Outcome: {outcome}

{trajectory}

## Analysis (write this out before the JSON)
1. READING: take the Outcome as authoritative; locate the CAUSE. List the query's \
constraints/subgoals and map each to the step(s) that handled it. On failure, \
pinpoint the first diverging step and the concrete reason.
2. CANDIDATES: propose 1-3 distinct reusable candidates (a correct subroutine, a \
discovered behavior, an avoidable failed call, a hidden constraint, an inefficient \
detour). Bias to EXECUTION_PLAN for reusable subroutines, EXECUTION_PITFALL for \
tempting mistakes. Reject any plan whose correctness or scope is uncertain.
3. UPDATES: if a candidate only refines/extends an existing entry above (a \
near-twin adding a new scope), do NOT create a duplicate — emit an `update` that \
invalidates the target(s) and replaces them with one merged entry.
4. QUALITY GATES (drop any candidate that fails): generalizes beyond this \
instance; not redundant with an existing entry; concrete trigger + named \
mechanism anchored to the trajectory; correctly scoped (do not overstate); \
changes a future agent's behavior.

## Output Format
A single JSON array. Every entry needs an `action` discriminator (`create` or \
`update`); malformed `action` is rejected. We take the LAST ```json block.

```json
[
  {{
    "action": "create",
    "id": "short_snake_case_name",
    "label": "ENVIRONMENT | EXECUTION_PLAN | EXECUTION_PITFALL",
    "content": "When [condition]... <actionable advice, 1-4 sentences>",
    "source": "success | failure | inefficiency",
    "dependent_tools": ["existing_tool_function_name", ...]
  }},
  {{
    "action": "update",
    "target_ids": ["existing_tip_id_1", "existing_tip_id_2"],
    "id": "new_snake_case_name",
    "content": "<new merged tip body>",
    "source": "success | failure | inefficiency",
    "dependent_tools": ["existing_tool_function_name", ...]
  }}
]
```
If nothing is worth generating, return ```json\n[]\n```.

`dependent_tools` is OPTIONAL and EXECUTION_PLAN-only (omit when no existing tool \
applies; ignored on ENV/PITFALL). For `update`, all `target_ids` must resolve to \
existing tips of the SAME category — the new tip inherits that category.
"""

_DEPENDENT_TOOLS_CLAUSE = """
   Dependent tools: if a plan's procedure would call any existing distilled tool \
listed below, add its exact function name(s) to the plan's optional \
`dependent_tools` field so the tool is surfaced alongside the plan. Only list a \
tool the plan actually calls; omit the field when none applies."""


def _executor_context_section(executor_context: str) -> str:
    """Render optional environment knowledge already available to the executor.

    Return an empty string when no executor context was supplied.
    """
    if not executor_context.strip():
        return ""
    return f"\n## Executor Environment\n{executor_context.strip()}\n"


def build_text_reflect_prompt(
    *,
    query: str,
    trajectory: str,
    existing_tips: List[BaseTip],
    existing_tools: List[CodeTool],
    recent_queries: List[str],
    recently_codified: Optional[Sequence[Tuple[BaseTip, Sequence[CodeTool]]]] = None,
    outcome: str = "Unknown",
    executor_context: str = "",
) -> str:
    """Fill the text-reflector skeleton with the rendered knowledge sections."""
    code_enabled = bool(existing_tools) or bool(recently_codified)
    return TEXT_REFLECTOR_PROMPT.format(
        executor_context_section=_executor_context_section(executor_context),
        dependent_tools_clause=_DEPENDENT_TOOLS_CLAUSE if code_enabled else "",
        existing_tips_section=format_existing_tips(existing_tips),
        existing_tools_section=format_existing_tools(existing_tools) if code_enabled else "",
        recent_queries_section=format_recent_queries(recent_queries),
        recently_codified_hint=format_recently_codified(recently_codified or []),
        query=query,
        outcome=outcome,
        trajectory=trajectory,
    )


CODE_REFLECTOR_PLAN_ONLY_PROMPT = """\
You distill reusable Python helper tools that accelerate an agent's future work.

You are shown a Plan that has already recurred across several distinct tasks (a \
shared pattern), the task queries that applied it, and the existing tool library. \
Identify high-reuse routines shared across the queries that are worth being \
callable helper functions. Returning [] is fully acceptable and often correct if \
the existing library already suffices. You get ONE turn: reason, then emit the \
final JSON array.
{executor_context_section}
## Quality bar (a tool earns its place only if it meets ALL)
1. Generality across the class: name at least two structurally different queries \
that would call it. A tool fitting only one query is an instance solution, not a tool.
2. Fully parameterized: every dimension the underlying operation can vary is a \
parameter (ids, filters, formats, keys, counts, tokens ...). Default to MORE \
parameters; silence in the queries is not evidence a dimension is fixed. If you \
must hardcode a value, say so in the docstring.
3. Real work, not a rename: the body adds logic beyond a single call (lookup, \
multi-step composition, transformation, error normalization, a calculation). Pure \
pass-through is discarded.
4. Modularity: prefer atomic helpers that each cover a partial, reusable step over \
one function that solves a whole query end-to-end. Treat tools as helpers, not solvers.

GOOD: a helper that resolves an entity by name, handles collisions, and returns \
its id (the search -> resolve -> fetch chain is brittle done step-by-step).
BAD: a function that solves one visible query end-to-end or covers the entire plan.

## Implementation rules (violations crash the tool factory)
1. Required parameters omit `default`; optional parameters MUST include `default` \
with a concrete JSON value (null, 0, "", false ...). Docstring and schema must agree.
2. NEVER use `**kwargs` in the body — the signature is built from `parameters`. \
Forward optional args explicitly and assemble a local dict if needed.
3. Reflect on edge cases and RAISE on each (empty result, missing key, unexpected \
value, failure payload) — never return None/[] on a real error; that looks like \
success to the agent. The exception message should name what was attempted.
4. Docstring is the agent's only surface when deciding to call — include: one-line \
summary; a WHEN clause (trigger / query shapes); Returns; behavior on errors/edge \
cases; per-parameter meaning (valid values, what default/null means). Keep it \
tight; do not overstate scope — if a tool is narrow, add a "NOT FOR" note.

## Output schema
A single JSON array (we take the LAST ```json block). Each entry:
```json
[
  {{
    "function_name": "verb_noun (e.g. resolve_contact, fetch_all_pages)",
    "docstring": "One-line summary.\\n\\nWhen to use: ...\\nReturns: ...\\nEdge cases: raises X when Y.",
    "parameters": {{
      "required_input": {{"type": "str", "description": "..."}},
      "optional_filter": {{"type": "str", "description": "keyword filter; null = no filter", "default": null}}
    }},
    "return_annotation": "str | int | list | dict | Any",
    "implementation": "Function BODY only (no def line); a plain Python function body."
  }}
]
```
Return ```json\\n[]\\n``` if no candidate passes the bar.

{existing_tools_section}

## The Plan
{plan_content}

## Candidate queries (triggered this codification)
{candidate_queries_section}

## Related queries (same plan, broader scope)
{related_queries_section}

Now reason briefly, then emit the final JSON array.
"""


def _bullet_queries(queries: List[str]) -> str:
    return "\n".join(f"- {q}" for q in queries) if queries else "(none)"


def build_code_reflect_prompt(
    *,
    plan_content: str,
    candidate_queries: List[str],
    related_queries: List[str],
    existing_tools: List[CodeTool],
    executor_context: str = "",
) -> str:
    """Fill the plan-only code-reflector skeleton (single-turn, no sandbox)."""
    return CODE_REFLECTOR_PLAN_ONLY_PROMPT.format(
        executor_context_section=_executor_context_section(executor_context),
        existing_tools_section=format_existing_tools(existing_tools),
        plan_content=plan_content,
        candidate_queries_section=_bullet_queries(candidate_queries),
        related_queries_section=_bullet_queries(related_queries),
    )


__all__ = [
    "TEXT_REFLECTOR_PROMPT",
    "build_text_reflect_prompt",
    "CODE_REFLECTOR_PLAN_ONLY_PROMPT",
    "build_code_reflect_prompt",
    "format_existing_tips",
    "format_existing_tools",
    "format_recent_queries",
    "format_recently_codified",
]
