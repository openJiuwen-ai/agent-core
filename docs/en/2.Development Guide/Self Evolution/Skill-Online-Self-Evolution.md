# Skill Online Self-Evolution: Keep Capabilities Improving in Real Use

Agent Skills and Swarm Skills solve the problem of how capabilities get captured: the former captures the expertise of a single Agent, and the latter captures the collaboration pattern of a multi-Agent team.

But capture alone is not enough.

Real-world tasks keep changing. Tool APIs fail, file formats shift, users correct the Agent's understanding, and team collaboration exposes new edge cases. If a Skill never changes after it is created, it will eventually become outdated documentation.

Skill online self-evolution solves this problem.

In one sentence:

> Creating a Skill gives an Agent a capability; online self-evolution keeps that capability improving in real use.

This is not about the system secretly rewriting files, and it is not about the Agent endlessly rewriting itself. openJiuwen's self-evolution is a transparent, manageable feedback loop with confirmation for important changes and rollback for important versions.

---

## First, draw the boundary: this section only covers existing capabilities evolving

This section is about **how an existing Agent Skill or Swarm Skill keeps improving in real use**.

If there is no suitable Skill yet and the system suggests creating a new Skill or Swarm Skill from the task trajectory, that belongs to the "automatic creation suggestion" flow. Its goal is to capture a new capability from scratch.

Online self-evolution focuses on a different question:

> An existing capability has shown problems or accumulated experience in real use. Should it be supplemented, corrected, reorganized, or rebuilt?

---

## What exactly is evolving?

When people hear "self-evolution", the first question is often: will the Agent randomly rewrite `SKILL.md`?

openJiuwen is not designed that way.

The core object of day-to-day evolution is the "experience record", not the original Skill body.

A Skill can be split into two layers:

```text
Skill definition
  └── SKILL.md, roles/, workflow.md, scripts/, and other base files

Evolution experience layer
  └── experience records in evolutions.json
```

The original definition answers:

> How should this Skill work at the beginning?

The evolution layer answers:

> What supplementary rules, error handling, user preferences, and edge cases have we learned from real use?

This design matters.

If every evolution directly edits `SKILL.md`, three problems appear:

- Mistakes become hard to roll back.
- Old and new experience get mixed together, so each item is hard to evaluate independently.
- Historical edits easily conflict with future Skill upgrades.

So openJiuwen uses an "evolution patch" architecture:

> New experience is first stored as an independent record attached to the Skill and loaded dynamically at runtime; the system may update the Skill's experience index or display block, but it does not rewrite each new experience directly into the main flow. Only when the user explicitly requests a rebuild will high-quality experience be reorganized into the Skill body.

---

## What does an evolution patch look like?

Evolution experience is stored in `evolutions.json` under the Skill directory.

A typical experience record contains:

- `id`: experience ID.
- `source`: where it came from, such as execution failure, user correction, or proactive evolution.
- `timestamp`: when it was generated.
- `context`: what happened at the time.
- `change`: where to supplement and what to add.
- `score`: quality score.
- `usage_stats`: later usage statistics.
- `applied`: whether it has already been promoted into the main Skill.

Example:

```json
{
  "entries": [
    {
      "id": "ev_1cdbc3a5",
      "source": "execution_failure",
      "timestamp": "2026-03-09T09:33:08Z",
      "context": "When exporting Excel files, the target system requires the tax rate field to be non-empty",
      "change": {
        "section": "Troubleshooting",
        "action": "append",
        "target": "body",
        "content": "Before generating the invoice Excel file, you must confirm the tax rate field. If the user does not provide a tax rate, ask first and do not default to 0."
      },
      "applied": false,
      "score": 0.6,
      "usage_stats": {
        "times_presented": 0,
        "times_used": 0,
        "times_positive": 0,
        "times_negative": 0
      }
    }
  ]
}
```

You do not need to hand-write these fields.

