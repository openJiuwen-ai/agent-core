# openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser.WebPageParser

通用网页解析器，用于抓取并解析普通网页 URL（如博客、文章等）为 Document。使用 BeautifulSoup 解析 HTML，通过常见选择器（article、main 等）提取正文。微信公众号文章请使用 [WeChatArticleParser](./wechat_article_parser.md)。

```python
WebPageParser(
    timeout: float = 30.0,
    user_agent: str = DEFAULT_USER_AGENT,
    verify: bool | str | ssl.SSLContext = True,
    **kwargs,
)
```

**参数**：

* **timeout**(float, 可选)：请求超时时间（秒）。默认值：30.0。
* **user_agent**(str, 可选)：HTTP User-Agent 头。默认值：内置浏览器 UA。
* **verify**(bool | str | ssl.SSLContext, 可选)：httpx 的 SSL 校验（与 :class:`httpx.AsyncClient` 一致）：``True`` 使用默认 CA；``False`` 关闭校验（如企业代理场景）；或传入 CA 证书路径、自定义 ``ssl.SSLContext``。默认值：True。
* **kwargs**(Any)：可变参数，传递给基类。

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

解析网页 URL，抓取 HTML 并提取正文，返回 Document 列表。

**参数**：

* **doc**(str)：网页 URL（http 或 https）。
* **doc_id**(str, 可选)：可选文档 ID；缺省时使用 URL 或生成 UUID。默认值：""。
* **kwargs**(Any)：可变参数。可传 `timeout`、`user_agent`、`verify`、`client`（:class:`httpx.AsyncClient`）等，覆盖构造时的设置。

**返回**：

**List[Document]**，通常为单个 Document，包含正文及 metadata（如 source_url、title、source_type="web_page"）。

### supports

```python
supports(doc: str) -> bool
```

判断是否为支持的 http(s) URL 且非微信公众号文章 URL（微信公众号 URL 需使用 WeChatArticleParser）。

**参数**：

* **doc**(str)：文档源（URL）。

**返回**：

**bool**，是否支持该文档源。

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

抓取给定网页 URL 并解析为 Document 列表（通常一个）。不经过 WebPageParser 实例，可直接用于单次抓取。

**参数**：

* **url**(str)：网页 URL（http 或 https）。
* **doc_id**(str, 可选)：可选文档 ID；缺省时使用 URL 或生成 UUID。默认值：""。
* **timeout**(float, 可选)：请求超时（秒）；仅在不传入 ``client`` 时使用。默认值：30.0。
* **user_agent**(str, 可选)：HTTP User-Agent。默认值：内置 UA。
* **verify**(bool | str | ssl.SSLContext, 可选)：内部 httpx 客户端的 SSL 校验；传入 ``client`` 时不使用此参数。默认值：True。如果客户端是注入的，则不会使用此参数。
* **client**(Optional[httpx.AsyncClient], 可选)：可复用的 httpx 异步客户端；不传则内部按 ``verify`` / ``timeout`` / User-Agent 创建并在请求后关闭。默认值：None。

**返回**：

**List[Document]**，解析得到的文档列表（通常一条）。
