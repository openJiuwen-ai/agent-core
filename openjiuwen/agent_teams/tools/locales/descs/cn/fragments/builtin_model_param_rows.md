| **builtin_model** | 内部 | 可选。从 `<builtin_model_catalog>` 中该 `cli_agent` 的条目里选择模型，使用 CLI 自身登录（如订阅）运行。与 `model_name` 互斥 |
| **effort** | 内部 | 可选。推理强度，必须是所选 `builtin_model` 的 `efforts` 之一；需同时指定 `builtin_model`。省略时取其 `default_effort` |
