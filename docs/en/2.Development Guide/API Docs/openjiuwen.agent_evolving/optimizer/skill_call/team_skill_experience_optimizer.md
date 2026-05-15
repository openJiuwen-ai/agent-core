# openjiuwen.agent_evolving.optimizer.skill_call.team_skill_experience_optimizer

`openjiuwen.agent_evolving.optimizer.skill_call.team_skill_experience_optimizer` provides LLM-driven team skill experience record generation and evolution support, extracting reusable collaboration patterns from execution trajectories.

---

## class TeamSkillExperienceOptimizer

LLM-driven team skill experience optimizer. The primary online path is aggregated `generate_records(EvolutionContext)`, which combines trajectory and user-intent signals, supports JSON repair/regeneration, and returns experience records targeting `description`, `body`, or `script`.

```text
class TeamSkillExperienceOptimizer(
    llm: Model,
    model: str,
    language: str = "en",
    debug_dir: Optional[str] = None,
    record_llm_policy: LLMInvokePolicy = ...,
)
```

**Parameters**:

* **llm** (Model): LLM client instance.
* **model** (str): Model name.
* **language** (str): Language setting, supports `"cn"` or `"en"`, defaults to `"en"`.
* **debug_dir** (str, optional): Debug output directory for saving raw LLM responses.
* **record_llm_policy** (LLMInvokePolicy): LLM invocation policy for experience record generation, defaults to timeout 120s, total budget 420s, max retries 3.
