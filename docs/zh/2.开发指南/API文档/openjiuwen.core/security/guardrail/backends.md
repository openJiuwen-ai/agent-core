# openjiuwen.core.security.guardrail.backends

## 配置数据类

### class openjiuwen.core.security.guardrail.RuleBasedBackendConfig

```
@dataclass
class openjiuwen.core.security.guardrail.RuleBasedBackendConfig:
    patterns: Optional[List[str]] = None
    risk_level: RiskLevel = RiskLevel.HIGH
```

规则后端配置数据类。

**参数**：

* **patterns**(List[str], 可选)：正则表达式模式列表。如果未提供则使用默认模式。默认值：`None`。
* **risk_level**(RiskLevel)：模式匹配时的风险等级。默认值：`RiskLevel.HIGH`。

---

### class openjiuwen.core.security.guardrail.APIModelBackendConfig

```
@dataclass
class openjiuwen.core.security.guardrail.APIModelBackendConfig:
    api_url: str
    parser: Optional[ModelOutputParser] = None
    api_key: Optional[str] = None
    timeout: float = 30.0
    risk_type: str = "model_detection"
```

API 模型后端配置数据类。

**参数**：

* **api_url**(str)：模型 API 端点 URL。
* **parser**(ModelOutputParser, 可选)：将模型输出转换为 RiskAssessment 的解析器。默认值：`None`。
* **api_key**(str, 可选)：API 认证密钥。默认值：`None`。
* **timeout**(float)：请求超时时间（秒）。默认值：`30.0`。
* **risk_type**(str)：风险类型标识符。默认值：`"model_detection"`。

---

### class openjiuwen.core.security.guardrail.LocalModelBackendConfig

```
@dataclass
class openjiuwen.core.security.guardrail.LocalModelBackendConfig:
    model_path: str
    parser: Optional[ModelOutputParser] = None
    device: str = "auto"
    risk_type: str = "model_detection"
```

本地模型后端配置数据类。

**参数**：

* **model_path**(str)：本地模型路径。
* **parser**(ModelOutputParser, 可选)：将模型输出转换为 RiskAssessment 的解析器。默认值：`None`。
* **device**(str)：运行模型的设备（"auto"、"cpu"、"cuda"）。默认值：`"auto"`。
* **risk_type**(str)：风险类型标识符。默认值：`"model_detection"`。

---

## class openjiuwen.core.security.guardrail.GuardrailBackend

```
class openjiuwen.core.security.guardrail.GuardrailBackend
```

护栏检测后端的抽象基类。后端实现提供特定的安全风险检测逻辑（例如提示词注入检测、敏感数据泄露检测）。

### async analyze

```python
async analyze(data: Dict[str, Any]) -> RiskAssessment
```

分析数据以检测安全风险。此方法实现核心检测逻辑，接收事件数据并返回风险评估结果。

**参数**：

* **data**(Dict[str, Any])：包含检测所需信息的事件数据字典。

**返回**：

**RiskAssessment**，描述检测到的风险。

**异常**：

* 任何异常将被护栏框架捕获，检测将失败（保守方法）。

**示例**：

```python
from openjiuwen.core.security.guardrail import GuardrailBackend, RiskAssessment, RiskLevel

class PromptInjectionBackend(GuardrailBackend):
    """示例提示词注入检测后端"""

    async def analyze(self, data: dict) -> RiskAssessment:
        text = data.get("text", "")
        injection_patterns = [
            "ignore previous instructions",
            "disregard all prior commands",
        ]

        has_risk = any(pattern in text.lower() for pattern in injection_patterns)

        return RiskAssessment(
            has_risk=has_risk,
            risk_level=RiskLevel.HIGH if has_risk else RiskLevel.SAFE,
            risk_type="prompt_injection" if has_risk else None
        )
```

---

## class openjiuwen.core.security.guardrail.RuleBasedPromptInjectionBackend

```
class openjiuwen.core.security.guardrail.RuleBasedPromptInjectionBackend(
    config: Optional[RuleBasedBackendConfig] = None,
    *,
    patterns: Optional[List[str]] = None,
    risk_level: RiskLevel = RiskLevel.HIGH
)
```

基于规则的提示词注入检测后端。使用正则表达式模式匹配来检测提示词注入风险。

**参数**：

* **config**(RuleBasedBackendConfig, 可选)：配置数据类。默认值：`None`。
* **patterns**(List[str], 可选)：正则表达式模式列表。如果未提供则使用默认模式。默认值：`None`。
* **risk_level**(RiskLevel)：模式匹配时的风险等级。默认值：`RiskLevel.HIGH`。

