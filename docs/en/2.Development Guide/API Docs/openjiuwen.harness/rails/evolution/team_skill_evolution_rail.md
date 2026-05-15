# Team Skill Evolution Rail

Team-skill creation and online evolution documentation.

---

## class TeamSkillCreateRail

Independent Rail that auto-detects multi-agent collaboration patterns and suggests team skill creation.

### Trigger Mechanism

- Detects `spawn_member` call count in `AFTER_TASK_ITERATION` lifecycle callback
- When count reaches threshold (default 2), injects follow_up via `TaskLoopController`
- After user confirmation, invokes `team-skill-creator` skill to execute creation

```text
class TeamSkillCreateRail(
    skills_dir: str,
    *,
    language: str = "cn",
    auto_trigger: bool = True,
    min_team_members_for_create: int = 2,
    trajectory_store: Optional[TrajectoryStore] = None,
)
```

**Parameters**:

* **skills_dir** (str): Skill directory path.
* **language** (str): Language setting, supports `"cn"` or `"en"`.
* **auto_trigger** (bool): Whether to auto-trigger, defaults to `True`.
* **min_team_members_for_create** (int): Trigger threshold, `spawn_member` call count reaching this value triggers, defaults to 2.
* **trajectory_store** (TrajectoryStore, optional): Trajectory store instance.

### Priority

`priority = 85`

---

## class TeamSkillRail

Public team skill evolution Rail, similar to `SkillEvolutionRail` but specialized for team skills.
`TeamSkillRail` is the compatibility public alias for `TeamSkillEvolutionRail`.
New team skill creation remains owned by `TeamSkillCreateRail`; this rail only evolves existing `kind: team-skill` skills.

### Features

- Trajectory issue detection (role coordination, constraint violations, workflow inefficiency)
- User-requested evolution
- Aggregated experience record generation and approval
- Experience simplify/rebuild

### Trigger Mechanism

- Monitors `view_task` tool result, detecting "all tasks completed"
- Supports passive trajectory analysis and active user request evolution paths
- `auto_scan=False` disables passive completion scanning and `notify_team_completed()` passive triggering.
- Passive evolution uses aggregated collaborative trajectory evidence. Team completion, team skill attribution, and runtime role attribution are heuristic host-bridge signals, not strong contracts.

```text
class TeamSkillRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    language: str = "cn",
    trajectory_store: Optional[TrajectoryStore] = None,
    team_trajectory_store: Optional[TrajectoryStore] = None,
    auto_scan: bool = True,
    auto_save: bool = False,
    async_evolution: bool = True,
    max_concurrent_evolution: int = 1,
    team_id: Optional[str] = None,
    trajectories_dir: Optional[Path] = None,
    user_request_llm_policy: LLMInvokePolicy = ...,
    trajectory_issue_llm_policy: LLMInvokePolicy = ...,
    record_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
    eval_interval: int = 5,
    evolution_total_timeout_secs: float = 600.0,
)
```

**Parameters**:

* **skills_dir** (Union[str, list[str]]): Skill directory path or path list.
* **llm** (Model): LLM client instance.
* **model** (str): Model name.
* **language** (str): Language setting.
* **trajectory_store** (TrajectoryStore, optional): Trajectory store instance.
* **team_trajectory_store** (TrajectoryStore, optional): Team trajectory store instance.
* **auto_scan** (bool): Whether to detect passive team completion and trigger passive evolution, defaults to `True`.
* **auto_save** (bool): Whether to auto-save generated experience records, defaults to `False` (requires user approval).
* **async_evolution** (bool): Whether to execute evolution asynchronously, defaults to `True`.
* **max_concurrent_evolution** (int): Max concurrent background evolution tasks, defaults to 1.
* **team_id** (str, optional): Team ID.
* **trajectories_dir** (Path, optional): Trajectory directory path.
* **user_request_llm_policy** (LLMInvokePolicy): User intent detection LLM invocation policy.
* **trajectory_issue_llm_policy** (LLMInvokePolicy): Trajectory issue detection LLM invocation policy.
* **record_llm_policy** (LLMInvokePolicy): Experience record generation LLM invocation policy.
* **evaluate_llm_policy** (LLMInvokePolicy): Experience evaluation LLM invocation policy.
* **simplify_llm_policy** (LLMInvokePolicy): Experience simplify LLM invocation policy.
* **eval_interval** (int): Number of presentations between experience scoring checks. Must be at least 1.
* **evolution_total_timeout_secs** (float): Background evolution total timeout budget, defaults to 600s.

