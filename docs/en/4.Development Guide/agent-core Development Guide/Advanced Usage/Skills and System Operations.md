# Skills and System Operations

Skills and System Operations (SysOperation) are a set of capabilities newly added in the current version of openJiuwen, used to safely expose **skill knowledge on disk + controlled system capabilities** to Agents.

This chapter is based on the source code `openjiuwen.core.skills` and `openjiuwen.core.sys_operation`, and introduces how to:

- Register skill directories for Agents through `SkillUtil`;
- Automatically mount built-in tools for Agents such as viewing files, executing code, and executing commands;
- Inject skill information into Agent prompts.

> Note: The examples below only use classes/methods that actually exist in the source code (e.g., `SkillUtil.register_skills`, `SkillUtil.get_skill_prompt`), and no longer use interfaces that have been removed in older versions.

---

## 1. Capability Overview

### 1.1 Role of the Skills Module

Source code entry: `openjiuwen.core.skills`, which mainly exports three classes:

- `SkillManager`: Maintains the skill registry, responsible for scanning skill directories and parsing `Skill.md` metadata.
- `SkillToolKit`: Creates built-in tools based on SysOperation (view files, execute Python, execute Shell).
- `SkillUtil`: A one-stop business-oriented wrapper that combines the above two to complete "register skills + mount tools + generate prompts".

In advanced usage, most scenarios only need to directly use `SkillUtil`.

### 1.2 Role of SysOperation

Source code entry: `openjiuwen.core.sys_operation`, which exports:

- `OperationMode`: Runtime mode enumeration (e.g., local / sandbox).
- `LocalWorkConfig`, `SandboxGatewayConfig`: Configuration for local / sandbox modes.
- `SysOperationCard`: Card describing a SysOperation instance.
- `SysOperation`: Entry class that actually interfaces with the file system, code execution, and Shell.

The Skills module does not directly access the operating system, but completes all operations through three sub-interfaces of SysOperation:

- `sys_op.fs()`: Read/write files, list directories, etc.;
- `sys_op.code()`: Execute code, such as Python code blocks;
- `sys_op.shell()`: Execute Shell commands.

> In actual engineering projects, SysOperation instances are usually registered uniformly by the Runner's resource manager at startup and assigned a convention-based `sys_operation_id` (e.g., `"default_sys_op"`). Skills-related code only needs to get this id, without caring about specific registration details.

---

## 2. Prepare Skill Directory (Skill.md)

Skills discover skills by scanning disk directories. A typical skill directory structure is as follows:

```text
skills_root/
  ├─ data_analysis/
  │    ├─ Skill.md
  │    └─ examples/ ...
  └─ sql_knowledge/
       ├─ Skill.md
       └─ cheatsheet.sql
```

Each subdirectory must contain a `Skill.md` file.

In the source code, `SkillManager` only depends on the `description` field in the YAML front matter of `Skill.md` to generate skill descriptions. The rest of the content is freely defined by the business, for example:

```markdown
---
name: sql_knowledge
description: Provides Agents with common SQL syntax, optimization techniques, and other knowledge
---

This is the detailed description of the skill, which can include examples, best practices, etc.
```

Key points:

- Skill names are taken from directory names by default (e.g., `sql_knowledge`).
- `description` will be displayed in skill prompts. It is recommended to write it as a concise but informative Chinese description.

---

## 3. Using SkillUtil to Mount Skills for Agents

### 3.1 Create SkillUtil

`SkillUtil` is the main entry point for advanced usage. A typical initialization method is as follows:

```python
from openjiuwen.core.skills import SkillUtil


async def setup_agent_with_skills(agent, sys_operation_id: str = "default_sys_op"):
    # 1. Create SkillUtil and specify the SysOperation id to use
    skill_util = SkillUtil(sys_operation_id=sys_operation_id)

    # 2. Register skill directory and mount skill-related tools for Agent
    await skill_util.register_skills(
        skill_path="/path/to/skills_root",  # skills_root directory path
        agent=agent,
        session_id="session_001",
    )

    # 3. Generate skill prompt fragment, which can be concatenated into the Agent's system prompt
    skill_prompt = skill_util.get_skill_prompt()
    print(skill_prompt)
```

