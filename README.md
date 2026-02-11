# openJiuwen Core

[中文版](README.zh.md) | [English Version](README.md)

## Introduction

**openJiuwen Core** is a Python SDK for large language model applications, providing a high-performance runtime for agents running on the **openJiuwen** framework. This development toolkit not only encapsulates multi-level, easy-to-use external interfaces for Agent creation, workflow orchestration, large language model invocation, and tool calling, but also includes a built-in high-performance runtime supporting asynchronous IO and streaming processing, enabling agent state saving and interruption recovery. Additionally, it comes equipped with a series of agent debugging and optimization tools, including prompt auto-optimization, prompt generation, and full-link observability. The **openJiuwen Core** development toolkit balances flexibility and stability, helping developers efficiently build stable large language model applications.

## Why Choose openJiuwen Core?

- **Ready-to-use Components**: Provides rich pre-built components, including intent recognition, questioners, large language model invocation, tool components, and more, significantly lowering the development threshold.

- **Efficient and Accurate Task Execution**: Built-in high-performance execution engine supports asynchronous parallel graph execution, component concurrency, streaming processing, and other capabilities, ensuring high efficiency and accuracy when agents execute tasks.

- **Flexible and Controllable Multi-Workflow Jump Capability**: Supports agents managing multiple workflows in the same session, allows users to freely switch between different workflows, and ensures checkpoint recovery for interrupted workflows through the framework. This solves the need for users to switch between different task scenarios in the same conversation, providing flexible multi-task management capabilities.

- **Practical Prompt Development and Optimization Capabilities**: Input requirements to generate suitable prompts with one click, combined with real-world scenario datasets for automatic optimization iteration, helping developers quickly produce high-quality prompts and lowering the development threshold for core agent capabilities.

## Quick Start

### Installation

- Operating System: Compatible with Windows, Linux, and macOS.
- Python Version: Python version should be 3.11 or higher, but lower than 3.14. Please check your Python version before use, Python 3.11.4 is recommended.

**Install from PyPI**

```bash
pip install -U openjiuwen
```

### Example

Let's create a simple WorkflowAgent that calls a workflow to generate a piece of text:

```python
import os
import asyncio
from openjiuwen.core.workflow import Start, End, LLMComponent, LLMCompConfig, generate_workflow_key
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.workflow import Workflow, WorkflowCard


# TODO: Please provide your LLM configuration information
os.environ.setdefault("API_BASE", "your_api_base")
os.environ.setdefault("API_KEY", "your_api_key")
os.environ.setdefault("MODEL_PROVIDER", "your_provider")
os.environ.setdefault("MODEL_NAME", "your_model_name")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

# Create LLM configuration object
model_client_config = ModelClientConfig(
    client_provider=os.getenv("MODEL_PROVIDER"),
    api_key=os.getenv("API_KEY"),
    api_base=os.getenv("API_BASE"),
    verify_ssl=os.getenv("LLM_SSL_VERIFY").lower() == "true"
)
model_config = ModelRequestConfig(
    model=os.getenv("MODEL_NAME")
)

# Create workflow configuration
workflow_card = WorkflowCard(
    id="generate_text_workflow",
    name="generate_text",
    version="1.0",
    description="Generate text based on user input",
    input_params={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "User input"}},
            "required": ['query']
    }
)

# Initialize workflow
flow = Workflow(card=workflow_card)

# Create components
start = Start()
end = End({"responseTemplate": "Workflow output text: {{output}}"})
llm_config = LLMCompConfig(
    model_client_config=model_client_config,
    model_config=model_config,
    template_content=[
        {"role": "system", "content": "You are an AI assistant that can help me complete tasks.\nNote: Please do not reason, just output the result directly!"},
        {"role": "user", "content": "{{query}}"}],
    response_format={"type": "json"},
    output_config={
        "type": "object",
        "description": "LLM output schema",
        "properties": {
            "output": {
                "type": "string",
                "description": "LLM output"
            }
        },
        "required": ["output"]
    }
)
llm = LLMComponent(llm_config)

# Register components and connect
flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
flow.add_workflow_comp("llm", llm, inputs_schema={"query": "${start.query}"})
flow.set_end_comp("end", end, inputs_schema={"output": "${llm.output}"})
flow.add_connection("start", "llm")
flow.add_connection("llm", "end")

Runner.resource_mgr.add_workflow(
    WorkflowCard(id=generate_workflow_key(flow.card.id, flow.card.version)),
    lambda: flow,
)

# Create and bind Agent
agent_config = WorkflowAgentConfig(
    id="hello_agent",
    version="0.1.1",
    description="First Agent",
)
workflow_agent = WorkflowAgent(agent_config)
workflow_agent.add_workflows([flow])


# Run Agent
async def main():
    invoke_result = await Runner.run_agent(workflow_agent, {"query": "Hello, please generate a joke, no more than 20 characters"})
    output_result = invoke_result.get("output").result
    print(f"WorkflowAgent output result >>> {output_result.get('response')}")

asyncio.run(main())
```

