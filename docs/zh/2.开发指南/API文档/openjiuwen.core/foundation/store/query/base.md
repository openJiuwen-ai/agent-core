# openjiuwen.core.foundation.store.query.base

## class openjiuwen.core.foundation.store.query.base.QueryLanguageDefinition

数据库特定查询语言的定义，用于将查询表达式转换为不同数据库的查询格式。

```python
QueryLanguageDefinition(
    comparison: Callable[[ComparisonExpr], str],
    range: Callable[[RangeExpr], str],
    arithmetic: Callable[[ArithmeticExpr], str],
    null: Callable[[NullExpr], str],
    json_filter: Callable[[JSONExpr], str],
    array: Callable[[ArrayExpr], str],
    logical: Callable[[LogicalExpr], str],
    text_match: Callable[[MatchExpr], str]
)
```

**参数**：

* **comparison**(Callable[[ComparisonExpr], str])：比较表达式的转换函数。
* **range**(Callable[[RangeExpr], str])：范围表达式的转换函数。
* **arithmetic**(Callable[[ArithmeticExpr], str])：算术表达式的转换函数。
* **null**(Callable[[NullExpr], str])：空值检查表达式的转换函数。
* **json_filter**(Callable[[JSONExpr], str])：JSON 字段表达式的转换函数。
* **array**(Callable[[ArrayExpr], str])：数组字段表达式的转换函数。
* **logical**(Callable[[LogicalExpr], str])：逻辑表达式的转换函数。
* **text_match**(Callable[[MatchExpr], str])：文本匹配表达式的转换函数。

---

## 数据库支持矩阵

下表展示了不同数据库对各类查询表达式的支持情况：

| QueryExpr 类型 | Milvus | Chroma | 说明 |
|---------------|--------|--------|------|
| `ComparisonExpr` | ✅ | ✅ | 比较表达式（==、!=、>、<、>=、<=） |
| `RangeExpr` | ✅ | ✅ | 范围表达式（in、like） |
| `ArithmeticExpr` | ✅ | ❌ | 算术表达式（字段值进行算术运算后比较） |
| `NullExpr` | ✅ | ❌ | 空值检查表达式（is null、is not null） |
| `JSONExpr` | ✅ | ❌ | JSON 字段表达式（访问 JSON 字段中的键） |
| `ArrayExpr` | ✅ | ❌ | 数组字段表达式（访问数组字段中的元素） |
| `LogicalExpr` | ✅ | ✅ | 逻辑表达式（and、or、not、xor） |
| `MatchExpr` | ✅ | ✅ | 文本匹配表达式（exact、prefix、suffix、infix） |

**说明**：
- ✅ 表示完全支持
- ❌ 表示不支持，使用时会抛出异常
- Chroma 仅支持扁平化的元数据结构（str、int、float、bool、None），不支持嵌套 JSON 和数组索引
- Milvus 支持所有查询表达式类型

---

## class openjiuwen.core.foundation.store.query.base.QueryExpr

所有查询过滤器的基类，提供统一的查询表达式抽象接口。

```python
QueryExpr()
```

抽象基类，不能直接实例化。所有具体的查询表达式类（如 `ComparisonExpr`、`LogicalExpr` 等）都继承自此类。

### abstractmethod to_expr

```python
to_expr(database: str) -> Any
```

将查询表达式转换为数据库特定的表达式格式。

**参数**：

* **database**(str)：数据库名称（如 "milvus"、"chroma"），必须已通过 `register_database_query_language` 注册。

**返回**：

**Any**，返回数据库特定的查询表达式格式（字符串或字典等）。

### staticmethod sanitize_str

```python
sanitize_str(value: Any) -> str
```

对字符串值进行转义处理，将双引号转义为 `\"`，并用双引号包裹整个字符串。

**参数**：

* **value**(Any)：要处理的字符串值。

**返回**：

**str**，转义后的字符串。

---

## class openjiuwen.core.foundation.store.query.base.CustomExpr

自定义表达式类，用于直接使用数据库原生的查询表达式。

```python
CustomExpr(expr: str | Any)
```

**参数**：

* **expr**(str | Any)：自定义表达式，可以是字符串或数据库特定的表达式对象。

### to_expr

```python
to_expr(database: str) -> Any
```

直接返回自定义表达式，不进行任何转换。

**参数**：

* **database**(str)：数据库名称（此参数在此类中不使用）。

**返回**：

**Any**，返回构造时传入的 `expr` 值。

---

## class openjiuwen.core.foundation.store.query.base.ComparisonExpr

比较表达式类，用于字段与值的比较操作（==、!=、>、<、>=、<=）。

