# openjiuwen.core.skills

## class openjiuwen.core.skills.skill_util.SkillUtil

```python
class openjiuwen.core.skills.skill_util.SkillUtil(...)
```

`SkillUtil` is a high-level wrapper for `SkillManager` and `SkillToolKit`, providing one-stop capabilities for **skill registration, tool mounting, and skill prompt generation** for business use.

### Parameters

* **sys_operation_id** (str): Used to construct internal `SkillManager` and `SkillToolKit`. This id will be used to get the corresponding `SysOperation` instance from `Runner.resource_mgr` to complete file/code/command related operations.

### Internal Members

* **_skill_manager** (SkillManager): Skill metadata manager.
* **_skill_tool_kit** (SkillToolKit): Component for creating and mounting skill-related tools.

### set_sys_operation_id

```python
def set_sys_operation_id(sys_operation_id: str) -> None
```

Synchronously update all internal components related to `SysOperation`.

**Parameters:**

* **sys_operation_id** (str): New SysOperation id.

### property skill_manager

```python
@property
def skill_manager() -> SkillManager
```

Returns the internally held `SkillManager` instance for direct access to underlying capabilities when needed.

### property skill_tool_kit

```python
@property
def skill_tool_kit() -> SkillToolKit
```

Returns the internally held `SkillToolKit` instance for direct access to underlying capabilities when needed.

### async invoke register_skills

```python
async def register_skills(
    skill_path: str,
    agent: "BaseAgent",
    session_id: str = None,
) -> bool
```

Register skills in the specified directory and mount all skill-related built-in tools for the Agent.

**Parameters:**

* **skill_path** (str): Skill root directory path. Internally, `Path(skill_path)` is passed to `SkillManager.register(...)`.
* **agent** (BaseAgent): Agent instance to mount tools to.
* **session_id** (str, optional): Session id used when performing file system operations. Default value: None.

#### Behavior

1. Call `SkillToolKit.add_skill_tools(agent)` to mount built-in tools for the Agent: `view_file`, `execute_python_code`, `run_command`.
2. Call `SkillManager.register(Path(skill_path), session_id)` to scan and register skill metadata.

**Returns:**

- None: The current implementation **does not explicitly return** (although the function signature is annotated as `-> bool`), so the runtime return value is `None`.

### has_skill

```python
def has_skill() -> bool
```

Check if at least one skill has been registered.

**Returns:**

- bool: Returns True when `SkillManager.count() > 0`, otherwise returns False.

### get_skill_prompt

```python
def get_skill_prompt() -> str
```

Generate a prompt fragment containing **all registered skill information** for concatenation into the Agent's system prompt.

#### Behavior

- Generate a `system_prompt` requiring the Agent to read relevant skill documents (`Skill.md`/`SKILL.md`) using `view_file` before executing tasks.
- Call `SkillManager.get_all()` to get the skill list and format each skill as a line of information:
  - `"{index}.Skill name: {skill.name}; Skill description: {skill.description}; Skill file path: {skill.directory}"`.
- Use `PromptTemplate` to insert skill information into the `SKILL_PROMPT_CONTENT` template and concatenate with `system_prompt` to return.

**Returns:**

- str: System prompt + skill list prompt (template rendering result).

## class openjiuwen.core.skills.skill_tool_kit.SkillToolKit

```python
class openjiuwen.core.skills.skill_tool_kit.SkillToolKit(...)
```

`SkillToolKit` is responsible for creating a set of skill-related built-in tools based on `SysOperation`, registering these tools in `Runner.resource_mgr`, and mounting them to the Agent's `ability_manager` for convenient invocation during reasoning.

### Parameters

* **sys_operation_id** (str): Id used to get the `SysOperation` instance from `Runner.resource_mgr.get_sys_operation(sys_operation_id)`.

### property sys_operation_id

```python
@property
def sys_operation_id() -> str
```

Read the currently used `sys_operation_id`.

```python
@sys_operation_id.setter
def sys_operation_id(sys_operation_id: str)
```

Update the internally used `sys_operation_id`. When the upper layer switches to a new `SysOperation` instance, this field needs to be synchronized.

### set_runner

```python
def set_runner(runner) -> None
```

Reserved Runner injection interface. The current implementation only saves a reference for future access to Runner context when creating tools.

### create_view_file_tool

```python
def create_view_file_tool() -> LocalFunction
```

Create a local tool for **viewing text files**, returning a `LocalFunction` instance.

#### Tool Card Definition

- id: `_internal_view_file`
- name: `view_file`
- description: Only used to read skill-related text files (such as `.md/.txt`), **does not read binary files** (such as `.pdf/.xlsx/.ppt`, etc.).
- input_params:
  - file_path (str): Path of the file to view.

#### Tool Function Behavior

- Call `sys_operation.fs().read_file(file_path, mode="text")` to read file content;
- If `bytes/bytearray` is read, return a prompt message (guide to use `execute_python_code` to read binary);
- If `SysOperation` cannot be obtained, return `"sys_operation is not available"`.

### create_execute_python_code_tool

```python
def create_execute_python_code_tool() -> LocalFunction
```

Create a local tool for **executing Python code blocks**.

#### Tool Card Definition

* **id**: `_internal_execute_python_code`
* **name**: `execute_python_code`
* **description**: `Execute Python code`
* **input_params**:
  * **code_block** (str): Python code to execute.