In daily use, you should only edit the experience content itself, that is `change.content`. Fields such as `id`, `source`, `timestamp`, `score`, `usage_stats`, and `applied` are maintained by the system. Editing them manually can break lifecycle management.

---

## How does the system find signals worth evolving?

Online self-evolution is driven by real usage, not by guesses.

There are four common signal types.

### 1. Tool or script execution failures

Examples:

- API timeout.
- Command not found.
- Permission denied.
- File format not as expected.
- A script fails on a specific input.

These signals are good candidates for troubleshooting experience or pre-checks.

For example:

```text
Experience: Before calling the export script, check whether the output directory exists. If it does not, create it first.
```

### 2. User corrections and preference feedback

When a user says "that's wrong", "it should be this way", "you misunderstood", or "ask me first next time", that feedback is often more valuable than an error log.

For example:

```text
User: No, I meant a technical research email, not a paper summary.
```

This can evolve into:

```text
When the user asks for a "technical research email", the output is for internal R&D colleagues and should include background, trends, discussion questions, and action suggestions, rather than only a paper summary.
```

### 3. Effective experience during task execution

Sometimes there is no failure, but the Agent discovers a better path while solving the problem.

For example:

- If an API is unstable, do a lightweight probe before issuing batch requests.
- For large-file processing, sample first before deciding whether to chunk.
- For multi-paper research, do a coarse screening first and then read the shortlisted papers in depth.

These experiences can improve future task efficiency.

### 4. New constraints in team collaboration

For Swarm Skills, signals may also come from the team level:

- A new role is needed.
- A role's responsibilities are too broad and should be split.
- The Leader often misses opposing views during summary.
- A SwarmFlow stage needs retries, budget limits, or human confirmation.

These experiences improve the collaboration rules and workflow of an existing Swarm Skill.

---

## Automatic evolution: observe first, then generate candidate experience

Once you understand the evolving object and the source of signals, the automatic evolution flow becomes straightforward.

In the JiuwenSwarm configuration page, you can enable "auto-detect evolvable signals". Once enabled, the system observes conversations and tool execution to see whether there is experience worth capturing.

Automatic evolution roughly has five steps:

```text
Run an existing Skill / Swarm Skill
        ↓
Observe tool results, user feedback, and team trajectories
        ↓
Identify evolvable signals
        ↓
Generate a candidate experience patch
        ↓
Wait for user confirmation before writing to the experience store
```

Automatic evolution does not create a new Skill. It assumes the current task is already associated with an existing Skill or Swarm Skill and adds experience around that capability.

Examples:

- If `invoice-xlsx` forgets to ask for the tax rate when generating invoice sheets, the system can suggest adding "confirm the tax rate before generation".
- If `paper-reading` learns from user correction that a "technical research email" is not a "paper summary", the system can suggest adding output audience and structure preferences.
- If `research-swarm` discovers that an experiment-audit perspective is missing, the system can suggest adding a team-level experience.

If the system determines that "there is no suitable Skill yet and a new Skill or Swarm Skill should be created", that belongs to the automatic creation suggestion flow, not the online evolution of an existing capability discussed here.

---

## User confirmation: automatically generated experience does not land silently

The most important safety boundary in online self-evolution is this: **there is usually user confirmation between automatic experience generation and actual writeback**.

When the system detects a signal, it first generates candidate experience and then shows a confirmation prompt like this:

```text
Skill 'xlsx' generated a new experience for evolution:

- Target: body
- Section: Troubleshooting

Before generating the invoice Excel file, you must confirm the tax rate field. If the user does not provide a tax rate, ask first and do not default to 0.

Options:
- Accept
- Reject
```

If you accept it, the experience is written into the experience store.

If you reject it, the experience is discarded.

Note that confirmation boundaries differ across governance commands:

- New experience generated by automatic signal detection usually asks the user to accept or reject.
- A normal Skill's `/evolve_simplify` usually shows the cleanup actions first and then asks whether to execute them.
- The cleanup path for Team / Swarm Skills may execute directly and return results in the current version.
- `/evolve_rebuild` is a user-initiated rebuild task; it archives the old version first and then generates a follow-up task prompt.
- `/evolve_rollback` is a user-initiated rollback command and does not show an extra confirmation prompt.

So the more accurate statement is: openJiuwen self-evolution is not "the Agent secretly changing itself". It explicitly exposes each stage to the user:

```text
Real use generates a signal
        ↓
The system generates candidate experience
        ↓
User confirmation (automatic experience path)
        ↓
Write to the experience store
        ↓
Load on demand next time
```

---

## Manual trigger: when you already know what should change

Automatic evolution is good when the system observes signals in the background. Manual triggering is for the case where you already know how an existing Skill or Swarm Skill should improve.

### Trigger an existing Skill to evolve immediately

Use:

```bash
/evolve <skill_name> [user_query]
```

For example:

```bash
/evolve xlsx Before creating invoice files, ask me to confirm the fields and tax rate instead of guessing
```

In Team mode, you can also express an evolution intent for a team Skill, but you should usually be explicit about what should improve:

```bash
/evolve research-swarm Add an experiment-audit role to check paper experimental weaknesses
```

### View and organize experience

To inspect the experience accumulated by a Skill:

```bash
/evolve_list <skill_name> [--sort score]
```

When experience grows too large, becomes repetitive, goes stale, or varies in quality, you can ask the system to generate a cleanup plan:

```bash
/evolve_simplify <skill_name> [user_intent]
```

For example:

```bash
/evolve_simplify xlsx Merge duplicate export-failure experiences and delete old rules that no longer apply
```

Cleanup for a normal Skill usually asks for confirmation first; the cleanup path for Team / Swarm Skills may execute directly in the current version. So before cleanup, it is better to inspect the experience store with `/evolve_list`.

### Rebuild and rollback

When a Skill has accumulated a lot of high-quality experience and you want to reorganize that experience back into `SKILL.md`, use:

```bash
/evolve_rebuild <skill_name> [user_intent]
```

For example:

```bash
/evolve_rebuild paper-reading Organize high-scoring experience into the reading workflow and de-emphasize low-scoring experience
```

Rebuild creates a follow-up rebuild task. The system archives the old version first and prepares the rebuild context; the new version itself depends on follow-up Agent execution.

When you need to roll back, use:

```bash
/evolve_rollback <skill_name> [version]
```

If you do not specify a version, the system lists the archive versions that can be rolled back to.

---

## How are these experiences used at runtime?

When a Skill is called, the system reads not only the original `SKILL.md`, but also the evolution experience accumulated for that Skill.

It does not dump all historical experience in one go.

The system loads, orders, and presents experience according to its target location, score, and other metadata.

High-scoring, recent, and frequently accepted experience is more likely to appear first. Low-scoring, stale, repetitive, or never-accepted experience is down-weighted and can later be cleaned up or deleted.

From the user's perspective, this looks like:

- The Agent reaches known error patterns faster.
- The Agent remembers user preferences that were corrected before.
- Roles and collaboration constraints in a Team become better over time.
- The same pitfall does not need to be explained again every time.

That is what "gets better with use" actually means.

The model parameters do not change; the Skill's experience layer becomes thicker and more accurate.

---

## How is experience scored?

Experience cannot grow forever.

If every record is kept permanently, the store eventually turns into another junk drawer: duplicate, stale, conflicting, and ultimately slowing the Agent down or misleading it.

So openJiuwen maintains a quality score for each experience item. The score mainly comes from three dimensions.

### 1. Effectiveness

Did task performance improve after this experience was used?

If an experience was accepted and solved the problem, its effectiveness goes up. If it caused errors or misled the Agent, the score goes down.

### 2. Utilization

After this experience is shown to the Agent, does the Agent actually use it?

If an experience has been shown many times but never used, it may be irrelevant, too abstract, or triggered too broadly.