```python
ComparisonExpr(field: str, operator: str, value: Any)
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **operator**(str)：比较运算符，支持 "=="、"!="、">"、"<"、">="、"<="。
* **value**(Any)：用于比较的值。

### to_expr

```python
to_expr(database: str) -> Any
```

将比较表达式转换为数据库特定的查询格式。

**参数**：

* **database**(str)：数据库名称（如 "milvus"、"chroma"）。

**返回**：

**Any**，返回数据库特定的查询表达式。

---

## class openjiuwen.core.foundation.store.query.base.RangeExpr

范围表达式类，用于范围操作（in、like）。

```python
RangeExpr(field: str, operator: str, value: Sequence | set | str)
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **operator**(str)：范围运算符，支持 "in" 或 "like"。
* **value**(Sequence | set | str)：范围操作的值。当 `operator` 为 "in" 时，应为序列（如 list 或 tuple）或集合；当 `operator` 为 "like" 时，应为字符串。

### to_expr

```python
to_expr(database: str) -> Any
```

将范围表达式转换为数据库特定的查询格式。

**参数**：

* **database**(str)：数据库名称（如 "milvus"、"chroma"）。

**返回**：

**Any**，返回数据库特定的查询表达式。

---

## class openjiuwen.core.foundation.store.query.base.ArithmeticExpr

算术表达式类，用于对字段值进行算术运算后再进行比较。

```python
ArithmeticExpr(
    field: str,
    arithmetic_operator: str,
    arithmetic_value: int | float,
    comparison_operator: str,
    comparison_value: int | float
)
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **arithmetic_operator**(str)：算术运算符，支持 "+"、"-"、"*"、"/"、"%"、"**"。
* **arithmetic_value**(int | float)：用于算术运算的值。
* **comparison_operator**(str)：算术运算后的比较运算符，支持 "=="、"!="、">"、"<"、">="、"<="。
* **comparison_value**(int | float)：用于比较的值。

### to_expr

```python
to_expr(database: str) -> Any
```

将算术表达式转换为数据库特定的查询格式。

**参数**：

* **database**(str)：数据库名称（如 "milvus"、"chroma"）。

**返回**：

**Any**，返回数据库特定的查询表达式。

---

## class openjiuwen.core.foundation.store.query.base.NullExpr

空值检查表达式类，用于检查字段是否为 null（is null、is not null）。

```python
NullExpr(field: str, is_null: bool)
```

**参数**：

* **field**(str)：要检查的字段名称。
* **is_null**(bool)：为 `True` 时表示检查字段是否为 null（is null），为 `False` 时表示检查字段是否不为 null（is not null）。

### to_expr

```python
to_expr(database: str) -> Any
```

将空值检查表达式转换为数据库特定的查询格式。

**参数**：

* **database**(str)：数据库名称（如 "milvus"、"chroma"）。

**返回**：

**Any**，返回数据库特定的查询表达式。

---

## class openjiuwen.core.foundation.store.query.base.JSONExpr

JSON 字段表达式类，用于对 JSON 字段中的键进行过滤操作。

```python
JSONExpr(field: str, key: str, operator: str, value: Any)
```

**参数**：

* **field**(str)：JSON 字段名称。
* **key**(str)：JSON 中要过滤的键。
* **operator**(str)：比较运算符，支持 "=="、"!="、">"、"<"、">="、"<="。
* **value**(Any)：用于比较的值。

### to_expr

```python
to_expr(database: str) -> Any
```

将 JSON 字段表达式转换为数据库特定的查询格式。

**参数**：

* **database**(str)：数据库名称（如 "milvus"、"chroma"）。

**返回**：

**Any**，返回数据库特定的查询表达式。

---

## class openjiuwen.core.foundation.store.query.base.ArrayExpr

数组字段表达式类，用于对数组字段进行过滤操作。

```python
ArrayExpr(field: str, index: Optional[int] = None, operator: str, value: Any)
```

**参数**：

* **field**(str)：数组字段名称。
* **index**(int, 可选)：数组索引，用于访问数组中的特定元素。默认值：`None`，表示对整个数组字段进行过滤。
* **operator**(str)：比较运算符，支持 "=="、"!="、">"、"<"、">="、"<="。
* **value**(Any)：用于比较的值。

### to_expr

```python
to_expr(database: str) -> Any
```

将数组字段表达式转换为数据库特定的查询格式。

**参数**：

* **database**(str)：数据库名称（如 "milvus"、"chroma"）。

**返回**：

**Any**，返回数据库特定的查询表达式。

---

## class openjiuwen.core.foundation.store.query.base.LogicalExpr

逻辑表达式类，用于逻辑操作（and、or、not、xor）。

```python
LogicalExpr(operator: str, left: QueryExpr, right: Optional[QueryExpr] = None)
```

**参数**：

* **operator**(str)：逻辑运算符，支持 "and"、"or"、"not"、"xor"。
* **left**(QueryExpr)：左操作数过滤器。
* **right**(QueryExpr, 可选)：右操作数过滤器。对于 "not" 运算符，不需要右操作数。默认值：`None`。

### to_expr

```python
to_expr(database: str) -> Any
```

将逻辑表达式转换为数据库特定的查询格式。

**参数**：

* **database**(str)：数据库名称（如 "milvus"、"chroma"）。

**返回**：

**Any**，返回数据库特定的查询表达式。

---

## class openjiuwen.core.foundation.store.query.base.MatchExpr

文本匹配表达式类，用于文本匹配操作。

```python
MatchExpr(
    field: str,
    value: str,
    match_mode: Literal["prefix", "suffix", "infix", "exact"] = "exact"
)
```

**参数**：

* **field**(str)：要匹配的字段名称。
* **value**(str)：要匹配的文本值。
* **match_mode**(Literal["prefix", "suffix", "infix", "exact"], 可选)：匹配模式。"prefix" 表示前缀匹配，"suffix" 表示后缀匹配，"infix" 表示包含匹配，"exact" 表示精确匹配。默认值："exact"。

### to_expr

```python
to_expr(database: str) -> Any
```

将文本匹配表达式转换为数据库特定的查询格式。

**参数**：

* **database**(str)：数据库名称（如 "milvus"、"chroma"）。

**返回**：

**Any**，返回数据库特定的查询表达式。

---

## func openjiuwen.core.foundation.store.query.base.eq

创建相等比较过滤器。

```python
eq(field: str, value: Any) -> ComparisonExpr
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **value**(Any)：要比较的值。

