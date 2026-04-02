# openjiuwen.harness.prompts

## enum openjiuwen.harness.prompts.PromptMode

```python
class PromptMode(str, Enum)
```

Controls how much of the system prompt is assembled.

| Value | Description |
|---|---|
| `FULL` | Include all prompt sections (workspace, tools, rails, identity, guidelines). |
| `MINIMAL` | Include only essential sections (identity, core guidelines). |
| `NONE` | Do not assemble a system prompt; use only the user-provided `system_prompt`. |

---

## class openjiuwen.harness.prompts.SystemPromptBuilder

Assembles the system prompt for a `DeepAgent` by composing sections for identity, workspace, tools, rails, language, and custom instructions.

### method build

```python
build() -> str
```

Build and return the complete system prompt string.

**Returns**:

**str**: The assembled system prompt.

### method build_report

```python
build_report() -> PromptReport
```

Build the system prompt and return a detailed report with per-section breakdowns.

**Returns**:

**[PromptReport](#class-openjiuwenharnesspromptspromptreport)**: A report containing the prompt text and metadata.

---

## class openjiuwen.harness.prompts.PromptReport

Diagnostic report produced by `SystemPromptBuilder.build_report()`.

**Attributes**:

- **total_chars** (int): Total character count of the assembled prompt.
- **estimated_tokens** (int): Estimated token count (heuristic: `total_chars / 4`).
- **section_count** (int): Number of sections included in the prompt.
- **sections** (list[dict]): Per-section details, each with `name`, `char_count`, and `content` keys.
- **mode** ([PromptMode](#enum-openjiuwenharnesspromptspromptmode)): The prompt mode that was used.
- **language** (str): The language that was used.

### method summary

```python
summary() -> str
```

Return a human-readable summary of the prompt report.

**Returns**:

**str**: A multi-line summary string including total chars, estimated tokens, section count, and per-section sizes.

---

## function openjiuwen.harness.prompts.sanitize_path

```python
sanitize_path(path: str) -> str
```

Remove or escape potentially dangerous characters from a file path before including it in a prompt.

**Parameters**:

- **path** (str): The raw file path.

**Returns**:

**str**: The sanitized path string.

---

## function openjiuwen.harness.prompts.sanitize_user_content

```python
sanitize_user_content(
    content: str,
    max_len: int = 2000,
) -> str
```

Truncate and sanitize user-provided content before injecting it into a system prompt.

**Parameters**:

- **content** (str): The raw user content.
- **max_len** (int, optional): Maximum allowed length in characters. Default: `2000`.

**Returns**:

**str**: The sanitized and possibly truncated content.
