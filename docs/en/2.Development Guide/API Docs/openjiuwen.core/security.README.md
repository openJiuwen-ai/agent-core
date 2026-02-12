# security

The openJiuwen security module provides the Guardrail security detection and interception framework.

## guardrail (Guardrail Framework)

The guardrail framework is used to monitor key events in the Agent execution flow and perform security detection when events are triggered, to prevent security risks such as prompt injection and sensitive data leaks.

### Core Classes

| CLASS                                                                                                       | DESCRIPTION                                                  |
|-------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------|
| [GuardrailBackend](./guardrail/backends.md#class-openjiuwencoresecurityguardrailguardrailbackend)   | Detection backend abstract interface for custom detection logic. |
| [BaseGuardrail](./guardrail/guardrail.md#class-openjiuwencoresecurityguardrailbaseguardrail)       | Guardrail abstract base class for creating custom guardrails. |
| [UserInputGuardrail](./guardrail/builtin.md#class-openjiuwencoresecurityguardrailuserinputguardrail) | User input validation guardrail, monitors user input events. |

### Quick Start

1. **Implement detection backend**: Inherit from `GuardrailBackend` to implement custom detection logic
2. **Configure guardrails**: Use built-in guardrail classes (e.g., `UserInputGuardrail`) and set the detection backend
3. **Register to framework**: Register guardrails to the callback framework for automatic detection triggering

For detailed usage, please refer to the complete documentation of each class.