**返回**：

**ComparisonExpr**，返回一个比较表达式，运算符为 "=="。

**样例**：

```python
from openjiuwen.core.foundation.store.query import eq

# 查找 category 字段等于 "tech" 的文档
filter_expr = eq("category", "tech")
```

---

## func openjiuwen.core.foundation.store.query.base.ne

创建不等比较过滤器。

```python
ne(field: str, value: Any) -> ComparisonExpr
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **value**(Any)：要比较的值。

**返回**：

**ComparisonExpr**，返回一个比较表达式，运算符为 "!="。

**样例**：

```python
from openjiuwen.core.foundation.store.query import ne

# 查找 category 字段不等于 "tech" 的文档
filter_expr = ne("category", "tech")
```

---

## func openjiuwen.core.foundation.store.query.base.gt

创建大于比较过滤器。

```python
gt(field: str, value: int | float) -> ComparisonExpr
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **value**(int | float)：要比较的数值。

**返回**：

**ComparisonExpr**，返回一个比较表达式，运算符为 ">"。

**样例**：

```python
from openjiuwen.core.foundation.store.query import gt

# 查找 score 字段大于 70 的文档
filter_expr = gt("score", 70)
```

---

## func openjiuwen.core.foundation.store.query.base.lt

创建小于比较过滤器。

```python
lt(field: str, value: int | float) -> ComparisonExpr
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **value**(int | float)：要比较的数值。

**返回**：

**ComparisonExpr**，返回一个比较表达式，运算符为 "<"。

**样例**：

```python
from openjiuwen.core.foundation.store.query import lt

# 查找 score 字段小于 80 的文档
filter_expr = lt("score", 80)
```

---

## func openjiuwen.core.foundation.store.query.base.gte

创建大于等于比较过滤器。

```python
gte(field: str, value: int | float) -> ComparisonExpr
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **value**(int | float)：要比较的数值。

**返回**：

**ComparisonExpr**，返回一个比较表达式，运算符为 ">="。

**样例**：

```python
from openjiuwen.core.foundation.store.query import gte

# 查找 score 字段大于等于 70 的文档
filter_expr = gte("score", 70)
```

---

## func openjiuwen.core.foundation.store.query.base.lte

创建小于等于比较过滤器。

```python
lte(field: str, value: int | float) -> ComparisonExpr
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **value**(int | float)：要比较的数值。

**返回**：

**ComparisonExpr**，返回一个比较表达式，运算符为 "<="。

**样例**：

```python
from openjiuwen.core.foundation.store.query import lte

