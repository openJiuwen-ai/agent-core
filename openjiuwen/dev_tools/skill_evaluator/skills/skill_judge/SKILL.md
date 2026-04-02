---
name: skill_llm_judge
description: >
 Audit Agent Skill design quality through static analysis of SKILL.md — no prompt execution, no code running. Scores 7 design dimensions (100pts): knowledge ratio, expert knowledge craft, specification compliance, progressive disclosure, pattern + freedom fit, predicted usability, and output specification. Use when reviewing a skill's DESIGN before or after functional testing. Outputs structured design_score.json for eval pipeline consumption. Do NOT use when you want to run the skill on real prompts or measure actual output quality — use skill_tester for that.
---
# Skill LLM Judge

Evaluate Agent Skills against official specifications and patterns.

## Scope

This skill performs **static design analysis** — it reads the SKILL.md and scores it against design principles. It does NOT:

* Run the skill on test prompts
* Execute any scripts or code
* Measure functional output quality

D6 (Practical Usability) assesses *predicted* usability from design signals only. Empirical usability — whether the skill actually produces good outputs on real prompts — is out of scope. Use `skill_tester` after design review passes.

## Core Philosophy

> **Good Skill = Expert-only Knowledge − What LLM Already Knows**

A Skill's value is its **knowledge delta** — the gap between what it provides and what the model already knows. When a Skill explains "what is PDF" or "how to write a for-loop", it wastes tokens on knowledge LLM already has.

### Three Types of Knowledge

| Type                 | Definition                      | Treatment                              |
| -------------------- | ------------------------------- | -------------------------------------- |
| **Expert**     | LLM genuinely doesn't know this | Must keep — this is the Skill's value |
| **Activation** | LLM knows but may not think of  | Keep if brief — serves as reminder    |
| **Redundant**  | LLM definitely knows this       | Delete — wastes tokens                |

## Evaluation Dimensions (100 points total)

### D1: Knowledge Delta (10 points)

Does the Skill add genuine expert knowledge?

Read through the full SKILL.md body, then score:

| Score | Criteria profile                                                                             |
| ----- | -------------------------------------------------------------------------------------------- |
| 0-2   | Poor - Mostly tutorials or basics (what is X, how to write code, standard library tutorials) |
| 3-5   | Mediocre — some expert knowledge diluted by obvious content                                 |
| 6-8   | Good - Mostly expert knowledge with minimal redundancy                                       |
| 9-10  | Excellent - Pure knowledge delta — every paragraph earns its tokens                         |

> D1 measures **proportion only** — not whether the expert content is well-structured. That is D2's job.

### D2: Expert Knowledge Craft (25 points)

Is the expert content well-structured? Covers three sub-criteria: thinking frameworks, domain procedures, and anti-patterns.

#### Sub-criteria A: Thinking Frameworks + Domain Procedures

Does the skill transfer expert *thinking patterns* alongside  *domain-specific procedures* ?

| Type                                 | Example                                                  | Value                          |
| ------------------------------------ | -------------------------------------------------------- | ------------------------------ |
| **Thinking patterns**          | "Before designing, ask: What makes this memorable?"      | High — shapes decision-making |
| **Domain-specific procedures** | "OOXML workflow: unpack → edit XML → validate → pack" | High — LLM may not know this  |
| **Generic procedures**         | "Step 1: Open file, Step 2: Edit, Step 3: Save"          | Low — LLM already knows       |

 **Valuable procedures** : non-obvious ordering, easy-to-miss critical steps, workflows LLM hasn't been trained on.
 **Redundant procedures** : generic file operations, standard programming patterns, well-documented library usage.

**Thinking framework looks like** :

```markdown
Before [action], ask yourself:
- **Purpose**: What problem does this solve? Who uses it?
- **Constraints**: What are the hidden requirements?
- **Differentiation**: What makes this solution memorable?
```

#### Sub-criteria B: Anti-Pattern Quality

