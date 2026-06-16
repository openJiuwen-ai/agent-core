# Skill Evolution Rail

Public rail for regular skill online evolution. This page covers existing skill experience evolution, not new skill creation and not team-skill evolution.

---

## class SkillEvolutionRail

Public rail for collecting agent trajectories, detecting reusable regular-skill improvements, staging generated experience records, and writing approved records through `EvolutionStore`.

### Import

```python
from openjiuwen.harness.rails import (
    EvolutionReviewRuntime,
    SkillEvolutionRail,
    SubagentRail,
    configure_skill_evolution,
)
```

The active evolution review flow in `SkillEvolutionRail` delegates to the `evolution_reviewer` subagent, so register it together with `SubagentRail`. Synchronous subagent mode registers `task_tool`, which the follow-up prompt uses to call the review subagent.

`SkillEvolutionRail.init()` now only registers the active review tools and stable review subagent; it does not configure `EvolutionInterruptRail`.

Stable review subagent registration is deduplicated by name (`evolution_reviewer`). Re-registering it with different `runtime`, `query_service`, or `store` fails fast.

### 推荐优先 / Recommended Construction

Prefer the configuration API for normal skills:

```python
configure_skill_evolution(
    agent,
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    auto_save=False,
    language="cn",
)
```

The configuration API adds `SubagentRail` when needed and wires `EvolutionInterruptRail` with the regular `SkillEvolutionRail`.

Manual wiring requires explicit shared objects:

```python
runtime = EvolutionReviewRuntime()
skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    review_runtime=runtime,
    auto_save=False,
)
interrupt_rail = EvolutionInterruptRail(
    review_runtime=runtime,
    submission_service=skill_rail.experience_manager.experience_submission_service,
)
agent = create_deep_agent(
    model=model_client,
    tools=tools,
    rails=[SubagentRail(), interrupt_rail, skill_rail],
)
```

When manual configuring, only one shared `EvolutionInterruptRail` should be used and it must be bound to one `review_runtime` and one `submission_service`. Subject kind is not used for interrupt routing.

### Trigger Mechanism

- Passive evolution runs after `DeepAgent.invoke()` completes.
- `auto_scan=False` disables passive signal scanning and async snapshot creation for passive evolution.
- Active evolution is available through `request_user_evolution()`; the returned prompt asks the main agent to call `prepare_skill_evolution(user_confirmed=true)` first, then delegate `evolution_reviewer` with the returned `evolution_review_ref`. The prepare tool collects the current rail's execution/conversation trajectory as default review materials, and `user_intent` only adds optimization direction.
- Regular skill evolution ignores `kind: team-skill` skills; team skills use `TeamSkillEvolutionRail` / `TeamSkillRail`.

