# openjiuwen.auto_harness.prompts

Auto Harness prompt 组装模块，负责构建 Agent 系统提示词中的各个 section。包含身份定义、平台适配、CI 门控规则和经验库活跃上下文等 section 的生成逻辑。

子模块：
- `sections`：prompt section 构建函数

---

## openjiuwen.auto_harness.prompts.sections.build_auto_harness_sections

```python
def build_auto_harness_sections(
    *,
    ci_gate_rules: str = '',
    wisdom: str = '',
) -> List[PromptSection]
```

构建 Auto Harness Agent 的 prompt sections。生成的 section 列表按优先级排序，注入到 `SystemPromptBuilder` 中。包含以下 section：

1. **identity**（优先级 10）：Agent 身份定义，从 `identity.md` 加载。
2. **platform_adaptation**（优先级 89）：当前运行平台和 Shell 信息，含跨平台命令差异和 Python 编码规范。
3. **ci_gate**（优先级 20）：CI 门控规则，仅在 `ci_gate_rules` 非空时生成。
4. **wisdom**（优先级 30）：经验库活跃上下文，仅在 `wisdom` 非空时生成。

**参数**：
* **ci_gate_rules**(`str`)：CI 门控规则文本（来自 `ci_gate.yaml`），默认为空。
* **wisdom**(`str`)：经验库合成的活跃上下文文本，默认为空。

**返回**：`PromptSection` 列表，可直接注入到系统提示词构建器。