Does the skill have an explicit NEVER/DONTS list or BAD examples with specific reasons?

Half of expert knowledge is knowing what NOT to do. LLM hasn't stepped on the landmines experts have. Good Skills must state the "absolute don'ts" — not vaguely, but with the *non-obvious reason* behind each one.

**Expert anti-patterns** (specific + reason):

```markdown
NEVER use generic AI-generated aesthetics:
- Overused fonts (Inter, Roboto, Arial)
- Purple gradients on white — the signature of AI-generated content
- Default border-radius on everything
```

**Weak anti-patterns** (vague, no reason):

```markdown
Avoid mistakes. Be careful with edge cases. Don't write bad code.
```

 **Sub-criterion cap** : if no explicit NEVER list or DONTS exists → D2 cannot exceed 20/25 regardless of framework quality. Anti-patterns are too commonly neglected to let framework quality compensate.

#### D2 Scoring

| Score | Criteria                                                                                  |
| ----- | ----------------------------------------------------------------------------------------- |
| 0-6   | Only generic procedures; no anti-patterns                                                 |
| 7-12  | Some domain procedures or some anti-patterns, not both                                    |
| 13-18 | Good thinking frameworks + domain procedures + basic NEVER list                           |
| 19-25 | Expert-level frameworks + non-obvious domain procedures + specific reasoned anti-patterns |

**The test** : Would an expert read this and say "yes, I learned this the hard way"? Or "this is obvious to everyone"?

### D3: Specification Compliance (15 points)

Does the Skill follow format requirements? **Critical focus: description quality.**

| Score | Criteria                                                                      |
| ----- | ----------------------------------------------------------------------------- |
| 0-5   | Missing frontmatter or invalid format                                         |
| 6-9   | Has frontmatter but description is vague or incomplete                        |
| 10-12 | Valid frontmatter, description has WHAT but weak on WHEN or missing exclusion |
| 13-15 | Perfect: WHAT + WHEN + KEYWORDS + exclusion clause                            |

**Frontmatter requirements** :

* `name`: lowercase, alphanumeric + hyphens only (not underscores), ≤64 characters
* `description`: the only field the agent sees before deciding whether to load the skill

**Description must answer four things** :

1. **WHAT** — what does this skill do?
2. **WHEN** — in what situations should it be used?
3. **KEYWORDS** — what searchable terms trigger it?
4. **EXCLUSION** — what should it explicitly NOT be used for?

```
┌──────────────────────────────────────────────────────────────┐
│  User Request → Agent sees ALL descriptions → Decides which  │
│                 (only descriptions, not bodies!)  to load    │
│                                                              │
│  No keywords → skill is invisible                            │
│  No exclusion → routing conflict with sibling skills         │
└──────────────────────────────────────────────────────────────┘
```

**Good description** :

```yaml
description: "Audit skill design via static SKILL.md analysis — knowledge delta,
anti-patterns, triggerability, progressive disclosure (100pts). Use when reviewing
a skill's DESIGN. Do NOT use to run real prompts or measure output quality —
use skill_tester for that."
```

**Poor description** :

```yaml
description: "Helps with document tasks"   # no WHEN, no keywords, no exclusion
description: "A helpful skill"             # useless
```

### D4: Progressive Disclosure (10 points)

Is content layered correctly across the three loading levels?

```
Layer 1: description (always in memory — ~100 tokens)
Layer 2: SKILL.md body (loaded on trigger — ideal <300 lines, max 500)
Layer 3: references/, scripts/, assets/ (loaded on demand — no limit)
```

| Score | Criteria                                                          |
| ----- | ----------------------------------------------------------------- |
| 0-3   | Everything in SKILL.md body (>500 lines, no references structure) |
| 4-6   | Has references but no loading triggers — orphaned content        |
| 7-8   | Good layering, MANDATORY triggers present in workflow             |
| 9-10  | Perfect: conditional triggers + explicit "Do NOT Load" guidance   |

