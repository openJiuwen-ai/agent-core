# openjiuwen.core.retrieval.indexing.processor.parser.excel_parser

## class openjiuwen.core.retrieval.indexing.processor.parser.excel_parser.ExcelParser

用于 Excel（.xlsx）、CSV 和 TSV 文件的解析器。将表格按行、按列分别生成 Document，便于按行或按列检索，同一文件的行文档与列文档写入同一索引。


```python
ExcelParser(**kwargs: Any)
```

初始化 Excel/CSV/TSV 解析器。

**参数**：

* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async parse

```python
parse(doc: str, doc_id: str = "", **kwargs: Any) -> List[Document]
```

解析 Excel、CSV 或 TSV 文件，生成按行与按列的 Document 列表。

**参数**：

* **doc**(str)：.xlsx、.csv 或 .tsv 文件路径。
* **doc_id**(str, 可选)：可选的基础文档 ID，用作每个生成文档 ID 的前缀。默认值：""。
* **kwargs**(Any)：可变参数。其中 `include_header`(bool, 可选)：若为 True（默认），在行文档与列文档中为单元格内容加上列名前缀；若为 False，仅使用单元格值。

**返回**：

**List[Document]**，返回所有 sheet/文件的行文档与列文档列表（比如 list）。

**说明**：

* 支持的文件扩展名：`.xlsx`, `.XLSX`, `.csv`, `.CSV`, `.tsv`, `.TSV`
* 每个 sheet 第一行视为表头；每行生成一个 row 文档，每列生成一个 column 文档
* metadata 中包含 `sheet_name`、`source_type`（"row" 或 "column"）等

### supports

```python
supports(doc: str) -> bool
```

判断是否为支持的 .xlsx、.csv 或 .tsv 文件路径。

**参数**：

* **doc**(str)：文件路径或文档源。

**返回**：

**bool**，是否支持该文档源。
