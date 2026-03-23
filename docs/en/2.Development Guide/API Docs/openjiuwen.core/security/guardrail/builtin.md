# openjiuwen.core.security.guardrail.builtin

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

Configuration dataclass for `PromptInjectionGuardrail`, reduces constructor parameter count and improves code readability.

**Parameters**:

* **mode**(str): Detection mode, options: "rules", "api", "local". Default: `"rules"`.
* **model_type**(str, optional): Model type, options: "bert", "qwen". Default: `None`.
* **api_url**(str, optional): API URL for api mode. Default: `None`.
* **api_key**(str, optional): API key. Default: `None`.
* **timeout**(float): Request timeout in seconds. Default: `30.0`.
* **model_path**(str, optional): Local model path for local mode. Default: `None`.
* **device**(str): Device selection, options: "auto", "cpu", "cuda". Default: `"auto"`.
* **custom_patterns**(List[str], optional): Custom regex patterns. Default: `None`.
* **risk_level**(RiskLevel): Risk level when risk detected. Default: `RiskLevel.HIGH`.
* **bert_thresholds**(Dict[str, float], optional): BERT confidence thresholds. Default: `None`.
* **attack_class_id**(int): BERT attack class ID. Default: `1`.
* **qwen_risk_type**(str): Qwen risk type. Default: `"content_risk"`.
* **parser**(ModelOutputParser, optional): Custom parser. Default: `None`.

**Example**:

```python
from openjiuwen.core.security.guardrail import (
    PromptInjectionGuardrail,
    PromptInjectionGuardrailConfig,
    RiskLevel
)

# Rules mode configuration
config = PromptInjectionGuardrailConfig(
    custom_patterns=[r"ignore.*instructions"],
    risk_level=RiskLevel.HIGH
)
guardrail = PromptInjectionGuardrail(config=config)

# API mode configuration
config = PromptInjectionGuardrailConfig(
    mode="api",
    model_type="bert",
    api_url="https://api.example.com/detect",
    api_key="your-api-key"
)
guardrail = PromptInjectionGuardrail(config=config)

# Local model mode configuration
config = PromptInjectionGuardrailConfig(
    mode="local",
    model_type="bert",
    model_path="/path/to/model",
    device="cuda"
)
guardrail = PromptInjectionGuardrail(config=config)
```

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

Prompt injection detection guardrail, inherited from `BaseGuardrail`. Monitors `LLM_INVOKE_INPUT` and `TOOL_INVOKE_OUTPUT` events by default to detect prompt injection attacks.

**Parameters**:

* **config**(PromptInjectionGuardrailConfig, optional): Configuration dataclass. Ignored if backend is provided. Default: `None`.
* **backend**(GuardrailBackend, optional): Custom detection backend. Takes precedence over config. Default: `None`.
* **events**(List[str], optional): Events to monitor. Supports strings or event objects. Default: `None` (uses default events).
* **priority**(int, optional): Callback priority. Default: `None`.
* **enable_logging**(bool): Enable logging output. Default: `True`.

### async detect

```python
async detect(event_name: str, *args, **kwargs) -> GuardrailResult
```

Detect risks in input.

**Parameters**:

* **event_name**(str): Triggered event name.
* **args**: Positional arguments from callback framework.
* **kwargs**: Keyword arguments from callback framework.

**Expected kwargs**:

* **messages**(List[dict]): LLM call message list.
* **result**(Any): Tool call result.

**Returns**:

**GuardrailResult**, detection result.

**Example**:

```python
from openjiuwen.core.security.guardrail import (
    PromptInjectionGuardrail,
    PromptInjectionGuardrailConfig,
    GuardrailBackend,
    RiskAssessment,
    RiskLevel
)

# Method 1: Using config class (recommended)
config = PromptInjectionGuardrailConfig(
    custom_patterns=[
        r"ignore\s+(all|previous)\s+instructions",
        r"forget\s+(your\s+)?training",
    ],
    risk_level=RiskLevel.HIGH
)
guardrail = PromptInjectionGuardrail(config=config)

# Method 2: Using custom backend
class MyDetector(GuardrailBackend):
    async def analyze(self, data: dict) -> RiskAssessment:
        text = data.get("content", "")
        has_risk = "ignore" in text.lower()
        return RiskAssessment(
            has_risk=has_risk,
            risk_level=RiskLevel.HIGH if has_risk else RiskLevel.SAFE,
            risk_type="prompt_injection" if has_risk else None
        )

guardrail = PromptInjectionGuardrail(backend=MyDetector())
```

---

## class openjiuwen.core.security.guardrail.GuardrailBackend

Abstract base class for detection backends.

```python
class GuardrailBackend(ABC):
    @abstractmethod
    async def analyze(self, data: dict) -> RiskAssessment:
        """Analyze data and return risk assessment"""
        pass
```

---

## class openjiuwen.core.security.guardrail.RiskLevel

Risk level enumeration.

| Value    | Description   |
| -------- | ------------- |
| SAFE     | No risk       |
| LOW      | Low risk      |
| MEDIUM   | Medium risk   |
| HIGH     | High risk     |
| CRITICAL | Critical risk |

---

## class openjiuwen.core.security.guardrail.RiskAssessment

Risk assessment result.

```python
@dataclass
class RiskAssessment:
    has_risk: bool
    risk_level: RiskLevel
    risk_type: Optional[str] = None
    confidence: float = 0.0
    details: dict = field(default_factory=dict)
```

