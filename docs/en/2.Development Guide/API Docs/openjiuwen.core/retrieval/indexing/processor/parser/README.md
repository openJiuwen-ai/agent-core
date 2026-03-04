# parser

`openjiuwen.core.retrieval.indexing.processor.parser` provides abstract interfaces and implementations for document parsers.

**Classes**:

| CLASS | DESCRIPTION | Detailed API |
|-------|-------------|---------------|
| **Parser** | Document parser abstract base class. | [base.md](./base.md) |
| **AutoParser** | Unified parser (files and URLs). | [auto_parser.md](./auto_parser.md) |
| **AutoLinkParser** | Link parser (WeChat articles, web pages). | [auto_link_parser.md](./auto_link_parser.md) |
| **AutoFileParser** | Auto file parser (by extension). | [auto_file_parser.md](./auto_file_parser.md) |
| **JSONParser** | JSON parser. | [json_parser.md](./json_parser.md) |
| **PDFParser** | PDF parser. | [pdf_parser.md](./pdf_parser.md) |
| **TxtMdParser** | Text and Markdown parser. | [txt_md_parser.md](./txt_md_parser.md) |
| **WordParser** | Word document parser. | [word_parser.md](./word_parser.md) |

**Functions**:

| FUNCTION | DESCRIPTION | Detailed API |
|----------|-------------|---------------|
| **register_parser** | Register parser function. | [auto_file_parser.md](./auto_file_parser.md) |
