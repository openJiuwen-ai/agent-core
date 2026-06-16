# Swarm Skills: Turn a Successful Collaboration into Team Capability

Agent Skills can capture the professional knowledge, workflows, scripts, and templates of a single Agent into a reusable capability.

But many real tasks cannot be solved just by "one Agent learning one workflow".

For example:

- Code review needs readability, performance, and security perspectives at the same time. If one Agent plays multiple roles in turn, the viewpoints are too similar.
- Financial analysis needs macro, industry, financial, risk, and investment recommendation experts to judge in parallel before everything is merged.
- Paper sharing needs one stage to read papers, another to decompose methods, another to generate materials, and another to write the email, with clear handoffs between stages.
- Large PPT generation needs chapter planning, parallel generation, and unified review. If one Leader has to improvise the whole orchestration, it can easily become chaotic.

The hard part in these tasks is not "the Agent lacks one piece of knowledge", but "the collaboration pattern itself needs to be captured".

Swarm Skills solve this problem.

In one sentence:

> An Agent Skill captures the capability of a single Agent; a Swarm Skill captures the collaboration capability of a multi-role Agent team.

---

## Fastest start: generate a team with one sentence

The shortest path to using Swarm Skills is not to hand-write role files or orchestrate every Agent yourself, but to use `swarmskill-creator` directly.

You only need to describe what kind of team you want and what task it should help you complete.

For example:

```text
Help me create a Swarm Skill for code security audits.
I want the team to include at least a readability reviewer, a performance reviewer, and a security reviewer.
They should review independently first, and then a summary role should consolidate the issues into a report ordered by severity.
```

Or:

```text
Help me create a Swarm Skill for internal paper sharing.
The team should first read the paper, then analyze the method and experiments, and finally generate an email suitable for sharing with the engineering team.
If the workflow can be determined in advance, please generate a SwarmFlow workflow script.
```

The usual process looks like this:

1. You describe the target task in natural language.
2. `swarmskill-creator` decides whether the task really needs a multi-Agent team.
3. If it fits, it chooses a suitable collaboration style and output form.
4. It designs the roles, division of labor, collaboration flow, constraints, and dependent resources.
5. It generates the standard Swarm Skill folder and validates the structure.
6. Next time a similar task appears, Team mode can call this team directly.

So what users really need to understand is not "how to hand-write a team directory", but "when to turn a collaboration into a team skill".

---

## What is a Swarm Skill?

A Swarm Skill is the multi-role extension of the Agent Skills standard.

A normal Agent Skill describes "how one Agent should complete a task". A Swarm Skill describes "how a team of Agents should collaborate to complete a task".

It is still a folder, and the entry point is still `SKILL.md`, but it also describes:

- Which roles the team has.
- What each role is responsible for, and what it is not responsible for.
- How roles collaborate, hand off work, and summarize results.
- How to handle failure, timeouts, conflicts, or resource limits.
- Which Skills, tools, or external resources each role can use.

A typical Swarm Skill structure looks like this:

```text
my-swarm/
├── SKILL.md              # Team entry: name, description, role list, trigger conditions
├── roles/                # Responsibilities, boundaries, and output format for each role
│   ├── researcher.md
│   ├── reviewer.md
│   └── editor.md
├── workflow.md           # How the team collaborates: parallel, serial, summary, quality gates
├── bind.md               # Team constraints: budget, timeout, failure handling, fallback strategy
├── dependencies.yaml     # Skills, tools, and external capabilities each role depends on
└── scripts/
    └── workflow.py       # Optional: executable SwarmFlow orchestration script
```

You only need to understand this structure. In practice, it is recommended to let `swarmskill-creator` generate it automatically.

In openJiuwen code and some historical docs, you may also see the name `Team Skill`. For user understanding, you can treat them as the same kind of capability: both capture a multi-Agent collaboration pattern as a reusable team skill. This document uses the product term `Swarm Skill` consistently.

---

## Why do we need Swarm Skills?

