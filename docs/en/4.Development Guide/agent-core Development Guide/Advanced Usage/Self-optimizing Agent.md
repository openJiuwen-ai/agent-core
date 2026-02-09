Currently, the accumulated bad cases in LLM applications are mainly used to directly fine-tune the LLM for intervention. However, this approach has high implementation costs, and the cycle for fixing cases depends on the version of the LLM fine-tuning, making instant intervention impossible. Therefore, there is an urgent need for automatic prompt tuning in Agents.

openJiuwen provides an automatic prompt tuning algorithm for Agents that uses a joint optimization mechanism based on instructions and examples. After users annotate errors encountered in existing application scenarios, the algorithm simultaneously optimizes instructions and examples, delivering tuned prompts that effectively fix errors and quickly improve prompt performance in specific scenarios.

The following introduces the entire workflow for automatically tuning Agents, including preparation, Agent performance evaluation, and Agent optimization. The openJiuwen framework also integrates the training process, allowing users to execute optimization tasks uniformly via the Trainer class.

Note: Currently only ChatAgent type tuning is supported (single prompt tuning).

# Self-Optimizing Agent Workflow

The following introduces the complete workflow for automatically tuning Agents, including:

- Preparation before optimization: Prepare the Agent to be tuned and the annotated dataset.
- Evaluate the Agent before optimization: Create an evaluator to assess Agent performance on the dataset.
- Optimize the Agent: Create an optimizer to tune the Agent and evaluate its performance after optimization.

## Preparation Before Optimization
### Build the Agent to Be Tuned

Suppose the task is to extract full names of people from a piece of text. First, you need to build an information extraction Agent with the following configuration:

```python
import os
from openjiuwen.dev_tools.tune import create_chat_agent_config, create_chat_agent
from openjiuwen.core.single_agent.legacy import LLMCallConfig
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

# LLM configuration
API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

# Agent system prompt
INFORMATION_EXTRACTION_TEMPLATE = """
You are an information extraction assistant. Please extract all personal names from the given sentence.
Output format should be a list: [Name1, Name2, ...]. Do not output anything else.
"""

# Agent model configuration
model_config = ModelRequestConfig(model=MODEL_NAME)
model_client_config = ModelClientConfig(
    client_provider=MODEL_PROVIDER,
    api_base=API_BASE,
    api_key=API_KEY,
)

# ChatAgent configuration
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

The Agent’s input and output can be defined according to the generic ChatAgent types:

- Input (Dict format): `{"query": "Text to extract names from"}`
- Output (Dict format): `{"output": "List of extracted names"}`

### Build the Dataset

First, you need to prepare the Cases used for tuning. The concept is similar to datasets in machine learning, with inputs and annotated labels (reference answers).
Below are sample cases for the information extraction scenario.

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

## Evaluate the Agent Before Optimization
### Evaluate the Performance of the Original Agent

Once the dataset and Agent are ready, you can first evaluate the current performance of the Agent. For example, create a model-based scorer `DefaultEvaluator` to score each Case in the dataset.

```python
import asyncio

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.dev_tools.tune import DefaultEvaluator
from openjiuwen.dev_tools.tune import create_chat_agent_config, create_chat_agent
from openjiuwen.core.single_agent.legacy import LLMCallConfig

# Create model configuration for the evaluator
model_config = ModelRequestConfig(model=MODEL_NAME)
model_client_config = ModelClientConfig(
    client_provider=MODEL_PROVIDER,
    api_base=API_BASE,
    api_key=API_KEY,
)

# Create the evaluator
evaluator = DefaultEvaluator(
    model_config,
    model_client_config,
    metric="The two answers must match, including counts and names. Note: quotation mark formatting can be ignored."
)
# Run the Agent to get results
async def forward(agent, cases):
    return [await agent.invoke(case.inputs) for case in cases]
predicts = asyncio.run(forward(agent, INFORMATION_EXTRACTION_CASES))
# Evaluate the Agent's performance
results = evaluator.batch_evaluate(INFORMATION_EXTRACTION_CASES, predicts)
for eval_result in results:
    print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
          f"answer: {eval_result.answer}, label: {eval_result.case.label}")
