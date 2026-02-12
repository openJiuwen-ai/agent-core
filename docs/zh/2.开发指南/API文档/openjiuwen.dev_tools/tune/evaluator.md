# openjiuwen.dev_tools.tune.evaluator.evaluator

## class openjiuwen.dev_tools.tune.evaluator.evaluator.DefaultEvaluator

```python
openjiuwen.dev_tools.tune.evaluator.evaluator.DefaultEvaluator(model_config: ModelRequestConfig, model_client_config: ModelClientConfig, metric: str = "")
```

`DefaultEvaluator`是默认的评估器类，可基于给定评价规则，利用大模型对Case的标签和Agent实际输出进行比对评估，输出评分以及评分依据。
**参数**：

* **model_config**([ModelRequestConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelrequestconfig))：用于执行评估的大模型请求配置。
* **model_client_config**([ModelClientConfig](../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmschemaconfigmodelclientconfig))：用于执行评估的大模型服务配置。
* **metric**(str, 可选)：用户基于自己的场景，自定义评价标准的描述文本，例如："回答不得超过10个字，否则一律给0分"。默认值：`""`。metric字段为空时会基于标签和实际输出的语义相似性给出评分，相同或相似得1分；不同得0分。

### evaluate

```python
evaluate(case: Case, predict: Dict[str, Any]) -> EvaluatedCase
```

评估单个Case。

**参数**：

* **case**([Case](base.md#class-openjiuwendev_toolstunebasecase))：测试用例对象，包含输入数据、期望输出、工具。
* **predict**(Dict[str, Any])：模型的预测结果，为字典格式，如 {"answer": "太阳是恒星"}。

**返回**：

**[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)**，评估结果对象，包含评分和评分原因。

**样例**：

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
>>>  # 2. 构造测试用例
>>> case = Case(
... 		inputs={"query":"潘之恒（约1536—1621）字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）"},
...        	label={"output": "[潘之恒]"}
...  	)
>>>
>>> # 3. 获取agent执行的预测结果
>>> async def forward(agent, cases):
...     return await agent.invoke(case.inputs)
>>> predict = asyncio.run(forward(agent, case))
>>>
>>> # 4. 创建指令优化器，绑定agent参数
>>> optimizer = JointOptimizer(model_config, model_client_config, agent.get_llm_calls())
>>>
>>> # 5. 创建评估器
>>> evaluator = DefaultEvaluator(
...             model_config,
...             model_client_config,
...             metric="如果是非工具调用，两个回答需要完全一致，包括数量和名字。但可以忽略对单引号、双引号>>> 格式问题以及tool_calls字段"
...                    "如果是工具调用，则只需要关注tool_calls字段中插件名称和插件参数是否一致，忽略文本内容"
...        	 )
>>>
>>> # 6. 评估单个case的效果
>>> eval_result= evaluator.evaluate(case, predict)
>>>
>>> # 7. 打印评估细节
>>> print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
...       f"answer: {eval_result.answer}, label: {eval_result.case.label}")
>>>
>>> # 8. 执行提示词优化
>>> optimizer.backward([eval_result])
>>> optimizer.update()
>>>
>>> # 9. 再次获取agent执行的预测结果
>>> predict = asyncio.run(forward(agent, case))
>>>
>>> # 10. 评估单个case的效果
>>> eval_result= evaluator.evaluate(case, predict)
>>>
>>> # 11. 打印评估细节
>>> print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
...       f"answer: {eval_result.answer}, label: {eval_result.case.label}")
```

### batch_evaluate

```python
batch_evaluate(cases: List[Case] | CaseLoader, predicts: List[Dict[str, Any]], **kwargs) -> List[[EvaluatedCase]]
```

批量评估Case列表。

**参数**：

- **cases**(List[[Case](base.md#class-openjiuwendev_toolstunebasecase)] | [CaseLoader](dataset.md#class-openjiuwendev_toolstunedatasetcase_loadercaseloader))：一组待评估的原始测试用例，每个`Case`应包含输入、期望输出、工具定义等信息。
- **predicts**(List[Dict[str, Any]])：与`cases`一一对应的模型预测结果列表，每个字典应包含模型输出的结构化内容（如`output`,`reasoning`等）。
- **kwargs**(Dict[str, Any])：可选参数，支持以下键值：
  - **num_parallel**(int)：并行评估的线程数，取值范围[1, 20], 默认`1`。实际并行数不会超过`cases`的数量。

**返回**：

**List[[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)]**，一个包含评估结果的列表，每个元素为一个`EvaluatedCase`实例，封装了原始用例、模型输出、评估得分及推理说明。

**样例**：

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
>>>  # 2. 构造测试用例
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
>>> # 3. 获取agent执行的预测结果
>>> # 异步前向推理函数
>>> async def forward(agent, cases):
...     return [await agent.invoke(case.inputs) for case in cases]
>>> predicts = asyncio.run(forward(agent, cases))
>>> 
>>> # 5. 创建评估器
>>> evaluator = DefaultEvaluator(
...             model_config,
...             model_client_config,
...             metric="如果是非工具调用，两个回答需要完全一致，包括数量和名字。但可以忽略对单引号、双引号>>> 格式问题以及tool_calls字段"
...                    "如果是工具调用，则只需要关注tool_calls字段中插件名称和插件参数是否一致，忽略文本内容"
...        	 )
>>> 
>>> # 6. 执行初始评估
>>> evaluated_cases = evaluator.batch_evaluate(cases, predicts)
>>> 
>>> # 7. 打印评估细节
>>> for eval_result in evaluated_cases:
...     print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
...           f"answer: {eval_result.answer}, label: {eval_result.case.label}")
score: 1.0, reason: 模型回答和标准答案中的名字和数量一致，尽管引号格式有所不同，但根据规则可以忽略这种差异。, answer: {'output': "['潘之恒']", 'tool_calls': []}, label: {'output': '[潘之恒]'}
score: 0.0, reason: 模型回答中包含了标准答案中没有的别名或简称，如'建成', '太宗皇帝', '玄霸', '元吉', '智云', '元景', '元昌'，这与标准答案中的完整名字不一致。, answer: {'output': "['建成', '李建成', '太宗皇帝', '李世民', '玄霸', '李玄霸', '元吉', '李元吉', '智云', '李智云', '元景', '李元景', '元昌', '李元昌']", 'tool_calls': []}, label: {'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]'}
```
