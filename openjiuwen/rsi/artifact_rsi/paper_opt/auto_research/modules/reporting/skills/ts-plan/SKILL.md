---
name: ts-plan
description: Decide the paper's title from the run's evidence. Use once, at the very start of a reporting session, before ts-write.
---

# ts-plan

Read the evidence blocks in the task message (objective, hypothesis, real
results). Write three plain-text files under `{PAPER_WORKSPACE}` (the
full absolute path — see your system prompt; never a bare relative
filename) — no LaTeX markup in any of them, ts-write reads these back
verbatim as plain text and wraps them in LaTeX itself.

1. `{PAPER_WORKSPACE}/title.txt` — a single concise, properly-cased title
   for the whole paper, 8-14 words — not a question, not first person, no
   trailing period, no surrounding quotes — summarizing what was actually
   found (results are already known for this run, so title it like a
   finding, not a proposal).
2. `{PAPER_WORKSPACE}/contributions.txt` — exactly 3 lines, one
   contribution per line, each a concrete, specific technical statement
   about what this run actually did or found (not a generic claim) —
   ts-write renders these verbatim as the introduction's 3-item
   contribution list, so write them as complete, self-contained
   sentences, not fragments.
3. `{PAPER_WORKSPACE}/keywords.txt` — a single line, 4-6 lowercase
   comma-separated index terms (task, method, data/signal type, key
   technique) — no connector words like "and"/"of"/"based".
