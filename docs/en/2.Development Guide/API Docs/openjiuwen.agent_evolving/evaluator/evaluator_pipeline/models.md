# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models

The `openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models` module defines core data models used in the evaluation process.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.ExecResult

```
class ExecResult(stdout: str, stderr: str, returncode: int, timed_out: bool)
```

Data class for command execution results, encapsulating output information.

* **stdout**(str): Standard output content. Default: `""`.
* **stderr**(str): Standard error content. Default: `""`.
* **returncode**(int): Command return code. Default: `-1`.
* **timed_out**(bool): Whether the command timed out. Default: `False`.

**Property:**

### success -> bool

Determine if the command executed successfully (return code is 0).

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.Task

```
class Task(task_id: str, instruction: str, metadata: dict[str, Any], environment_spec: dict[str, Any], has_skills: bool, skills: list[str])
```

Data class for evaluation tasks, defining basic information for a single evaluation task.

* **task_id**(str): Unique task identifier.
* **instruction**(str): Task instruction description.
* **metadata**(dict[str, Any], optional): Task metadata including category, difficulty, etc. Default: `{}`.
* **environment_spec**(dict[str, Any], optional): Environment specification defining Docker environment configuration. Default: `{}`.
* **has_skills**(bool, optional): Whether the task requires skills. Default: `False`.
* **skills**(list[str], optional): List of skills associated with the task. Default: `[]`.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.AgentContext

```
class AgentContext(iteration: int, has_skill: bool, previous_result: IterationResult | None, evolution_suggestions: str | None, evolution_files: dict[str, str] | None, n_input_tokens: int, n_output_tokens: int, metadata: dict[str, Any])
```

Data class for Agent execution context, passing runtime context information to the Agent.

* **iteration**(int, optional): Current iteration number. Default: `1`.
* **has_skill**(bool, optional): Whether skills have been loaded. Default: `False`.
* **previous_result**(IterationResult, optional): Result of the previous iteration. Default: `None`.
* **evolution_suggestions**(str, optional): Evolution suggestions generated based on previous evaluation results. Default: `None`.
* **evolution_files**(dict[str, str], optional): Evolution file mapping. Default: `None`.
* **n_input_tokens**(int, optional): Number of input tokens. Default: `0`.
* **n_output_tokens**(int, optional): Number of output tokens. Default: `0`.
* **metadata**(dict[str, Any], optional): Metadata dictionary. Default: `{}`.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.AgentRunResult

```
class AgentRunResult(final_response: str, trajectory: list[dict], execution_time: float, tokens_used: int, raw_output: str, stderr: str, evolution_events: list[dict], metadata: dict[str, Any], llm_logs: dict[str, str] | None)
```

Data class for Agent run results, encapsulating output after Agent executes a task.

* **final_response**(str, optional): Final response from the Agent. Default: `""`.
* **trajectory**(list[dict], optional): Execution trajectory containing detailed step information. Default: `[]`.
* **execution_time**(float, optional): Execution time in seconds. Default: `0.0`.
* **tokens_used**(int, optional): Number of tokens used. Default: `0`.
* **raw_output**(str, optional): Raw output content. Default: `""`.
* **stderr**(str, optional): Error output. Default: `""`.
* **evolution_events**(list[dict], optional): List of evolution events. Default: `[]`.
* **metadata**(dict[str, Any], optional): Metadata dictionary. Default: `{}`.
* **llm_logs**(dict[str, str], optional): LLM log file mapping. Default: `None`.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.EvalResult

```
class EvalResult(passed: bool, pass_rate: float, test_output: str, returncode: int, failed_tests: list[str], test_details: dict[str, Any])
```

Data class for evaluation results, encapsulating benchmark evaluation results.

* **passed**(bool, optional): Whether all tests passed. Default: `False`.
* **pass_rate**(float, optional): Test pass rate (0-1). Default: `0.0`.
* **test_output**(str, optional): Test output content. Default: `""`.
* **returncode**(int, optional): Test return code. Default: `-1`.
* **failed_tests**(list[str], optional): List of failed test names. Default: `[]`.
* **test_details**(dict[str, Any], optional): Test details dictionary. Default: `{}`.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.SkillDelta

```
class SkillDelta(skills: dict[str, str], evolutions: dict[str, str], evolution_files: dict[str, dict[str, str]], changed: bool)
```

Data class for skill change delta, recording skill changes during iterations.

* **skills**(dict[str, str], optional): Mapping of skill names to skill content. Default: `{}`.
* **evolutions**(dict[str, str], optional): Mapping of skill names to evolution JSON. Default: `{}`.
* **evolution_files**(dict[str, dict[str, str]], optional): Mapping of skill names to evolution file mappings. Default: `{}`.
* **changed**(bool, optional): Whether skills have changed. Default: `False`.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.IterationResult

```
class IterationResult(iteration: int, agent_result: AgentRunResult, eval_result: EvalResult, skill_delta: SkillDelta, skill_changed: bool, started_at: datetime, completed_at: datetime)
```

Data class for single iteration results, encapsulating all results of one complete iteration.

* **iteration**(int): Iteration number.
* **agent_result**(AgentRunResult): Agent run result.
* **eval_result**(EvalResult): Evaluation result.
* **skill_delta**(SkillDelta): Skill change delta.
* **skill_changed**(bool, optional): Whether skills changed. Default: `False`.
* **started_at**(datetime, optional): Iteration start time. Default: current time.
* **completed_at**(datetime, optional): Iteration completion time. Default: current time.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.models.PipelineResult

```
class PipelineResult(task_id: str, agent_name: str, benchmark_name: str, total_iterations: int, convergence_achieved: bool, convergence_type: str, results: list[IterationResult], metrics: dict[str, Any], output_dir: Path, report_path: Path | None, timestamp: float)
```

Data class for pipeline execution results, encapsulating final results of the entire evaluation task.

* **task_id**(str): Task ID.
* **agent_name**(str): Agent name.
* **benchmark_name**(str): Benchmark name.
* **total_iterations**(int): Total number of iterations.
* **convergence_achieved**(bool): Whether convergence was achieved.
* **convergence_type**(str, optional): Convergence type. Default: `""`.
* **results**(list[IterationResult], optional): List of iteration results. Default: `[]`.
* **metrics**(dict[str, Any], optional): Evaluation metrics dictionary. Default: `{}`.
* **output_dir**(Path, optional): Output directory path. Default: `"./evolution_results"`.
* **report_path**(Path, optional): Report file path. Default: `None`.
* **timestamp**(float, optional): Timestamp. Default: current time.

### to_dict() -> dict[str, Any]

Convert result to dictionary format for serialization.

**Returns:**

**dict[str, Any]** containing task_id, agent_name, benchmark_name, total_iterations, convergence_achieved, convergence_type, metrics, output_dir, timestamp, etc.