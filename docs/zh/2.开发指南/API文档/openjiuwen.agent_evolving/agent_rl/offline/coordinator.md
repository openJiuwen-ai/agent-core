# openjiuwen.agent_evolving.agent_rl.coordinator

## class openjiuwen.agent_evolving.agent_rl.schemas.Rollout

```python
class openjiuwen.agent_evolving.agent_rl.schemas.Rollout(turn_id: Optional[int] = None, input_prompt: Optional[Dict[str, Any]] = None, output_response: Optional[Dict[str, Any]] = None, llm_config: Optional[Dict[str, Any]] = None)
```

单轮对话 rollout。

字段约定：
- input_prompt["message"]：输入消息列表（OpenAI 消息格式）
- input_prompt["tools"]：工具定义列表
- output_response：LLM 输出消息（content 或 tool_calls）

**参数**：

* **turn_id**(Optional[int]，可选)：轮次 ID。默认值：`None`。
* **input_prompt**(Optional[Dict[str, Any]]，可选)：输入提示词。默认值：`None`。
* **output_response**(Optional[Dict[str, Any]]，可选)：LLM 输出响应。默认值：`None`。
* **llm_config**(Optional[Dict[str, Any]]，可选)：LLM 配置。默认值：`None`。

## class openjiuwen.agent_evolving.agent_rl.schemas.RolloutMessage

```python
class openjiuwen.agent_evolving.agent_rl.schemas.RolloutMessage(task_id: Optional[str] = None, origin_task_id: Optional[str] = None, rollout_id: Optional[str] = None, start_time: Optional[str] = None, end_time: Optional[str] = None, rollout_info: List[Rollout] = [], reward_list: List[float] = [], global_reward: Optional[float] = None, turn_count: int = 0, round_num: Optional[int] = None)
```

完整的任务执行结果，聚合多轮对话和关联的奖励。

**参数**：

* **task_id**(Optional[str]，可选)：任务 ID。默认值：`None`。
* **origin_task_id**(Optional[str]，可选)：原始任务 ID。默认值：`None`。
* **rollout_id**(Optional[str]，可选)：Rollout ID。默认值：`None`。
* **start_time**(Optional[str]，可选)：开始时间（ISO 格式）。默认值：`None`。
* **end_time**(Optional[str]，可选)：结束时间（ISO 格式）。默认值：`None`。
* **rollout_info**(List[Rollout]，可选)：Rollout 信息列表。默认值：`[]`。
* **reward_list**(List[float]，可选)：奖励列表。默认值：`[]`。
* **global_reward**(Optional[float]，可选)：全局奖励。默认值：`None`。
* **turn_count**(int，可选)：对话轮次计数。默认值：`0`。
* **round_num**(Optional[int]，可选)：Rollout 轮数。默认值：`None`。

## class openjiuwen.agent_evolving.agent_rl.schemas.RLTask

```python
class openjiuwen.agent_evolving.agent_rl.schemas.RLTask(task_id: str, origin_task_id: str, task_sample: Dict[str, Any] = {}, round_num: int = 0)
```

最小训练任务单元。

**参数**：

* **task_id**(str)：任务 ID（必需）。
* **origin_task_id**(str)：原始任务 ID（必需）。
* **task_sample**(Dict[str, Any]，可选)：任务样本数据。默认值：`{}`。
* **round_num**(int，可选)：Rollout 轮数。默认值：`0`。

## class openjiuwen.agent_evolving.agent_rl.schemas.RolloutWithReward

```python
class openjiuwen.agent_evolving.agent_rl.schemas.RolloutWithReward(turn_id: Optional[int] = None, task_id: Optional[str] = None, rollout_id: Optional[str] = None, input_prompt_ids: List[int], output_response_ids: List[int], reward: Optional[float] = None, n_turns: Optional[int] = None, loss_mask: Optional[List[int]] = None)
```

标准 MDP 数据单元，表示 token 级别的（输入、输出、奖励）三元组。

**参数**：

* **turn_id**(Optional[int]，可选)：轮次 ID。默认值：`None`。
* **task_id**(Optional[str]，可选)：任务 ID。默认值：`None`。
* **rollout_id**(Optional[str]，可选)：Rollout ID。默认值：`None`。
* **input_prompt_ids**(List[int])：输入提示词 token ID 列表（必需）。
* **output_response_ids**(List[int])：输出响应 token ID 列表（必需）。
* **reward**(Optional[float]，可选)：奖励值。默认值：`None`。
* **n_turns**(Optional[int]，可选)：轮次数量。默认值：`None`。
* **loss_mask**(Optional[List[int]]，可选)：每 token 的损失掩码。1 = 模型生成的 token（参与损失计算），0 = 环境 token（排除在损失计算之外）。默认值：`None`。

## class openjiuwen.agent_evolving.agent_rl.coordinator.task_queue.TaskQueue

```python
class openjiuwen.agent_evolving.agent_rl.coordinator.task_queue.TaskQueue()
```

异步任务队列 + Rollout 结果缓冲区，用于 RL 训练守护进程。

### __init__(self) -> None

初始化空的任务队列和 rollout 缓冲区。

### queue_task(self, task: RLTask) -> str

将新任务加入队列并返回其任务标识符。

**参数**：

* **task**(RLTask)：要加入队列的任务。

**返回**：

**str**，任务标识符。

### get_task(self) -> Optional[RLTask]

获取下一个待处理的任务。

**返回**：

**Optional[RLTask]**，下一个任务，若队列为空则返回 `None`。

### delete_task(self, task: RLTask)

直接从处理中池中移除任务。

**参数**：

* **task**(RLTask)：要移除的任务。

### add_rollout(self, rollout: RolloutMessage) -> str

存储已完成的 rollout 并清除其处理中条目。

**参数**：

* **rollout**(RolloutMessage)：要存储的 rollout。

**返回**：

**str**，Rollout ID。

### get_rollouts(self) -> Dict[str, RolloutMessage]

原子性地获取并清除所有缓存的 rollouts。

**返回**：

**Dict[str, RolloutMessage]**，Rollout 字典。

### is_finished(self) -> bool

检查所有任务是否已处理完成。

**返回**：

**bool**，若队列为空且没有正在处理的任务则返回 `True`。

### clear(self) -> None

完全重置队列。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.agent_rl.schemas import RLTask
>>> from openjiuwen.agent_evolving.agent_rl.coordinator.task_queue import TaskQueue
>>> 
>>> async def demo_task_queue():
>>>     queue = TaskQueue()
>>>     
>>>     # 添加任务
>>>     task = RLTask(task_id="task_1", origin_task_id="origin_1", task_sample={"query": "test"})
>>>     await queue.queue_task(task)
>>>     
>>>     # 获取任务
>>>     retrieved_task = await queue.get_task()
>>>     print(f"Retrieved task: {retrieved_task.task_id}")
>>>     
>>>     # 检查是否完成
>>>     print(f"Is finished: {queue.is_finished()}")
>>> 
>>> asyncio.run(demo_task_queue())
Retrieved task: task_1
Is finished: False
```
