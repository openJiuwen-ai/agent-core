## openjiuwen.core.skills

`openjiuwen.core.skills` 模块为 Agent 提供统一的 **技能（Skill）管理与内置辅助工具** 能力，主要包含三个对外类：

- `SkillManager`：负责技能元数据的注册、查询和管理。
- `SkillToolKit`：负责创建与技能相关的内置工具（如查看文件、执行代码、执行命令）。
- `SkillUtil`：面向业务的高级封装，组合 `SkillManager` 与 `SkillToolKit`，提供一站式接口。

在大多数场景下，推荐直接使用 `SkillUtil` 来完成技能注册与工具挂载。

### SkillManager（技能管理器）

对应源码：`openjiuwen.core.skills.skill_manager.SkillManager`。

**核心职责**：

- 维护技能注册表 `_registry[name -> Skill]`；
- 从 `Skill.md` 文件的 YAML front matter 中读取 `description`；
- 提供增删查改等基础接口。

**重要类型与方法**：

- `class Skill(BaseModel)`：单个技能的元数据，字段：`name: str`、`description: str | None`、`directory: Path`。
- `SkillManager(sys_operation_id: str)`：构造函数，`sys_operation_id` 用于通过 `SysOperation` 访问文件系统。
- `set_sys_operation_id(sys_operation_id: str) -> None`：变更底层使用的 `sys_operation_id`。
- `await register(skill_path: Path | list[Path], session_id: str | None = None, overwrite: bool = False)`：
  - 传入技能根目录或目录列表，自动遍历子目录，寻找包含 `Skill.md` 的目录并注册为技能；
  - 若 `overwrite=False` 且技能已存在，会抛出 `ValueError`。
- `unregister(name: str) -> None`：按名称注销技能。
- `get(name: str) -> Skill | None`：按名称获取技能。
- `get_all() -> list[Skill]`：获取全部已注册技能。
- `get_names() -> list[str]`：获取全部技能名称列表。
- `has(name: str) -> bool`：判断指定技能是否存在。
- `clear() -> None`：清空注册表。
- `count() -> int`：返回当前注册技能数量。

> **说明**：`register()` 内部依赖 `Runner.resource_mgr.get_sys_operation(self._sys_operation_id)`，并通过 `fs().list_directories / list_files / read_file` 访问技能目录与 `Skill.md`，因此在使用前需要确保 `SysOperation` 已正确注册。

### SkillToolKit（技能工具集）

对应源码：`openjiuwen.core.skills.skill_tool_kit.SkillToolKit`。

**核心职责**：

- 基于 `SysOperation` 创建一组内置工具（Tool），用于：
  - 查看文本文件内容（`view_file`）；
  - 执行 Python 代码（`execute_python_code`）；
  - 执行 Shell 命令（`run_command`）；
- 将这些工具注册到全局 `ResourceMgr` 中，并挂到指定 Agent 的 `ability_kit` 上。

**关键方法**：

- `SkillToolKit(sys_operation_id: str)`：构造函数。
- `sys_operation_id` 属性（带 getter/setter）：控制内部使用的 `sys_operation_id`。
- `set_runner(runner) -> None`：预留的 runner 注入接口（当前实现中仅保存引用）。
- `create_view_file_tool() -> LocalFunction`：
  - 创建 `id="_internal_view_file", name="view_file"` 的本地工具；
  - 入参：`file_path: str`，输出为指定文本文件内容或提示信息。
- `create_execute_python_code_tool() -> LocalFunction`：
  - 创建 `id="_internal_execute_python_code", name="execute_python_code"` 工具；
  - 入参：`code_block: str`，在 `sys_operation.code().execute_code()` 中执行，并返回 stdout/stderr。
- `create_execute_command_tool() -> LocalFunction`：
  - 创建 `id="_internal_run_command", name="run_command"` 工具；
  - 入参：`bash_command: str`，通过 `sys_operation.shell().execute_cmd()` 执行命令并返回输出。
- `add_skill_tools(agent) -> None`：
  - 创建上述三个工具，并通过 `Runner.resource_mgr.add_tool([...])` 注册；
  - 同时把对应的 `ToolCard` 加入 `agent.ability_kit`，使 Agent 能在推理过程中调用这些能力。

### SkillUtil（技能工具类高级封装）

对应源码：`openjiuwen.core.skills.skill_util.SkillUtil`。

**核心职责**：

- 聚合 `SkillManager` 与 `SkillToolKit`，向上提供更易用的一站式接口；
- 为 Agent 注册技能目录、挂载内置工具，并生成技能提示词片段。

**重要属性与方法**：

- `SkillUtil(sys_operation_id: str)`：构造函数。
- `set_sys_operation_id(sys_operation_id: str) -> None`：同步更新内部 `SkillManager` 与 `SkillToolKit` 所使用的 `sys_operation_id`。
- `skill_manager` / `skill_tool_kit` 属性：分别返回内部的 `SkillManager` 与 `SkillToolKit` 实例。
- `await register_skills(skill_path: str, agent: BaseAgent, session_id: str | None = None) -> bool`：
  - 为给定 `agent` 调用 `add_skill_tools()` 挂载三类内置工具；
  - 调用 `SkillManager.register(Path(skill_path), session_id)` 注册技能目录；
  - 注册成功返回 `True`。
- `has_skill() -> bool`：当前是否已注册至少一个技能（底层通过 `SkillManager.count()` 判断）。
- `get_skill_prompt() -> str`：
  - 读取所有已注册技能，拼接成多行技能信息：`Skill name/description/file path`；
  - 将其渲染进预置的 `PromptTemplate` 中，返回可直接用于 Agent 提示词的字符串。

### 典型使用方式示例

```python
from openjiuwen.core.skills import SkillUtil
from openjiuwen.core.single_agent import BaseAgent


async def setup_agent_with_skills(agent: BaseAgent):
    # sys_operation_id 由上层系统在注册 SysOperation 时约定
    skill_util = SkillUtil(sys_operation_id="default_sys_op")

    # 注册某个目录下的技能（目录中包含若干子目录，每个子目录有一个 Skill.md）
    await skill_util.register_skills(
        skill_path="/path/to/skills_root",
        agent=agent,
        session_id="session_001",
    )

    # 生成技能提示片段，可拼接到 Agent 系统提示词中
    skill_prompt = skill_util.get_skill_prompt()
    print(skill_prompt)
```

通过以上接口，`openjiuwen.core.skills` 实现了“基于文件系统的技能注册 + 内置工具挂载 + 技能提示词生成”的闭环能力，便于在不同 Agent 中统一管理和复用技能知识。