### 3. Freshness

Is this experience still valid for the current Skill?

Experience decays over time. If the Skill body has already been rebuilt or upgraded, older experience may be down-weighted so stale rules do not keep affecting the new version.

The system combines these dimensions into a final score. A simple working rule is:

> High-scoring experience is injected first, low-scoring experience is gradually down-weighted, and eventually becomes a cleanup candidate.

---

## Lifecycle management: experience must have inflow and outflow

Online self-evolution is not "only add, never remove".

A healthy experience store should behave like a team knowledge base: new issues are captured continuously, old experience is cleaned up regularly, stale content can be removed, and high-value content can be promoted into the base Skill.

### View: know what the Skill has learned

Use:

```bash
/evolve_list <skill_name> [--sort score]
```

You can inspect what experience a Skill currently has, how each item scores, and which items are used more often.

### Clean up: merge duplicates, delete low-quality items, refine verbose ones

Use:

```bash
/evolve_simplify <skill_name> [user_intent]
```

The system will generate cleanup actions or execute them directly. Common actions include:

- `DELETE`: remove low-quality or stale experience.
- `MERGE`: combine similar experience.
- `REFINE`: tighten up a single item.
- `KEEP`: retain high-value experience.

Cleanup for a normal Skill usually needs confirmation before execution. The cleanup path for Team / Swarm Skills may execute directly in the current version. So before running cleanup, it is better to inspect the experience content with `/evolve_list`.

### Rebuild: reorganize high-value experience back into the Skill

After long-term use, when a Skill has accumulated many high-quality items, you can rebuild it.

Use:

```bash
/evolve_rebuild <skill_name> [user_intent]
```

Rebuild creates a follow-up rebuild task. The core process includes:

1. Archive the current `SKILL.md`.
2. Archive the current `evolutions.json` and select high-scoring experience as rebuild context.
3. Generate a follow-up task prompt that guides the Agent to use `skill-creator` or `swarmskill-creator` to reorganize the Skill body.
4. After the rebuild context is ready, clear or reset the currently active experience to avoid duplicated injection from old and new experience.

Rebuild is not a direct overwrite button, and it is not just appending all experience to the end of the document. It is closer to a rewrite task with archive protection: keep the old version first, then restructure high-value experience into the right place.

For example:

- Multiple troubleshooting items can be merged into a clear `Troubleshooting` section.
- Multiple user preferences can be organized into a pre-execution checklist.
- Multiple collaboration experiences in a Swarm Skill can be turned into new roles, quality gates, or failure-handling strategies.

### Rollback: if rebuild is unsatisfactory, roll back

Before rebuilding, the system archives the old version.

If the rebuild result is unsatisfactory, use:

```bash
/evolve_rollback <skill_name> latest
```

Or first inspect the available rollback versions:

```bash
/evolve_rollback <skill_name>
```

Then roll back to the chosen version.

This makes "promoting experience into the main document" a reversible operation.

---

## Can both Skill and Swarm Skill evolve?

Yes, but the evolving object is different.

### Evolution for Agent Skills

The evolution of a normal Skill mainly focuses on:

- Whether the execution steps are missing prerequisites.
- Whether tool calls need retries or fallback behavior.
- Whether user preferences should be added.
- Whether the output format needs to be stricter.
- Whether scripts or templates need more explanation.

For example:

```text
Before generating invoice Excel files, the tax rate field must be confirmed first.
```

### Evolution for Swarm Skills

In addition to member Skill experience, Swarm Skill evolution also looks at the team level:

- Whether roles need to be added, removed, or split.
- Whether the Leader needs clearer division-of-labor rules.
- Whether a stage needs a quality gate.
- Whether parallel tasks need budget and timeout constraints.
- Whether SwarmFlow needs extra failure handling or human confirmation nodes.

For example:

```text
When distributing short-video content across platforms, the team should add a "platform operations" role to adapt titles and copy.
```