```text
class SkillEvolutionRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    review_runtime: EvolutionReviewRuntime,
    auto_scan: bool = True,
    auto_save: bool = True,
    subject_kind: str = "skill",
    language: str = "cn",
    trajectory_store: Optional[TrajectoryStore] = None,
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
* **review_runtime** (EvolutionReviewRuntime): Shared active-review state for review subagent bindings.
* **auto_scan** (bool): Whether to run passive signal scanning after invoke. Defaults to `True`.
* **auto_save** (bool): Whether generated passive records are auto-approved and persisted. Defaults to `True`; production hosts should usually set this to `False` and consume approval events.
* **subject_kind** (str): Subject kind used by this rail (`"skill"` or `"swarm-skill"` normalized).
* **language** (str): Prompt language, commonly `"cn"` or `"en"`.
* **trajectory_store** (TrajectoryStore, optional): Store for captured execution trajectories.
* **eval_interval** (int): Number of presentations between experience scoring checks. Must be at least 1.
* **evolution_total_timeout_secs** (float): Background evolution timeout budget.
* **generate_records_llm_policy** (LLMInvokePolicy): LLM retry/timeout policy for record generation.
* **evaluate_llm_policy** (LLMInvokePolicy): LLM retry/timeout policy for experience scoring.
* **simplify_llm_policy** (LLMInvokePolicy): LLM retry/timeout policy for simplify governance.
* **disabled_skills** (Optional[Union[str, list[str]]], optional): Deny-list of skill names excluded from self-optimization. Supports a single skill name (str) or multiple names (list[str]).

### Priority

`priority = 80`

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
| `request_id` | Approval request id. |
| `signal_type` | Signal type that contributed to the request. |
| `source` | Signal or event source. |
| `status` | Outcome status when available. |

Approval events use `type="chat.ask_user_question"` and include `payload["request_id"]`. Progress events use `type="llm_reasoning"`. Background failures are reported as outcome events and do not fail the main invoke.

`outcome` events are terminal machine-readable events. A normal no-op evolution run emits `status="no_evolution_no_records"` when the orchestrator completes successfully but produces no records. Hosts should not parse progress text to infer terminal state.

### Subject Schema in Review/Mutation Tools

Active-review and mutation tools share a subject envelope:

```python
{
    "kind": "skill" | "swarm-skill",
    "name": "my-skill",
    "scope": { ... }  # optional
}
```

`"team-skill"` is accepted as a legacy input alias and normalized to `"swarm-skill"` by runtime tooling before persistence/approval.

`subject.kind` is accepted by `prepare_skill_evolution`, `list_skill_experiences`, `read_skill_experiences`, `evolve_skill_experiences`, and `simplify_skill_experiences`.

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

### async request_user_evolution(skill_name, user_intent, *, max_index_records=20) -> EvolutionRequestResult

Build a host-delivered active evolution command prompt for a regular skill. The prompt does not create a review scope directly; it instructs the main agent to call `prepare_skill_evolution(user_confirmed=true)` and then use `task_tool(subagent_type="evolution_reviewer")` with the returned `evolution_review_ref`.

**Parameters**:

* **skill_name** (str): Target regular skill name.
* **user_intent** (str): User improvement intent.
* **max_index_records** (int): Maximum experience index entries to inline in the prompt preview.

**Returns**:

* `EvolutionRequestResult`: `mode="agent_prompt"` and `followup_prompt` for the host to inject into the agent loop. It does not stage records or emit an approval event.

### async approve_record(request_id) -> None

Approve staged records and write them through `EvolutionStore`.

If a partial failure occurs, the unwritten tail remains in the same `PendingChange`; retry with the same `request_id`.

### async reject_record(request_id) -> None

Reject staged records without writing them.

### async request_simplify(skill_name, user_intent=None, mode="agent_prompt") -> SimplifyRequestResult

Build a host-delivered simplify command prompt. The prompt contains a bounded experience summary index and asks the agent to use `list_skill_experiences`, `read_skill_experiences`, and `simplify_skill_experiences`.

**Returns**:

* `SimplifyRequestResult`: `mode="agent_prompt"` and `followup_prompt`. It does not call the scorer, stage governance actions, or emit an approval event.

### async request_rebuild(skill_name, user_intent=None, min_score=0.5) -> Optional[str]

Archive current skill assets and return a rebuild follow-up prompt using filtered evolution records. The host or command handler must inject the returned prompt into the agent loop; the rail does not directly write the rebuilt `SKILL.md`.

### async drain_pending_host_events(wait=False, timeout=None) -> list[OutputSchema]

Return and clear buffered host events. If `wait=True`, waits for pending background evolution tasks up to `timeout`.

### async drain_pending_approval_events(wait=False, timeout=None) -> list[OutputSchema]

Compatibility wrapper for `drain_pending_host_events()`.

---

## Example

```python
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails import SkillEvolutionRail, SubagentRail

skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    auto_save=False,
)

agent = create_deep_agent(
    model=model_client,
    tools=tools,
    rails=[
        skill_rail,
        SubagentRail(),
    ],
)

result = await skill_rail.request_user_evolution(
    "code-review",
    "Prefer behavior-level findings before style comments",
)

if result.followup_prompt:
    # Host delivery is application-specific: queue it as the next query,
    # follow-up, or equivalent message in your agent loop.
    await agent.invoke({"query": result.followup_prompt})
```