# 查找 score 字段小于等于 80 的文档
filter_expr = lte("score", 80)
```

---

## func openjiuwen.core.foundation.store.query.base.in_list

创建列表包含过滤器，用于检查字段值是否在给定的值列表中。

```python
in_list(field: str, values: Sequence | set) -> RangeExpr | ComparisonExpr
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **values**(Sequence | set)：值列表（如 list 或 tuple）或集合。

**返回**：

**RangeExpr | ComparisonExpr**，当值列表只有一个元素时，返回 `ComparisonExpr`（使用 "==" 运算符）；否则返回 `RangeExpr`（使用 "in" 运算符）。

**样例**：

```python
from openjiuwen.core.foundation.store.query import in_list

# 查找 category 字段在 ["tech", "science", "business"] 中的文档
filter_expr = in_list("category", ["tech", "science", "business"])
```

---

## func openjiuwen.core.foundation.store.query.base.wildcard_match

创建通配符匹配过滤器（数据库必须支持此功能），在模式字符串中使用 `*` 作为通配符。

```python
wildcard_match(field: str, pattern: str, operator: str = "wildcard") -> RangeExpr
```

**参数**：

* **field**(str)：要过滤的字段名称。
* **pattern**(str)：通配符模式字符串，使用 `*` 表示任意字符序列。
* **operator**(str, 可选)：通配符运算符名称。默认值："wildcard"。

**返回**：

**RangeExpr**，返回一个范围表达式，运算符为指定的 `operator`。

**样例**：

```python
from openjiuwen.core.foundation.store.query import wildcard_match

# 查找 author 字段匹配 "Al*" 模式的文档（如 "Alice"、"Alex"）
filter_expr = wildcard_match("author", "Al*")
```

---

## func openjiuwen.core.foundation.store.query.base.is_null

创建 IS NULL 过滤器，用于检查字段是否为 null。

```python
is_null(field: str) -> NullExpr
```

**参数**：

* **field**(str)：要检查的字段名称。

**返回**：

**NullExpr**，返回一个空值检查表达式，`is_null` 参数为 `True`。

**样例**：

```python
from openjiuwen.core.foundation.store.query import is_null

# 查找 optional_field 字段为 null 的文档
filter_expr = is_null("optional_field")
```

---

## func openjiuwen.core.foundation.store.query.base.is_not_null

创建 IS NOT NULL 过滤器，用于检查字段是否不为 null。

```python
is_not_null(field: str) -> NullExpr
```

**参数**：

* **field**(str)：要检查的字段名称。

**返回**：

**NullExpr**，返回一个空值检查表达式，`is_null` 参数为 `False`。

**样例**：

```python
from openjiuwen.core.foundation.store.query import is_not_null

# 查找 optional_field 字段不为 null 的文档
filter_expr = is_not_null("optional_field")
```

---

## func openjiuwen.core.foundation.store.query.base.json_key

创建 JSON 键过滤器，用于对 JSON 字段中的键进行过滤。

```python
json_key(field: str, key: str, operator: str, value: Any) -> JSONExpr
```

**参数**：

* **field**(str)：JSON 字段名称。
* **key**(str)：JSON 中要过滤的键。
* **operator**(str)：比较运算符，支持 "=="、"!="、">"、"<"、">="、"<="。
* **value**(Any)：用于比较的值。

**返回**：

**JSONExpr**，返回一个 JSON 字段表达式。

**样例**：

```python
from openjiuwen.core.foundation.store.query import json_key

# 查找 metadata JSON 字段中 "user" 键的值等于 "Alice" 的文档
filter_expr = json_key("metadata", "user", "==", "Alice")
```

---

## func openjiuwen.core.foundation.store.query.base.array_index

创建数组索引过滤器，用于对数组字段中的特定索引位置进行过滤。

```python
array_index(field: str, index: int, operator: str, value: Any) -> ArrayExpr
```

**参数**：

* **field**(str)：数组字段名称。
* **index**(int)：数组索引，用于访问数组中的特定元素。
* **operator**(str)：比较运算符，支持 "=="、"!="、">"、"<"、">="、"<="。
* **value**(Any)：用于比较的值。

**返回**：

**ArrayExpr**，返回一个数组字段表达式。

**样例**：

```python
from openjiuwen.core.foundation.store.query import array_index

# 查找 tags 数组字段中索引 0 位置的值等于 "important" 的文档
filter_expr = array_index("tags", 0, "==", "important")
```

---

## func openjiuwen.core.foundation.store.query.base.filter_user

创建用户 ID 过滤器，用于过滤一个或多个用户 ID。

```python
filter_user(users: str | List[str], user_id_field: str = "user_id") -> RangeExpr | ComparisonExpr
```

**参数**：

