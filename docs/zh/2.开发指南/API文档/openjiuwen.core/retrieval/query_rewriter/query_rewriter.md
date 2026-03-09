# openjiuwen.core.retrieval.query_rewriter

## class openjiuwen.core.retrieval.query_rewriter.query_rewriter.QueryRewriter

Query 重写器。基于 ContextEngine 存储的上下文实现检索请求中指代词消解、去口语化、歧义修正，用以在 openJiuwen 中提升检索请求 query 的精度。

```python
QueryRewriter(cfg: ModelConfig, ctx: ModelContext, compress_range: int = 20, prompt_lang: str = "zh")
```

**参数**
* **cfg**(ModelConfig)：用于压缩与重写调用的 LLM 配置（model_provider + model_info）。必填。
* **ctx**(ModelContext)：当前会话的模型上下文实例，需实现 get_messages/set_messages。必填。
* **compress_range**(int)：触发压缩的多轮对话条数阈值。小于 1 时按 1 处理。默认值：20。
* **prompt_lang**(str)：重写器提示词模板语言（如 `"zh"` 对应 `intention_completion_zh.md`）。支持 `"zh"`（中文）与 `"en"`（英文）。未提供或为空时使用 `"zh"`。默认值："zh"。

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `showcase_query_rewriter.py` — 多轮检索场景下的 Query 重写器用法

### async rewrite

```python
async def rewrite(self, query: str) -> dict
```

根据提示词与模型对话历史重写检索请求 query。当历史消息条数达到 `compress_range` 时，会先执行压缩（将历史汇总并替换）以控制 token 规模。此为异步方法，调用时需使用 `await rewriter.rewrite(query)`。

**参数**

* **query**(str)：用户提供的原始检索请求。

**返回**

**dict**，返回 json 格式的重写后的检索请求，格式如下。

```json
{
  "before": "<原始用户 query，与输入 query 一致>",
  "intention": "<用一句话概括的用户意图>",
  "standalone_query": "<完成指代消解与补全后的完整单句 query>",
  "references": { "<指代短语>": "<指代对象或无法确定说明>" },
  "missing": ["<缺失项1>", "<缺失项2>"],
  "typo": [
    { "original": "<原词>", "corrected": "<修正后>", "reason": "<简短依据>" }
  ],
  "gibberish": [],
  "from_history": "<使用的历史依据范围或关键短语>"
}
```

**样例**

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.query_rewriter.query_rewriter import QueryRewriter
>>> from openjiuwen.core.foundation.llm.schema.mode_info import BaseModelInfo, ModelConfig
>>> from openjiuwen.core.context_engine.context.context import SessionModelContext
>>> from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, UserMessage


>>> async def run():
...     #配置Query重写器参数
...     config = ModelConfig(...)
...     context = SessionModelContext(...)
...     #设定并添加当前环节模型的历史消息
...     context.add_messages([
...         UserMessage(content="推荐一些人工智能入门的学习资源"),
...         AssistantMessage(content="当然可以！人工智能入门学习资源分「基础理论」和「实战项目」两类，你更倾向于哪种？"),
...         UserMessage(content="我想要实战类的，最好是Python相关的"),
...         AssistantMessage(content="Python相关的AI实战入门资源推荐：1. 《Python深度学习》（Chollet著）；2. Kaggle上的入门级AI竞赛；3. HuggingFace的Transformers库教程。需要我提供具体的学习路径吗？"),
...     ])
...     #创建重写器实例
...     rewriter = QueryRewriter(cfg=config, ctx=context,)
...     # 使用重写器对有指代的检索请求改写
...     query = await rewriter.rewrite(query="不用路径，先给我这些资@#￥%……&*（源的链接吧")
...     print(query)
>>> asyncio.run(run())
{
  "before": "不用路径，先给我这些资@#￥%……&*（源的链接吧",
  "intention": "用户想要Python相关的AI实战入门资源的链接",
  "standalone_query": "不用路径，先给我Python相关的AI实战入门资源的链接吧",
  "references": { "这些": "Python相关的AI实战入门资源" },
  "missing": [],
  "typo": [],
  "gibberish": ["@#￥%……&*（"],
  "from_history": "Python相关的AI实战入门资源推荐：1. 《Python深度学习》（Chollet著）；2. Kaggle上的入门级AI竞赛；3. HuggingFace的Transformers库教程。需要我提供具体的学习路径吗？"
}
```