# openjiuwen.extensions.external_provider

`openjiuwen.extensions.external_provider` 提供统一的外部账户 Provider 抽象与注册表，用于接入需要 OAuth 等免密登录方式的外部大模型账户（区别于基于静态 `api_key` 的接入方式）。

模块职责：

- `base`：定义通用协议 `ExternalAuthProvider`（鉴权状态查询、登出、访问令牌刷新）与 `ProviderModelCatalog`（模型发现）。具体 Provider 的登录流程各不相同，因此协议只覆盖凭据建立后可共享的稳定操作。
- `registry`：提供 Provider 工厂的注册、查找与创建能力，并内置注册了 `OpenAIAccount` Provider。
- `openai_auth`：首个内置实现，对接 OpenAI 账户的设备码（Device Code）OAuth 登录流程。

## 页面索引

- [基础协议](base.md)：`ExternalAuthProvider`、`ProviderModelCatalog`。
- [注册表](registry.md)：`register_auth_provider`、`register_model_catalog`、`create_auth_provider` 等。
- [openai_auth](openai_auth/README.md)：OpenAI 账户 OAuth 登录与模型发现的具体实现。

## 使用要点

- `openjiuwen.core.foundation.llm.model_clients.openai_account_model_client.OpenAIAccountModelClient` 通过 `client_provider="OpenAIAccount"` 使用本模块提供的鉴权能力，详见「基础功能 > 接入大模型」文档中的「使用 OpenAI 账户 OAuth 登录」章节。
- Provider 名称按 `strip().lower()` 归一化后比较，大小写和首尾空白不影响匹配结果。
- 自定义 Provider 可通过 `register_auth_provider` / `register_model_catalog` 注册；重复注册同名 Provider 时需显式传入 `replace=True`，否则会抛出 `ExternalProviderRegistryError`。
