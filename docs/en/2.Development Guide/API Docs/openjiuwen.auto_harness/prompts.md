# openjiuwen.auto_harness.prompts

Auto Harness prompt assembly module, responsible for building the various sections of the Agent system prompt. Contains generation logic for sections including identity definition, platform adaptation, CI gate rules, and experience store active context.

Submodules:
- `sections`: Prompt section builder functions

---

## openjiuwen.auto_harness.prompts.sections.build_auto_harness_sections

```python
def build_auto_harness_sections(
    *,
    ci_gate_rules: str = '',
    wisdom: str = '',
) -> List[PromptSection]
```

Build prompt sections for the Auto Harness Agent. The generated section list is sorted by priority and injected into `SystemPromptBuilder`. Includes the following sections:

1. **identity** (priority 10): Agent identity definition, loaded from `identity.md`.
2. **platform_adaptation** (priority 89): Current runtime platform and shell information, including cross-platform command differences and Python coding conventions.
3. **ci_gate** (priority 20): CI gate rules, only generated when `ci_gate_rules` is non-empty.
4. **wisdom** (priority 30): Experience store active context, only generated when `wisdom` is non-empty.

**Parameters**:
* **ci_gate_rules**(`str`): CI gate rules text (from `ci_gate.yaml`), default empty.
* **wisdom**(`str`): Experience store synthesized active context text, default empty.

**Returns**: A list of `PromptSection` objects, ready for injection into the system prompt builder.