Not every complex task needs multiple Agents. A Swarm Skill is only valuable when a single Agent is structurally insufficient.

The most common cases fall into three categories.

### 1. Same-source viewpoints: one Agent cannot truly "argue with itself"

If the same Agent plays both the "solution designer" and the "critical reviewer", it may look like two roles, but in practice they often share the same analytical prior.

It can sound different, but it does not necessarily reach truly independent judgments.

Good examples for Swarm Skills:

- Code review: readability, performance, and security roles review independently.
- Product proposal review: user, business, and engineering viewpoints each judge the plan.
- Paper review simulation: supporter, opponent, and experiment auditor each give opinions.

The key is not to write a few more prompts, but to keep different roles bounded and independent.

### 2. Parallelism constraints: the subtasks can already run at the same time

Some tasks naturally split into multiple independent perspectives.

If one Agent handles them sequentially, the task becomes slower and the context from earlier subtasks can contaminate later ones.

Good examples for Swarm Skills:

- Competitive analysis across multiple platforms.
- Reading multiple papers in parallel.
- Macro, industry, financial, and risk analysis in financial due diligence.
- Parallel generation of multiple chapters in a large document.

The key is parallel decomposition and final integration.

### 3. Stage coupling: the workflow has clear handoffs and quality gates

Some tasks are not parallel, but they do have clear stages.

For example, "ideation -> drafting -> editing -> review" requires each stage to inherit the previous output, but the stages should not be blended together. A single Agent often starts ideating while editing, then rewrites the goal while editing, which blurs the stage boundaries.

Good examples for Swarm Skills:

- Paper sharing: reading -> method breakdown -> material preparation -> email generation.
- Short-video production: topic selection -> script -> storyboard -> title copy -> platform adaptation.
- Incident postmortem: information gathering -> timeline reconstruction -> root cause analysis -> action items.

The key is stage handoff, quality gates, and failure handling.

---

## Two collaboration modes for Swarm Skills

A Swarm Skill is not "the more automatic, the better", and it is not "all collaboration must be written as code".

openJiuwen's judgment is clear:

> If orchestration can be determined in advance, let the system execute it reliably; if it requires live discussion, keep the collaboration open.

So the same Swarm Skill can use two main collaboration modes.

### Mode 1: open collaboration Swarm Skill

This mode does not force an executable script. It focuses on capturing roles, rules, boundaries, and the collaboration style.

It is suitable for tasks where orchestration changes with context.

Examples:

- Multi-expert roundtable discussion.
- Proposal review and cross-examination.
- Strategic discussion.
- Creative planning.
- Multi-role interactive games such as Werewolf-style games.

In these tasks, the roles and rough stages are fixed, but "who responds to whom, who challenges whom, and how opinions flow" has to happen live.

Trying to hard-code the process often reduces collaboration quality.

An open collaboration Swarm Skill is more like a "team collaboration manual": it tells the Leader which roles to organize, how to keep role boundaries, when to isolate thinking, when to discuss, and how to summarize at the end.

### Mode 2: executable SwarmFlow orchestration

This mode generates `scripts/workflow.py` and uses SwarmFlow to turn a fixed collaboration flow into executable workflow.

It is suitable for tasks whose orchestration can be determined in advance.

Examples:

- Analyze multiple papers one by one and then summarize them into a report.
- Run parallel analysis on multiple dimensions in financial research and then produce a unified investment recommendation.
- Collect and organize materials in batch and generate emails.
- Generate large PPTs in parallel by chapter and then merge them.

The core idea of SwarmFlow is:

> Orchestration is handled by the system; intelligence is handled by the Agent.

Who goes first, who runs in parallel, who passes results to whom, when to summarize, and how to handle failures are executed reliably by the system. The reasoning inside each node is still delegated to the corresponding Agent.

This turns a Swarm Skill from a "collaboration manual" into an "executable workflow".

---

## The three output forms of `swarmskill-creator`

Open collaboration and SwarmFlow are two user-facing collaboration modes. In `swarmskill-creator` outputs, they map to three forms.

