# Skill Evolution Rail

Public rail for regular skill online evolution. This page covers existing skill experience evolution, not new skill creation and not team-skill evolution.

---

## class SkillEvolutionRail

Public rail for collecting agent trajectories, detecting reusable regular-skill improvements, staging generated experience records, and writing approved records through `EvolutionStore`.

### Import

```python
from openjiuwen.harness.rails import SkillEvolutionRail
```

### Trigger Mechanism

- Passive evolution runs after `DeepAgent.invoke()` completes.
- `auto_scan=False` disables passive signal scanning and async snapshot creation for passive evolution.
- Active evolution is available through `request_user_evolution()`; the current rail's collected execution/conversation trajectory is used as default evidence, and `user_intent` only adds optimization direction.
- Regular skill evolution ignores `kind: team-skill` skills; team skills use `TeamSkillEvolutionRail` / `TeamSkillRail`.

```text
class SkillEvolutionRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    auto_scan: bool = True,
    auto_save: bool = True,
    language: str = "cn",
    trajectory_store: Optional[TrajectoryStore] = None,
    team_trajectory_store: Optional[TrajectoryStore] = None,
    eval_interval: int = 5,
    evolution_total_timeout_secs: float = 600.0,
    generate_records_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
    disabled_skills: Optional[Union[str, list[str]]] = None,
)
```

**Parameters**:

* **skills_dir** (Union[str, list[str]]): Skill directory path or path list.
* **llm** (Model): LLM client instance used by signal, record, scoring, and governance stages.
* **model** (str): Model name.
* **auto_scan** (bool): Whether to run passive signal scanning after invoke. Defaults to `True`.
* **auto_save** (bool): Whether generated passive records are auto-approved and persisted. Defaults to `True`; production hosts should usually set this to `False` and consume approval events.
* **language** (str): Prompt language, commonly `"cn"` or `"en"`.
* **trajectory_store** (TrajectoryStore, optional): Store for captured execution trajectories.
* **team_trajectory_store** (TrajectoryStore, optional): Deprecated shared trajectory store parameter. It emits a deprecation warning and does not enable runtime team aggregation for regular skill evolution.
* **eval_interval** (int): Number of presentations between experience scoring checks. Must be at least 1.
* **evolution_total_timeout_secs** (float): Background evolution timeout budget.
* **generate_records_llm_policy** (LLMInvokePolicy): LLM retry/timeout policy for record generation.
* **evaluate_llm_policy** (LLMInvokePolicy): LLM retry/timeout policy for experience scoring.
* **simplify_llm_policy** (LLMInvokePolicy): LLM retry/timeout policy for simplify governance.
* **disabled_skills** (Optional[Union[str, list[str]]], optional): Deny-list of skill names excluded from self-optimization. Supports a single skill name (str) or multiple names (list[str]).

### Priority

`priority = 80`

---

## Lifecycle

The observable lifecycle is:

```text
trajectory captured
-> signals detected
-> local apply preview
-> pending approval or auto-approved
-> EvolutionStore persistence
-> evolutions.json and evolution/*.md projection
```

The ownership boundary is stable:

* `EvolutionRail` captures trajectories, snapshots callback context, manages background tasks, and buffers host events.
* `OnlineEvolutionOrchestrator` coordinates context build, update generation, and local preview.
* `ExperienceManager + PendingChange` owns pending approval state.
* `EvolutionStore` owns durable writes and projection.

All durable skill experience writes must go through `EvolutionStore`; hosts should not edit `evolutions.json` directly.

---

## Host Events

Use `drain_pending_host_events()` as the canonical API to consume buffered evolution events. `drain_pending_approval_events()` is a compatibility wrapper that drains the same shared host-event buffer.

Evolution events are `OutputSchema` objects. Evolution-specific metadata is carried in:

```python
event.payload["evolution_meta"]
```

Known metadata fields:

| Field | Meaning |
|---|---|
| `event_kind` | `approval`, `progress`, or `outcome`. |
| `rail_kind` | Producing rail kind when available, such as `regular` or `team`. |
| `stage` | Lifecycle stage for progress or outcome events. |
| `skill_name` | Target skill name. |
| `request_id` | Approval or governance request id. |
| `signal_type` | Signal type that contributed to the request. |
| `source` | Signal or event source. |
| `status` | Outcome status when available. |

