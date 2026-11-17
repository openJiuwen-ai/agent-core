---
name: ts-review
description: Check every section against deterministic rules, then run a three-angle adversarial review and fix what survives. Use after ts-write, before ts-latex.
---

# ts-review

## Step 1 — deterministic checks (always first, every section)

For each section file under `{PAPER_WORKSPACE}/sections/`, run (both
`{SKILLS_DIR}` and `{PAPER_WORKSPACE}` are absolute paths given in your
system prompt — pass `{PAPER_WORKSPACE}` exactly as shown, do not `cd`
there instead):

```
python {SKILLS_DIR}/ts-review/scripts/lint_check.py <section_id> {PAPER_WORKSPACE}
```

It prints JSON with a `violations` list — empty means the section is
clean. Violations cover: word count out of band, a number that doesn't
match any real result (a hyperparameter/design constant like "a learning
rate of 0.001" is exempt), a banned AI-writing phrase, a citation where
none is allowed, a banned LaTeX command in prose (`\textbf`/`\textit`/
`\documentclass`/`\usepackage`/`\begin{document}`), a numbered heading, a
markdown code fence, or a missing structural element for that section
(the introduction's exactly-3 contribution `\item`s, related work's
exactly-3 `\subsection` themes, method's `\subsection{Notation}`,
experiments' exactly-3 `\subsection`s, discussion's required
`\subsection`).

If violations are returned, rewrite that section to fix every one of them
— trim or expand prose for length, remove or correct any fabricated
number/citation, rephrase banned phrases — without changing what it
claims otherwise. Save the fixed text back to the same file and re-run
the check. Stop after 3 attempts on one section and move on, reporting it
at the end, rather than looping forever.

## Step 2 — adversarial review (only after every section passes Step 1)

Adapted from spark-to-paper-skills' ts-paper-review recipe. Its real
implementation runs three fully isolated reviewers and prefers doing that
via genuinely separate contexts ("Tier 2: subagents") whenever a
task-delegation tool is available — it explicitly says this tier has "the
SAME quality" as true parallel isolation, because each spawned agent
cannot see its peers or any prior round. You have that tool: a
`paper-reviewer` subagent is available via your task tool (see your
system prompt's available-agents list). Use it — do not role-play the
three angles yourself in one context; that is a strictly weaker
approximation of the same algorithm.

Concatenate the full draft (every `{PAPER_WORKSPACE}/sections/*.tex`
file, in document order) into one text blob **in your own context —
never write this blob to a file**; it exists only to paste into each
task-tool call's prompt below, and nothing else needs it on disk. Then
call
your task tool **three times**, once per lens below, delegating to the
`paper-reviewer` subagent. Each call's task prompt must contain: the lens
name and its mandate (verbatim from the list below), and the full draft
blob. Nothing else — no summary of what you already think is wrong, no
mention of the other lenses. Each call is a separate, isolated context by
construction; do not let what one reviewer returns leak into another
call's prompt.

1. **Theory lens** — "Judge whether the Method as described is sound and
   complete on its own terms. Flag any logical gap, unsupported causal
   claim, or claim that overstates what the rest of the draft actually
   establishes."
2. **Empirical lens** — "Judge whether every number, comparison, and
   claimed result in the draft is consistent with the Results
   section/table elsewhere in the same draft. Flag any mismatch between
   what the table shows and what prose elsewhere claims about it,
   including a correctly-real number used to support the wrong
   conclusion."
3. **Applied lens** — "Judge whether a reader who only sees this draft
   would come away with an accurate, non-misleading understanding of what
   was done and found. Flag anything technically true but stated
   misleadingly, or a caveat stated in one section that a later section
   silently drops or contradicts."

Each call returns a JSON array of issues (`quote`, `section`, `severity`,
`summary`, `close_criterion` — see the subagent's own output contract).
Collect all three arrays.

## Step 3 — merge and survival gate

**Merge**: across the three returned arrays, collapse issues that quote
the same sentence or make the same point into one, keeping every distinct
issue. Drop anything without a usable `close_criterion` — not actionable.

**Survival gate**: for each remaining issue, check it against the other
two lenses' mandates: would at least one of the other two lenses, applied
to that same quote, also treat it as a real problem? If yes, it survives.
If only the lens that raised it would defend it, discard it.

## Step 4 — fix and repeat

Fix every surviving issue by editing the specific section it's in — change
only what's needed to satisfy its `close_criterion` (no new numbers, no
new citations, no claim beyond what the evidence already supports). Save
the file, then repeat Steps 2-3 on the updated full draft (three fresh
task-tool calls — do not reuse a prior round's reviewer output). Stop when
one full pass finds zero surviving issues, or after 3 total review passes,
whichever comes first — report any issue still open after 3 passes in
your final summary rather than looping forever.

## Step 5 — citation check (after the review passes)

```
python {SKILLS_DIR}/ts-review/scripts/check_citations.py {PAPER_WORKSPACE}
```

It prints `hallucinated_keys` — real citation keys used across all
sections that don't actually exist in `refs.bib`. If any come back,
find which section(s) use them and either replace with a real key from
`refs.bib` or remove the citation entirely — a fabricated citation is a
hard failure, never leave one in.
