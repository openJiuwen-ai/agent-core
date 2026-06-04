# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.base

The `openjiuwen.agent_evolving.evaluator.evaluator_pipeline.base` module defines abstract adapter interfaces for Agent and Benchmark.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.base.BaseAgentAdapter

```
class BaseAgentAdapter(config: dict[str, Any] | None)
```

Abstract base class for Agent adapters. Developers should inherit from `BaseAgentAdapter` when implementing custom Agents.

**Parameters:**

* **config**(dict[str, Any], optional): Agent configuration dictionary. Default: `None`.

### abstractmethod name() -> str

Get Agent name.

**Returns:**

**str** - unique identifier name of the Agent.

### abstractmethod supported_skills_modes() -> list[str]

Get list of supported skill modes.

**Returns:**

**list[str]** - list of supported skill mode names.

### default_model() -> str | None

Get default model name.

**Returns:**

**str** or **None** - default model name, defaults to `None`.

### validate_config() -> list[str]

Validate configuration.

**Returns:**

**list[str]** - list of configuration error messages, defaults to empty list.

### logs_dir -> Path

Get logs directory.

**Returns:**

**Path** - logs directory path. **Note**: Must call `set_logs_dir()` first.

### set_logs_dir(logs_dir: Path) -> None

Set logs directory.

**Parameters:**

* **logs_dir**(Path): Logs directory path.

### abstractmethod async setup(env: DockerEnvironment) -> bool

Initialize Agent.

**Parameters:**

* **env**(DockerEnvironment): Docker environment object.

**Returns:**

**bool** - whether initialization was successful.

### abstractmethod async run(env: DockerEnvironment, task: Task, context: AgentContext) -> AgentRunResult

Run Agent to execute task.

**Parameters:**

* **env**(DockerEnvironment): Docker environment object.
* **task**(Task): Task object.
* **context**(AgentContext): Agent context.

**Returns:**

**AgentRunResult** - Agent run result.

### async load_skills(env: DockerEnvironment, skills: dict[str, str], evolutions: dict[str, str] | None, evolution_files: dict[str, dict[str, str]] | None) -> int

Load skills into Agent.

**Parameters:**

* **env**(DockerEnvironment): Docker environment object.
* **skills**(dict[str, str]): Mapping of skill names to content.
* **evolutions**(dict[str, str], optional): Evolution information mapping. Default: `None`.
* **evolution_files**(dict[str, dict[str, str]], optional): Evolution file mapping. Default: `None`.

**Returns:**

**int** - number of skills loaded, defaults to `0`.

### set_skill_context(resolved_name: str, all_names: list[str]) -> None

Set skill context.

**Parameters:**

* **resolved_name**(str): Resolved skill name.
* **all_names**(list[str]): List of all skill names.

### async load_skills_from_dir(env: DockerEnvironment, skills_dir: Path) -> list[str]

Load skills from directory.

**Parameters:**

* **env**(DockerEnvironment): Docker environment object.
* **skills_dir**(Path): Skills directory path.

**Returns:**

**list[str]** - list of loaded skill names, defaults to empty list.

### captured_evolution_json -> dict[str, str]

Get captured evolution JSON files.

**Returns:**

**dict[str, str]** - mapping of filenames to content, defaults to empty dictionary.

### async capture_skills(env: DockerEnvironment) -> SkillDelta

Capture current skill state.

**Parameters:**

* **env**(DockerEnvironment): Docker environment object.

**Returns:**

**SkillDelta** - skill change delta object, defaults to empty SkillDelta.

### get_source_files() -> dict[str, Any] | None

Get source file configuration required for Agent installation.

**Returns:**

**dict[str, Any]** or **None** - dictionary containing installation configuration with the following keys:
- `mode`: Installation mode, possible values: `"local"` | `"git"` | `"pypi"`
- `sources`: dict[name -> path], only valid when mode="local", indicates local source directories
- `packages`: list[str], pip package list (valid for mode="git" or mode="pypi")

Defaults to `None`.

**Example:**

```python
class MyAgent(BaseAgentAdapter):
    @staticmethod
    def name() -> str:
        return "my_agent"
    
    def supported_skills_modes(self) -> list[str]:
        return ["tool_use", "code_execution"]
    
    async def setup(self, env: DockerEnvironment) -> bool:
        # Initialize Agent environment
        return True
    
    async def run(self, env: DockerEnvironment, task: Task, context: AgentContext) -> AgentRunResult:
        # Execute task logic
        return AgentRunResult(final_response="Task completed")

# Create custom Agent instance
agent = MyAgent({"model": "glm-5"})
```

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.base.BaseBenchAdapter

```
class BaseBenchAdapter(config: dict[str, Any] | None)
```

Abstract base class for Benchmark adapters. Developers should inherit from `BaseBenchAdapter` when implementing custom benchmarks.

**Parameters:**

* **config**(dict[str, Any], optional): Benchmark configuration dictionary. Default: `None`.

### abstractmethod name() -> str

Get benchmark name.

**Returns:**

**str** - unique identifier name of the benchmark.

### abstractmethod load_tasks() -> list[Task]

Load all test tasks.

**Returns:**

**list[Task]** - list of tasks.

### abstractmethod async prepare_environment(task: Task, env: DockerEnvironment) -> None

Prepare task execution environment.

**Parameters:**

* **task**(Task): Task object.
* **env**(DockerEnvironment): Docker environment object.

### abstractmethod async evaluate(env: DockerEnvironment, task: Task) -> EvalResult

Execute evaluation.

**Parameters:**

* **env**(DockerEnvironment): Docker environment object.
* **task**(Task): Task object.

**Returns:**

**EvalResult** - evaluation result.

### clone_repo() -> bool

Automatically clone or update benchmark repository.

**Returns:**

**bool** - returns `True` if successful or repository already exists, `False` if failed. Defaults to `True`.

### task_base_path() -> str

Get task base path.

**Returns:**

**str** - task base path, defaults to empty string.

### filter_tasks(tasks: list[Task], task_ids: list[str] | None, categories: list[str] | None, difficulties: list[str] | None) -> list[Task]

Filter tasks by conditions.

**Parameters:**

* **tasks**(list[Task]): Original task list.
* **task_ids**(list[str], optional): Task ID list to filter specific tasks. Default: `None`.
* **categories**(list[str], optional): Category list to filter specific categories. Default: `None`.
* **difficulties**(list[str], optional): Difficulty list to filter specific difficulties. Default: `None`.

**Returns:**

**list[Task]** - filtered task list.

### aggregate(results: list[EvalResult]) -> dict[str, Any]

Aggregate multiple evaluation results.

**Parameters:**

* **results**(list[EvalResult]): List of evaluation results.

**Returns:**

**dict[str, Any]** - containing overall_score, passed, total.

**Example:**

```python
class MyBench(BaseBenchAdapter):
    @staticmethod
    def name() -> str:
        return "my_bench"
    
    def load_tasks(self) -> list[Task]:
        # Load test tasks
        return [Task(task_id="task1", instruction="Test task")]
    
    async def prepare_environment(self, task: Task, env: DockerEnvironment) -> None:
        # Prepare environment
        pass
    
    async def evaluate(self, env: DockerEnvironment, task: Task) -> EvalResult:
        # Execute evaluation
        return EvalResult(passed=True, pass_rate=1.0)

# Create custom benchmark instance
bench = MyBench({"tasks_dir": "./tasks"})
```