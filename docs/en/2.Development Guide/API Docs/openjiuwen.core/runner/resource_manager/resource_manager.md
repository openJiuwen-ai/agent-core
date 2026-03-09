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

Add a single agent to the resource manager.

**Parameters:**

* **card**(AgentCard): The agent's metadata card, containing configuration and identification information.
* **agent**(AgentProvider | RemoteAgent): Callable provider for creating or returning an agent instance.
* **tag**(Optional[Tag | list[Tag]], optional): Tags for categorizing and filtering agents.

**Returns:**

**Result[[AgentCard](../single_agent/single_agent.md), Exception]**, Result object containing the added agent card or an exception.

**Example:**

```python
>>> from openjiuwen.core.runner import Runner
>>> from openjiuwen.core.single_agent import AgentCard
>>> from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
>>> 
>>>
>>> card = AgentCard(id="data_analyzer", name="Data Analyzer", description="Professional data analysis agent")
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

Batch add multiple agents.

**Parameters:**

* **agents**(list[Tuple[AgentCard, AgentProvider]]): List of tuples, each containing (AgentCard, AgentProvider).
* **tag**(Optional[Tag | list[Tag]], optional): Optional tags to apply to all added agents. These tags are applied in addition to any tags on individual AgentCards.

**Returns:**

**Result[[AgentCard](../single_agent/single_agent.md), Exception]|list[Result[[AgentCard](../single_agent/single_agent.md), Exception]]**, Result object or list containing the added agent cards or exceptions.

**Example:**

```python
>>> from openjiuwen.core.runner import Runner
>>> from openjiuwen.core.single_agent.schema.agent_card import AgentCard
>>> 
>>>
>>> agents = [
...     (AgentCard(id="agent1", name="Agent 1"), lambda _: BaseAgent()),
...     (AgentCard(id="agent2", name="Agent 2"), lambda _: BaseAgent())
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

Remove an agent by ID or tag.

**Parameters:**

* **agent_id**(str | list[str], optional): Single ID or list of IDs of agents to remove.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; remove all agents matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **skip_if_tag_not_exists**(bool, optional): If True, skip non-existent resources.

**Returns:**

**Result**[Optional[[AgentCard](../single_agent/single_agent.md)], Exception]|list[Result[Optional[[AgentCard](../single_agent/single_agent.md)], Exception]], Result object or list containing the removed agent cards or exceptions.

**Example:**

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

Get an agent instance by ID or tag.

**Parameters:**

* **agent_id**(str | list[str], optional): Single ID or list of IDs of agents to retrieve.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; return all agents matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **session**(Optional[Session], optional): Optional session context for the agent.

**Returns:**

**[BaseAgent](../single_agent/single_agent.md)|list[[BaseAgent](../single_agent/single_agent.md)]**, agent instance if found, otherwise None.

**Example:**

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

Add a single workflow to the resource manager.

**Parameters:**

* **card**(WorkflowCard): The workflow's metadata card, containing configuration and identification information.
* **workflow**(WorkflowProvider): Callable provider for creating or returning a workflow instance.
* **tag**(Optional[Tag | list[Tag]], optional): Tags for categorizing and filtering workflows.

**Returns:**

**Result**[[WorkflowCard](../workflow/workflow.md), Exception], Result object containing the added workflow card or an exception.

**Example:**

```python
>>> from openjiuwen.core.workflow import WorkflowCard, Workflow
>>> 
>>> card = WorkflowCard(id="data_pipeline", name="Data Processing Pipeline")
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

Batch add multiple workflows.

**Parameters:**

* **workflows**(list[Tuple[WorkflowCard, WorkflowProvider]]): List of tuples, each containing (WorkflowCard, WorkflowProvider).
* **tag**(Optional[Tag | list[Tag]], optional): Optional tags to apply to all added workflows.

**Returns:**

**Result**[[WorkflowCard](../workflow/workflow.md), Exception]|list[Result[[WorkflowCard](../workflow/workflow.md), Exception]], Result object or list containing the added workflow cards or exceptions.

**Example:**

```python
>>> workflows = [
...     (WorkflowCard(id="wf1", name="Workflow 1"), lambda c: Workflow(card=c)),
...     (WorkflowCard(id="wf2", name="Workflow 2"), lambda c: Workflow(card=c))
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

Remove a workflow by ID or tag.

**Parameters:**

* **workflow_id**(str | list[str], optional): Single ID or list of IDs of workflows to remove.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; remove all workflows matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **skip_if_tag_not_exists**(bool, optional): If True, skip non-existent workflows.

**Returns:**

**Result**[Optional[[WorkflowCard](../workflow/workflow.md)], Exception]|list[Result[Optional[[WorkflowCard](../workflow/workflow.md)], Exception]], Result object or list containing the removed workflow cards or exceptions.

**Example:**

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

Get a workflow instance by ID or tag.

**Parameters:**

* **workflow_id**(str | list[str], optional): Single ID or list of IDs of workflows to retrieve.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; return all workflows matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **session**(Optional[Session], optional): Optional session context for the workflow.

**Returns:**

**Workflow|list[Workflow]**, workflow instance if found, otherwise None.

**Example:**

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

Add a tool to the resource manager.

**Parameters:**

* **tool**(Tool | list[Tool]): Single Tool instance or list of Tool instances to add.
* **tag**(Optional[Tag | list[Tag]], optional): Optional tags for categorizing and filtering tools.

**Returns:**

**Result**[[ToolCard](../foundation/tool/tool.md), Exception]|list[Result[[ToolCard](../foundation/tool/tool.md), Exception]], Result object or list containing the added tool cards or exceptions.

**Example:**

```python
>>> from openjiuwen.core.foundation.tool import Tool, ToolCard
>>> 
>>> tool_card = ToolCard(id="calculator", name="Calculator", description="Mathematical calculation tool")
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

Get a tool by ID or tag.

**Parameters:**

* **tool_id**(str | list[str], optional): Single ID or list of IDs of tools to retrieve.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; return all tools matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **session**(Optional[Session], optional): Optional session context for the tool.

**Returns:**

**Tool|list[Tool]**, tool instance if found, otherwise None.

**Example:**

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

Remove a tool by ID or tag.

**Parameters:**

* **tool_id**(str | list[str], optional): Single ID or list of IDs of tools to remove.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; remove all tools matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **skip_if_tag_not_exists**(bool, optional): If True, skip non-existent tags.

**Returns:**

**Result**[Optional[[ToolCard](../foundation/tool/tool.md)], Exception]|list[Result[Optional[[ToolCard](../foundation/tool/tool.md)], Exception]], Result object or list containing the removed tool cards or exceptions.

**Example:**

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

Add a model to the resource manager.

**Parameters:**

* **model_id**(str): Unique identifier for the model.
* **model**(ModelProvider): Callable provider for creating or returning a model instance.
* **tag**(Optional[Tag | list[Tag]], optional): Tags for categorizing and filtering models.

**Returns:**

**Result**[str, Exception], Result object containing the model ID or an exception.

**Example:**

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

Batch add multiple models.

**Parameters:**

* **models**(list[Tuple[str, ModelProvider]]): List of tuples, each containing (model_id, ModelProvider).
* **tag**(Optional[Tag | list[Tag]], optional): Optional tags to apply to all added models.

**Returns:**

**Result**[str, Exception]|list[Result[str, Exception]], Result object or list containing the model IDs or exceptions.

**Example:**

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

Remove a model by ID or tag.

**Parameters:**

* **model_id**(str | list[str], optional): Single ID or list of IDs of models to remove.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; remove all models matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **skip_if_tag_not_exists**(bool, optional): If True, skip non-existent models.

**Returns:**

**Result**[str, Exception]|list[Result[str, Exception]], Result object or list containing the model IDs or exceptions.

**Example:**

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

Get a model instance by ID or tag.

**Parameters:**

* **model_id**(str | list[str], optional): Single ID or list of IDs of models to retrieve.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; return all models matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **session**(Optional[Session], optional): Optional session context for the model.

**Returns:**

**Model|list[Model]**, model instance if found, otherwise None.

**Example:**

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

Add a prompt template to the resource manager.

**Parameters:**

* **prompt_id**(str): Unique identifier for the prompt template.
* **template**(PromptTemplate): PromptTemplate instance containing prompt content and configuration.
* **tag**(Optional[Tag | list[Tag]], optional): Tags for categorizing and filtering prompts.

**Returns:**

**Result**[str, Exception], Result object containing the prompt ID or an exception.

**Example:**

```python
>>> from openjiuwen.core.foundation.prompt import PromptTemplate
>>> 
>>> template = PromptTemplate(content="Please analyze the following data: {data}")
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

Batch add multiple prompt templates.

**Parameters:**

* **prompts**(list[Tuple[str, PromptTemplate]]): List of tuples, each containing (prompt_id, PromptTemplate).
* **tag**(Optional[Tag | list[Tag]], optional): Optional tags to apply to all added prompts.

**Returns:**

**Result**[str, Exception]|list[Result[str, Exception]], Result object or list containing the prompt IDs or exceptions.

**Example:**

```python
>>> prompts = [
...     ("prompt1", PromptTemplate(content="Prompt 1: {input}")),
...     ("prompt2", PromptTemplate(content="Prompt 2: {input}"))
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

Remove a prompt template by ID or tag.

**Parameters:**

* **prompt_id**(str | list[str], optional): Single ID or list of IDs of prompts to remove.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; remove all prompts matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **skip_if_tag_not_exists**(bool, optional): If True, skip non-existent prompts.

**Returns:**

**Result**[str, Exception]|list[Result[str, Exception]], Result object or list containing the prompt IDs or exceptions.

**Example:**

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

Get a prompt template by ID or tag.

**Parameters:**

* **prompt_id**(str | list[str], optional): Single ID or list of IDs of prompts to retrieve.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; return all prompts matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.

**Returns:**

**[PromptTemplate](../foundation/prompt/template.md)|list[[PromptTemplate](../foundation/prompt/template.md)]**, prompt template instance if found, otherwise None.

**Example:**

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

Add a system operation via SysOperationCard (optional tags). Supports batch addition of multiple system operations.

**Parameters:**

* **card**(SysOperationCard | List[SysOperationCard]): Single SysOperationCard or list of SysOperationCards (required).
* **tag**(Optional[Tag | List[Tag]], optional): Optional single tag or list of tags for categorization.

**Returns:**

**Result**[[SysOperationCard](../sys_operation/sys_operation.md), Exception] | List[Result[[SysOperationCard](../sys_operation/sys_operation.md), Exception]], single result object or list of result objects containing the successfully added card(s) or error(s).

**Example:**

```python
>>> from openjiuwen.core.sys_operation import SysOperationCard
>>> 
>>> # Add a single system operation
>>> card = SysOperationCard(id="cleanup_op", name="Cleanup Operation")
>>> result = Runner.resource_mgr.add_sys_operation(card, tag=["maintenance"])
>>>
>>> # Batch add multiple system operations
>>> cards = [
...     SysOperationCard(id="op1", name="Operation 1"),
...     SysOperationCard(id="op2", name="Operation 2")
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

Remove a system operation by ID/tag (supports batch). When removing a system operation, its associated tools will also be removed.

**Parameters:**

* **sys_operation_id**(str | List[str]): Single operation ID or list of IDs to remove (required).
* **tag**(Optional[Tag | List[Tag]], optional): Single tag or list of tags filter (default: GLOBAL).
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy (default: ALL).
* **skip_if_tag_not_exists**(bool, optional): If True, skip non-existent tags (default: False).

**Returns:**

**Result**[Optional[[SysOperationCard](../sys_operation/sys_operation.md)], Exception] | List[Result[Optional[[SysOperationCard](../sys_operation/sys_operation.md)], Exception]], single result object or list of result objects containing the removed card(s) or error(s).

**Example:**

```python
>>> # Remove a single system operation
>>> Runner.resource_mgr.remove_sys_operation("cleanup_op")
>>>
>>> # Batch remove multiple system operations
>>> Runner.resource_mgr.remove_sys_operation(["op1", "op2"])
>>>
>>> # Remove by tag
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

Get a system operation instance by ID/tag.

**Parameters:**

* **sys_operation_id**(str | List[str], optional): Single operation ID or list of IDs.
* **tag**(Optional[Tag | List[Tag]], optional): Single tag or list of tags filter.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy (default: ALL).
* **session**(Optional[Session], optional): Optional context session.

**Returns:**

**SysOperation | List[[SysOperation](../sys_operation/sys_operation.md)]**, single system operation instance or list of instances, returns None if not found.

**Example:**

```python
>>> # Get a single system operation
>>> operation = Runner.resource_mgr.get_sys_operation("cleanup_op")
>>>
>>> # Batch get multiple system operations
>>> operations = Runner.resource_mgr.get_sys_operation(["op1", "op2"])
>>>
>>> # Get by tag
>>> operations = Runner.resource_mgr.get_sys_operation(tag=["scheduled"])
```

### get_sys_op_tool_cards

```python
get_sys_op_tool_cards(self,
                      sys_operation_id: str,
                      *,
                      operation_name: str | List[str] = None,
                      tool_name: str | List[str] = None
                      ) -> ToolCard | List[ToolCard] | None
```

Get tool cards from a system operation.

**Parameters:**

* **sys_operation_id**(str): The ID of the system operation.
* **operation_name**(str | List[str], optional): Single operation name or list of operation names, e.g., `"fs"`, `["fs", "shell"]`. If `None`, returns all tool cards from all operations.
* **tool_name**(str | List[str], optional): Single tool name or list of tool names, e.g., `"read_file"`, `["read_file", "write_file"]`. Only valid when `operation_name` is a single string. Cannot be used when `operation_name` is a list. If `None`, returns all tool cards from the specified operation(s).

**Returns:**

**[ToolCard](../foundation/tool/tool.md) | List[[ToolCard](../foundation/tool/tool.md)] | None**, Tool card(s) matching the criteria.

**Raises:**

* **ValidationError**: If `operation_name` is a list and `tool_name` is also provided.

**Example:**

```python
>>> # Scenario 1: Get a single tool card
>>> tool_card = Runner.resource_mgr.get_sys_op_tool_cards("my_sys_op", operation_name="fs", tool_name="read_file")
>>>
>>> # Scenario 2: Get multiple tool cards from the same operation
>>> tool_cards = Runner.resource_mgr.get_sys_op_tool_cards("my_sys_op", operation_name="fs", tool_name=["read_file", "write_file"])
>>>
>>> # Scenario 3: Get all tool cards from a single operation
>>> tool_cards = Runner.resource_mgr.get_sys_op_tool_cards("my_sys_op", operation_name="fs")
>>>
>>> # Scenario 4: Get all tool cards from multiple operations
>>> tool_cards = Runner.resource_mgr.get_sys_op_tool_cards("my_sys_op", operation_name=["fs", "shell"])
>>>
>>> # Scenario 5: Get all tool cards from all operations
>>> tool_cards = Runner.resource_mgr.get_sys_op_tool_cards("my_sys_op")
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

Get tool information/metadata by ID, type, or tag.

**Parameters:**

* **tool_id**(str | list[str], optional): Single ID or list of IDs of tools to get information for.
* **tool_type**(str | list[str], optional): Single type or list of types for filtering tools. Common types: ["function", "mcp", "workflow", "agent", "group"].
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; return information for all tools matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **ignore_exception**(bool, optional): If True, ignore exceptions and return None for failed items.

**Returns:**

**[ToolInfo](../foundation/tool/tool.md)|list[[ToolInfo](../foundation/tool/tool.md)]**, tool information instance if found, otherwise None.

**Example:**

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

Add MCP (Model Context Protocol) server configuration.

**Parameters:**

* **server_config**(McpServerConfig | list[McpServerConfig]): Single or list of McpServerConfig instances.
* **tag**(Optional[Tag | list[Tag]], optional): Optional tags for categorizing servers.
* **expiry_time**(Optional[float], optional): Optional Unix timestamp for server configuration expiration. If None, the configuration never expires.

**Returns:**

**Result**[str, Exception]|list[Result[str, Exception]], Result object or list containing the server name or exceptions.

**Example:**

```python
>>> from openjiuwen.core.foundation.tool import McpServerConfig
>>> 
>>> config = McpServerConfig(server_id="mcp1", server_name="MCP Server 1", ...)
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

Refresh MCP server tool cards by name.

**Parameters:**

* **server_id**(Optional[str | list[str]], optional): Single or list of MCP server IDs to refresh.
* **server_name**(Optional[str | list[str]], optional): Single or list of MCP server names to refresh.
* **tag**(Optional[Tag | list[Tag]], optional): Optional tags for filtering servers to refresh.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **ignore_exception**(bool, optional): If True, continue refreshing other servers if one fails.
* **skip_if_tag_not_exists**(bool, optional): If True, skip non-existent servers.

**Returns:**

**Result[str, Exception]|list[Result[str, Exception]]**, Result object or list containing the server name or exceptions.

**Example:**

```python
>>> results = await Runner.resource_mgr.refresh_mcp_server(server_name=["MCP Server 1"], tag=["external"])
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

Remove MCP servers by name or tag.

**Parameters:**

* **server_id**(Optional[str | list[str]], optional): Single or list of MCP server IDs to remove.
* **server_name**(Optional[str | list[str]], optional): Single or list of MCP server names to remove.
* **tag**(Optional[Tag | list[Tag]], optional): Single tag or list of tags; remove all servers matching the tags.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **skip_if_tag_not_exists**(bool, optional): If True, skip non-existent servers.
* **ignore_exception**(bool, optional): If True, continue removing other servers if one fails.

**Returns:**

**Result**[str, Exception]|list[Result[str, Exception]], Result object or list containing the server name or exceptions.

**Example:**

```python
>>> result = await Runner.resource_mgr.remove_mcp_server(server_name="MCP Server 1")
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

Get an MCP tool by name and server.

**Parameters:**

* **name**(str | list[str], optional): Single or list of MCP tool names to retrieve.
* **server_id**(str | list[str], optional): Single or list of MCP server IDs containing the tools.
* **server_name**(str | list[str], optional): Single or list of MCP server names containing the tools.
* **tag**([Tag | list[Tag]], optional): Optional tags for filtering servers/tools.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **skip_if_tag_not_exists**(bool, optional): If True, skip non-existent servers.
* **ignore_exception**(bool, optional): If True, ignore exceptions required for refreshing MCP servers (if needed).
* **session**(Optional[Session], optional): Optional session context for the tool.

**Returns:**

**Tool|list[Tool]**, MCP tool instance if found, otherwise None.

**Example:**

```python
>>> tool = await Runner.resource_mgr.get_mcp_tool(name="weather_api", server_name="MCP Server 1")
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

Get MCP tool information/metadata by name and server.

**Parameters:**

* **name**(str | list[str], optional): Single or list of MCP tool names to get information for. If None, returns information for all tools in the specified server.
* **server_name**(str | list[str], optional): Single or list of MCP server names containing the tools. If name is None, this must be provided.
* **server_id**(str | list[str], optional): Single or list of MCP server IDs containing the tools.
* **tag**(Optional[Tag | list[Tag]], optional): Optional tags for filtering servers/tools.
* **tag_match_strategy**(TagMatchStrategy, optional): Tag matching strategy when using tag parameter.
* **skip_if_tag_not_exists**(bool, optional): If True, skip non-existent servers.
* **ignore_exception**(bool, optional): If True, ignore exceptions required for refreshing MCP servers.

**Returns:**

**[ToolInfo](../foundation/tool/tool.md)|list[[ToolInfo](../foundation/tool/tool.md)]**, MCP tool information instance if found, otherwise None.

**Example:**

```python
>>> tool_info = await Runner.resource_mgr.get_mcp_tool_infos(name="weather_api", server_name="MCP Server 1")
>>> tool_infos = await Runner.resource_mgr.get_mcp_tool_infos(server_id="mcp1")
```

### get_resource_by_tag

```python
get_resource_by_tag(self, tag: Tag) -> Optional[list[BaseCard]]
```

Retrieve all resources associated with a specific tag.

**Parameters:**

* **tag**(Tag): The tag to search for.

**Returns:**

**Optional[list[BaseCard]]**, list of BaseCard instances representing resources with the specified tag, or None if no resources found.

**Example:**

```python
>>> cards = Runner.resource_mgr.get_resource_by_tag("analysis")
>>> for card in cards:
...     print(f"Resource ID: {card.id}, Type: {type(card).__name__}")
```


### list_tags

```python
list_tags(self) -> list[Tag]
```

List all tags currently used across all resources.

**Returns:**

**list[Tag]**, list of unique tag strings.

**Example:**

```python
>>> tags = Runner.resource_mgr.list_tags()
>>> print(f"Available tags: {tags}")
```

### has_tag

```python
has_tag(self, tag: str) -> bool
```

Check if the specified tag exists in the resource_mgr.

**Parameters:**

* **tag**(str): The tag to check for existence.

**Returns:**

**bool**, True if the tag exists, otherwise False.

**Example:**

```python
>>> if Runner.resource_mgr.has_tag("analysis"):
...     print("'analysis' tag exists")
```

### remove_tag

```python
async remove_tag(self,
           tag: Tag | list[Tag],
           *,
           skip_if_tag_not_exists: bool = False,
          ) -> Result[Tag, Exception] | list[Result[Tag, Exception]]
```

Remove tags from all resources.

**Parameters:**

* **tag**(Tag | list[Tag]): Single tag or list of tags to remove from all resources.
* **skip_if_tag_not_exists**(bool, optional): If True, ignore non-existent tags.

**Returns:**

**Result**[Tag, Exception]|list[Result[Tag, Exception]], Result object or list containing the tags or exceptions.

**Example:**

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

Replace all tags on a resource with new tags.

**Parameters:**

* **resource_id**(str): Resource identifier.
* **tag**(Tag | list[Tag]): New tags to set on the resource.

**Returns:**

**Result**[list[Tag], Exception], Result object containing the new list of tags or an exception.

**Example:**

```python
>>> result = Runner.resource_mgr.update_resource_tag("data_analyzer", ["expert", "premium"])
>>> if result.is_ok():
...     print(f"Updated tags: {result.msg()}")
```

### add_resource_tag

```python
add_resource_tag(self,
                     resource_id: str,
                     tag: Tag | list[Tag]
                     ) -> Result[list[Tag], Exception]
```

Add tags to a resource.

**Parameters:**

* **resource_id**(str): Resource identifier.
* **tag**(Tag | list[Tag]): Tags to add to the resource.

**Returns:**

**Result**[list[Tag], Exception], Result object containing all tags now associated with the resource.

**Example:**

```python
>>> result = Runner.resource_mgr.add_resource_tag("calculator", ["math", "utility"])
>>> if result.is_ok():
...     print(f"Current tags: {result.msg()}")
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

Remove specific tags from a resource.

**Parameters:**

* **resource_id**(str): Resource identifier.
* **tag**(Tag | list[Tag]): Tags to remove from the resource.
* **skip_if_tag_not_exists**(bool, optional): If True, ignore non-existent tags.

**Returns:**

**Result**[list[Tag], Exception], Result object containing the remaining tags on the resource.

**Example:**

```python
>>> result = Runner.resource_mgr.remove_resource_tag("data_analyzer", "test", skip_if_tag_not_exists=True)
```

### get_resource_tag

```python
get_resource_tag(self, resource_id: str) -> Optional[list[Tag]]
```

Get all tags associated with a resource.

**Parameters:**

* **resource_id**(str): Resource identifier.

**Returns:**

**Optional[list[Tag]]**, list of tags associated with the resource, or None if resource not found.

**Example:**

```python
>>> tags = Runner.resource_mgr.get_resource_tag("data_analyzer")
>>> print(f"Resource tags: {tags}")
```

### resource_has_tag

```python
>>> resource_has_tag(self, resource_id: str, tag: Tag) -> bool
```

Check if a specific resource is associated with a given tag.

**Parameters:**

* **resource_id**(str): Unique identifier of the resource to check.
* **tag**(Tag): Tag to verify association with the resource.

**Returns:**

**bool**, True if the resource has the specified tag, otherwise False.

**Example:**

```python
>>> if Runner.resource_mgr.resource_has_tag("data_analyzer", "expert"):
...     print("Resource has 'expert' tag")
```


### release

```python
async release(self)
```

Release all resources and perform cleanup.

This method should be called when the ResourceMgr is no longer needed.

**Example:**

```python
>>> await Runner.resource_mgr.release()
```