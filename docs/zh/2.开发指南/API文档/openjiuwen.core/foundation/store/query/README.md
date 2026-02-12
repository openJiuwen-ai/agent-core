# query

`openjiuwen.core.foundation.store.query` 提供了数据库查询表达式的抽象接口和实现，用于将统一的查询表达式转换为不同数据库的查询格式。

**详细 API 文档**：[base.md](./base.md)、[registry.md](./registry.md)

**Classes**：

| CLASS | DESCRIPTION | 详细 API |
|-------|-------------|----------|
| **QueryLanguageDefinition** | 数据库特定查询语言的定义。 | [base.md](./base.md) |
| **QueryExpr** | 所有查询过滤器的基类。 | [base.md](./base.md) |
| **CustomExpr** | 自定义表达式类。 | [base.md](./base.md) |
| **ComparisonExpr** | 比较表达式类（==、!=、>、<、>=、<=）。 | [base.md](./base.md) |
| **RangeExpr** | 范围表达式类。 | [base.md](./base.md) |
| **ArithmeticExpr** | 算术表达式类。 | [base.md](./base.md) |
| **NullExpr** | 空值检查表达式类。 | [base.md](./base.md) |
| **JSONExpr** | JSON 字段表达式类。 | [base.md](./base.md) |
| **ArrayExpr** | 数组字段表达式类。 | [base.md](./base.md) |
| **LogicalExpr** | 逻辑表达式类（AND、OR、NOT）。 | [base.md](./base.md) |
| **MatchExpr** | 文本匹配表达式类。 | [base.md](./base.md) |

**Functions**：

| FUNCTION | DESCRIPTION | 详细 API |
|----------|-------------|----------|
| **register_database_query_language** | 注册数据库查询语言定义。 | [registry.md](./registry.md) |
| **eq, ne, gt, lt, gte, lte** | 比较操作函数。 | [base.md](./base.md) |
| **in_list** | 列表包含操作函数。 | [base.md](./base.md) |
| **wildcard_match** | 通配符匹配函数。 | [base.md](./base.md) |
| **is_null, is_not_null** | 空值检查函数。 | [base.md](./base.md) |
| **json_key** | JSON 字段访问函数。 | [base.md](./base.md) |
| **array_index** | 数组索引访问函数。 | [base.md](./base.md) |
| **filter_user, chain_filters** | 过滤器工具函数。 | [base.md](./base.md) |
