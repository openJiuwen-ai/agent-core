# openjiuwen.core.retrieval.indexing.processor.parser.auto_link_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.auto_link_parser.AutoLinkParser

按 URL 模式路由的链接解析器。可配置多组（模式或可调用对象, 解析器）路由，按顺序匹配，先匹配先使用。默认路由：微信公众号文章（mp.weixin.qq.com）→ WeChatArticleParser，其他 http(s) URL → WebPageParser。配合知识库使用时，可统一处理任意微信公众号或网页链接。

### __init__

```python
__init__(routes: List[Tuple[Union[re.Pattern, Callable[[str], bool]], Parser]] | None = None, **kwargs)
```

**参数**：

* **routes**：路由列表，每项为 (模式或可调用对象, Parser)。为 None 时使用默认路由（微信公众号 + 通用网页）。
* **kwargs**：其他参数传递给基类。

### supports

```python
supports(doc: str) -> bool
```

判断输入是否为支持的 http(s) URL 且被某条路由匹配。

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs) -> List[Document]
```

按路由选择对应解析器解析 URL，返回 Document 列表。