```

Sample Output

```
score: 1.0, reason: The model's answer matches the standard answer in both names and count. Although the quotation style differs, this difference can be ignored as per the rules., answer: {'output': "['潘之恒']", 'tool_calls': []}, label: {'output': '[潘之恒]'}
score: 0.0, reason: The model's answer includes aliases or short forms not present in the standard answer, such as '建成', '太宗皇帝', '玄霸', '元吉', '智云', '元景', '元昌', which do not match the full names in the standard answer., answer: {'output': "['建成', '李建成', '太宗皇帝', '李世民', '玄霸', '李玄霸', '元吉', '李元吉', '智云', '李智云', '元景', '李元景', '元昌', '李元昌']", 'tool_calls': []}, label: {'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]'}
score: 0.0, reason: '郭造卿' appears twice in the model's answer, while in the standard answer each name appears only once, violating Custom Check Rule 1, i.e., the answers must match in both names and count., answer: {'output': "['郭造卿', '郭遇卿', '郭造卿']", 'tool_calls': []}, label: {'output': '[郭造卿, 郭遇卿]'}
score: 1.0, reason: The model's answer matches the standard answer in both names and count. Although the quotation style differs, this difference can be ignored as per the rules., answer: {'output': "['沈自邠']", 'tool_calls': []}, label: {'output': '[沈自邠]'}
```

## Optimize the Agent
### Build the Optimizer

With the preparation done, you can use the openJiuwen optimizer to optimize and correct the Agent's prompt via a run–evaluate–optimize process.
The openJiuwen framework currently provides three types of optimizers:

- InstructionOptimizer: Instruction prompt optimizer that refines prompt content based on dataset evaluation feedback.
- ExampleOptimizer: Example prompt optimizer that extracts the best examples from the dataset and adds them to the prompt.
- JointOptimizer: Instruction/example joint optimizer that optimizes the prompt content and adds examples to the prompt simultaneously.

Here we use the joint optimizer to optimize the Agent. Creating and running the JointOptimizer involves:

1. Create the optimizer
   - Provide parameter information of the Agent to be optimized: retrieve the parameter list via the Agent’s `get_llm_calls` method.
   - Configure the optimization model: use a strong model for the optimizer to achieve better optimization results.
   - Configure few-shot examples: set the number of examples to add to the prompt.
2. Execute the optimizer
   - Within a with-statement, run and obtain the Agent's outputs on the dataset.
   - Batch evaluate the Agent's scores on each case.
   - Call the backward interface to analyze the evaluation results.
   - Call the update interface to update the Agent's prompt accordingly.

```python
from openjiuwen.dev_tools.tune.optimizer.joint_optimizer import JointOptimizer

# Create model configuration used by the optimizer
model_config = ModelRequestConfig(model=MODEL_NAME)
model_client_config = ModelClientConfig(
    client_provider=MODEL_PROVIDER,
    api_base=API_BASE,
    api_key=API_KEY,
)
# Create the optimizer, bind Agent parameters, and optimize the Agent
optimizer = JointOptimizer(
    model_config,
    model_client_config,
    parameters=agent.get_llm_calls(),
    num_examples=1
)
# Execute and evaluate the Agent; the optimizer collects necessary information during execution
predicts = asyncio.run(forward(agent, INFORMATION_EXTRACTION_CASES))
results = evaluator.batch_evaluate(INFORMATION_EXTRACTION_CASES, predicts)
# Analyze the Agent optimization based on evaluated data
optimizer.backward(results)
# Update the optimized prompt in the Agent
optimizer.update()
```

From the logs, you can see the prompt has been modified:

```
2025-10-13 16:26:51 | common | base.py | 72 | update |  | INFO | [llm_call name]: llm_call [frozen]: False [system prompt]:  You are an information extraction assistant. Please extract all personal names from the given sentence. A name is defined as a full formal name, excluding aliases or short forms. Each name should appear only once. Output format should be [Name1, Name2, ...]. Do not output anything else. example 1: [question]: query:高祖二十二子：窦皇后生建成（李建成）、太宗皇帝（李世民）、玄霸（李玄霸）、元吉（李元吉），万贵妃生智云（李智云），莫嫔生元景（李元景），孙嫔生元昌（李元昌）） [expected answer]: output:[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌] 
2025-10-13 16:26:51 | common | base.py | 75 | update |  | INFO | [llm_call name]: llm_call [frozen]: True [user prompt]: {{query}}
```

The optimized information extraction system prompt is:

```
You are an information extraction assistant. Please extract all personal names from the given sentence.
A name is defined as a full formal name, excluding aliases or short forms. Each name should appear only once. Output format should be [Name1, Name2, ...]. Do not output anything else.
 
