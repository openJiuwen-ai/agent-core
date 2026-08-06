# openjiuwen.extensions.external_provider.openai_auth

`openai_auth` 是 `external_provider` 的内置实现，对接 OpenAI 账户后端（`chatgpt.com/backend-api/codex`），提供设备码（Device Code）OAuth 登录、凭据存储与模型发现能力。

启动时会自动向 `external_provider` 注册表注册为 `OpenAIAccount` Provider（`register_auth_provider` / `register_model_catalog`），供 `openjiuwen.core.foundation.llm.model_clients.openai_account_model_client.OpenAIAccountModelClient` 使用。

## 页面索引

- [OpenAI 账户鉴权](openai_account_auth.md)：`OpenAIAccountAuthManager` 及登录/刷新/登出相关的数据类。
- [OpenAI 账户模型发现](openai_account_models.md)：`OpenAIAccountModelCatalog`。

## 使用要点

- 凭据默认保存在 `~/.openjiuwen/auth.json`（可通过环境变量 `OPENJIUWEN_HOME` 或 `OPENJIUWEN_AUTH_FILE` 自定义），写入时文件权限被设置为 `0o600`，且通过 `filelock` 防止并发写入冲突。
- `OpenAIAccountAuthManager.login_with_device_code()` 会阻塞等待用户在浏览器中完成登录，默认最长等待 15 分钟（`max_wait_seconds`）。
- 也可以使用命令行脚本 `python -m openjiuwen.extensions.external_provider.openai_auth.openai_account_login {login|status|logout}` 在不依赖 harness CLI 的情况下测试登录流程。
