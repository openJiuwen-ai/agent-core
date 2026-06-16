# Agent Skills: Let Agents Capture Your Expertise

When people first use openJiuwen or JiuwenSwarm, the most common questions are usually not "Is the model strong enough?", but:

- What capabilities can I give the Agent?
- How do I pass my own working methods to the Agent?
- Why do I need to explain the same thing every time?
- After the Agent has completed a complex workflow once, can it reuse it next time?

Agent Skills solve this problem.

It is not a heavy plugin system, and it is not a development framework that requires users to write code. More precisely, Agent Skills are a lightweight, open capability packaging format: put expertise, operating steps, scripts, templates, and reference materials into a standard folder so the Agent can load and execute them when needed.

In one sentence:

> An Agent Skill is a "professional playbook + reusable resource pack" for an Agent. It turns a one-off experience into a capability that can be called again and again.

---

## Fastest start: you do not need to write a Skill by hand

The shortest path to using Agent Skills is not to write `SKILL.md` from scratch, but to let `skill-creator` build it for you.

You only need to tell it what capability you want to capture, when that capability should trigger, and what preferences and boundaries apply during execution.

For example:

```text
Help me create a Skill for generating a weekly project status report.
It should first organize this week's completed work, risks, and next week's plan,
and then output them in the structure "progress, risks, dependencies, next week's focus".
The tone should fit an engineering manager audience.
```

Or:

```text
Help me create a Skill for handling invoice Excel files.
Before writing any file, it must confirm the invoice title, amount, tax rate, date, and import-system fields.
If any field is missing, ask me first and do not guess.
```

The usual process looks like this:

1. You describe the capability in natural language.
2. `skill-creator` decides whether the request is suitable for a Skill.
3. It generates the standard Skill folder, including the entry file and required resources.
4. You confirm or supplement the requirements.
5. The Skill is saved to the skills directory.
6. Next time a related task appears, the Agent decides whether to use it based on the Skill description.

So what users really need to learn is not "how to hand-write a folder", but "how to explain their working method clearly".

---

## What is an Agent Skill?

An Agent Skill is a lightweight, open format used to extend an AI Agent with professional knowledge and procedural workflows.

The core of a Skill is a folder containing a `SKILL.md` file. That file contains metadata - name and description are required - as well as instructions telling the Agent how to execute a specific task. Skills can also bundle scripts, templates, and references.

A typical Skill directory looks like this:

```text
my-skill/
├── SKILL.md          # Required: instructions + metadata
├── scripts/          # Optional: executable code
├── references/       # Optional: documentation
└── assets/           # Optional: templates, resources
```

You only need to understand this structure. In practice, it is recommended to let `skill-creator` generate it automatically.

Among them:

- `SKILL.md` is the entry file and must exist. It tells the Agent what this Skill is, when to use it, and how to do the task.
- `scripts/` contains executable scripts, which are suitable for deterministic, fixed-step, and error-prone operations.
- `references/` contains longer background material, API docs, and specification notes, and is only read when needed.
- `assets/` contains templates, sample files, table styles, prompt fragments, and other directly reusable resources.

A Skill can be light or deep.

In the lightweight case, it may only have a single `SKILL.md`, used to tell the Agent which fields to confirm when handling invoice files. In a more complex case, it may include scripts, templates, validation rules, and long documents to help the Agent complete an entire workflow reliably.

---

## Why not put everything into the prompt?

Because prompts keep getting longer, while the information the Agent actually needs usually takes only a small part of that space.

If all domain knowledge, operating rules, error handling, and templates are stuffed into the system prompt, it may look convenient in the short term, but it creates three problems over time:

- Irrelevant content crowds out the context, making the Agent slower and less focused.
- Different tasks interfere with each other. A task that only needs table processing ends up reading a bunch of PowerPoint rules.
- Knowledge becomes hard to maintain. Changing one workflow note can affect every task.

The core design of Agent Skills is "load on demand", which means progressive disclosure.

Skills use progressive disclosure to manage context efficiently. This keeps the Agent fast while still allowing very deep context when needed.

### Discovery

At startup, the Agent loads only the name and description of each available Skill, just enough to know when it might be relevant.

For example:

```yaml
name: invoice-xlsx
description: Handle invoice Excel files; use when generating, validating, or fixing invoice sheets, and confirm field, amount, and tax rate requirements before writing.
```

At this point, the Agent does not read the full `SKILL.md`, and it definitely does not pull all reference documents into context.

### Activation

When the task matches a Skill description, the Agent autonomously decides to load the full `SKILL.md` instructions into the current context.

For example, the user says:

