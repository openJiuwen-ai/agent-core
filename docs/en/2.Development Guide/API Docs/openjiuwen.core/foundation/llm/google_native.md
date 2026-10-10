## Native Google text and function tools

Install the optional SDK with `pip install "openjiuwen[google]"`. Import and
register the client before creating a Google model configuration.

```python
import os
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.google_model_client import register_google_client

register_google_client()
model = Model(
    ModelClientConfig(
        client_provider="Google",
        api_key=os.environ["GOOGLE_API_KEY"],
        api_base="https://generativelanguage.googleapis.com",
    ),
    ModelRequestConfig(model="gemini-3.8-flash", max_tokens=4096),
)
# In an async function:
# response = await model.invoke("Write an abstract grounded in these results: ...")
# async for chunk in model.stream("Write an abstract: ..."):
#     consume(chunk)
```

The Google SDK sends native `generateContent` and `streamGenerateContent`
requests. Function declarations, calls, and results remain native Google
objects; SDK automatic tool execution is disabled. Existing agent tools own
execution. Assistant metadata preserves every returned content part, including
binary thought signatures, across serialization and stream merging. Retain
this metadata when storing conversation history.

`max_tokens` maps to `maxOutputTokens`; temperature, top-p, stop sequences,
timeout overrides, custom headers, and tool choice are supported. SDK-native
generation options such as `thinking_config` may be supplied as model request
extras. This adapter targets Gemini Developer API text and function tools;
media generation and Vertex AI authentication are outside its interface.
Thinking tokens are included in output usage and also reported separately;
provider totals and finish reasons are retained. Missing usage stays unknown.
SDK retries are disabled so framework retries own complete attempts.

Verification uses the real Google SDK with an offline HTTP transport; no API
key or paid requests are needed:

```sh
pytest tests/unit_tests/core/foundation/llm/test_google_model_client.py -q
```

Protocol references: [Google Gen AI SDK](https://googleapis.github.io/python-genai/),
[Gemini thinking and signatures](https://ai.google.dev/gemini-api/docs/thinking).
