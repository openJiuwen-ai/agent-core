# openjiuwen.core.retrieval.indexing.processor.parser.html_file_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.html_file_parser.HTMLFileParser

用于本地 **HTML** 文件（`.html`、`.htm`）的解析器：按文本读取文件，用 BeautifulSoup 解析 DOM，抽取**主体正文**并生成通常仅含一条的 `Document`，同时写入标题等 metadata。正文区域的选择策略与 [WebPageParser](./web_page_parser.md) 类似（如 `article`、`main`、常见内容区 class 等），但本类针对**本地路径**，不负责 HTTP 抓取。

继承 [TxtMdParser](./txt_md_parser.md)，因此 HTML 文件在读取侧可与纯文本一致地使用异步读盘与 charset-normalizer 编码探测。

```python
HTMLFileParser(**kwargs: Any)
```

初始化 HTML 文件解析器。

**参数**：

* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

解析本地 HTML 文件，返回 `Document` 列表（一般为一个）。

**参数**：

* **doc**(str)：`.html` 或 `.htm` 文件的本地路径。
* **doc_id**(str, 可选)：生成文档的 ID；若为空，实现上可能回退为路径或其他稳定标识。默认值：""。
* **kwargs**(Any)：可选参数（例如 `timeout`、`user_agent`），与其他解析器接口对齐；本地文件解析不进行 HTTP 请求，这些参数通常不生效。

**返回**：

**List[Document]**，通常一项，其 `text` 为从识别出的主体区域提取并规范化后的纯文本。

**抛出**：

* 若无法确定合适的主体节点，或解析后正文为空/短于内部阈值，将抛出以 `StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR` 构建的错误。

**说明**：

* 注册扩展名：`.htm`、`.HTM`、`.html`、`.HTML`（见模块内 `@register_parser`）。
* 解析器：若已安装 `lxml` 则优先使用，否则使用标准库 `html.parser`。
* **标题** metadata：优先 `<meta property="og:title" content="...">`，否则使用 `<title>`；若仍无标题，可能使用占位标题。
* **主体正文**：依次尝试 `article`、`main`、`[role="main"]` 及若干常见文章/正文 class 选择器；若无足够文本，再回退到较大的 `div`/`section` 或 `body`。
* **文本处理**：去除 `script`、`style`，压缩横向空白并规整空行后写入 `Document.text`。
* **metadata** 至少包含 `title` 与 `source_type`（值为 `"web_page"`）。

### async _parse_html（classmethod）

```python
_parse_html(
    html: str,
    doc_id: str = "",
    source: Optional[str] = None,
) -> List[Document]
```

在内存中直接解析 **HTML 字符串**（已由磁盘或其他来源读出），标题与主体抽取逻辑与 `parse` 一致。适用于内部或高级场景。

**参数**：

* **html**(str)：完整 HTML 文档字符串。
* **doc_id**(str, 可选)：结果文档 ID。默认值：""。
* **source**(str, 可选)：用于错误信息的来源说明（如文件路径）。默认值：None。

**返回**：

**List[Document]**，通常一条。

**抛出**：

* 与 `parse` 相同：无法确定主体或正文过短时失败。
