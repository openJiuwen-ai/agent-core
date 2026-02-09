# openjiuwen.core.skills

## class openjiuwen.core.skills.skill_util.SkillUtil

```python
class openjiuwen.core.skills.skill_util.SkillUtil(...)
```

`SkillUtil` 是对 `SkillManager` 与 `SkillToolKit` 的高级封装，为业务侧提供一站式的**技能注册、工具挂载与技能提示词生成**能力。

### 参数

* **sys_operation_id**(str)：用于构造内部的 `SkillManager` 与 `SkillToolKit`。该 id 会被用于从 `Runner.resource_mgr` 获取对应的 `SysOperation` 实例，以完成文件/代码/命令相关操作。

### 内部成员

* **_skill_manager**(SkillManager)：技能元数据管理器。
* **_skill_tool_kit**(SkillToolKit)：技能相关工具创建与挂载组件。

### set_sys_operation_id

```python
def set_sys_operation_id(sys_operation_id: str) -> None
```

同步更新内部所有与 `SysOperation` 相关的组件。

**参数：**

* **sys_operation_id**(str)：新的 SysOperation id。

### property skill_manager

```python
@property
def skill_manager() -> SkillManager
```

返回内部持有的 `SkillManager` 实例，便于在需要时直接访问底层能力。

### property skill_tool_kit

```python
@property
def skill_tool_kit() -> SkillToolKit
```

返回内部持有的 `SkillToolKit` 实例，便于在需要时直接访问底层能力。

### async invoke register_skills

```python
async def register_skills(
    skill_path: str,
    agent: "BaseAgent",
    session_id: str = None,
) -> bool
```

注册指定目录下的技能，并为 Agent 挂载所有技能相关内置工具。

**参数：**

* **skill_path(str)**：技能根目录路径。内部会 `Path(skill_path)` 后传给 `SkillManager.register(...)`。
* **agent(BaseAgent)**：要挂载工具的 Agent 实例。
* **session_id(str, 可选)**：执行文件系统操作时使用的会话 id。默认值：None。

#### 行为

1. 调用 `SkillToolKit.add_skill_tools(agent)`，为 Agent 挂载内置工具：`view_file`、`execute_python_code`、`run_command`。
2. 调用 `SkillManager.register(Path(skill_path), session_id)` 扫描并注册技能元数据。

**返回：**

- None：当前实现**未显式 return**（尽管函数签名标注为 `-> bool`），因此运行时返回值为 `None`。

### has_skill

```python
def has_skill() -> bool
```

判断当前是否已经注册了至少一个技能。

**返回：**

- bool：当 `SkillManager.count() > 0` 时返回 True，否则返回 False。

### get_skill_prompt

```python
def get_skill_prompt() -> str
```

生成包含**所有已注册技能信息**的提示词片段，用于拼接到 Agent 的系统提示词中。

#### 行为

- 生成一段 `system_prompt`，要求 Agent 在执行任务前先用 `view_file` 阅读相关技能文档（`Skill.md`/`SKILL.md`）。
- 调用 `SkillManager.get_all()` 获取技能列表，并将每个技能格式化为一行信息：
  - `"{index}.Skill name: {skill.name}; Skill description: {skill.description}; Skill file path: {skill.directory}"`。
- 使用 `PromptTemplate` 将技能信息插入 `SKILL_PROMPT_CONTENT` 模板，并与 `system_prompt` 拼接返回。

**返回：**

- str：系统提示词 + 技能清单提示词（模板渲染结果）。

## class openjiuwen.core.skills.skill_tool_kit.SkillToolKit

```python
class openjiuwen.core.skills.skill_tool_kit.SkillToolKit(...)
```

`SkillToolKit` 负责基于 `SysOperation` 创建一组与技能相关的内置工具，并将这些工具注册到 `Runner.resource_mgr` 中，同时挂载到 Agent 的 `ability_manager` 上，方便在推理过程中调用。

### 参数

* **sys_operation_id**(str)：用于从 `Runner.resource_mgr.get_sys_operation(sys_operation_id)` 获取 `SysOperation` 实例的 id。

### property sys_operation_id

```python
@property
def sys_operation_id() -> str
```

读取当前使用的 `sys_operation_id`。

```python
@sys_operation_id.setter
def sys_operation_id(sys_operation_id: str)
```

更新内部使用的 `sys_operation_id`。当上层切换到新的 `SysOperation` 实例时，需要同步更新该字段。

### set_runner

```python
def set_runner(runner) -> None
```

预留的 Runner 注入接口。当前实现仅保存引用，方便未来在创建工具时访问 Runner 上下文。

### create_view_file_tool

```python
def create_view_file_tool() -> LocalFunction
```

创建一个用于**查看文本文件**的本地工具，返回 `LocalFunction` 实例。

#### 工具卡片定义

- id：`_internal_view_file`
- name：`view_file`
- description：只用于读取技能相关的文本文件（如 `.md/.txt`），**不读取二进制文件**（如 `.pdf/.xlsx/.ppt` 等）。
- input_params：
  - file_path(str)：要查看的文件路径。

#### 工具函数行为

- 调用 `sys_operation.fs().read_file(file_path, mode="text")` 读取文件内容；
- 若读取到 `bytes/bytearray`，返回提示信息（引导用 `execute_python_code` 读取二进制）；
- 若无法获取 `SysOperation`，返回 `"sys_operation is not available"`。

### create_execute_python_code_tool

```python
def create_execute_python_code_tool() -> LocalFunction
```

创建一个用于**执行 Python 代码块**的本地工具。

#### 工具卡片定义

* **id**：`_internal_execute_python_code`
* **name**：`execute_python_code`
* **description**：`Execute Python code`
* **input_params**：
  * **code_block(str)**：要执行的 Python 代码。

