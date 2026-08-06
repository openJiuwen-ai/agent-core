# openjiuwen.auto_harness.registry

Registry and built-in registration module, providing unified registration and query mechanisms for Auto Harness metadata (stages and pipelines). Supports extending with custom stage and pipeline registrars through configuration.

Submodules:
- `base`: Registry base class implementation
- `builtin`: Built-in stage and pipeline registration

---

## class openjiuwen.auto_harness.registry.base.BaseRegistry

```python
@dataclass
class BaseRegistry(Generic[SpecT]):
    """Shared registry implementation."""
```

Generic registry implementation supporting generic spec types. Provides basic operations for registration, query, and enumeration; detects duplicate names during registration.

**Fields**:
* **_items**(`dict[str, SpecT]`): Name-to-spec mapping of registered items, default empty dict.

### register(spec: SpecT) -> None

```python
def register(self, spec: SpecT) -> None
```

Register a spec. Gets the name via the `spec.name` attribute; raises `ValueError` if the name already exists.

**Parameters**:
* **spec**(`SpecT`): The spec object to register, must have a `name` attribute.

**Raises**: `ValueError` — Raised when the name is duplicated.

---

### get(name: str) -> SpecT | None

```python
def get(self, name: str) -> SpecT | None
```

Query a spec by name, returning `None` if it does not exist.

**Parameters**:
* **name**(`str`): Spec name.

**Returns**: The matching spec object, or `None`.

---

### names() -> list[str]

```python
def names(self) -> list[str]
```

Return a list of all registered spec names.

**Returns**: List of name strings.

---

### require(name: str) -> SpecT

```python
def require(self, name: str) -> SpecT
```

Query a spec by name, raising `KeyError` if it does not exist.

**Parameters**:
* **name**(`str`): Spec name.

**Returns**: The matching spec object.

**Raises**: `KeyError` — Raised when the name does not exist.

---

## class openjiuwen.auto_harness.registry.base.StageRegistry

```python
@dataclass
class StageRegistry(BaseRegistry[StageSpec]):
    """Registry for stage specs."""
```

Stage spec registry, inheriting `BaseRegistry[StageSpec]`, for registering and querying pipeline stage metadata.

---

## class openjiuwen.auto_harness.registry.base.PipelineRegistry

```python
@dataclass
class PipelineRegistry(BaseRegistry[PipelineSpec]):
    """Registry for pipeline specs."""
```

Pipeline spec registry, inheriting `BaseRegistry[PipelineSpec]`, for registering and querying pipeline metadata.

---

## openjiuwen.auto_harness.registry.builtin.register_builtin_stages

```python
def register_builtin_stages(registry: StageRegistry) -> StageRegistry
```

Register built-in stage metadata into the given stage registry. Registers the following built-in stages: `MetaAssessStage`, `ExtendAssessStage`, `MetaPlanStage`, `ExtendPlanStage`, `MetaImplementStage`, `ExtendImplementStage`, `MetaVerifyStage`, `ExtendVerifyStage`, `CommitStage`, `PublishPRStage`, `LearningsStage`.

**Parameters**:
* **registry**(`StageRegistry`): Target stage registry.

**Returns**: The populated stage registry (same reference).

---

## openjiuwen.auto_harness.registry.builtin.build_stage_registry

```python
def build_stage_registry(config: AutoHarnessConfig) -> StageRegistry
```

Build the stage registry. First registers all built-in stages, then iterates through extension registrar paths configured in `config.stage_registrars` (`module:callable` format), calling each extension registrar in sequence.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.

**Returns**: The constructed stage registry.

---

## openjiuwen.auto_harness.registry.builtin.build_pipeline_registry

```python
def build_pipeline_registry(config: AutoHarnessConfig, stage_registry: StageRegistry) -> PipelineRegistry
```

Build the pipeline registry. First registers the built-in `MetaEvolvePipeline` and `ExtendedEvolvePipeline`, then iterates through extension registrar paths configured in `config.pipeline_registrars`. Extension registrars support two signatures: `(pipeline_registry, stage_registry)` or `(pipeline_registry)`.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness global configuration.
* **stage_registry**(`StageRegistry`): The constructed stage registry, passed to extension registrars for reference.

**Returns**: The constructed pipeline registry.
