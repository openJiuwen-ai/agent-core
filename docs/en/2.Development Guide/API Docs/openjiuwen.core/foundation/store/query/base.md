# openjiuwen.core.foundation.store.query.base

## class openjiuwen.core.foundation.store.query.base.QueryLanguageDefinition

Definition of a database-specific query language for converting query expressions to different database query formats.

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

**Parameters**:

* **comparison**(Callable[[ComparisonExpr], str]): Conversion function for comparison expressions.
* **range**(Callable[[RangeExpr], str]): Conversion function for range expressions.
* **arithmetic**(Callable[[ArithmeticExpr], str]): Conversion function for arithmetic expressions.
* **null**(Callable[[NullExpr], str]): Conversion function for null check expressions.
* **json_filter**(Callable[[JSONExpr], str]): Conversion function for JSON field expressions.
* **array**(Callable[[ArrayExpr], str]): Conversion function for array field expressions.
* **logical**(Callable[[LogicalExpr], str]): Conversion function for logical expressions.
* **text_match**(Callable[[MatchExpr], str]): Conversion function for text match expressions.

---

## Database Support Matrix

The following table shows the support status of different query expression types across databases:

| QueryExpr Type | Milvus | Chroma | Notes |
|---------------|--------|--------|-------|
| `ComparisonExpr` | ✅ | ✅ | Comparison expressions (==, !=, >, <, >=, <=) |
| `RangeExpr` | ✅ | ✅ | Range expressions (in, like) |
| `ArithmeticExpr` | ✅ | ❌ | Arithmetic expressions (compare after arithmetic operations on field values) |
| `NullExpr` | ✅ | ❌ | Null check expressions (is null, is not null) |
| `JSONExpr` | ✅ | ❌ | JSON field expressions (access keys within JSON fields) |
| `ArrayExpr` | ✅ | ❌ | Array field expressions (access elements within array fields) |
| `LogicalExpr` | ✅ | ✅ | Logical expressions (and, or, not, xor) |
| `MatchExpr` | ✅ | ✅ | Text match expressions (exact, prefix, suffix, infix) |

**Notes**:
- ✅ indicates full support
- ❌ indicates not supported, will raise an exception when used
- Chroma only supports flat metadata structures (str, int, float, bool, None), does not support nested JSON or array indexing
- Milvus supports all query expression types

---

## class openjiuwen.core.foundation.store.query.base.QueryExpr

Base class for all query filters, providing a unified query expression abstraction interface.

```python
QueryExpr()
```

Abstract base class that cannot be instantiated directly. All specific query expression classes (such as `ComparisonExpr`, `LogicalExpr`, etc.) inherit from this class.

### abstractmethod to_expr

```python
to_expr(database: str) -> Any
```

Convert the query expression to a database-specific expression format.

**Parameters**:

* **database**(str): Database name (e.g., "milvus", "chroma"), must be registered via `register_database_query_language`.

**Returns**:

**Any**, returns a database-specific query expression format (string, dictionary, etc.).

### staticmethod sanitize_str

```python
sanitize_str(value: Any) -> str
```

Escape string values, converting double quotes to `\"` and wrapping the entire string in double quotes.

**Parameters**:

* **value**(Any): String value to process.

**Returns**:

**str**, escaped string.

---

## class openjiuwen.core.foundation.store.query.base.CustomExpr

Custom expression class for directly using database-native query expressions.

```python
CustomExpr(expr: str | Any)
```

**Parameters**:

* **expr**(str | Any): Custom expression, can be a string or database-specific expression object.

### to_expr

```python
to_expr(database: str) -> Any
```

Directly returns the custom expression without any conversion.

**Parameters**:

* **database**(str): Database name (this parameter is not used in this class).

**Returns**:

**Any**, returns the `expr` value passed during construction.

---

## class openjiuwen.core.foundation.store.query.base.ComparisonExpr

Comparison expression class for field-to-value comparison operations (==, !=, >, <, >=, <=).

