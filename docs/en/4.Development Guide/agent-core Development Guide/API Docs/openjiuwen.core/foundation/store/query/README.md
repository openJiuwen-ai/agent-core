# query

`openjiuwen.core.foundation.store.query` provides abstract interfaces and implementations for database query expressions, used to convert unified query expressions into query formats for different databases.

**Detailed API Documentation**: [base.md](./base.md), [registry.md](./registry.md)

**Classes**:

| CLASS | DESCRIPTION | Detailed API |
|-------|-------------|---------------|
| **QueryLanguageDefinition** | Database-specific query language definition. | [base.md](./base.md) |
| **QueryExpr** | Base class for all query filters. | [base.md](./base.md) |
| **CustomExpr** | Custom expression class. | [base.md](./base.md) |
| **ComparisonExpr** | Comparison expression class (==, !=, >, <, >=, <=). | [base.md](./base.md) |
| **RangeExpr** | Range expression class. | [base.md](./base.md) |
| **ArithmeticExpr** | Arithmetic expression class. | [base.md](./base.md) |
| **NullExpr** | Null value check expression class. | [base.md](./base.md) |
| **JSONExpr** | JSON field expression class. | [base.md](./base.md) |
| **ArrayExpr** | Array field expression class. | [base.md](./base.md) |
| **LogicalExpr** | Logical expression class (AND, OR, NOT). | [base.md](./base.md) |
| **MatchExpr** | Text matching expression class. | [base.md](./base.md) |

**Functions**:

| FUNCTION | DESCRIPTION | Detailed API |
|----------|-------------|---------------|
| **register_database_query_language** | Register database query language definition. | [registry.md](./registry.md) |
| **eq, ne, gt, lt, gte, lte** | Comparison operation functions. | [base.md](./base.md) |
| **in_list** | List inclusion operation function. | [base.md](./base.md) |
| **wildcard_match** | Wildcard matching function. | [base.md](./base.md) |
| **is_null, is_not_null** | Null value check functions. | [base.md](./base.md) |
| **json_key** | JSON field access function. | [base.md](./base.md) |
| **array_index** | Array index access function. | [base.md](./base.md) |
| **filter_user, chain_filters** | Filter utility functions. | [base.md](./base.md) |
