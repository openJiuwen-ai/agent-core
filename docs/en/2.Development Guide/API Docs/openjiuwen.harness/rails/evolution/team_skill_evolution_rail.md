# Team Skill Evolution Rail

Team-skill creation and online evolution documentation.

---

## class TeamSkillCreateRail

Independent Rail that auto-detects multi-agent collaboration patterns and suggests team skill creation.

### Trigger Mechanism

- Waits for team completion, then checks recorded `spawn_member` calls from the current team run
- When count reaches threshold (default 2) and no existing Team/Swarm Skill was used, injects a short follow_up via `TaskLoopController` to wake up the next round
- The full self-check rules are injected as system prompt text. If the Agent finds reusable team collaboration value, it confirms through normal reply text; after user confirmation, it invokes `swarmskill-creator` or a compatible team skill creator Skill. If that creator is unavailable, the Agent should tell the user in normal reply text.

```text
class TeamSkillCreateRail(
    skills_dir: str,
    *,
    language: str = "cn",
    auto_trigger: bool = True,
    min_team_members_for_create: int = 2,
)
```

**Parameters**:

* **skills_dir** (str): Skill directory path.
* **language** (str): Language setting, supports `"cn"` or `"en"`.
* **auto_trigger** (bool): Whether to auto-trigger, defaults to `True`.
* **min_team_members_for_create** (int): Trigger threshold, `spawn_member` call count reaching this value triggers, defaults to 2.

### Priority

`priority = 85`

---

## class TeamSkillRail

Public team skill evolution Rail, similar to `SkillEvolutionRail` but specialized for team skills.
`TeamSkillRail` is the compatibility public alias for `TeamSkillEvolutionRail`.
New team skill creation remains owned by `TeamSkillCreateRail`; this rail only evolves existing `kind: team-skill` skills.

### Import

```python
from openjiuwen.harness.rails import (
    EvolutionInterruptRail,
    EvolutionReviewRuntime,
    TeamSkillRail,
    configure_skill_evolution,
)
```

`TeamSkillEvolutionRail` registers the stable `evolution_reviewer` and exposes it through the rail-owned `evolve_review_task`. The active review path does not require a global `task_tool` or `SubagentRail`; its tools share `EvolutionReviewRuntime`.

`TeamSkillEvolutionRail` / `SkillEvolutionRail` `init()` does not configure `EvolutionInterruptRail`. Add one shared interrupt rail explicitly if you do not use the factory.

Stable review subagent registration is deduplicated by `evolution_reviewer`; a stale binding is replaced with the current runtime/query/store.

### 推荐优先 / 推荐构建方式

Prefer team configuration:

```python
configure_skill_evolution(
    agent,
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    team=True,
    auto_save=False,
    language="cn",
)
```

The configuration API wires `EvolutionInterruptRail` with `TeamSkillRail`.

Manual assembly requires explicit shared dependencies:

```python
runtime = EvolutionReviewRuntime()
team_rail = TeamSkillRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    review_runtime=runtime,
    team_id="research-team",
    auto_save=False,
)
interrupt_rail = EvolutionInterruptRail(
    review_runtime=runtime,
    submission_service=team_rail.experience_manager.experience_submission_service,
)
agent = create_deep_agent(
    model=model_client,
    tools=team_tools,
    rails=[interrupt_rail, team_rail],
)
```

`EvolutionInterruptRail` no longer routes by `subject.kind`; it should be one shared rail bound to the same runtime/service.

### Features

- Trajectory issue detection (role coordination, constraint violations, workflow inefficiency)
- User-requested evolution
- Aggregated experience record generation and approval
- Experience simplify/rebuild

### Trigger Mechanism

- Monitors `view_task` tool result, detecting "all tasks completed"
- Supports a passive signal path and an Agent-decided active review path
- `signal_trigger` controls passive team completion scanning and defaults to `False`.
- `review_trigger` controls team completion self-check follow_up insertion and defaults to `False`.
- When `review_trigger=True`, active review takes precedence over passive signal generation after team completion. The main Agent decides whether evolution is needed and calls the rail-owned `evolve_review_task`, which runs `evolution_reviewer`.
- `signal_trigger=False` disables passive completion scanning and `notify_team_completed()` passive triggering. `notify_team_completed()` may still schedule active review when `review_trigger=True`.
- The passive path aggregates collaborative trajectory evidence and uses `SkillExperienceOptimizer(profile="team")`. Team completion, team skill attribution, and runtime role attribution are heuristic host-bridge signals, not strong contracts.

```text
class TeamSkillRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    language: str = "cn",
    trajectory_store: Optional[TrajectoryStore] = None,
    trajectory_source: Optional[TrajectorySource] = None,
    trajectory_sink: Optional[TrajectorySink] = None,
    member_role: Optional[str] = None,
    signal_trigger: Optional[bool] = None,
    auto_save: bool = False,
    review_runtime: EvolutionReviewRuntime,
    async_evolution: bool = True,
    max_concurrent_evolution: int = 1,
    team_id: Optional[str] = None,
    trajectories_dir: Optional[Path] = None,
    record_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
    eval_interval: int = 5,
    evolution_total_timeout_secs: float = 720.0,
    disabled_skills: Optional[Union[str, list[str]]] = None,
    review_trigger: Optional[bool] = None,
    review_interval: int = 5,
    review_agent_max_iterations: int = 40,
)
```

