# openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer

`openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer` provides the shared LLM-based experience
record generator used by regular and team/swarm Skill evolution.

---

## class SkillExperienceOptimizer

Generates `EvolutionRecord` values from an `EvolutionContext`. The `profile` selects the prompt and parsing path:
`"regular"` for regular Skills and `"team"` for team/swarm Skills.

```text
class SkillExperienceOptimizer(
    llm: Model,
    model: str,
    language: str = "cn",
    generate_records_llm_policy: LLMInvokePolicy = ...,
    profile: str = "regular",
    *,
    two_stage: bool = False,
)
```

**Parameters**:

* **llm** (Model): LLM client instance.
* **model** (str): Model name passed to the LLM client.
* **language** (str): Prompt language, `"cn"` or `"en"`.
* **generate_records_llm_policy** (LLMInvokePolicy): Retry and timeout policy for record generation.
* **profile** (str): `"regular"` or `"team"`. Other values raise `ValueError`.
* **two_stage** (bool): Enables analyzer-then-formatter generation for the regular profile. The team profile uses
  its dedicated team generation path.

### Properties

* **generate_records_llm_policy** (LLMInvokePolicy): Configured record-generation policy.
* **record_llm_policy** (LLMInvokePolicy): Compatibility name for `generate_records_llm_policy`.
* **profile** (str): Active optimizer profile.
* **llm** (Model): Configured LLM client.
* **model** (str): Configured model name.

### staticmethod default_targets() -> List[str]

Returns the experience-record update target used by `SkillExperienceOperator`.

### bind(operators=None, targets=None, **config) -> int

Binds optimizable operators and reads per-Skill `EvolutionContext` values from `config["online_contexts"]`.

### async generate_records(ctx: EvolutionContext) -> List[EvolutionRecord]

Returns no records when `ctx.signals` is empty. Otherwise it dispatches to the selected profile and produces parsed,
validated experience records for the downstream online evolution pipeline.

`TeamSkillEvolutionRail` constructs this optimizer with `profile="team"` for its optional
`signal_trigger` path. Agent-reviewed evolution uses the rail-owned `evolve_review_task` and
`evolution_reviewer` proposal flow instead.
