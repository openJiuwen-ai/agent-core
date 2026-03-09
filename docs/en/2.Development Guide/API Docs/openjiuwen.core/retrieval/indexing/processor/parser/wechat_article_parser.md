# openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser.WeChatArticleParser

WeChat official account article parser. Fetches and parses WeChat article pages from URLs (e.g. https://mp.weixin.qq.com/s/...) into Documents using BeautifulSoup. Can be used with [AutoParser](./auto_parser.md) or [AutoLinkParser](./auto_link_parser.md) for URL-based dispatch.

```python
WeChatArticleParser(timeout: float = 30.0, user_agent: str = DEFAULT_USER_AGENT, **kwargs)
```

**Parameters**:

* **timeout**(float, optional): Request timeout in seconds. Default: 30.0.
* **user_agent**(str, optional): HTTP User-Agent header. Default: built-in browser UA.
* **kwargs**(Any): Variable arguments passed to the base class.

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

Parse a WeChat article URL: fetch the page and extract content (js_content area), return a list of Documents.

**Parameters**:

* **doc**(str): WeChat article URL (e.g. https://mp.weixin.qq.com/s/...).
* **doc_id**(str, optional): Optional document ID; defaults to URL or generated UUID. Default: "".
* **kwargs**(Any): Variable arguments. May include `timeout`, `user_agent`, `session`(aiohttp.ClientSession) to override constructor settings.

**Returns**:

**List[Document]**, typically a single Document with text and metadata (e.g. source_url, title, source_type="wechat_article").

### supports

```python
supports(doc: str) -> bool
```

Whether the input is a WeChat article URL.

**Parameters**:

* **doc**(str): Document source (URL).

**Returns**:

**bool**, whether the URL is a WeChat article URL.

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

Fetch a WeChat article URL and parse it into a list of Documents (typically one). Can be used standalone without a WeChatArticleParser instance.

**Parameters**:

* **url**(str): WeChat article URL (e.g. https://mp.weixin.qq.com/s/...).
* **doc_id**(str, optional): Optional document ID; defaults to URL or generated UUID. Default: "".
* **timeout**(float, optional): Request timeout in seconds. Default: 30.0.
* **user_agent**(str, optional): HTTP User-Agent. Default: built-in UA.
* **session**(Optional[aiohttp.ClientSession], optional): Reusable aiohttp session; if not provided, one is created and closed after use. Default: None.

**Returns**:

**List[Document]**, the parsed document list (typically one).
