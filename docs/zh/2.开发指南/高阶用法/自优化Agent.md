当前在大模型应用上积累的badcase主要用于直接微调大模型进行干预，但该方法实现成本高，且case修复的周期取决于大模型微调的版本，无法做到即时干预，因此对Agent中提示词的自动调优需求十分急迫。

openJiuwen提供的对Agent提示词的自动调优算法，采用基于指令和示例的联合优化机制，在用户对已有应用场景中出现的错误案例进行标注后，同时优化指令和示例，提供自动调优后的提示词，从而有效修复错误案例，快速提升特定场景下的提示词效果。

以下将介绍自动调优Agent的整个流程，包括优化前准备、Agent性能评估、Agent优化。另外openJiuwen框架对训练过程进行了整合，用户可以直接通过Trainer类来统一执行优化任务

注意：当前只支持ChatAgent类型的Agent调优（单提示词调优）。

# 自优化Agent流程

以下将介绍自动调优Agent的完整流程，包括：

- **优化前准备**：准备好待优化的Agent和标注好的数据集。
- **评估优化前的Agent**：创建评估器，评估Agent在数据集上的表现。
- **优化Agent**：创建优化器优化Agent，评估优化后Agent的表现。

## 优化前准备
### 构建待调优的Agent

假设任务是从一段文本中抽取出人物的全名，首先需要构建一个信息抽取Agent，Agent配置如下：

```python
import os
from openjiuwen.dev_tools.tune import create_chat_agent_config, create_chat_agent
from openjiuwen.core.single_agent.legacy import LLMCallConfig
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

# 大模型配置
API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

# Agent系统提示词
INFORMATION_EXTRACTION_TEMPLATE = """
你是一个信息抽取助手，请从给定句子中提取所有的人名名称
输出格式为[人名1, 人名2, ...]的列表形式，不要输出其他内容
"""

# Agent模型配置
model_config = ModelRequestConfig(model=MODEL_NAME)
model_client_config = ModelClientConfig(
    client_provider=MODEL_PROVIDER,
    api_base=API_BASE,
    api_key=API_KEY,
)

# ChatAgent配置
config = create_chat_agent_config(
    agent_id='name_extractor',
    agent_version='1.0.0',
    description="",
    model=LLMCallConfig(
        model=model_config,
        model_client=model_client_config,
        system_prompt=[{"role": "system", "content": INFORMATION_EXTRACTION_TEMPLATE}],
    )
)
agent = create_chat_agent(config, None)
```

Agent的输入输出可以根据通用ChatAgent的输入输出类型明确为：

- **输入(Dict格式)**：`{"query": "待提取的文本内容"}`
- **输出(Dict格式)**：`{"output": "提取的人名列表"}`

### 构造数据集

首先，需要准备调优使用的Case（案例），其概念类似机器学习中的数据集，有inputs(输入)和标注好的参考答案label（标签）。
下面准备一个在信息抽取场景下的几条样例，作为任务的Case数据集。

```python
from openjiuwen.dev_tools.tune import Case

INFORMATION_EXTRACTION_CASES=[
    Case(
        inputs={"query": "潘之恒（约1536—1621）字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）"},
        label={"output": "[潘之恒]"}
    ),
    Case(
        inputs={"query": "高祖二十二子：窦皇后生建成（李建成）、太宗皇帝（李世民）、玄霸（李玄霸）、元吉（李元吉），万贵妃生智云（李智云），莫嫔生元景（李元景），孙嫔生元昌（李元昌））"},
        label={"output": "[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]"}
    ),
    Case(
        inputs={"query": "郭造卿（1532—1593），字建初，号海岳，福建福清县化南里人（今福清市人），郭遇卿之弟，郭造卿少年的时候就很有名气，曾游学吴越"},
        label={"output": "[郭造卿, 郭遇卿]"}
    ),
    Case(
        inputs={
            "query": "沈自邠，字茂仁，号几轩，又号茂秀，浙江秀水长溪（今嘉兴南汇）人"},
        label={"output": "[沈自邠]"}
    )
]
```

## 评估优化前的Agent
### 评估原始Agent的表现

准备好数据集和Agent后，可以先评估一下当前Agent的表现。例如，创建一个基于模型打分的评估器 `DefaultEvaluator`，来对数据集中每个Case的表现进行打分。