**Good loading trigger** (embedded in workflow, not just listed):

```markdown
**MANDATORY — READ ENTIRE FILE**: Before proceeding, read `docx-js.md`
completely. Do NOT load `ooxml.md` or `redlining.md` for this task.
```

**Bad loading trigger** (listed but never triggered):

```markdown
## References
- docx-js.md - for creating documents
- ooxml.md - for editing
```

For simple skills with no references directory (<100 lines, self-contained): score on conciseness — penalise body bloat even without a references directory.

### D5: Pattern + Freedom Fit (15 points)

Did the skill choose the right structural pattern, and does the constraint level match the task's fragility?

These are scored together because pattern choice *encodes* freedom level — picking the wrong pattern and having wrong freedom calibration are almost always the same mistake.

#### Pattern selection

| Pattern              | ~Lines | Key characteristics                                   | When to use                            |
| -------------------- | ------ | ----------------------------------------------------- | -------------------------------------- |
| **Mindset**    | ~50    | Thinking > technique, strong NEVER list, high freedom | Creative tasks requiring taste         |
| **Navigation** | ~30    | Minimal body, routes to sub-files                     | Multiple distinct sub-scenarios        |
| **Philosophy** | ~150   | Two-step: philosophy → expression, emphasises craft  | Art/creation requiring originality     |
| **Process**    | ~200   | Phased workflow, checkpoints, medium freedom          | Complex multi-step projects            |
| **Tool**       | ~300   | Decision trees, code examples, low freedom            | Precise operations on specific formats |

#### Freedom calibration

| Task type              | Freedom level                | Because                                                 |
| ---------------------- | ---------------------------- | ------------------------------------------------------- |
| Creative/design        | High (principles, not steps) | Multiple valid approaches; differentiation is the value |
| Code review            | Medium (parameterised)       | Principles exist but judgment required                  |
| File format operations | Low (exact scripts)          | One wrong byte corrupts the file                        |

 **The fragility test** : if the agent makes a mistake, what's the consequence?

* High consequence → Low freedom
* Low consequence → High freedom

#### D5 Scoring

| Score | Criteria                                                                  |
| ----- | ------------------------------------------------------------------------- |
| 0-4   | Wrong pattern chosen; freedom level severely mismatched                   |
| 5-9   | Pattern partially fits; some freedom mismatches                           |
| 10-12 | Correct pattern, mostly calibrated freedom                                |
| 13-15 | Optimal pattern choice with perfectly matched constraint level throughout |

### D6: Practical Usability (15 points)

Can an agent *be expected to* act on this skill effectively, based on design signals alone?

> This dimension predicts usability from design indicators — decision trees, fallback coverage, code example completeness. It does NOT measure empirical usability. That is `skill_tester`'s job.
> **Note** : if D4 scores below 5, D6 cannot exceed 8 — orphaned references directly cause usability failures.

| Score | Criteria                                                       |
| ----- | -------------------------------------------------------------- |
| 0-4   | Confusing, incomplete, or contradictory guidance               |
| 5-9   | Usable but with noticeable gaps                                |
| 10-12 | Clear guidance for common cases                                |
| 13-15 | Comprehensive: decision trees + fallbacks + edge cases covered |

**Check for** :

* **Decision trees** : for multi-path scenarios, is there clear branching guidance?
* **Code examples** : do they actually work, or are they broken pseudocode?
* **Fallbacks** : what if the primary approach fails?
* **Edge cases** : unusual but realistic scenarios covered?

**Good usability** (decision tree + fallback):

```markdown
| Task | Primary | Fallback | When |
|------|---------|----------|------|
| Read text | pdftotext | PyMuPDF | Need layout info |
| Extract tables | camelot-py | tabula-py | camelot fails |

Scanned PDF: pdftotext returns blank → use OCR first
Encrypted PDF: permission error → use PyMuPDF with password
```

**Poor usability** :

