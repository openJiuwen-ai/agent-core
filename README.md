# openJiuwen Core

## 简介

**openJiuwen Core**是一款大模型应用的Python软件开发工具包，为运行在**openJiuwen**框架上的智能体提供高性能运行时。这款开发工具包不仅封装了Agent创建、工作流编排、大模型与工具调用等多层次、易上手的对外接口；还内置了支持异步IO、流式处理的高性能运行时，实现智能体的状态保存和中断接续；更配备了提示词自优化、提示词生成、全链路观测等一系列智能体调试调优工具。**openJiuwen Core**开发工具包兼顾灵活性与稳定性，助力开发者高效构建稳定的大模型应用。

## 为什么选择openJiuwen Core?

- **开箱即用的组件**：提供丰富的预置组件，包括意图识别、提问器、大模型调用、工具组件等，大幅降低开发门槛。

- **高效精准的任务执行**：内置高性能执行引擎，支持异步并行图执行、组件并发、流式处理等能力，确保Agent在执行任务时的高效性与精准性。

- **灵活可控的多工作流跳转能力**：支持Agent在同一会话中管理多个工作流，支持用户在不同工作流间自由切换，由框架保障被打断工作流的断点接续。解决了用户在同一对话中切换不同任务场景的需求，提供了灵活的多任务管理能力。

- **实用的提示词开发与调优能力**：输入需求即可一键生成适配的提示词，结合真实场景数据集实现自动优化迭代，助力开发者快速产出高质量提示词，降低Agent核心能力开发门槛。

## 快速开始

### 安装

- 操作系统：兼容Windows、Linux、macOS。
- Python 版本：Python的版本应高于或者等于Python 3.11版本，建议使用3.11.4版本，使用前请检查Python版本信息。

**从PyPi安装**

```bash
pip install -U openjiuwen
```

### 样例

让我们创建一个简单的WorkflowAgent，调用工作流生成一段文本:

```python
import os
import asyncio
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.llm_comp import LLMComponent, LLMCompConfig
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.workflow.workflow_config import WorkflowConfig, WorkflowMetadata, WorkflowInputsSchema
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.utils.llm.base import BaseModelInfo
from openjiuwen.agent.workflow_agent.workflow_agent import WorkflowAgent
from openjiuwen.agent.config.workflow_config import WorkflowAgentConfig
from openjiuwen.agent.common.schema import WorkflowSchema

# TODO：请提供用户的大模型配置信息
os.environ.setdefault("API_BASE", "your_api_base")
os.environ.setdefault("API_KEY", "your_api_key")
os.environ.setdefault("MODEL_PROVIDER", "your_provider")
os.environ.setdefault("MODEL_NAME", "your_model_name")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

# 创建大模型配置对象
model_config = ModelConfig(
    model_provider=os.getenv("MODEL_PROVIDER"),
    model_info=BaseModelInfo(
        api_key=os.getenv("API_KEY"),
        api_base=os.getenv("API_BASE"),
        model=os.getenv("MODEL_NAME"),
    )
)

# 创建工作流配置
workflow_config = WorkflowConfig(
    metadata=WorkflowMetadata(
        id="generate_text_workflow",
        name="generate_text",
        version="1.0",
        description="根据用户输入生成文本"
    ),
    workflow_inputs_schema=WorkflowInputsSchema(
        type="object",
        properties={"query": {"type": "string", "description": "用户输入", "required": True}},
        required=['query']
    )
)

# 初始化工作流
flow = Workflow(workflow_config=workflow_config)

# 创建组件
start = Start({"inputs": [{"id": "query", "type": "String", "required": "true", "sourceType": "ref"}]})
end = End({"responseTemplate": "工作流输出文本: {{output}}"})
llm_config = LLMCompConfig(
    model=model_config,
    template_content=[
        {"role": "system", "content": "你是一个AI助手，能够帮我完成任务。\n注意：请不要推理，直接输出结果就好了！"},
        {"role": "user", "content": "{{query}}"}],
    response_format={"type": "text"},
    output_config={"output": {"type": "string", "required": True}},
)
llm = LLMComponent(llm_config)

# 注册组件并连接
flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
flow.add_workflow_comp("llm", llm, inputs_schema={"query": "${start.query}"})
flow.set_end_comp("end", end, inputs_schema={"output": "${llm.output}"})
flow.add_connection("start", "llm")
flow.add_connection("llm", "end")

# 创建并绑定Agent
schema = WorkflowSchema(
    id=flow.config().metadata.id,
    name=flow.config().metadata.name,
    version=flow.config().metadata.version,
    description="第一个工作流",
    inputs={"query": {"type": "string"}},
)
agent_config = WorkflowAgentConfig(
    id="hello_agent",
    version="0.1.0",
    description="第一个Agent",
    workflows=[schema],
)
workflow_agent = WorkflowAgent(agent_config)
workflow_agent.bind_workflows([flow])

# 运行Agent
async def main():
    invoke_result = await Runner.run_agent(workflow_agent, {"query": "你好,请生成一则笑话,不要超过20个字"})
    output_result = invoke_result.get("output").result
    print(f"WorkflowAgent output result >>> {output_result.get('responseContent')}")

asyncio.run(main())
```