example 1: 
[question]: query:高祖二十二子：窦皇后生建成（李建成）、太宗皇帝（李世民）、玄霸（李玄霸）、元吉（李元吉），万贵妃生智云（李智云），莫嫔生元景（李元景），孙嫔生元昌（李元昌））
[expected answer]: output:[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]
```

### Evaluate the Performance of the Optimized Agent

After optimization, the optimizer automatically modifies the relevant prompt content inside the Agent. Let's see how the optimized Agent performs.

```python
# Run the optimized Agent and get results
predicts = asyncio.run(forward(agent, INFORMATION_EXTRACTION_CASES))
# Evaluate the Agent's performance
results = evaluator.batch_evaluate(INFORMATION_EXTRACTION_CASES, predicts)
for eval_result in results:
    print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
          f"answer: {eval_result.answer}, label: {eval_result.case.label}")
```

Sample Output

```
score: 1.0, reason: The model's answer matches the standard answer in names. Although names are enclosed in single quotes, quotation formatting can be ignored according to the rules., answer: {'output': "['潘之恒']", 'tool_calls': []}, label: {'output': '[潘之恒]'}
score: 1.0, reason: The model's answer exactly matches the standard answer in both names and count, with no tool calls, complying with the validation rules., answer: {'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]', 'tool_calls': []}, label: {'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]'}
score: 1.0, reason: The model's answer matches the standard answer in both names and count, with no tool calls, complying with the validation rules., answer: {'output': '[郭造卿, 郭遇卿]', 'tool_calls': []}, label: {'output': '[郭造卿, 郭遇卿]'}
score: 1.0, reason: The model's answer matches the standard answer in both names and count. Although the quotation style differs, this difference can be ignored as per the rules., answer: {'output': "['沈自邠']", 'tool_calls': []}, label: {'output': '[沈自邠]'}
```

As you can see, the optimized Agent performs better on this dataset!

# Optimize the Agent via Trainer

The openJiuwen framework integrates the run, evaluation, and optimization processes and provides a Trainer class. Users can use the Trainer class to complete Agent training tasks with one click.

## Preparation for Training

As with the self-optimization workflow above, you first need to build the Agent to be tuned and the dataset. See:
[Preparation Before Optimization](Self-optimizing%20Agent.md#preparation-before-optimization)
Based on that, to unify data access, load the data into the CaseLoader.

```python
from openjiuwen.dev_tools.tune import CaseLoader

# Add the dataset to the loader
case_loader = CaseLoader(cases=INFORMATION_EXTRACTION_CASES)
```

## Create the Trainer

The Trainer needs to bind an evaluator and an optimizer, so before instantiating the Trainer, you need to specify the evaluator and optimizer.
Note: The optimizer used to create the Trainer does not need to be bound to specific Agent parameters, because the binding will happen automatically during training.

Steps to create the Trainer:
1. Create the optimizer used by the Trainer.
2. Create the evaluator used by the Trainer.
3. Create the Trainer, binding the optimizer and evaluator, and optionally configure the level of parallelism and early stopping parameters.

```python
from openjiuwen.dev_tools.tune.evaluator.evaluator import DefaultEvaluator
from openjiuwen.dev_tools.tune.optimizer.joint_optimizer import JointOptimizer
from openjiuwen.dev_tools.tune.trainer.trainer import Trainer