```python
import asyncio

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.dev_tools.tune import DefaultEvaluator
from openjiuwen.dev_tools.tune import create_chat_agent_config, create_chat_agent
from openjiuwen.core.single_agent.legacy import LLMCallConfig

# 创建评估器使用的模型配置
model_config = ModelRequestConfig(model=MODEL_NAME)
model_client_config = ModelClientConfig(
    client_provider=MODEL_PROVIDER,
    api_base=API_BASE,
    api_key=API_KEY,
)

# 创建评估器
evaluator = DefaultEvaluator(
    model_config,
    model_client_config,
    metric="两个回答需要一致，包括数量和名字。注意：但可以忽略对引号格式问题"
)
# 运行Agent，获取结果
async def forward(agent, cases):
    return [await agent.invoke(case.inputs) for case in cases]
predicts = asyncio.run(forward(agent, INFORMATION_EXTRACTION_CASES))
# 评估Agent的表现
results = evaluator.batch_evaluate(INFORMATION_EXTRACTION_CASES, predicts)
for eval_result in results:
    print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
          f"answer: {eval_result.answer}, label: {eval_result.case.label}")
```

**样例输出**

```
score: 1.0, reason: 模型回答和标准答案中的名字和数量一致，尽管引号格式有所不同，但根据规则可以忽略这种差异。, answer: {'output': "['潘之恒']", 'tool_calls': []}, label: {'output': '[潘之恒]'}
score: 0.0, reason: 模型回答中包含了标准答案中没有的别名或简称，如'建成', '太宗皇帝', '玄霸', '元吉', '智云', '元景', '元昌'，这与标准答案中的完整名字不一致。, answer: {'output': "['建成', '李建成', '太宗皇帝', '李世民', '玄霸', '李玄霸', '元吉', '李元吉', '智云', '李智云', '元景', '李元景', '元昌', '李元昌']", 'tool_calls': []}, label: {'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]'}
score: 0.0, reason: 模型回答中的'郭造卿'出现了两次，而标准答案中每个名字只出现一次，违反了自定义校验规则1，即回答需要一致，包括数量和名字。, answer: {'output': "['郭造卿', '郭遇卿', '郭造卿']", 'tool_calls': []}, label: {'output': '[郭造卿, 郭遇卿]'}
score: 1.0, reason: 模型回答和标准答案中的名字和数量一致，尽管引号格式有所不同，但根据规则可以忽略这种差异。, answer: {'output': "['沈自邠']", 'tool_calls': []}, label: {'output': '[沈自邠]'}
```

## 优化Agent
### 构建优化器

做好前置准备后，可以使用openJiuwen优化器，通过运行、评估、优化的流程，来优化和修正Agent中的提示词。
目前openJiuwen框架提供了三种类型的优化器：

- **InstructionOptimizer**：提示词指令优化器，基于数据集评估结果反馈修正提示词内容。
- **ExampleOptimizer**：提示词示例优化器，从数据集中提取最佳示例添加到提示词中。
- **JointOptimizer**：提示词指令/示例联合优化器，同时优化提示词内容并在提示词中添加示例。

这里以联合优化器来对Agent进行优化为例，JointOptimizer的创建、运行需要以下步骤：

1. **创建优化器**
   - 提供待优化的Agent参数信息：可通过Agent的`get_llm_calls`方法获取参数列表。
   - 配置优化模型信息：优化器使用的模型配置，推荐使用比较强大的模型，获取更好优化效果。
   - 配置few-shot示例信息：配置添加到提示词的示例数量。
2. **执行优化器**
   - 执行并获取agent在数据集上的输出。
   - 批量评估agent在各个用例上的评估分数
   - 调用backward接口对评估结果进行分析
   - 调用update接口对agent对应提示词进行更新

```python
from openjiuwen.dev_tools.tune.optimizer.joint_optimizer import JointOptimizer

# 创建优化器使用的模型配置
model_config = ModelRequestConfig(model=MODEL_NAME)
model_client_config = ModelClientConfig(
    client_provider=MODEL_PROVIDER,
    api_base=API_BASE,
    api_key=API_KEY,
)
# 创建优化器，绑定Agent参数，优化Agent
optimizer = JointOptimizer(
    model_config,
    model_client_config,
    parameters=agent.get_llm_calls(),
    num_examples=1
)
# 执行、评估Agent，优化器会采集运行过程中的必要信息
predicts = asyncio.run(forward(agent, INFORMATION_EXTRACTION_CASES))
results = evaluator.batch_evaluate(INFORMATION_EXTRACTION_CASES, predicts)
# 基于评估后的数据，对Agent优化进行分析
optimizer.backward(results)
# 更新优化后的提示词到Agent中
optimizer.update()
```

