# openjiuwen.core.runner.resources_manager.resource_manager

## class ResourceMgr

### add_agent

```python
add_agent(self,
              card: AgentCard,
              agent: AgentProvider | RemoteAgent,
              *,
              tag: Optional[Tag | list[Tag]] = None
              ) -> Result[AgentCard, Exception]
```

添加单个代理到资源管理器。

**参数：**

* **card**(AgentCard)：代理的元数据卡片，包含配置和标识信息。
* **agent**(AgentProvider | RemoteAgent)：可调用提供者，用于创建或返回代理实例。
* **tag**(Optional[Tag | list[Tag]]，可选)：用于分类和过滤代理的标签。

**返回：**

**Result**[[AgentCard](../single_agent/single_agent.md), Exception]，包含添加的代理卡片或异常的Result对象。

**样例：**

```python
>>> from openjiuwen.core.runner import Runner
>>> from openjiuwen.core.single_agent import AgentCard
>>> from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
>>> 
>>>
>>> card = AgentCard(id="data_analyzer", name="数据分析师", description="专业数据分析代理")
>>> agent_provider = lambda _: BaseAgent()
>>> 
>>> result = Runner.resource_mgr.add_agent(card, agent_provider, tag=["analysis", "expert"])
```

### add_agents

```python
add_agents(self,
               agents: list[Tuple[AgentCard, AgentProvider]],
               *,
               tag: Optional[Tag | list[Tag]] = None
               ) -> Result[AgentCard, Exception] | list[Result[AgentCard, Exception]]
```

批量添加多个代理。

**参数：**

* **agents**(list[Tuple[AgentCard, AgentProvider]])：元组列表，每个元组包含(AgentCard, AgentProvider)。
* **tag**(Optional[Tag | list[Tag]]，可选)：要应用于所有被添加代理的可选标签。除了单个AgentCard上的任何标签外，还会应用这些标签。

**返回：**

**Result**[[AgentCard](../single_agent/single_agent.md), Exception]|list[Result[[AgentCard](../single_agent/single_agent.md), Exception]]，包含添加的代理卡片或异常的Result对象或列表。

**样例：**

```python
>>> from openjiuwen.core.runner import Runner
>>> from openjiuwen.core.single_agent.schema.agent_card import AgentCard
>>> 
>>>
>>> agents = [
...     (AgentCard(id="agent1", name="代理1"), lambda _: BaseAgent()),
...     (AgentCard(id="agent2", name="代理2"), lambda _: BaseAgent())
... ]
>>> results = Runner.resource_mgr.add_agents(agents, tag=["batch_added"])
```

### remove_agent

```python
remove_agent(self,
                 agent_id: str | list[str] = None,
                 *,
                 tag: Optional[Tag | list[Tag]] = GLOBAL,
                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                 skip_if_tag_not_exists: bool = False,
                 ) -> Result[Optional[AgentCard], Exception] | list[Result[Optional[AgentCard], Exception]]
```

通过ID或标签删除代理。

**参数：**

* **agent_id**(str | list[str]，可选)：要删除的代理的单个ID或ID列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；删除所有匹配标签的代理。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，跳过不存在的资源。

**返回：**

**Result**[Optional[[AgentCard](../single_agent/single_agent.md)], Exception]|list[Result[Optional[[AgentCard](../single_agent/single_agent.md)], Exception]]，包含删除的代理卡片或异常的Result对象或列表。

**样例：**

```python
>>> Runner.resource_mgr.remove_agent(agent_id=["agent1", "agent2"])
>>> Runner.resource_mgr.remove_agent(tag=["obsolete"], tag_match_strategy=TagMatchStrategy.ANY)
```

### get_agent

```python
async get_agent(self,
                    agent_id: str | list[str] = None,
                    *,
                    tag: Optional[Tag | list[Tag]] = None,
                    tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                    session: Optional[Session] = None
                    ) -> Optional[BaseAgent] | list[Optional[BaseAgent]]
```

通过ID或标签获取代理实例。

**参数：**

* **agent_id**(str | list[str]，可选)：要检索的代理的单个ID或ID列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；返回所有匹配标签的代理。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **session**(Optional[Session]，可选)：代理的可选会话上下文。

**返回：**

**[BaseAgent](../single_agent/single_agent.md)|list[[BaseAgent](../single_agent/single_agent.md)]**，如果找到则返回代理实例，否则为None。

**样例：**

```python
>>> agent = await Runner.resource_mgr.get_agent(agent_id="data_analyzer")
>>> agents = await Runner.resource_mgr.get_agent(tag=["expert"], session=Session())
```

