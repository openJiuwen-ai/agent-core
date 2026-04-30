# openjiuwen.agent_evolving.optimizer

`openjiuwen.agent_evolving.optimizer` 提供按维度（如 LLM、工具、记忆）过滤可优化算子、缓存轨迹、根据评估结果生成参数更新的优化器基类与 LLM 指令优化实现。

---

## class openjiuwen.agent_evolving.optimizer.base.BaseOptimizer

各维度优化器的公共骨架：bind 过滤可优化 Operator 并返回数量（0 触发上层软退出）；add_trajectory/get_trajectories 缓存轨迹供 backward；step() 返回 Updates，由 Trainer.apply_updates 统一应用。

```text
class BaseOptimizer(**kwargs)
```

**参数**：

* **kwargs**：保留给子类，基类不直接使用。

类属性 **domain**(str)：维度标识，子类覆盖（如 `"llm"`）。

### staticmethod requires_forward_data() -> bool

是否依赖框架在 train_cases 上执行前向。返回 `True`（默认）表示使用前向得到的轨迹与评估结果；返回 `False` 表示黑盒优化器在内部自行执行/评估。子类可覆盖为 `False` 以跳过 Trainer 前向。

### staticmethod default_targets() -> List[str]

子类可覆盖，返回该维度的默认可优化参数名列表。

### staticmethod filter_operators(operators, targets) -> Dict[str, Operator]

从 operators 中筛选出暴露 targets 中任一参数的算子；不匹配时打 warning，不中断。

### bind(operators=None, targets=None, **config) -> int

绑定并筛选可优化算子，初始化 _targets、_operators、_parameters，清空 _trajectories、_bad_cases。返回绑定数量；0 时上层软退出。

### add_trajectory(trajectory: Trajectory) -> None

将一条轨迹加入缓存，供 backward 阶段使用。

### get_trajectories() -> List[Trajectory]

返回当前缓存的轨迹列表（只读拷贝）。

### clear_trajectories() -> None

清空轨迹缓存（通常在 step 后调用）。

### backward(evaluated_cases: List[EvaluatedCase]) -> None

先取 bad cases（score==0），再调用子类实现的 _backward；异常时抛出 TOOLCHAIN_OPTIMIZER_BACKWARD_EXECUTION_ERROR。

### step() -> Updates

调用子类 _step() 得到 Updates，清空轨迹后返回；异常时清空轨迹并抛出 TOOLCHAIN_OPTIMIZER_UPDATE_EXECUTION_ERROR。

### parameters() -> Dict[str, TextualParameter]

返回当前绑定的 operator_id 到 TextualParameter 的拷贝。

---

## class openjiuwen.agent_evolving.optimizer.base.TextualParameter

与单个 operator_id 对应的梯度容器，保存 target -> 梯度文本及可选描述；不持有 Operator 引用。

```text
class TextualParameter(operator_id: str)
```

* **operator_id**(str)：对应算子 ID。
* **gradients**(Dict[str, str])：target -> 梯度文本。
* **description**(str)：可选描述。

### set_gradient(name: str, gradient: str) -> None

设置某 target 的梯度文本。

### get_gradient(name: str) -> Optional[str]

获取某 target 的梯度文本。

### set_description(description: str) -> None

设置描述。

### get_description() -> str

获取描述。

---

## class openjiuwen.agent_evolving.optimizer.llm_call.base.LLMCallOptimizerBase

LLM 调用维度优化器基类：仅优化暴露 system_prompt / user_prompt 的算子；domain 为 `"llm"`，default_targets 为 `["system_prompt", "user_prompt"]`。子类实现 _backward / _step 完成提示词优化逻辑。

### default_targets() -> List[str]

返回 `["system_prompt", "user_prompt"]`。

### filter_operators(operators, targets) -> Dict[str, Operator]

委托基类，筛选暴露 prompt 类 tunable 的算子。

### _is_target_frozen(op, target: str) -> bool

根据 op.get_tunables() 判断 target 是否被冻结。

### _get_prompt_template(op, target: str) -> PromptTemplate

从 op.get_state() 中取 target 对应内容并封装为 PromptTemplate。

---

## class openjiuwen.agent_evolving.optimizer.llm_call.instruction_optimizer.InstructionOptimizer

基于 LLM 的指令（提示词）优化器：backward 阶段用 LLM 对 bad cases 生成文本梯度写入 TextualParameter；step 阶段用 LLM 生成优化后的 system/user 提示词，返回 Updates。

```text
class InstructionOptimizer(
    model_config: ModelRequestConfig,
    model_client_config: ModelClientConfig,
)
```

**参数**：

* **model_config**(ModelRequestConfig)：LLM 请求配置。
* **model_client_config**(ModelClientConfig)：LLM 客户端配置。

继承自 LLMCallOptimizerBase，targets 默认即 system_prompt、user_prompt；_backward 为每个绑定算子生成文本梯度并写入对应 TextualParameter；_step 根据梯度生成优化后的 system/user 提示词并保持占位符一致，返回 (operator_id, target) -> 新内容的 Updates。

---

## 团队技能优化器

详见 [team_skill_optimizer](team_skill_optimizer.md)。
