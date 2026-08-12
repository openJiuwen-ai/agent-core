# Reviewer Feedback Attribution

`openjiuwen.agent_evolving.signal.review_feedback` turns aggregated task-review feedback into a structured,
policy-enforced downstream action. The module does not subscribe to team events, mutate a Skill, or create a Skill;
runtime wiring belongs to the host.

## Import

```python
from openjiuwen.agent_evolving.signal import (
    ReviewFeedbackAction,
    ReviewFeedbackAttribution,
    ReviewFeedbackAttributor,
    ReviewFeedbackClassification,
    ReviewFeedbackContext,
    ReviewFeedbackContextBuilder,
    attribution_to_evolution_signal,
)
```

## Attribution results

| Classification | Action | Meaning |
|---|---|---|
| `skill_issue` | `evolve_existing_skill` | A proven-read Skill lacks or misstates reusable guidance. |
| `new_skill_pattern` | `suggest_new_skill` | No proven-read Skill applies and a reusable matching pattern has repeated. |
| `executor_error` | `record_task_failure` | The Skill guidance was adequate but the executor did not follow it. |
| `unattributed` | `skip_unattributed` | Evidence is insufficient for safe attribution. |

`ReviewFeedbackAttribution` also contains `is_skill_actionable`, `skill_name`, `target`, `reason`,
`reusable_guidance`, `confidence`, and a bounded `feedback_excerpt`.

## class ReviewFeedbackAttributor

```python
attributor = ReviewFeedbackAttributor(
    llm=model_client,
    model="model-name",
    language="en",
    timeout=30.0,
)

result = await attributor.attribute(feedback, context=context)
```

The attributor uses one structured LLM call for semantic classification, then enforces deterministic policy over the
model output. Model failures, invalid JSON, empty input, untrusted Skill names, and insufficient evidence all fail
closed to `skip_unattributed`.

## class ReviewFeedbackContextBuilder

```python
context = await ReviewFeedbackContextBuilder(store=evolution_store).build(
    task_id="task-1",
    review_round=1,
    task_objective="Build and validate a workbook",
    trajectory=member_trajectory,
    repetition_count=1,
    repeated_pattern_evidence=(),
)
```

The builder extracts `SKILL.md` read evidence from trajectory tool-call arguments and loads the corresponding Skill
content as bounded attribution context. Merely installing a Skill, mentioning it in model prose, or returning its name
from a tool does not prove that it influenced the task.

## Safety boundaries

- Only `skill_issue` may evolve an existing Skill.
- The target Skill must belong to the trajectory-proven, currently loadable read set.
- Existing-Skill evolution requires reusable guidance and a concrete `description`, `body`, or `script` target.
- `executor_error` never becomes a Skill mutation.
- New-Skill suggestions require repeated evidence; this module returns a suggestion but never creates the Skill.

## Convert to a standard EvolutionSignal

```python
signal = attribution_to_evolution_signal(
    result,
    task_id="task-1",
    review_round=1,
)
```

Only an actionable `evolve_existing_skill` result is converted; other actions return `None`. The signal uses
`signal_type="review_feedback"` and `source="scheduler_review_feedback"` and can be passed to
`SkillEvolutionRail.evolve_from_external_signals(...)` for the standard generation and approval lifecycle.
