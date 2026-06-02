# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models

`openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models` 模块定义了评估流程中使用的核心数据模型。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.ExecResult

```
class ExecResult(stdout: str, stderr: str, returncode: int, timed_out: bool)
```

命令执行结果的数据类，封装命令执行的输出信息。

* **stdout**(str)：标准输出内容。默认值：`""`。
* **stderr**(str)：标准错误输出内容。默认值：`""`。
* **returncode**(int)：命令返回码。默认值：`-1`。
* **timed_out**(bool)：是否超时。默认值：`False`。

**属性：**

### success -> bool

判断命令是否执行成功（返回码为0）。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.Task

```
class Task(task_id: str, instruction: str, metadata: dict[str, Any], environment_spec: dict[str, Any], has_skills: bool, skills: list[str])
```

评估任务的数据类，定义了单个评估任务的基本信息。

* **task_id**(str)：任务唯一标识符。
* **instruction**(str)：任务指令描述。
* **metadata**(dict[str, Any]，可选)：任务元数据，包含分类、难度等信息。默认值：`{}`。
* **environment_spec**(dict[str, Any]，可选)：环境规格，定义任务所需的 Docker 环境配置。默认值：`{}`。
* **has_skills**(bool，可选)：任务是否需要技能。默认值：`False`。
* **skills**(list[str]，可选)：任务关联的技能列表。默认值：`[]`。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.AgentContext

```
class AgentContext(iteration: int, has_skill: bool, previous_result: IterationResult | None, evolution_suggestions: str | None, evolution_files: dict[str, str] | None, n_input_tokens: int, n_output_tokens: int, metadata: dict[str, Any])
```

Agent 执行上下文的数据类，传递给 Agent 的运行时上下文信息。

* **iteration**(int，可选)：当前迭代次数。默认值：`1`。
* **has_skill**(bool，可选)：是否已加载技能。默认值：`False`。
* **previous_result**(IterationResult，可选)：上一次迭代的结果。默认值：`None`。
* **evolution_suggestions**(str，可选)：进化建议，基于上一次评估结果生成。默认值：`None`。
* **evolution_files**(dict[str, str]，可选)：进化文件映射。默认值：`None`。
* **n_input_tokens**(int，可选)：输入 token 数量。默认值：`0`。
* **n_output_tokens**(int，可选)：输出 token 数量。默认值：`0`。
* **metadata**(dict[str, Any]，可选)：元数据字典。默认值：`{}`。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.AgentRunResult

```
class AgentRunResult(final_response: str, trajectory: list[dict], execution_time: float, tokens_used: int, raw_output: str, stderr: str, evolution_events: list[dict], metadata: dict[str, Any], llm_logs: dict[str, str] | None)
```

Agent 运行结果的数据类，封装 Agent 执行任务后的输出。

* **final_response**(str，可选)：Agent 的最终响应。默认值：`""`。
* **trajectory**(list[dict]，可选)：执行轨迹，包含每一步的详细信息。默认值：`[]`。
* **execution_time**(float，可选)：执行耗时（秒）。默认值：`0.0`。
* **tokens_used**(int，可选)：使用的 token 数量。默认值：`0`。
* **raw_output**(str，可选)：原始输出内容。默认值：`""`。
* **stderr**(str，可选)：错误输出。默认值：`""`。
* **evolution_events**(list[dict]，可选)：进化事件列表。默认值：`[]`。
* **metadata**(dict[str, Any]，可选)：元数据字典。默认值：`{}`。
* **llm_logs**(dict[str, str]，可选)：LLM 日志文件映射。默认值：`None`。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.EvalResult

```
class EvalResult(passed: bool, pass_rate: float, test_output: str, returncode: int, failed_tests: list[str], test_details: dict[str, Any])
```

评估结果的数据类，封装基准测试的评估结果。

* **passed**(bool，可选)：是否通过所有测试。默认值：`False`。
* **pass_rate**(float，可选)：测试通过率（0-1）。默认值：`0.0`。
* **test_output**(str，可选)：测试输出内容。默认值：`""`。
* **returncode**(int，可选)：测试返回码。默认值：`-1`。
* **failed_tests**(list[str]，可选)：失败的测试名称列表。默认值：`[]`。
* **test_details**(dict[str, Any]，可选)：测试详情字典。默认值：`{}`。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.SkillDelta

```
class SkillDelta(skills: dict[str, str], evolutions: dict[str, str], evolution_files: dict[str, dict[str, str]], changed: bool)
```

技能变更增量的数据类，记录技能在迭代中的变化。

* **skills**(dict[str, str]，可选)：技能名称到技能内容的映射。默认值：`{}`。
* **evolutions**(dict[str, str]，可选)：技能名称到进化 JSON 的映射。默认值：`{}`。
* **evolution_files**(dict[str, dict[str, str]]，可选)：技能名称到进化文件映射的映射。默认值：`{}`。
* **changed**(bool，可选)：技能是否发生变化。默认值：`False`。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.IterationResult

```
class IterationResult(iteration: int, agent_result: AgentRunResult, eval_result: EvalResult, skill_delta: SkillDelta, skill_changed: bool, started_at: datetime, completed_at: datetime)
```

单次迭代结果的数据类，封装一次完整迭代的所有结果。

* **iteration**(int)：迭代次数。
* **agent_result**(AgentRunResult)：Agent 运行结果。
* **eval_result**(EvalResult)：评估结果。
* **skill_delta**(SkillDelta)：技能变更增量。
* **skill_changed**(bool，可选)：技能是否变更。默认值：`False`。
* **started_at**(datetime，可选)：迭代开始时间。默认值：当前时间。
* **completed_at**(datetime，可选)：迭代完成时间。默认值：当前时间。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.PipelineResult

```
class PipelineResult(task_id: str, agent_name: str, benchmark_name: str, total_iterations: int, convergence_achieved: bool, convergence_type: str, results: list[IterationResult], metrics: dict[str, Any], output_dir: Path, report_path: Path | None, timestamp: float)
```

流水线执行结果的数据类，封装整个评估任务的最终结果。

* **task_id**(str)：任务 ID。
* **agent_name**(str)：Agent 名称。
* **benchmark_name**(str)：基准测试名称。
* **total_iterations**(int)：总迭代次数。
* **convergence_achieved**(bool)：是否达到收敛。
* **convergence_type**(str，可选)：收敛类型。默认值：`""`。
* **results**(list[IterationResult]，可选)：各次迭代结果列表。默认值：`[]`。
* **metrics**(dict[str, Any]，可选)：评估指标字典。默认值：`{}`。
* **output_dir**(Path，可选)：输出目录路径。默认值：`"./evolution_results"`。
* **report_path**(Path，可选)：报告文件路径。默认值：`None`。
* **timestamp**(float，可选)：时间戳。默认值：当前时间。

### to_dict() -> dict[str, Any]

将结果转换为字典格式，便于序列化存储。

**返回：**

**dict[str, Any]**，包含 task_id、agent_name、benchmark_name、total_iterations、convergence_achieved、convergence_type、metrics、output_dir、timestamp 等字段。