# openjiuwen.core.security.guardrail

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

        # Simple detection logic example
        injection_patterns = [
            "ignore previous instructions",
            "disregard all prior commands",
            "forget what you were told"
        ]

        has_risk = any(pattern in text.lower() for pattern in injection_patterns)

        return RiskAssessment(
            has_risk=has_risk,
            risk_level=RiskLevel.HIGH if has_risk else RiskLevel.SAFE,
            risk_type="prompt_injection" if has_risk else None,
            confidence=0.9 if has_risk else 0.0,
            details={"matched": injection_patterns} if has_risk else {}
        )
```
