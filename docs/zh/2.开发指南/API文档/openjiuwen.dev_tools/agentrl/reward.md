# openjiuwen.dev_tools.agentrl.reward

## class openjiuwen.dev_tools.agentrl.reward.registry.RewardRegistry

```python
class openjiuwen.dev_tools.agentrl.reward.registry.RewardRegistry()
```

全局注册表，将奖励名称映射到可调用对象。

### __init__(self) -> None

初始化奖励注册表。

### register(self, name: str, fn: RewardCallable) -> None

按名称注册奖励函数。

**参数**：

* **name**(str)：奖励函数名称。
* **fn**(RewardCallable)：奖励函数。

**异常**：

* **BaseError**：奖励名称为空时抛出。

### get(self, name: str) -> RewardCallable

根据名称查找奖励函数。若未找到则抛出异常。

**参数**：

* **name**(str)：奖励函数名称。

**返回**：

**RewardCallable**，奖励函数。

**异常**：

* **BaseError**：奖励函数未找到时抛出。

### list(self) -> List[str]

返回所有已注册奖励名称的列表。

**返回**：

**List[str]**，奖励名称列表。

## 模块级默认注册表

```python
from openjiuwen.dev_tools.agentrl.reward.registry import reward_registry
```

模块级默认奖励注册表实例。

## func openjiuwen.dev_tools.agentrl.reward.registry.register_reward

```python
def register_reward(name: str) -> Callable[[RewardCallable], RewardCallable]
```

用于按名称注册奖励函数的装饰器。

**参数**：

* **name**(str)：奖励函数名称。

**返回**：

**Callable[[RewardCallable], RewardCallable]**，装饰器函数。

**样例**：

```python
>>> from openjiuwen.dev_tools.agentrl.reward.registry import register_reward, reward_registry
>>> 
>>> @register_reward("my_reward")
... def my_reward_function(rollout_message):
...     # 根据 rollout_message 计算奖励
...     # 返回 float 或 dict（包含 reward_list 和/或 global_reward）
...     return {"reward_list": [1.0, 0.5], "global_reward": 0.75}
>>> 
>>> # 查看已注册的奖励函数
>>> print(reward_registry.list())
['my_reward']
>>> 
>>> # 获取奖励函数并调用
>>> reward_fn = reward_registry.get("my_reward")
>>> # reward_fn(rollout_message) -> 计算奖励
```
