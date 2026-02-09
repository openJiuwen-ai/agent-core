# openjiuwen.dev_tools.tune.trainer.trainer

## class openjiuwen.dev_tools.tune.trainer.trainer.Trainer

```python
openjiuwen.dev_tools.tune.trainer.trainer.Trainer(optimizer: BaseOptimizer, evaluator: BaseEvaluator, **kwargs)
```

`Trainer` class provides unified training and evaluation interfaces for Agent optimization by flexibly binding optimizer and evaluator, implements one-click Agent capability leap.
**Parameters**:

- **optimizer** (BaseOptimizer): Optimizer bound to trainer, currently supports: [InstructionOptimizer](optimizer.md#class-openjiuwendev_toolstuneoptimizerinstruction_optimizerinstructionoptimizer), [ExampleOptimizer](optimizer.md#class-openjiuwendev_toolstuneoptimizerexample_optimizerexampleoptimizer), [JointOptimizer](optimizer.md#class-openjiuwendev_toolstuneoptimizerjoint_optimizerjointoptimizer).
- **evaluator** (BaseEvaluator): Evaluator bound to trainer, e.g., [DefaultEvaluator](evaluator.md#class-openjiuwendev_toolstuneevaluatorevaluatordefaultevaluator).
- **kwargs** (Dict, optional): Training optional hyperparameters.
  - **num_parallel** (int, optional): Training and evaluation concurrency count, value range [1, 20], default value: `1`.
  - **early_stop_score** (float, optional): Early stopping parameter, when Agent performance exceeds this score, will end iterative training, value range [0, 1], default value: `1`.

**Example**:

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
>>> # 2. Create instruction optimizer
>>> optimizer = InstructionOptimizer(model_config, model_client_config)
>>> # 3. Create evaluator, set scoring rules
>>> evaluator = DefaultEvaluator(
...     model_config,
...     model_client_config,
...     metric="两个回答需要一致。注意：可以忽略引号格式问题"
... )
>>> # 4. Create trainer
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

Trains Agent based on given training dataset, combining optimizer and evaluator.

**Parameters**:

- **agent** (Agent): Agent to be optimized.
- **train_cases** ([CaseLoader](dataset.md#class-openjiuwendev_toolstunedatasetcase_loadercaseloader)): Training data.
- **val_cases** ([CaseLoader](dataset.md#class-openjiuwendev_toolstunedatasetcase_loadercaseloader), optional): Validation data.
- **kwargs** (Dict, optional): Training optional hyperparameters.
  - **num_iterations** (int, optional): Training iteration rounds, [1, 20], default value: `3`.

**Returns**:
**Optional[Agent]**, optimized Agent object, may return `None` if errors occur during training process.

**Example**:

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
>>> # 2. Create instruction optimizer
>>> optimizer = InstructionOptimizer(model_config, model_client_config)
>>> # 3. Create evaluator, set scoring rules
>>> evaluator = DefaultEvaluator(
...     model_config,
...     model_client_config,
...     metric="两个回答需要一致。注意：可以忽略引号格式问题"
... )
>>> # 4. Create trainer
>>> trainer = Trainer(
...     optimizer=optimizer,
...     evaluator=evaluator,
...     num_parallel=5
... )
>>> # Create ChatAgent object to be optimized
>>> config = create_chat_agent_config(
...     agent_id='chat_agent',
...     agent_version='1.0.0',
...     model=LLMCallConfig(
...         model=model_config,
...         model_client=model_client_config,
...         # Optimizable prompt content
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

Evaluates Agent performance on dataset.

**Parameters**:

- **agent** (Agent): Agent to be evaluated.
- **cases** ([CaseLoader](dataset.md#class-openjiuwendev_toolstunedatasetcase_loadercaseloader)): Dataset to be evaluated.

**Returns**:
**Tuple[float, List[[EvaluatedCase](base.md#class-openjiuwendev_toolstunebaseevaluatedcase)]]**, tuple's first element is evaluation score, is a 0~1 score, higher score indicates better Agent performance; second element is evaluated dataset, containing specific score, model answer and scoring reason for each data item.

**Example**:

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

Predicts Agent's complete output results on given dataset

**Parameters**:

- **agent** (Agent): Agent to be tested.
- **cases** ([CaseLoader](dataset.md#class-openjiuwendev_toolstunedatasetcase_loadercaseloader)): Dataset to be tested.

**Returns**:
**List[Dict]**, Agent's answers based on each data's inputs.

**Example**:

```python
>>> case = Case(inputs={"query": "strawberry有几个r"}, label={"output": "3"})
>>> case_loader = CaseLoader(cases=[case])
>>> answers = trainer.predict(agent, case_loader)
>>> print(answers)
[{'output': '"strawberry" 这个单词中有 **3 个 r**。', 'tool_calls': []}]
```