### add_workflow

```python
add_workflow(self,
                 card: WorkflowCard,
                 workflow: WorkflowProvider,
                 *,
                 tag: Optional[Tag | list[Tag]] = None
                 ) -> Result[WorkflowCard, Exception]
```

添加单个工作流到资源管理器。

**参数：**

* **card**(WorkflowCard)：工作流的元数据卡片，包含配置和标识信息。
* **workflow**(WorkflowProvider)：可调用提供者，用于创建或返回工作流实例。
* **tag**(Optional[Tag | list[Tag]]，可选)：用于分类和过滤工作流的标签。

**返回：**

**Result**[[WorkflowCard](../workflow/workflow.md), Exception]，包含添加的工作流卡片或异常的Result对象。

**样例：**

```python
>>> from openjiuwen.core.workflow import WorkflowCard, Workflow
>>> 
>>> card = WorkflowCard(id="data_pipeline", name="数据处理流水线")
>>> workflow_provider = lambda c: Workflow(card=c)
>>> result = Runner.resource_mgr.add_workflow(card, workflow_provider, tag=["pipeline", "data"])
```


### add_workflows

```python
add_workflows(self,
                  workflows: list[Tuple[WorkflowCard, WorkflowProvider]],
                  *,
                  tag: Optional[Tag | list[Tag]] = None
                  ) -> Result[WorkflowCard, Exception] | list[Result[WorkflowCard, Exception]]
```

批量添加多个工作流。

**参数：**

* **workflows**(list[Tuple[WorkflowCard, WorkflowProvider]])：元组列表，每个元组包含(WorkflowCard, WorkflowProvider)。
* **tag**(Optional[Tag | list[Tag]]，可选)：要应用于所有被添加工作流的可选标签。

**返回：**

**Result**[[WorkflowCard](../workflow/workflow.md), Exception]|list[Result[[WorkflowCard](../workflow/workflow.md), Exception]]，包含添加的工作流卡片或异常的Result对象或列表。

**样例：**

```python
>>> workflows = [
...     (WorkflowCard(id="wf1", name="工作流1"), lambda c: Workflow(card=c)),
...     (WorkflowCard(id="wf2", name="工作流2"), lambda c: Workflow(card=c))
... ]
>>> results = Runner.resource_mgr.add_workflows(workflows, tag=["batch"])
```

### remove_workflow

```python
remove_workflow(self,
                    workflow_id: str | list[str] = None,
                    *,
                    tag: Optional[Tag | list[Tag]] = None,
                    tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                    skip_if_tag_not_exists: bool = False,
                    ) -> Result[Optional[WorkflowCard], Exception] | list[Result[Optional[WorkflowCard], Exception]]
```

通过ID或标签删除工作流。

**参数：**

* **workflow_id**(str | list[str]，可选)：要删除的工作流的单个ID或ID列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；删除所有匹配标签的工作流。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，跳过不存在的工作流。

**返回：**

**Result**[Optional[[WorkflowCard](../workflow/workflow.md)], Exception]|list[Result[Optional[[WorkflowCard](../workflow/workflow.md)], Exception]]，包含删除的工作流卡片或异常的Result对象或列表。

**样例：**

```python
>>> Runner.resource_mgr.remove_workflow(workflow_id="data_pipeline")
>>> Runner.resource_mgr.remove_workflow(tag=["deprecated"])
```

### get_workflow

```python
async get_workflow(self,
                       workflow_id: str | list[str] = None,
                       *,
                       tag: Optional[Tag | list[Tag]] = None,
                       tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                       session: Optional[Session] = None
                       ) -> Optional[Workflow] | list[Optional[Workflow]]
```

通过ID或标签获取工作流实例。

**参数：**

* **workflow_id**(str | list[str]，可选)：要检索的工作流的单个ID或ID列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；返回所有匹配标签的工作流。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **session**(Optional[Session]，可选)：工作流的可选会话上下文。

**返回：**

**Workflow|list[Workflow]**，如果找到则返回工作流实例，否则为None。

**样例：**

```python
>>> workflow = await Runner.resource_mgr.get_workflow(workflow_id="data_pipeline")
>>> workflows = await Runner.resource_mgr.get_workflow(tag=["data_processing"])
```

### add_tool

```python
add_tool(self,
             tool: Tool | list[Tool],
             *,
             tag: Optional[Tag | list[Tag]] = None
             ) -> Result[ToolCard, Exception] | list[Result[ToolCard, Exception]]
```

添加工具到资源管理器。

**参数：**

