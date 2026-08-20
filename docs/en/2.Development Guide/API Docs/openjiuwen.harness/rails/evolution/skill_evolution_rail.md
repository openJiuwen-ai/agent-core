# Skill Evolution Rail

Public rail for regular skill online evolution. This page covers existing skill experience evolution, not new skill creation and not team-skill evolution.

---

## class SkillEvolutionRail

Public rail for collecting agent trajectories, detecting reusable regular-skill improvements, staging generated experience records, and writing approved records through `EvolutionStore`.

### Import

```python
from openjiuwen.harness.rails import (
    EvolutionInterruptRail,
    EvolutionReviewRuntime,
    SkillEvolutionRail,
    configure_skill_evolution,
)
```

The active evolution review flow delegates to the stable `evolution_reviewer` subagent through the rail-owned
`evolve_review_task` tool. It does not require the global `task_tool` or `SubagentRail`.

`SkillEvolutionRail.init()` now only registers the active review tools and stable review subagent; it does not configure `EvolutionInterruptRail`.

Stable review subagent registration is deduplicated by name (`evolution_reviewer`). Rail initialization replaces a
stale reviewer binding with the current runtime, query service, and store.

### 推荐优先 / Recommended Construction

Prefer the configuration API for normal skills:

```python
configure_skill_evolution(
    agent,
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=runtime_processor,
    auto_save=False,
    language="cn",
)
```

The configuration API wires `EvolutionInterruptRail` with the regular `SkillEvolutionRail`.

Manual wiring requires explicit shared objects:

```python
runtime = EvolutionReviewRuntime()
skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=runtime_processor,
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
    rails=[interrupt_rail, skill_rail],
)
```

When manual configuring, only one shared `EvolutionInterruptRail` should be used and it must be bound to one `review_runtime` and one `submission_service`. Subject kind is not used for interrupt routing.

### Trigger Mechanism

- Passive evolution runs after `DeepAgent.invoke()` completes.
- `signal_trigger` controls passive signal scanning and defaults to `False`.
- `review_trigger` controls periodic self-check follow_up insertion and defaults to `False`.
- `review_interval` controls the number of non-follow_up task iterations between review checks and must be at least 1.
- `signal_trigger=False` disables passive signal scanning and async snapshot creation for passive evolution.
- Active evolution is available through `request_user_evolution()`; the returned prompt asks the main agent to call `prepare_skill_evolution(user_confirmed=true)` first, then call `evolve_review_task(evolution_review_ref=...)`. The prepare tool collects the current rail's execution/conversation trajectory as default review materials, and `user_intent` only adds optimization direction.
- Regular skill evolution ignores `kind: team-skill` skills; team skills use `TeamSkillEvolutionRail` / `TeamSkillRail`.

### Externally attributed signals

When a host has already completed attribution, it can call:

```python
result = await skill_rail.evolve_from_external_signals(
    signals=[signal],
    messages=messages,
    trajectory=trajectory,
    user_query="Add reusable validation guidance.",
    requires_approval=True,
)
```

This entry point bypasses passive `signal_trigger` detection, so it remains available when `signal_trigger=False`.
The caller owns attribution and evidence policy. The Rail still requires all signals to target exactly one existing
regular Skill, rejects disabled, missing, and team-skill targets, and uses the standard optimizer, concurrency
semaphore, approval, and `EvolutionStore` persistence pipeline.

When `requires_approval=None`, the default remains `not auto_save`; explicit `True` stages an approval request, while
explicit `False` allows an authorized host to save automatically. Callers should not edit `evolutions.json` directly.

```text
class SkillEvolutionRail(
    skills_dir: Union[str, list[str]],
    *,
    llm: Model,
    model: str,
    signal_trigger: Optional[bool] = None,
    auto_save: bool = False,
    review_runtime: EvolutionReviewRuntime,
    subject_kind: str = "skill",
    language: str = "cn",
    trajectory_span_processor: TrajectorySpanProcessor,
    eval_interval: int = 5,
    evolution_total_timeout_secs: float = 600.0,
    generate_records_llm_policy: LLMInvokePolicy = ...,
    evaluate_llm_policy: LLMInvokePolicy = ...,
    simplify_llm_policy: LLMInvokePolicy = ...,
    two_stage: bool = False,
    review_agent_max_iterations: int = 25,
    sharing_config: Optional[Dict[str, Any]] = None,
    disabled_skills: Optional[Union[str, list[str]]] = None,
    evolution_trigger: EvolutionTriggerPoint = EvolutionTriggerPoint.AFTER_INVOKE,
    async_evolution: bool = True,
    max_concurrent_evolution: int = 1,
    review_trigger: Optional[bool] = None,
    review_interval: int = 5,
)
```

