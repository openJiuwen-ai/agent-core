# openjiuwen.core.security.guardrail

## class openjiuwen.core.security.guardrail.PromptInjectionGuardrailConfig

```
@dataclass
class openjiuwen.core.security.guardrail.PromptInjectionGuardrailConfig:
    mode: str = "rules"
    model_type: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout: float = 30.0
    model_path: Optional[str] = None
    device: str = "auto"
    custom_patterns: Optional[List[str]] = None
    risk_level: RiskLevel = RiskLevel.HIGH
    bert_thresholds: Optional[Dict[str, float]] = None
    attack_class_id: int = 1
    qwen_risk_type: str = "content_risk"
    parser: Optional[ModelOutputParser] = None
```

PromptInjectionGuardrail 的配置数据类，用于减少构造函数参数数量。

**参数**：

* **mode**(str, 可选)：检测模式，可选值为 "rules"、"api"、"local"。默认值：`"rules"`。
* **model_type**(str, 可选)：模型类型，可选值为 "bert"、"qwen"。默认值：`None`。
* **api_url**(str, 可选)：API 地址（api 模式）。默认值：`None`。
* **api_key**(str, 可选)：API 密钥。 默认值：`None`。
* **timeout**(float, 可选)：请求超时时间（秒）。默认值：`30.0`。
* **model_path**(str, 可选)：本地模型路径（local 模式）。默认值：`None`。
* **device**(str, 可选)：设备选择，可选值为 "auto"、"cpu"、"cuda"。默认值：`"auto"`。
* **custom_patterns**(List[str], 可选)：自定义正则规则。默认值：`None`。
* **risk_level**(RiskLevel, 可选)：检测到风险时的等级。默认值：`RiskLevel.HIGH`。
* **bert_thresholds**(Dict[str, float], 可选)：BERT 置信度阈值。默认值：`None`。
* **attack_class_id**(int, 可选)：BERT 攻击类别 ID。默认值：`1`。
* **qwen_risk_type**(str, 可选)：Qwen 风险类型。默认值：`"content_risk"`。
* **parser**(ModelOutputParser, 可选)：自定义解析器。默认值：`None`。

---

## class openjiuwen.core.security.guardrail.PromptInjectionGuardrail

```
class openjiuwen.core.security.guardrail.PromptInjectionGuardrail(
    config: Optional[PromptInjectionGuardrailConfig] = None,
    backend: Optional[GuardrailBackend] = None,
    events: Optional[List[str]] = None,
    priority: Optional[int] = None,
    enable_logging: bool = True
)
```

Prompt 注入检测护栏，用于检测提示词注入攻击。

**参数**：

* **config**(PromptInjectionGuardrailConfig, 可选)：配置数据类。如果提供 backend，则 config 被忽略。默认值：`None`。
* **backend**(GuardrailBackend, 可选)：自定义检测后端。优先级高于 config。默认值：`None`。
* **events**(List[str], 可选)：要监听的事件名称列表。默认值：`["llm_invoke_input", "tool_invoke_output"]`。
* **priority**(int, 可选)：回调优先级。默认值：`None`。
* **enable_logging**(bool, 可选)：是否启用日志输出。默认值：`True`。

**示例**：

```python
from openjiuwen.core.security.guardrail import (
    PromptInjectionGuardrail,
    PromptInjectionGuardrailConfig,
    RiskLevel
)

# 使用配置类
config = PromptInjectionGuardrailConfig(
    custom_patterns=[r"ignore.*instructions"],
    risk_level=RiskLevel.HIGH
)
guardrail = PromptInjectionGuardrail(config=config)

# 使用自定义后端
class MyBackend(GuardrailBackend):
    async def analyze(self, ctx) -> RiskAssessment:
        # 实现检测逻辑
        pass

guardrail = PromptInjectionGuardrail(backend=MyBackend())
```

---

## class openjiuwen.core.security.guardrail.BaseGuardrail

```
class openjiuwen.core.security.guardrail.BaseGuardrail(
    backend: Optional[GuardrailBackend] = None,
    events: Optional[List[str]] = None,
    enable_logging: bool = True
)
```

护栏实现的抽象基类。护栏用于监控Agent执行流程中的特定事件，当这些事件被触发时执行安全检测。它与回调框架集成，支持使用自定义检测后端进行配置。

子类应定义`DEFAULT_EVENTS`类属性，并可以重写`listen_events`属性以实现动态事件配置。

**参数**：

* **backend**(GuardrailBackend, 可选)：可选的检测后端。也可以通过`set_backend()`方法稍后设置。默认值：`None`。
* **events**(List[str], 可选)：可选的要监听的事件名称列表。如果未提供，则使用子类的`DEFAULT_EVENTS`。默认值：`None`。
* **enable_logging**(bool, 可选)：是否启用日志输出。默认值：`True`。

### listen_events

属性，返回此护栏应监听的事件名称列表。

**返回**：

**List[str]**，事件名称字符串列表（返回副本以防止外部修改）。

### with_events

```python
with_events(events: List[str]) -> BaseGuardrail
```

设置要监听的事件名称（支持链式调用）。这允许在运行时配置护栏应监控哪些事件。必须在`register()`方法之前调用。

**参数**：

* **events**(List[str])：事件名称字符串列表。

**返回**：

**BaseGuardrail**，返回自身以支持方法链式调用。

