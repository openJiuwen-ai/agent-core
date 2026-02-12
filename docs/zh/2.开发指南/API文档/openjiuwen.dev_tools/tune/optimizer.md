# openjiuwen.dev_tools.tune.optimizer

## class openjiuwen.dev_tools.tune.optimizer.instruction_optimizer.InstructionOptimizer

```python
openjiuwen.dev_tools.tune.optimizer.instruction_optimizer.InstructionOptimizer(model_config: ModelRequestConfig, model_client_config: ModelClientConfig, parameters: Optional[Dict[str, LLMCall]] = None, **kwargs)
```

`InstructionOptimizer`类为Agent提示词指令优化器，通过分析Agent输入输出的错误用例生成的反馈，修正内部提示词模板，优化Agent在错误用例上的准确率。
**参数**：

* **model_config**([ModelRequestConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelrequestconfig))：用于执行优化的大模型请求配置。
* **model_client_config**([ModelClientConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelclientconfig))：用于执行优化的大模型服务配置。
* **parameters**(Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#openjiuwencoreoperatorllm_callbase)], 可选)：Agent的提示词优化参数列表，可以为空。如果为空，需要在后续优化时通过bind_parameter接口绑定参数。默认值：`None`。参数可以通过get_llm_calls接口获取（当前仅ChatAgent支持）。

**样例**：

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
>>> # 1. 创建待优化的ChatAgent对象
>>> config = create_chat_agent_config(
...     agent_id='chat_agent',
...     agent_version='1.0.0',
...     model=LLMCallConfig(
...         model=model_config,
...         model_client=model_client_config,
...         # 可优化的提示词内容
...         system_prompt=[{"role": "system", "content": "你是一个信息抽取助手"}],
...     ),
...     description=''
... )
>>> agent = create_chat_agent(config, None)
>>>
>>> # 2. 创建指令优化器，绑定agent参数
>>> optimizer = InstructionOptimizer(model_config, model_client_config, agent.get_llm_calls())
```

### bind_parameter

```python
bind_parameter(parameters: Dict[str, LLMCall])
```

手动绑定或更新待优化参数。

**参数**：

* **parameters**(Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#class-openjiuwencoreoperatorllm_callbasellmcall)])：待优化的Agent参数。

**样例**：

```python
>>> optimizer.bind_parameter(agent.get_llm_calls())
```

### backward:

```python
backward(evaluated_cases: List[EvaluatedCase])
```

基于评估后的用例进行文本梯度更新。

**参数**：

- **evaluated_cases**(List[[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)])：评估后的用例。

**样例**：

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

更新Agent中的提示词内容。该接口需要在调用backward之后执行。


**样例**：

```python
>>> optimizer.update()
此时agent的提示词已经完成更新，例如："你是一个聊天小助手，请仔细分析用户的问题，例如数学问题，并给出答案"
```

## class openjiuwen.dev_tools.tune.optimizer.example_optimizer.ExampleOptimizer

```python
openjiuwen.dev_tools.tune.optimizer.example_optimizer.ExampleOptimizer(model_config: ModelConfig, parameters: Optional[Dict[str, LLMCall]] = None, num_examples: int = 1)
```

`ExampleOptimizer`类为Agent提示词示例优化器，通过归纳分析错误用例，添加最具代表性的示例到提示词中，优化Agent在错误实例上的表现。
**参数**：

* **model_config**([ModelRequestConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelrequestconfig))：用于执行优化的大模型请求配置。
* **model_client_config**([ModelClientConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelclientconfig))：用于执行优化的大模型服务配置。
* **parameters**(Optional[Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#openjiuwencoreoperatorllm_callbase)]], 可选)：Agent的提示词优化参数列表。可以为空，后续优化时通过bind_parameter接口绑定参数。默认值为`None`。
* **num_examples**(int, 可选)：在提示词中允许添加的示例数量，取值范围[0, 20]，默认值：`1`。

**样例**：

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
>>> # 1. 创建待优化的ChatAgent对象
>>> config = create_chat_agent_config(
...     agent_id='chat_agent',
...     agent_version='1.0.0',
...     model=LLMCallConfig(
...         model=model_config,
...         model_client=model_client_config,
...         # 可优化的提示词内容
...         system_prompt=[{"role": "system", "content": "你是一个信息抽取助手"}],
...     ),
...     description=''
... )
>>> agent = create_chat_agent(config, None)
>>> 
>>> # 2. 创建示例优化器，绑定agent参数
>>> optimizer = ExampleOptimizer(model_config, model_client_config, agent.get_llm_calls(), 1)
```

### bind_parameter

```python
bind_parameter(parameters: Dict[str, LLMCall])
```

手动绑定或更新待优化参数。

**参数**：

- **parameters**(Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#openjiuwencoreoperatorllm_callbase)])：待优化的Agent参数。

**样例**：

```python
>>> optimizer.bind_parameter(agent.get_llm_calls())
```

### backward

```python
backward(evaluated_cases: List[EvaluatedCase])
```

基于评估后的用例进行文本梯度更新。

**参数**：

- **evaluated_cases**(List[[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)])：评估后的用例。

**样例**：

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
更新Agent中的提示词内容。该接口需要在调用backward之后执行。 

**样例**：

```python
>>> optimizer.update()
此时agent的提示词已经完成更新，例如："你是一个聊天小助手。example-1:\n[question]: query: strawberry有几个r\n[expected answer]: output: 3"
```

## class openjiuwen.dev_tools.tune.optimizer.joint_optimizer.JointOptimizer

```python
openjiuwen.dev_tools.tune.optimizer.joint_optimizer.JointOptimizer(model_config: ModelConfig, parameters: Optional[Dict[str, LLMCall]] = None, num_examples: int = 1)
```

`JointOptimizer`类为Agent提示词指令和示例联合优化器，通过同时优化提示词内容和筛选有效示例，提升Agent的表现。
**参数**：

* **model_config**([ModelRequestConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelrequestconfig))：用于执行优化的大模型请求配置。
* **model_client_config**([ModelClientConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelclientconfig))：用于执行优化的大模型服务配置。
* **parameters**(Optional[Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#openjiuwencoreoperatorllm_callbase)]], 可选)：Agent的提示词优化参数列表。可以为空，后续优化时通过bind_parameter接口绑定参数。默认值为`None`。
* **num_examples**(int, 可选)：在提示词中允许添加的示例数量，取值范围[0, 20]，默认值：`1`。

**样例**：

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
>>> # 1. 创建待优化的ChatAgent对象
>>> config = create_chat_agent_config(
...     agent_id='chat_agent',
...     agent_version='1.0.0',
...     model=LLMCallConfig(
...         model=model_config,
...         model_client=model_client_config,
...         # 可优化的提示词内容
...         system_prompt=[{"role": "system", "content": "你是一个信息抽取助手"}],
...     ),
...     description=''
... )
>>> agent = create_chat_agent(config, None)
>>> 
>>> # 2. 创建联合优化器，绑定agent参数
>>> optimizer = JointOptimizer(model_config, model_client_config, agent.get_llm_calls(), 1)
```

### bind_parameter

```python
bind_parameter(parameters: Dict[str, LLMCall])
```

手动绑定或更新待优化参数。

**参数**：

- **parameters**(Dict[str, [LLMCall](../../openjiuwen.core/operator/llm_call/base.md#openjiuwencoreoperatorllm_callbase)])：待优化的Agent参数。

**样例**：

```python
>>> optimizer.bind_parameter(agent.get_llm_calls())
```

### backward:

```python
backward(evaluated_cases: List[EvaluatedCase])
```

基于评估后的用例进行文本梯度更新。

**参数**：

- **evaluated_cases**(List[[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)])：评估后的用例。

**样例**：

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

更新Agent中的提示词内容。该接口需要在调用backward之后执行。

**样例**：

```python
>>> optimizer.update()
此时agent的提示词已经完成更新，例如："你是一个聊天小助手，请仔细分析用户的问题，例如数学问题，并给出答案。example-1:\n[question]: query: strawberry有几个r\n[expected answer]: output: 3"
```
