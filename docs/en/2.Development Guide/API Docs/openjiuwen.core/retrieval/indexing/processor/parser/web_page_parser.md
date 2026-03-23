# openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser.WebPageParser

Generic web page parser. Fetches and parses web page URLs (e.g. blog posts, articles) into Documents. Uses BeautifulSoup and common selectors (article, main, etc.) to extract main content. For WeChat official account articles use [WeChatArticleParser](./wechat_article_parser.md).

```python
WebPageParser(
    timeout: float = 30.0,
    user_agent: str = DEFAULT_USER_AGENT,
    verify: bool | str | ssl.SSLContext = True,
    **kwargs,
)
```

**Parameters**:

* **timeout**(float, optional): Request timeout in seconds. Default: 30.0.
* **user_agent**(str, optional): HTTP User-Agent header. Default: built-in browser UA.
* **verify**(bool | str | ssl.SSLContext, optional): SSL verification for httpx (same as :class:`httpx.AsyncClient`): ``True`` for default CAs, ``False`` to disable (e.g. corporate proxies), a path to a CA bundle, or a custom ``ssl.SSLContext``. Default: True.
* **kwargs**(Any): Variable arguments passed to the base class.

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

Parse a web page URL: fetch HTML and extract main content, return a list of Documents.

**Parameters**:

* **doc**(str): Web page URL (http or https).
* **doc_id**(str, optional): Optional document ID; defaults to URL or generated UUID. Default: "".
* **kwargs**(Any): Variable arguments. May include `timeout`, `user_agent`, `verify`, `client` (:class:`httpx.AsyncClient`) to override constructor settings.

**Returns**:

**List[Document]**, typically a single Document with text and metadata (e.g. source_url, title, source_type="web_page").

### supports

```python
supports(doc: str) -> bool
```

Whether the input is a supported http(s) URL and not a WeChat article URL (use WeChatArticleParser for WeChat URLs).

**Parameters**:

* **doc**(str): Document source (URL).

**Returns**:

**bool**, whether this document source is supported.

---

## func openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser.parse_web_page_url

```python
async parse_web_page_url(
    url: str,
    doc_id: str = "",
    *,
    timeout: float = 30.0,
    user_agent: str = DEFAULT_USER_AGENT,
    verify: bool | str | ssl.SSLContext = True,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Document]
```

Fetch a web page URL and parse it into a list of Documents (typically one). Can be used standalone without a WebPageParser instance.

**Parameters**:

* **url**(str): Web page URL (http or https).
* **doc_id**(str, optional): Optional document ID; defaults to URL or generated UUID. Default: "".
* **timeout**(float, optional): Request timeout in seconds (only when ``client`` is not provided). Default: 30.0.
* **user_agent**(str, optional): HTTP User-Agent. Default: built-in UA.
* **verify**(bool | str | ssl.SSLContext, optional): SSL verification for the internal httpx client; ignored when ``client`` is provided. Default: True. If client is injected, this parameter will not be used. 
* **client**(Optional[httpx.AsyncClient], optional): Reusable async httpx client; if not provided, one is created (with ``verify`` / ``timeout`` / User-Agent) and closed after the request. Default: None. 

**Returns**:

**List[Document]**, the parsed document list (typically one).
