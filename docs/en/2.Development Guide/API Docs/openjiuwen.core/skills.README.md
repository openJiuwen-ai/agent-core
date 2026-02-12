## openjiuwen.core.skills

The `openjiuwen.core.skills` module provides unified **skill (Skill) management and built-in auxiliary tools** for Agents, mainly containing three public classes:

- `SkillManager`: Responsible for skill metadata registration, query, and management.
- `SkillToolKit`: Responsible for creating built-in tools related to skills (e.g., view files, execute code, execute commands).
- `SkillUtil`: High-level encapsulation for business, combining `SkillManager` and `SkillToolKit` to provide one-stop interfaces.

In most scenarios, it is recommended to directly use `SkillUtil` to complete skill registration and tool mounting.

### SkillManager (Skill Manager)

Corresponding source: `openjiuwen.core.skills.skill_manager.SkillManager`.

**Core Responsibilities**:

- Maintains skill registry `_registry[name -> Skill]`;
- Reads `description` from YAML front matter in `Skill.md` files;
- Provides basic interfaces for add, delete, query, and modify operations.

**Important Types and Methods**:

- `class Skill(BaseModel)`: Metadata for a single skill, fields: `name: str`, `description: str | None`, `directory: Path`.
- `SkillManager(sys_operation_id: str)`: Constructor, `sys_operation_id` is used to access the file system through `SysOperation`.
- `set_sys_operation_id(sys_operation_id: str) -> None`: Change the `sys_operation_id` used internally.
- `await register(skill_path: Path | list[Path], session_id: str | None = None, overwrite: bool = False)`:
  - Pass in skill root directory or directory list, automatically traverses subdirectories, finds directories containing `Skill.md` and registers them as skills;
  - If `overwrite=False` and the skill already exists, raises `ValueError`.
- `unregister(name: str) -> None`: Unregister skill by name.
- `get(name: str) -> Skill | None`: Get skill by name.
- `get_all() -> list[Skill]`: Get all registered skills.
- `get_names() -> list[str]`: Get all skill name list.
- `has(name: str) -> bool`: Check if the specified skill exists.
- `clear() -> None`: Clear the registry.
- `count() -> int`: Return the current number of registered skills.

> **Note**: `register()` internally depends on `Runner.resource_mgr.get_sys_operation(self._sys_operation_id)` and accesses skill directories and `Skill.md` through `fs().list_directories / list_files / read_file`, so ensure `SysOperation` is correctly registered before use.

### SkillToolKit (Skill Tool Kit)

Corresponding source: `openjiuwen.core.skills.skill_tool_kit.SkillToolKit`.

**Core Responsibilities**:

- Creates a set of built-in tools (Tool) based on `SysOperation`, for:
  - Viewing text file content (`view_file`);
  - Executing Python code (`execute_python_code`);
  - Executing Shell commands (`run_command`);
- Registers these tools in the global `ResourceMgr` and mounts them to the specified Agent's `ability_kit`.

**Key Methods**:

- `SkillToolKit(sys_operation_id: str)`: Constructor.
- `sys_operation_id` property (with getter/setter): Controls the `sys_operation_id` used internally.
- `set_runner(runner) -> None`: Reserved runner injection interface (current implementation only saves reference).
- `create_view_file_tool() -> LocalFunction`:
  - Creates a local tool with `id="_internal_view_file", name="view_file"`;
  - Input: `file_path: str`, outputs the specified text file content or prompt message.
- `create_execute_python_code_tool() -> LocalFunction`:
  - Creates a tool with `id="_internal_execute_python_code", name="execute_python_code"`;
  - Input: `code_block: str`, executed in `sys_operation.code().execute_code()`, returns stdout/stderr.
- `create_execute_command_tool() -> LocalFunction`:
  - Creates a tool with `id="_internal_run_command", name="run_command"`;
  - Input: `bash_command: str`, executes command through `sys_operation.shell().execute_cmd()` and returns output.
- `add_skill_tools(agent) -> None`:
  - Creates the above three tools and registers them through `Runner.resource_mgr.add_tool([...])`;
  - Also adds corresponding `ToolCard` to `agent.ability_kit`, enabling the Agent to call these capabilities during reasoning.

### SkillUtil (High-level Encapsulation for Skill Tools)

Corresponding source: `openjiuwen.core.skills.skill_util.SkillUtil`.

**Core Responsibilities**:

- Aggregates `SkillManager` and `SkillToolKit`, providing easier-to-use one-stop interfaces;
- Registers skill directories for Agents, mounts built-in tools, and generates skill prompt fragments.

**Important Attributes and Methods**:

- `SkillUtil(sys_operation_id: str)`: Constructor.
- `set_sys_operation_id(sys_operation_id: str) -> None`: Synchronously updates the `sys_operation_id` used by internal `SkillManager` and `SkillToolKit`.
- `skill_manager` / `skill_tool_kit` properties: Return internal `SkillManager` and `SkillToolKit` instances respectively.
- `await register_skills(skill_path: str, agent: BaseAgent, session_id: str | None = None) -> bool`:
  - Calls `add_skill_tools()` for the given `agent` to mount three types of built-in tools;
  - Calls `SkillManager.register(Path(skill_path), session_id)` to register skill directory;
  - Returns `True` on successful registration.
- `has_skill() -> bool`: Whether at least one skill is currently registered (determined through `SkillManager.count()`).
- `get_skill_prompt() -> str`:
  - Reads all registered skills, concatenates into multi-line skill information: `Skill name/description/file path`;
  - Renders it into a preset `PromptTemplate`, returns a string that can be directly used for Agent prompts.

### Typical Usage Example

```python
from openjiuwen.core.single_agent.skills import SkillUtil
from openjiuwen.core.single_agent import BaseAgent


async def setup_agent_with_skills(agent: BaseAgent):
    # sys_operation_id is agreed upon by the upper system when registering SysOperation
    skill_util = SkillUtil(sys_operation_id="default_sys_op")

    # Register skills in a directory (directory contains several subdirectories, each with a Skill.md)
    await skill_util.register_skills(
        skill_path="/path/to/skills_root",
        agent=agent,
        session_id="session_001",
    )

    # Generate skill prompt fragment, can be concatenated to Agent system prompt
    skill_prompt = skill_util.get_skill_prompt()
    print(skill_prompt)
```

Through the above interfaces, `openjiuwen.core.skills` implements a closed-loop capability of "file system-based skill registration + built-in tool mounting + skill prompt generation", facilitating unified management and reuse of skill knowledge across different Agents.
