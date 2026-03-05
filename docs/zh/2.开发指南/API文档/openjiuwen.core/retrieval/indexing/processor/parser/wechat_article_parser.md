# openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser.WeChatArticleParser

微信公众号文章解析器。从 URL（如 https://mp.weixin.qq.com/s/...）抓取并解析公众号文章页为 Document，使用 BeautifulSoup 解析 HTML。可与 [AutoParser](./auto_parser.md) 或 [AutoLinkParser](./auto_link_parser.md) 配合，按 URL 自动选择解析器。

```python
WeChatArticleParser(timeout: float = 30.0, user_agent: str = DEFAULT_USER_AGENT, **kwargs)
```

**参数**：

* **timeout**(float, 可选)：请求超时时间（秒）。默认值：30.0。
* **user_agent**(str, 可选)：HTTP User-Agent 头。默认值：内置浏览器 UA。
* **kwargs**(Any)：可变参数，传递给基类。

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

解析微信公众号文章 URL，抓取页面并提取正文（js_content 区域），返回 Document 列表。

**参数**：

* **doc**(str)：微信公众号文章 URL（如 https://mp.weixin.qq.com/s/...）。
* **doc_id**(str, 可选)：可选文档 ID；缺省时使用 URL 或生成 UUID。默认值：""。
* **kwargs**(Any)：可变参数。可传 `timeout`、`user_agent`、`session`(aiohttp.ClientSession) 等，覆盖构造时的设置。

**返回**：

**List[Document]**，通常为单个 Document，包含正文及 metadata（如 source_url、title、source_type="wechat_article"）。

### supports

```python
supports(doc: str) -> bool
```

判断输入是否为微信公众号文章 URL。

**参数**：

* **doc**(str)：文档源（URL）。

**返回**：

**bool**，是否为微信公众号文章 URL。

---

## func openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser.parse_wechat_article_url

```python
async parse_wechat_article_url(
    url: str,
    doc_id: str = "",
    *,
    timeout: float = 30.0,
    user_agent: str = DEFAULT_USER_AGENT,
    session: Optional[aiohttp.ClientSession] = None,
) -> List[Document]
```

抓取给定微信公众号文章 URL 并解析为 Document 列表（通常一个）。不经过 WeChatArticleParser 实例，可直接用于单次抓取。

**参数**：

* **url**(str)：微信公众号文章 URL（如 https://mp.weixin.qq.com/s/...）。
* **doc_id**(str, 可选)：可选文档 ID；缺省时使用 URL 或生成 UUID。默认值：""。
* **timeout**(float, 可选)：请求超时（秒）。默认值：30.0。
* **user_agent**(str, 可选)：HTTP User-Agent。默认值：内置 UA。
* **session**(Optional[aiohttp.ClientSession], 可选)：可复用的 aiohttp 会话；不传则内部创建并在完成后关闭。默认值：None。

**返回**：

**List[Document]**，解析得到的文档列表（通常一条）。
