# openjiuwen.core.retrieval.reranker.chat_reranker

## class openjiuwen.core.retrieval.reranker.chat_reranker.ChatReranker

Chat-based reranker implementation, supports any chat completion API that provide logprobs.

### How It Works

ChatReranker judges whether a document meets the query requirements through a chat completion API. The workflow is as follows:

1. **Build Judgment Task**: Combine the query and document into a prompt, asking the model to judge whether the document meets the query requirements, with only "yes" or "no" as valid answers.
2. **Get Probability Distribution**: Call a chat completion API that supports logprobs to obtain the probability distribution of the model for the "yes" and "no" tokens.
3. **Calculate Relevance Score**: Extract the probabilities of "yes" and "no" from logprobs, and calculate the relevance score: `confidence / (confidence + max_no_score)`, where `confidence` is the maximum probability of "yes" and `max_no_score` is the maximum probability of "no".

To ensure the model can correctly identify the "yes" and "no" tokens, ChatReranker uses the `logit_bias` parameter to bias these two tokens, which requires providing the corresponding token IDs (`yes_no_ids`).

### Obtaining yes_no_ids

`yes_no_ids` is a sequence of two integers corresponding to the token IDs of "yes" and "no" in the model's tokenizer. You can obtain them using the following methods:

#### Method 1: From HuggingFace Models

For models on HuggingFace, you can parse token IDs from the tokenizer.json file:

```python
import re
import tempfile
from pathlib import Path
from typing import Any


def load_tokens_from_huggingface(hf_repo: str, filename: str = "tokenizer.json", token: Any = None, **kwargs):
    """Load token config for reranking from huggingface"""
    import huggingface_hub

    hf_repo = hf_repo.rstrip("/")

    with tempfile.TemporaryDirectory() as tmp_dir:
        huggingface_hub.hf_hub_download(repo_id=hf_repo, filename=filename, local_dir=tmp_dir, token=token, **kwargs)
        tmp_file = Path(tmp_dir) / filename
        tokenizer_content = tmp_file.read_text(encoding="utf-8")
        token_ids, token_texts = [None] * 2, [None] * 2
        suffix_pattern = r"\s*([0-9]+)"
        for i, prefix_pattern in enumerate([r'"(yes)"\s*', r'"(no)"\s*']):
            token_match = re.search(prefix_pattern + ":" + suffix_pattern, tokenizer_content)
            if token_match is None:
                token_match = re.search(prefix_pattern + ":" + suffix_pattern, tokenizer_content, flags=re.IGNORECASE)
            token_ids[i] = int(token_match.group(2))
            token_texts[i] = token_match.group(1)
        return dict(zip(token_texts, token_ids))


# Usage example
MODEL = "Qwen/Qwen3-8B"
token_dict = load_tokens_from_huggingface(MODEL)
yes_no_ids = (token_dict["yes"], token_dict["no"])
```

#### Method 2: Using tiktoken (for OpenAI Models)

For models using tiktoken encoding (such as OpenAI models), you can directly use the tiktoken library:

```python
import tiktoken


def load_tokens_from_tiktoken(model_name: str):
    """Load token config for reranking from tiktoken"""
    encoding = tiktoken.encoding_for_model(model_name)
    token_dict = dict(
        yes=encoding.encode_single_token("yes"),
        no=encoding.encode_single_token("no")
    )
    return token_dict


# Usage example
MODEL = "gpt-4o"
token_dict = load_tokens_from_tiktoken(MODEL)
yes_no_ids = (token_dict["yes"], token_dict["no"])
```

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `showcase_chat_reranker.py` - ChatReranker examples
> - `utils/find_token.py` - Examples of obtaining token IDs for yes_no_ids

```python
ChatReranker(config: RerankerConfig, max_retries: int = 3, retry_wait: float = 0.1, extra_headers: Optional[dict] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

Initialize chat reranker.

**Parameters**:

* **config**(RerankerConfig): Reranker configuration, must include `yes_no_ids` field (`tuple[int, int]`).
* **max_retries**(int): Maximum retry count. Default: 3.
* **retry_wait**(float): Retry wait time in seconds. Default: 0.1.
* **extra_headers**(dict, optional): Additional request headers. Default: None.
* **verify**(bool | str | ssl.SSLContext): SSL verification settings. Default: True.
* **kwargs**: Variable arguments for passing additional configuration parameters.

**Note**:

ChatReranker is an experimental feature and requires the service to support logprobs functionality. The input document list must contain only one document.

### test_compatibility

```python
test_compatibility() -> bool
```

Test to see if selected service is compatible for chat-completion-based reranking.

**Returns**:

**bool**, returns True if the service is compatible, False otherwise.

### async rerank

```python
rerank(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

Rerank documents and return a mapping from document to relevance score.

**Parameters**:

* **query**(str): Query string.
* **doc**(list[str | Document]): List of documents to rerank (must contain only one document).
* **instruct**(bool | str): Whether to provide instruction to reranker, pass in a string for custom instruction. Default: True.
* **kwargs**: Variable arguments for passing additional configuration parameters.

**Returns**:

**dict[str, float]**, returns a mapping from document ID to relevance score.

### rerank_sync

```python
rerank_sync(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

Rerank documents and return a mapping from document to relevance score (synchronous version).

**Parameters**:

* **query**(str): Query string.
* **doc**(list[str | Document]): List of documents to rerank (must contain only one document).
* **instruct**(bool | str): Whether to provide instruction to reranker, pass in a string for custom instruction. Default: True.
* **kwargs**: Variable arguments for passing additional configuration parameters.

**Returns**:

**dict[str, float]**, returns a mapping from document ID to relevance score.
