# openjiuwen.core.retrieval.indexing.processor.parser.excel_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.excel_parser.ExcelParser

Parser for Excel (.xlsx), CSV, and TSV files. Produces both row-wise and column-wise Documents for the same index so that retrieval can be done by row or by column.


```python
ExcelParser(**kwargs: Any)
```

Initialize the Excel/CSV/TSV parser.

**Parameters**:

* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

Parse an Excel, CSV, or TSV file into row and column Documents.

**Parameters**:

* **doc**(str): Path to a .xlsx, .csv, or .tsv file.
* **doc_id**(str, optional): Optional base document ID used as prefix for each generated doc ID. Default: "".
* **kwargs**(Any): Variable arguments. `include_header`(bool, optional): If True (default), prepend column name to each cell in row/column docs; if False, use only cell values.

**Returns**:

**List[Document]**, list of row and column documents for all sheets/file.

**Description**:

* Supported file extensions: `.xlsx`, `.XLSX`, `.csv`, `.CSV`, `.tsv`, `.TSV`
* First row of each sheet is treated as header; each row yields a row doc, each column yields a column doc
* metadata includes `sheet_name`, `source_type` ("row" or "column"), etc.

### supports

```python
supports(doc: str) -> bool
```

Whether the given path is a supported .xlsx, .csv, or .tsv file.

**Parameters**:

* **doc**(str): File path or document source.

**Returns**:

**bool**, whether this document source is supported.
