# Team Skill Rails

Rails for automated team skill creation and online evolution.

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
    language: str = "en",
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

Team skill evolution Rail, similar to `SkillEvolutionRail` but specialized for team skills.

### Features

- Trajectory issue detection (role coordination, constraint violations, workflow inefficiency)
- User-requested evolution
- PATCH generation and approval
- Experience simplify/rebuild

### Trigger Mechanism

- Monitors `view_task` tool result, detecting "all tasks completed"
- Supports passive trajectory analysis and active user request evolution paths

```text
class TeamSkillRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    language: str = "en",
    trajectory_store: Optional[TrajectoryStore] = None,
    team_trajectory_store: Optional[TrajectoryStore] = None,
    auto_save: bool = False,
    async_evolution: bool = True,
    team_id: Optional[str] = None,
    trajectories_dir: Optional[Path] = None,
    user_request_llm_policy: LLMInvokePolicy = ...,
    trajectory_issue_llm_policy: LLMInvokePolicy = ...,
    patch_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
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
* **auto_save** (bool): Whether to auto-save PATCH, defaults to `False` (requires user approval).
* **async_evolution** (bool): Whether to execute evolution asynchronously, defaults to `True`.
* **team_id** (str, optional): Team ID.
* **trajectories_dir** (Path, optional): Trajectory directory path.
* **user_request_llm_policy** (LLMInvokePolicy): User intent detection LLM invocation policy.
* **trajectory_issue_llm_policy** (LLMInvokePolicy): Trajectory issue detection LLM invocation policy.
* **patch_llm_policy** (LLMInvokePolicy): PATCH generation LLM invocation policy.
* **evaluate_llm_policy** (LLMInvokePolicy): Experience evaluation LLM invocation policy.
* **simplify_llm_policy** (LLMInvokePolicy): Experience simplify LLM invocation policy.
* **evolution_total_timeout_secs** (float): Background evolution total timeout budget, defaults to 600s.

### Priority

`priority = 80`

---

## Properties

### store -> EvolutionStore

Evolution store instance.

### scorer -> ExperienceScorer

Experience scorer.

### optimizer -> TeamSkillOptimizer

Team skill optimizer.

### evolution_config -> dict

Complete evolution configuration, including phase LLM invocation policies and timeout settings.

---

## Methods

### async notify_team_completed(ctx) -> bool

Trigger skill evolution (when all tasks complete).

**Parameters**:

* **ctx** (AgentCallbackContext, optional): Callback context.

**Returns**:

* `bool`: Whether evolution was successfully triggered.

---

### async request_user_evolution(skill_name, user_intent, auto_approve) -> Optional[str]

User-initiated evolution request.

**Parameters**:

* **skill_name** (str): Target skill name.
* **user_intent** (str): User improvement intent description.
* **auto_approve** (bool): Whether to auto-approve, defaults to `False`.

**Returns**:

* `str`: request_id or `None` (when skill not found or no PATCH).

---

### async request_simplify(skill_name, user_intent) -> Optional[Dict[str, int]]

Request experience simplification, executes directly without approval.

**Parameters**:

* **skill_name** (str): Target skill name.
* **user_intent** (str, optional): User simplification intent.

**Returns**:

* `Dict[str, int]`: Simplification action counts `{"deleted": N, "merged": N, "refined": N, "kept": N, "errors": N}` or `None`.

---

### async request_rebuild(skill_name, user_intent, min_score) -> Optional[str]

Request skill rebuild (archive old version and generate new version).

**Parameters**:

* **skill_name** (str): Target skill name.
* **user_intent** (str, optional): User rebuild intent.
* **min_score** (float): Evolution record filter threshold, defaults to 0.5.

**Returns**:

* `str`: Rebuild prompt text or `None` (when skill not found).

---

### async on_approve_patch(request_id) -> None

Approve PATCH, write staged PATCH to `evolutions.json`.

**Parameters**:

* **request_id** (str): Request ID.

---

### async on_reject_patch(request_id) -> None

Reject PATCH, clear staged records.

**Parameters**:

* **request_id** (str): Request ID.

---

### async drain_pending_approval_events(wait, timeout) -> List[OutputSchema]

Get pending approval event list.

**Parameters**:

* **wait** (bool): Whether to wait for events.
* **timeout** (float, optional): Wait timeout, defaults to `evolution_total_timeout_secs`.

**Returns**:

* `List[OutputSchema]`: Pending approval event list.

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
        await team_rail.on_approve_patch(request_id)

# Request simplify
result = await team_rail.request_simplify("research-team")

# Request rebuild
prompt = await team_rail.request_rebuild("research-team", min_score=0.5)
```