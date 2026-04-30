# openjiuwen.agent_evolving.optimizer.team_skill_optimizer

`openjiuwen.agent_evolving.optimizer.team_skill_optimizer` provides LLM-driven team skill PATCH generation and evolution support, extracting reusable collaboration patterns from execution trajectories.

---

## class TeamSkillOptimizer

LLM-driven team skill PATCH generator that supports three evolution dimensions: trajectory analysis, user intent, and trajectory issue detection.

```text
class TeamSkillOptimizer(
    llm: Model,
    model: str,
    language: str = "en",
    debug_dir: Optional[str] = None,
    patch_llm_policy: LLMInvokePolicy = ...,
)
```

**Parameters**:

* **llm** (Model): LLM client instance.
* **model** (str): Model name.
* **language** (str): Language setting, supports `"cn"` or `"en"`, defaults to `"en"`.
* **debug_dir** (str, optional): Debug output directory for saving raw LLM responses.
* **patch_llm_policy** (LLMInvokePolicy): LLM invocation policy for PATCH generation, defaults to timeout 120s, total budget 420s, max retries 3.

---

## Properties

### language -> str

Language setting (`"cn"` or `"en"`).

### llm -> Model

LLM client instance.

### model -> str

Model name.

### patch_llm_policy -> LLMInvokePolicy

LLM invocation policy configuration for PATCH generation.

---

## Methods

### async generate_patch(trajectory, skill_name, current_skill_content) -> Optional[EvolutionRecord]

Analyze trajectory against existing skill and generate a PATCH if warranted.

**Parameters**:

* **trajectory** (Trajectory): Execution trajectory.
* **skill_name** (str): Skill name.
* **current_skill_content** (str): Current SKILL.md content.

**Returns**:

* `EvolutionRecord` or `None` (when no PATCH needed).

---

### async generate_user_patch(trajectory, skill_name, user_intent) -> Optional[EvolutionRecord]

Generate a PATCH based on explicit user improvement intent.

**Parameters**:

* **trajectory** (Trajectory): Execution trajectory (can be empty or minimal placeholder).
* **skill_name** (str): Skill name.
* **user_intent** (str): User improvement intent description.

**Returns**:

* `EvolutionRecord` or `None`.

---

### async generate_trajectory_patch(trajectory, skill_name, current_skill_content, trajectory_issues) -> Optional[EvolutionRecord]

Generate a PATCH based on trajectory issue analysis results.

**Parameters**:

* **trajectory** (Trajectory): Execution trajectory.
* **skill_name** (str): Skill name.
* **current_skill_content** (str): Current skill content.
* **trajectory_issues** (list[dict]): Trajectory issue list, each element contains `issue_type`, `description`, `affected_role`, `severity`.

**Returns**:

* `EvolutionRecord` or `None`.

---

### async regenerate_body(skill_name, current_body, evolution_records, user_intent) -> Optional[str]

Rewrite skill body, incorporating evolution experiences.

**Parameters**:

* **skill_name** (str): Skill name.
* **current_body** (str): Current SKILL.md body content.
* **evolution_records** (List): Evolution record list.
* **user_intent** (str, optional): User-specified optimization direction.

**Returns**:

* New body text or `None` (when LLM fails or declines).

---

### update_llm(llm, model) -> None

Update LLM client and model name.

---

### staticmethod build_trajectory_summary(trajectory) -> str

Build trajectory summary text for LLM analysis. Prioritizes collaboration-related tool calls (`spawn_member`, `create_task`, `build_team`, etc.), followed by LLM responses.

---

### staticmethod parse_json(raw) -> Optional[Dict]

Parse JSON from LLM output, supporting code block extraction, syntax repair, balanced bracket scanning, and other strategies.

---

## Example

```python
from openjiuwen.agent_evolving.optimizer.team_skill_optimizer import TeamSkillOptimizer
from openjiuwen.core.foundation.llm.model import Model

# Create optimizer
optimizer = TeamSkillOptimizer(
    llm=model_client,
    model="gpt-4",
    language="en",
)

# Generate trajectory PATCH
record = await optimizer.generate_trajectory_patch(
    trajectory=team_trajectory,
    skill_name="research-team",
    current_skill_content=skill_md_content,
    trajectory_issues=[
        {"issue_type": "coordination", "description": "handoff too loose", "severity": "medium"}
    ],
)

# Generate user PATCH
record = await optimizer.generate_user_patch(
    trajectory=trajectory,
    skill_name="research-team",
    user_intent="Add reviewer role, limit research time to 10 minutes",
)

# Build trajectory summary
summary = TeamSkillOptimizer.build_trajectory_summary(trajectory)
```