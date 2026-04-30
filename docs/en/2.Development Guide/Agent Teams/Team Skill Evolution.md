# Team Skill Evolution

Team Skill Evolution is a multi-agent collaboration skill auto-creation and online evolution feature in agent-core, extracting reusable collaboration patterns from execution trajectories.

## Core Concepts

### Team Skill

A Team Skill is a special type of Skill with the following file structure:

```
skills/<skill-name>/
├── SKILL.md          # Entry file (YAML frontmatter + Markdown body)
│                      # frontmatter must include kind: team-skill
├── roles/
│   ├── <role-id>.md  # Detailed definition for each role
│   └── ...
├── workflow.md       # Workflow description (task dependencies, parallel/sequential)
├── bind.md           # Constraints (round limits, timeouts, quality gates)
└── evolutions.json   # Evolution records
```

Unlike regular Skills, Team Skills:

- `kind: team-skill` identifies the type
- `roles` list defines roles and their skills/tools configuration
- Suitable for multi-agent collaboration scenarios

### Evolution Mechanism

| Module | Function |
|--------|----------|
| `TeamSkillCreateRail` | Auto-detect collaboration patterns, suggest team skill creation |
| `TeamSkillRail` | Online evolution: trajectory analysis, user request, PATCH generation/approval |
| `TeamSkillOptimizer` | LLM-driven PATCH generation |
| `ExperienceScorer` | Experience scoring and simplify maintenance |
| `TeamTrajectoryAggregator` | Aggregate team member trajectories |

---

## Auto Creation

### Principle

`TeamSkillCreateRail` detects `spawn_member` call count in `AFTER_TASK_ITERATION` callback. When threshold is reached, it injects a follow_up prompt guiding the Agent to confirm via `ask_user` and invoke `team-skill-creator` skill.

### Configuration Example

```python
from openjiuwen.harness.rails import TeamSkillCreateRail
from openjiuwen.harness import create_deep_agent

create_rail = TeamSkillCreateRail(
    skills_dir="/path/to/skills",
    language="en",
    min_team_members_for_create=2,  # trigger when spawn_member >= 2
)

agent = create_deep_agent(
    model=model_client,
    system_prompt="You are a team leader...",
    tools=team_tools,
    rails=[create_rail],
    enable_task_loop=True,
    workspace="/path/to/workspace",
)
```

### Creation Flow

1. Agent executes collaboration flow (calls `build_team`, `spawn_member`, `create_task`)
2. Rail detects `spawn_member` call count reaches threshold
3. Rail injects prompt via `TaskLoopController.enqueue_follow_up()`
4. Agent calls `ask_user` tool to confirm with user
5. User selects "Create", Agent invokes `team-skill-creator` skill
6. Skill generates Team Skill files based on trajectory

---

## Online Evolution

### TeamSkillRail Configuration

```python
from openjiuwen.harness.rails import TeamSkillRail

team_rail = TeamSkillRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    language="en",
    auto_save=False,           # PATCH requires user approval
    async_evolution=True,      # Execute evolution asynchronously
    evolution_total_timeout_secs=600.0,
)

agent = create_deep_agent(
    model=model_client,
    tools=team_tools,
    rails=[team_rail],
    skills=["research-team"],  # Load existing team skill
)
```

### Trigger Timing

| Trigger Method | Trigger Condition |
|----------------|-------------------|
| Passive trigger | `view_task` returns "all tasks completed" |
| Active trigger | User calls `request_user_evolution()` |

### Evolution Paths

#### 1. Trajectory Issue Analysis

Rail analyzes team execution trajectory, detecting:

- **Role coordination issues**: Collaboration breaks, data not passed
- **Constraint violations**: Timeout, output format issues
- **Workflow inefficiency**: Redundant calls, extra steps
- **Role capability gaps**: Repeated failures, poor output quality

When issues detected, Rail calls `TeamSkillOptimizer.generate_trajectory_patch()` to generate PATCH.

#### 2. User Request Evolution

User actively provides improvement suggestions:

```python
request_id = await team_rail.request_user_evolution(
    skill_name="research-team",
    user_intent="Add reviewer role, limit research time to 10 minutes",
)
```

Rail calls `TeamSkillOptimizer.generate_user_patch()` to generate PATCH.

### PATCH Approval Flow

1. Rail generates PATCH and stages it
2. PATCH is sent to TUI via `OutputSchema`
3. User selects "Accept" or "Reject"
4. On approval, calls `on_approve_patch(request_id)` to write to `evolutions.json`

```python
# Get pending approval events
events = await team_rail.drain_pending_approval_events(wait=True)

for event in events:
    if event.type == "chat.ask_user_question":
        request_id = event.payload["request_id"]
        # User approval
        await team_rail.on_approve_patch(request_id)
```

---

## Experience Management

### Scoring System

ExperienceScorer uses three-dimension weighted scoring:

| Dimension | Formula | Weight |
|-----------|---------|--------|
| E (Effectiveness) | Beta(1,1) Bayesian smoothing | 0.5 |
| U (Utilization) | `times_used / times_presented` | 0.3 |
| F (Freshness) | Time decay + version expiry penalty | 0.2 |

**Composite score**: `score = 0.5 * E + 0.3 * U + 0.2 * F`

### Simplify Operations

```python
result = await team_rail.request_simplify(
    skill_name="research-team",
    user_intent="Clean up outdated experiences",
)

# Returns: {"deleted": N, "merged": N, "refined": N, "kept": N, "errors": N}
```

| Operation | Description |
|-----------|-------------|
| DELETE | Remove low-quality experiences (score < threshold) |
| MERGE | Combine similar experiences |
| REFINE | Optimize verbose content |
| KEEP | Retain high-quality experiences |

### Rebuild Flow

```python
prompt = await team_rail.request_rebuild(
    skill_name="research-team",
    user_intent="Comprehensive optimization based on historical experiences",
    min_score=0.5,  # Only keep experiences with score >= 0.5
)

# prompt is rebuild instruction text, needs injection into Agent execution
```

Rebuild process:

1. Archive current `SKILL.md` and `evolutions.json` to `archive/`
2. Filter high-quality evolution experiences (score >= min_score)
3. Build rebuild prompt, invoke `teamskill-creator` skill to generate new version
4. Clear `evolutions.json`

---

## Best Practices

### When to Use

- Multi-agent collaboration scenarios (`spawn_member >= 2`)
- Need to capture collaboration experience, form reusable patterns
- Team skills require continuous improvement and optimization

### Configuration Recommendations

| Setting | Recommendation |
|---------|----------------|
| `min_team_members_for_create` | Adjust based on team size, suggest 2-3 |
| `auto_save` | For important evolution suggest `False`, ensure approval |
| `async_evolution` | For long tasks suggest `True`, avoid blocking |
| `evolution_total_timeout_secs` | Adjust based on task complexity, suggest 300-900 |

### Regular Maintenance

1. Execute `request_simplify()` weekly to clean low-quality experiences
2. Evaluate monthly whether `request_rebuild()` is needed
3. Set `auto_save=False` before important evolution to ensure user approval

### Notes

- `TeamSkillCreateRail` and `TeamSkillRail` can be used together
- Rails depend on DeepAgent with `enable_task_loop=True`
- Evolution-generated content requires user approval to take effect