预期输出
```
WorkflowAgent output result >>> 工作流输出文本: 冰箱罢工了，因为它觉得生活太冷了。
```


## 架构设计

**openJiuwen Core**作为openJiuwen架构的核心引擎，在本次开源版本中，核心能力包括：

* **SDK接口层**：聚焦大模型应用的开发需求，为开发者提供Python SDK接口。接口能力覆盖Agent实例创建、工作流设计与编排、大模型调用及输出结果解析、提示词模板构建与动态填充，并支持本地工具调用外部服务。

* **Agent引擎**：针对ReAct智能交互与工作流自动跳转两大场景，通过构建Agent控制器，支撑复杂任务规划、工具选择与调用、工作流任务切换。内置开箱即用的标准化组件，降低Agent的开发门槛。提供Agent运行时环境，同时配套对话历史上下文管理、基础工具集等底层能力。

## 功能特性

### **Agent编排**

**openJiuwen Core**内置了**ReActAgent**和**WorkflowAgent**两类预置智能体，功能丰富、开发灵活，可满足不同场景下的智能需求。

- ReActAgent：遵循ReAct（Reasoning + Action）规划范式，以**思考→行动→观察**的循环迭代完成任务。凭借强大的多轮推理与自我修正能力，具备动态决策和环境适应特性，适用于需复杂推理、策略调整的多样化场景。
- WorkflowAgent：专注多步骤任务导向的流程自动化，严格按照用户预定义流程高效执行复杂任务，也能够随着用户意图的变更灵活切换任务。其侧重于基于预设流程实现任务的规范化与高效化执行，适用于任务结构清晰、可拆解为多步骤的场景。

### **高性能执行引擎**

**openJiuwen Core**提供高性能执行引擎，支持分布式部署与低成本运行，可有效解决海量智能体执行效率低、运维成本高的痛点，为大规模智能体集群运转及行业级生产应用落地提供坚实支撑。

- **异步并行图执行器**：具备组件并发执行、异步IO处理、结构化上下文管理能力，支持多工作流任务高效并行处理，实现异构组件的灵活调用。
- **组件基础能力**：支持组件间流批一体传值、动态跳转、状态中断与恢复，同时提供组件动态配置与多实例管理功能。
- **数据存储与流式处理**：提供流式输出、组件间流式传输等数据管控能力，可对接外部存储系统实现智能体上下文数据外置，助力分布式场景下的弹性扩展。

## 参与贡献

我们欢迎所有形式的贡献，包括但不限于:
- 提交问题和功能建议
- 改进文档
- 提交代码
- 分享使用经验

## 开源许可证

本项目依据Apache-2.0许可证授权。