### 1. Markdown spec: the default team specification

This is the default form for creating a complete, readable team capability package.

It usually includes:

- `SKILL.md`: team entry, trigger conditions, role list, and file index.
- `roles/`: responsibilities, boundaries, and output format for each role.
- `workflow.md`: collaboration flow, parallel or serial relationships, and quality gates.
- `bind.md`: resource constraints, failure handling, and fallback strategy.
- `dependencies.yaml`: Skills and tools each role depends on.

This form is suitable for open collaboration tasks such as proposal reviews, multi-expert discussions, creative planning, and cross-review.

### 2. Script-only SwarmFlow: the smallest executable workflow

When the user explicitly wants a "workflow", "SwarmFlow", "executable orchestration", or `workflow.py`, but does not need a full team manual, `swarmskill-creator` can generate the minimal form:

```text
my-workflow-swarm/
├── SKILL.md
└── scripts/
    └── workflow.py
```

This form keeps only a minimal `SKILL.md` entry and a `scripts/workflow.py` file. The role prompts, structured output schema, stage definitions, and orchestration logic all live in the script.

It is suitable for workflows that are very certain and where the goal is "get it running and make it reusable", such as batch paper processing, batch report generation, and fixed-step office automation.

### 3. Markdown spec + SwarmFlow: full team spec plus executable script

Some complex scenarios need both a complete team manual and executable orchestration.

For example, an enterprise-grade investment research Swarm Skill may need to explain the boundaries between macro analysts, industry analysts, and risk-control experts, while also providing a fixed SwarmFlow execution path.

In this case, `swarmskill-creator` can generate both:

- Standard team specification files: `SKILL.md`, `roles/`, `workflow.md`, `bind.md`, `dependencies.yaml`.
- Executable orchestration script: `scripts/workflow.py`.

The user does not need to figure out the file structure manually, as long as the task goal is clear.

---

## How to use `swarmskill-creator` to create a Swarm Skill?

`swarmskill-creator` is the companion authoring tool for Swarm Skills.

It is itself a standard Agent Skill, dedicated to creating, converting, or modifying Swarm Skills. You can think of it as a "team-skill generation specialist".

It supports three kinds of operations.

### Create: generate a team from one requirement

This is suitable when you do not yet have an existing team Skill and only know that you want to complete a complex task.

Example:

```text
Help me create a Swarm Skill for code review.
I want it to focus on readability, performance, and security issues at the same time.
Different reviewers should output independently first, and then a lead reviewer should summarize them into a final report.
```

`swarmskill-creator` will decide whether this scenario really needs a team. If a single Agent Skill is already enough, it should advise you not to over-orchestrate.

If the task fits a team, it will continue to design:

- Which roles should exist.
- Each role's stance, success criteria, and boundaries.
- Whether execution should be parallel or serial.
- Which stages need quality gates.
- Which local Skills or tools should be assigned to each role.
- Whether a SwarmFlow executable script is needed.

### Convert: upgrade a single-Agent Skill into a team Skill

Some single-Agent Skills already contain multiple implicit roles.

For example, a code review Skill might include:

- Check readability.
- Check performance.
- Check security.
- Output a consolidated report.

At this point, you can ask `swarmskill-creator` to convert it into a Swarm Skill:

```text
Convert this single-Agent Skill @code-review into a Swarm Skill.
Keep the existing review standards, but split it into readability, performance, and security reviewers.
```

The point of conversion is not to make more files. It is to separate the multiple responsibilities that were mixed inside one Agent so the role boundaries become clearer.

### Modify: keep adjusting an existing team

A Swarm Skill can continue to evolve after it is created.

For example:

```text
Add a platform operations role to short-video-swarm,
responsible for adapting titles and copy for Douyin, Xiaohongshu, and Bilibili.
```

Or:

```text
Turn this investment research Swarm Skill into a SwarmFlow version,
so macro, industry, and financial analysis run in parallel and then merge into one summary.
```

