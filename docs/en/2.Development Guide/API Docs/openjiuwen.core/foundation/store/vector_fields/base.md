# openjiuwen.core.foundation.store.vector_fields.base

## class openjiuwen.core.foundation.store.vector_fields.base.VectorField

Base class for configuring Approximate Nearest Neighbor (ANN) search in vector databases.

Provides a common interface for configuring vector field indexes across different database backends. Supports stage-based field filtering to separate construction and search parameters.


```python
VectorField(vector_field: str = "embedding", database_type: Literal["milvus", "chroma"], index_type: Literal["auto", "hnsw", "flat", "ivf", "scann"])
```

Initialize vector field configuration.

**Parameters**:

* **vector_field**(str, optional): Name of the vector field in the database schema. Default: "embedding".
* **database_type**(Literal["milvus", "chroma"]): Type of vector database (milvus or chroma).
* **index_type**(Literal["auto", "hnsw", "flat", "ivf", "scann"]): Type of ANN index algorithm (auto, hnsw, flat, ivf, or scann).

### staticmethod should_keep

```python
should_keep(finfo: FieldInfo, stage: Literal["search", "construct"]) -> bool
```

Determine whether a field should be kept for a given stage.

Fields can be marked with a "stage" in their json_schema_extra to indicate they are only relevant for "construct" or "search" operations.

**Parameters**:

* **finfo**(FieldInfo): Pydantic FieldInfo containing field metadata.
* **stage**(Literal["search", "construct"]): The stage to check: "search" (search-only), or "construct" (construction-only).

**Returns**:

**bool**, returns True if the field should be kept for this stage, False otherwise.

### to_dict

```python
to_dict(stage: Literal["search", "construct"]) -> dict[str, Any]
```

Convert the vector field configuration to a dictionary for a specific stage.

Filters fields based on the specified stage and merges extra arguments for that stage. Removes internal fields (database_type, index_type, vector_field) and stage-specific extra fields that don't match the current stage.

**Parameters**:

* **stage**(Literal["search", "construct"]): The stage to generate configuration for: "search" (fields and extra_search arguments for search operations) or "construct" (fields and extra_construct arguments for index construction).

**Returns**:

**dict[str, Any]**, returns a dictionary containing only the relevant fields for the stage, with extra arguments merged in.