**Parameters**:

* **skills_dir** (Union[str, list[str]]): Skill directory path or path list.
* **llm** (Model): LLM client instance.
* **model** (str): Model name.
* **language** (str): Language setting.
* **trajectory_store** (TrajectoryStore, optional): Trajectory store instance.
* **trajectory_source** (TrajectorySource, optional): Runtime source for aggregated member trajectory evidence.
* **trajectory_sink** (TrajectorySink, optional): Runtime sink for publishing this member's latest trajectory snapshot.
* **member_role** (str, optional): Role written to published snapshots. Defaults to `"leader"` for team skill evolution.
* **signal_trigger** (bool, optional): Whether to detect passive team completion and trigger passive evolution, defaults to `False`.
* **auto_save** (bool): Whether to auto-save generated experience records, defaults to `False` (requires user approval).
* **review_runtime** (EvolutionReviewRuntime): Shared active-review runtime required for review subagent + active approval tools.
* **async_evolution** (bool): Whether to execute evolution asynchronously, defaults to `True`.
* **max_concurrent_evolution** (int): Max concurrent background evolution tasks, defaults to 1.
* **team_id** (str, optional): Team ID.
* **trajectories_dir** (Path, optional): Trajectory directory path.
* **record_llm_policy** (LLMInvokePolicy): Experience record generation LLM invocation policy.
* **evaluate_llm_policy** (LLMInvokePolicy): Experience evaluation LLM invocation policy.
* **simplify_llm_policy** (LLMInvokePolicy): Experience simplify LLM invocation policy.
* **eval_interval** (int): Number of presentations between experience scoring checks. Must be at least 1.
* **evolution_total_timeout_secs** (float): Background evolution total timeout budget, defaults to 720s.
* **disabled_skills** (Optional[Union[str, list[str]]], optional): Deny-list of skill names excluded from self-optimization. Supports a single skill name (str) or multiple names (list[str]).
* **review_trigger** (bool, optional): Whether team completion enqueues a short evolution self-check follow_up, defaults to `False`.
* **review_interval** (int): Review interval accepted by the shared base rail. It must be at least 1 and defaults to 5; Team review follow-ups remain completion-driven.
* **review_agent_max_iterations** (int): Maximum iterations for `evolution_reviewer`, defaults to 40.

### Runtime Trajectory Source/Sink

`TeamSkillRail` uses `trajectory_source` and `trajectory_sink` for online team trajectory aggregation. A common setup is to pass the same `InMemoryTrajectoryRegistry` as both:

```python
from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryRegistry
from openjiuwen.harness.rails import TeamSkillRail

trajectory_registry = InMemoryTrajectoryRegistry()

team_rail = TeamSkillRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    team_id="research-team",
    trajectory_source=trajectory_registry,
    trajectory_sink=trajectory_registry,
)
```

The rail publishes `MemberTrajectorySnapshot` values after invoke. Snapshots contain `team_id`, `session_id`, `member_id`, `member_role`, `trajectory`, and `recorded_at_ms`; they do not contain a public revision. `InMemoryTrajectoryRegistry` owns latest-snapshot ordering: newer `recorded_at_ms` wins, and equal timestamps are resolved by registry receive order.

To aggregate multiple members, every rail or agent that should contribute evidence must publish to the same `trajectory_sink`; this rail then reads that shared registry through `trajectory_source`.

### Priority

`priority = 80`

---

## Properties

### store -> EvolutionStore

Evolution store instance.

### scorer -> ExperienceScorer

Experience scorer.

### generator -> SkillExperienceOptimizer

Shared experience optimizer configured with `profile="team"` for the passive signal path.

### evolution_config -> dict

Complete evolution configuration, including phase LLM invocation policies and timeout settings.

---

## Runtime Trajectory Methods

### set_trajectory_source(source) -> None

Bind or replace the runtime `TrajectorySource` used to aggregate team trajectory evidence.

### set_trajectory_sink(sink, *, team_id, member_role=None) -> None

Bind or replace the runtime `TrajectorySink` used to publish this rail's member snapshots. `team_id` is required when `sink` is not `None`. `member_role` defaults to `"leader"` for team skill evolution.

---

## Lifecycle and Contracts

The passive signal lifecycle matches regular skill evolution:

```text
team trajectory aggregated
-> team signals detected
-> local apply preview
-> pending approval or auto-approved
-> EvolutionStore persistence
-> evolutions.json and evolution/*.md projection
```

The active-review lifecycle is separate:

```text
team completion or user request
-> main Agent decides/prepares a bounded review scope
-> evolve_review_task runs evolution_reviewer
-> reviewer submits a proposal through review tools
-> interrupt-governed approval and persistence
```

Stable ownership boundaries:

* `TeamSkillEvolutionRail` owns team-specific host bridge behavior: `view_task` completion detection, `notify_team_completed()`, team trajectory aggregation, and used team-skill detection.
* The rail-owned `evolve_review_task` is the only task wrapper for the dedicated `evolution_reviewer`.
* `OnlineEvolutionOrchestrator` coordinates context build, update generation, and local preview.
* `ExperienceManager + PendingChange` owns pending approval state.
* `EvolutionStore` owns durable writes and projection.

`EvolutionApprovalRuntime` is a rail-bound adapter over manager approval methods and pending snapshot lookup. It does not own approval state, and approval lifecycle should not be moved back into `EvolutionRail`.

### Host events

Use `drain_pending_host_events()` as the canonical API to consume evolution events. `drain_pending_approval_events()` is a compatibility wrapper over the same buffer.

Evolution metadata is carried in `OutputSchema.payload["evolution_meta"]`:

| Field | Meaning |
|---|---|
| `event_kind` | `approval`, `progress`, or `outcome`. |
| `rail_kind` | Producing rail kind, usually `team` for this rail. |
| `stage` | Lifecycle stage for progress or outcome events. |
| `skill_name` | Target team skill name. |
| `request_id` | Approval request id. |
| `signal_type` | Signal type that contributed to the request. |
| `source` | Signal or event source. |
| `status` | Outcome status when available. |

Approval events use `type="chat.ask_user_question"` and include `payload["request_id"]`. Progress events use `type="llm_reasoning"`. Background failures are reported as outcome events and do not fail the main invoke.

`outcome` events are terminal machine-readable events. A normal no-op evolution run emits `status="no_evolution_no_records"` when the orchestrator completes successfully but produces no records. Hosts should not parse progress text to infer terminal state.

### Snapshot and signal boundaries

Async snapshots contain `trajectory`, `messages`, and optionally `skill_name`. `messages` are detection context; `trajectory` is execution evidence. The current implementation keeps legacy dict compatibility, so hosts should treat rail methods and host events as public integration points instead of depending on the dict shape.

Team signal semantics are partly structured as `EvolutionSignal` fields and partly carried in `EvolutionSignal.context`. Runtime team member / role attribution remains heuristic; role summaries extracted from `SKILL.md` are documentation context, not runtime identity proof.

### Subject Schema in Evolution Tools

Team evolution also uses the normalized subject contract:

```python
{
    "kind": "swarm-skill",
    "name": "team-skill-name",
    "scope": { ... }  # optional
}
```

`"team-skill"` remains accepted as a legacy alias and is normalized to `"swarm-skill"` by `EvolutionReviewRuntime` / interrupt contract validation.

---

## Methods

### async notify_team_completed(ctx) -> bool

Mark team completion for the enabled passive signal and/or active-review trigger.

**Parameters**:

* **ctx** (AgentCallbackContext, optional): Callback context.

**Returns**:

* `bool`: Whether team completion was accepted for configured evolution handling.

---

### async request_user_evolution(skill_name, user_intent="", *, auto_approve=None, max_index_records=None) -> EvolutionRequestResult

Build a host-delivered active-review prompt for a team skill. Current rail trajectory or aggregated team trajectory becomes the default review evidence; `user_intent` only adds direction.

**Parameters**:

* **skill_name** (str): Target skill name.
* **user_intent** (str): User improvement intent description, defaults to `""`.
* **auto_approve** (bool, optional): Accepted for compatibility and ignored by the active-review path.
* **max_index_records** (int, optional): Accepted for compatibility and ignored by the active-review path.

**Returns**:

* `EvolutionRequestResult`: `mode="agent_prompt"` and a `followup_prompt` for the host to deliver to the main Agent. An unknown or non-team skill returns an empty result.

---

### async request_simplify(skill_name, user_intent=None) -> SimplifyRequestResult

Stage scorer-driven Team Skill simplify governance and return an approval event.

**Parameters**:

* **skill_name** (str): Target skill name.
* **user_intent** (str, optional): User simplification intent.

**Returns**:

* `SimplifyRequestResult`: governance `request_id`, proposed `actions`, and optional `approval_event`.

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
from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryRegistry
from openjiuwen.harness.rails import TeamSkillCreateRail, TeamSkillRail
from openjiuwen.harness import create_deep_agent

# Create team skill creation rail
create_rail = TeamSkillCreateRail(
    skills_dir="/path/to/skills",
    min_team_members_for_create=2,
)

trajectory_registry = InMemoryTrajectoryRegistry()

# Create team skill evolution rail
team_rail = TeamSkillRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    team_id="research-team",
    trajectory_source=trajectory_registry,
    trajectory_sink=trajectory_registry,
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
result = await team_rail.request_user_evolution(
    skill_name="research-team",
    user_intent="Add reviewer role, limit research time to 10 minutes",
)

# User approval
if result.approval_event is not None:
    await team_rail.approve_record(result.request_id)

# Request simplify
simplify_result = await team_rail.request_simplify("research-team")
if simplify_result.approval_event:
    await team_rail.on_approve_simplify(simplify_result.request_id)

# Request rebuild
prompt = await team_rail.request_rebuild("research-team", min_score=0.5)
```