### Priority

`priority = 80`

---

## Properties

### store -> EvolutionStore

Evolution store instance.

### scorer -> ExperienceScorer

Experience scorer.

### generator -> TeamSkillExperienceOptimizer

Team skill experience optimizer.

### evolution_config -> dict

Complete evolution configuration, including phase LLM invocation policies and timeout settings.

---

## Lifecycle and Contracts

The observable lifecycle matches regular skill evolution:

```text
team trajectory aggregated
-> team signals detected
-> local apply preview
-> pending approval or auto-approved
-> EvolutionStore persistence
-> evolutions.json and evolution/*.md projection
```

Stable ownership boundaries:

* `TeamSkillEvolutionRail` owns team-specific host bridge behavior: `view_task` completion detection, `notify_team_completed()`, team trajectory aggregation, and used team-skill detection.
* `OnlineEvolutionOrchestrator` coordinates context build, update generation, and local preview.
* `ExperienceManager + PendingChange` owns pending approval state.
* `EvolutionStore` owns durable writes and projection.

`EvolutionApprovalRuntime` is a rail-bound adapter over manager approval methods and pending snapshot lookup. It does not own approval state, and approval lifecycle should not be moved back into `EvolutionRail`.

### Host events

Use `drain_pending_host_events()` as the canonical API to consume evolution events. `drain_pending_approval_events()` is a compatibility wrapper over the same buffer.

Evolution metadata is carried in `OutputSchema.payload["_evolution_meta"]`:

| Field | Meaning |
|---|---|
| `event_kind` | `approval`, `progress`, or `outcome`. |
| `rail_kind` | Producing rail kind, usually `team-skill` for this rail. |
| `stage` | Lifecycle stage for progress or outcome events. |
| `skill_name` | Target team skill name. |
| `request_id` | Approval or governance request id. |
| `signal_type` | Signal type that contributed to the request. |
| `source` | Signal or event source. |
| `status` | Outcome status when available. |

Approval events use `type="chat.ask_user_question"` and include `payload["request_id"]`. Progress events use `type="llm_reasoning"`. Background failures are reported as outcome events and do not fail the main invoke.

### Snapshot and signal boundaries

Async snapshots contain `trajectory`, `messages`, and optionally `skill_name`. `messages` are detection context; `trajectory` is execution evidence. The current implementation keeps legacy dict compatibility, so hosts should treat rail methods and host events as public integration points instead of depending on the dict shape.

Team signal semantics are partly structured as `EvolutionSignal` fields and partly carried in `EvolutionSignal.context`. Runtime team member / role attribution remains heuristic; role summaries extracted from `SKILL.md` are documentation context, not runtime identity proof.

---

## Methods

### async notify_team_completed(ctx) -> bool

Trigger skill evolution (when all tasks complete).

**Parameters**:

* **ctx** (AgentCallbackContext, optional): Callback context.

**Returns**:

* `bool`: Whether evolution was successfully triggered.

---

### async request_user_evolution(skill_name, user_intent, *, auto_approve=False) -> Optional[str]

User-initiated evolution request.

**Parameters**:

* **skill_name** (str): Target skill name.
* **user_intent** (str): User improvement intent description.
* **auto_approve** (bool): Whether to auto-approve, defaults to `False`.

**Returns**:

* `str`: request_id or `None` (when skill not found or no records generated).

---

