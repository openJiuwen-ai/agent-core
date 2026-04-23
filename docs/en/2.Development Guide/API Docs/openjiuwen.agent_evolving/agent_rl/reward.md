# openjiuwen.agent_evolving.agent_rl.reward

## class openjiuwen.agent_evolving.agent_rl.reward.RewardRegistry

```python
class openjiuwen.agent_evolving.agent_rl.reward.RewardRegistry()
```

Global registry mapping reward names to callable objects.

### __init__(self) -> None

Initialize reward registry.

### register(self, name: str, fn: RewardCallable) -> None

Register reward function by name.

**Parameters**:

* **name**(str): Reward function name.
* **fn**(RewardCallable): Reward function.

**Exceptions**:

* **BaseError**: Raised when reward name is empty.

### get(self, name: str) -> RewardCallable

Look up reward function by name. Raises if not found.

**Parameters**:

* **name**(str): Reward function name.

**Returns**:

**RewardCallable**, The reward function.

**Exceptions**:

* **BaseError**: Raised when reward function is not found.

### list(self) -> List[str]

Return a list of all registered reward names.

**Returns**:

**List[str]**, List of reward names.

## Module-level default registry

```python
from openjiuwen.agent_evolving.agent_rl.reward import reward_registry
```

Module-level default reward registry instance.

## func openjiuwen.agent_evolving.agent_rl.reward.register_reward

```python
def register_reward(name: str) -> Callable[[RewardCallable], RewardCallable]
```

Decorator for registering reward functions by name.

**Parameters**:

* **name**(str): Reward function name.

**Returns**:

**Callable[[RewardCallable], RewardCallable]**, The decorator function.

**Example**:

```python
>>> from openjiuwen.agent_evolving.agent_rl.reward import register_reward, reward_registry
>>> 
>>> @register_reward("my_reward")
... def my_reward_function(rollout_message):
...     # Compute reward based on rollout_message
...     # Return float or dict (with reward_list and/or global_reward)
...     return {"reward_list": [1.0, 0.5], "global_reward": 0.75}
>>> 
>>> # List registered reward functions
>>> print(reward_registry.list())
['my_reward']
>>> 
>>> # Get and call reward function
>>> reward_fn = reward_registry.get("my_reward")
>>> # reward_fn(rollout_message) -> compute reward
```
