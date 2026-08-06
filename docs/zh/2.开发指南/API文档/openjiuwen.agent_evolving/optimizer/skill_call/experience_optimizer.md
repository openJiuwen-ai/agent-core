# openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer

`openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer` 提供普通 Skill 与 team/swarm Skill
共用的 LLM 经验记录生成器。

---

## class SkillExperienceOptimizer

根据 `EvolutionContext` 生成 `EvolutionRecord`。`profile` 决定提示词和解析路径：`"regular"` 用于普通
Skill，`"team"` 用于 team/swarm Skill。

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

**参数**：

* **llm** (Model): LLM 客户端实例。
* **model** (str): 传给 LLM 客户端的模型名称。
* **language** (str): 提示词语言，支持 `"cn"` 或 `"en"`。
* **generate_records_llm_policy** (LLMInvokePolicy): 经验记录生成的重试与超时策略。
* **profile** (str): `"regular"` 或 `"team"`；其它值会抛出 `ValueError`。
* **two_stage** (bool): 为 regular profile 启用 analyzer → formatter 两阶段生成；team profile 使用独立的
  team 生成路径。

### 属性

* **generate_records_llm_policy** (LLMInvokePolicy): 当前经验记录生成策略。
* **record_llm_policy** (LLMInvokePolicy): `generate_records_llm_policy` 的兼容属性名。
* **profile** (str): 当前 optimizer profile。
* **llm** (Model): 当前 LLM 客户端。
* **model** (str): 当前模型名称。

### staticmethod default_targets() -> List[str]

返回 `SkillExperienceOperator` 使用的经验记录更新目标。

### bind(operators=None, targets=None, **config) -> int

绑定可优化 operator，并从 `config["online_contexts"]` 读取各 Skill 的 `EvolutionContext`。

### async generate_records(ctx: EvolutionContext) -> List[EvolutionRecord]

当 `ctx.signals` 为空时返回空列表；否则按所选 profile 生成并解析经验记录，交给后续在线演进流程。

`TeamSkillEvolutionRail` 仅在可选的 `signal_trigger` 路径中使用 `profile="team"`。由 Agent 审核的演进
改为通过 Rail 自有的 `evolve_review_task` 和 `evolution_reviewer` proposal 链路完成。
