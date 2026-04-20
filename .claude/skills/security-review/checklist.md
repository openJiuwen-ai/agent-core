# Security Review Checklist (机器可读版)

此文件是 `SKILL.md` 中 10 类安全检查的机器可遍历版本，供自动化工具调用。

## 1. Secrets Management

| ID | 检查项 |
|----|--------|
| SEC-1.1 | 无 API keys、tokens、passwords 硬编码在 `.py` 文件中 |
| SEC-1.2 | 所有 secrets 通过 `os.getenv()` 从环境变量加载 |
| SEC-1.3 | `.env` 文件未提交（已在 `.gitignore` 中） |
| SEC-1.4 | `settings.json` deny 规则阻止读取 `.env` 和 `**/secrets/**` |
| SEC-1.5 | 测试文件使用 mock 默认值：`os.getenv("KEY", "mock-key-for-tests")` |

## 2. Input Validation

| ID | 检查项 |
|----|--------|
| SEC-2.1 | 用户提供的文件路径通过 `safe_path` 工具验证 |
| SEC-2.2 | 包含 `..` 或超出允许范围的路径被拒绝 |
| SEC-2.3 | Shell 命令通过参数化 API 构造，不使用字符串拼接 |
| SEC-2.4 | URL 参数和查询字符串在使用前经过清理 |

## 3. SQL Injection

| ID | 检查项 |
|----|--------|
| SEC-3.1 | 无 SQL 字符串插值：`f"SELECT * FROM {table}"` 禁止 |
| SEC-3.2 | 所有 SQL 使用参数化占位符：`"WHERE id = ?", (id,)` |
| SEC-3.3 | 动态表/列名通过 allowlist 验证 |

## 4. Authentication and RBAC

| ID | 检查项 |
|----|--------|
| SEC-4.1 | 所有 agent 能力通过 `core/security/` 中的权限检查门控 |
| SEC-4.2 | `openjiuwen/core/security/` 中的 guardrails 在执行前验证权限 |
| SEC-4.3 | 无 capability 因备用代码路径缺少检查而被绕过 |
| SEC-4.4 | 强制资源限制（速率限制、并发请求限制） |

## 5. Prompt Injection

| ID | 检查项 |
|----|--------|
| SEC-5.1 | 用户提供的字符串在未清理的情况下不直接拼接到 system prompts |
| SEC-5.2 | Prompt 模板使用占位符隔离（用户内容与指令分离） |
| SEC-5.3 | Rail 输出在传递给下游组件前经过验证 |
| SEC-5.4 | Security rail 正确阻止危险模式 |

## 6. CSRF / Request Validation

| ID | 检查项 |
|----|--------|
| SEC-6.1 | `core/session/` 验证请求源自合法 session |
| SEC-6.2 | Session IDs 不可猜测（使用 `secrets.token_urlsafe()`） |
| SEC-6.3 | Session 状态变更具有幂等性或有事务语义保护 |
| SEC-6.4 | 无 session 上下文则无法访问状态修改操作 |

## 7. Rate Limiting

| ID | 检查项 |
|----|--------|
| SEC-7.1 | `Runner.resource_mgr` 对并发 agent 执行强制限制 |
| SEC-7.2 | 长时间运行的 session 内存使用有界 |
| SEC-7.3 | 压缩触发器防止无限制的上下文增长 |
| SEC-7.4 | 每个 session 强制执行 tool call 频率限制 |

## 8. Sensitive Data in Logs

| ID | 检查项 |
|----|--------|
| SEC-8.1 | 日志中无 API keys、tokens 或 passwords |
| SEC-8.2 | 使用带显式字段名的结构化日志，不用 f-string 插值 |
| SEC-8.3 | 错误消息不包含敏感用户数据 |
| SEC-8.4 | 使用 `openjiuwen.core.common.logging` 而非 `print()` |

## 9. Dependency Security

| ID | 检查项 |
|----|--------|
| SEC-9.1 | 新依赖经过已知 CVE 检查：`pip-audit` |
| SEC-9.2 | 新网络依赖经过安全影响审查 |
| SEC-9.3 | `bandit -r openjiuwen/ -ll` 通过（无 HIGH/CRITICAL 发现） |
| SEC-9.4 | `core/sys_operation/` 和 `core/security/` 中的第三方代码最小化 |

## 10. Sandbox Isolation

| ID | 检查项 |
|----|--------|
| SEC-10.1 | 文件操作遵守路径作用域（无通过 `../` 逃逸） |
| SEC-10.2 | Shell 执行在受限环境中运行 |
| SEC-10.3 | 网络访问显式允许/拒绝，非默认开放 |
| SEC-10.4 | 每次操作后（即使失败）都运行清理 |
| SEC-10.5 | 用户面向操作的 interrupt/confirm 流程保留 |
