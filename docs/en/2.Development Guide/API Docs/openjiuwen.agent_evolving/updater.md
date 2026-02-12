# openjiuwen.agent_evolving.updater

`openjiuwen.agent_evolving.updater` is the **update generation module** in the openJiuwen self-evolving Agent framework, responsible for:

- Defining the `Updater` protocol, unifying "single-dimension optimizer direct writeback" and "multi-dimensional attribution + allocation" into one interface;
- Providing `SingleDimUpdater` and `MultiDimUpdater` implementations;
- Trainer only needs to call the `(trajectories, evaluated_cases) -> Updates` interface, with Updater generating parameter updates.

---

## func openjiuwen.agent_evolving.updater

The `openjiuwen.agent_evolving.updater` module exports the following public interfaces:

```python
from openjiuwen.agent_evolving import Updater, SingleDimUpdater, MultiDimUpdater
```

---

## class openjiuwen.agent_evolving.updater.protocol.Updater

Updater protocol: Unifies "single-dimension optimizer direct writeback" and "multi-dimensional attribution + allocation" into one interface. Trainer only depends on: `(trajectories, evaluated_cases) -> Updates` or `List[Updates]` (when multiple candidates, Trainer selects the best on validation set).

Developers implementing custom updaters need to implement the following methods (or compatible protocol).

### bind(operators, targets=None, **config) -> int

Bind operators and filter optimizable ones; returns bind count, 0 triggers Trainer soft-exit (logs and returns agent directly).

### requires_forward_data() -> bool

Whether this updater needs framework to execute forward on train_cases. Returns False for black-box optimizers (e.g., tool_optimizer, data-self-generation) that generate/execute/evaluate internally.

### update(trajectories, evaluated_cases, config) -> Union[Updates, List[Updates]]

Generate updates based on trajectories and evaluation results. Returns single Updates (applied directly by Trainer) or multiple Updates list (evaluated by Trainer on validation set with best selected).

### get_state() -> Dict[str, Any]

Returns persistable internal state (for checkpointing).

### load_state(state: Dict[str, Any]) -> None

Restores from persisted state.

---

## class openjiuwen.agent_evolving.updater.single_dim.SingleDimUpdater

Single-dimension updater: Internally uses a BaseOptimizer, first backward then step, the resulting Updates are applied uniformly by Trainer.

```python
class SingleDimUpdater(optimizer: BaseOptimizer)
```

**Parameters**:

* **optimizer**(BaseOptimizer): Single-dimension optimizer instance.

### bind(operators, targets=None, **config) -> int

Passes operators and optional targets to internal optimizer.bind, returns bind count; 0 triggers Trainer soft-exit.

### requires_forward_data() -> bool

Delegates to internal optimizer.requires_forward_data().

### update(trajectories, evaluated_cases, config) -> Updates

Calls optimizer.add_trajectory for each trajectory, then backward(evaluated_cases), finally returns optimizer.step(); Trainer calls apply_updates to apply.

### staticmethod get_state() -> Dict[str, Any]

Current implementation has no persistable state, returns empty dict.

### staticmethod load_state(state) -> None

Current implementation does not restore state.

---

## class openjiuwen.agent_evolving.updater.multi_dim.MultiDimUpdater

Multi-dimensional updater: Internally handles "attribution/allocation" (distributes bad case signals to multiple operators), then calls corresponding dimension optimizer for attributed operators, merges Updates applied uniformly by Trainer. Dimensions divided by operator domain: `llm` / `tool` / `memory` (correspond to LLMCallOperator, ToolCallOperator, MemoryCallOperator); users only need to configure `domain_optimizers: Dict[domain, optimizer]`, only one optimizer per domain.

```python
class MultiDimUpdater(*, domain_optimizers: Optional[Dict[str, Any]] = None)
```

**Parameters**:

* **domain_optimizers**(Dict[str, Any], optional): Domain to optimizer mapping; empty dict if not provided.

### abstractmethod bind(operators, targets=None, **config) -> int

Bind operators and filter optimizable ones; implemented by subclass.

### requires_forward_data() -> bool

Default: returns True if any optimizer in domain_optimizers needs forward data; subclass may override for custom logic.

### abstractmethod update(trajectories, evaluated_cases, config) -> Updates

Generates merged Updates; implemented by subclass.

### abstractmethod get_state() -> Dict[str, Any]

Returns persistable state; implemented by subclass.

### abstractmethod load_state(state) -> None

Restores from state; implemented by subclass.
