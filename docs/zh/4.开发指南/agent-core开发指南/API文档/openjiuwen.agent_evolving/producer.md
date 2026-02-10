# openjiuwen.agent_evolving.producer

`openjiuwen.agent_evolving.producer` 定义「轨迹 + 评估结果 → 参数更新」的生产者接口，并实现单维与多维更新生产者。

---

## class openjiuwen.agent_evolving.producer.protocol.UpdateProducer

更新生产者协议：统一「单维优化器直接写回」与「多维归因 + 分配」的入口。Trainer 只依赖：`(trajectories, evaluated_cases) -> Updates` 或 `List[Updates]`（多候选时由 Trainer 在验证集上选优）。

开发者实现自定义生产者时，需实现以下方法（或兼容协议）。

### bind(operators, targets=None, **config) -> int

绑定算子并筛选可优化项；返回绑定数量，0 时 Trainer 软退出（打日志并直接返回 agent）。

### requires_forward_data() -> bool

是否需要框架在 train_cases 上执行前向。黑盒优化器（如部分 tool_optimizer、数据自生成）可在内部自行执行/评估，返回 `False` 时 Trainer 不执行前向。

### produce(trajectories, evaluated_cases, config) -> Union[Updates, List[Updates]]

根据轨迹与评估结果生成更新。返回单份 Updates（由 Trainer 直接应用），或多份 Updates 列表（由 Trainer 在验证集上评估并选出最优一份应用）。

### get_state() -> Dict[str, Any]

返回可持久化的内部状态（用于检查点）。

### load_state(state: Dict[str, Any]) -> None

从持久化状态恢复。

---

## class openjiuwen.agent_evolving.producer.single_dim.SingleDimProducer

单维更新生产者：内部使用一个 BaseOptimizer，先 backward 再 update，得到的 Updates 由 Trainer 统一应用。

```text
class SingleDimProducer(optimizer: BaseOptimizer)
```

**参数**：

* **optimizer**(BaseOptimizer)：单维优化器实例。

### bind(operators, targets=None, **config) -> int

将算子与可选 targets 传给内部 optimizer.bind，返回绑定数量；0 时 Trainer 软退出。

### requires_forward_data() -> bool

委托给内部 optimizer.requires_forward_data()。

### produce(trajectories, evaluated_cases, config) -> Updates

对每条轨迹调用 optimizer.add_trajectory，再 backward(evaluated_cases)，最后返回 optimizer.update()；Trainer 会调用 apply_updates 应用。

### staticmethod get_state() -> Dict[str, Any]

当前实现无持久化状态，返回空字典。

### staticmethod load_state(state) -> None

当前实现不恢复状态。

---

## class openjiuwen.agent_evolving.producer.multi_dim.MultiDimProducer

多维更新生产者：在内部做「归因/分配」（将 bad case 信号分配到多个算子），再按维度调用对应优化器，合并 Updates 后由 Trainer 统一应用。维度按算子域划分：`llm` / `tool` / `memory`（对应 LLMCallOperator、ToolCallOperator、MemoryCallOperator）；用户只需配置 `domain_optimizers: Dict[domain, optimizer]`，同一域仅一个优化器。

```text
class MultiDimProducer(*, domain_optimizers: Optional[Dict[str, Any]] = None)
```

**参数**：

* **domain_optimizers**(Dict[str, Any]，可选)：域到优化器的映射；未传则为空字典。

### abstractmethod bind(operators, targets=None, **config) -> int

绑定算子并筛选可优化项；子类实现。

### requires_forward_data() -> bool

默认：若任意 domain_optimizers 中优化器需要前向数据则返回 `True`；子类可重写。

### abstractmethod produce(trajectories, evaluated_cases, config) -> Updates

生成合并后的 Updates；子类实现。

### abstractmethod get_state() -> Dict[str, Any]

返回可持久化状态；子类实现。

### abstractmethod load_state(state) -> None

从状态恢复；子类实现。