```python
ComparisonExpr(field: str, operator: str, value: Any)
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **operator**(str): Comparison operator, supports "==", "!=", ">", "<", ">=", "<=".
* **value**(Any): Value to compare against.

### to_expr

```python
to_expr(database: str) -> Any
```

Convert the comparison expression to a database-specific query format.

**Parameters**:

* **database**(str): Database name (e.g., "milvus", "chroma").

**Returns**:

**Any**, returns a database-specific query expression.

---

## class openjiuwen.core.foundation.store.query.base.RangeExpr

Range expression class for range operations (in, like).

```python
RangeExpr(field: str, operator: str, value: Sequence | set | str)
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **operator**(str): Range operator, supports "in" or "like".
* **value**(Sequence | set | str): Value for range operation. When `operator` is "in", should be a sequence (e.g., list or tuple) or set; when `operator` is "like", should be a string.

### to_expr

```python
to_expr(database: str) -> Any
```

Convert the range expression to a database-specific query format.

**Parameters**:

* **database**(str): Database name (e.g., "milvus", "chroma").

**Returns**:

**Any**, returns a database-specific query expression.

---

## class openjiuwen.core.foundation.store.query.base.ArithmeticExpr

Arithmetic expression class for performing arithmetic operations on field values before comparison.

```python
ArithmeticExpr(
    field: str,
    arithmetic_operator: str,
    arithmetic_value: int | float,
    comparison_operator: str,
    comparison_value: int | float
)
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **arithmetic_operator**(str): Arithmetic operator, supports "+", "-", "*", "/", "%", "**".
* **arithmetic_value**(int | float): Value for arithmetic operation.
* **comparison_operator**(str): Comparison operator after arithmetic operation, supports "==", "!=", ">", "<", ">=", "<=".
* **comparison_value**(int | float): Value to compare against.

### to_expr

```python
to_expr(database: str) -> Any
```

Convert the arithmetic expression to a database-specific query format.

**Parameters**:

* **database**(str): Database name (e.g., "milvus", "chroma").

**Returns**:

**Any**, returns a database-specific query expression.

---

## class openjiuwen.core.foundation.store.query.base.NullExpr

Null check expression class for checking if a field is null (is null, is not null).

```python
NullExpr(field: str, is_null: bool)
```

**Parameters**:

* **field**(str): Name of the field to check.
* **is_null**(bool): When `True`, checks if the field is null (is null); when `False`, checks if the field is not null (is not null).

### to_expr

```python
to_expr(database: str) -> Any
```

Convert the null check expression to a database-specific query format.

**Parameters**:

* **database**(str): Database name (e.g., "milvus", "chroma").

**Returns**:

**Any**, returns a database-specific query expression.

---

## class openjiuwen.core.foundation.store.query.base.JSONExpr

JSON field expression class for filtering on keys within JSON fields.

```python
JSONExpr(field: str, key: str, operator: str, value: Any)
```

**Parameters**:

* **field**(str): JSON field name.
* **key**(str): Key within the JSON to filter on.
* **operator**(str): Comparison operator, supports "==", "!=", ">", "<", ">=", "<=".
* **value**(Any): Value to compare against.

### to_expr

```python
to_expr(database: str) -> Any
```

Convert the JSON field expression to a database-specific query format.

**Parameters**:

* **database**(str): Database name (e.g., "milvus", "chroma").

**Returns**:

**Any**, returns a database-specific query expression.

---

## class openjiuwen.core.foundation.store.query.base.ArrayExpr

Array field expression class for filtering on array fields.

```python
ArrayExpr(field: str, index: Optional[int] = None, operator: str, value: Any)
```

**Parameters**:

* **field**(str): Array field name.
* **index**(int, optional): Array index for accessing a specific element in the array. Default: `None`, meaning filter on the entire array field.
* **operator**(str): Comparison operator, supports "==", "!=", ">", "<", ">=", "<=".
* **value**(Any): Value to compare against.

### to_expr

```python
to_expr(database: str) -> Any
```

Convert the array field expression to a database-specific query format.

**Parameters**:

* **database**(str): Database name (e.g., "milvus", "chroma").

**Returns**:

**Any**, returns a database-specific query expression.

---

## class openjiuwen.core.foundation.store.query.base.LogicalExpr

Logical expression class for logical operations (and, or, not, xor).

```python
LogicalExpr(operator: str, left: QueryExpr, right: Optional[QueryExpr] = None)
```

**Parameters**:

* **operator**(str): Logical operator, supports "and", "or", "not", "xor".
* **left**(QueryExpr): Left operand filter.
* **right**(QueryExpr, optional): Right operand filter. Not needed for "not" operator. Default: `None`.

### to_expr

```python
to_expr(database: str) -> Any
```

Convert the logical expression to a database-specific query format.

**Parameters**:

* **database**(str): Database name (e.g., "milvus", "chroma").

**Returns**:

**Any**, returns a database-specific query expression.

---

## class openjiuwen.core.foundation.store.query.base.MatchExpr

Text match expression class for text matching operations.

```python
MatchExpr(
    field: str,
    value: str,
    match_mode: Literal["prefix", "suffix", "infix", "exact"] = "exact"
)
```

**Parameters**:

* **field**(str): Name of the field to match.
* **value**(str): Text value to match.
* **match_mode**(Literal["prefix", "suffix", "infix", "exact"], optional): Match mode. "prefix" for prefix matching, "suffix" for suffix matching, "infix" for contains matching, "exact" for exact matching. Default: "exact".

### to_expr

```python
to_expr(database: str) -> Any
```

Convert the text match expression to a database-specific query format.

**Parameters**:

* **database**(str): Database name (e.g., "milvus", "chroma").

**Returns**:

**Any**, returns a database-specific query expression.

---

## func openjiuwen.core.foundation.store.query.base.eq

Create an equality comparison filter.

```python
eq(field: str, value: Any) -> ComparisonExpr
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **value**(Any): Value to compare against.