Expected Output
```
WorkflowAgent output result >>> Workflow output text: The refrigerator went on strike because it felt life was too cold.
```


## Architecture Design

**openJiuwen Core** serves as the core engine of the openJiuwen architecture. In this open-source version, the core capabilities include:

* **SDK Interface Layer**: Focuses on the development needs of large language model applications, providing Python SDK interfaces for developers. Interface capabilities cover Agent instance creation, workflow design and orchestration, large language model invocation and output result parsing, prompt template construction and dynamic filling, and support for local tools calling external services.

* **Agent Engine**: For the two major scenarios: ReAct intelligent interaction and workflow automatic jumping, with our Agent controllers, openJiuwen supports complex task planning, tool selection and invocation, and workflow task switching. Built-in ready-to-use standardized components lower the development threshold for Agents. Provides Agent runtime environment, along with underlying capabilities such as conversation history context management and basic tool sets.

## Features

### **Agent Orchestration**

**openJiuwen Core** includes two types of pre-built agents: **ReActAgent** and **WorkflowAgent**, which are feature-rich and flexible for development, meeting intelligent needs in different scenarios.

- ReActAgent: Follows the ReAct (Reasoning + Action) planning paradigm, completing tasks through iterative cycles of **thinking → action → observation**. With powerful multi-round reasoning and self-correction capabilities, it has dynamic decision-making and environmental adaptation characteristics, suitable for diverse scenarios requiring complex reasoning and strategy adjustment.
- WorkflowAgent: Focuses on multi-step task-oriented process automation, strictly executing complex tasks efficiently according to user-predefined processes, and can also flexibly switch tasks as user intent changes. It emphasizes standardized and efficient task execution based on preset processes, suitable for scenarios with clear task structures that can be decomposed into multiple steps.

### **High-Performance Execution Engine**

**openJiuwen Core** provides a high-performance execution engine, supporting distributed deployment and low-cost operation, effectively solving the pain points of low execution efficiency and high operation and maintenance costs for massive agents, providing solid support for large-scale agent cluster operation and industry-level production application deployment.

- **Asynchronous Parallel Graph Executor**: Has capabilities for component concurrent execution, asynchronous IO processing, and structured context management, supporting efficient parallel processing of multi-workflow tasks and flexible invocation of heterogeneous components.
- **Component Basic Capabilities**: Supports batch and streaming value transfer between components, dynamic jumping, state interruption and recovery, while providing component dynamic configuration and multi-instance management functions.
- **Data Storage and Streaming Processing**: Provides data control capabilities such as streaming output and streaming transmission between components, can connect to external storage systems to externalize agent context data, helping with elastic scaling in distributed scenarios.

## Contributing

We welcome all forms of contributions, including but not limited to:
- Submitting issues and feature suggestions
- Improving documentation
- Submitting code
- Sharing usage experiences

## Open Source License

This project is licensed under the Apache-2.0 License.