基于日志信息，可以看到提示词发生了修改：

```
2025-10-13 16:26:51 | common | base.py | 72 | update |  | INFO | [llm_call name]: llm_call [frozen]: False [system prompt]:  你是一个信息抽取助手，请从给定句子中提取所有的人名名称。人名定义为完整的姓名，不包括别名或简称。每个名字只应出现一次。输出格式为[人名1, 人名2, ...]的列表形式，不要输出其他内容。 example 1: [question]: query:高祖二十二子：窦皇后生建成（李建成）、太宗皇帝（李世民）、玄霸（李玄霸）、元吉（李元吉），万贵妃生智云（李智云），莫嫔生元景（李元景），孙嫔生元昌（李元昌）） [expected answer]: output:[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌] 
2025-10-13 16:26:51 | common | base.py | 75 | update |  | INFO | [llm_call name]: llm_call [frozen]: True [user prompt]: {{query}}
```

优化后的信息抽取系统提示词为：

```
你是一个信息抽取助手，请从给定句子中提取所有的人名名称。
人名定义为完整的姓名，不包括别名或简称。每个名字只应出现一次。输出格式为[人名1, 人名2, ...]的列表形式，不要输出其他内容。
 
example 1: 
[question]: query:高祖二十二子：窦皇后生建成（李建成）、太宗皇帝（李世民）、玄霸（李玄霸）、元吉（李元吉），万贵妃生智云（李智云），莫嫔生元景（李元景），孙嫔生元昌（李元昌））
[expected answer]: output:[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]
```

### 评估优化后Agent的表现

优化完成后，优化器会自动修改Agent中相关的提示词内容。使用优化后的Agent来看看效果吧。

```python
# 运行优化后的Agent，获取结果
predicts = asyncio.run(forward(agent, INFORMATION_EXTRACTION_CASES))
# 评估Agent的表现
results = evaluator.batch_evaluate(INFORMATION_EXTRACTION_CASES, predicts)
for eval_result in results:
    print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
          f"answer: {eval_result.answer}, label: {eval_result.case.label}")
```

**样例输出**

```
score: 1.0, reason: 模型回答和标准答案中的名字一致，尽管模型回答中名字被单引号包围，但根据规则可以忽略引号格式问题。, answer: {'output': "['潘之恒']", 'tool_calls': []}, label: {'output': '[潘之恒]'}
score: 1.0, reason: 模型回答和标准答案中的名字和数量完全一致，且没有工具调用，符合校验规则。, answer: {'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]', 'tool_calls': []}, label: {'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]'}
score: 1.0, reason: 模型回答和标准答案中的名字和数量一致，且没有工具调用，符合校验规则。, answer: {'output': '[郭造卿, 郭遇卿]', 'tool_calls': []}, label: {'output': '[郭造卿, 郭遇卿]'}
score: 1.0, reason: 模型回答和标准答案中的名字和数量一致，尽管引号格式有所不同，但根据规则可以忽略这种差异。, answer: {'output': "['沈自邠']", 'tool_calls': []}, label: {'output': '[沈自邠]'}
```

可以看到，优化后的Agent在该数据集上表现提升了！

# 通过训练器优化Agent

openJiuwen框架对运行、评估、优化过程进行了整合，提供训练类Trainer。用户可以通过Trainer类来一键式完成Agent训练任务的创建。

## 训练前准备

