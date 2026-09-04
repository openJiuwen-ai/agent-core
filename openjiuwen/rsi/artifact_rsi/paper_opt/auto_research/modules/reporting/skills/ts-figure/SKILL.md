---
name: ts-figure
description: Plan, generate, integrate, and verify the figures needed to communicate the paper's scientific claims. Use after ts-write, before ts-review.
---

# ts-figure

**Core rule:** Do not generate figures to maximize figure count or display all available data. Figure generation must be driven by the paper's scientific claims and evidence. Generate only figures that materially improve scientific communication and each figure must communicate a distinct, meaningful message.

The figure process has four stages:

1. **Planning** — determine which figures are justified, what each should communicate, what evidence it should show, and where it belongs.
2. **Generation** — create the planned figures using the appropriate rendering technique.
3. **Integration** — place each figure in the relevant section with an accurate caption and textual reference.
4. **Verification** — verify data provenance, rendering success, readability, and basic consistency before promotion.

Planning is mandatory and must happen before figure-generation code is written. Keep planning as an intermediate reasoning step; do not write it to a file or require a separate planning schema.

## 1. Plan the figures

Inspect the available evidence, including the `ts-write` output, `sections/method.tex`, the experimental sections, and `{PAPER_WORKSPACE}/results.json`.

Identify the paper's central scientific claims and determine which claims benefit from visual communication. For each candidate figure, decide:

- the scientific claim or question it answers;
- the minimum evidence required to support that claim;
- which metrics, variants, conditions, examples, or subsets should be shown;
- which available information should be omitted as redundant, secondary, or distracting;
- whether the information is better communicated as a figure, table, or prose;
- the appropriate visual form or chart structure;
- the target section and narrative location.

Select the smallest subset of evidence that makes the intended claim visually clear. Do not automatically visualize every available metric, variant, condition, or result. Prefer a focused figure over a dense dashboard. When several views support the same claim, prefer a coherent multi-panel figure only when combining them improves understanding.

The number of figures is not fixed. A method/architecture overview, main-results figure, ablation figure, scaling or sensitivity figure, efficiency figure, qualitative analysis figure, or other figure should be generated only when it communicates an important claim that is not already conveyed clearly by an existing figure, table, or prose.

Treat the planning decision as binding: figure generation must implement the selected claim, evidence, scope, and visual form rather than opportunistically re-selecting data while coding.

## 2. Part A — Method figure (system/architecture overview)

Generate a method figure when the Method contains a meaningful architecture, workflow, or multi-stage procedure that benefits from visual explanation.

### 2.1 Extract Method vocabulary

```text
python {SKILLS_DIR}/ts-figure/scripts/extract_headings.py {PAPER_WORKSPACE}
```

This prints `{"headings": [...]}` containing the `\subsection{}` titles from `sections/method.tex`, in order.

Use these headings as the vocabulary source for method-figure node labels. Node labels must exactly match extracted headings. Select only headings that represent actual method components; do not turn `Problem Formulation`, `Notation`, or other non-component sections into nodes unless the Method explicitly treats them as components.

If no headings are found, skip the method figure and note this in the final summary; do not invent component names.

Do not shorten or paraphrase node labels to solve visual overflow. Resolve overflow through subtitles, layout, or sizing instead.

### 2.2 Design the figure spec

Write:

```text
{PAPER_WORKSPACE}/method_figure.spec.json
```

using:

```json
{
  "claim": "one sentence, the figure's actual scientific claim",
  "five_second_takeaway": "<12 words>",
  "nodes": [
    {
      "id": "short_id",
      "label": "<exact extracted heading>",
      "subtitle": "<optional, <=80 chars>",
      "tone": "neutral|accent|secondary|warning|success|danger",
      "badge": "<optional, <=20 chars>"
    }
  ],
  "edges": [
    {
      "from": "node_id",
      "to": "node_id",
      "label": "<optional>",
      "style": "solid|dashed"
    }
  ]
}
```

Rules:

* Use 3-6 nodes when a method figure is generated.
* Every `label` must exactly match an extracted heading.
* Represent only the smallest set of method components needed to explain the proposed approach.
* `tone: "accent"` marks the run's actual contribution.
* Use `neutral` for setup, baseline, context, or supporting stages.
* Use other tones only for genuinely distinct semantic roles.
* Edges must represent data or control flow supported by `sections/method.tex`.
* Use `solid` for the main forward flow and `dashed` only for feedback or retry loops.
* A self-loop is allowed for a retry-on-itself stage.
* Do not invent components, relationships, feedback loops, or data flow unsupported by the Method.
* The `claim` must describe the actual visual message of the figure.

### 2.3 Validate the spec

```text
python {SKILLS_DIR}/ts-figure/scripts/check_method_figure_spec.py {PAPER_WORKSPACE} method_figure.spec.json
```

Repair invalid labels or other reported errors and re-run.

Stop after 3 attempts. If validation still fails, skip the method figure and note the failure in the final summary.

### 2.4 Render

Check Draw.io availability:

```text
python {SKILLS_DIR}/ts-figure/scripts/attempt_drawio.py
```

If Draw.io is available, use its build/export scripts to render the figure. If it is unavailable or build/export fails, use the matplotlib renderer:

