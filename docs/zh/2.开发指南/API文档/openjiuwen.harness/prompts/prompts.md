# prompts

系统提示词构建器，负责 DeepAgent 的提示词组装、模式过滤和诊断报告。

---

## class PromptMode

```python
class PromptMode(str, Enum):
    FULL = "full"
    MINIMAL = "minimal"
    NONE = "none"
```

DeepAgent 的提示词组装模式枚举。

| 值 | 说明 |
|---|---|
| `FULL` | 完整模式，包含所有提示词段落 |
| `MINIMAL` | 精简模式，仅保留核心段落（IDENTITY、SAFETY、SKILLS、TOOLS、TASK_TOOL、RUNTIME、MEMORY） |
| `NONE` | 无模式，仅输出 IDENTITY 段落 |

---

## class SystemPromptBuilder

```python
class SystemPromptBuilder(BaseSystemPromptBuilder):
    def __init__(
        self,
        language: str = DEFAULT_LANGUAGE,
        mode: PromptMode = PromptMode.FULL,
    ): ...
```

DeepAgent 提示词构建器，继承自核心 `BaseSystemPromptBuilder`，增加模式过滤和诊断功能。

**属性**:

| 属性 | 类型 | 说明 |
|---|---|---|
| `mode` | `PromptMode` | 当前提示词组装模式 |
| `language` | `str` | 提示词语言（`"cn"` 或 `"en"`） |

---

### build

```python
def build(self) -> str
```

根据当前 DeepAgent 提示词模式构建提示词。

- `FULL`: 包含所有已注册段落。
- `MINIMAL`: 仅包含核心段落。
- `NONE`: 仅包含 IDENTITY 段落。

**返回值**: `str` — 组装后的系统提示词文本。

---

### build_report

```python
def build_report(self) -> PromptReport
```

返回当前构建器状态的诊断报告。

**返回值**: `PromptReport` — 包含字符数、token 估算和段落明细的报告。

---

## class PromptReport

```python
@dataclass
class PromptReport:
    total_chars: int
    estimated_tokens: int
    section_count: int
    sections: List[SectionInfo] = field(default_factory=list)
    mode: str = "full"
    language: str = "cn"
```

系统提示词的诊断报告。

**属性**:

| 属性 | 类型 | 说明 |
|---|---|---|
| `total_chars` | `int` | 总字符数 |
| `estimated_tokens` | `int` | 估算 token 数（中文 ≈ 字符数/2.5，英文 ≈ 字符数/4） |
| `section_count` | `int` | 段落总数 |
| `sections` | `List[SectionInfo]` | 段落明细列表 |
| `mode` | `str` | 提示词模式 |
| `language` | `str` | 提示词语言 |

---

### from_builder

```python
@classmethod
def from_builder(cls, builder: SystemPromptBuilder) -> PromptReport
```

从构建器的当前状态创建报告。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `builder` | `SystemPromptBuilder` | 提示词构建器实例 |

**返回值**: `PromptReport` — 诊断报告。

---

### to_dict

```python
def to_dict(self) -> Dict
```

序列化为普通字典。

**返回值**: `Dict` — 包含所有报告字段的字典。

---

### summary

```python
def summary(self) -> str
```

人类可读的单行摘要。

**返回值**: `str` — 格式如 `[PromptReport] mode=full lang=cn sections=8 chars=5000 est_tokens≈2000`。

---

## class SectionInfo

```python
@dataclass
class SectionInfo:
    name: str
    priority: int
    char_count: int
```

单个段落的轻量快照。

**属性**:

| 属性 | 类型 | 说明 |
|---|---|---|
| `name` | `str` | 段落名称 |
| `priority` | `int` | 段落优先级 |
| `char_count` | `int` | 字符数 |

---

## function sanitize_path

```python
def sanitize_path(path: str) -> str
```

清理用户可控的路径字符串。移除可用于提示词注入的特殊字符，同时保留正常的路径分隔符。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | `str` | 原始路径字符串 |

**返回值**: `str` — 清理后的路径。

---

## function sanitize_user_content

```python
def sanitize_user_content(content: str, max_len: int = 2000) -> str
```

从用户内容中移除注入风险字符并限制长度。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `content` | `str` | 原始用户提供的文本 |
| `max_len` | `int` | 返回字符串的最大长度，默认 `2000` |

**返回值**: `str` — 清理后的字符串。

---

## function resolve_language

```python
def resolve_language(config_language: Optional[str] = None) -> str
```

解析提示词语言。优先级：配置参数 > 环境变量 `AGENT_PROMPT_LANGUAGE` > 默认值 `"cn"`。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `config_language` | `Optional[str]` | 配置中指定的语言 |

**返回值**: `str` — 解析后的语言代码（`"cn"` 或 `"en"`）。

---

## function resolve_mode

```python
def resolve_mode(config_mode: Optional[str] = None) -> PromptMode
```

解析提示词模式。默认为 `PromptMode.FULL`。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `config_mode` | `Optional[str]` | 配置中指定的模式 |

**返回值**: `PromptMode` — 解析后的提示词模式。
