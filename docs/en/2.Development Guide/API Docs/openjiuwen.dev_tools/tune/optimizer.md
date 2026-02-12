# openjiuwen.dev_tools.tune.optimizer

## class openjiuwen.dev_tools.tune.optimizer.instruction_optimizer.InstructionOptimizer

```python
openjiuwen.dev_tools.tune.optimizer.instruction_optimizer.InstructionOptimizer(model_config: ModelRequestConfig, model_client_config: ModelClientConfig, parameters: Optional[Dict[str, LLMCall]] = None, **kwargs)
```

`InstructionOptimizer` class is an Agent prompt instruction optimizer, corrects internal prompt templates by analyzing feedback generated from Agent input-output error cases, optimizes Agent accuracy on error cases.
**Parameters**:

* **model_config** ([ModelRequestConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelrequestconfig)): Large language model request configuration for executing optimization.
* **model_client_config** ([ModelClientConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelclientconfig)): Large language model service configuration for executing optimization.
* **parameters** (Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#openjiuwencoreoperatorllm_callbase)], optional): Agent prompt optimization parameter list, can be empty. If empty, need to bind parameters through bind_parameter interface during subsequent optimization. Default value: `None`. Parameters can be obtained through get_llm_calls interface (currently only ChatAgent supports).

**Example**:

```python
>>> import os
>>> from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
>>> from openjiuwen.dev_tools.tune.optimizer.instruction_optimizer import InstructionOptimizer
>>> from openjiuwen.dev_tools.tune.chat_agent import create_chat_agent_config, create_chat_agent
>>> from openjiuwen.core.single_agent.legacy import LLMCallConfig
>>>
>>> API_BASE = os.getenv("API_BASE", "your api base")
>>> API_KEY = os.getenv("API_KEY", "your api key")
>>> MODEL_NAME = os.getenv("MODEL_NAME", "")
>>> MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
>>>
>>> model_config = ModelRequestConfig(
...     model=MODEL_NAME,
... )
>>> model_client_config = ModelClientConfig(
...     client_provider=MODEL_PROVIDER,
...     api_base=API_BASE,
...     api_key=API_KEY,
...)
>>> # 1. Create ChatAgent object to be optimized
>>> config = create_chat_agent_config(
...     agent_id='chat_agent',
...     agent_version='1.0.0',
...     model=LLMCallConfig(
...         model=model_config,
...         model_client=model_client_config,
...         # Optimizable prompt content
...         system_prompt=[{"role": "system", "content": "你是一个信息抽取助手"}],
...     ),
...     description=''
... )
>>> agent = create_chat_agent(config, None)
>>>
>>> # 2. Create instruction optimizer, bind agent parameters
>>> optimizer = InstructionOptimizer(model_config, model_client_config, agent.get_llm_calls())
```

### bind_parameter

```python
bind_parameter(parameters: Dict[str, LLMCall])
```

Manually bind or update parameters to be optimized.

**Parameters**:

* **parameters** (Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#class-openjiuwencoreoperatorllm_callbasellmcall)]): Agent parameters to be optimized.

**Example**:

```python
>>> optimizer.bind_parameter(agent.get_llm_calls())
```

### backward:

```python
backward(evaluated_cases: List[EvaluatedCase])
```

Performs text gradient update based on evaluated cases.

**Parameters**:

- **evaluated_cases** (List[[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)]): Evaluated cases.

**Example**:

```python
>>> from openjiuwen.dev_tools.tune.base import Case, EvaluatedCase
>>> case = Case(inputs={"query": "strawberry有几个r"}, label={"output": "3"})
>>> 
>>> evaluated_cases = [
...     EvaluatedCase(case=case, answer={"output": "2"}, score=0.0, reason="strawberry有3个r，但模型回答为2，数量对应不上")
... ]
>>> optimizer.backward(evaluated_cases)
```

### update

```python
update()
```

Updates prompt content in Agent. This interface needs to be executed after calling backward.


**Example**:

```python
>>> optimizer.update()
此时agent的提示词已经完成更新，例如："你是一个聊天小助手，请仔细分析用户的问题，例如数学问题，并给出答案"
```

## class openjiuwen.dev_tools.tune.optimizer.example_optimizer.ExampleOptimizer

```python
openjiuwen.dev_tools.tune.optimizer.example_optimizer.ExampleOptimizer(model_config: ModelConfig, parameters: Optional[Dict[str, LLMCall]] = None, num_examples: int = 1)
```

`ExampleOptimizer` class is an Agent prompt example optimizer, adds most representative examples to prompts by inductively analyzing error cases, optimizes Agent performance on error instances.
**Parameters**:

* **model_config** ([ModelRequestConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelrequestconfig)): Large language model request configuration for executing optimization.
* **model_client_config** ([ModelClientConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelclientconfig)): Large language model service configuration for executing optimization.
* **parameters** (Optional[Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#openjiuwencoreoperatorllm_callbase)]], optional): Agent prompt optimization parameter list. Can be empty, bind parameters through bind_parameter interface during subsequent optimization. Default value is `None`.
* **num_examples** (int, optional): Number of examples allowed to be added in prompt, value range [0, 20], default value: `1`.

**Example**:

```python
>>> import os
>>> from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
>>> from openjiuwen.dev_tools.tune.optimizer.example_optimizer import ExampleOptimizer
>>> from openjiuwen.dev_tools.tune.chat_agent import create_chat_agent_config, create_chat_agent
>>> from openjiuwen.core.single_agent.legacy import LLMCallConfig
>>>
>>> API_BASE = os.getenv("API_BASE", "your api base")
>>> API_KEY = os.getenv("API_KEY", "your api key")
>>> MODEL_NAME = os.getenv("MODEL_NAME", "")
>>> MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
>>>
>>> model_config = ModelRequestConfig(
...     model=MODEL_NAME,
... )
>>> model_client_config = ModelClientConfig(
...     client_provider=MODEL_PROVIDER,
...     api_base=API_BASE,
...     api_key=API_KEY,
...)
>>> # 1. Create ChatAgent object to be optimized
>>> config = create_chat_agent_config(
...     agent_id='chat_agent',
...     agent_version='1.0.0',
...     model=LLMCallConfig(
...         model=model_config,
...         model_client=model_client_config,
...         # Optimizable prompt content
...         system_prompt=[{"role": "system", "content": "你是一个信息抽取助手"}],
...     ),
...     description=''
... )
>>> agent = create_chat_agent(config, None)
>>> 
>>> # 2. Create example optimizer, bind agent parameters
>>> optimizer = ExampleOptimizer(model_config, model_client_config, agent.get_llm_calls(), 1)
```

### bind_parameter

```python
bind_parameter(parameters: Dict[str, LLMCall])
```

Manually bind or update parameters to be optimized.

**Parameters**:

- **parameters** (Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#openjiuwencoreoperatorllm_callbase)]): Agent parameters to be optimized.

**Example**:

```python
>>> optimizer.bind_parameter(agent.get_llm_calls())
```

### backward

```python
backward(evaluated_cases: List[EvaluatedCase])
```

Performs text gradient update based on evaluated cases.

**Parameters**:

- **evaluated_cases** (List[[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)]): Evaluated cases.

**Example**:

```python
>>> from openjiuwen.dev_tools.tune.base import Case, EvaluatedCase
>>> case = Case(inputs={"query": "strawberry有几个r"}, label={"output": "3"})
>>> 
>>> evaluated_cases = [
...     EvaluatedCase(case=case, answer={"output": "2"}, score=0.0, reason="strawberry有3个r，但模型回答为2，数量对应不上")
... ]
>>> optimizer.backward(evaluated_cases)
```

### update

```python
update()
```
Updates prompt content in Agent. This interface needs to be executed after calling backward. 

**Example**:

```python
>>> optimizer.update()
此时agent的提示词已经完成更新，例如："你是一个聊天小助手。example-1:\n[question]: query: strawberry有几个r\n[expected answer]: output: 3"
```

## class openjiuwen.dev_tools.tune.optimizer.joint_optimizer.JointOptimizer

```python
openjiuwen.dev_tools.tune.optimizer.joint_optimizer.JointOptimizer(model_config: ModelConfig, parameters: Optional[Dict[str, LLMCall]] = None, num_examples: int = 1)
```

`JointOptimizer` class is an Agent prompt instruction and example joint optimizer, improves Agent performance by simultaneously optimizing prompt content and picking effective examples.
**Parameters**:

* **model_config** ([ModelRequestConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelrequestconfig)): Large language model request configuration for executing optimization.
* **model_client_config** ([ModelClientConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelclientconfig)): Large language model service configuration for executing optimization.
* **parameters** (Optional[Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#openjiuwencoreoperatorllm_callbase)]], optional): Agent prompt optimization parameter list. Can be empty, bind parameters through bind_parameter interface during subsequent optimization. Default value is `None`.
* **num_examples** (int, optional): Number of examples allowed to be added in prompt, value range [0, 20], default value: `1`.

**Example**:

```python
>>> import os
>>> from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
>>> from openjiuwen.dev_tools.tune.optimizer.joint_optimizer import JointOptimizer
>>> from openjiuwen.dev_tools.tune.chat_agent import create_chat_agent_config, create_chat_agent
>>> from openjiuwen.core.single_agent.legacy import LLMCallConfig
>>>
>>> API_BASE = os.getenv("API_BASE", "your api base")
>>> API_KEY = os.getenv("API_KEY", "your api key")
>>> MODEL_NAME = os.getenv("MODEL_NAME", "")
>>> MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
>>>
>>> model_config = ModelRequestConfig(
...     model=MODEL_NAME,
... )
>>> model_client_config = ModelClientConfig(
...     client_provider=MODEL_PROVIDER,
...     api_base=API_BASE,
...     api_key=API_KEY,
...)
>>> # 1. Create ChatAgent object to be optimized
>>> config = create_chat_agent_config(
...     agent_id='chat_agent',
...     agent_version='1.0.0',
...     model=LLMCallConfig(
...         model=model_config,
...         model_client=model_client_config,
...         # Optimizable prompt content
...         system_prompt=[{"role": "system", "content": "你是一个信息抽取助手"}],
...     ),
...     description=''
... )
>>> agent = create_chat_agent(config, None)
>>> 
>>> # 2. Create joint optimizer, bind agent parameters
>>> optimizer = JointOptimizer(model_config, model_client_config, agent.get_llm_calls(), 1)
```

### bind_parameter

```python
bind_parameter(parameters: Dict[str, LLMCall])
```

Manually bind or update parameters to be optimized.

**Parameters**:

- **parameters** (Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#openjiuwencoreoperatorllm_callbase)]): Agent parameters to be optimized.

**Example**:

```python
>>> optimizer.bind_parameter(agent.get_llm_calls())
```

### backward:

```python
backward(evaluated_cases: List[EvaluatedCase])
```

Performs text gradient update based on evaluated cases.

**Parameters**:

- **evaluated_cases** (List[[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)]): Evaluated cases.

**Example**:

```python
>>> case = Case(inputs={"query": "strawberry有几个r"}, label={"output": "3"})
>>> 
>>> evaluated_cases = [
>>>     EvaluatedCase(case=case, answer={"output": "2"}, score=0.0, reason="strawberry有3个r，但模型回答为2，数量对应不上")
>>> ]
>>> optimizer.backward(evaluated_cases)
```

### update

```python
update()
```

Updates prompt content in Agent. This interface needs to be executed after calling backward.

**Example**:

```python
>>> optimizer.update()
此时agent的提示词已经完成更新，例如："你是一个聊天小助手，请仔细分析用户的问题，例如数学问题，并给出答案。example-1:\n[question]: query: strawberry有几个r\n[expected answer]: output: 3"
```
