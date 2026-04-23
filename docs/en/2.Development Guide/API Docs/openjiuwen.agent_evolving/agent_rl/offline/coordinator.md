# openjiuwen.agent_evolving.agent_rl.coordinator

## class openjiuwen.agent_evolving.agent_rl.schemas.Rollout

```python
class openjiuwen.agent_evolving.agent_rl.schemas.Rollout(turn_id: Optional[int] = None, input_prompt: Optional[Dict[str, Any]] = None, output_response: Optional[Dict[str, Any]] = None, llm_config: Optional[Dict[str, Any]] = None)
```

Single-turn dialogue rollout.

Field conventions:
- input_prompt["message"]: Input message list (OpenAI message format)
- input_prompt["tools"]: Tool definition list
- output_response: LLM output message (content or tool_calls)

**Parameters**:

* **turn_id**(Optional[int], optional): Turn ID. Default: `None`.
* **input_prompt**(Optional[Dict[str, Any]], optional): Input prompt. Default: `None`.
* **output_response**(Optional[Dict[str, Any]], optional): LLM output response. Default: `None`.
* **llm_config**(Optional[Dict[str, Any]], optional): LLM config. Default: `None`.

## class openjiuwen.agent_evolving.agent_rl.schemas.RolloutMessage

```python
class openjiuwen.agent_evolving.agent_rl.schemas.RolloutMessage(task_id: Optional[str] = None, origin_task_id: Optional[str] = None, rollout_id: Optional[str] = None, start_time: Optional[str] = None, end_time: Optional[str] = None, rollout_info: List[Rollout] = [], reward_list: List[float] = [], global_reward: Optional[float] = None, turn_count: int = 0, round_num: Optional[int] = None)
```

Complete task execution result, aggregating multi-turn dialogue and associated rewards.

**Parameters**:

* **task_id**(Optional[str], optional): Task ID. Default: `None`.
* **origin_task_id**(Optional[str], optional): Original task ID. Default: `None`.
* **rollout_id**(Optional[str], optional): Rollout ID. Default: `None`.
* **start_time**(Optional[str], optional): Start time (ISO format). Default: `None`.
* **end_time**(Optional[str], optional): End time (ISO format). Default: `None`.
* **rollout_info**(List[Rollout], optional): Rollout info list. Default: `[]`.
* **reward_list**(List[float], optional): Reward list. Default: `[]`.
* **global_reward**(Optional[float], optional): Global reward. Default: `None`.
* **turn_count**(int, optional): Dialogue turn count. Default: `0`.
* **round_num**(Optional[int], optional): Rollout round count. Default: `None`.

## class openjiuwen.agent_evolving.agent_rl.schemas.RLTask

```python
class openjiuwen.agent_evolving.agent_rl.schemas.RLTask(task_id: str, origin_task_id: str, task_sample: Dict[str, Any] = {}, round_num: int = 0)
```

Minimal training task unit.

**Parameters**:

* **task_id**(str): Task ID (required).
* **origin_task_id**(str): Original task ID (required).
* **task_sample**(Dict[str, Any], optional): Task sample data. Default: `{}`.
* **round_num**(int, optional): Rollout round count. Default: `0`.

## class openjiuwen.agent_evolving.agent_rl.schemas.RolloutWithReward

```python
class openjiuwen.agent_evolving.agent_rl.schemas.RolloutWithReward(turn_id: Optional[int] = None, task_id: Optional[str] = None, rollout_id: Optional[str] = None, input_prompt_ids: List[int], output_response_ids: List[int], reward: Optional[float] = None, n_turns: Optional[int] = None, loss_mask: Optional[List[int]] = None)
```

Standard MDP data unit representing token-level (input, output, reward) triplets.

**Parameters**:

* **turn_id**(Optional[int], optional): Turn ID. Default: `None`.
* **task_id**(Optional[str], optional): Task ID. Default: `None`.
* **rollout_id**(Optional[str], optional): Rollout ID. Default: `None`.
* **input_prompt_ids**(List[int]): Input prompt token ID list (required).
* **output_response_ids**(List[int]): Output response token ID list (required).
* **reward**(Optional[float], optional): Reward value. Default: `None`.
* **n_turns**(Optional[int], optional): Number of turns. Default: `None`.
* **loss_mask**(Optional[List[int]], optional): Per-token loss mask. 1 = model-generated token (participates in loss), 0 = environment token (excluded from loss). Default: `None`.

## class openjiuwen.agent_evolving.agent_rl.coordinator.task_queue.TaskQueue

```python
class openjiuwen.agent_evolving.agent_rl.coordinator.task_queue.TaskQueue()
```

Async task queue + Rollout result buffer for RL training daemon.

### __init__(self) -> None

Initialize empty task queue and rollout buffer.

### queue_task(self, task: RLTask) -> str

Add new task to queue and return its task identifier.

**Parameters**:

* **task**(RLTask): Task to enqueue.

**Returns**:

**str**, Task identifier.

### get_task(self) -> Optional[RLTask]

Get next pending task.

**Returns**:

**Optional[RLTask]**, Next task, or `None` if queue is empty.

### delete_task(self, task: RLTask)

Remove task directly from in-progress pool.

**Parameters**:

* **task**(RLTask): Task to remove.

### add_rollout(self, rollout: RolloutMessage) -> str

Store completed rollout and clear its in-progress entry.

**Parameters**:

* **rollout**(RolloutMessage): Rollout to store.

**Returns**:

**str**, Rollout ID.

### get_rollouts(self) -> Dict[str, RolloutMessage]

Atomically get and clear all cached rollouts.

**Returns**:

**Dict[str, RolloutMessage]**, Rollout dictionary.

### is_finished(self) -> bool

Check whether all tasks have been processed.

**Returns**:

**bool**, `True` if queue is empty and no tasks in progress.

### clear(self) -> None

Fully reset queue.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.agent_rl.schemas import RLTask
>>> from openjiuwen.agent_evolving.agent_rl.coordinator.task_queue import TaskQueue
>>> 
>>> async def demo_task_queue():
>>>     queue = TaskQueue()
>>>     
>>>     # Add task
>>>     task = RLTask(task_id="task_1", origin_task_id="origin_1", task_sample={"query": "test"})
>>>     await queue.queue_task(task)
>>>     
>>>     # Get task
>>>     retrieved_task = await queue.get_task()
>>>     print(f"Retrieved task: {retrieved_task.task_id}")
>>>     
>>>     # Check if finished
>>>     print(f"Is finished: {queue.is_finished()}")
>>> 
>>> asyncio.run(demo_task_queue())
Retrieved task: task_1
Is finished: False
```