# Create the optimizer
optimizer = JointOptimizer(
    model_config,
    model_client_config,
    num_examples=1
)
# Create the evaluator
evaluator = DefaultEvaluator(
    model_config,
    model_client_config,
    metric="The two answers must match, including counts and names. Note: quotation mark formatting can be ignored."
)
# Create the Trainer
trainer = Trainer(
    evaluator=evaluator,
    optimizer=optimizer,
    num_parallel=5
)
```

## Training/Optimization

After creating the Trainer, simply call the `train` interface. Parameters include:

- agent: The Agent to be optimized.
- train_cases: The dataset loader used for training.
- kwargs: Optional training hyperparameters.
  - num_iterations: Number of training iterations. Default: 3.

Calling the Trainer to tune the Agent will iteratively improve the prompt based on the best result from each round and apply the optimal prompt back to the Agent.

```
optimized_agent = trainer.train(agent, case_loader, num_iterations=3)
```

Training Logs:

```
score: 1.0, reason: The model's answer matches the standard answer in both names and count. Although the quotation style differs, this difference can be ignored as per the rules., answer: {'output': "['沈自邠']", 'tool_calls': []}, label: {'output': '[沈自邠]'}
2025-10-13 19:47:37 | common | trainer.py | 55 | train |  | INFO | train iteration: (baseline), score: 0.5
2025-10-13 19:48:03 | common | base.py | 72 | update |  | INFO | [llm_call name]: llm_call [frozen]: False [system prompt]:  You are an information extraction assistant. Please extract all people’s complete formal names from the given sentence, excluding short forms or aliases. Ensure names in the list are not duplicated. Output format should be [Name1, Name2, ...]. Do not output anything else.  
2025-10-13 19:48:03 | common | base.py | 75 | update |  | INFO | [llm_call name]: llm_call [frozen]: True [user prompt]: {{query}}
2025-10-13 19:48:15 | common | trainer.py | 65 | train |  | INFO | train iteration: 1, score: 1.0
```

## Evaluate Agent Performance

The Trainer integrates batch run and batch evaluation functionalities. You can use the following interfaces:
Batch prediction:

```python
case_loader = CaseLoader(cases=INFORMATION_EXTRACTION_CASES)
predicts = trainer.predict(agent, case_loader)
```

Sample Output:

```
[{'output': '[潘之恒]', 'tool_calls': []}
{'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]', 'tool_calls': []}
{'output': '[郭造卿, 郭遇卿, 郭造卿]', 'tool_calls': []}
{'output': "['沈自邠']", 'tool_calls': []}]
```

Batch evaluation

```python
case_loader = CaseLoader(cases=INFORMATION_EXTRACTION_CASES)
avg_score, evaluated_cases = trainer.evaluate(agent, case_loader)

# Print evaluation details
for eval_result in evaluated_cases:
    print(f"score: {eval_result.score}, reason: {eval_result.reason}, "
          f"answer: {eval_result.answer}, label: {eval_result.case.label}")
```

Sample Output:

```
score: 1.0, reason: The model output exactly matches the standard answer, both containing [潘之恒], with no dialogue or tool calls involved, complying with the validation rules., answer: {'output': '[潘之恒]', 'tool_calls': []}, label: {'output': '[潘之恒]'}
score: 0.0, reason: The model's answer includes names of consorts (e.g., 窦皇后, 万贵妃, 莫嫔, 孙嫔), while the standard answer lists only the names of Emperor Gaozu’s sons. Therefore, the model's answer is inconsistent with the standard answer and includes irrelevant entries., answer: {'output': '[窦皇后, 李建成, 太宗皇帝, 李世民, 李玄霸, 李元吉, 万贵妃, 李智云, 莫嫔, 李元景, 孙嫔, 李元昌]', 'tool_calls': []}, label: {'output': '[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]'}
score: 1.0, reason: The model output exactly matches the standard answer, including both names and count. Although the model's answer contains an extra 'tool_calls' field, it is empty and does not affect output consistency., answer: {'output': '[郭造卿, 郭遇卿]', 'tool_calls': []}, label: {'output': '[郭造卿, 郭遇卿]'}
score: 1.0, reason: The model output exactly matches the standard answer. Although tool calls are missing, according to the problem and validation rules, only the consistency of the output content matters., answer: {'output': '[沈自邠]', 'tool_calls': []}, label: {'output': '[沈自邠]'}
```