* **tool**(Tool | list[Tool])：要添加的单个Tool实例或Tool实例列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：用于分类和过滤工具的可选标签。

**返回：**

**Result**[[ToolCard](../foundation/tool/tool.md), Exception]|list[Result[[ToolCard](../foundation/tool/tool.md), Exception]]，包含添加的工具卡片或异常的Result对象或列表。

**样例：**

```python
>>> from openjiuwen.core.foundation.tool import Tool, ToolCard
>>> 
>>> tool_card = ToolCard(id="calculator", name="计算器", description="数学计算工具")
>>> tool = Tool(card=tool_card, func=lambda x: x*2)
>>> result = Runner.resource_mgr.add_tool(tool, tag=["utility", "math"])
```

### get_tool

```python
get_tool(self,
             tool_id: str | list[str] = None,
             *,
             tag: Optional[Tag | list[Tag]] = None,
             tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
             session: Optional[Session] = None
             ) -> Optional[Tool] | list[Optional[Tool]]
```

通过ID或标签获取工具。

**参数：**

* **tool_id**(str | list[str]，可选)：要检索的工具的单个ID或ID列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；返回所有匹配标签的工具。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **session**(Optional[Session]，可选)：工具的可选会话上下文。

**返回：**

**Tool|list[Tool]**，如果找到则返回工具实例，否则为None。

**样例：**

```python
>>> tool = Runner.resource_mgr.get_tool(tool_id="calculator")
>>> tools = Runner.resource_mgr.get_tool(tag=["utility"])
```

### remove_tool

```python
remove_tool(self,
                tool_id: str | list[str] = None,
                *,
                tag: Optional[Tag | list[Tag]] = None,
                tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                skip_if_tag_not_exists: bool = False,
                ) -> Result[Optional[ToolCard], Exception] | list[Result[Optional[ToolCard], Exception]]
```

通过ID或标签删除工具。

**参数：**

* **tool_id**(str | list[str]，可选)：要删除的工具的单个ID或ID列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；删除所有匹配标签的工具。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，跳过不存在的标签。

**返回：**

**Result[Optional[[ToolCard](../foundation/tool/tool.md)], Exception]|list[Result[Optional[[ToolCard](../foundation/tool/tool.md)], Exception]]**，包含删除的工具卡片或异常的Result对象或列表。

**样例：**

```python
>>> Runner.resource_mgr.remove_tool(tool_id="calculator")
>>> Runner.resource_mgr.remove_tool(tag=["obsolete"])
```

### add_model

```python
add_model(self,
              model_id: str,
              model: ModelProvider,
              *,
              tag: Optional[Tag | list[Tag]] = None
              ) -> Result[str, Exception]
```

添加模型到资源管理器。

**参数：**

* **model_id**(str)：模型的唯一标识符。
* **model**(ModelProvider)：可调用提供者，用于创建或返回模型实例。
* **tag**(Optional[Tag | list[Tag]]，可选)：用于分类和过滤模型的标签。

**返回：**

**Result**[str, Exception]，包含模型ID或异常的Result对象。

**样例：**

```python
>>> from openjiuwen.core.runner.resources_manager.base import ModelProvider
>>> 
>>> model_provider: ModelProvider = lambda *args: SomeModel()
>>> result = Runner.resource_mgr.add_model(model_id="gpt-4", model=model_provider, tag=["llm", "gpt"])
```

### add_models

```python
add_models(self,
               models: list[Tuple[str, ModelProvider]],
               *,
               tag: Optional[Tag | list[Tag]] = None
               ) -> Result[str, Exception] | list[Result[str, Exception]]
```

批量添加多个模型。

**参数：**

* **models**(list[Tuple[str, ModelProvider]])：元组列表，每个元组包含(model_id, ModelProvider)。
* **tag**(Optional[Tag | list[Tag]]，可选)：要应用于所有被添加模型的可选标签。

**返回：**

**Result**[str, Exception]|list[Result[str, Exception]]，包含模型ID或异常的Result对象或列表。

**样例：**

```python
>>> models = [
...     ("gpt-3.5", lambda *args: GPTModel()),
...     ("claude-2", lambda *args: ClaudeModel())
... ]
>>> results = Runner.resource_mgr.add_models(models, tag=["language_model"])
```

### remove_model

```python
remove_model(self,
                 *,
                 model_id: str | list[str] = None,
                 tag: Optional[Tag | list[Tag]] = None,
                 tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                 skip_if_tag_not_exists: bool = False,
                 ) -> Result[str, Exception] | list[Result[str, Exception]]
```

