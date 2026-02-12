# openjiuwen.core.security.guardrail

## class openjiuwen.core.security.guardrail.GuardrailBackend

```
class openjiuwen.core.security.guardrail.GuardrailBackend
```

检测后端的抽象基类。护栏后端实现具体的检测逻辑，用于检测特定类型的安全风险（如提示词注入、敏感数据泄露等）。

### async analyze

```python
async analyze(data: Dict[str, Any]) -> RiskAssessment
```

分析数据以检测安全风险。此方法实现核心检测逻辑，接收事件数据并返回风险评估结果。

**参数**：

* **data**(Dict[str, Any])：要分析的事件数据字典，包含检测所需的信息。

**返回**：

**RiskAssessment**，描述检测到的风险。

**异常**：

* 任何异常都会被护栏框架捕获，检测将失败（采取保守策略）。

**示例**：

```python
from openjiuwen.core.security.guardrail import GuardrailBackend, RiskAssessment, RiskLevel

class PromptInjectionBackend(GuardrailBackend):
    """提示词注入检测后端示例"""

    async def analyze(self, data: dict) -> RiskAssessment:
        text = data.get("text", "")

        # 简单的检测逻辑示例
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