Approval events use `type="chat.ask_user_question"` and include `payload["request_id"]`. Progress events use `type="llm_reasoning"`. Background failures are reported as outcome events and do not fail the main invoke.

`outcome` events are terminal machine-readable events. A normal no-op evolution run emits `status="no_evolution_no_records"` when the orchestrator completes successfully but produces no records. Hosts should not parse progress text to infer terminal state.

---

## Async Snapshot Contract

When `async_evolution=True`, the rail snapshots callback data before the background task starts.

| Snapshot field | Meaning |
|---|---|
| `trajectory` | Complete trajectory for the invoke. |
| `messages` | Conversation messages, preferably derived from trajectory and falling back to callback/session data. |
| `skill_name` | Optional label used by specific rails or snapshots. |

`messages` are detection context, while `trajectory` is the execution evidence. Do not treat snapshot dictionaries as a public serialization format; use the host event and rail methods as public integration points.

---

## Properties

### evolution_store -> EvolutionStore

Evolution store for skill data. This is distinct from `trajectory_store`.

### store -> EvolutionStore

Backward-compatible alias for `evolution_store`.

### scorer -> ExperienceScorer

Experience scorer.

### evolver -> SkillExperienceOptimizer

Regular skill experience optimizer.

### evolution_config -> dict

Effective LLM policies, timeout, `auto_scan`, `auto_save`, and `eval_interval`.

---

## Methods

### async request_user_evolution(skill_name, user_intent="", *, auto_approve=False) -> EvolutionRequestResult

Stage active evolution for a regular skill. The method first uses the current rail's bounded trajectory evidence window to detect execution signals and user feedback; a non-empty `user_intent` is appended as an explicit request signal instead of replacing trajectory evidence.

**Parameters**:

* **skill_name** (str): Target regular skill name.
* **user_intent** (str): User improvement intent. Defaults to `""`; when empty, current trajectory evidence can still trigger evolution if it contains actionable signals.
* **auto_approve** (bool): Whether to auto-approve the generated request. Defaults to `False`.

**Returns**:

* `EvolutionRequestResult`: `request_id` is set when records were generated; otherwise an empty result object is returned.

### async approve_record(request_id) -> None

Approve staged records and write them through `EvolutionStore`.

If a partial failure occurs, the unwritten tail remains in the same `PendingChange`; retry with the same `request_id`.

### async reject_record(request_id) -> None

Reject staged records without writing them.

### async request_simplify(skill_name, user_intent=None) -> Optional[str]

Stage a simplify proposal and emit an approval event.

**Returns**:

* `str`: governance request id when actions were proposed, otherwise `None`.

Use `on_approve_simplify(request_id)` to execute and `on_reject_simplify(request_id)` to discard.

### async request_rebuild(skill_name, user_intent=None, min_score=0.5) -> Optional[str]

Archive current skill assets and return a rebuild follow-up prompt using filtered evolution records. The host or command handler must inject the returned prompt into the agent loop; the rail does not directly write the rebuilt `SKILL.md`.

### async drain_pending_host_events(wait=False, timeout=None) -> list[OutputSchema]

Return and clear buffered host events. If `wait=True`, waits for pending background evolution tasks up to `timeout`.

### async drain_pending_approval_events(wait=False, timeout=None) -> list[OutputSchema]

Compatibility wrapper for `drain_pending_host_events()`.

### async generate_and_emit_experience(...) -> bool

Compatibility wrapper for legacy host-driven/manual evolution. New integrations should call `request_user_evolution()`.

---

## Example

```python
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails import SkillEvolutionRail

skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    auto_save=False,
)

agent = create_deep_agent(
    model=model_client,
    tools=tools,
    rails=[skill_rail],
)

result = await skill_rail.request_user_evolution(
    "code-review",
    "Prefer behavior-level findings before style comments",
)

if result.approval_event is not None:
    await skill_rail.approve_record(result.request_id)
```
