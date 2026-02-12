# parser

`openjiuwen.core.retrieval.indexing.processor.parser` 提供了文档解析器的抽象接口和实现。

**Classes**：

| CLASS | DESCRIPTION | 详细 API |
|-------|-------------|----------|
| **Parser** | 文档解析器抽象基类。 | [base.md](./base.md) |
| **AutoFileParser** | 自动文件解析器。 | [auto_file_parser.md](./auto_file_parser.md) |
| **JSONParser** | JSON 解析器。 | [json_parser.md](./json_parser.md) |
| **PDFParser** | PDF 解析器。 | [pdf_parser.md](./pdf_parser.md) |
| **TxtMdParser** | 文本和 Markdown 解析器。 | [txt_md_parser.md](./txt_md_parser.md) |
| **WordParser** | Word 文档解析器。 | [word_parser.md](./word_parser.md) |

**Functions**：

| FUNCTION | DESCRIPTION | 详细 API |
|----------|-------------|----------|
| **register_parser** | 注册解析器函数。 | [auto_file_parser.md](./auto_file_parser.md) |