**示例**：

```python
from openjiuwen.core.security.guardrail import (
    RuleBasedPromptInjectionBackend,
    RuleBasedBackendConfig,
    RiskLevel
)

# 使用配置类
config = RuleBasedBackendConfig(
    patterns=[r"ignore.*previous.*instructions"],
    risk_level=RiskLevel.HIGH
)
backend = RuleBasedPromptInjectionBackend(config=config)

# 使用关键字参数
backend = RuleBasedPromptInjectionBackend(
    patterns=[r"ignore.*previous.*instructions"],
    risk_level=RiskLevel.HIGH
)
```

---

## class openjiuwen.core.security.guardrail.APIModelBackend

```
class openjiuwen.core.security.guardrail.APIModelBackend(
    config: Optional[APIModelBackendConfig] = None,
    *,
    api_url: Optional[str] = None,
    parser: Optional[ModelOutputParser] = None,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
    risk_type: str = "model_detection"
)
```

通过 HTTP API 调用远程模型的后端。支持调用远程模型服务进行安全检测，使用 ModelOutputParser 解析模型响应。

**参数**：

* **config**(APIModelBackendConfig, 可选)：配置数据类。默认值：`None`。
* **api_url**(str, 可选)：模型 API 端点 URL。
* **parser**(ModelOutputParser, 可选)：将模型输出转换为 RiskAssessment 的解析器。默认值：`None`。
* **api_key**(str, 可选)：API 认证密钥。默认值：`None`。
* **timeout**(float)：请求超时时间（秒）。默认值：`30.0`。
* **risk_type**(str)：风险类型标识符。默认值：`"model_detection"`。

**示例**：

```python
from openjiuwen.core.security.guardrail import APIModelBackend, APIModelBackendConfig
from openjiuwen.core.security.guardrail.context import BertBinaryParser

# 使用配置类
parser = BertBinaryParser(risk_type="prompt_injection")
config = APIModelBackendConfig(
    api_url="https://api.example.com/detect",
    parser=parser,
    api_key="your-api-key"
)
backend = APIModelBackend(config=config)

# 使用关键字参数
backend = APIModelBackend(
    api_url="https://api.example.com/detect",
    parser=parser,
    api_key="your-api-key"
)
```

---

## class openjiuwen.core.security.guardrail.LocalModelBackend

```
class openjiuwen.core.security.guardrail.LocalModelBackend(
    config: Optional[LocalModelBackendConfig] = None,
    *,
    model_path: Optional[str] = None,
    parser: Optional[ModelOutputParser] = None,
    device: str = "auto",
    risk_type: str = "model_detection"
)
```

本地运行模型的后端。加载并在本地运行模型进行安全检测，使用延迟导入隔离 torch/transformers 依赖。

**参数**：

* **config**(LocalModelBackendConfig, 可选)：配置数据类。默认值：`None`。
* **model_path**(str, 可选)：本地模型路径。
* **parser**(ModelOutputParser, 可选)：将模型输出转换为 RiskAssessment 的解析器。默认值：`None`。
* **device**(str)：运行模型的设备（"auto"、"cpu"、"cuda"）。默认值：`"auto"`。
* **risk_type**(str)：风险类型标识符。默认值：`"model_detection"`。

**示例**：

```python
from openjiuwen.core.security.guardrail import LocalModelBackend, LocalModelBackendConfig
from openjiuwen.core.security.guardrail.context import BertBinaryParser

# 使用配置类
parser = BertBinaryParser()
config = LocalModelBackendConfig(
    model_path="/path/to/bert-classifier",
    parser=parser,
    device="cuda"
)
backend = LocalModelBackend(config=config)

# 使用关键字参数
backend = LocalModelBackend(
    model_path="/path/to/bert-classifier",
    parser=parser,
    device="cuda"
)
```

**依赖**：

需要以下依赖：
```
pip install torch transformers
```

### is_model_loaded

```python
is_model_loaded() -> bool
```

检查模型是否已加载。

**返回**：

**bool**，如果模型已加载返回 True，否则返回 False。

### get_model_info

```python
get_model_info() -> Dict[str, Any]
```

获取模型信息。

**返回**：

**Dict[str, Any]**，包含模型状态信息的字典，包括：
* `model_path`：模型路径
* `device`：设备设置
* `model_loaded`：模型是否已加载
* `has_model`：模型对象是否存在
* `has_tokenizer`：分词器对象是否存在
