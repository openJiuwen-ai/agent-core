# openjiuwen.core.retrieval.indexing.processor.parser.auto_link_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.auto_link_parser.AutoLinkParser

Link parser that routes by URL pattern. You can configure a list of (pattern or callable, parser) routes; the first match is used. Default routes: WeChat article URLs (mp.weixin.qq.com) → WeChatArticleParser, other http(s) URLs → WebPageParser. With a knowledge base, any WeChat or web URL can be parsed through this single entry.

```python
AutoLinkParser(routes: List[Tuple[Union[re.Pattern, Callable[[str], bool]], Parser]] | None = None, **kwargs)
```

**Parameters**:

* **routes**(List[Tuple[..., Parser]], optional): List of (pattern_or_callable, Parser) pairs. If None, default routes (WeChat + generic web) are used. Default: None.
* **kwargs**: Additional arguments passed to the base class.

### supports

```python
supports(doc: str) -> bool
```

Returns whether the input is a supported http(s) URL matched by one of the routes.

### async parse

```python
parse(doc: str, doc_id: str = "", llm_client: Optional[Model] = None, **kwargs) -> List[Document]
```

Selects the parser for the URL from the routes and returns a list of Documents.

**Parameters**:

* **doc**(str): Document source (URL).
* **doc_id**(str): Document ID. Default: "".
* **llm_client**(Optional[Model], optional): LLM client for captioning or other LLM-based processing. Default: None.
* **kwargs**: Variable arguments passed to the downstream parser.
