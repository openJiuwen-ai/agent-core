# processor

`openjiuwen.core.retrieval.indexing.processor` provides abstract interfaces and implementations for document processors, including parsers, chunkers, extractors, and splitters.

**Detailed API Documentation**: [base.md](./base.md)

**Classes**:

| CLASS | DESCRIPTION | Detailed API |
|-------|-------------|---------------|
| **Processor** | Processor abstract base class. | [base.md](./base.md) |
| **Parser** | Document parser abstract base class. | [parser/base.md](./parser/base.md) |
| **AutoFileParser** | Auto file parser. | [parser/auto_file_parser.md](./parser/auto_file_parser.md) |
| **JSONParser** | JSON parser. | [parser/json_parser.md](./parser/json_parser.md) |
| **PDFParser** | PDF parser. | [parser/pdf_parser.md](./parser/pdf_parser.md) |
| **TxtMdParser** | Text and Markdown parser. | [parser/txt_md_parser.md](./parser/txt_md_parser.md) |
| **WordParser** | Word document parser. | [parser/word_parser.md](./parser/word_parser.md) |
| **Chunker** | Text chunker abstract base class. | [chunker/base.md](./chunker/base.md) |
| **CharChunker** | Character-based chunker. | [chunker/char_chunker.md](./chunker/char_chunker.md) |
| **TokenizerChunker** | Tokenizer-based chunker. | [chunker/tokenizer_chunker.md](./chunker/tokenizer_chunker.md) |
| **TextChunker** | Text chunker (supports character/token). | [chunker/chunking.md](./chunker/chunking.md) |
| **Extractor** | Extractor abstract base class. | [extractor/base.md](./extractor/base.md) |
| **TripleExtractor** | Triple extractor. | [extractor/triple_extractor.md](./extractor/triple_extractor.md) |
| **SentenceSplitter** | Sentence splitter. | [splitter/splitter.md](./splitter/splitter.md) |

**Functions**:

| FUNCTION | DESCRIPTION | Detailed API |
|----------|-------------|---------------|
| **register_parser** | Register parser function. | [parser/auto_file_parser.md](./parser/auto_file_parser.md) |