```text
Help me organize these invoices and generate an Excel file that can be imported into the system.
```

The Agent matches this against `invoice-xlsx`, so it loads the full Skill and reads the field confirmation rules, spreadsheet generation steps, common error cases, and output format requirements.

### Execution

The Agent works according to the instructions and can load referenced long documents or execute bundled code scripts as needed.

For example:

- If `SKILL.md` says "see `references/schema.md` for complex field mapping", the Agent only reads that file when field mapping is actually needed.
- If `scripts/validate_invoice.py` can validate the invoice format, the Agent can execute the script directly instead of reading the whole script into context.
- If `assets/template.xlsx` is the standard template, the Agent can generate output based on that template instead of guessing the format again.

That is the main advantage of Agent Skills: light most of the time, deep when necessary.

---

## What scenarios are worth turning into Agent Skills?

Not every task needs a Skill.

If a task is done only once, or can be reliably completed from a single natural-language instruction, there is no need to capture it. The value of a Skill lies in reuse and stability.

Create a Skill when you run into the following situations.

### You keep repeating the same requirements

Examples:

- Every time you generate a PPT, you need to explain the company template, chapter order, and chart style.
- Every time you handle Excel files, you need to remind the Agent about field validation, naming rules, and the output directory.
- Every time you write technical research, you need to emphasize the structure for summary, competitors, risks, and recommendations.

These are not one-off needs. They are your working habits. Once they are captured as a Skill, the Agent will follow them automatically in the future.

### The task has a stable operating procedure

Examples:

- Download files -> parse fields -> validate format -> generate report.
- Collect materials -> deduplicate -> categorize -> write email.
- Read logs -> locate the error -> reproduce the issue -> provide a fix suggestion.

These workflows are a great fit for Skills because they are not just knowledge, but executable work procedures.

### The task depends on professional knowledge or team conventions

Examples:

- Legal contract review standards.
- Data analysis conventions.
- Paper reading report format.
- Code review focus areas.
- Internal system API calling conventions.

These often are not in the model's general knowledge, or they may not match your organization's preferences even if the model knows them. A Skill can calibrate "general model capability" to "your working method".

### The task depends on scripts, templates, or reference materials

If a task needs fixed scripts, template files, or long specification documents, a Skill is a better fit than a prompt.

A prompt is good at telling the Agent how to think. A Skill can also tell the Agent where to look, which script to run, and which template to use.

---

## How do you use `skill-creator` to create a good Skill?

The value of `skill-creator` is not that it "generates a file for you", but that it helps turn vague experience into reusable capability.

You can describe your request with this template:

```text
Help me create a Skill for [task scenario].
It should be used when [trigger condition].
It must follow [steps / rules / preferences] during execution.
If it encounters [edge case], it should [handling method].
The output format is [expected result].
```

Example:

```text
Help me create a Skill for paper reading reports.
It should be used when I ask the Agent to read a paper, summarize a paper, or generate conference insights.
During execution it should first identify the paper's contribution, then analyze the method, experiments, innovations, and reusable ideas.
If the experiments are weak, it should explicitly call out where reviewers are likely to attack.
The output should be in Chinese and include: one-sentence conclusion, core innovation, method breakdown, experimental weaknesses, and reusable ideas.
```

Two things matter most for a high-quality Skill:

### 1. The description must help the Agent trigger correctly

Not recommended:

```yaml
description: Handle papers.
```

Recommended:

```yaml
description: Use when the user asks to read, summarize, critique, or generate a Chinese paper-reading report; focus on the paper's contribution, method, experimental weaknesses, review risks, and reusable ideas.
```

The first version is just a label. The second version contains trigger conditions and behavioral boundaries.

### 2. The instructions should read like an operating manual, not a marketing copy

Not recommended:

```text
You should handle user requests carefully and ensure output quality.
```

Recommended:

```text
When handling paper reading tasks:
1. First judge the core problem the paper tries to solve in one sentence.
2. Then break down the method, including inputs, key modules, and the training or inference flow.
3. List the parts of the experiments that may be challenged separately.
4. Finally, state the points that are worth borrowing for openJiuwen or Agent self-evolution research.
```

Skills are for Agents to execute, not for humans to read as promotional copy. The more executable they are, the more stable they become.

---

## The system can also suggest creating a Skill during use

Some Skills are not designed upfront. They emerge from real tasks.

For example, you ask the Agent to complete a complex research task. It searches for material, filters papers, organizes viewpoints, and generates an email. The execution itself has reuse value. Next time you do a similar research task, you should not start from scratch.