`swarmskill-creator` will try to change only the affected files and then revalidate the structure.

---

## How should you describe the task so it produces a better Swarm Skill?

You do not need to know the directory structure, but you do need to explain why a team is needed.

You can use this template:

```text
Help me create a Swarm Skill for [task scenario].
The current single-Agent problem is [same-source viewpoints / cannot parallelize / stages get confused easily].
I want the team to include [key roles or professional perspectives].
The collaboration style should be [independent parallel / sequential pipeline / independent first, then discussion / fixed workflow].
The final output should be [report / file / email / decision recommendation].
If the workflow can be determined in advance, please generate a SwarmFlow workflow script.
```

Example:

```text
Help me create a Swarm Skill for technical proposal review.
The current single-Agent problem is that it often looks at proposals only from the implementer's point of view and lacks adversarial review.
I want the team to include an architect, an SRE, a security reviewer, and a product representative.
They should review independently first, then cross-examine each other, and finally a review chair should summarize risks and suggestions.
The final output should be organized as "blocking issues, important suggestions, optional optimizations, open questions".
```

If you want an executable workflow, be more explicit:

```text
Help me create a SwarmFlow for batch reading papers and generating internal sharing emails.
The workflow is fixed: parse papers in parallel -> summarize key points -> generate a sharing outline -> draft the email.
Please generate an executable workflow.py.
```

The key is not to say "help me build a powerful team". You need to explain where a single Agent is insufficient and how the team should compensate.

---

## The Team mode can also suggest accumulation based on trajectories

In addition to actively using `swarmskill-creator`, the system can also suggest Swarm Skill accumulation based on real Team execution trajectories.

This corresponds to "a successful collaboration that has been used once grows into a reusable team".

For example, when you run a short-video creation task for the first time, there may be no existing Swarm Skill. The Leader temporarily assembles a team:

- topic planning role
- script writing role
- storyboard role
- visual style role
- title copy role

After the task finishes, the system may recognize that this collaboration was not a one-off chat, but a reusable team pattern. It may then ask:

```text
I detected a multi-Agent collaboration pattern that may be worth capturing as a team Skill. Create it?

Options:
- Create
- Skip
- Custom instruction: describe how you want this team Skill captured
```

Only after you choose "Create" or provide a custom instruction will the system call `swarmskill-creator` and generate a new Swarm Skill from the current team trajectory.

This is similar to automatic Agent Skill creation, but the object is different:

- Automatic Agent Skill creation captures a reusable working pattern for a single Agent.
- Automatic Swarm Skill creation captures a reusable collaboration pattern for multiple Agents.

In the current Team mode, automatic accumulation suggestions mainly focus on whether real multi-member collaboration occurred and whether the task already used an existing team Skill. If the execution already used an existing Swarm Skill, the system will avoid suggesting a duplicate.

If you want to enable this capability, turn on "auto-suggest new Skill creation" in the JiuwenSwarm configuration page. The exact configuration entry point may change across versions, so the product configuration page should be preferred.

What is discussed here is "creating a new Swarm Skill from a successful team collaboration after user confirmation". It does not modify an existing Skill. How an existing Skill or Swarm Skill keeps improving belongs to the online self-evolution mechanism.

---

## When should you not use a Swarm Skill?

Swarm Skills are powerful, but not every task should use a team.

Do not use one in the following situations:

- A single Agent Skill can already complete the task reliably.
- The task does not have obvious role division, and you only want "more Agents to make it smarter".
- Subtasks are tightly coupled and hard to parallelize, and there are no clear stage handoffs.
- The task is too small and the collaboration cost outweighs the benefit.
- You have not yet run through one effective workflow; capturing it too early will freeze the wrong collaboration pattern.

A practical rule is:

> If you cannot clearly say why one Agent is not enough, do not create a Swarm Skill yet.

You can first run the task with a normal Agent or Agent Skill, and then decide whether team-ification is really needed.

---

## The relationship between Swarm Skill, Agent Skill, and SwarmFlow