通过ID或标签删除模型。

**参数：**

* **model_id**(str | list[str]，可选)：要删除的模型的单个ID或ID列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；删除所有匹配标签的模型。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，跳过不存在的模型。

**返回：**

**Result**[str, Exception]|list[Result[str, Exception]]，包含模型ID或异常的Result对象或列表。

**样例：**

```python
>>> Runner.resource_mgr.remove_model(model_id="gpt-3.5")
>>> Runner.resource_mgr.remove_model(tag=["deprecated_models"])
```

### get_model

```python
async get_model(self,
                    model_id: str | list[str] = None,
                    *,
                    tag: Optional[Tag | list[Tag]] = None,
                    tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                    session: Optional[Session] = None) \
        -> Optional[BaseModel] | list[Optional[BaseModel]]
```

通过ID或标签获取模型实例。

**参数：**

* **model_id**(str | list[str]，可选)：要检索的模型的单个ID或ID列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；返回所有匹配标签的模型。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **session**(Optional[Session]，可选)：模型的可选会话上下文。

**返回：**

**Model|list[Model]**，如果找到则返回模型实例，否则为None。

**样例：**

```python
>>> model = await Runner.resource_mgr.get_model(model_id="gpt-4")
>>> models = await Runner.resource_mgr.get_model(tag=["vision_model"])
```

### add_prompt

```python
add_prompt(self,
               prompt_id: str,
               template: PromptTemplate,
               *,
               tag: Optional[Tag | list[Tag]] = None,
               ) -> Result[str, Exception]
```

添加提示模板到资源管理器。

**参数：**

* **prompt_id**(str)：提示模板的唯一标识符。
* **template**(PromptTemplate)：包含提示内容和配置的PromptTemplate实例。
* **tag**(Optional[Tag | list[Tag]]，可选)：用于分类和过滤提示的标签。

**返回：**

**Result**[str, Exception]，包含提示ID或异常的Result对象。

**样例：**

```python
>>> from openjiuwen.core.foundation.prompt import PromptTemplate
>>> 
>>> template = PromptTemplate(content="请分析以下数据: {data}")
>>> result = Runner.resource_mgr.add_prompt(prompt_id="data_analysis_prompt", template=template, tag=["analysis", "prompt"])
```

### add_prompts

```python
add_prompts(self,
                prompts: list[Tuple[str, PromptTemplate]],
                *,
                tag: Optional[Tag | list[Tag]] = None
                ) -> Result[str, Exception] | list[Result[str, Exception]]
```

批量添加多个提示模板。

**参数：**

* **prompts**(list[Tuple[str, PromptTemplate]])：元组列表，每个元组包含(prompt_id, PromptTemplate)。
* **tag**(Optional[Tag | list[Tag]]，可选)：要应用于所有被添加提示的可选标签。

**返回：**

**Result**[str, Exception]|list[Result[str, Exception]]，包含提示ID或异常的Result对象或列表。

**样例：**

```python
>>> prompts = [
...     ("prompt1", PromptTemplate(content="提示1: {input}")),
...     ("prompt2", PromptTemplate(content="提示2: {input}"))
... ]
>>> results = Runner.resource_mgr.add_prompts(prompts, tag=["batch_prompts"])
```

### remove_prompt

```python
remove_prompt(self,
                  prompt_id: str | list[str] = None,
                  *,
                  tag: Optional[Tag | list[Tag]] = None,
                  tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                  skip_if_tag_not_exists: bool = False,
                  ) -> Result[str, Exception] | list[Result[str, Exception]]
```

通过ID或标签删除提示模板。

**参数：**

* **prompt_id**(str | list[str]，可选)：要删除的提示的单个ID或ID列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；删除所有匹配标签的提示。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，跳过不存在的提示。

**返回：**

**Result**[str, Exception]|list[Result[str, Exception]]，包含提示ID或异常的Result对象或列表。

**样例：**

```python
>>> Runner.resource_mgr.remove_prompt(prompt_id="data_analysis_prompt")
>>> Runner.resource_mgr.remove_prompt(tag=["old_prompts"])
```

### get_prompt

```python
get_prompt(self,
               prompt_id: str | list[str] = None,
               *,
               tag: Optional[Tag | list[Tag]] = None,
               tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
               ) -> Optional[PromptTemplate] | list[Optional[PromptTemplate]]
```

通过ID或标签获取提示模板。

**参数：**

* **prompt_id**(str | list[str]，可选)：要检索的提示的单个ID或ID列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；返回所有匹配标签的提示。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。

**返回：**

