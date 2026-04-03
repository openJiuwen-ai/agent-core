---
name: eval_pipeline
description: >
 Full skill evaluation pipeline — the single entry point for comprehensive skill assessment. Orchestrates multiple evaluators in sequence, aggregates their scores, and produces a final weighted verdict. Use when you want to fully evaluate a skill end-to-end: design quality, functional correctness, and (when available) safety. Triggers on: "evaluate this skill", "run full eval on skill", "assess skill quality", "is this skill ready to ship". Do NOT use when you only want design review (use skill_llm_judge directly) or only want to run functional tests (use skill_tester directly).
---
# Eval Pipeline

The single entry point for full skill evaluation. Orchestrates registered
evaluators in sequence, enforces stage gates, and produces a final score.

## Evaluator Registry

Each evaluator has a role: **weighted** (contributes to final score) or
**gate** (must pass threshold before pipeline continues — failure is terminal
regardless of other scores).

| #  | Evaluator              | Role     | Weight / Threshold | Output file                  |
| -- | ---------------------- | -------- | ------------------ | ---------------------------- |
| E1 | `skill_llm_judge`    | Weighted | 40%                | `llm_judge_score.json`     |
| E2 | `skill_tester`       | Weighted | 60%                | `skill_tester_report.json` |
| E3 | `skill_safety_judge` | Gate     | score_pct ≥ 0.80  | `skill_safety_score.json`  |

 **To add a new evaluator** : add one row to this table with its role, weight or
threshold, and output filename. The aggregation formula in Step 4 reads from
this registry — no other changes needed.

 **Weight rule** : weighted evaluator weights must always sum to 100%.
When E3 activates, rebalance E1/E2 weights as needed (suggested: E1 40%, E2 60%, E3 gate).

## Pipeline Flow

```
skill path
    │
    ▼
[E1] skill_llm_judge ──── gate: score ≥ 60 ──── FAIL → stop, report design issues
    │ pass
    ▼
[E2] skill_tester ─────────────── FAIL → stop, report functional issues
    │ pass (all weighted evaluators done)
    ▼
[E3] safety evaluator (future) ── gate: ≥ 0.80 ── FAIL → stop, safety block
    │ pass
    ▼
[Aggregate] compute weighted final score
    │
    ▼
final_score.json + pipeline_report.md
```

Gates run before weighted scoring. A gate failure short-circuits the pipeline —
don't run downstream evaluators or compute a final score. Report *why* the gate
failed so the skill author knows what to fix.

## Execution

### Step 1: Resolve paths

Read the system prompt for `output_dir`. All output paths below are relative to:

```
<output_dir>/evals/skill-tests/<skill-name>/
```

### Step 2: Run E1 — skill_llm_judge

Use sub-agent tool with the following prompt. Pass ONLY what the sub-agent cannot infer itself — the target path and output dir. Do not embed skill content, pre-resolve steps, or add workflow instructions.

```
"You are an LLM judge. Evaluate the skill at `{target_skill_path}`.
  Follow the skill_llm_judge skill at {<skill_llm_judge_path>} to focus on the skill design.
  Output save to `{output_dir}/evals/skill-tests/{skill_name}/`."
```

Expected output: `llm_judge_score.json`

 **Gate check** : if `design_score_pct < 0.60` → stop pipeline.
Save `pipeline_report.md` with status `BLOCKED_AT_E1` and the Critical Issues
from `llm_judge_report.md`. **DO NOT** proceed to E2.

### Step 3: Run E2 — skill_tester

Call sub-agent with the following prompt. Pass ONLY what the sub-agent cannot infer itself — the target path and output dir. Do not embed skill content, pre-resolve steps, or add workflow instructions.

```
"You are an safety evaluator. Evaluate the skill at `{target_skill_path}`.
  Follow the skill_safety_judge skill at {<skill_safety_judge_path>}.
  Output save to `{output_dir}/evals/skill-tests/{skill_name}/`."
```

Expected output: `skill_tester_report.json`

No hard gate on pass_rate — low pass_rate is penalised in the weighted score,
but the pipeline completes so the author gets a full picture.

### Step 4: Run safety gate evaluators

Call sub-agent with the following prompt.

```
"You are a safety evaluator. Evaluate the skill at  `{target_skill_path}`. Follow the skill_safety_judge skill at {<skill_safety_judge_path>}. Save all output to `{output_dir}/evals/skill-tests/{skill_name}/`."
```

1. Run it and collect its `score_pct`
2. If `score_pct < threshold` → stop, save `pipeline_report.md` with
   status `BLOCKED_AT_<evaluator>`, do not compute final score

### Step 5: Aggregate

Compute weighted final score from all weighted evaluators:

```
final_score_pct = Σ (evaluator.score_pct × evaluator.weight)

Current formula:
final_score_pct = (design_score_pct × 0.40) + (pass_rate × 0.60)
```

Determine grade:

| Grade | final_score_pct | Meaning                             |
| ----- | --------------- | ----------------------------------- |
| A     | ≥ 0.90         | Ship-ready                          |
| B     | 0.80–0.89      | Minor issues — fix before shipping |
| C     | 0.70–0.79      | Functional but needs work           |
| D     | 0.60–0.69      | Significant issues — do not ship   |
| F     | < 0.60          | Fundamental problems                |

### Step 6: Write outputs

In the output folder, save `final_score.json`:

```json
{
  "skill_name": "<name>",
  "pipeline_status": "COMPLETED",
  "final_score_pct": 0.81,
  "grade": "B",
  "evaluators": {
    "e1_llm_judge":   { "score_pct": 0.74, "weight": 0.40, "contribution": 0.296 },
    "e2_functional":  { "score_pct": 0.87, "weight": 0.60, "contribution": 0.522 }
  },
  "gates_passed": [],
  "recommendation": "Good functional quality. Design issues in D2 and D4 should be fixed before shipping."
}
```

Save `final_report.md`:

```markdown
# Eval Pipeline Report: <skill-name>

## Final Verdict
- **Score**: 0.81 (B)
- **Status**: COMPLETED
- **Ship-ready**: No — fix before shipping

## Evaluator Breakdown
| Evaluator | Score | Weight | Contribution |
|-----------|-------|--------|--------------|
| E1 Design (skill_llm_judge) | 74% | 40% | 0.296 |
| E2 Functional (skill_tester) | 87% | 60% | 0.522 |

## Key Issues to Fix
[Pull Critical Issues from each evaluator's report]

## Full Reports
- Design: `llm_judge_report.md`
- Functional: `report.json`
```

---

## Output Structure

```
evals/skill-tests/<skill-name>/
├── llm_judge_report.md          # E1 detailed report
├── llm_judge_score.json         # E1 machine-readable score
├── skill_tester_cases.json                   # E2 test case definitions
├── run-1/ … run-N/              # E2 per-test outputs
├── skill_tester_report.json                  # E2 consolidated functional results
├── safety_score.json            # E3 output (future)
├── final_score.json             # Pipeline aggregated score
└── final_report.md           # Human-readable final verdict
```

---

## NEVER Do

* **NEVER** compute a final score if any gate evaluator failed — a blocked pipeline has no valid final score
* **NEVER** skip E1 to go straight to E2 — poor design produces noisy functional test results
* **NEVER** hardcode the aggregation formula in a way that requires code changes to add a new evaluator — the registry table is the single source of truth
* **NEVER** let a high functional score mask a low design score — report both, the author needs the full picture
* **NEVER** rebalance weights silently — if weights change, note it in `pipeline_report.md`
