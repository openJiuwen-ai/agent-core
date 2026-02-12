# openjiuwen.dev_tools.tune.evaluator.evaluator

## class openjiuwen.dev_tools.tune.evaluator.evaluator.DefaultEvaluator

```python
openjiuwen.dev_tools.tune.evaluator.evaluator.DefaultEvaluator(model_config: ModelRequestConfig, model_client_config: ModelClientConfig, metric: str = "")
```

`DefaultEvaluator` is the default evaluator class, can compare and evaluate Case labels and Agent actual output using large language models based on given evaluation rules, outputs score and scoring basis.
**Parameters**:

* **model_config** ([ModelRequestConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelrequestconfig)): Large language model request configuration for executing evaluation.
* **model_client_config** ([ModelClientConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelclientconfig)): Large language model service configuration for executing evaluation.
* **metric** (str, optional): User-defined evaluation standard description text based on their own scenarios, e.g., "Answers must not exceed 10 characters, otherwise give 0 points". Default value: `""`. When metric field is empty, will give score based on semantic similarity between label and actual output, same or similar gets 1 point; different gets 0 points.

### evaluate

```python
evaluate(case: Case, predict: Dict[str, Any]) -> EvaluatedCase
```

Evaluates a single Case.

**Parameters**:

* **case** ([Case](base.md#class-openjiuwendev_toolstunebasecase)): Test case object, containing input data, expected output, tools.
* **predict** (Dict[str, Any]): Model's prediction result, dictionary format, e.g., {"answer": "太阳是恒星"}.

**Returns**:

**[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)**, evaluation result object, containing score and scoring reason.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.dev_tools.tune.chat_agent import create_chat_agent_config, create_chat_agent
>>> from openjiuwen.core.single_agent.legacy import LLMCallConfig
>>> from openjiuwen.dev_tools.tune.base import Case
>>> from openjiuwen.dev_tools.tune.optimizer.joint_optimizer import JointOptimizer
>>> from openjiuwen.dev_tools.tune.evaluator.evaluator import DefaultEvaluator
>>> from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
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
>>>  # 2. Construct test case
>>> case = Case(
... 		inputs={"query":"潘之恒（约1536—1621）字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）"},
...        	label={"output": "[潘之恒]"}
...  	)
>>>
>>> # 3. Get agent execution prediction result
>>> async def forward(agent, cases):
...     return await agent.invoke(case.inputs)
>>> predict = asyncio.run(forward(agent, case))
>>>
>>> # 4. Create instruction optimizer, bind agent parameters
>>> optimizer = JointOptimizer(model_config, model_client_config, agent.get_llm_calls())
>>>
>>> # 5. Create evaluator
>>> evaluator = DefaultEvaluator(
...             model_config,
...             model_client_config,
...             metric="如果是非工具调用，两个回答需要完全一致，包括数量和名字。但可以忽略对单引号、双引号>>> 格式问题以及tool_calls字段"
...                    "如果是工具调用，则只需要关注tool_calls字段中插件名称和插件参数是否一致，忽略文本内容"
...        	 )
>>>
>>> # 6. Evaluate single case effect
>>> eval_result= evaluator.evaluate(case, predict)
>>>
>>> # 7. Print evaluation details
>>> print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
...       f"answer: {eval_result.answer}, label: {eval_result.case.label}")
>>>
>>> # 8. Execute prompt optimization
>>> optimizer.backward([eval_result])
>>> optimizer.update()
>>>
>>> # 9. Get agent execution prediction result again
>>> predict = asyncio.run(forward(agent, case))
>>>
>>> # 10. Evaluate single case effect
>>> eval_result= evaluator.evaluate(case, predict)
>>>
>>> # 11. Print evaluation details
>>> print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
...       f"answer: {eval_result.answer}, label: {eval_result.case.label}")
```

### batch_evaluate

```python
batch_evaluate(cases: List[Case] | CaseLoader, predicts: List[Dict[str, Any]], **kwargs) -> List[[EvaluatedCase]
```

Batch evaluates Case list.

**Parameters**:

- **cases** (List[[Case](base.md#class-openjiuwendev_toolstunebasecase)] | [CaseLoader](dataset.md#class-openjiuwendev_toolstunedatasetcase_loadercaseloader)): A set of original test cases to be evaluated, each `Case` should contain input, expected output, tool definitions and other information.
- **predicts** (List[Dict[str, Any]]): Model prediction result list corresponding one-to-one with `cases`, each dictionary should contain structured content of model output (e.g., `output`, `reasoning`, etc.).
- **kwargs** (Dict[str, Any]): Optional parameters, supports the following key-value pairs:
  - **num_parallel** (int): Number of parallel evaluation threads, value range [1, 20], default `1`. Actual parallelism will not exceed number of `cases`.

**Returns**:

**List[[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)]**, a list containing evaluation results, each element is an `EvaluatedCase` instance, encapsulating original case, model output, evaluation score and reasoning description.

**Example**:

```python
>>> import os
>>> import asyncio
>>> from openjiuwen.dev_tools.tune.chat_agent import create_chat_agent_config, create_chat_agent
>>> from openjiuwen.core.single_agent.legacy import LLMCallConfig
>>> from openjiuwen.dev_tools.tune.base import Case
>>> from openjiuwen.dev_tools.tune.evaluator.evaluator import DefaultEvaluator
>>> from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
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
>>>  # 2. Construct test cases
>>> cases = [
... 	Case(
... 		inputs={"query":"潘之恒（约1536—1621）字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）"},
...        	label={"output": "[潘之恒]"}
...  	),
... 	Case(
...        	 inputs={"query": "高祖二十二子：窦皇后生建成（李建成）、太宗皇帝（李世民）、玄霸（李玄霸）、元吉（李元吉），万贵妃生智云（李智云），莫嫔生元景（李元景），孙嫔生元昌（李元昌））"},
...        	 label={"output": "[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]"}
... 	 )
... ]	
>>> 
>>> # 3. Get agent execution prediction results
>>> # Async forward inference function
>>> async def forward(agent, cases):
...     return [await agent.invoke(case.inputs) for case in cases]
>>> predicts = asyncio.run(forward(agent, cases))
>>> 
>>> # 5. Create evaluator
>>> evaluator = DefaultEvaluator(
...             model_config,
...             model_client_config,
...             metric="如果是非工具调用，两个回答需要完全一致，包括数量和名字。但可以忽略对单引号、双引号>>> 格式问题以及tool_calls字段"
...                    "如果是工具调用，则只需要关注tool_calls字段中插件名称和插件参数是否一致，忽略文本内容"
...        	 )
>>> 
>>> # 6. Execute initial evaluation
>>> evaluated_cases = evaluator.batch_evaluate(cases, predicts)
>>> 
>>> # 7. Print evaluation details
>>> for eval_result in evaluated_cases:
...     print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
...           f"answer: {eval_result.answer}, label: {eval_result.case.label}")
score: 1.0, reason: 模型回答和标准答案中的名字和数量一致，尽管引号格式有所不同，但根据规则可以忽略这种差异。, answer: {'output': "['潘之恒']", 'tool_calls': []}, label: {'output': '[潘之恒]'}
score: 0.0, reason: 模型回答中包含了标准答案中没有的别名或简称，如'建成', '太宗皇帝', '玄霸', '元吉', '智云', '元景', '元昌'，这与标准答案中的完整名字不一致。, answer: {'output': "['建成', '李建成', '太宗皇帝', '李世民', '玄霸', '李玄霸', '元吉', '李元吉', '智云', '李智云', '元景', '李元景', '元昌', '李元昌']", 'tool_calls': []}, label: {'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]'}
```
