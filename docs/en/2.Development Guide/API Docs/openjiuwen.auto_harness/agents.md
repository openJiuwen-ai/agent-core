# openjiuwen.auto_harness.agents

Agent factory function module, providing a unified entry point for creating agents required by each Auto Harness stage. Each factory function encapsulates the prompt, rails, toolset, and sub-agent configuration for the corresponding stage, returning a ready-to-run `DeepAgent` instance.

Submodules:
- `factory`: Agent factory function collection

---

## openjiuwen.auto_harness.agents.factory.create_auto_harness_agent

```python
def create_auto_harness_agent(
    config: 'AutoHarnessConfig',
    *,
    workspace_override: Optional[str] = None,
    edit_safety_rail: Optional['AgentRail'] = None,
    enable_edit_safety: bool = True,
    skill_names: Optional[List[str]] = None,
    enable_task_loop: bool = True,
    enable_task_planning: bool = True,
    enable_progress_repeat: bool = True,
    extra_rails: Optional[List['AgentRail']] = None,
    extra_tools: Optional[List[Tool | ToolCard]] = None,
) -> 'DeepAgent'
```

Create the main task implementation agent. This agent is the core coding agent in the Auto Harness pipeline, with full capabilities including file editing, shell execution, and skill loading, along with built-in rails for context injection, experience retrieval, and security protection.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **workspace_override**(`Optional[str]`): Override the workspace path in configuration.
* **edit_safety_rail**(`Optional[AgentRail]`): Custom edit safety rail; uses default `EditSafetyRail` when `None`.
* **enable_edit_safety**(`bool`): Whether to enable the edit safety rail, default `True`.
* **skill_names**(`Optional[List[str]]`): Enabled skill name list, defaults to `["implement", "verify", "communicate"]`.
* **enable_task_loop**(`bool`): Whether to enable the task loop, default `True`.
* **enable_task_planning**(`bool`): Whether to enable the task planning rail, default `True`.
* **enable_progress_repeat**(`bool`): Whether to enable progress repeat checking in task planning, default `True`.
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.
* **extra_tools**(`Optional[List[Tool | ToolCard]]`): Additional tools to append.

**Returns**: A fully configured `DeepAgent` instance.

---

## openjiuwen.auto_harness.agents.factory.create_commit_agent

```python
def create_commit_agent(
    config: 'AutoHarnessConfig',
    *,
    workspace_override: Optional[str] = None,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

Create a dedicated commit stage agent. Built on the main agent but with task loop and task planning disabled, loading only `commit` and `communicate` skills.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **workspace_override**(`Optional[str]`): Override the workspace path in configuration.
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.

**Returns**: A fully configured `DeepAgent` instance.

---

## openjiuwen.auto_harness.agents.factory.create_assess_agent

```python
def create_assess_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

Create the assessment stage agent. Uses read-only rails (no edit safety rail), loads the `assess` skill, and is equipped with web search and web scraping tools for codebase research.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.

**Returns**: A fully configured `DeepAgent` instance.

---

## openjiuwen.auto_harness.agents.factory.create_plan_agent

```python
def create_plan_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

Create the planning stage agent. Uses read-only rails, loads the `plan` skill, and is equipped with web search and web scraping tools for creating optimization task lists.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.

**Returns**: A fully configured `DeepAgent` instance.

---

## openjiuwen.auto_harness.agents.factory.create_eval_agent

```python
def create_eval_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

Create the evaluation agent used in verify-fix loops. Uses read-only rails, loads `verify` and `verify_ext` skills for reviewing code change quality.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.

**Returns**: A fully configured `DeepAgent` instance.

---

## openjiuwen.auto_harness.agents.factory.create_select_pipeline_agent

```python
def create_select_pipeline_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

Create the pipeline selection agent. Uses read-only rails, loads the `select_pipeline` skill, and is equipped with web search tools for selecting the most suitable optimization pipeline based on codebase state.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.

**Returns**: A fully configured `DeepAgent` instance.

---

## openjiuwen.auto_harness.agents.factory.create_design_ext_agent

```python
def create_design_ext_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

Create the Phase 2 extension design agent. Uses read-only rails, loads the `design_ext` skill, and is equipped with web search tools for designing runtime extension plans.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.

**Returns**: A fully configured `DeepAgent` instance.

---

## openjiuwen.auto_harness.agents.factory.create_pr_draft_agent

```python
def create_pr_draft_agent(
    config: 'AutoHarnessConfig',
    *,
    workspace_override: Optional[str] = None,
    extra_rails: Optional[List['AgentRail']] = None,
    pr_template: Optional[str] = None,
) -> 'DeepAgent'
```

Create a communicate-only PR draft agent. Uses read-only rails, loads the `communicate` skill, and generates GitCode PR drafts based on task facts. Supports custom PR templates.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **workspace_override**(`Optional[str]`): Override the workspace path in configuration.
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.
* **pr_template**(`Optional[str]`): Custom PR template content; uses built-in fallback template when `None` or empty.

**Returns**: A fully configured `DeepAgent` instance.

---

## openjiuwen.auto_harness.agents.factory.create_learnings_agent

```python
def create_learnings_agent(
    config: 'AutoHarnessConfig',
    *,
    session_results: str = '',
    existing_memories: str = '',
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

Create the session experience reflection agent. Uses read-only rails, loads the `communicate` skill, reflects on session results, and extracts reusable experience records.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **session_results**(`str`): Session results summary text, injected into the prompt template.
* **existing_memories**(`str`): Existing experience memory text, injected into the prompt template to avoid duplication.
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.

**Returns**: A fully configured `DeepAgent` instance.

---

## openjiuwen.auto_harness.agents.factory.create_merge_ext_agent

```python
def create_merge_ext_agent(
    config: 'AutoHarnessConfig',
    *,
    workspace_override: str,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

Create the merge extension fix agent. The workspace is locked to the `merged_extensions/` directory to prevent out-of-scope edits. No skills are mounted; it relies solely on prompt instructions and basic filesystem/shell tools to fix static check errors in merged extension artifacts.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **workspace_override**(`str`): Merged workspace path (required).
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.

**Returns**: A fully configured `DeepAgent` instance.

---

## openjiuwen.auto_harness.agents.factory.create_activate_guide_agent

```python
def create_activate_guide_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

Create the activate test guidance agent. A pure text generation agent with no tools or file-reading rails mounted; directly generates extension test guidance documentation in a single iteration. Uses `config.plan_model` if available, otherwise falls back to `config.model`.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **extra_rails**(`Optional[List[AgentRail]]`): Additional rails to append.

**Returns**: A fully configured `DeepAgent` instance.