**Returns**:

**ComparisonExpr**, returns a comparison expression with operator "==".

**Example**:

```python
from openjiuwen.core.foundation.store.query import eq

# Find documents where category field equals "tech"
filter_expr = eq("category", "tech")
```

---

## func openjiuwen.core.foundation.store.query.base.ne

Create a not-equal comparison filter.

```python
ne(field: str, value: Any) -> ComparisonExpr
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **value**(Any): Value to compare against.

**Returns**:

**ComparisonExpr**, returns a comparison expression with operator "!=".

**Example**:

```python
from openjiuwen.core.foundation.store.query import ne

# Find documents where category field does not equal "tech"
filter_expr = ne("category", "tech")
```

---

## func openjiuwen.core.foundation.store.query.base.gt

Create a greater-than comparison filter.

```python
gt(field: str, value: int | float) -> ComparisonExpr
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **value**(int | float): Numeric value to compare against.

**Returns**:

**ComparisonExpr**, returns a comparison expression with operator ">".

**Example**:

```python
from openjiuwen.core.foundation.store.query import gt

# Find documents where score field is greater than 70
filter_expr = gt("score", 70)
```

---

## func openjiuwen.core.foundation.store.query.base.lt

Create a less-than comparison filter.

```python
lt(field: str, value: int | float) -> ComparisonExpr
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **value**(int | float): Numeric value to compare against.

**Returns**:

**ComparisonExpr**, returns a comparison expression with operator "<".

**Example**:

```python
from openjiuwen.core.foundation.store.query import lt

# Find documents where score field is less than 80
filter_expr = lt("score", 80)
```

---

## func openjiuwen.core.foundation.store.query.base.gte

Create a greater-than-or-equal comparison filter.

```python
gte(field: str, value: int | float) -> ComparisonExpr
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **value**(int | float): Numeric value to compare against.

**Returns**:

**ComparisonExpr**, returns a comparison expression with operator ">=".

**Example**:

```python
from openjiuwen.core.foundation.store.query import gte

# Find documents where score field is greater than or equal to 70
filter_expr = gte("score", 70)
```

---

## func openjiuwen.core.foundation.store.query.base.lte

Create a less-than-or-equal comparison filter.

```python
lte(field: str, value: int | float) -> ComparisonExpr
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **value**(int | float): Numeric value to compare against.

**Returns**:

**ComparisonExpr**, returns a comparison expression with operator "<=".

**Example**:

```python
from openjiuwen.core.foundation.store.query import lte

# Find documents where score field is less than or equal to 80
filter_expr = lte("score", 80)
```

---

## func openjiuwen.core.foundation.store.query.base.in_list

Create a list inclusion filter to check if a field value is in a given list of values.

```python
in_list(field: str, values: Sequence | set) -> RangeExpr | ComparisonExpr
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **values**(Sequence | set): List of values (e.g., list or tuple) or set.

