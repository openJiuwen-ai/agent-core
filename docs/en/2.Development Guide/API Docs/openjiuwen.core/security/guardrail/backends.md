# openjiuwen.core.security.guardrail.backends

## Configuration Dataclasses

### class openjiuwen.core.security.guardrail.RuleBasedBackendConfig

```
@dataclass
class openjiuwen.core.security.guardrail.RuleBasedBackendConfig:
    patterns: Optional[List[str]] = None
    risk_level: RiskLevel = RiskLevel.HIGH
```

Configuration for rule-based prompt injection backend.

**Parameters**:

* **patterns**(List[str], optional): List of regex patterns. Uses default patterns if not provided. Default: `None`.
* **risk_level**(RiskLevel): Risk level when a pattern matches. Default: `RiskLevel.HIGH`.

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

Configuration for API model backend.

**Parameters**:

* **api_url**(str): Model API endpoint URL.
* **parser**(ModelOutputParser, optional): Parser to convert model output to RiskAssessment. Default: `None`.
* **api_key**(str, optional): API authentication key. Default: `None`.
* **timeout**(float): Request timeout in seconds. Default: `30.0`.
* **risk_type**(str): Risk type identifier. Default: `"model_detection"`.

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

Configuration for local model backend.

**Parameters**:

* **model_path**(str): Path to the local model.
* **parser**(ModelOutputParser, optional): Parser to convert model output to RiskAssessment. Default: `None`.
* **device**(str): Device to run model on ("auto", "cpu", "cuda"). Default: `"auto"`.
* **risk_type**(str): Risk type identifier. Default: `"model_detection"`.

---

## class openjiuwen.core.security.guardrail.GuardrailBackend

```
class openjiuwen.core.security.guardrail.GuardrailBackend
```

Abstract base class for guardrail detection backends. Backend implementations provide specific detection logic for security risks (e.g., prompt injection detection, sensitive data leakage detection).

### async analyze

```python
async analyze(data: Dict[str, Any]) -> RiskAssessment
```

Analyzes data to detect security risks. This method implements the core detection logic, receiving event data and returning a risk assessment result.

**Parameters**:

* **data**(Dict[str, Any]): Event data dictionary containing information needed for detection.

**Returns**:

**RiskAssessment**, describing the detected risk.

**Exceptions**:

* Any exception will be caught by the guardrail framework and detection will fail (conservative approach).

**Example**:

```python
from openjiuwen.core.security.guardrail import GuardrailBackend, RiskAssessment, RiskLevel

class PromptInjectionBackend(GuardrailBackend):
    """Example prompt injection detection backend"""

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

Rule-based prompt injection detection backend. Uses regex pattern matching to detect prompt injection risks.

**Parameters**:

* **config**(RuleBasedBackendConfig, optional): Configuration dataclass. Default: `None`.
* **patterns**(List[str], optional): List of regex patterns. Uses default patterns if not provided. Default: `None`.
* **risk_level**(RiskLevel): Risk level when a pattern matches. Default: `RiskLevel.HIGH`.

**Example**:

```python
from openjiuwen.core.security.guardrail import (
    RuleBasedPromptInjectionBackend,
    RuleBasedBackendConfig,
    RiskLevel
)

# Using config
config = RuleBasedBackendConfig(
    patterns=[r"ignore.*previous.*instructions"],
    risk_level=RiskLevel.HIGH
)
backend = RuleBasedPromptInjectionBackend(config=config)

# Using keyword arguments
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

Backend that calls a remote model via HTTP API. Supports calling remote model services for security detection, uses ModelOutputParser to parse model responses.

**Parameters**:

* **config**(APIModelBackendConfig, optional): Configuration dataclass. Default: `None`.
* **api_url**(str, optional): Model API endpoint URL.
* **parser**(ModelOutputParser, optional): Parser to convert model output to RiskAssessment. Default: `None`.
* **api_key**(str, optional): API authentication key. Default: `None`.
* **timeout**(float): Request timeout in seconds. Default: `30.0`.
* **risk_type**(str): Risk type identifier. Default: `"model_detection"`.

**Example**:

```python
from openjiuwen.core.security.guardrail import APIModelBackend, APIModelBackendConfig
from openjiuwen.core.security.guardrail.context import BertBinaryParser

# Using config
parser = BertBinaryParser(risk_type="prompt_injection")
config = APIModelBackendConfig(
    api_url="https://api.example.com/detect",
    parser=parser,
    api_key="your-api-key"
)
backend = APIModelBackend(config=config)

# Using keyword arguments
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

Backend that runs a model locally. Loads and runs model locally for security detection, uses lazy import to isolate torch/transformers dependencies.

**Parameters**:

* **config**(LocalModelBackendConfig, optional): Configuration dataclass. Default: `None`.
* **model_path**(str, optional): Path to the local model.
* **parser**(ModelOutputParser, optional): Parser to convert model output to RiskAssessment. Default: `None`.
* **device**(str): Device to run model on ("auto", "cpu", "cuda"). Default: `"auto"`.
* **risk_type**(str): Risk type identifier. Default: `"model_detection"`.

**Example**:

```python
from openjiuwen.core.security.guardrail import LocalModelBackend, LocalModelBackendConfig
from openjiuwen.core.security.guardrail.context import BertBinaryParser

# Using config
parser = BertBinaryParser()
config = LocalModelBackendConfig(
    model_path="/path/to/bert-classifier",
    parser=parser,
    device="cuda"
)
backend = LocalModelBackend(config=config)

# Using keyword arguments
backend = LocalModelBackend(
    model_path="/path/to/bert-classifier",
    parser=parser,
    device="cuda"
)
```

**Dependencies**:

Requires the following dependencies:
```
pip install torch transformers
```

### is_model_loaded

```python
is_model_loaded() -> bool
```

Check if model has been loaded.

**Returns**:

**bool**, True if model is loaded, False otherwise.

### get_model_info

```python
get_model_info() -> Dict[str, Any]
```

Get model information.

**Returns**:

**Dict[str, Any]**, dictionary with model status information including:
* `model_path`: Path to the model
* `device`: Device setting
* `model_loaded`: Whether model is loaded
* `has_model`: Whether model object exists
* `has_tokenizer`: Whether tokenizer object exists
