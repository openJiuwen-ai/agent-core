---
name: ts-write
description: Draft every section's LaTeX body from the evidence. Use after ts-plan, before ts-review.
---

# ts-write

Before drafting, read back `{PAPER_WORKSPACE}/title.txt`,
`{PAPER_WORKSPACE}/contributions.txt`, and `{PAPER_WORKSPACE}/keywords.txt`
from ts-plan — the Introduction and Abstract below build on them directly.

Write one file per section under `{PAPER_WORKSPACE}/sections/` (the full
absolute path — never a bare relative `sections/...`), in this fixed
order (id —
title — evidence to use — target word count — citations allowed). These
targets are deliberately close to a real paper's actual length (adapted
from spark-to-paper-skills' own recipe) — hit them with genuine detail
pulled from the evidence, not filler. If you run out of real content
before reaching the minimum, that's a sign the evidence doesn't support a
section this long — say less rather than pad with restatement,
hedge-phrases, or generic transitions, and note in your final summary that
this section's evidence was thin.

1. `introduction.tex` — Introduction — background evidence — 800-1200
   words — yes. End with a lead-in sentence, then `\begin{itemize}` with
   **exactly 3 `\item`s** — one per line of `contributions.txt`, verbatim
   or lightly rephrased to fit as a complete sentence — then
   `\end{itemize}`. No heading before the list.
2. `related_work.tex` — Related Work — background evidence — 400-800
   words — yes. Organize by theme, never chronology: **exactly 3
   `\subsection{...}`** (each a distinct thread of prior work relevant to
   this run), each ending with an explicit one-sentence gap statement
   ("However, none of these address X, which this run's approach does by
   Y."). Follow the 3 subsections with a plain closing paragraph, no
   heading, contrasting the single nearest prior approach.
3. `method.tex` — Method — objective/hypothesis + method evidence —
   2000-3000 words — yes. Structure: `\subsection{Problem Formulation}`
   (one framing paragraph) → `\subsection{Notation}` (only if you use
   mathematical notation — a small table via `\begin{tabular}` mapping
   symbol to meaning; if the evidence has no math, still include this
   subsection with a one-sentence note that the method is described
   procedurally, not symbolically) → one `\subsection{}` per method
   component named in the evidence → a closing subsection stating the
   decision rule / objective the method optimizes for, if the evidence
   names one. If the evidence describes a multi-step procedure, include at
   least one pseudocode block (a `\begin{enumerate}` numbered list of
   steps is fine — do not use `algorithm`/`algorithmic` environments
   unless the evidence itself is already pseudocode-shaped). Include the
   host-rendered evidence exactly as given if a figure/table is provided
   in this block, in the subsection it belongs to.
4. `experiments.tex` — Experiments — results evidence — 1000-1500
   words — yes. **Exactly 3 `\subsection{}`s, in this order**:
   `Implementation Details` (setup/protocol facts from the evidence, one
   paragraph), `Experimental Design` (variants/metrics/baselines and why,
   one paragraph), `Results` (the host-rendered results table and figure
   from the evidence, included exactly as given — never redraw them —
   plus prose grouped by outcome pattern, mentioning every variant and
   every decision metric named in the evidence, by name).
5. `discussion.tex` — Discussion — discussion/reflection evidence —
   900-1400 words — yes. At least one `\subsection{}` (e.g. "Limitations
   and Failure Modes"): ground it in a real caveat from the evidence — a
   metric that didn't move, a variant that underperformed, a boundary
   condition the reflection or plan noted. If the evidence reports no
   failures at all, discuss the conditions under which the result might
   not generalize instead of inventing a failure.
6. `conclusion.tex` — Conclusion — discussion evidence — 200-280 words — no citations
7. `abstract.tex` — Abstract — write this one LAST, from `title.txt` and
   the other six section files you just wrote (read them back), not from a
   primary evidence block — 150-220 words — no citations. **Prose only —
   no `\section{...}` heading, no heading of any kind.** The compile step
   wraps this file's raw content in a proper `\begin{abstract}...\end{abstract}`
   block itself; a heading you add would show up as extra, wrongly-numbered
   text inside that block.

## Hard rules for every section

- Output LaTeX body content only: a `\section{...}` heading followed by
  prose (**except `abstract.tex` — see item 7 above, prose only, no
  heading**). The heading text doesn't need to match anything exactly —
  the compile step replaces it with the canonical title regardless — but
  include one so the section reads naturally while you're writing it. No
  `\documentclass`, `\usepackage`, `\begin{document}`, `\textbf`, or
  `\textit` — none of these are allowed in prose (`\textbf`/`\textit` in
  particular: this house style uses no bold/italic emphasis in running
  text, only in captions). Never put a number at the start of a
  `\section{}`/`\subsection{}` heading (LaTeX numbers headings itself) and
  never use a markdown code fence (`` ``` ``) anywhere.
- Ground every claim in the evidence you were given. No fabricated
  numbers, sources, or claims. This run's results are always real and
  already measured — never hedge with "we expect"/"this should show" for
  anything already reported as measured. A hyperparameter or design
  constant quoted from the evidence (e.g. "a learning rate of 0.001") is
  not a fabricated result and is fine even if it isn't one of the run's
  reported metrics.
- Cite only with `\cite{key}` for keys listed in the evidence's citation
  list. Never invent a key.
- One logical paragraph per physical line — no hand-wrapping mid-sentence.
- Escape LaTeX special characters (`_ % & #`) in prose you write yourself.
- Never quote non-Latin script verbatim (CJK, Cyrillic, Arabic, etc.) even
  if it appears in the evidence — the default fonts can't render it.
  Paraphrase or describe it in English instead.
- Avoid AI-writing tells: "it is worth noting," "plays a crucial/pivotal
  role," "a testament to," "rich tapestry," "delve into," "the realm of,"
  "ever-evolving," "in today's world," "navigating the landscape,"
  "paradigm shift," "game-changing," "in order to." Punctuate an
  appositive or enumeration with a colon or em-dash, never a bare comma —
  "a pipeline: retrieval, planning, execution," not "a pipeline, retrieval,
  planning, execution, which..." (that bare-comma "comma soup" reads as
  fragmented, not connected prose).
- The Experiments section must mention every variant and every decision
  metric named in the evidence, by name.