# openjiuwen.extensions.external_provider

`openjiuwen.extensions.external_provider` provides a unified external-account provider abstraction and registry, for connecting external large-model accounts that require OAuth-style sign-in (as opposed to a static `api_key`).

Module responsibilities:

- `base`: defines the common protocols `ExternalAuthProvider` (auth status lookup, logout, access token refresh) and `ProviderModelCatalog` (model discovery). Login flows differ per provider, so the protocols only cover the stable operations shared once credentials exist.
- `registry`: provides registration, lookup, and creation of provider factories, with the built-in `OpenAIAccount` provider registered automatically.
- `openai_auth`: the first built-in implementation, connecting to the OpenAI account device-code OAuth login flow.

## Page Index

- [Base Protocols](base.md): `ExternalAuthProvider`, `ProviderModelCatalog`.
- [Registry](registry.md): `register_auth_provider`, `register_model_catalog`, `create_auth_provider`, etc.
- [openai_auth](openai_auth/README.md): the concrete implementation for OpenAI account OAuth login and model discovery.

## Usage Notes

- `openjiuwen.core.foundation.llm.model_clients.openai_account_model_client.OpenAIAccountModelClient` uses the auth capability provided by this module via `client_provider="OpenAIAccount"`. See the "Sign In with OpenAI Account OAuth" section under Basic Functions > Connect to LLM.
- Provider names are compared after `strip().lower()` normalization, so casing and surrounding whitespace do not affect matching.
- Custom providers can be registered via `register_auth_provider` / `register_model_catalog`; re-registering the same name requires passing `replace=True`, otherwise an `ExternalProviderRegistryError` is raised.