All interfaces used in the above code can be found in the source code:

- `SkillUtil.__init__(sys_operation_id: str)`
- `SkillUtil.register_skills(skill_path: str, agent, session_id: str | None = None) -> bool`
- `SkillUtil.get_skill_prompt() -> str`

SkillUtil internally does two things:

- Calls `SkillToolKit.add_skill_tools(agent)` to mount built-in tools for the Agent;
- Calls `SkillManager.register(Path(skill_path), session_id)` to scan the skill directory and register skills.

### 3.2 Built-in Tools Overview

According to the implementation of `openjiuwen.core.skills.skill_tool_kit.SkillToolKit`, three local tools (all implemented based on `LocalFunction`) are currently mounted for Agents:

- `view_file`: View text file content.
  - Corresponding internal tool id: `"_internal_view_file"`.
  - Main input parameters: `file_path: str`.
  - Internally reads text content through `sys_operation.fs().read_file(file_path, mode="text")`.
- `execute_python_code`: Execute a block of Python code.
  - Corresponding internal tool id: `"_internal_execute_python_code"`.
  - Main input parameters: `code_block: str`.
  - Internally executes through `sys_operation.code().execute_code(code_block, language="python")` and returns stdout / stderr.
- `run_command`: Execute Shell commands.
  - Corresponding internal tool id: `"_internal_run_command"`.
  - Main input parameters: `bash_command: str`.
  - Internally executes commands through `sys_operation.shell().execute_cmd(bash_command)`.

> These tools are automatically registered to the Runner's resource manager, and their `ToolCard` will be mounted on the Agent's `ability_kit`. For how to call Tools in ReAct Agent, please refer to the instructions on Tool usage in "Building ReActAgent.md".

---

## 4. Using Skills in ReAct / Workflow Agent

Skills itself is only responsible for "providing tools + providing skill prompts" and is decoupled from specific Agent types. A common pattern is:

1. Define an Agent based on ReAct (e.g., `LLMAgent` or other single Agents introduced in "Building ReActAgent.md").
2. After Agent initialization is complete, call the aforementioned `setup_agent_with_skills(agent, sys_operation_id)` to mount skills for it.
3. When constructing the Agent's system prompt, concatenate `skill_util.get_skill_prompt()` into it to guide the large model to use skills and built-in tools as needed.

Pseudo-code example (omitting Agent construction details, only showing concatenation ideas):

```python
BASE_SYSTEM_PROMPT = "You are an agent capable of reading project code and executing commands."

async def build_agent_with_skills():
    agent = create_my_react_agent()  # Reuse the example from Building ReActAgent

    skill_util = SkillUtil(sys_operation_id="default_sys_op")
    await skill_util.register_skills(
        skill_path="/path/to/skills_root",
        agent=agent,
        session_id="session_001",
    )

    skill_prompt = skill_util.get_skill_prompt()
    agent.system_prompt = BASE_SYSTEM_PROMPT + "\n\n" + skill_prompt

    return agent
```

> Since `SkillUtil` only interacts with Agent types through `agent.ability_kit` and ToolCard, the above usage also applies to WorkflowAgent or other custom Agents.

---

## 5. Coordination with Memory, Retrieval, and Other Modules

In a complete intelligent agent application, Skills often work together with memory engines, knowledge retrieval, and other modules:

- Manage user profiles and variables through the memory engine (`LongTermMemory`);
- Manage external document knowledge through knowledge retrieval (`SimpleKnowledgeBase`, etc.);
- Extend "operational capabilities" through Skills, such as:
  - Viewing configuration files or code snippets in projects;
  - Executing small amounts of debugging code in a secure environment;
  - Executing controlled Shell commands (e.g., `ls` or `cat`).

These modules are independent of each other in the source code with clear interfaces and can be combined as needed. When using, just ensure:

- Classes and methods mentioned in the documentation (such as `SkillUtil.register_skills`, `SkillUtil.get_skill_prompt`, `OperationMode`, etc.) actually exist in the source code;
- In actual engineering, reasonably configure the runtime mode and working directory of SysOperation according to business needs to avoid unauthorized access.