**[PromptTemplate](../foundation/prompt/template.md)|list[[PromptTemplate](../foundation/prompt/template.md)]**，如果找到则返回提示模板实例，否则为None。

**样例：**

```python
>>> prompt = Runner.resource_mgr.get_prompt(prompt_id="data_analysis_prompt")
>>> prompts = Runner.resource_mgr.get_prompt(tag=["system_prompts"])
```

### add_sys_operation

```python
add_sys_operation(self,
                      card: SysOperationCard | List[SysOperationCard],
                      *,
                      tag: Optional[Tag | List[Tag]] = None
                      ) -> Result[SysOperationCard, Exception] | List[Result[SysOperationCard, Exception]]
```

通过SysOperationCard添加系统操作（可选标签）。支持批量添加多个系统操作。

**参数：**

* **card**(SysOperationCard | List[SysOperationCard])：单个SysOperationCard或SysOperationCard列表（必需）。
* **tag**(Optional[Tag | List[Tag]]，可选)：用于分类的可选单个标签/标签列表。

**返回：**

**Result[[SysOperationCard](../sys_operation/sys_operation.md), Exception] | List[Result[[SysOperationCard](../sys_operation/sys_operation.md), Exception]]**，单个结果对象或结果对象列表，包含成功添加的卡片或错误。

**样例：**

```python
>>> from openjiuwen.core.sys_operation import SysOperationCard
>>> 
>>> # 添加单个系统操作
>>> card = SysOperationCard(id="cleanup_op", name="清理操作")
>>> result = Runner.resource_mgr.add_sys_operation(card, tag=["maintenance"])
>>>
>>> # 批量添加多个系统操作
>>> cards = [
...     SysOperationCard(id="op1", name="操作1"),
...     SysOperationCard(id="op2", name="操作2")
... ]
>>> results = Runner.resource_mgr.add_sys_operation(cards, tag=["batch_ops"])
```

### remove_sys_operation

```python
remove_sys_operation(self,
                         sys_operation_id: str | List[str],
                         *,
                         tag: Optional[Tag | List[Tag]] = GLOBAL,
                         tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                         skip_if_tag_not_exists: bool = False,
                         ) -> Result[Optional[SysOperationCard], Exception] | List[Result[Optional[SysOperationCard], Exception]]
```

通过ID/标签删除系统操作（支持批量）。删除系统操作时会同时删除其关联的工具。

**参数：**

* **sys_operation_id**(str | List[str])：要删除的单个操作ID或ID列表（必需）。
* **tag**(Optional[Tag | List[Tag]]，可选)：单个标签或标签列表过滤器（默认：GLOBAL）。
* **tag_match_strategy**(TagMatchStrategy，可选)：标签匹配策略（默认：ALL）。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，跳过不存在的标签（默认：False）。

**返回：**

**Result**[Optional[[SysOperationCard](../sys_operation/sys_operation.md)], Exception] | List[Result[Optional[[SysOperationCard](../sys_operation/sys_operation.md)], Exception]]，单个结果对象或结果对象列表，包含删除的卡片或错误。

**样例：**

```python
>>> # 删除单个系统操作
>>> Runner.resource_mgr.remove_sys_operation("cleanup_op")
>>>
>>> # 批量删除多个系统操作
>>> Runner.resource_mgr.remove_sys_operation(["op1", "op2"])
>>>
>>> # 按标签删除
>>> Runner.resource_mgr.remove_sys_operation(tag=["temporary"])
```

### get_sys_operation

```python
get_sys_operation(self,
                      sys_operation_id: str | List[str] = None,
                      *,
                      tag: Optional[Tag | List[Tag]] = None,
                      tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                      session: Optional[Session] = None
                      ) -> Optional[SysOperation] | List[Optional[SysOperation]]
```

通过ID/标签获取系统操作实例。

**参数：**

* **sys_operation_id**(str | List[str]，可选)：单个操作ID或ID列表。
* **tag**(Optional[Tag | List[Tag]]，可选)：单个标签或标签列表过滤器。
* **tag_match_strategy**(TagMatchStrategy，可选)：标签匹配策略（默认：ALL）。
* **session**(Optional[Session]，可选)：可选上下文会话。

**返回：**

**SysOperation | List[[SysOperation](../sys_operation/sys_operation.md)]**，单个系统操作实例或实例列表，如果未找到则返回None。

**样例：**