### set_backend

```python
set_backend(backend: GuardrailBackend) -> BaseGuardrail
```

设置检测后端。

**参数**：

* **backend**(GuardrailBackend)：要使用的检测后端。

**返回**：

**BaseGuardrail**，返回自身以支持方法链式调用。

### get_backend

```python
get_backend() -> Optional[GuardrailBackend]
```

获取当前配置的检测后端。

**返回**：

**Optional[GuardrailBackend]**，配置的检测后端，如果未设置则返回`None`。

### async detect

```python
async detect(event_name: str, *args, **kwargs) -> GuardrailResult
```

对被触发事件执行安全检测。当订阅的事件被触发时调用此方法。默认实现会委托给配置的后端（如果可用）。子类可以重写此方法以实现自定义检测逻辑。

**参数**：

* **event_name**(str)：被触发的事件名称。
* **args**：从回调框架触发事件时传递的位置参数。
* **kwargs**：从回调框架触发事件时传递的关键字参数（事件数据）。

**返回**：

**GuardrailResult**，表示内容是否安全。

**异常**：

* **ValueError**：如果未配置后端且未重写`detect`方法。

### async register

```python
async register(framework: Any) -> None
```

将此护栏注册到回调框架。这会将护栏的`detect`方法注册为`listen_events`中所有事件的回调函数。

**参数**：

* **framework**(Any)：`AsyncCallbackFramework`实例。

### async unregister

```python
async unregister() -> None
```

从回调框架注销此护栏。移除所有已注册的回调函数。即使未注册，调用此方法也是安全的。

---

## class openjiuwen.core.security.guardrail.GuardrailResult

```
class openjiuwen.core.security.guardrail.GuardrailResult(
    is_safe: bool,
    risk_level: RiskLevel,
    risk_type: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    modified_data: Optional[Dict[str, Any]] = None
)
```

护栏检测结果数据类。

**参数**：

* **is_safe**(bool)：检测是否通过。
* **risk_level**(RiskLevel)：检测到的风险等级。
* **risk_type**(str, 可选)：风险类型标识符。默认值：`None`。
* **details**(Dict[str, Any], 可选)：检测详细信息。默认值：`None`。
* **modified_data**(Dict[str, Any], 可选)：脱敏后的数据。默认值：`None`。

### pass_

```python
class method pass_(details: Optional[Dict[str, Any]] = None) -> GuardrailResult
```

创建检测通过的便捷方法。

**参数**：

* **details**(Dict[str, Any], 可选)：可选的检测详情。默认值：`None`。

**返回**：

**GuardrailResult**，`is_safe=True`且`risk_level=SAFE`的结果。

### block

```python
class method block(
    risk_level: RiskLevel,
    risk_type: str,
    details: Optional[Dict[str, Any]] = None,
    modified_data: Optional[Dict[str, Any]] = None
) -> GuardrailResult
```

创建检测拦截的便捷方法。

**参数**：

* **risk_level**(RiskLevel)：风险等级。
* **risk_type**(str)：风险类型标识符。
* **details**(Dict[str, Any], 可选)：检测详情。默认值：`None`。
* **modified_data**(Dict[str, Any], 可选)：脱敏后的数据。默认值：`None`。

**返回**：

**GuardrailResult**，`is_safe=False`的结果。

---

## class openjiuwen.core.security.guardrail.RiskAssessment

```
class openjiuwen.core.security.guardrail.RiskAssessment(
    has_risk: bool,
    risk_level: RiskLevel,
    risk_type: Optional[str] = None,
    confidence: float = 0.0,
    details: Optional[Dict[str, Any]] = None
)
```

检测后端返回的风险评估结果。

**参数**：

* **has_risk**(bool)：是否检测到风险。
* **risk_level**(RiskLevel)：风险等级。
* **risk_type**(str, 可选)：风险类型标识符。默认值：`None`。
* **confidence**(float)：置信度，0.0到1.0之间。默认值：`0.0`。
* **details**(Dict[str, Any], 可选)：详细信息。默认值：`None`。

---

## class openjiuwen.core.security.guardrail.RiskLevel

```
class openjiuwen.core.security.guardrail.RiskLevel
```

风险等级枚举。

* **SAFE**：安全，无风险
* **LOW**：低风险
* **MEDIUM**：中风险
* **HIGH**：高风险
* **CRITICAL**：严重风险

**处理机制**：

| 风险等级 | 处理方式 |
|---------|---------|
| SAFE | 正常通过 |
| LOW | 抛出 `GuardrailError` |
| MEDIUM | 抛出 `GuardrailError` |
| HIGH | 抛出 `GuardrailError` |
| CRITICAL | 抛出 `AbortError`，阻断执行 |

> **说明**：`CRITICAL` 等级会抛出 `AbortError` 终止回调执行，其他危险等级抛出 `GuardrailError`。

---

## class openjiuwen.core.common.exception.errors.GuardrailError

```
class openjiuwen.core.common.exception.errors.GuardrailError
```

护栏检测到风险时抛出的异常。继承自框架异常体系。

**示例**：

```python
from openjiuwen.core.common.exception.errors import GuardrailError

try:
    # 触发护栏检测
    result = await guardrail.detect("user_input", text=malicious_input)
except GuardrailError as e:
    print(f"检测到风险: {e.details}")
```
