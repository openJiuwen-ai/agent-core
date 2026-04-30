# openjiuwen.agent_evolving.optimizer

`openjiuwen.agent_evolving.optimizer` provides optimizer base classes and LLM instruction optimization implementations for filtering optimizable operators by dimension (e.g., LLM, tool, memory), caching trajectories, and generating parameter updates based on evaluation results.

---

## class openjiuwen.agent_evolving.optimizer.base.BaseOptimizer

Common skeleton for dimension optimizers: bind filters optimizable Operators and returns count (0 triggers upper-layer soft-exit); add_trajectory/get_trajectories caches trajectories for backward; step() returns Updates, applied uniformly by Trainer.apply_updates.

```text
class BaseOptimizer(**kwargs)
```

**Parameters**:

* **kwargs**: Reserved for subclasses, base class doesn't use directly.

Class attribute **domain**(str): Dimension identifier, overridden by subclasses (e.g., `"llm"`).

### staticmethod requires_forward_data() -> bool

Whether depends on framework executing forward on train_cases. Returns `True` (default) means using trajectories and evaluation results from forward; returns `False` means black-box optimizer executes/evaluates internally. Subclasses can override to `False` to skip Trainer forward.

### staticmethod default_targets() -> List[str]

Subclasses can override, returns list of default optimizable parameter names for this dimension.

### staticmethod filter_operators(operators, targets) -> Dict[str, Operator]

Filters operators that expose any of targets from operators; logs warning on no match, doesn't interrupt.

### bind(operators=None, targets=None, **config) -> int

Binds and filters optimizable operators, initializes _targets, _operators, _parameters, clears _trajectories, _bad_cases. Returns bind count; 0 triggers upper-layer soft-exit.

### add_trajectory(trajectory: Trajectory) -> None

Adds one trajectory to cache for use in backward stage.

### get_trajectories() -> List[Trajectory]

Returns current cached trajectory list (read-only copy).

### clear_trajectories() -> None

Clears trajectory cache (usually called after step).

### backward(evaluated_cases: List[EvaluatedCase]) -> None

First extracts bad cases (score==0), then calls subclass-implemented _backward; throws TOOLCHAIN_OPTIMIZER_BACKWARD_EXECUTION_ERROR on exception.

### step() -> Updates

Calls subclass _step() to get Updates, clears trajectories and returns; throws TOOLCHAIN_OPTIMIZER_UPDATE_EXECUTION_ERROR on exception after clearing trajectories.

### parameters() -> Dict[str, TextualParameter]

Returns copy of current bound operator_id to TextualParameter mapping.

---

## class openjiuwen.agent_evolving.optimizer.base.TextualParameter

Gradient container corresponding to single operator_id, stores target -> gradient text with optional description; doesn't hold Operator reference.

```text
class TextualParameter(operator_id: str)
```

* **operator_id**(str): Corresponding operator ID.
* **gradients**(Dict[str, str]): target -> gradient text.
* **description**(str): Optional description.

### set_gradient(name: str, gradient: str) -> None

Sets gradient text for a target.

### get_gradient(name: str) -> Optional[str]

Gets gradient text for a target.

### set_description(description: str) -> None

Sets description.

### get_description() -> str

Gets description.

---

## class openjiuwen.agent_evolving.optimizer.llm_call.base.LLMCallOptimizerBase

LLM call dimension optimizer base class: only optimizes operators exposing system_prompt / user_prompt; domain is `"llm"`, default_targets is `["system_prompt", "user_prompt"]`. Subclasses implement _backward / _step to complete prompt optimization logic.

### default_targets() -> List[str]

Returns `["system_prompt", "user_prompt"]`.

### filter_operators(operators, targets) -> Dict[str, Operator]

Delegates to base class, filters operators exposing prompt-type tunables.

### _is_target_frozen(op, target: str) -> bool

Determines if target is frozen based on op.get_tunables().

### _get_prompt_template(op, target: str) -> PromptTemplate

Gets target content from op.get_state() and wraps as PromptTemplate.

---

## class openjiuwen.agent_evolving.optimizer.llm_call.instruction_optimizer.InstructionOptimizer

LLM-based instruction (prompt) optimizer: backward stage uses LLM to generate text gradients for bad cases and writes to TextualParameter; step stage uses LLM to generate optimized system/user prompts, returns Updates.

```text
class InstructionOptimizer(
    model_config: ModelRequestConfig,
    model_client_config: ModelClientConfig,
)
```

**Parameters**:

* **model_config**(ModelRequestConfig): LLM request configuration.
* **model_client_config**(ModelClientConfig): LLM client configuration.

Inherits from LLMCallOptimizerBase, targets default to system_prompt, user_prompt; _backward generates text gradients for each bound operator and writes to corresponding TextualParameter; _step generates optimized system/user prompts based on gradients and keeps placeholder consistency, returns Updates of (operator_id, target) -> new content.

---

## Team Skill Optimizer

See [team_skill_optimizer](team_skill_optimizer.md) for details.