You can think of this as two-layer evolution:

- Member level: each Teammate gains more execution experience.
- Team level: the Leader gets better at assignment, collaboration, and control.

---

## The boundary between automatic creation and online self-evolution

In openJiuwen's Skill system, there are two concepts that are easy to confuse:

- Automatic suggestion to create a Skill / Swarm Skill.
- Online self-evolution of an existing Skill / Swarm Skill.

The boundary is clear.

### Automatic suggestion to create: from zero to one

When the system sees a task or team collaboration that forms a reusable pattern but no suitable Skill exists yet, it suggests creating a new Skill or Swarm Skill.

It answers:

> Should this newly learned pattern become a new capability?

### Online self-evolution: make an existing capability better

When an existing Skill or Swarm Skill fails, drifts, accumulates duplicates, or needs cleanup in real use, the system generates experience patches from the trajectory and user feedback. New experience generated automatically is usually written to the store only after user confirmation; cleanup, rebuild, and rollback follow the behavior of their respective commands.

It answers:

> Should this existing capability be supplemented, corrected, reorganized, or rebuilt?

This section is about the latter.

---

## A complete example: turning technical research into a Skill

Suppose you often ask the Agent to write "technical research emails".

The first time, you can just ask the Agent to do the task:

```text
Help me research the latest papers and open-source projects on Agent self-evolution,
and organize the results into a technical sharing email suitable for our R&D team.
```

The Agent may search for materials, shortlist papers, extract ideas, and generate the email. After the task succeeds, if you think this workflow will be reused often, you can ask `skill-creator`:

```text
Based on this task, help me create a Skill.
It will be used for pre-sharing research emails.
The output should include: background, key papers, trend analysis, risks, and discussion questions for the team.
The email tone should fit an R&D audience.
```

If automatic creation suggestion is enabled, the system may also ask whether to create the Skill after recognizing a reusable workflow.

After creation, next time you say:

```text
Help me prepare a research email about multi-Agent collaboration.
```

The Agent can match this Skill during discovery and execute according to the workflow captured in it.

The end result is:

> You do not need to teach the Agent the same thing every time. It captures the workflow that worked and reuses it next time.

---

## FAQ

### Q1: Do I need to learn how to write `SKILL.md` first?

No.

The recommended path is to start with `skill-creator`. You only need to describe the goal, scenario, and preferences. If you want to refine the result later, you can edit `SKILL.md`.

### Q2: Will Skill make the Agent slower?

A well-designed Skill should not slow the Agent down significantly.

That is because the Agent only loads the name and description at startup. The full `SKILL.md`, references, and scripts are loaded on demand. What really slows things down is stuffing lots of irrelevant instructions into the system prompt, not using Skills.

### Q3: When should I not create a Skill?

Do not create a Skill when:

- The task is only done once.
- A single sentence can reliably complete it.
- It is just temporary information with no reuse value.
- An existing Skill already covers it.
- The workflow has not been validated yet, and capturing it early would freeze the wrong experience.

### Q4: Will automatic Skill creation suggestion create things randomly?

No silent creation happens.

The system only suggests based on the execution trajectory. A real Skill is created only after the user confirms, and then `skill-creator` generates it.

### Q5: Should I aim for a big, all-in-one Skill?

Not recommended.

It is better to start with a small but precise Skill that covers a high-frequency, clearly defined, reusable task. You can keep enriching it with experience during real use.

The value of Agent Skills is not that you write the perfect document once. The value is that you create a capability carrier that can keep accumulating experience.

It turns the Agent from "re-understanding the task every time" into "gradually learning your way of working".

---

## Related topic

Swarm Skills focus on:

> When a Skill is no longer just a single Agent's operating manual, but a reusable collaboration package for a multi-role team, how should it be created, used, and accumulated?

Related topics include `swarmskill-creator`, trajectory accumulation in Team mode, and the two collaboration forms: open collaboration and SwarmFlow.