```text
python {SKILLS_DIR}/ts-figure/scripts/render_method_figure.py {PAPER_WORKSPACE}/method_figure.spec.json {PAPER_WORKSPACE}/figures/method_figure
```

If `exceeds_double_column` is true, shorten subtitles or adjust the spec and re-render once. Do not alter exact node labels. If it still does not fit, ship the figure and note the width issue rather than making text unreadable.

### 2.5 Integrate into Method

```text
python {SKILLS_DIR}/ts-figure/scripts/insert_method_figure.py {PAPER_WORKSPACE} figures/method_figure.pdf "<one-sentence caption>"
```

Let the script determine placement; do not manually edit `sections/method.tex` to insert the figure.

The caption should accurately describe what the figure shows and reflect its `claim`. The script gives the figure the fixed label `fig:method` — reference it with `\ref{fig:method}` somewhere in Method's prose (e.g. "Figure~\ref{fig:method} shows...") so a reader is pointed at it, not just shown it inline.

## 3. Part B — Quantitative and experimental figures

Generate experimental figures only when selected during planning. Possible types include main results, ablation, scaling, sensitivity, efficiency, qualitative, or error-analysis figures.

Do not assume there is only one experimental figure. Each planned figure should have its own implementation and artifact, while keeping stable filenames for existing paper references where applicable.

`{PAPER_WORKSPACE}/results.json` is the source of truth for quantitative values.

`{PAPER_WORKSPACE}/figures/results.pdf` already exists as a trusted host-generated artifact. It may be replaced only through the verified candidate-and-promotion flow below. Never overwrite it directly.

### 3.1 Inspect the results schema

Before writing any plotting code, inspect the actual structure of `results.json`. Never guess dictionary keys or data layout.

For example:

```text
python -c "import json; data = json.load(open('{PAPER_WORKSPACE}/results.json')); print(data.keys())"
```

Inspect further as necessary to understand the relevant metrics, variants, conditions, and result structure.

Use the actual schema and the figure-planning decision to determine which evidence to visualize.

### 3.2 Analyze the selected evidence before coding

For each planned experimental figure, identify the smallest subset of results needed to make its intended claim clear.

Do not plot every available metric, variant, or field. Do not create a generic bar-of-everything chart or a dashboard simply because the data is available.

When several metrics or views tell substantially the same story, prefer the single view that most directly supports the claim. When multiple views are necessary, combine them only when they form one coherent visual argument.

Decide the chart type, layout, axes, grouping, panels, and annotations from the actual evidence rather than from a generic plotting template.

### 3.3 Write the plotting script

For each planned quantitative figure, write the required plotting script under:

```text
{PAPER_WORKSPACE}/figures/
```

For the main results figure, write:

```text
{PAPER_WORKSPACE}/figures/make_results_figure.py
```

The script must:

* read `{PAPER_WORKSPACE}/results.json` at runtime;
* implement the figure design selected during planning;
* visualize only the selected evidence;
* save the main candidate to:
  `{PAPER_WORKSPACE}/figures/results_candidate.pdf`.

Every plotted numerical value must come from `results.json` at runtime. Never hardcode an experimental result, score, percentage, count, metric value, or other result-derived number.

Method names, metric names, category labels, and other non-numeric metadata may be written as literals when appropriate.

For additional planned figures, use descriptive script and candidate filenames and apply the same runtime data-provenance rule.

### 3.4 Verify before promotion

For the main results figure:

```text
python {SKILLS_DIR}/ts-figure/scripts/check_figure_script.py {PAPER_WORKSPACE} figures/make_results_figure.py figures/results_candidate.pdf
```

Require `"passed": true`.

If verification fails, repair the script and retry. Stop after 3 attempts.

If the candidate still fails verification, leave the trusted `figures/results.pdf` unchanged and note the failure in the final summary. Never promote an unverified candidate.

Apply the same principle to additional quantitative figures: do not treat an artifact as final until its data provenance and execution have been verified.

### 3.5 Promote

For the main results figure, only after verification passes:

```text
python {SKILLS_DIR}/ts-figure/scripts/promote_figure.py {PAPER_WORKSPACE} figures/results_candidate.pdf figures/results.pdf
```

Do not overwrite `figures/results.pdf` directly.

The stable `figures/results.pdf` filename means `sections/experiments.tex` does not need to change solely because a new verified version was promoted.

Use the same verified-artifact principle for additional figures.

## 4. Integration and figure-level checks

For every generated figure:

* place it at the planned narrative location;
* use a meaningful caption that matches what the figure actually shows;
* give it a stable label;
* ensure nearby prose references or interprets it when appropriate;
* ensure the figure supports the intended scientific claim;
* remove unnecessary visual clutter;
* ensure labels, axes, legends, and panels are readable;
* ensure the figure does not duplicate an existing figure or table without adding a distinct insight.

Do not add prose solely to justify a figure that was not scientifically necessary.

Before handing the workspace to `ts-review`, confirm that every generated figure has a clear scientific purpose, uses evidence grounded in the run, has been rendered successfully, and has passed its applicable validation or promotion step.

Do not generate redundant figures or add figures merely to increase figure count.
