# openjiuwen.dev_tools.tune.trainer.trainer

## class openjiuwen.dev_tools.tune.trainer.trainer.Trainer

```python
openjiuwen.dev_tools.tune.trainer.trainer.Trainer(optimizer: BaseOptimizer, evaluator: BaseEvaluator, **kwargs)
```

`Trainer`类通过灵活绑定优化器和评估器，为Agent优化提供统一的训练、评估接口，实现一键实现Agent能力跃升。
**参数**：

- **optimizer**(BaseOptimizer)：训练器绑定的优化器，目前支持：[InstructionOptimizer](optimizer.md#class-openjiuwendev_toolstuneoptimizerinstruction_optimizerinstructionoptimizer)、[ExampleOptimizer](optimizer.md#class-openjiuwendev_toolstuneoptimizerexample_optimizerexampleoptimizer)、[JointOptimizer](optimizer.md#class-openjiuwendev_toolstuneoptimizerjoint_optimizerjointoptimizer)。
- **evaluator**(BaseEvaluator)：训练器绑定的评估器，例如[DefaultEvaluator](evaluator.md#class-openjiuwendev_toolstuneevaluatorevaluatordefaultevaluator)。
- **kwargs**(Dict, 可选)：训练可选超参数。
  - **num_parallel**(int, 可选)：训练、评估并发数量，取值范围[1, 20]，默认值：`1`。
  - **early_stop_score**(float, 可选)：早停参数，当Agent表现超过该分数，会结束迭代训练，取值范围[0, 1]，默认值：`1`。

**样例**：

```python
>>> import os
>>> from openjiuwen.dev_tools.tune.optimizer.joint_optimizer import InstructionOptimizer
>>> from openjiuwen.dev_tools.tune.evaluator.evaluator import DefaultEvaluator
>>> from openjiuwen.dev_tools.tune.trainer.trainer import Trainer
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
>>> # 2. 创建指令优化器
>>> optimizer = InstructionOptimizer(model_config, model_client_config)
>>> # 3. 创建评估器，设置评分规则
>>> evaluator = DefaultEvaluator(
...     model_config,
...     model_client_config,
...     metric="两个回答需要一致。注意：可以忽略引号格式问题"
... )
>>> # 4. 创建训练器
>>> trainer = Trainer(
...     optimizer=optimizer,
...     evaluator=evaluator,
...     num_parallel=5
... )
```

### train

```python
train(agent: Agent, train_cases: CaseLoader, val_cases: Optional[CaseLoader] = None, **kwargs) -> Optional[Agent]
```

基于给定训练数据集，结合优化器和评估器训练Agent。

**参数**：

- **agent**(Agent)：待优化的Agent。
- **train_cases**([CaseLoader](dataset.md#class-openjiuwendev_toolstunedatasetcase_loadercaseloader))：训练数据。
- **val_cases**([CaseLoader](dataset.md#class-openjiuwendev_toolstunedatasetcase_loadercaseloader), 可选)：验证数据。
- **kwargs**(Dict, 可选)：训练可选超参数。
  - **num_iterations**(int, 可选)：训练迭代轮次，[1, 20]，默认值：`3`。

**返回**：
**Optional[Agent]**，优化后的Agent对象，如果训练过程中出现错误可能返回 `None`。

**样例**：

```python
>>> import os
>>> from openjiuwen.dev_tools.tune.optimizer.joint_optimizer import InstructionOptimizer
>>> from openjiuwen.dev_tools.tune.evaluator.evaluator import DefaultEvaluator
>>> from openjiuwen.dev_tools.tune.trainer.trainer import Trainer
>>> from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
>>> from openjiuwen.dev_tools.tune.chat_agent import create_chat_agent_config, create_chat_agent
>>> from openjiuwen.core.single_agent.legacy import LLMCallConfig
>>> from openjiuwen.dev_tools.tune import Case, EvaluatedCase, CaseLoader
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
>>> # 2. 创建指令优化器
>>> optimizer = InstructionOptimizer(model_config, model_client_config)
>>> # 3. 创建评估器，设置评分规则
>>> evaluator = DefaultEvaluator(
...     model_config,
...     model_client_config,
...     metric="两个回答需要一致。注意：可以忽略引号格式问题"
... )
>>> # 4. 创建训练器
>>> trainer = Trainer(
...     optimizer=optimizer,
...     evaluator=evaluator,
...     num_parallel=5
... )
>>> # 创建待优化的ChatAgent对象
>>> config = create_chat_agent_config(
...     agent_id='chat_agent',
...     agent_version='1.0.0',
...     model=LLMCallConfig(
...         model=model_config,
...         model_client=model_client_config,
...         # 可优化的提示词内容
...         system_prompt=[{"role": "system", "content": "你是一个聊天助手"}],
...     ),
...     description=''
... )
>>> agent = create_chat_agent(config, None)
>>> case = Case(inputs={"query": "strawberry有几个r"}, label={"output": "3"})
>>> case_loader = CaseLoader(cases=[case])
>>> agent = trainer.train(agent, case_loader)
```

### evaluate

```python
evaluate(agent: Agent, cases: CaseLoader) -> Tuple[float, List[EvaluatedCase]]
```

评估Agent在数据集上的表现。

**参数**：

- **agent**(Agent)：待评估的Agent。
- **cases**([CaseLoader](dataset.md#class-openjiuwendev_toolstunedatasetcase_loadercaseloader))：待评估的数据集。

**返回**：
**Tuple[float, List[[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)]]**，元组第一个元素为评估后的分数，为一个0~1的分值，分值越高代表Agent表现越好；第二个元素为评估后的数据集，包含每条数据的具体评分、模型回答和评分原因。

**样例**：

```python
>>> case = Case(inputs={"query": "strawberry有几个r"}, label={"output": "3"})
>>> case_loader = CaseLoader(cases=[case])
>>> score, evaluated_cases = trainer.evaluate(agent, case_loader)
>>> print(score)
>>> print(evaluated_cases)
1.0
case=Case(inputs={'query': 'strawberry有几个r'}, label={'output': '3'}, tools=None, case_id='case_0'), answer={'output': '"strawberry" 这个单词中有 **3 个 r**。', 'tool_calls': []}, score=1.0, reason="模型回答和标准答案的含义和结论一致，都指出单词 'strawberry' 中有 3 个字母 'r'。虽然模型回答的格式更详细，但结论与标准答案一致，符合校验要求。"
```

### predict

```python
predict(agent: Agent, cases: CaseLoader) -> List[Dict]
```

预测Agent在给定数据集上的完整输出结果

**参数**：

- **agent**(Agent)：待测试的Agent。
- **cases**([CaseLoader](dataset.md#class-openjiuwendev_toolstunedatasetcase_loadercaseloader))：待测试的数据集。

**返回**：
**List[Dict]**，Agent基于各个数据inputs的回答。

**样例**：

```python
>>> case = Case(inputs={"query": "strawberry有几个r"}, label={"output": "3"})
>>> case_loader = CaseLoader(cases=[case])
>>> answers = trainer.predict(agent, case_loader)
>>> print(answers)
[{'output': '"strawberry" 这个单词中有 **3 个 r**。', 'tool_calls': []}]
```
