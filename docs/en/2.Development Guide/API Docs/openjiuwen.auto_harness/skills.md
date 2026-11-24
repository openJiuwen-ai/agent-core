# openjiuwen.auto_harness.skills

Auto Harness skill reference. Each skill is a `SKILL.md` prompt file defining behavioral specifications and workflow constraints for auto-harness stage agents. Skill files are loaded by agents at runtime and injected into the LLM context as part of the system prompt.

All skill files are marked `immutable: true` and cannot be modified by agents at runtime.

---

## assess

| Attribute | Value |
|---|---|
| **Name** | `assess` |
| **Description** | Assessment methodology — execute codebase health assessment or runtime extension capability gap assessment based on the assessment mode in the query |
| **Triggers** | `repository_health_assessment`, `runtime_extension_gap_assessment` |
| **Tools** | read_file, glob_tool, grep_tool, list_dir, experience_search |

Selects different assessment methods based on assessment mode: codebase health assessment is suitable for optimizing auto-harness itself, enhancing CLI, fixing pipelines, etc.; Runtime Extension capability gap assessment analyzes the gap between user target capabilities and currently available capabilities.

---

## commit

| Attribute | Value |
|---|---|
| **Name** | `commit` |
| **Description** | Autonomous commit flow based on commit skill. Suitable for the implement stage to plan scope before commit and complete git commit via bash |
| **Triggers** | commit, git add, git commit, commit changes |
| **Tools** | read_file, glob_tool, grep_tool, bash_tool |

Defines the fixed workflow for the commit stage: read git status → narrow commit scope → generate commit message → `git add` explicit files → `git commit` → post-commit self-check. Prohibits mixing in old dirty files and files outside the current task.

---

## communicate

| Attribute | Value |
|---|---|
| **Name** | `communicate` |
| **Description** | Communication specification — constrains the expression of commit messages, PRs, journals, and help requests |
| **Triggers** | commit message, PR description, journal, communication, expression |
| **Tools** | bash_tool, experience_search |

Defines writing specifications for technical communication content, including conventional commits format commit messages, PR description templates, journal entry format, and standard expression for help requests.

---

## design_ext

| Attribute | Value |
|---|---|
| **Name** | `design_ext` |
| **Description** | Extension design — convert capability gaps into ExtensionDesign structures |
| **Triggers** | extension design, ExtensionDesign, gap conversion, capability gap |
| **Tools** | read_file, glob_tool, grep_tool, experience_search, bash_tool |

Convert capability gaps from GapAnalysisArtifact into executable runtime extension plans. Read-only stage, no file modifications allowed. Prefers reusing community skills; checks for matching community skills to set `skill_source='community:<skill_name>'`; only designs from scratch when no match is found.

---

## implement

| Attribute | Value |
|---|---|
| **Name** | `implement` |
| **Description** | Implementation stage playbook — guide the agent through code changes and local validation, leaving commit to the independent commit phase |
| **Triggers** | implement, code changes, code modification, implement, local validation |
| **Tools** | read_file, write_file, edit_file, glob_tool, grep_tool, bash_tool, experience_search |

Guide the agent to complete a single optimization task within strict scope. Fixed workflow: understand task → gather context → minimal changes → local validation → check change facts → generate commit plan → stop in uncommitted state. Prohibits executing git commit/push and other commit actions.

---

## implement_ext

| Attribute | Value |
|---|---|
| **Name** | `implement_ext` |
| **Description** | Extension implementation stage — generate runtime extension code in worktree |
| **Triggers** | extension implementation, runtime extension, implement_ext, worktree |
| **Tools** | read_file, write_file, edit_file, glob_tool, grep_tool, bash_tool, experience_search |

Generate runtime extension code in an isolated worktree. Strictly implements according to ExtensionDesign's components, does not auto-add undeclared components. Supports community skill reuse (skips skill creation when `skill_source='community:<skill_name>'`). Includes dependency identification and requirements.txt generation, code generation, manifest generation, and local syntax validation.

---

## plan

| Attribute | Value |
|---|---|
| **Name** | `plan` |
| **Description** | Planning specification — converge assessment results into a structured task plan |
| **Triggers** | planning, task plan, plan, optimization plan |
| **Tools** | read_file, glob_tool, grep_tool, experience_search, bash_tool |

Convert assessment facts into executable tasks. Fixed workflow: read assessment report → check experience → converge to single highest-priority task → clarify scope and files. Currently fixed to `extended_evolve_pipeline`, only allows outputting 1 task per round, each task involving at most 3 source files.

---

## select_pipeline

| Attribute | Value |
|---|---|
| **Name** | `select_pipeline` |
| **Description** | Pipeline selection specification — select the most suitable pipeline based on task and facts |
| **Triggers** | pipeline selection, pipeline, select_pipeline |
| **Tools** | read_file, experience_search, bash_tool |

Pipeline selection agent. Current strategy is fixed to select `extended_evolve_pipeline`, prioritizing producing isolatable runtime extensions. `fallback_pipeline` must also be `extended_evolve_pipeline`.

---

## verify

| Attribute | Value |
|---|---|
| **Name** | `verify` |
| **Description** | Verification specification — define the verification level and pass criteria that the implementation stage should satisfy |
| **Triggers** | verify, verify, lint, type-check, test |
| **Tools** | read_file, bash_tool, glob_tool, grep_tool |

Define verification levels and pass criteria for code changes. 4 levels by change scope: L1 (single file lint + unit test) → L2 (multi-file + type check) → L3 (cross-module + full test suite) → L4 (public API change + example validation).

---

## verify_ext

| Attribute | Value |
|---|---|
| **Name** | `verify_ext` |
| **Description** | Runtime extension verification specification — verify that tools, rails, and skills in the harness package can truly hot-load and run |
| **Triggers** | extension verification, verify_ext, runtime extension verification, hot-load verification |
| **Tools** | read_file, bash_tool, glob_tool, grep_tool |

Verify that the generated runtime extension can be registered, observed, and invoked in the real harness loading path. Verification has 3 layers: L1 structure check (manifest schema, module path, ToolCard construction) → L2 temporary hot-load (DeepAgent.load_expert_harness verify registration) → L3 runtime acceptance (Tool invoke, Rail side effects, Skill loading, file artifact format validation).