```markdown
Use appropriate tools. Handle errors properly. Consider edge cases.
```

### D7: Output Specification (10 points)

Does the skill define what success looks like? A skill that defines its outputs enables tight, reliable assertions.

| Score | Criteria                                                                    |
| ----- | --------------------------------------------------------------------------- |
| 0-2   | No mention of expected outputs or completion signals                        |
| 3-5   | Vague output description ("generates a report")                             |
| 6-8   | Output format specified with key fields named                               |
| 9-10  | Full specification: format + required fields + verifiable completion signal |

**Good output specification** :

```markdown
## Output
Saves two files:
- `report.md` — human-readable with dimension scores table and Top 3 improvements
- `design_score.json` — machine-readable with `design_score_pct` and
  `ready_for_functional_testing` boolean
```

**Poor output specification** :

```markdown
Generate a report summarising your findings.
```

## NEVER Do When Evaluating

* **NEVER** give high scores because it "looks professional" or is well-formatted
* **NEVER** ignore token waste — every redundant paragraph is a deduction
* **NEVER** let length impress you — a 43-line skill can outperform a 500-line one
* **NEVER** skip testing decision trees — do they actually lead to correct choices?
* **NEVER** forgive explaining basics with "but it provides helpful context"
* **NEVER** overlook a missing NEVER list — it caps D2 at 20/25
* **NEVER** undervalue the description — poor description = skill never loads
* **NEVER** put "when to use" info only in the body — agent only sees description before loading
* **NEVER** treat D6 predicted usability as empirical — that requires running real prompts

## Evaluation Protocol

### Step 1: Knowledge Ratio Scan

Read SKILL.md and mark each section:

* **[E]** Expert — LLM genuinely doesn't know this
* **[A]** Activation — LLM knows but brief reminder is useful
* **[R]** Redundant — LLM definitely knows this, delete it

Calculate E:A:R ratio → feeds directly into D1 score.

### Step 2: Structure Check

```
[ ] name: lowercase, hyphens only, ≤64 chars
[ ] description: WHAT + WHEN + KEYWORDS + exclusion clause
[ ] Total lines in SKILL.md
[ ] References directory exists? Loading triggers present?
[ ] Which pattern does this follow?
[ ] Output format defined?
```

### Step 3: Score Each Dimension

For each of the 7 dimensions:

1. Find specific evidence (quote relevant lines)
2. Assign score with one-line justification
3. Note improvements if score < max

### Step 4: Calculate Total & Grade

```
Total = D1 + D2 + D3 + D4 + D5 + D6 + D7
Max = 100 points
```

| Grade | Score  | Meaning                                   |
| ----- | ------ | ----------------------------------------- |
| A     | 90-100 | Excellent — production-ready             |
| B     | 80-89  | Good — minor improvements needed         |
| C     | 70-79  | Adequate — proceed to functional testing |
| D     | 60-69  | Average — fix before functional testing  |
| F     | <60    | Poor — needs fundamental redesign        |

### Step 5: Generate Report

In your OUTPUT_FOLDER, save `evals/skill-tests/<skill-name>/llm_judge_report.md`:

```markdown
# Skill Evaluation Report: [Skill Name]

## Summary
- **Total Score**: X/100
- **Grade**: [A/B/C/D/F]
- **Pattern**: [Mindset/Navigation/Philosophy/Process/Tool]
- **Knowledge Delta**: [Poor/Mediocre/Good/Excellent]
- **Verdict**: [One sentence]
- **Ready for functional testing**: [Yes / No]

## Dimension Scores

| Dimension | Score | Max | Notes |
|-----------|-------|-----|-------|
| D1: Knowledge Ratio | | 10 | |
| D2: Expert Knowledge Craft | | 25 | |
| D3: Specification Compliance | | 15 | |
| D4: Progressive Disclosure | | 10 | |
| D5: Pattern + Freedom Fit | | 15 | |
| D6: Practical Usability | | 15 | |
| D7: Output Specification | | 10 | |

## Critical Issues
[Must-fix problems]

## Top 3 Improvements
1. [Highest impact]
2. [Second priority]
3. [Third priority]

## Detailed Analysis
[For each dimension scoring below 80%: what's wrong, specific evidence, concrete fix]
```

