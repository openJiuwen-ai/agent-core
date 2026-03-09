# openjiuwen.core.retrieval.query_rewriter

## class openjiuwen.core.retrieval.query_rewriter.query_rewriter.QueryRewriter

Query Rewriter. Implements pronouns resolution, formalization of expression, and resolving ambiguity in retrieval requests based on the context stored in the ContextEngine. It is used to improve the accuracy of retrieving results in openJiuwen.

```python
QueryRewriter(cfg: ModelConfig, ctx: ModelContext, compress_range: int = 20, prompt_lang: str = "zh")
```

**Parameters**
* **cfg**(ModelConfig): LLM config (model_provider + model_info) for compression and rewrite calls. Required.
* **ctx**(ModelContext): Current session model context implementing get_messages/set_messages. Required.
* **compress_range**(int): Threshold to activate chat history compression (number of message rounds). Values less than 1 are treated as 1. Default: 20.
* **prompt_lang**(str): Language option for QR prompt template (e.g. `"zh"` → `intention_completion_zh.md`). Valid values are `"zh"` (Chinese) and `"en"` (English) only. If not provided or empty, `"zh"` is used. Default: "zh".

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `showcase_query_rewriter.py` — Query Rewriter usage in multi-turn retrieval

### async rewrite

```python
async def rewrite(self, query: str) -> dict
```

Rewrite query according to prompt combined with past messages. When the history message count reaches `compress_range`, a compression step runs first (history is summarized and replaced) to control token size. This is an async method; use `await rewriter.rewrite(query)` when calling.

**Parameters**

* **query**(str): Raw query provided by user.

**Returns**

**dict**, return a json-structured query after the raw query being rewritten. The structure is defined as below.

```json
{
  "before": "<original user query, identical to the input query>",
  "intention": "<detected user intention summarized in one sentence>",
  "standalone_query": "<rewritten complete single-sentence query for retrieval>",
  "references": { "<referential phrase>": "<resolved entity or unresolved note>" },
  "missing": ["<missing item 1>", "<missing item 2>"],
  "typo": [
    { "original": "<original token>", "corrected": "<corrected token>", "reason": "<brief justification>" }
  ],
  "gibberish": [],
  "from_history": "<evidence range or key phrases used>"
}
```

**Example**

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.query_rewriter.query_rewriter import QueryRewriter
>>> from openjiuwen.core.foundation.llm.schema.mode_info import BaseModelInfo, ModelConfig
>>> from openjiuwen.core.context_engine.context.context import SessionModelContext
>>> from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, UserMessage


>>> async def run():
...     #Configure a rewriter
...     config = ModelConfig(...)
...     context = SessionModelContext(...)
...     #Set the chat history
...     context.add_messages([
...         UserMessage(content="Please recommend me some resource to learn about AI"),
...         AssistantMessage(content="Of course! Resources of AI are devided into 'Foundamental Theories' and 'Practical Projects' two classes, which one you prefer?"),
...         UserMessage(content="I think it's 'Practical Projects'. And it will be better if it is related to Python"),
...         AssistantMessage(content="Recommendations of practical projects of AI in Python ：1. 《Python深度学习》(Author: Chollet); 2. AI competition for beginners from Kaggle; 3. Tutorial of Transformer of HuggingFace. Do you need me to guid you to learn?"),
...     ])
...     # Create rewriter instance
...     rewriter = QueryRewriter(cfg=config, ctx=context,)
...     # Rewrite query with references and gibberish
...     query = await rewriter.rewrite(query="No thanks，Just give me the link of those reso@#￥%……&*（urces.")
...     print(query)
>>> asyncio.run(run())
{
  "before": "No thanks，Just give me the link of those reso@#￥%……&*（urces.",
  "intention": "User may want to have the link of the study resource",
  "standalone_query": "No thanks，Just give me the link of resources of practical projects of AI in Python.",
  "references": { "those": "practical projects of AI in Python." },
  "missing": [],
  "typo": [],
  "gibberish": ["@#￥%……&*（"],
  "from_history": "Recommendations of practical projects of AI in Python ：1. 《Python深度学习》(Author: Chollet); 2. AI competition for beginners from Kaggle; 3. Tutorial of Transformer of HuggingFace. Do you need me to guid you to learn?"
}
```