openJiuwen can observe the task trajectory during use and decide whether there is a reusable pattern worth capturing. If you enable "auto-suggest new Skill creation", the system will ask you at the right moment:

```text
I detected a reusable pattern in the current task, and it may be worth creating a new Skill. Create it?

Options:
- Create
- Skip
- Custom instruction: describe how you want this Skill to be captured
```

Only after you choose "Create" or provide a custom instruction will the system call `skill-creator` and generate a new Skill using the current conversation context and execution trajectory.

This is important:

> Automatic creation suggestion does not silently modify your workspace in the background. It is "system finds the opportunity -> user confirms -> skill-creator creates it".

If you want to enable this capability, turn on "auto-suggest new Skill creation" in the JiuwenSwarm configuration page. In the default configuration file, the corresponding capability is located at `react.evolution.skill_create`:

```yaml
react:
  evolution:
    skill_create: true
```

The environment variable `SKILL_CREATE=true` overrides the configuration file. The exact configuration entry point may change across versions, so the product configuration page should be treated as the primary source.

---

## What is the difference between automatic creation and online self-evolution?

These two concepts are easy to mix up, but the boundary is clear.

### Skill automatic creation: from zero to one

When the system discovers that a task has produced a reusable workflow but there is no suitable Skill yet, it suggests creating a new Skill.

It answers:

> Should this newly learned pattern become a new capability?

### Skill online self-evolution: make an existing Skill better

When an existing Skill shows failure, drift, duplicate experience, or cleanup needs in real use, the system uses trajectory and user feedback to generate improvement records.

It answers:

> Should this existing Skill be fixed, supplemented, or reorganized?

This section discusses Agent Skill creation and use. The patch mechanism, user confirmation, experience lifecycle, and rebuild flow of online self-evolution belong to the topic of "how an existing Skill keeps improving over time".

---

## A complete example: capturing technical research as a Skill

Suppose you often ask the Agent to help with "technical research emails".

The first time, you can ask the Agent to complete the task directly:

```text
Help me research the latest papers and open-source projects on Agent self-evolution,
and organize the findings into a technical sharing email suitable for our R&D colleagues.
```

The Agent may search for materials, shortlist papers, organize viewpoints, and generate the email. If the workflow is likely to be reused later, you can say to `skill-creator`:

```text
Based on this task, help me create a Skill.
It will be used for research emails before technical sharing sessions.
The output should include: background, key papers, trend analysis, risks, and discussion questions suitable for the team.
The email tone should fit an R&D audience.
```

If automatic creation suggestion is enabled, the system may also ask proactively whether to create a Skill after it recognizes a reusable workflow.

After creation, next time you say:

```text
Help me prepare a research email about multi-Agent collaboration.
```

The Agent can match this Skill during discovery and execute the captured workflow.

The final result is:

> You do not need to teach the Agent the same method every time. It captures a workflow that worked and reuses it next time.

---

## FAQ

### Q1: Do I need to learn how to write `SKILL.md` first?

No.

The recommended approach is to start with `skill-creator`. You only need to describe the goal, scenario, and preferences. If you want to optimize the result later, you can then edit `SKILL.md`.

### Q2: Will a Skill make the Agent slower?

A well-designed Skill should not significantly slow the Agent down.

That is because the Agent only loads the name and description at startup. The full `SKILL.md`, reference materials, and scripts are loaded on demand. What really slows things down is stuffing a large amount of irrelevant information into the system prompt, not using Skills.

### Q3: When should I not create a Skill?

Do not create one when:

- The task is only done once.
- A single sentence can complete it reliably.
- It is only temporary information with no reuse value.
- An existing Skill already covers it.
- The workflow has not been validated yet, and capturing it now would freeze the wrong behavior.

### Q4: Will automatic Skill creation suggestion create things randomly?

No silent creation happens.

The system only makes suggestions based on the execution trajectory. A real Skill is created only after the user confirms, and then `skill-creator` generates it.

### Q5: Should I aim for a big all-in-one Skill?

Not recommended.

It is better to create a small and precise Skill first, one that covers a high-frequency, well-defined, reusable task. You can then enrich it gradually with real usage experience.

The value of Agent Skills is not that you write a perfect document in one shot. The value is that you build a capability carrier that can keep accumulating experience.

It turns the Agent from "re-understanding the task every time" into "gradually mastering your working method".

---

## Related topic

Swarm Skills focus on:

> When a Skill is no longer just a single Agent's operating manual, but a reusable collaboration capability package for a multi-role team, how should it be created, used, and accumulated?

Related content includes `swarmskill-creator`, trajectory accumulation in Team mode, and the two forms of collaboration: open collaboration and SwarmFlow.
