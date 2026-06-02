# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.base

`openjiuwen.agent_evolving.evaluator.evaluator_pipeline.base` 模块定义了 Agent 和 Benchmark 的抽象适配器接口。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.base.BaseAgentAdapter

```
class BaseAgentAdapter(config: dict[str, Any] | None)
```

Agent 适配器的抽象基类。开发者实现自定义 Agent 时，需要继承 `BaseAgentAdapter` 这个基类。

**参数：**

* **config**(dict[str, Any]，可选)：Agent 配置字典。默认值：`None`。

### abstractmethod name() -> str

获取 Agent 名称。

**返回：**

**str**，Agent 的唯一标识名称。

### abstractmethod supported_skills_modes() -> list[str]

获取支持的技能模式列表。

**返回：**

**list[str]**，支持的技能模式名称列表。

### default_model() -> str | None

获取默认模型名称。

**返回：**

**str** 或 **None**，默认模型名称，默认返回 `None`。

### validate_config() -> list[str]

验证配置是否有效。

**返回：**

**list[str]**，配置错误信息列表，默认返回空列表。

### logs_dir -> Path

获取日志目录。

**返回：**

**Path**，日志目录路径。**注意**：调用前必须先调用 `set_logs_dir()`。

### set_logs_dir(logs_dir: Path) -> None

设置日志目录。

**参数：**

* **logs_dir**(Path)：日志目录路径。

### abstractmethod async setup(env: DockerEnvironment) -> bool

初始化 Agent。

**参数：**

* **env**(DockerEnvironment)：Docker 环境对象。

**返回：**

**bool**，初始化是否成功。

### abstractmethod async run(env: DockerEnvironment, task: Task, context: AgentContext) -> AgentRunResult

运行 Agent 执行任务。

**参数：**

* **env**(DockerEnvironment)：Docker 环境对象。
* **task**(Task)：任务对象。
* **context**(AgentContext)：Agent 上下文。

**返回：**

**AgentRunResult**，Agent 运行结果。

### async load_skills(env: DockerEnvironment, skills: dict[str, str], evolutions: dict[str, str] | None, evolution_files: dict[str, dict[str, str]] | None) -> int

加载技能到 Agent。

**参数：**

* **env**(DockerEnvironment)：Docker 环境对象。
* **skills**(dict[str, str])：技能名称到内容的映射。
* **evolutions**(dict[str, str]，可选)：进化信息映射。默认值：`None`。
* **evolution_files**(dict[str, dict[str, str]]，可选)：进化文件映射。默认值：`None`。

**返回：**

**int**，加载的技能数量，默认返回 `0`。

### set_skill_context(resolved_name: str, all_names: list[str]) -> None

设置技能上下文。

**参数：**

* **resolved_name**(str)：解析后的技能名称。
* **all_names**(list[str])：所有技能名称列表。

### async load_skills_from_dir(env: DockerEnvironment, skills_dir: Path) -> list[str]

从目录加载技能。

**参数：**

* **env**(DockerEnvironment)：Docker 环境对象。
* **skills_dir**(Path)：技能目录路径。

**返回：**

**list[str]**，加载的技能名称列表，默认返回空列表。

### captured_evolution_json -> dict[str, str]

获取捕获的进化 JSON 文件。

**返回：**

**dict[str, str]**，文件名到内容的映射，默认返回空字典。

### async capture_skills(env: DockerEnvironment) -> SkillDelta

捕获当前技能状态。

**参数：**

* **env**(DockerEnvironment)：Docker 环境对象。

**返回：**

**SkillDelta**，技能变更增量对象，默认返回空的 SkillDelta。

### get_source_files() -> dict[str, Any] | None

获取 Agent 安装所需的源文件配置。

**返回：**

**dict[str, Any]** 或 **None**，包含安装配置的字典，格式如下：
- `mode`：安装模式，可选值：`"local"`、`"git"`、`"pypi"`
- `sources`：本地源目录映射（仅 `mode="local"` 时有效）
- `packages`：pip 包列表（`mode="git"` 或 `mode="pypi"` 时有效）

默认返回 `None`。

**样例：**

```python
class MyAgent(BaseAgentAdapter):
    @staticmethod
    def name() -> str:
        return "my_agent"
    
    def supported_skills_modes(self) -> list[str]:
        return ["tool_use", "code_execution"]
    
    async def setup(self, env: DockerEnvironment) -> bool:
        # 初始化 Agent 环境
        return True
    
    async def run(self, env: DockerEnvironment, task: Task, context: AgentContext) -> AgentRunResult:
        # 执行任务逻辑
        return AgentRunResult(final_response="完成任务")

# 创建自定义 Agent 实例
agent = MyAgent({"model": "glm-5"})
```

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.base.BaseBenchAdapter

```
class BaseBenchAdapter(config: dict[str, Any] | None)
```

基准测试适配器的抽象基类。开发者实现自定义基准测试时，需要继承 `BaseBenchAdapter` 这个基类。

**参数：**

* **config**(dict[str, Any]，可选)：基准测试配置字典。默认值：`None`。

### abstractmethod name() -> str

获取基准测试名称。

**返回：**

**str**，基准测试的唯一标识名称。

### abstractmethod load_tasks() -> list[Task]

加载所有测试任务。

**返回：**

**list[Task]**，任务列表。

### abstractmethod async prepare_environment(task: Task, env: DockerEnvironment) -> None

准备任务执行环境。

**参数：**

* **task**(Task)：任务对象。
* **env**(DockerEnvironment)：Docker 环境对象。

### abstractmethod async evaluate(env: DockerEnvironment, task: Task) -> EvalResult

执行评估。

**参数：**

* **env**(DockerEnvironment)：Docker 环境对象。
* **task**(Task)：任务对象。

**返回：**

**EvalResult**，评估结果。

### clone_repo() -> bool

自动克隆或更新基准测试仓库。

**返回：**

**bool**，成功返回 `True`，失败返回 `False`，默认返回 `True`。

### task_base_path() -> str

获取任务基础路径。

**返回：**

**str**，任务基础路径，默认返回空字符串。

### filter_tasks(tasks: list[Task], task_ids: list[str] | None, categories: list[str] | None, difficulties: list[str] | None) -> list[Task]

根据条件过滤任务。

**参数：**

* **tasks**(list[Task])：原始任务列表。
* **task_ids**(list[str]，可选)：任务 ID 列表，过滤指定 ID 的任务。默认值：`None`。
* **categories**(list[str]，可选)：分类列表，过滤指定分类的任务。默认值：`None`。
* **difficulties**(list[str]，可选)：难度列表，过滤指定难度的任务。默认值：`None`。

**返回：**

**list[Task]**，过滤后的任务列表。

### aggregate(results: list[EvalResult]) -> dict[str, Any]

聚合多个评估结果。

**参数：**

* **results**(list[EvalResult])：评估结果列表。

**返回：**

**dict[str, Any]**，包含 overall_score（综合得分）、passed（通过数）、total（总数）。

**样例：**

```python
class MyBench(BaseBenchAdapter):
    @staticmethod
    def name() -> str:
        return "my_bench"
    
    def load_tasks(self) -> list[Task]:
        # 加载测试任务
        return [Task(task_id="task1", instruction="测试任务")]
    
    async def prepare_environment(self, task: Task, env: DockerEnvironment) -> None:
        # 准备环境
        pass
    
    async def evaluate(self, env: DockerEnvironment, task: Task) -> EvalResult:
        # 执行评估
        return EvalResult(passed=True, pass_rate=1.0)

# 创建自定义基准测试实例
bench = MyBench({"tasks_dir": "./tasks"})
```