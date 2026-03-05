# parser

`openjiuwen.core.retrieval.indexing.processor.parser` 提供了文档解析器的抽象接口和实现。

**Classes**：

| CLASS | DESCRIPTION | 详细 API |
|-------|-------------|----------|
| **Parser** | 文档解析器抽象基类。 | [base.md](./base.md) |
| **AutoParser** | 统一解析器（文件与 URL 均可）。 | [auto_parser.md](./auto_parser.md) |
| **AutoLinkParser** | 链接解析器（微信公众号、网页）。 | [auto_link_parser.md](./auto_link_parser.md) |
| **AutoFileParser** | 自动文件解析器（按扩展名选择格式）。 | [auto_file_parser.md](./auto_file_parser.md) |
| **ExcelParser** | Excel/CSV/TSV 解析器（按行、按列生成 Document）。 | [excel_parser.md](./excel_parser.md) |
| **JSONParser** | JSON 解析器。 | [json_parser.md](./json_parser.md) |
| **PDFParser** | PDF 解析器。 | [pdf_parser.md](./pdf_parser.md) |
| **TxtMdParser** | 文本和 Markdown 解析器。 | [txt_md_parser.md](./txt_md_parser.md) |
| **WebPageParser** | 通用网页解析器（博客、文章等 URL）。 | [web_page_parser.md](./web_page_parser.md) |
| **WeChatArticleParser** | 微信公众号文章解析器。 | [wechat_article_parser.md](./wechat_article_parser.md) |
| **WordParser** | Word 文档解析器。 | [word_parser.md](./word_parser.md) |
| **ImageParser** | 图片解析器（VLM caption）。 | [image_parser.md](./image_parser.md) |
| **ImageCaptioner** | 图片描述辅助类（内部使用）。 | [captioner.md](./captioner.md) |

**Functions**：

| FUNCTION | DESCRIPTION | 详细 API |
|----------|-------------|----------|
| **register_parser** | 注册解析器函数。 | [auto_file_parser.md](./auto_file_parser.md) |
| **parse_web_page_url** | 抓取并解析网页 URL 为 Document 列表。 | [web_page_parser.md](./web_page_parser.md) |
| **parse_wechat_article_url** | 抓取并解析微信公众号文章 URL 为 Document 列表。 | [wechat_article_parser.md](./wechat_article_parser.md) |