* **users**(str | List[str])：单个用户 ID 字符串或用户 ID 列表。
* **user_id_field**(str, 可选)：用户 ID 字段名称。默认值："user_id"。

**返回**：

**RangeExpr | ComparisonExpr**，当只有一个用户 ID 时，返回 `ComparisonExpr`；否则返回 `RangeExpr`（使用 "in" 运算符）。

**样例**：

```python
from openjiuwen.core.foundation.store.query import filter_user

# 查找 user_id 字段等于 "user123" 的文档
filter_expr = filter_user("user123")

# 查找 user_id 字段在 ["user123", "user456"] 中的文档
filter_expr = filter_user(["user123", "user456"])
```

---

## func openjiuwen.core.foundation.store.query.base.chain_filters

使用 AND 运算符（&）链接多个过滤器，输入为空时返回 `None`。

```python
chain_filters(filters: List[QueryExpr]) -> Optional[LogicalExpr]
```

**参数**：

* **filters**(List[QueryExpr])：要链接的过滤器列表。

**返回**：

**Optional[LogicalExpr]**，返回链接后的逻辑表达式（使用 AND 运算符）；如果输入列表为空，返回 `None`。

**样例**：

```python
from openjiuwen.core.foundation.store.query import chain_filters, eq, gt, gte

# 将多个过滤器用 AND 链接
filter_expr = chain_filters([
    eq("category", "tech"),
    gt("score", 70),
    gte("year", 2022)
])
# 等价于：eq("category", "tech") & gt("score", 70) & gte("year", 2022)
```

---

## 使用示例

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `chroma_query_expr.py`：演示如何在 ChromaDB 中使用查询表达式，包括比较运算符、范围运算符、逻辑运算符、文本匹配等
> - `milvus_query_expr.py`：演示如何在 Milvus 中使用查询表达式，包括比较运算符、范围运算符、逻辑运算符、算术运算符、空值检查、JSON 字段过滤、数组字段过滤、文本匹配等

### 基本使用

```python
from openjiuwen.core.foundation.store.query import eq, gt, gte, in_list
from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore

# 创建向量存储实例
store = ChromaVectorStore(persist_directory="./data/chroma")

# 使用比较运算符
filter_expr = eq("category", "tech")
results = await store.search(query_vector, top_k=10, filters=filter_expr)

# 组合多个条件（使用 & 运算符）
filter_expr = eq("category", "tech") & gt("score", 70) & gte("year", 2022)
results = await store.search(query_vector, top_k=10, filters=filter_expr)

# 使用 OR 运算符
filter_expr = eq("category", "tech") | eq("category", "science")
results = await store.search(query_vector, top_k=10, filters=filter_expr)

# 使用 in_list
filter_expr = in_list("category", ["tech", "science", "business"])
results = await store.search(query_vector, top_k=10, filters=filter_expr)
```

### 逻辑运算符

查询表达式支持使用 Python 的位运算符进行逻辑组合：

- `&`：AND 运算符
- `|`：OR 运算符
- `^`：XOR 运算符（实现为 OR 加否定）
- `~`：NOT 运算符（取反）

```python
from openjiuwen.core.foundation.store.query import eq, gt

# AND 运算符
filter_expr = eq("category", "tech") & gt("score", 70)

# OR 运算符
filter_expr = eq("category", "tech") | eq("category", "science")

# NOT 运算符
filter_expr = ~eq("category", "tech")

# 复杂组合
filter_expr = (eq("category", "tech") | eq("category", "science")) & gt("score", 70)
```

### 文本匹配

```python
from openjiuwen.core.foundation.store.query import MatchExpr

# 精确匹配
match_expr = MatchExpr(field="content", value="tech", match_mode="exact")

# 前缀匹配
match_expr = MatchExpr(field="content", value="This is document 1", match_mode="prefix")

# 后缀匹配
match_expr = MatchExpr(field="content", value="tech.", match_mode="suffix")

# 包含匹配
match_expr = MatchExpr(field="content", value="tech", match_mode="infix")
```

### JSON 和数组字段过滤（仅 Milvus 支持）

```python
from openjiuwen.core.foundation.store.query import json_key, array_index

# JSON 字段过滤
filter_expr = json_key("metadata", "user", "==", "Alice")

# 数组字段过滤
filter_expr = array_index("tags", 0, "==", "important")
```

### 空值检查（仅 Milvus 支持）

```python
from openjiuwen.core.foundation.store.query import is_null, is_not_null

# 检查字段是否为 null
filter_expr = is_null("optional_field")

# 检查字段是否不为 null
filter_expr = is_not_null("optional_field")
```