You can understand these three concepts like this:

### Agent Skill: one person's playbook

It answers:

> What should one Agent do when it encounters a certain type of task?

It is suited for single-role, stable workflows, and professional knowledge capture.

### Swarm Skill: a team's battle manual

It answers:

> What should multiple Agents do when they encounter a certain complex task?

It is suited for multi-viewpoint tasks, parallel decomposition, stage handoffs, and team collaboration.

### SwarmFlow: the executable orchestration inside team collaboration

It answers:

> If the collaboration flow can be determined in advance, can the system execute it reliably?

It is suited for deterministic workflows with clear steps, parallel branches, pipelines, and observable stages.

So SwarmFlow is not a brand-new concept standing beside Swarm Skill. It is the part of Swarm Skill responsible for executable orchestration.

---

## A complete example: from a temporary team to a Swarm Skill

Suppose the first time you ask JiuwenSwarm to do "multi-paper technical research" you say:

```text
Help me research the latest 5 papers on Agent self-evolution,
summarize the technical approaches, experimental weaknesses, and implications for openJiuwen,
and finally write a sharing email suitable for our R&D colleagues.
```

During the first run, the Leader may assemble multiple members temporarily:

- paper retriever: finds related papers
- method analyst: breaks down the technical approach
- experiment auditor: checks experimental weaknesses
- engineering translator: extracts implications for openJiuwen
- email writer: generates the sharing email

If this collaboration works, you should not rebuild the team from scratch every time afterward.

You can proactively say:

```text
Based on this collaboration, help me create a Swarm Skill.
It will be used for multi-paper technical research and internal sharing email generation.
Keep the paper retriever, method analyst, experiment auditor, engineering translator, and email writer roles.
If suitable, generate SwarmFlow for the fixed workflow.
```

If automatic creation suggestion is enabled, the system may also ask whether to capture the collaboration after the Team task finishes.

After creation, next time you only need to say:

```text
Use the multi-paper research team to analyze the latest Agent Memory paper.
```

The Team mode can then load the team directly instead of reinventing the workflow.

That is the core value of Swarm Skills:

> Turn one successful team collaboration into a team capability that can be called directly next time.

---

## FAQ

### Q1: What is the difference between a Swarm Skill and a normal Agent Skill?

A normal Agent Skill targets a single Agent and captures professional knowledge and workflows.

A Swarm Skill targets a multi-Agent team and captures role division, collaboration flow, constraint rules, and tool dependencies.

### Q2: Do I need to hand-write `roles/`, `workflow.md`, and `bind.md`?

No.

It is recommended to use `swarmskill-creator` first. You only need to describe the task goal, role needs, and collaboration style. It will generate the files and validate them.

### Q3: Do all Swarm Skills need `workflow.py`?

No.

If the task needs open discussion, cross-examination, or live negotiation, keeping the Markdown team collaboration spec is more appropriate.

Only when the collaboration flow can be determined in advance do we recommend generating a SwarmFlow script.

### Q4: Should I create a Swarm Skill directly, or run one Team first?

Both approaches are valid.

If you already know the team structure and workflow, you can ask `swarmskill-creator` to create it directly.

If you are still unsure how to divide the work, let Team mode collaborate once first. After that works, capture the trajectory as a Swarm Skill.

### Q5: Why might the system skip automatic creation suggestion?

Common reasons include:

- The current task did not form a multi-member collaboration.
- An existing Swarm Skill was already used, so there is no need to create a duplicate.
- The collaboration pattern is too temporary to be reusable.
- The user did not confirm creation.

The goal of automatic creation suggestion is not to save every task, but to identify truly reusable team patterns and capture them only after user confirmation.

---

## Related topic

The online self-evolution mechanism for Skills focuses on:

> When a Skill or Swarm Skill already exists, how can it keep improving in real use?

It involves the patch mechanism, user confirmation, experience records, lifecycle management, and why openJiuwen self-evolution is not "secretly rewriting files", but a transparent, controllable, and reversible optimization loop.
