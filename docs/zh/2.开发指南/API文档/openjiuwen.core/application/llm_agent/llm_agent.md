# openjiuwen.core.application.llm_agent.llm_agent

## class openjiuwen.core.application.llm_agent.llm_agent.LLMAgent

```
class openjiuwen.core.application.llm_agent.llm_agent.LLMAgent(agent_config: ReActAgentConfig)
```

基于新架构的ReAct风格智能体，内部使用控制器完成“思考-规划-执行-汇报”流程，可选接入长期记忆。

### async invoke(inputs: Dict, session: Session = None) -> Dict

执行智能体并返回完整结果。支持长记忆异步写入。

**参数：**
* **inputs**(Dict)：必含query，建议带 conversation_id（默认"default_session"）与user_id（写记忆必需）。
* **session**(Session, 可选)：已有会话，不传则自动创建。

**返回：**
* **Dict**：控制器执行结果（内容取决于任务规划与执行）。

### async stream(inputs: Dict, session: Session = None) -> AsyncIterator[Any]

流式执行智能体，实时产出`OutputSchema`块。自动管理会话生命周期，支持长记忆异步写入。

**参数：**

* **inputs**(Dict)：同`invoke`，需`query`，可选`conversation_id`/`user_id`。
* **session**(Session, 可选)：外部会话；未提供则内部创建并负责生命周期。

**返回：**

* **AsyncIterator[Any]**：流式输出块。

**异常：**

* **RuntimeError**：未初始化控制器。

### set_prompt_template(prompt_template: List[Dict]) -> None

更新Agent与LLMController的提示模板，并刷新内部配置包装器。

**参数：**

* **prompt_template**(List[Dict])：新的提示模板列表。

### async _write_messages_to_memory(inputs, result=None) -> None

将用户查询和模型回答写入长期记忆。支持多种输出格式（`OutputSchema`/dict/str）。

**参数：**

* **inputs**：原始输入字典，需包含`user_id`。
* **result**(可选)：控制器返回结果或流式拼接文本。

## func openjiuwen.core.application.llm_agent.llm_agent.create_llm_agent_config(agent_id: str, agent_version: str, description: str, workflows: List[WorkflowSchema], plugins: List[PluginSchema], model: ModelConfig, prompt_template: List[Dict], tools: Optional[List[str]] = None)

创建LLM ReAct Agent的配置对象（兼容旧版接口）。

**参数**：
- **agent_id**(str)：Agent的唯一标识符。
- **agent_version**(str)：Agent的版本号。
- **description**(str)：Agent的描述信息。
- **workflows**(List[WorkflowSchema])：Agent支持的工作流描述列表。
- **plugins**(List[PluginSchema])：Agent支持的插件描述列表。
- **model**(ModelConfig)：Agent使用的模型配置。
- **prompt_template**(List[Dict])：提示词模板列表，例如`{ "role": "system", "content": "..." }`；`role`可为`system`或`user`，默认值：`[]`。
- **tools**(List[str], 可选)：可用工具ID列表，默认值：`None`（内部会转为空列表）。

**返回**：
- **ReActAgentConfig**：新的ReAct Agent配置对象。

## func openjiuwen.core.application.llm_agent.llm_agent.create_llm_agent(agent_config: ReActAgentConfig, workflows: List[Workflow] = None, tools: List[Tool] = None)

根据配置创建LLM ReAct Agent并批量注册工作流与工具的便捷工厂函数。

**参数**：
- **agent_config**(ReActAgentConfig)：已构建的ReAct Agent配置对象。
- **workflows**(List[Workflow], 可选)：要注册的工作流实例列表，默认`None`（内部转为空列表）。
- **tools**(List[Tool], 可选)：要注册的工具实例列表，默认`None`（内部转为空列表）。

**返回**：
- **LLMAgent**：初始化并完成工作流/工具注册的LLM Agent实例。


# openjiuwen.core.single_agent.legacy.config

## class openjiuwen.core.single_agent.legacy.config.ConstrainConfig

表示智能体行为约束配置的数据模型。

- **reserved_max_chat_rounds**(int)：允许的最大对话轮数，必须大于0。默认值：10。
- **max_iteration**(int)：单次任务的最大迭代次数，必须大于0。默认值：5。

## class openjiuwen.core.single_agent.legacy.config.IntentDetectionConfig

意图识别配置的数据模型。

- **intent_detection_template**(List[Dict])：意图判别提示模版列表，默认空列表。
- **default_class**(str)：默认分类标签，默认值："分类1"。
- **enable_input**(bool)：是否启用当前输入内容参与识别，默认值：True。
- **enable_history**(bool)：是否使用聊天历史辅助识别，默认值：False。
- **chat_history_max_turn**(int)：使用的历史轮次上限，默认值：5。
- **category_list**(List[str])：可选分类标签列表，默认空列表。
- **user_prompt**(str)：用户提示模版，默认空字符串。
- **example_content**(List[str])：示例内容列表，用于few-shot引导，默认空列表。

## class openjiuwen.core.single_agent.legacy.config.LegacyReActAgentConfig

兼容旧版接口的ReAct Agent配置。

- **controller_type**(ControllerType)：控制器类型，默认`ReActController`。
- **prompt_template_name**(str)：提示模板名称，默认值：`"react_system_prompt"`。
- **prompt_template**(List[Dict])：提示模板列表，默认空列表。
- **constrain**(ConstrainConfig)：行为约束配置，默认`ConstrainConfig()`。
- **plugins**(List[Any])：插件schema列表，默认空列表。
- **memory_scope_id**(str)：长期记忆作用域ID，默认空字符串。
- **agent_memory_config**(AgentMemoryConfig)：长记忆配置，默认构造。