#### Tool Function Behavior

- Call `sys_operation.code().execute_code(code_block, language="python")` to execute code;
- If the returned result contains `stdout/stderr`, concatenate as `stdout + stderr` to form the final output string;
- If `SysOperation` cannot be obtained, return `"sys_operation is not available"`.

### create_execute_command_tool

```python
def create_execute_command_tool() -> LocalFunction
```

Create a local tool for **executing Shell commands**.

#### Tool Card Definition

* **id**: `_internal_run_command`
* **name**: `run_command`
* **description**: `Execute bash commands in a Linux terminal`
* **input_params**:
  * **bash_command** (str): One or more command strings to execute.

#### Tool Function Behavior

- Call `sys_operation.shell().execute_cmd(bash_command)` to execute the command;
- If the returned result contains `stdout/stderr`, concatenate as `stdout + stderr` to form the final output string;
- If `SysOperation` cannot be obtained, return `"sys_operation is not available"`.

### add_skill_tools

```python
def add_skill_tools(agent)
```

Create and register all skill-related built-in tools for the specified Agent and mount them to the Agent.

#### Behavior

1. Create tools in sequence:
   - `create_execute_python_code_tool()`
   - `create_execute_command_tool()`
   - `create_view_file_tool()`
2. Register tools through `Runner.resource_mgr.add_tool([...])` (can be called at the resource manager level).
3. Add each tool's `card` to `agent.ability_manager` so the Agent can call these capabilities during reasoning.

> Usually, `SkillToolKit` is not called directly. Instead, use `SkillUtil.register_skills(...)` to complete the "register skills + mount tools" process in one go.

## class openjiuwen.core.skills.skill_manager.Skill

```python
class openjiuwen.core.skills.skill_manager.Skill(BaseModel)
```

Represents metadata information for a single skill (Skill).

### Fields

- name (str): Skill name, generally corresponding to the skill directory name (derived from `Path.name` of the directory where `Skill.md` is located).
- description (str, optional): Skill description, parsed from the `description` field in the YAML front matter of `Skill.md`. Default value: None.
- directory (pathlib.Path): Directory path where the skill is located.

### Note

- `Skill` implements `__str__` and `__repr__` for easy printing and debugging.

## class openjiuwen.core.skills.skill_manager.SkillManager

```python
class openjiuwen.core.skills.skill_manager.SkillManager(...)
```

Responsible for registration, querying, and management of skill metadata.

### Parameters

* **sys_operation_id** (str): Id used to get the `SysOperation` instance from `Runner.resource_mgr.get_sys_operation(sys_operation_id)`. All subsequent file system operations are executed based on this `SysOperation`.

### set_sys_operation_id

```python
def set_sys_operation_id(sys_operation_id: str) -> None
```

Update the internally used `sys_operation_id`. All subsequent file system operations will be executed based on the new `SysOperation` instance.

**Parameters:**

* **sys_operation_id** (str): New SysOperation id.

### async invoke register

```python
async def register(
    skill_path: Union[Path, List[Path]],
    session_id: str = None,
    overwrite: bool = False
) -> None
```

Register one or more skill directories (or a single `Skill.md` file path).

**Parameters:**

- skill_path (pathlib.Path | List[pathlib.Path]): Skill root directory path or list of paths.
  - When it is a directory: Traverse its **one-level subdirectories** and search for files named `Skill.md` (case-insensitive) within the subdirectories.
  - When it is not recognized as a directory: Will try to treat `skill_path` as a file path and directly read and parse that file.
- session_id (str, optional): Session id used when performing file operations. Default value: None.
- overwrite (bool, optional): Whether to overwrite existing skill records. Default value: False.

#### Behavior

- Execute through `SysOperation.fs()`:
  - `list_directories(root, recursive=False)`
  - `list_files(child_dir, recursive=False)`
  - `read_file(skill_md_path, mode="text", encoding="utf-8")`
- Parse YAML front matter in `Skill.md` (when starting with `---`, split by `text.split("---", 2)`).
- Read the `description` field from YAML, construct `Skill(name=directory_name, description=..., directory=directory_path)`, and write to internal registry.

**Returns:**

- None: Does not return skill list or count after registration (can query with `count()/get_all()`).

### unregister

```python
def unregister(name: str) -> None
```

Unregister a skill by name (ignores if the name does not exist).

**Parameters:**

- name (str): Name of the skill to unregister.

### get

```python
def get(name: str) -> Optional[Skill]
```

Get skill metadata by name.

**Parameters:**

- name (str): Skill name.

**Returns:**

- Skill | None: Returns `Skill` if it exists, otherwise returns None.

### get_all

```python
def get_all() -> List[Skill]
```

Return a list of all currently registered skills.

**Returns:**

- List[Skill]: All registered skills.

### get_names

```python
def get_names() -> List[str]
```

Return a list of all currently registered skill names.

**Returns:**

- List[str]: All registered skill names.

### has

```python
def has(name: str) -> bool
```

Check if a skill with the specified name exists in the registry.

**Parameters:**

- name (str): Skill name.

**Returns:**

- bool: Returns True if it exists, otherwise returns False.

### clear

```python
def clear() -> None
```

Clear all registered skills.

### count

```python
def count() -> int
```

Return the number of currently registered skills.

**Returns:**

- int: Number of skills.