```python
>>> # 获取单个系统操作
>>> operation = Runner.resource_mgr.get_sys_operation("cleanup_op")
>>>
>>> # 批量获取多个系统操作
>>> operations = Runner.resource_mgr.get_sys_operation(["op1", "op2"])
>>>
>>> # 按标签获取
>>> operations = Runner.resource_mgr.get_sys_operation(tag=["scheduled"])
```

### get_tool_infos

```python
async get_tool_infos(self,
                         tool_id: str | list[str] = None,
                         *,
                         tool_type: str | list[str] = None,
                         tag: Optional[Tag | list[Tag]] = None,
                         tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                         ignore_exception: bool = False,
                         ) -> Optional[ToolInfo] | list[Optional[ToolInfo]]
```

通过ID、类型或标签获取工具信息/元数据。

**参数：**

* **tool_id**(str | list[str]，可选)：要获取信息的工具的单个ID或ID列表。
* **tool_type**(str | list[str]，可选)：用于过滤工具的单个类型或类型列表。常见类型：["function", "mcp", "workflow", "agent", "group"]。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；返回所有匹配标签的工具的信息。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **ignore_exception**(bool，可选)：如果为True，忽略异常并为失败项返回None。

**返回：**

**[ToolInfo](../foundation/tool/tool.md)|list[[ToolInfo](../foundation/tool/tool.md)]**，如果找到则返回工具信息实例，否则为None。

**样例：**

```python
>>> tool_info = await Runner.resource_mgr.get_tool_infos(tool_id="calculator")
>>> tool_infos = await Runner.resource_mgr.get_tool_infos(tool_type=["function", "mcp"], tag=["utility"])
```

### add_mcp_server

```python
async add_mcp_server(self,
                         server_config: McpServerConfig | list[McpServerConfig],
                         *,
                         tag: Optional[Tag | list[Tag]] = None,
                         expiry_time: Optional[float] = None
                         ) -> Result[str, Exception] | list[Result[str, Exception]]
```

添加MCP（模型上下文协议）服务器配置。

**参数：**

* **server_config**(McpServerConfig | list[McpServerConfig])：单个或McpServerConfig实例列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：用于分类服务器的可选标签。
* **expiry_time**(Optional[float]，可选)：服务器配置到期的可选Unix时间戳。如果为None，则配置永不过期。

**返回：**

**Result**[str, Exception]|list[Result[str, Exception]]，包含服务器名称或异常的Result对象或列表。

**样例：**

```python
>>> from openjiuwen.core.foundation.tool import McpServerConfig
>>> 
>>> config = McpServerConfig(server_id="mcp1", server_name="MCP服务器1", ...)
>>> result = await Runner.resource_mgr.add_mcp_server(config, tag=["mcp", "external"], expiry_time=1735689600)
```

### refresh_mcp_server

```python
async refresh_mcp_server(self,
                             server_id: Optional[str | list[str]] = None,
                             *,
                             server_name: Optional[str | list[str]] = None,
                             tag: Optional[Tag | list[Tag]] = None,
                             tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                             ignore_exception: bool = False,
                             skip_if_tag_not_exists: bool = False,
                             ) -> Result[str, Exception] | list[Result[str, Exception]]
```

通过名称刷新MCP服务器工具卡片。

**参数：**

* **server_id**(Optional[str | list[str]]，可选)：要刷新的MCP服务器ID的单个或列表。
* **server_name**(Optional[str | list[str]]，可选)：要刷新的MCP服务器名称的单个或列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：用于过滤要刷新的服务器的可选标签。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **ignore_exception**(bool，可选)：如果为True，如果一个失败则继续刷新其他服务器。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，跳过不存在的服务器。

**返回：**

**Result**[str, Exception]|list[Result[str, Exception]]，包含服务器名称或异常的Result对象或列表。

**样例：**

```python
>>> results = await Runner.resource_mgr.refresh_mcp_server(server_name=["MCP服务器1"], tag=["external"])
```

### remove_mcp_server

```python
async remove_mcp_server(self,
                            server_id: Optional[str | list[str]] = None,
                            *,
                            server_name: Optional[str | list[str]] = None,
                            tag: Optional[Tag | list[Tag]] = None,
                            tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                            skip_if_tag_not_exists: bool = False,
                            ignore_exception: bool = False,
                            ) -> Result[str, Exception] | list[Result[str, Exception]]
```

通过名称或标签删除MCP服务器。

**参数：**

* **server_id**(Optional[str | list[str]]，可选)：要删除的MCP服务器ID的单个或列表。
* **server_name**(Optional[str | list[str]]，可选)：要删除的MCP服务器名称的单个或列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：单个标签或标签列表；删除所有匹配标签的服务器。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，跳过不存在的服务器。
* **ignore_exception**(bool，可选)：如果为True，如果一个失败则继续删除其他服务器。

