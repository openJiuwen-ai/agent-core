# processor

`openjiuwen.core.retrieval.indexing.processor` 提供了文档处理器的抽象接口和实现，包括解析器、分块器、提取器和分割器。

**详细 API 文档**：[base.md](./base.md)

**Classes**：

| CLASS | DESCRIPTION | 详细 API |
|-------|-------------|----------|
| **Processor** | 处理器抽象基类。 | [base.md](./base.md) |
| **Parser** | 文档解析器抽象基类。 | [parser/base.md](./parser/base.md) |
| **AutoFileParser** | 自动文件解析器。 | [parser/auto_file_parser.md](./parser/auto_file_parser.md) |
| **JSONParser** | JSON 解析器。 | [parser/json_parser.md](./parser/json_parser.md) |
| **PDFParser** | PDF 解析器。 | [parser/pdf_parser.md](./parser/pdf_parser.md) |
| **TxtMdParser** | 文本和 Markdown 解析器。 | [parser/txt_md_parser.md](./parser/txt_md_parser.md) |
| **WordParser** | Word 文档解析器。 | [parser/word_parser.md](./parser/word_parser.md) |
| **Chunker** | 文本分块器抽象基类。 | [chunker/base.md](./chunker/base.md) |
| **CharChunker** | 基于字符的分块器。 | [chunker/char_chunker.md](./chunker/char_chunker.md) |
| **TokenizerChunker** | 基于 tokenizer 的分块器。 | [chunker/tokenizer_chunker.md](./chunker/tokenizer_chunker.md) |
| **TextChunker** | 文本分块器（支持字符/token）。 | [chunker/chunking.md](./chunker/chunking.md) |
| **Extractor** | 提取器抽象基类。 | [extractor/base.md](./extractor/base.md) |
| **TripleExtractor** | 三元组提取器。 | [extractor/triple_extractor.md](./extractor/triple_extractor.md) |
| **SentenceSplitter** | 句子分割器。 | [splitter/splitter.md](./splitter/splitter.md) |

**Functions**：

| FUNCTION | DESCRIPTION | 详细 API |
|----------|-------------|----------|
| **register_parser** | 注册解析器函数。 | [parser/auto_file_parser.md](./parser/auto_file_parser.md) |
