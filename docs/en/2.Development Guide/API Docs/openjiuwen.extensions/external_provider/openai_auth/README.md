# openjiuwen.extensions.external_provider.openai_auth

`openai_auth` is the built-in `external_provider` implementation that connects to the OpenAI account backend (`chatgpt.com/backend-api/codex`), providing device-code OAuth login, credential storage, and model discovery.

On import, it registers itself with the `external_provider` registry as the `OpenAIAccount` provider (via `register_auth_provider` / `register_model_catalog`), which is used by `openjiuwen.core.foundation.llm.model_clients.openai_account_model_client.OpenAIAccountModelClient`.

## Page Index

- [OpenAI Account Auth](openai_account_auth.md): `OpenAIAccountAuthManager` and the login/refresh/logout-related data classes.
- [OpenAI Account Model Discovery](openai_account_models.md): `OpenAIAccountModelCatalog`.

## Usage Notes

- Credentials are saved by default to `~/.openjiuwen/auth.json` (configurable via the `OPENJIUWEN_HOME` or `OPENJIUWEN_AUTH_FILE` environment variables). Writes set file permissions to `0o600` and use `filelock` to prevent concurrent write conflicts.
- `OpenAIAccountAuthManager.login_with_device_code()` blocks while waiting for the user to complete sign-in in a browser, up to `max_wait_seconds` (default 15 minutes).
- You can also use the command-line script `python -m openjiuwen.extensions.external_provider.openai_auth.openai_account_login {login|status|logout}` to test the login flow without depending on the harness CLI.