**返回：**

**Result**[str, Exception]|list[Result[str, Exception]]，包含服务器名称或异常的Result对象或列表。

**样例：**

```python
>>> result = await Runner.resource_mgr.remove_mcp_server(server_name="MCP服务器1")
>>> results = await Runner.resource_mgr.remove_mcp_server(tag=["deprecated_mcp"])
```

### get_mcp_tool

```python
async get_mcp_tool(self,
                       name: str | list[str] = None,
                       server_id: str | list[str] = None,
                       *,
                       server_name: str | list[str] = None,
                       tag: Optional[Tag | list[Tag]] = None,
                       tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                       skip_if_tag_not_exists: bool = False,
                       ignore_exception: bool = False,
                       session: Optional[Session] = None
                       ) -> Optional[Tool] | list[Optional[Tool]]
```

通过名称和服务器获取MCP工具。

**参数：**

* **name**(str | list[str]，可选)：要检索的MCP工具名称的单个或列表。
* **server_id**(str | list[str]，可选)：包含工具的MCP服务器ID的单个或列表。
* **server_name**(str | list[str]，可选)：包含工具的MCP服务器名称的单个或列表。
* **tag**([Tag | list[Tag]]，可选)：用于过滤服务器/工具的可选标签。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，跳过不存在的服务器。
* **ignore_exception**(bool，可选)：如果为True，忽略刷新MCP服务器所需的异常（如果需要）。
* **session**(Optional[Session]，可选)：工具的可选会话上下文。

**返回：**

**Tool|list[Tool]**，如果找到则返回MCP工具实例，否则为None。

**样例：**

```python
>>> tool = await Runner.resource_mgr.get_mcp_tool(name="weather_api", server_name="MCP服务器1")
>>> tools = await Runner.resource_mgr.get_mcp_tool(server_id="mcp1", tag=["external_tools"])
```

### get_mcp_tool_infos

```python
async get_mcp_tool_infos(self,
                             name: str | list[str] = None,
                             server_id: str | list[str] = None,
                             *,
                             server_name: str | list[str] = None,
                             tag: Optional[Tag | list[Tag]] = None,
                             tag_match_strategy: TagMatchStrategy = TagMatchStrategy.ALL,
                             skip_if_tag_not_exists: bool = False,
                             ignore_exception: bool = False,
                             ) -> Optional[ToolInfo] | list[Optional[ToolInfo]]
```

通过名称和服务器获取MCP工具信息/元数据。

**参数：**

* **name**(str | list[str]，可选)：要获取信息的MCP工具名称的单个或列表。如果为None，则返回指定服务器中所有工具的信息。
* **server_name**(str | list[str]，可选)：包含工具的MCP服务器名称的单个或列表。如果名称为None，则必须提供。
* **server_id**(str | list[str]，可选)：包含工具的MCP服务器ID的单个或列表。
* **tag**(Optional[Tag | list[Tag]]，可选)：用于过滤服务器/工具的可选标签。
* **tag_match_strategy**(TagMatchStrategy，可选)：使用tag参数时的标签匹配策略。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，跳过不存在的服务器。
* **ignore_exception**(bool，可选)：如果为True，忽略刷新MCP服务器所需的异常。

**返回：**

**[ToolInfo](../foundation/tool/tool.md)|list[[ToolInfo](../foundation/tool/tool.md)]**，如果找到则返回MCP工具信息实例，否则为None。

**样例：**

```python
>>> tool_info = await Runner.resource_mgr.get_mcp_tool_infos(name="weather_api", server_name="MCP服务器1")
>>> tool_infos = await Runner.resource_mgr.get_mcp_tool_infos(server_id="mcp1")
```

### get_resource_by_tag

```python
get_resource_by_tag(self, tag: Tag) -> Optional[list[BaseCard]]
```

检索与特定标签关联的所有资源。

**参数：**

* **tag**(Tag)：要搜索的标签。

**返回：**

**Optional[list[BaseCard]]**，表示具有指定标签的资源的BaseCard实例列表，如果未找到资源则为None。

**样例：**

```python
>>> cards = Runner.resource_mgr.get_resource_by_tag("analysis")
>>> for card in cards:
...     print(f"资源ID: {card.id}, 类型: {type(card).__name__}")
```


### list_tags

```python
def list_tags(self) -> list[Tag]
```

列出当前所有资源中使用的所有标签。

**返回：**

**list[Tag]**，唯一标签字符串列表。

**样例：**