**Returns**:

**RangeExpr | ComparisonExpr**, when the value list has only one element, returns `ComparisonExpr` (using "==" operator); otherwise returns `RangeExpr` (using "in" operator).

**Example**:

```python
from openjiuwen.core.foundation.store.query import in_list

# Find documents where category field is in ["tech", "science", "business"]
filter_expr = in_list("category", ["tech", "science", "business"])
```

---

## func openjiuwen.core.foundation.store.query.base.wildcard_match

Create a wildcard match filter (database must support this feature), use `*` in the pattern string as a wildcard.

```python
wildcard_match(field: str, pattern: str, operator: str = "wildcard") -> RangeExpr
```

**Parameters**:

* **field**(str): Name of the field to filter on.
* **pattern**(str): Wildcard pattern string, use `*` to represent any sequence of characters.
* **operator**(str, optional): Wildcard operator name. Default: "wildcard".

**Returns**:

**RangeExpr**, returns a range expression with the specified `operator`.

**Example**:

```python
from openjiuwen.core.foundation.store.query import wildcard_match

# Find documents where author field matches "Al*" pattern (e.g., "Alice", "Alex")
filter_expr = wildcard_match("author", "Al*")
```

---

## func openjiuwen.core.foundation.store.query.base.is_null

Create an IS NULL filter to check if a field is null.

```python
is_null(field: str) -> NullExpr
```

**Parameters**:

* **field**(str): Name of the field to check.

**Returns**:

**NullExpr**, returns a null check expression with `is_null` parameter set to `True`.

**Example**:

```python
from openjiuwen.core.foundation.store.query import is_null

# Find documents where optional_field is null
filter_expr = is_null("optional_field")
```

---

## func openjiuwen.core.foundation.store.query.base.is_not_null

Create an IS NOT NULL filter to check if a field is not null.

```python
is_not_null(field: str) -> NullExpr
```

**Parameters**:

* **field**(str): Name of the field to check.

**Returns**:

**NullExpr**, returns a null check expression with `is_null` parameter set to `False`.

**Example**:

```python
from openjiuwen.core.foundation.store.query import is_not_null

# Find documents where optional_field is not null
filter_expr = is_not_null("optional_field")
```

---

## func openjiuwen.core.foundation.store.query.base.json_key

Create a JSON key filter for filtering on keys within JSON fields.

```python
json_key(field: str, key: str, operator: str, value: Any) -> JSONExpr
```

**Parameters**:

* **field**(str): JSON field name.
* **key**(str): Key within the JSON to filter on.
* **operator**(str): Comparison operator, supports "==", "!=", ">", "<", ">=", "<=".
* **value**(Any): Value to compare against.

**Returns**:

**JSONExpr**, returns a JSON field expression.

**Example**:

```python
from openjiuwen.core.foundation.store.query import json_key

# Find documents where "user" key in metadata JSON field equals "Alice"
filter_expr = json_key("metadata", "user", "==", "Alice")
```

---

## func openjiuwen.core.foundation.store.query.base.array_index

Create an array index filter for filtering on a specific index position in an array field.

```python
array_index(field: str, index: int, operator: str, value: Any) -> ArrayExpr
```

**Parameters**:

* **field**(str): Array field name.
* **index**(int): Array index for accessing a specific element in the array.
* **operator**(str): Comparison operator, supports "==", "!=", ">", "<", ">=", "<=".
* **value**(Any): Value to compare against.

**Returns**:

**ArrayExpr**, returns an array field expression.

**Example**:

```python
from openjiuwen.core.foundation.store.query import array_index

# Find documents where value at index 0 in tags array field equals "important"
filter_expr = array_index("tags", 0, "==", "important")
```

---

## func openjiuwen.core.foundation.store.query.base.filter_user

Create a user ID filter for filtering one or more user IDs.

```python
filter_user(users: str | List[str], user_id_field: str = "user_id") -> RangeExpr | ComparisonExpr
```

**Parameters**:

* **users**(str | List[str]): Single user ID string or list of user IDs.
* **user_id_field**(str, optional): Name of the user ID field. Default: "user_id".

**Returns**:

**RangeExpr | ComparisonExpr**, when there is only one user ID, returns `ComparisonExpr`; otherwise returns `RangeExpr` (using "in" operator).

**Example**:

```python
from openjiuwen.core.foundation.store.query import filter_user

# Find documents where user_id field equals "user123"
filter_expr = filter_user("user123")

# Find documents where user_id field is in ["user123", "user456"]
filter_expr = filter_user(["user123", "user456"])
```

---

## func openjiuwen.core.foundation.store.query.base.chain_filters

Chain multiple filters using the AND operator (&), returns `None` for empty input.

```python
chain_filters(filters: List[QueryExpr]) -> Optional[LogicalExpr]
```

**Parameters**:

* **filters**(List[QueryExpr]): List of filters to chain.

**Returns**:

**Optional[LogicalExpr]**, returns a chained logical expression (using AND operator); returns `None` if the input list is empty.

**Example**:

```python
from openjiuwen.core.foundation.store.query import chain_filters, eq, gt, gte

# Chain multiple filters with AND
filter_expr = chain_filters([
    eq("category", "tech"),
    gt("score", 70),
    gte("year", 2022)
])
# Equivalent to: eq("category", "tech") & gt("score", 70) & gte("year", 2022)
```

---

## Usage Examples

> **Reference Examples**: For more usage examples, please refer to the example code in the `examples/retrieval/` directory of the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository, including:
> - `chroma_query_expr.py`: Demonstrates how to use query expressions with ChromaDB, including comparison operators, range operators, logical operators, text matching, etc.
> - `milvus_query_expr.py`: Demonstrates how to use query expressions with Milvus, including comparison operators, range operators, logical operators, arithmetic operators, null checks, JSON field filtering, array field filtering, text matching, etc.

### Basic Usage

```python
from openjiuwen.core.foundation.store.query import eq, gt, gte, in_list
from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore

# Create vector store instance
store = ChromaVectorStore(persist_directory="./data/chroma")

# Use comparison operators
filter_expr = eq("category", "tech")
results = await store.search(query_vector, top_k=10, filters=filter_expr)

# Combine multiple conditions (using & operator)
filter_expr = eq("category", "tech") & gt("score", 70) & gte("year", 2022)
results = await store.search(query_vector, top_k=10, filters=filter_expr)

# Use OR operator
filter_expr = eq("category", "tech") | eq("category", "science")
results = await store.search(query_vector, top_k=10, filters=filter_expr)

# Use in_list
filter_expr = in_list("category", ["tech", "science", "business"])
results = await store.search(query_vector, top_k=10, filters=filter_expr)
```

### Logical Operators

Query expressions support logical combination using Python's bitwise operators:

- `&`: AND operator
- `|`: OR operator
- `^`: XOR operator (implemented as OR with negation)
- `~`: NOT operator (negation)

```python
from openjiuwen.core.foundation.store.query import eq, gt

# AND operator
filter_expr = eq("category", "tech") & gt("score", 70)

# OR operator
filter_expr = eq("category", "tech") | eq("category", "science")

# NOT operator
filter_expr = ~eq("category", "tech")

# Complex combination
filter_expr = (eq("category", "tech") | eq("category", "science")) & gt("score", 70)
```

### Text Matching

```python
from openjiuwen.core.foundation.store.query import MatchExpr

# Exact match
match_expr = MatchExpr(field="content", value="tech", match_mode="exact")

# Prefix match
match_expr = MatchExpr(field="content", value="This is document 1", match_mode="prefix")

# Suffix match
match_expr = MatchExpr(field="content", value="tech.", match_mode="suffix")

# Contains match
match_expr = MatchExpr(field="content", value="tech", match_mode="infix")
```

### JSON and Array Field Filtering (Milvus only)

```python
from openjiuwen.core.foundation.store.query import json_key, array_index

# JSON field filtering
filter_expr = json_key("metadata", "user", "==", "Alice")

# Array field filtering
filter_expr = array_index("tags", 0, "==", "important")
```

### Null Checks (Milvus only)

```python
from openjiuwen.core.foundation.store.query import is_null, is_not_null

# Check if field is null
filter_expr = is_null("optional_field")

# Check if field is not null
filter_expr = is_not_null("optional_field")
```
