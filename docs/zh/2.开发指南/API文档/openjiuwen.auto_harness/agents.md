# openjiuwen.auto_harness.agents

Agent 工厂函数模块，提供 Auto Harness 各阶段所需 Agent 的统一创建入口。每个工厂函数封装了对应阶段的 prompt、rails、工具集和子 Agent 配置，返回一个可直接运行的 `DeepAgent` 实例。

子模块：
- `factory`：Agent 工厂函数集合

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

创建主任务实现 Agent。该 Agent 是 Auto Harness 流水线的核心编码 Agent，具备文件编辑、Shell 执行、技能加载等完整能力，并内置上下文注入、经验检索、安全防护等 rails。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **workspace_override**(`Optional[str]`)：覆盖配置中的工作区路径。
* **edit_safety_rail**(`Optional[AgentRail]`)：自定义编辑安全 rail，为 `None` 时使用默认的 `EditSafetyRail`。
* **enable_edit_safety**(`bool`)：是否启用编辑安全 rail，默认 `True`。
* **skill_names**(`Optional[List[str]]`)：启用的技能名称列表，默认为 `["implement", "verify", "communicate"]`。
* **enable_task_loop**(`bool`)：是否启用任务循环，默认 `True`。
* **enable_task_planning**(`bool`)：是否启用任务规划 rail，默认 `True`。
* **enable_progress_repeat**(`bool`)：是否在任务规划中启用进度重复检查，默认 `True`。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。
* **extra_tools**(`Optional[List[Tool | ToolCard]]`)：额外追加的工具列表。

**返回**：配置完成的 `DeepAgent` 实例。

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

创建专用提交阶段 Agent。基于主 Agent 构建，但禁用任务循环和任务规划，仅加载 `commit` 和 `communicate` 技能。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **workspace_override**(`Optional[str]`)：覆盖配置中的工作区路径。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。

**返回**：配置完成的 `DeepAgent` 实例。

---

## openjiuwen.auto_harness.agents.factory.create_assess_agent

```python
def create_assess_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

创建评估阶段 Agent。使用只读 rails（不含编辑安全 rail），加载 `assess` 技能，并配备 Web 搜索和网页抓取工具用于代码库调研。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。

**返回**：配置完成的 `DeepAgent` 实例。

---

## openjiuwen.auto_harness.agents.factory.create_plan_agent

```python
def create_plan_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

创建规划阶段 Agent。使用只读 rails，加载 `plan` 技能，并配备 Web 搜索和网页抓取工具用于制定优化任务列表。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。

**返回**：配置完成的 `DeepAgent` 实例。

---

## openjiuwen.auto_harness.agents.factory.create_eval_agent

```python
def create_eval_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

创建验证修复循环中使用的评审 Agent。使用只读 rails，加载 `verify` 和 `verify_ext` 技能，用于评审代码变更质量。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。

**返回**：配置完成的 `DeepAgent` 实例。

---

## openjiuwen.auto_harness.agents.factory.create_select_pipeline_agent

```python
def create_select_pipeline_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

创建流水线选择 Agent。使用只读 rails，加载 `select_pipeline` 技能，并配备 Web 搜索工具，用于根据代码库状态选择最合适的优化流水线。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。

**返回**：配置完成的 `DeepAgent` 实例。

---

## openjiuwen.auto_harness.agents.factory.create_design_ext_agent

```python
def create_design_ext_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

创建 Phase 2 扩展设计 Agent。使用只读 rails，加载 `design_ext` 技能，并配备 Web 搜索工具，用于设计运行时扩展方案。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。

**返回**：配置完成的 `DeepAgent` 实例。

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

创建仅用于通信的 PR 草稿 Agent。使用只读 rails，加载 `communicate` 技能，根据任务事实生成 GitCode PR 草稿。支持自定义 PR 模板。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **workspace_override**(`Optional[str]`)：覆盖配置中的工作区路径。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。
* **pr_template**(`Optional[str]`)：自定义 PR 模板内容，为 `None` 或空时使用内置回退模板。

**返回**：配置完成的 `DeepAgent` 实例。

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

创建会话经验总结 Agent。使用只读 rails，加载 `communicate` 技能，反思会话结果并提取可复用的经验记录。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **session_results**(`str`)：会话结果摘要文本，注入到 prompt 模板中。
* **existing_memories**(`str`)：已有经验记忆文本，注入到 prompt 模板中以避免重复。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。

**返回**：配置完成的 `DeepAgent` 实例。

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

创建合并扩展修复 Agent。工作区锁定在 `merged_extensions/` 目录，防止编辑越界。不挂载任何技能，仅依赖 prompt 指令和基础文件系统 / Shell 工具修复合并扩展产物的静态校验错误。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **workspace_override**(`str`)：合并后的工作区路径（必填）。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。

**返回**：配置完成的 `DeepAgent` 实例。

---

## openjiuwen.auto_harness.agents.factory.create_activate_guide_agent

```python
def create_activate_guide_agent(
    config: 'AutoHarnessConfig',
    *,
    extra_rails: Optional[List['AgentRail']] = None,
) -> 'DeepAgent'
```

创建激活测试引导 Agent。纯文本生成 Agent，不挂载任何工具或文件读取 rails，在单次迭代中直接生成扩展测试引导文档。使用 `config.plan_model`（若存在）否则回退到 `config.model`。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **extra_rails**(`Optional[List[AgentRail]]`)：额外追加的 rails 列表。

**返回**：配置完成的 `DeepAgent` 实例。
