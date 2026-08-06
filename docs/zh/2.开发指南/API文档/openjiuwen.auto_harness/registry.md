# openjiuwen.auto_harness.registry

注册表与内置注册模块，提供 Auto Harness 元数据（阶段和流水线）的统一注册、查询机制。支持通过配置扩展自定义阶段和流水线注册器。

子模块：
- `base`：注册表基类实现
- `builtin`：内置阶段和流水线注册

---

## class openjiuwen.auto_harness.registry.base.BaseRegistry

```python
@dataclass
class BaseRegistry(Generic[SpecT]):
    """Shared registry implementation."""
```

通用注册表实现，支持泛型 spec 类型。提供注册、查询、列举等基础操作，注册时检测重复名称。

**字段**：
* **_items**(`dict[str, SpecT]`)：已注册项的名称到 spec 映射，默认空字典。

### register(spec: SpecT) -> None

```python
def register(self, spec: SpecT) -> None
```

注册一个 spec。通过 `spec.name` 属性获取名称，若名称已存在则抛出 `ValueError`。

**参数**：
* **spec**(`SpecT`)：要注册的 spec 对象，必须具有 `name` 属性。

**异常**：`ValueError` — 名称重复时抛出。

---

### get(name: str) -> SpecT | None

```python
def get(self, name: str) -> SpecT | None
```

按名称查询 spec，不存在时返回 `None`。

**参数**：
* **name**(`str`)：spec 名称。

**返回**：匹配的 spec 对象，或 `None`。

---

### names() -> list[str]

```python
def names(self) -> list[str]
```

返回所有已注册 spec 的名称列表。

**返回**：名称字符串列表。

---

### require(name: str) -> SpecT

```python
def require(self, name: str) -> SpecT
```

按名称查询 spec，不存在时抛出 `KeyError`。

**参数**：
* **name**(`str`)：spec 名称。

**返回**：匹配的 spec 对象。

**异常**：`KeyError` — 名称不存在时抛出。

---

## class openjiuwen.auto_harness.registry.base.StageRegistry

```python
@dataclass
class StageRegistry(BaseRegistry[StageSpec]):
    """Registry for stage specs."""
```

阶段 spec 注册表，继承 `BaseRegistry[StageSpec]`，用于注册和查询流水线阶段元数据。

---

## class openjiuwen.auto_harness.registry.base.PipelineRegistry

```python
@dataclass
class PipelineRegistry(BaseRegistry[PipelineSpec]):
    """Registry for pipeline specs."""
```

流水线 spec 注册表，继承 `BaseRegistry[PipelineSpec]`，用于注册和查询流水线元数据。

---

## openjiuwen.auto_harness.registry.builtin.register_builtin_stages

```python
def register_builtin_stages(registry: StageRegistry) -> StageRegistry
```

将内置阶段元数据注册到给定的阶段注册表中。注册以下内置阶段：`MetaAssessStage`、`ExtendAssessStage`、`MetaPlanStage`、`ExtendPlanStage`、`MetaImplementStage`、`ExtendImplementStage`、`MetaVerifyStage`、`ExtendVerifyStage`、`CommitStage`、`PublishPRStage`、`LearningsStage`。

**参数**：
* **registry**(`StageRegistry`)：目标阶段注册表。

**返回**：注册完成的阶段注册表（同一引用）。

---

## openjiuwen.auto_harness.registry.builtin.build_stage_registry

```python
def build_stage_registry(config: AutoHarnessConfig) -> StageRegistry
```

构建阶段注册表。先注册所有内置阶段，然后遍历 `config.stage_registrars` 中配置的扩展注册器路径（`module:callable` 格式），依次调用扩展注册器。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。

**返回**：构建完成的阶段注册表。

---

## openjiuwen.auto_harness.registry.builtin.build_pipeline_registry

```python
def build_pipeline_registry(config: AutoHarnessConfig, stage_registry: StageRegistry) -> PipelineRegistry
```

构建流水线注册表。先注册内置的 `MetaEvolvePipeline` 和 `ExtendedEvolvePipeline`，然后遍历 `config.pipeline_registrars` 中配置的扩展注册器路径。扩展注册器支持两种签名：`(pipeline_registry, stage_registry)` 或 `(pipeline_registry)`。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 全局配置。
* **stage_registry**(`StageRegistry`)：已构建的阶段注册表，传递给扩展注册器以供引用。

**返回**：构建完成的流水线注册表。
