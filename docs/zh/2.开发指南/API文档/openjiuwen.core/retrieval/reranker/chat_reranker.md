# openjiuwen.core.retrieval.reranker.chat_reranker

## class openjiuwen.core.retrieval.reranker.chat_reranker.ChatReranker

基于 chat completions 的重排序器实现，支持任何提供logprobs的 chat completions API。

### 工作原理

ChatReranker 通过 chat completions API 来判断文档是否满足查询要求。其工作流程如下：

1. **构建判断任务**：将查询和文档组合成提示，要求模型判断文档是否满足查询要求，且只能回答 "yes" 或 "no"。
2. **获取概率分布**：调用支持 logprobs 的 chat completions API，获取模型对 "yes" 和 "no" 两个 token 的概率分布。
3. **计算相关性得分**：从 logprobs 中提取 "yes" 和 "no" 的概率，计算相关性得分：`confidence / (confidence + max_no_score)`，其中 `confidence` 是 "yes" 的最大概率，`max_no_score` 是 "no" 的最大概率。

为了确保模型能够正确识别 "yes" 和 "no" token，ChatReranker 使用 `logit_bias` 参数来偏向这两个 token，这需要提供对应的 token ID（`yes_no_ids`）。

### 获取 yes_no_ids

`yes_no_ids` 是一个包含两个整数的序列，分别对应模型 tokenizer 中 "yes" 和 "no" 的 token ID。可以通过以下方法获取：

#### 方法一：从 HuggingFace 模型获取

对于 HuggingFace 上的模型，可以从 tokenizer.json 文件中解析 token ID：

```python
import re
import tempfile
from pathlib import Path
from typing import Any


def load_tokens_from_huggingface(hf_repo: str, filename: str = "tokenizer.json", token: Any = None, **kwargs):
    """从 HuggingFace 加载重排序所需的 token 配置"""
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


# 使用示例
MODEL = "Qwen/Qwen3-8B"
token_dict = load_tokens_from_huggingface(MODEL)
yes_no_ids = (token_dict["yes"], token_dict["no"])
```

#### 方法二：使用 tiktoken（适用于 OpenAI 模型）

对于使用 tiktoken 编码的模型（如 OpenAI 模型），可以直接使用 tiktoken 库获取：

```python
import tiktoken


def load_tokens_from_tiktoken(model_name: str):
    """从 tiktoken 加载重排序所需的 token 配置"""
    encoding = tiktoken.encoding_for_model(model_name)
    token_dict = dict(
        yes=encoding.encode_single_token("yes"),
        no=encoding.encode_single_token("no")
    )
    return token_dict


# 使用示例
MODEL = "gpt-4o"
token_dict = load_tokens_from_tiktoken(MODEL)
yes_no_ids = (token_dict["yes"], token_dict["no"])
```

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `showcase_chat_reranker.py` - ChatReranker 示例
> - `utils/find_token.py` - 获取 yes_no_ids 的 token ID 示例

```python
ChatReranker(config: RerankerConfig, max_retries: int = 3, retry_wait: float = 0.1, extra_headers: Optional[dict] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

初始化LLM重排序器。

**参数**：

* **config**(RerankerConfig)：重排序器配置，必须包含`yes_no_ids`字段（`tuple[int, int]`）。
* **max_retries**(int)：最大重试次数。默认值：3。
* **retry_wait**(float)：重试等待时间（秒）。默认值：0.1。
* **extra_headers**(dict, 可选)：额外的请求头。默认值：None。
* **verify**(bool | str | ssl.SSLContext)：SSL验证设置。默认值：True。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**说明**：

ChatReranker是实验性功能，需要服务支持logprobs功能。输入文档列表必须只包含一个文档。

### test_compatibility

```python
test_compatibility() -> bool
```

测试所选服务是否兼容基于 chat completions 的重排序。

**返回**：

**bool**，如果服务兼容则返回True，否则返回False。

### async rerank

```python
rerank(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

重排序文档并返回文档到相关性得分的映射。

**参数**：

* **query**(str)：查询字符串。
* **doc**(list[str | Document])：待重排序的文档列表（必须只包含一个文档）。
* **instruct**(bool | str)：是否提供指令给重排序器，传入字符串可自定义指令。默认值：True。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**返回**：

**dict[str, float]**，返回文档ID到相关性得分的映射。

### rerank_sync

```python
rerank_sync(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

重排序文档并返回文档到相关性得分的映射（同步版本）。

**参数**：

* **query**(str)：查询字符串。
* **doc**(list[str | Document])：待重排序的文档列表（必须只包含一个文档）。
* **instruct**(bool | str)：是否提供指令给重排序器，传入字符串可自定义指令。默认值：True。
* **kwargs**：可变参数，用于传递其他额外的配置参数。

**返回**：

**dict[str, float]**，返回文档ID到相关性得分的映射。
