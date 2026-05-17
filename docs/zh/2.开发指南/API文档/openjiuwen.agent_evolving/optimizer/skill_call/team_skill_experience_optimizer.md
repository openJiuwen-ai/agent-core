# openjiuwen.agent_evolving.optimizer.skill_call.team_skill_experience_optimizer

`openjiuwen.agent_evolving.optimizer.skill_call.team_skill_experience_optimizer` 提供 LLM 驱动的团队技能经验记录生成与演进支持，从执行轨迹中提炼可复用的团队协作经验。

---

## class TeamSkillExperienceOptimizer

LLM 驱动的团队技能经验优化器。主在线流程为聚合式 `generate_records(EvolutionContext)`，会合并轨迹信号与用户意图信号，支持 JSON repair/regeneration，并返回目标为 `description`、`body` 或 `script` 的经验记录。

```text
class TeamSkillExperienceOptimizer(
    llm: Model,
    model: str,
    language: str = "cn",
    debug_dir: Optional[str] = None,
    record_llm_policy: LLMInvokePolicy = ...,
)
```

**参数**：

* **llm** (Model): LLM 客户端实例。
* **model** (str): 模型名称。
* **language** (str): 语言设置，支持 `"cn"` 或 `"en"`，默认 `"cn"`。
* **debug_dir** (str, 可选): 调试输出目录，用于保存 LLM 原始响应。
* **record_llm_policy** (LLMInvokePolicy): 经验记录生成的 LLM 调用策略，默认超时 120s、总预算 420s、最大重试 3 次。