### async request_simplify(skill_name, user_intent=None) -> Optional[str]

Stage an experience simplification proposal and emit an approval event.

**Parameters**:

* **skill_name** (str): Target skill name.
* **user_intent** (str, optional): User simplification intent.

**Returns**:

* `str`: governance request id when actions were proposed, otherwise `None`.

Use `on_approve_simplify(request_id)` to execute and `on_reject_simplify(request_id)` to discard.

---

### async request_rebuild(skill_name, user_intent=None, min_score=0.5) -> Optional[str]

Request skill rebuild (archive old version and generate new version).

**Parameters**:

* **skill_name** (str): Target skill name.
* **user_intent** (str, optional): User rebuild intent.
* **min_score** (float): Evolution record filter threshold, defaults to 0.5.

**Returns**:

* `str`: Rebuild follow-up prompt text or `None` (when skill not found). The caller injects the returned prompt into the agent loop; the rail does not directly write the rebuilt `SKILL.md`.

---

### async approve_record(request_id) -> None

Approve staged experience records and write them to `evolutions.json`.

**Parameters**:

* **request_id** (str): Request ID.

---

### async reject_record(request_id) -> None

Reject staged experience records and clear the pending request.

**Parameters**:

* **request_id** (str): Request ID.

---

### async drain_pending_approval_events(wait=False, timeout=None) -> List[OutputSchema]

Compatibility wrapper for draining buffered host events.

**Parameters**:

* **wait** (bool): Whether to wait for events.
* **timeout** (float, optional): Wait timeout, defaults to `evolution_total_timeout_secs`.

**Returns**:

* `List[OutputSchema]`: Pending approval event list.

### async drain_pending_host_events(wait=False, timeout=None) -> List[OutputSchema]

Get and clear buffered host events. If `wait=True`, waits for pending background evolution tasks up to `timeout`.

**Parameters**:

* **wait** (bool): Whether to wait for events.
* **timeout** (float, optional): Wait timeout, defaults to `evolution_total_timeout_secs`.

**Returns**:

* `List[OutputSchema]`: Pending evolution host events.

---

## Helper Types

### class TeamSignalType

Evolution signal type enum:

* `USER_REQUEST`: User-initiated evolution request
* `TRAJECTORY_ISSUE`: Trajectory issue detection triggered evolution

### class UserIntent

User intent dataclass:

* `is_improvement` (bool): Whether improvement intent
* `intent` (str): Intent description

### class TrajectoryIssue

Trajectory issue dataclass:

* `issue_type` (str): Issue type
* `description` (str): Issue description
* `affected_role` (str): Affected role
* `severity` (str): Severity (`"low"` | `"medium"` | `"high"`)

---

## Example

```python
from openjiuwen.harness.rails import TeamSkillCreateRail, TeamSkillRail
from openjiuwen.harness import create_deep_agent

# Create team skill creation rail
create_rail = TeamSkillCreateRail(
    skills_dir="/path/to/skills",
    min_team_members_for_create=2,
)

# Create team skill evolution rail
team_rail = TeamSkillRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    auto_save=False,
    async_evolution=True,
)

# Configure on DeepAgent
agent = create_deep_agent(
    model=model_client,
    tools=team_tools,
    rails=[create_rail, team_rail],
    enable_task_loop=True,
)

# User requests evolution
request_id = await team_rail.request_user_evolution(
    skill_name="research-team",
    user_intent="Add reviewer role, limit research time to 10 minutes",
)

# Get approval events
events = await team_rail.drain_pending_approval_events(wait=True)
for event in events:
    if event.type == "chat.ask_user_question":
        request_id = event.payload["request_id"]
        # User approval
        await team_rail.approve_record(request_id)

# Request simplify
simplify_request_id = await team_rail.request_simplify("research-team")
if simplify_request_id:
    await team_rail.on_approve_simplify(simplify_request_id)

# Request rebuild
prompt = await team_rail.request_rebuild("research-team", min_score=0.5)
```