与上述自优化Agent流程一致，首先需要构建好待调优的Agent与数据集。参见：
[优化前准备](自优化Agent.md#优化前准备)
在此基础上，为了统一数据的获取，需要将数据加载到数据加载器`CaseLoader`中

```python
from openjiuwen.dev_tools.tune import CaseLoader

# 添加数据集到加载器中
case_loader = CaseLoader(cases=INFORMATION_EXTRACTION_CASES)
```

## 创建训练器

训练器需要绑定评估器和优化器，所以在实例化训练器之前，需要指定好评估器和优化器。
说明：用于创建训练器的优化器不需要与具体Agent参数绑定，因为在执行训练过程中会自动绑定。

训练器的创建步骤如下：
1. 创建训练器使用的优化器。
2. 创建训练器使用的评估器。
3. 创建训练器，绑定优化器和评估器，同时可以配置并行数、早停参数。

```python
from openjiuwen.dev_tools.tune.evaluator.evaluator import DefaultEvaluator
from openjiuwen.dev_tools.tune.optimizer.joint_optimizer import JointOptimizer
from openjiuwen.dev_tools.tune.trainer.trainer import Trainer

# 创建优化器
optimizer = JointOptimizer(
    model_config,
    model_client_config,
    num_examples=1
)
# 创建评估器
evaluator = DefaultEvaluator(
    model_config,
    model_client_config,
    metric="两个回答需要一致，包括数量和名字。注意：但可以忽略对引号格式问题"
)
# 创建训练器
trainer = Trainer(
    evaluator=evaluator,
    optimizer=optimizer,
    num_parallel=5
)
```

## 训练/优化

创建好训练器之后，只需要调用`train`接口。接口包含如下参数：

- **agent**：待优化的Agent。
- **train_cases**：训练使用的数据集加载器。
- **kwargs**：可选训练超参数。
  - **num_iterations**：训练的迭代轮次。默认值：3。

调用训练器调优Agent，会基于每轮最好的结果持续迭代，并应用最优的提示词结果到Agent中。

```python
optimized_agent = trainer.train(agent, case_loader, num_iterations=3)
```

训练日志：

```
score: 1.0, reason: 模型回答和标准答案中的名字和数量一致，尽管引号格式有所不同，但根据规则可以忽略这种差异。, answer: {'output': "['沈自邠']", 'tool_calls': []}, label: {'output': '[沈自邠]'}
2025-10-13 19:47:37 | common | trainer.py | 55 | train |  | INFO | train iteration: (baseline), score: 0.5
2025-10-13 19:48:03 | common | base.py | 72 | update |  | INFO | [llm_call name]: llm_call [frozen]: False [system prompt]:  你是一个信息抽取助手，请从给定句子中提取所有人的完整正式名字，不要包括简称或别名。请确保列表中的人名不重复。输出格式为[人名1, 人名2, ...]的列表形式，不要输出其他内容。  
2025-10-13 19:48:03 | common | base.py | 75 | update |  | INFO | [llm_call name]: llm_call [frozen]: True [user prompt]: {{query}}
2025-10-13 19:48:15 | common | trainer.py | 65 | train |  | INFO | train iteration: 1, score: 1.0
```

## 评估Agent的表现

Trainer集成了Agent批量运行、批量评估的功能，用户可以使用如下接口：
批量预测：

```python
case_loader = CaseLoader(cases=INFORMATION_EXTRACTION_CASES)
predicts = trainer.predict(agent, case_loader)
```

样例输出：

```
[{'output': '[潘之恒]', 'tool_calls': []}
{'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]', 'tool_calls': []}
{'output': '[郭造卿, 郭遇卿, 郭造卿]', 'tool_calls': []}
{'output': "['沈自邠']", 'tool_calls': []}]
```

批量评估

```python
case_loader = CaseLoader(cases=INFORMATION_EXTRACTION_CASES)
avg_score, evaluated_cases = trainer.evaluate(agent, case_loader)

# 打印评估细节
for eval_result in evaluated_cases:
    print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
          f"answer: {eval_result.answer}, label: {eval_result.case.label}")
```

样例输出：

```
score: 1.0, reason: 模型回答中的输出与标准答案完全一致，均包含[潘之恒]，且未涉及对话或工具调用，符合校验规则。, answer: {'output': '[潘之恒]', 'tool_calls': []}, label: {'output': '[潘之恒]'}
score: 0.0, reason: 模型回答中包含了妃嫔的名字（如窦皇后、万贵妃、莫嫔、孙嫔），而标准答案仅列出了高祖的儿子们的名字。因此，模型回答与标准答案在内容上不一致，多出了与问题无关的条目。, answer: {'output': '[窦皇后, 李建成, 太宗皇帝, 李世民, 李玄霸, 李元吉, 万贵妃, 李智云, 莫嫔, 李元景, 孙嫔, 李元昌]', 'tool_calls': []}, label: {'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]'}
score: 1.0, reason: 模型回答中的输出与标准答案完全一致，包括人物名字和数量。虽然模型回答中包含额外的 'tool_calls' 字段，但其内容为空，不影响输出结果的一致性。, answer: {'output': '[郭造卿, 郭遇卿]', 'tool_calls': []}, label: {'output': '[郭造卿, 郭遇卿]'}
score: 1.0, reason: 模型回答中的输出内容与标准答案完全一致，仅缺少了工具调用部分，但根据问题和校验规则，只需关注输出内容的一致性。, answer: {'output': '[沈自邠]', 'tool_calls': []}, label: {'output': '[沈自邠]'}
```