**Parameters**:

* **skills_dir** (Union[str, list[str]]): Skill directory path or path list.
* **llm** (Model): LLM client instance used by signal, record, scoring, and governance stages.
* **model** (str): Model name.
* **signal_trigger** (bool, optional): Whether to run passive signal scanning after invoke. Defaults to `False`.
* **auto_save** (bool): Whether generated passive records are auto-approved and persisted. Defaults to `False`.
* **review_runtime** (EvolutionReviewRuntime): Shared active-review state for review subagent bindings.
* **subject_kind** (str): Subject kind used by this rail (`"skill"` or `"swarm-skill"` normalized).
* **language** (str): Prompt language, commonly `"cn"` or `"en"`.
* **trajectory_span_processor** (TrajectorySpanProcessor): Shared processor already registered with the runtime's OpenTelemetry provider.
* **eval_interval** (int): Number of presentations between experience scoring checks. Must be at least 1.
* **evolution_total_timeout_secs** (float): Background evolution timeout budget.
* **generate_records_llm_policy** (LLMInvokePolicy): LLM retry/timeout policy for record generation.
* **evaluate_llm_policy** (LLMInvokePolicy): LLM retry/timeout policy for experience scoring.
* **simplify_llm_policy** (LLMInvokePolicy): LLM retry/timeout policy for simplify governance.
* **two_stage** (bool): Whether record generation uses the analyzer + formatter pipeline. Defaults to `False`.
* **review_agent_max_iterations** (int): Maximum iterations for `evolution_reviewer`, defaults to 25.
* **sharing_config** (dict, optional): Cross-user sharing settings such as `enabled` and `hub_path`.
* **disabled_skills** (Optional[Union[str, list[str]]], optional): Deny-list of skill names excluded from self-optimization. Supports a single skill name (str) or multiple names (list[str]).
* **evolution_trigger** (EvolutionTriggerPoint): Rail callback point for passive evolution. Defaults to `AFTER_INVOKE`.
* **async_evolution** (bool): Whether passive evolution runs asynchronously. Defaults to `True`.
* **max_concurrent_evolution** (int): Maximum concurrent passive evolution tasks. Defaults to 1.
* **review_trigger** (bool, optional): Whether to periodically enqueue a short evolution self-check follow_up. Defaults to `False`.
* **review_interval** (int): Number of non-follow_up task iterations between review checks. Must be at least 1; defaults to 5.

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

Evolution store for Skill experience data. Execution trajectory capture remains in the injected processor and the Rail's clean window.

### store -> EvolutionStore

Backward-compatible alias for `evolution_store`.

### scorer -> ExperienceScorer

Experience scorer.

### evolver -> SkillExperienceOptimizer

Regular skill experience optimizer.

### evolution_config -> dict

Effective record-generation, evaluation, and simplify LLM policies, the total timeout, and `two_stage` mode.

---

## Methods

### async request_user_evolution(skill_name, user_intent="", *, auto_approve=None, max_index_records=None) -> EvolutionRequestResult

Build a host-delivered active evolution command prompt for a regular skill. The prompt does not create a review scope directly; it instructs the main agent to call `prepare_skill_evolution(user_confirmed=true)` and then use `evolve_review_task(evolution_review_ref=...)` with the returned `evolution_review_ref`.

**Parameters**:

* **skill_name** (str): Target regular skill name.
* **user_intent** (str): User improvement intent, defaults to `""`.
* **auto_approve** (bool, optional): Accepted for compatibility and ignored by the active-review path.
* **max_index_records** (int, optional): Accepted for compatibility and ignored by the active-review path.

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
from openjiuwen.harness.rails import EvolutionInterruptRail, EvolutionReviewRuntime, SkillEvolutionRail

runtime = EvolutionReviewRuntime()
skill_rail = SkillEvolutionRail(
    skills_dir="/path/to/skills",
    llm=model_client,
    model="gpt-4",
    trajectory_span_processor=runtime_processor,
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
    rails=[interrupt_rail, skill_rail],
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