```python
>>> tags = Runner.resource_mgr.list_tags()
>>> print(f"可用标签: {tags}")
```

### has_tag

```python
has_tag(self, tag: str) -> bool
```

检查resource_mgr中是否存在指定的标签。

**参数：**

* **tag**(str)：要检查是否存在的标签。

**返回：**

**bool**，如果标签存在则为True，否则为False。

**样例：**

```python
>>> if Runner.resource_mgr.has_tag("analysis"):
...     print("存在'analysis'标签")
```

### remove_tag

```python
async remove_tag(self,
                     tag: Tag | list[Tag],
                     *,
                     skip_if_tag_not_exists: bool = False,
                     ) -> Result[Tag, Exception] | list[Result[Tag, Exception]]
```

从所有资源中删除标签。

**参数：**

* **tag**(Tag | list[Tag])：要从所有资源中删除的单个标签或标签列表。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，忽略不存在的标签。

**返回：**

**Result**[Tag, Exception]|list[Result[Tag, Exception]]，包含标签或异常的Result对象或列表。

**样例：**

```python
>>> result = await Runner.resource_mgr.remove_tag("obsolete")
>>> results = await Runner.resource_mgr.remove_tag(["temp", "test"], skip_if_tag_not_exists=True)
```

### update_resource_tag

```python
update_resource_tag(self,
                        resource_id: str,
                        tag: Tag | list[Tag]
                        ) -> Result[list[Tag], Exception]
```

将资源上的所有标签替换为新标签。

**参数：**

* **resource_id**(str)：资源标识符。
* **tag**(Tag | list[Tag])：要在资源上设置的新标签。

**返回：**

**Result**[list[Tag], Exception]，包含新标签列表或异常的Result对象。

**样例：**

```python
>>> result = Runner.resource_mgr.update_resource_tag("data_analyzer", ["expert", "premium"])
>>> if result.is_ok():
...     print(f"更新后的标签: {result.msg()}")
```

### add_resource_tag

```python
add_resource_tag(self,
                     resource_id: str,
                     tag: Tag | list[Tag]
                     ) -> Result[list[Tag], Exception]
```

向资源添加标签。

**参数：**

* **resource_id**(str)：资源标识符。
* **tag**(Tag | list[Tag])：要添加到资源的标签。

**返回：**

**Result**[list[Tag], Exception]，包含资源现在关联的所有标签的Result对象。

**样例：**

```python
>>> result = Runner.resource_mgr.add_resource_tag("calculator", ["math", "utility"])
>>> if result.is_ok():
...     print(f"当前标签: {result.msg()}")
```

### remove_resource_tag

```python
remove_resource_tag(self,
                        resource_id: str,
                        tag: Tag | list[Tag],
                        *,
                        skip_if_tag_not_exists: bool = False
                        ) -> Result[list[Tag], Exception]
```

从资源中删除特定标签。

**参数：**

* **resource_id**(str)：资源标识符。
* **tag**(Tag | list[Tag])：要从资源中删除的标签。
* **skip_if_tag_not_exists**(bool，可选)：如果为True，忽略不存在的标签。

**返回：**

**Result**[list[Tag], Exception]，包含资源上剩余标签的Result对象。

**样例：**

```python
>>> result = Runner.resource_mgr.remove_resource_tag("data_analyzer", "test", skip_if_tag_not_exists=True)
```

### get_resource_tag

```python
get_resource_tag(self, resource_id: str) -> Optional[list[Tag]]
```

获取与资源关联的所有标签。

**参数：**

* **resource_id**(str)：资源标识符。

**返回：**

**Optional[list[Tag]]**，与资源关联的标签列表，如果未找到资源则为None。

**样例：**

```python
>>> tags = Runner.resource_mgr.get_resource_tag("data_analyzer")
>>> print(f"资源标签: {tags}")
```

### resource_has_tag

```python
>>> resource_has_tag(self, resource_id: str, tag: Tag) -> bool
```

检查特定资源是否与给定标签关联。

**参数：**

* **resource_id**(str)：要检查的资源的唯一标识符。
* **tag**(Tag)：要验证与资源关联的标签。

**返回：**

**bool**，如果资源具有指定的标签则为True，否则为False。

**样例：**

```python
>>> if Runner.resource_mgr.resource_has_tag("data_analyzer", "expert"):
...     print("资源具有'expert'标签")
```


### release

```python
async release(self)
```

释放所有资源并进行清理。

当不再需要ResourceMgr时应调用此方法。

**样例：**

```python
>>> await Runner.resource_mgr.release()
```