In the OUTPUT_FOLDER, also save `evals/skill-tests/<skill-name>/llm_judge_score.json` for pipeline consumption:

```json
{
  "skill_name": "<name>",
  "design_score": 74,
  "design_score_max": 100,
  "design_score_pct": 0.74,
  "grade": "C",
  "knowledge_delta": "Good",
  "dimensions": {
    "d1_knowledge_ratio":        { "score":  7, "max": 10 },
    "d2_expert_knowledge_craft": { "score": 18, "max": 25 },
    "d3_spec_compliance":        { "score": 12, "max": 15 },
    "d4_progressive_disclosure": { "score":  7, "max": 10 },
    "d5_pattern_freedom_fit":    { "score": 11, "max": 15 },
    "d6_practical_usability":    { "score": 10, "max": 15 },
    "d7_output_specification":   { "score":  9, "max": 10 }
  },
  "ready_for_functional_testing": true
}
```

## Common Failure Patterns

### Pattern 1: The Tutorial

```
Symptom: Explains what PDF is, how Python works, basic library usage
Fix: Delete all basic explanations. LLM already knows this.
     Focus on expert decisions, trade-offs, and anti-patterns.
```

### Pattern 2: The Dump

```
Symptom: SKILL.md is 800+ lines with everything included
Fix: Core routing and decision trees in SKILL.md (<300 lines)
     Detailed content in references/, loaded on-demand
```

### Pattern 3: The Orphan References

```
Symptom: References directory exists but files are never loaded
Fix: Add "MANDATORY - READ ENTIRE FILE" at workflow decision points
     Add "Do NOT Load" to prevent over-loading
```

### Pattern 4: The Checkbox Procedure

```
Symptom: Step 1, Step 2, Step 3... mechanical procedures
Fix: Transform into "Before doing X, ask yourself..."
     Focus on decision principles, not operation sequences
```

### Pattern 5: The Vague Warning

```
Symptom: "Be careful", "avoid errors", "consider edge cases"
Fix: Specific NEVER list with concrete examples and non-obvious reasons
     Absence of NEVER list caps D2 at 20/25
```

### Pattern 6: The Invisible Skill

```
Symptom: Great content but skill rarely activates
Fix: Description must answer WHAT + WHEN + KEYWORDS + exclusion clause
```

### Pattern 7: The Wrong Location

```
Symptom: "When to use this skill" section in body, not description
Fix: Move all triggering info to description
     Body loads only AFTER the triggering decision is made
```

### Pattern 8: The Over-Engineered

```
Symptom: README.md, changelogs, self-evaluation notes, restatement checklists
Fix: Delete everything an agent doesn't need to act on the task
     No documentation about the skill itself
```

### Pattern 9: The Pattern Mismatch

```
Symptom: Tool-pattern structure for a creative task, or vague Mindset-style
         guidance for a fragile file-format operation
Fix: Match pattern to task type (see D5 pattern selection table)
     Pattern choice determines appropriate constraint level
```

### Pattern 10: The Routing Conflict

```
Symptom: Two skills use identical trigger words ("evaluate", "test", "validate")
Fix: Add explicit "Do NOT use when..." exclusion clause to description
     Written with sibling skills in mind, not in isolation
```

### Pattern 11: The Undefined Output

```
Symptom: "Generate a report" with no format, fields, or completion signal
Fix: Specify output file names, formats, required fields, and what
     "done" looks like — enables tight assertions in functional testing
```

---

## The Meta-Question

> **"Would an expert in this domain say: 'Yes, this captures knowledge that took me years to learn'?"**

If yes → the Skill has genuine value.
If no → it's compressing what LLM already knows.
