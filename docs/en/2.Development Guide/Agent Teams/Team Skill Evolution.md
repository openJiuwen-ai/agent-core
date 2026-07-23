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
| `TeamSkillRail` | Public rail for online evolution: trajectory analysis, user request, aggregated experience record generation/approval. It is the compatibility public alias for `TeamSkillEvolutionRail`. |
| `TeamSkillExperienceOptimizer` | LLM-driven experience record generation. `TeamSkillOptimizer` remains available as a compatibility alias. |
| `ExperienceScorer` | Experience scoring and simplify maintenance |
| `InMemoryTrajectoryRegistry` | Runtime source/sink for publishing member snapshots and aggregating team trajectory evidence |
| `TeamTrajectoryAggregator` | Offline/debug aggregation utility for stored member trajectories |

---

## Auto Creation

### Principle

`TeamSkillCreateRail` waits until the team task is completed, then checks whether the run spawned enough team members and did not already use an existing Team/Swarm Skill. When these gates pass, it injects a short follow_up to wake up the next round and injects system prompt text that guides the Agent to decide whether the collaboration pattern is reusable. If so, the Agent asks for confirmation in normal reply text. It must not use popup-style interaction tools for this confirmation.

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
2. Team completion callback marks the run as eligible for creation review
3. Rail detects `spawn_member` call count reaches threshold and no existing Team/Swarm Skill was used
4. Rail injects a short follow_up via `TaskLoopController.enqueue_follow_up()` and injects a system self-check prompt before the next model call
5. Agent asks in normal reply text whether to create a Team/Swarm Skill when it finds reusable value
6. User confirms or provides custom instructions
7. Agent invokes `swarmskill-creator` or a compatible team skill creator skill
8. Skill generates Team/Swarm Skill files based on the current team context

---

## Online Evolution

### TeamSkillRail Configuration

```python
from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryRegistry
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails import TeamSkillRail

trajectory_registry = InMemoryTrajectoryRegistry()

team_rail = TeamSkillRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    language="en",
    team_id="research-team",
    trajectory_source=trajectory_registry,
    trajectory_sink=trajectory_registry,
    auto_save=False,           # generated records require user approval
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

`trajectory_source` and `trajectory_sink` are the runtime integration points for team trajectory aggregation. Rails publish `MemberTrajectorySnapshot` values to the sink after invoke; `TeamSkillRail` reads the source when passive or user-requested evolution needs team-level evidence.

To aggregate multiple members, every rail or agent that should contribute evidence must publish to the same `trajectory_sink`; `TeamSkillRail` then reads that shared registry through `trajectory_source`.

`MemberTrajectorySnapshot` contains only published content: `team_id`, `session_id`, `member_id`, `member_role`, `trajectory`, and `recorded_at_ms`. Snapshot freshness ordering is owned by `InMemoryTrajectoryRegistry`: newer `recorded_at_ms` wins, and equal timestamps are resolved by the registry's receive order.

### Trigger Timing

| Trigger Method | Trigger Condition |
|----------------|-------------------|
| Passive trigger | `view_task` returns "all tasks completed" |
| Active trigger | User calls `request_user_evolution()` |

### Evolution Paths

#### 1. Trajectory Issue Analysis

Rail analyzes team execution context records, detecting:

- **Role coordination issues**: Collaboration breaks, data not passed
- **Constraint violations**: Timeout, output format issues
- **Workflow inefficiency**: Redundant calls, extra steps
- **Role capability gaps**: Repeated failures, poor output quality

When issues are detected, Rail aggregates signals and calls `TeamSkillExperienceOptimizer.generate_records(EvolutionContext)` to generate experience records.

#### 2. User Request Evolution

User actively provides improvement suggestions:

```python
result = await team_rail.request_user_evolution(
    skill_name="research-team",
    user_intent="Add reviewer role, limit research time to 10 minutes",
)
```

Rail first uses the current rail trajectory, or the aggregated team trajectory from `trajectory_source`, as the evidence window for trajectory issue detection. It then merges user intent as supplemental direction into the same `generate_records(EvolutionContext)` flow to generate experience records.

### Record Approval Flow

1. Rail generates experience records and stages them
2. The returned `approval_event` can be shown by the host UI
3. User selects "Accept" or "Reject"
4. On approval, call `approve_record(result.request_id)` to write to `evolutions.json`

```python
if result.approval_event:
    await team_rail.approve_record(result.request_id)
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
simplify_result = await team_rail.request_simplify(
    skill_name="research-team",
    user_intent="Clean up outdated experiences",
)

if simplify_result.approval_event:
    await team_rail.on_approve_simplify(simplify_result.request_id)

# The scorer-generated cleanup actions are applied after approval.
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

1. Execute `request_simplify()` weekly and approve the generated cleanup actions to clean low-quality experiences
2. Evaluate monthly whether `request_rebuild()` is needed
3. Set `auto_save=False` before important evolution to ensure user approval

### Notes

- `TeamSkillCreateRail` and `TeamSkillRail` can be used together
- Rails depend on DeepAgent with `enable_task_loop=True`
- Evolution-generated content requires user approval to take effect
