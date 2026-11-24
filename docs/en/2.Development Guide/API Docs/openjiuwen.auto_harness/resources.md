# openjiuwen.auto_harness.resources

Auto Harness resource configuration template reference. Contains two YAML configuration file templates: `config.yaml` (main configuration) and `ci_gate.yaml` (CI gate configuration). These files define the behavioral parameters of the auto-harness runtime; they are not Python APIs but deployment and operations-level configuration references.

---

## config.yaml

Main configuration file template. Placement location is determined by the host CLI:
- openjiuwen CLI → `~/.openjiuwen/auto_harness/config.yaml`
- jiuwenclaw → `~/.jiuwenclaw/auto_harness/config.yaml`

### Full structure

```yaml
# Optional: Local agent-core repository path
# When configured, uses git worktree for acceleration; auto-clones to data_dir/repo/ if not configured
# local_repo: "/home/user/code/agent-core"

# Optional: Remote repository URL
# repo_url: "https://gitcode.com/openJiuwen/agent-core.git"

# Optional: Session pipeline. auto selects by goal signal, meta/extended forces specific pipeline
# pipeline: "auto"

# Optional: Agent prompt and PR template language. Supports "cn" (Chinese) or "en" (English)
# language: "cn"

# Optional: Custom skills directories, merged with framework built-in skills
# skills_dirs:
#   - "/path/to/my-skills"

# Optional: Override non-editable files; uses framework built-in defaults when empty
# immutable_files:
#   - "openjiuwen/auto_harness/prompts/identity.md"

# Optional: Experience store directory
# experience_dir: "/home/user/.openjiuwen/auto_harness/experience"

git:
  remote: ""                    # Fork remote name
  base_branch: "develop-auto-harness"        # PR target branch
  user_name: ""                 # Git commit username
  user_email: ""                # Git commit email
  fork_owner: ""                # Fork owner
  upstream_owner: "openJiuwen"  # Upstream repository owner
  upstream_repo: "agent-core"   # Upstream repository name

gitcode:
  username: ""                                 # GitCode login username; falls back to git.fork_owner when empty
  access_token_env: "GITCODE_ACCESS_TOKEN"    # Read token from this environment variable

budget:
  session_secs: 900000           # Session total budget (seconds)
  cost_limit_usd: 10.0          # Cost limit (USD)
  task_timeout_secs: 300000      # Single task timeout (seconds)
  model_timeout_secs: 300000     # Single LLM request timeout (seconds)
  max_tasks_per_session: 5      # Maximum tasks per session

ci_gate:
  config_path: ""               # CI gate configuration path, uses repo default when empty
  python_executable: ""         # Fixed Python for CI gate; falls back to .venv or current interpreter when empty
  install_command: ""           # Optional environment warmup command

fix_loop:
  phase1_max_retries: 10        # Phase 1 max retries
  phase2_max_retries: 9         # Phase 2 max retries

agent:
  implement: 60                 # Implement agent max_iterations
  assess: 60                    # Assess agent max_iterations
  plan: 60                      # Plan agent max_iterations
  select_pipeline: 20           # Pipeline selector max_iterations
  eval: 20                      # Evaluator max_iterations
  pr_draft: 20                  # PR draft agent max_iterations
  learnings: 20                 # Learnings agent max_iterations

extensions:
  stage_registrars: []          # module:callable, signature (StageRegistry) -> None
  pipeline_registrars: []       # module:callable, signature (PipelineRegistry, StageRegistry) -> None
```

### Section descriptions

| Section | Description |
|---|---|
| **Top-level optional fields** | `local_repo`, `repo_url`, `pipeline`, `language`, `skills_dirs`, `immutable_files`, `experience_dir` and other global configurations |
| **git** | Git remote repository configuration, including fork remote, target branch, user info, upstream/downstream repository owners |
| **gitcode** | GitCode platform authentication configuration, `username` falls back to `git.fork_owner` when empty, token read from environment variable |
| **budget** | Resource budget control, including session total duration, cost limit, single task timeout, LLM request timeout, maximum task count |
| **ci_gate** | CI gate configuration, including custom configuration path, Python interpreter path, environment warmup command |
| **fix_loop** | Fix loop configuration, Phase 1 (CI fix) and Phase 2 (review fix) max retry counts |
| **agent** | `max_iterations` upper limits for each agent, controlling agent iteration count per stage |
| **extensions** | Extension registration points, register custom stages and pipelines via `module:callable` format |

---

## ci_gate.yaml

CI gate rules configuration file template. This file is protected by `ImmutableFileRail` and cannot be modified at runtime.

### Full structure

```yaml
# CI Gate Rules — Auto Harness Agent
# IMMUTABLE: This file is protected by ImmutableFileRail

immutable_files:
  - auto_harness/prompts/identity.md
  - auto_harness/resources/ci_gate.yaml
  - auto_harness/agent.py
  - auto_harness/orchestrator.py
  - auto_harness/infra/fix_loop.py
  - openjiuwen/harness/rails/security/prompt_security_rail.py

high_impact:
  - "openjiuwen/core/**"

requires_human_approval:
  - pyproject.toml
  - openjiuwen/harness/__init__.py
  - openjiuwen/harness/factory.py
  - openjiuwen/core/__init__.py

ci_gates:
  - name: lint
    command: "make check COMMITS=1"
    required: true
  - name: type-check
    command: "make type-check COMMITS=1"
    required: true

fix_loop:
  phase1_max_retries: 10
  phase1_timeout_per_attempt: 600
  phase2_max_retries: 9
  revert_on_exhaustion: true

session_budget:
  wall_clock_secs: 3600
  cost_limit_usd: 10.0
  task_timeout_secs: 1200

task_constraints:
  max_tasks_per_session: 10
  max_files_per_task: 3
  self_driven_slots: 1
```

### Section descriptions

| Section | Description |
|---|---|
| **immutable_files** | Non-editable file list. These files are prohibited from being modified by the agent during auto-harness execution, intercepted by `ImmutableFileRail` |
| **high_impact** | High-impact scope glob patterns. File changes matching these paths are marked as high-impact, potentially triggering stricter review |
| **requires_human_approval** | Files requiring human approval. Auto-harness pauses and waits for human confirmation when modifying these files |
| **ci_gates** | CI gate definitions. Each gate contains `name` (name), `command` (execution command), `required` (whether it must pass) |
| **fix_loop** | Fix loop parameters. `phase1_max_retries`: CI fix max retries; `phase1_timeout_per_attempt`: Timeout per attempt (seconds); `phase2_max_retries`: Review fix max retries; `revert_on_exhaustion`: Whether to rollback when retries are exhausted |
| **session_budget** | Session-level budget. `wall_clock_secs`: Total wall clock time (seconds); `cost_limit_usd`: Cost limit (USD); `task_timeout_secs`: Single task timeout (seconds) |
| **task_constraints** | Task constraints. `max_tasks_per_session`: Maximum tasks per session; `max_files_per_task`: Maximum files per task; `self_driven_slots`: Self-driven slot count |