#### 工具函数行为

- 调用 `sys_operation.code().execute_code(code_block, language="python")` 执行代码；
- 若返回结果包含 `stdout/stderr`，按 `stdout + stderr` 拼接为最终输出字符串；
- 若无法获取 `SysOperation`，返回 `"sys_operation is not available"`。

### create_execute_command_tool

```python
def create_execute_command_tool() -> LocalFunction
```

创建一个用于**执行 Shell 命令**的本地工具。

#### 工具卡片定义

* **id**：`_internal_run_command`
* **name**：`run_command`
* **description**：`Execute bash commands in a Linux terminal`
* **input_params**：
  * **bash_command(str)**：要执行的一条或多条命令字符串。

#### 工具函数行为

- 调用 `sys_operation.shell().execute_cmd(bash_command)` 执行命令；
- 若返回结果包含 `stdout/stderr`，按 `stdout + stderr` 拼接为最终输出字符串；
- 若无法获取 `SysOperation`，返回 `"sys_operation is not available"`。

### add_skill_tools

```python
def add_skill_tools(agent)
```

为指定的 Agent 创建并注册所有技能相关的内置工具，并挂载到 Agent 上。

#### 行为

1. 依次创建工具：
   - `create_execute_python_code_tool()`
   - `create_execute_command_tool()`
   - `create_view_file_tool()`
2. 通过 `Runner.resource_mgr.add_tool([...])` 注册工具（资源管理器层面可被调用）。
3. 将每个工具的 `card` 添加到 `agent.ability_manager`，使 Agent 可在推理过程中调用这些能力。

> 通常不直接调用 `SkillToolKit`，而是通过 `SkillUtil.register_skills(...)` 一次性完成“注册技能 + 挂载工具”的流程。

## class openjiuwen.core.skills.skill_manager.Skill

```python
class openjiuwen.core.skills.skill_manager.Skill(BaseModel)
```

表示单个技能（Skill）的元数据信息。

### 字段

- name(str)：技能名称，一般对应技能目录名（通过 `Skill.md` 所在目录的 `Path.name` 推导）。
- description(str, 可选)：技能描述，从 `Skill.md` 的 YAML front matter 的 `description` 字段解析得到。默认值：None。
- directory(pathlib.Path)：技能所在目录路径。

### 说明

- `Skill` 实现了 `__str__` 与 `__repr__`，便于打印与调试。

## class openjiuwen.core.skills.skill_manager.SkillManager

```python
class openjiuwen.core.skills.skill_manager.SkillManager(...)
```

负责技能元数据的注册、查询与管理。

### 参数

* **sys_operation_id**(str)：用于从 `Runner.resource_mgr.get_sys_operation(sys_operation_id)` 获取 `SysOperation` 实例的 id。后续所有文件系统操作都基于该 `SysOperation` 执行。

### set_sys_operation_id

```python
def set_sys_operation_id(sys_operation_id: str) -> None
```

更新内部使用的 `sys_operation_id`，后续文件系统操作都会基于新的 `SysOperation` 实例执行。

**参数：**

* **sys_operation_id**(str)：新的 SysOperation id。

### async invoke register

```python
async def register(
    skill_path: Union[Path, List[Path]],
    session_id: str = None,
    overwrite: bool = False
) -> None
```

注册一个或多个技能目录（或单个 `Skill.md` 文件路径）。

**参数：**

- skill_path(pathlib.Path | List[pathlib.Path])：技能根目录路径或路径列表。
  - 当为目录时：遍历其**一层子目录**，并在子目录内查找名为 `Skill.md`（大小写不敏感）的文件。
  - 当不被识别为目录时：会尝试把 `skill_path` 当作文件路径，直接读取并解析该文件。
- session_id(str, 可选)：执行文件操作时使用的会话 id。默认值：None。
- overwrite(bool, 可选)：是否覆盖已存在的技能记录。默认值：False。

#### 行为

- 通过 `SysOperation.fs()` 执行：
  - `list_directories(root, recursive=False)`
  - `list_files(child_dir, recursive=False)`
  - `read_file(skill_md_path, mode="text", encoding="utf-8")`
- 在 `Skill.md` 中解析 YAML front matter（以 `---` 开头时，按 `text.split("---", 2)` 拆分）。
- 从 YAML 中读取 `description` 字段，构造 `Skill(name=目录名, description=..., directory=目录路径)`，写入内部注册表。

**返回：**

- None：注册完成后不返回技能列表或数量（可用 `count()/get_all()` 查询）。

### unregister

```python
def unregister(name: str) -> None
```

按照技能名称注销一个技能（若名称不存在则忽略）。

**参数：**

- name(str)：要注销的技能名称。

### get

```python
def get(name: str) -> Optional[Skill]
```

按名称获取技能元数据。

**参数：**

- name(str)：技能名称。

**返回：**

- Skill | None：存在则返回 `Skill`，否则返回 None。

### get_all

```python
def get_all() -> List[Skill]
```

返回当前注册的所有技能列表。

**返回：**

- List[Skill]：所有已注册技能。

### get_names

```python
def get_names() -> List[str]
```

返回当前注册的所有技能名称列表。

**返回：**

- List[str]：所有已注册技能名。

### has

```python
def has(name: str) -> bool
```

判断指定名称的技能是否存在于注册表中。

**参数：**

- name(str)：技能名称。

**返回：**

- bool：存在返回 True，否则返回 False。

### clear

```python
def clear() -> None
```

清空所有已注册技能。

### count

```python
def count() -> int
```

返回当前注册技能的数量。

**返回：**

- int：技能数量。
